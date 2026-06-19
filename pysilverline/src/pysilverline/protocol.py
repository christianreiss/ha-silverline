"""Tuya local protocol frame codecs for v3.3, v3.4 (both 55AA/AES-ECB) and v3.5
(6699/AES-GCM).

v3.3 frame on the wire:

    [prefix:4][seq:4][cmd:4][size:4][payload:N][crc32:4][suffix:4]

with `size = N + 8`. CRC32 is computed over everything before the CRC bytes.
All multi-byte integers are big-endian.

v3.4 frame on the wire:

    [prefix:4][seq:4][cmd:4][size:4][payload:N][hmac_sha256:32][suffix:4]

with `size = N + 36`. The CRC32 is replaced by a keyed 32-byte HMAC-SHA256 over
everything before it. Like v3.5, every TCP connection runs a 3-message
session-key handshake (cmds 0x03/0x04/0x05); unlike v3.5 the cipher is AES-ECB
and the derived session key is `AES-ECB(real_key, local_nonce XOR remote_nonce)`.
The version header is encrypted *inside* the AES ciphertext (v3.3 keeps it
outside). See `Frame34Codec` and `derive_session_key_34`.

v3.5 frame on the wire:

    [prefix:4][unknown:2][seq:4][cmd:4][length:4][iv:12][ciphertext:N][tag:16][suffix:4]

with `length = N + 28`. GCM tag authenticates header bytes[4:18] as AAD.
Every TCP connection requires a 3-message session-key handshake (cmds 0x03/0x04/0x05)
before any data frame. See `Frame35Codec` and `derive_session_key_35`.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import itertools
import json
import os
import struct
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import const
from .exceptions import IncompleteFrame, InvalidAuth, ProtocolError

_HEADER_FMT = ">IIII"  # prefix, seq, cmd, size
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_FOOTER_FMT = ">II"  # crc32, suffix
_FOOTER_SIZE = struct.calcsize(_FOOTER_FMT)
_BLOCK_SIZE = 16  # AES-128 block size
# Upper bound on the wire-claimed `size` field. Real Tuya frames from a heat
# pump are well under 1 KiB; capping at 64 KiB prevents a hostile LAN peer
# from claiming a 4 GiB frame to exhaust memory while we wait for bytes.
_MAX_FRAME_SIZE = 64 * 1024

_RETCODE_INVALID_KEY = {0x00000FFF, 0xFFFFFFFF}


def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = _BLOCK_SIZE - (len(data) % _BLOCK_SIZE)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % _BLOCK_SIZE != 0:
        raise ProtocolError("ciphertext length not a multiple of block size")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > _BLOCK_SIZE:
        raise ProtocolError("invalid PKCS#7 padding")
    if not hmac.compare_digest(data[-pad_len:], bytes([pad_len]) * pad_len):
        raise ProtocolError("corrupt PKCS#7 padding")
    return data[:-pad_len]


def _make_cipher(key: bytes) -> Cipher[modes.ECB]:
    if len(key) != 16:
        raise ValueError(f"local_key must be 16 bytes, got {len(key)}")
    return Cipher(algorithms.AES(key), modes.ECB())


def aes_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB encrypt with PKCS#7 padding."""
    encryptor = _make_cipher(key).encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def aes_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB decrypt with PKCS#7 unpadding."""
    decryptor = _make_cipher(key).decryptor()
    raw = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(raw)


@dataclass(slots=True, kw_only=True)
class Frame:
    """A decoded Tuya wire frame.

    ``payload`` is the raw inner bytes; use ``FrameCodec.split_response_payload``
    or ``FrameCodec.split_request_payload`` to peel the retcode / v3.3 header
    before decryption, depending on direction.
    """

    seq: int
    cmd: int
    payload: bytes


