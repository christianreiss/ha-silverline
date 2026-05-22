"""Round-trip and edge-case tests for the v3.3 frame codec."""

from __future__ import annotations

import binascii
import json
import struct

import pytest

from pysilverline import const
from pysilverline.exceptions import InvalidAuth, ProtocolError
from pysilverline.protocol import (
    FrameCodec,
    aes_decrypt,
    aes_encrypt,
)

KEY = "0123456789abcdef"


def test_aes_round_trip() -> None:
    plaintext = b'{"hello":"world"}'
    ct = aes_encrypt(plaintext, KEY.encode())
    assert ct != plaintext
    assert aes_decrypt(ct, KEY.encode()) == plaintext


def test_aes_decrypt_wrong_key() -> None:
    ct = aes_encrypt(b"payload", KEY.encode())
    with pytest.raises(ProtocolError):
        aes_decrypt(ct, b"abcdefghijklmnop")


def test_codec_rejects_short_key() -> None:
    with pytest.raises(ValueError):
        FrameCodec("short")


def test_encode_query_no_header() -> None:
    codec = FrameCodec(KEY)
    body = {"gwId": "abc", "devId": "abc"}
    wire = codec.encode(const.CMD_DP_QUERY, body)

    prefix, seq, cmd, size = struct.unpack(">IIII", wire[:16])
    assert prefix == const.FRAME_PREFIX
    assert seq == 1
    assert cmd == const.CMD_DP_QUERY
    assert size == len(wire) - 16

    suffix = struct.unpack(">I", wire[-4:])[0]
    assert suffix == const.FRAME_SUFFIX

    crc_actual = struct.unpack(">I", wire[-8:-4])[0]
    assert crc_actual == binascii.crc32(wire[:-8]) & 0xFFFFFFFF

    inner = wire[16:-8]
    # No 3.3 header on DP_QUERY
    assert not inner.startswith(b"3.3")


def test_encode_control_has_header() -> None:
    codec = FrameCodec(KEY)
    body = {"dps": {"1": True}}
    wire = codec.encode(const.CMD_CONTROL, body)
    inner = wire[16:-8]
    assert inner.startswith(const.PROTOCOL_33_HEADER)


def test_seq_monotonic() -> None:
    codec = FrameCodec(KEY)
    seq1 = struct.unpack(">I", codec.encode(const.CMD_DP_QUERY, {})[4:8])[0]
    seq2 = struct.unpack(">I", codec.encode(const.CMD_DP_QUERY, {})[4:8])[0]
    seq3 = struct.unpack(">I", codec.encode(const.CMD_CONTROL, {})[4:8])[0]
    assert seq1 < seq2 < seq3


def test_round_trip_query() -> None:
    """Build a query, then build a synthetic response and decode it."""

    codec = FrameCodec(KEY)
    codec.encode(const.CMD_DP_QUERY, {"gwId": "x"})  # advance seq

    response_body = {"devId": "x", "dps": {"1": True, "4": "Heat"}}
    plaintext = json.dumps(response_body).encode()
    ciphertext = aes_encrypt(plaintext, KEY.encode())

    payload = struct.pack(">I", 0) + ciphertext  # retcode=0
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 42, const.CMD_DP_QUERY, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    wire = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)

    frame, remainder = codec.decode(wire)
    assert frame.seq == 42
    assert frame.cmd == const.CMD_DP_QUERY
    assert remainder == b""
    retcode, body = codec.split_response_payload(frame.cmd, frame.payload)
    assert retcode == 0
    decoded = codec.decrypt_body(body)
    assert decoded == response_body


def test_decode_handles_v33_header_in_response() -> None:
    """Some firmwares prepend the v3.3 header even on DP_QUERY responses."""
    codec = FrameCodec(KEY)
    body = {"dps": {"1": True}}
    plaintext = json.dumps(body).encode()
    ciphertext = aes_encrypt(plaintext, KEY.encode())

    payload = struct.pack(">I", 0) + const.PROTOCOL_33_HEADER + ciphertext
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 7, const.CMD_DP_QUERY, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    wire = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)

    frame, _ = codec.decode(wire)
    _, peeled = codec.split_response_payload(frame.cmd, frame.payload)
    assert codec.decrypt_body(peeled) == body


def test_decode_status_push_no_retcode() -> None:
    """Spontaneous CMD_STATUS pushes have no leading retcode."""
    codec = FrameCodec(KEY)
    body = {"dps": {"3": 26}}
    plaintext = json.dumps(body).encode()
    ciphertext = aes_encrypt(plaintext, KEY.encode())

    payload = ciphertext  # no retcode prefix for push
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 99, const.CMD_STATUS, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    wire = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)

    frame, _ = codec.decode(wire)
    assert frame.cmd == const.CMD_STATUS
    bare = codec.split_request_payload(frame.payload)
    assert codec.decrypt_body(bare) == body


def test_decode_status_push_with_retcode_and_v33_header() -> None:
    """Real WBR3 firmware pushes carry a 4-byte zero retcode + v3.3 header
    before the ciphertext, despite the protocol notes saying pushes are bare.

    Verified against a live PC-SLP090N: every spontaneous DP-3 (temperature)
    push has this shape, so the codec must peel both prefixes."""
    codec = FrameCodec(KEY)
    body = {"dps": {"3": 28}, "t": 77848}
    plaintext = json.dumps(body, separators=(",", ":")).encode()
    ciphertext = aes_encrypt(plaintext, KEY.encode())

    payload = struct.pack(">I", 0) + const.PROTOCOL_33_HEADER + ciphertext
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, const.CMD_STATUS, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    wire = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)

    frame, _ = codec.decode(wire)
    bare = codec.split_request_payload(frame.payload)
    assert codec.decrypt_body(bare) == body


def test_decode_truncated() -> None:
    codec = FrameCodec(KEY)
    with pytest.raises(ProtocolError):
        codec.decode(b"\x00\x00\x55\xaa" + b"\x00" * 4)


def test_decode_bad_prefix() -> None:
    codec = FrameCodec(KEY)
    bad = b"\xde\xad\xbe\xef" + b"\x00" * 32
    with pytest.raises(ProtocolError):
        codec.decode(bad)


def test_decode_bad_crc() -> None:
    codec = FrameCodec(KEY)
    wire = bytearray(codec.encode(const.CMD_DP_QUERY, {"x": 1}))
    wire[-8] ^= 0xFF  # flip a bit in the CRC
    with pytest.raises(ProtocolError):
        codec.decode(bytes(wire))


def test_decrypt_body_rejects_garbage() -> None:
    codec = FrameCodec(KEY)
    with pytest.raises(InvalidAuth):
        codec.decrypt_body(b"\x00" * 16)


def test_decrypt_body_empty_returns_empty_dict() -> None:
    codec = FrameCodec(KEY)
    assert codec.decrypt_body(b"") == {}


def test_decode_rejects_oversize_size_field() -> None:
    """A header claiming a multi-GiB frame must be rejected immediately
    instead of waiting for the bytes to arrive — protects against a
    hostile LAN peer trying to exhaust memory."""
    codec = FrameCodec(KEY)
    # Header with size = 0xFFFFFFFF (~4 GiB); no payload follows.
    header = struct.pack(
        ">IIII", const.FRAME_PREFIX, 1, const.CMD_DP_QUERY, 0xFFFFFFFF
    )
    # Pad to clear the "frame too short" guard so the size check is reached.
    wire = header + b"\x00" * 8
    with pytest.raises(ProtocolError, match="frame too large"):
        codec.decode(wire)
