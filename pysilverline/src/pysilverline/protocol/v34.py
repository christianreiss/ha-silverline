"""Tuya local protocol v3.4 — AES-128-ECB / 55AA frames with HMAC-SHA256 trailer.

v3.4 frame on the wire:

    [prefix:4][seq:4][cmd:4][size:4][payload:N][hmac_sha256:32][suffix:4]

with `size = N + 36`. The CRC32 is replaced by a keyed 32-byte HMAC-SHA256 over
everything before it. Like v3.5, every TCP connection runs a 3-message
session-key handshake (cmds 0x03/0x04/0x05); unlike v3.5 the cipher is AES-ECB
and the derived session key is `AES-ECB(real_key, local_nonce XOR remote_nonce)`.
The version header is encrypted *inside* the AES ciphertext (v3.3 keeps it
outside). See `Frame34Codec` and `derive_session_key_34`.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import struct
from typing import Any

from .. import const
from ..exceptions import IncompleteFrame, InvalidAuth, ProtocolError
from .crypto import _BLOCK_SIZE, aes_decrypt, aes_encrypt
from .frame import _HEADER_FMT, _HEADER_SIZE, _MAX_FRAME_SIZE, Frame

_HMAC_FOOTER_FMT = ">32sI"  # hmac-sha256(32), suffix(4)
_HMAC_FOOTER_SIZE = struct.calcsize(_HMAC_FOOTER_FMT)  # 36 bytes


class Frame34Codec:
    """Encodes and decodes Tuya local protocol v3.4 (55AA / AES-ECB + HMAC) frames.

    Structurally v3.3 with two changes: the CRC32 trailer becomes a keyed
    32-byte HMAC-SHA256, and the version header is encrypted *inside* the AES
    ciphertext rather than prepended outside it. Both the AES payload and the
    HMAC trailer use the *current* key: the real device key during the
    per-connection handshake, the derived session key afterwards.

    Call ``update_session_key`` after the handshake completes and ``reset``
    before each new TCP connection so the next handshake starts on the real key.
    Mirrors the public surface of :class:`FrameCodec` / :class:`Frame35Codec`
    so :class:`~pysilverline.client.SilverlineClient` needs no version branching.
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

    @staticmethod
    def extract_seq_from_wire(wire: bytes) -> int:
        # 55AA header: prefix(4) + seq(4) + …
        return int.from_bytes(wire[4:8], "big")

    def encode(self, cmd: int, body: dict[str, Any]) -> bytes:
        """Build a 55AA HMAC frame with JSON-serialised ``body``.

        For commands outside ``CMDS_34_WITHOUT_HEADER`` (in practice only
        CONTROL) the 15-byte version header is prepended to the plaintext
        *before* encryption, matching the device firmware.
        """
        plaintext = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if cmd not in const.CMDS_34_WITHOUT_HEADER:
            plaintext = const.PROTOCOL_34_HEADER + plaintext
        ciphertext = aes_encrypt(plaintext, self._key)
        return self._build_frame(cmd, ciphertext)

    def encode_raw(self, cmd: int, payload: bytes) -> bytes:
        """Build a 55AA HMAC frame from a raw (header-less) payload.

        Used for the handshake frames: the payload (a nonce or an HMAC digest)
        is AES-ECB-encrypted with the current key but carries no version header.
        """
        return self._build_frame(cmd, aes_encrypt(payload, self._key))

    def _build_frame(self, cmd: int, ciphertext: bytes) -> bytes:
        seq = next(self._seq)
        size = len(ciphertext) + _HMAC_FOOTER_SIZE
        header = struct.pack(_HEADER_FMT, const.FRAME_PREFIX, seq, cmd, size)
        pre_hmac = header + ciphertext
        mac = hmac.new(self._key, pre_hmac, hashlib.sha256).digest()
        return pre_hmac + struct.pack(_HMAC_FOOTER_FMT, mac, const.FRAME_SUFFIX)

    def decode(self, data: bytes) -> tuple[Frame, bytes]:
        """Decode the first complete 55AA HMAC frame from ``data``.

        The returned ``payload`` is the raw inner bytes (an optional 4-byte
        retcode followed by ciphertext) — exactly like :meth:`FrameCodec.decode`,
        decryption is deferred to ``decrypt_body``. Raises ``IncompleteFrame``
        when more bytes are needed, ``ProtocolError`` on structural violations
        (bad prefix/suffix/oversize) and ``InvalidAuth`` when the keyed HMAC
        trailer does not verify (wrong key, or — far rarer — wire corruption).
        """
        if len(data) < _HEADER_SIZE + _HMAC_FOOTER_SIZE:
            raise IncompleteFrame("header not yet complete")
        prefix, seq, cmd, size = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
        if prefix != const.FRAME_PREFIX:
            raise ProtocolError(f"bad prefix 0x{prefix:08x}")
        if size > _MAX_FRAME_SIZE:
            raise ProtocolError(f"frame too large: {size}")
        # A v3.4 frame must be at least large enough for its own trailer. A
        # smaller `size` is a foreign frame (e.g. a v3.3 device's CRC reply to
        # our handshake probe); reject it cleanly instead of slicing past the
        # header into a negative payload window.
        if size < _HMAC_FOOTER_SIZE:
            raise ProtocolError(f"frame too small: {size}")
        total = _HEADER_SIZE + size
        if len(data) < total:
            raise IncompleteFrame(f"need {total - len(data)} more bytes")

        payload_end = total - _HMAC_FOOTER_SIZE
        payload = data[_HEADER_SIZE:payload_end]
        mac, suffix = struct.unpack(_HMAC_FOOTER_FMT, data[payload_end:total])
        if suffix != const.FRAME_SUFFIX:
            raise ProtocolError(f"bad suffix 0x{suffix:08x}")
        expected = hmac.new(self._key, data[:payload_end], hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise InvalidAuth("HMAC mismatch — local_key likely wrong")
        return Frame(seq=seq, cmd=cmd, payload=payload), data[total:]

    @staticmethod
    def split_response_payload(cmd: int, payload: bytes) -> tuple[int | None, bytes]:
        """Peel a 4-byte (unencrypted) retcode off a response payload.

        Only device→client response frames carry a retcode, and it sits between
        the header and the ciphertext (covered by the HMAC, not encrypted). The
        version header — unlike v3.3 — lives *inside* the ciphertext, so it is
        stripped later by ``decrypt_body``, not here.
        """
        retcode: int | None = None
        body = payload
        if cmd in (
            const.CMD_CONTROL,
            const.CMD_CONTROL_NEW,
            const.CMD_DP_QUERY,
            const.CMD_DP_QUERY_NEW,
            const.CMD_DP_REFRESH,
        ):
            # AES-ECB ciphertext length is always a multiple of 16; a 4-byte
            # retcode prefix is therefore present iff len % 16 == 4. This also
            # covers the v3.4 CONTROL_NEW bare-ACK (a 4-byte cleartext retcode
            # with no JSON body → len == 4 → empty ciphertext after the peel).
            if len(body) % _BLOCK_SIZE == 4:
                retcode = struct.unpack(">I", body[:4])[0]
                body = body[4:]
        return retcode, body

    @staticmethod
    def split_request_payload(payload: bytes) -> bytes:
        """Strip an optional 4-byte retcode prefix off a push frame payload.

        Real firmwares prefix spontaneous STATUS pushes with a zero retcode just
        like responses. The ciphertext that follows is a multiple of 16, so the
        retcode is unambiguously present iff ``len(payload) % 16 == 4``.
        """
        if len(payload) % _BLOCK_SIZE == 4:
            return payload[4:]
        return payload

    def decrypt_body(self, body: bytes) -> dict[str, Any]:
        """AES-ECB-decrypt a ciphertext body, strip the v3.4 header, parse JSON.

        Empty bodies return ``{}``. A decryption failure raises ``InvalidAuth``
        (wrong key → reauth); a clean decrypt that yields non-JSON raises
        ``ProtocolError`` (transient corruption, key is fine) — same contract as
        :meth:`FrameCodec.decrypt_body`.
        """
        if not body:
            return {}
        try:
            plaintext = aes_decrypt(body, self._key)
        except (ProtocolError, ValueError) as err:
            raise InvalidAuth("decryption failed — local_key likely wrong") from err
        # The version header is encrypted in v3.4, so it surfaces here (inside
        # the plaintext) rather than in split_*_payload. Valid JSON starts with
        # '{', so a leading "3.4" is unambiguously the header.
        if plaintext.startswith(const.PROTOCOL_34_HEADER):
            plaintext = plaintext[len(const.PROTOCOL_34_HEADER) :]
        try:
            parsed = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ProtocolError("decrypted payload is not JSON") from err
        if not isinstance(parsed, dict):
            raise ProtocolError(
                f"decrypted payload is not a JSON object: {type(parsed).__name__}"
            )
        return parsed