class FrameCodec:
    """Encodes outbound frames and decodes inbound ones for one device.

    Sequence numbers monotonically increase per outbound frame; the codec is
    not thread-safe, callers should serialize use within their own locks.
    """

    def __init__(self, local_key: str) -> None:
        self._key = local_key.encode("utf-8")
        if len(self._key) != 16:
            raise ValueError("local_key must be 16 ASCII characters")
        self._seq = itertools.count(1)

    def next_seq(self) -> int:
        return next(self._seq)

    @staticmethod
    def extract_seq_from_wire(wire: bytes) -> int:
        # 55AA header: prefix(4) + seq(4) + …
        return int.from_bytes(wire[4:8], "big")

    def encode(self, cmd: int, body: dict[str, Any]) -> bytes:
        """Build a complete frame for `cmd` with JSON-serialized `body`."""

        plaintext = json.dumps(body, separators=(",", ":")).encode("utf-8")
        ciphertext = aes_encrypt(plaintext, self._key)
        if cmd not in const.CMDS_WITHOUT_HEADER:
            payload = const.PROTOCOL_33_HEADER + ciphertext
        else:
            payload = ciphertext

        seq = self.next_seq()
        size = len(payload) + _FOOTER_SIZE
        header = struct.pack(_HEADER_FMT, const.FRAME_PREFIX, seq, cmd, size)
        body_bytes = header + payload
        crc = binascii.crc32(body_bytes) & 0xFFFFFFFF
        return body_bytes + struct.pack(_FOOTER_FMT, crc, const.FRAME_SUFFIX)

    def decode(self, data: bytes) -> tuple[Frame, bytes]:
        """Decode the first complete frame from `data`.

        Returns the decoded frame (with its raw inner payload — the v3.3
        header and any retcode prefix are NOT stripped here, since that
        depends on whether the frame is a request or a response) and the
        unconsumed remainder of the buffer.

        Raises ``IncompleteFrame`` when more bytes are needed before a
        frame can be decoded — caller should accumulate more bytes and
        retry. Raises ``ProtocolError`` only when the bytes that have
        arrived violate the spec (bad prefix/suffix/size/CRC), in which
        case the connection is desynchronized and must be dropped.
        """

        if len(data) < _HEADER_SIZE + _FOOTER_SIZE:
            raise IncompleteFrame("header not yet complete")
        prefix, seq, cmd, size = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
        # Validate the prefix BEFORE the size cap: a peer that sends
        # garbage shaped vaguely like a Tuya frame might also produce a
        # plausibly-sized but bogus size field, and we want the more
        # specific "bad prefix" diagnostic in the logs.
        if prefix != const.FRAME_PREFIX:
            raise ProtocolError(f"bad prefix 0x{prefix:08x}")
        if size > _MAX_FRAME_SIZE:
            raise ProtocolError(f"frame too large: {size}")
        total = _HEADER_SIZE + size
        if len(data) < total:
            raise IncompleteFrame(f"need {total - len(data)} more bytes")

        payload_end = total - _FOOTER_SIZE
        payload = data[_HEADER_SIZE:payload_end]
        crc, suffix = struct.unpack(_FOOTER_FMT, data[payload_end:total])
        if suffix != const.FRAME_SUFFIX:
            raise ProtocolError(f"bad suffix 0x{suffix:08x}")
        if crc != binascii.crc32(data[:payload_end]) & 0xFFFFFFFF:
            raise ProtocolError("CRC mismatch")

        return Frame(seq=seq, cmd=cmd, payload=payload), data[total:]

    @staticmethod
    def split_response_payload(cmd: int, payload: bytes) -> tuple[int | None, bytes]:
        """Peel a 4-byte retcode and a v3.3 header off a response payload.

        Use this on frames received in response to commands we sent
        (CONTROL/DP_QUERY/DP_REFRESH). Spontaneous pushes (CMD_STATUS,
        CMD_HEART_BEAT) carry no retcode, so callers should pass them
        directly to ``decrypt_body``.
        """
        retcode: int | None = None
        body = payload
        if cmd in (const.CMD_CONTROL, const.CMD_DP_QUERY, const.CMD_DP_REFRESH):
            if len(body) >= 4:
                retcode = struct.unpack(">I", body[:4])[0]
                body = body[4:]
        # Match the full 15-byte v3.3 header, not just its 3-byte ASCII
        # prefix — at ~1/16M frames a random AES ciphertext would otherwise
        # begin with 33 2e 33 and we'd peel 15 bytes off real payload.
        if body.startswith(const.PROTOCOL_33_HEADER):
            body = body[len(const.PROTOCOL_33_HEADER) :]
        return retcode, body

    @staticmethod
    def split_request_payload(payload: bytes) -> bytes:
        """Strip the optional v3.3 header from a push frame payload.

        Real WBR3 firmwares send spontaneous ``CMD_STATUS`` pushes shaped
        as ``[4-byte zero retcode][v3.3 header][ciphertext]``, even though
        the Tuya protocol notes describe pushes as headerless. We peel
        either shape so push DPs decrypt correctly.
        """
        # Use the full 15-byte v3.3 header; the bare 3-byte ASCII prefix
        # is a 1/16M collision target on encrypted bytes.
        if payload.startswith(const.PROTOCOL_33_HEADER):
            return payload[len(const.PROTOCOL_33_HEADER) :]
        if len(payload) >= 4 and payload[4:].startswith(const.PROTOCOL_33_HEADER):
            return payload[4 + len(const.PROTOCOL_33_HEADER) :]
        return payload

    def decrypt_body(self, body: bytes) -> dict[str, Any]:
        """Decrypt a payload body and parse it as JSON.

        Empty bodies return an empty dict. A failure to decrypt with our
        key raises ``InvalidAuth`` (signals reauth at the user). A
        successful decrypt followed by garbled output (non-UTF8 / non-JSON
        / non-object) raises ``ProtocolError`` instead — the key is fine,
        a frame just got corrupted on the wire, and triggering a reauth
        flow over transient corruption would punish the user for a single
        bit-flip.
        """

        if not body:
            return {}
        try:
            plaintext = aes_decrypt(body, self._key)
        except (ProtocolError, ValueError) as err:
            raise InvalidAuth("decryption failed — local_key likely wrong") from err
        try:
            parsed = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ProtocolError("decrypted payload is not JSON") from err
        if not isinstance(parsed, dict):
            raise ProtocolError(
                f"decrypted payload is not a JSON object: {type(parsed).__name__}"
            )
        return parsed


