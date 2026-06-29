"""Tuya local protocol v3.5 — AES-128-GCM / 6699 frames.

v3.5 frame on the wire:

    [prefix:4][unknown:2][seq:4][cmd:4][length:4][iv:12][ciphertext:N][tag:16][suffix:4]

with `length = N + 28`. GCM tag authenticates header bytes[4:18] as AAD.
Every TCP connection requires a 3-message session-key handshake (cmds 0x03/0x04/0x05)
before any data frame. See `Frame35Codec` and `derive_session_key_35`.

Control writes (CONTROL/CONTROL_NEW) prepend a 15-byte Tuya version header to the
JSON inside the ciphertext; reads and the handshake do not. See `encode`.
"""

from __future__ import annotations

import itertools
import json
import os
import struct
from typing import Any

from .. import const
from ..exceptions import IncompleteFrame, ProtocolError
from .crypto import _GCM_IV_SIZE, _GCM_TAG_SIZE, aes_gcm_decrypt, aes_gcm_encrypt
from .frame import _MAX_FRAME_SIZE, Frame

_35_HEADER_FMT = ">IHIII"  # prefix(4) unknown(2) seq(4) cmd(4) length(4)
_35_HEADER_SIZE = struct.calcsize(_35_HEADER_FMT)  # 18 bytes

# Some firmware prepends a Tuya "version header" to the JSON body of a push:
# 3 version bytes (b"3.5") + 12 reserved bytes = 15. The same header is required
# on outbound control writes (see ``Frame35Codec.encode``). See
# split_request_payload.
_35_VERSION_HEADER_SIZE = len(const.PROTOCOL_35_HEADER)


