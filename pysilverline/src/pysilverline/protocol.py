"""Tuya local protocol v3.3 frame codec.

A frame on the wire is:

    [prefix:4][seq:4][cmd:4][size:4][payload:N][crc32:4][suffix:4]

with `size = N + 8`. CRC32 is computed over everything before the CRC bytes.
All multi-byte integers are big-endian.

Payloads for outbound CONTROL/REFRESH commands are prefixed with 15 bytes of
v3.3 header (b"3.3" + 12 nulls) before AES encryption; DP_QUERY (0x0a) is
encrypted directly. Inbound payloads start with a 4-byte return code on most
command echoes, optionally followed by the v3.3 header and the AES-encrypted
JSON body.
"""

from __future__ import annotations

import binascii
import itertools
import json
import struct
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
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
        if body.startswith(const.PROTOCOL_VERSION):
            body = body[len(const.PROTOCOL_33_HEADER):]
        return retcode, body

    @staticmethod
    def split_request_payload(payload: bytes) -> bytes:
        """Strip the optional v3.3 header from a push frame payload.

        Real WBR3 firmwares send spontaneous ``CMD_STATUS`` pushes shaped
        as ``[4-byte zero retcode][v3.3 header][ciphertext]``, even though
        the Tuya protocol notes describe pushes as headerless. We peel
        either shape so push DPs decrypt correctly.
        """
        if payload.startswith(const.PROTOCOL_VERSION):
            return payload[len(const.PROTOCOL_33_HEADER):]
        if len(payload) >= 4 and payload[4:].startswith(const.PROTOCOL_VERSION):
            return payload[4 + len(const.PROTOCOL_33_HEADER):]
        return payload

    def decrypt_body(self, body: bytes) -> dict[str, Any]:
        """Decrypt a payload body and parse it as JSON.

        Empty bodies return an empty dict; a non-JSON or auth-rejected
        ciphertext raises InvalidAuth so the caller can trigger a reauth flow.
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
            raise InvalidAuth("decrypted payload is not JSON") from err
        if not isinstance(parsed, dict):
            raise InvalidAuth(f"decrypted payload is not a JSON object: {type(parsed).__name__}")
        return parsed


def is_invalid_auth_retcode(retcode: int | None) -> bool:
    """Some firmwares signal a wrong local_key with these return codes."""
    return retcode is not None and retcode in _RETCODE_INVALID_KEY