def is_invalid_auth_retcode(retcode: int | None) -> bool:
    """Some firmwares signal a wrong local_key with these return codes."""
    return retcode is not None and retcode in _RETCODE_INVALID_KEY


# ---------------------------------------------------------------------------
# Tuya local protocol v3.4 — AES-128-ECB / 55AA frames with HMAC-SHA256 trailer
# ---------------------------------------------------------------------------

_HMAC_FOOTER_FMT = ">32sI"  # hmac-sha256(32), suffix(4)
_HMAC_FOOTER_SIZE = struct.calcsize(_HMAC_FOOTER_FMT)  # 36 bytes


def derive_session_key_34(
    local_nonce: bytes, remote_nonce: bytes, real_key: bytes
) -> bytes:
    """Derive the v3.4 per-connection session key from the exchanged nonces.

    XOR the two 16-byte nonces and AES-ECB-encrypt the result with the real
    key — no padding, no IV, a single 16-byte block out. Mirrors TinyTuya's
    ``_negotiate_session_key_generate_finalize`` for v3.4
    (``cipher.encrypt(local_nonce ^ remote_nonce, use_base64=False, pad=False)``).
    Differs from :func:`derive_session_key_35`, which uses GCM with an IV slice.
    """
    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    encryptor = _make_cipher(real_key).encryptor()
    return encryptor.update(xored) + encryptor.finalize()


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


# ---------------------------------------------------------------------------
# Tuya local protocol v3.5 — AES-128-GCM / 6699 frames
# ---------------------------------------------------------------------------

_35_HEADER_FMT = ">IHIII"  # prefix(4) unknown(2) seq(4) cmd(4) length(4)
_35_HEADER_SIZE = struct.calcsize(_35_HEADER_FMT)  # 18 bytes
_GCM_TAG_SIZE = 16
_GCM_IV_SIZE = 12


def aes_gcm_encrypt(
    plaintext: bytes, key: bytes, iv: bytes, aad: bytes
) -> tuple[bytes, bytes]:
    """AES-128-GCM encrypt; returns (ciphertext, tag)."""
    ct_and_tag = AESGCM(key).encrypt(iv, plaintext, aad)
    return ct_and_tag[:-_GCM_TAG_SIZE], ct_and_tag[-_GCM_TAG_SIZE:]


def aes_gcm_decrypt(
    ciphertext: bytes, key: bytes, iv: bytes, aad: bytes, tag: bytes
) -> bytes:
    """AES-128-GCM decrypt; raises InvalidAuth on tag mismatch."""
    try:
        return AESGCM(key).decrypt(iv, ciphertext + tag, aad)
    except Exception as err:
        raise InvalidAuth("GCM tag mismatch — local_key likely wrong") from err


def derive_session_key_35(
    local_nonce: bytes, remote_nonce: bytes, real_key: bytes
) -> bytes:
    """Derive the v3.5 per-connection session key from the exchanged nonces.

    XOR nonces, AES-GCM-encrypt the result with the real key (IV = first 12
    bytes of local nonce), and take bytes 12..28 of the full (IV||CT||tag)
    output — i.e. the 16-byte ciphertext slice.  Mirrors TinyTuya's
    ``_negotiate_session_key_generate_finalize`` for v3.5.
    """
    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    iv = local_nonce[:_GCM_IV_SIZE]
    # AESGCM.encrypt returns ciphertext(16B) + tag(16B) for 16-byte plaintext.
    # Prepend IV to match TinyTuya's (IV||CT||tag)[12:28] = CT[0:16].
    ct_tag = AESGCM(real_key).encrypt(iv, xored, None)
    return ct_tag[:_BLOCK_SIZE]  # first 16 bytes = ciphertext = session key


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
        """Build a 6699 frame with JSON-serialised ``body``."""
        plaintext = json.dumps(body, separators=(",", ":")).encode("utf-8")
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
        if cmd in (const.CMD_CONTROL, const.CMD_DP_QUERY, const.CMD_DP_REFRESH):
            if len(body) >= 4:
                retcode = struct.unpack(">I", body[:4])[0]
                body = body[4:]
        return retcode, body

    @staticmethod
    def split_request_payload(payload: bytes) -> bytes:
        """Strip an optional 4-byte retcode from a push frame payload.

        Payload is already decrypted; strip retcode if the first byte is not
        the start of a JSON object.
        """
        if len(payload) > 4 and payload[0:1] != b"{" and payload[4:5] == b"{":
            return payload[4:]
        return payload

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