class Frame35Codec:
    """Encodes and decodes Tuya local protocol v3.5 (6699 / AES-GCM) frames.

    Holds both the real device key and the current session key (derived during
    the per-connection handshake).  Call ``update_session_key`` after the
    handshake completes; call ``reset`` before each new TCP connection so the
    next handshake starts with the real key.

    Shares the ``Frame`` dataclass with ``FrameCodec`` — ``payload`` on
    decoded frames is already AES-GCM-decrypted plaintext (IV stripped, tag
    validated).
    """

    def __init__(self, local_key: str) -> None:
        self._real_key = local_key.encode("utf-8")
        if len(self._real_key) != 16:
            raise ValueError("local_key must be 16 ASCII characters")
        self._key = self._real_key
        self._seq = itertools.count(1)

    def reset(self) -> None:
        """Reset to the real key; call before each new TCP connection."""
        self._key = self._real_key

    def update_session_key(self, session_key: bytes) -> None:
        """Switch to the derived session key after handshake."""
        self._key = session_key

    # ------------------------------------------------------------------
    # Shared interface with FrameCodec (used by SilverlineClient without
    # branching on protocol version)
    # ------------------------------------------------------------------

    @staticmethod
    def extract_seq_from_wire(wire: bytes) -> int:
        # 6699 header: prefix(4) + unknown(2) + seq(4) + …
        return int.from_bytes(wire[6:10], "big")

    def encode(self, cmd: int, body: dict[str, Any]) -> bytes:
        """Build a 6699 frame with JSON-serialised ``body``.

        Control writes (CONTROL / CONTROL_NEW) carry a 15-byte Tuya version
        header (``b"3.5"`` + 12 reserved bytes) prepended to the JSON, encrypted
        inside the GCM ciphertext. Real v3.5 firmware rejects header-less writes
        with retcode 0x01000000 while still answering header-less reads — issue
        #7, confirmed against a tinytuya write that powered the same unit on.
        DP_QUERY(_NEW), heartbeat, refresh and the handshake stay header-less
        (``CMDS_35_WITHOUT_HEADER``). Mirrors ``Frame34Codec.encode`` and
        tinytuya's ``NO_PROTOCOL_HEADER_CMDS`` logic.
        """
        plaintext = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if cmd not in const.CMDS_35_WITHOUT_HEADER:
            plaintext = const.PROTOCOL_35_HEADER + plaintext
        return self._build_frame(cmd, plaintext)

    def encode_raw(self, cmd: int, payload: bytes) -> bytes:
        """Build a 6699 frame with a raw bytes payload (for handshake)."""
        return self._build_frame(cmd, payload)

    def _build_frame(self, cmd: int, plaintext: bytes) -> bytes:
        seq = next(self._seq)
        iv = os.urandom(_GCM_IV_SIZE)
        # length field = IV + ciphertext + tag (no suffix counted here)
        length = _GCM_IV_SIZE + len(plaintext) + _GCM_TAG_SIZE
        header = struct.pack(_35_HEADER_FMT, const.FRAME_PREFIX_35, 0, seq, cmd, length)
        aad = header[4:]  # bytes[4:18] authenticated but not encrypted
        ciphertext, tag = aes_gcm_encrypt(plaintext, self._key, iv, aad)
        suffix = struct.pack(">I", const.FRAME_SUFFIX_35)
        return header + iv + ciphertext + tag + suffix

    def decode(self, data: bytes) -> tuple[Frame, bytes]:
        """Decode the first complete 6699 frame from ``data``.

        Returns the decoded frame (payload is the decrypted plaintext) and the
        unconsumed remainder.  Raises ``IncompleteFrame`` if more bytes are
        needed, or ``ProtocolError`` on structural violations.  Raises
        ``InvalidAuth`` on GCM tag mismatch (wrong key).
        """
        min_frame = _35_HEADER_SIZE + _GCM_IV_SIZE + _GCM_TAG_SIZE + 4
        if len(data) < min_frame:
            raise IncompleteFrame("header not yet complete")

        prefix, _unknown, seq, cmd, length = struct.unpack(
            _35_HEADER_FMT, data[:_35_HEADER_SIZE]
        )
        if prefix != const.FRAME_PREFIX_35:
            raise ProtocolError(f"bad v3.5 prefix 0x{prefix:08x}")
        if length > _MAX_FRAME_SIZE:
            raise ProtocolError(f"frame too large: {length}")

        total = _35_HEADER_SIZE + length + 4  # header + encrypted_blob + suffix
        if len(data) < total:
            raise IncompleteFrame(f"need {total - len(data)} more bytes")

        suffix_val = struct.unpack(">I", data[total - 4 : total])[0]
        if suffix_val != const.FRAME_SUFFIX_35:
            raise ProtocolError(f"bad v3.5 suffix 0x{suffix_val:08x}")

        inner = data[_35_HEADER_SIZE : total - 4]  # IV + ciphertext + tag
        iv = inner[:_GCM_IV_SIZE]
        tag = inner[-_GCM_TAG_SIZE:]
        ciphertext = inner[_GCM_IV_SIZE:-_GCM_TAG_SIZE]
        aad = data[4:_35_HEADER_SIZE]

        plaintext = aes_gcm_decrypt(ciphertext, self._key, iv, aad, tag)
        return Frame(seq=seq, cmd=cmd, payload=plaintext), data[total:]

    @staticmethod
    def split_response_payload(cmd: int, payload: bytes) -> tuple[int | None, bytes]:
        """Peel a 4-byte retcode from a decrypted response payload.

        Payload is already decrypted by ``decode()``; this mirrors the v3.3
        method's interface so callers need no version-awareness.
        """
        retcode: int | None = None
        body = payload
        if cmd in (
            const.CMD_CONTROL,
            const.CMD_CONTROL_NEW,
            const.CMD_DP_QUERY,
            const.CMD_DP_REFRESH,
        ):
            if len(body) >= 4:
                retcode = struct.unpack(">I", body[:4])[0]
                body = body[4:]
        return retcode, body

    @staticmethod
    def split_request_payload(payload: bytes) -> bytes:
        """Strip a retcode and/or Tuya version header from a push frame payload.

        The payload is already decrypted. Three shapes are seen on STATUS
        (0x08) pushes:

        * bare ``{...}`` JSON;
        * ``[retcode:4]{...}`` — a 4-byte return code then JSON;
        * ``[retcode:4]["3.x" + 12 reserved : 15]{...}`` — a 4-byte retcode,
          then a 15-byte Tuya version header, then JSON. Observed on JetLine
          Selection FI firmware (productKey 3bhylhz5zhogklel, v3.5), where the
          JSON is the ``{"protocol":4,"t":...,"data":{"dps":{...}}}`` envelope
          that ``_unwrap_dps`` already understands.

        Peel whatever framing precedes the JSON so ``decrypt_body`` sees a clean
        object; return the payload unchanged if nothing matches (the caller
        then logs and drops it).
        """
        body = payload
        # Version header with or without a leading 4-byte retcode: validated by
        # confirming a JSON object opens immediately after the 15-byte header,
        # so a future firmware with different framing degrades to the drop path
        # rather than mis-slicing.
        vh = _35_VERSION_HEADER_SIZE
        for offset in (0, 4):
            if (
                len(body) > offset + vh
                and body[offset : offset + 2] == b"3."
                and body[offset + vh : offset + vh + 1] == b"{"
            ):
                return body[offset + vh :]
        # Legacy: bare JSON behind an optional 4-byte retcode.
        if len(body) > 4 and body[0:1] != b"{" and body[4:5] == b"{":
            return body[4:]
        return body

    @staticmethod
    def decrypt_body(body: bytes) -> dict[str, Any]:
        """Parse an already-decrypted payload as JSON."""
        if not body:
            return {}
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ProtocolError("v3.5 payload is not JSON") from err
        if not isinstance(parsed, dict):
            raise ProtocolError(
                f"v3.5 payload is not a JSON object: {type(parsed).__name__}"
            )
        return parsed
