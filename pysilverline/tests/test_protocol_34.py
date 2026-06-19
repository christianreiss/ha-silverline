"""Round-trip and handshake tests for the v3.4 frame codec (55AA / AES-ECB)."""

from __future__ import annotations

import json
import struct

import pytest

from pysilverline import const
from pysilverline.exceptions import IncompleteFrame, InvalidAuth, ProtocolError
from pysilverline.protocol import (
    Frame34Codec,
    aes_encrypt_block,
    derive_session_key_34,
)

KEY = "0123456789abcdef"
KEY_B = KEY.encode()


def test_derive_session_key_34_known_vector() -> None:
    """Verify session key derivation matches TinyTuya's v3.4 logic."""
    local_nonce = bytes(range(16))
    remote_nonce = bytes(range(16, 32))
    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    expected = aes_encrypt_block(xored, KEY_B)
    assert derive_session_key_34(local_nonce, remote_nonce, KEY_B) == expected


def test_derive_session_key_34_is_deterministic() -> None:
    nonce_a = b"\x01" * 16
    nonce_b = b"\x02" * 16
    key1 = derive_session_key_34(nonce_a, nonce_b, KEY_B)
    key2 = derive_session_key_34(nonce_a, nonce_b, KEY_B)
    key3 = derive_session_key_34(nonce_b, nonce_a, KEY_B)
    assert key1 == key2
    # XOR is commutative — order of nonces does not change the session key.
    assert key1 == key3
    assert key1 != derive_session_key_34(b"\x03" * 16, nonce_b, KEY_B)


def test_codec_rejects_short_key() -> None:
    with pytest.raises(ValueError):
        Frame34Codec("short")


def test_frame34_encode_decode_roundtrip() -> None:
    codec = Frame34Codec(KEY)
    body = {"dps": {"1": True, "2": 28}}
    wire = codec.encode(const.CMD_CONTROL, body)

    assert wire[:4] == b"\x00\x00\x55\xaa"
    assert wire[-4:] == b"\x00\x00\xaa\x55"

    frame, remainder = codec.decode(wire, cleartext_retcode=False)
    assert remainder == b""
    assert frame.cmd == const.CMD_CONTROL
    assert json.loads(frame.payload[4:]) == body


def test_frame34_dp_query_has_no_version_header() -> None:
    codec = Frame34Codec(KEY)
    wire = codec.encode(const.CMD_DP_QUERY, {"devId": "x"})
    frame, _ = codec.decode(wire, cleartext_retcode=False)
    assert frame.payload[4:] == b'{"devId":"x"}'


def test_frame34_encode_raw_roundtrip() -> None:
    codec = Frame34Codec(KEY)
    nonce = b"\xab" * 16
    wire = codec.encode_raw(const.SESS_KEY_NEG_START, nonce)
    _, _, cmd, size = struct.unpack(">IIII", wire[:16])
    assert cmd == const.SESS_KEY_NEG_START
    assert size == 16 + Frame34Codec._FOOTER_SIZE
    frame, _ = codec.decode(wire, cleartext_retcode=False)
    assert frame.payload[-16:] == nonce


def test_frame34_handshake_finish_is_cleartext() -> None:
    codec = Frame34Codec(KEY)
    finish = b"\xcd" * 32
    wire = codec.encode_raw(const.SESS_KEY_NEG_FINISH, finish)
    _, _, cmd, size = struct.unpack(">IIII", wire[:16])
    assert cmd == const.SESS_KEY_NEG_FINISH
    assert size == 32 + Frame34Codec._FOOTER_SIZE
    frame, _ = codec.decode(wire, cleartext_retcode=False)
    assert frame.payload[-32:] == finish


def test_frame34_session_key_update() -> None:
    codec = Frame34Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    session_key = b"\xff" * 16
    codec.update_session_key(session_key)
    with pytest.raises(InvalidAuth):
        codec.decode(wire)
    codec.reset()
    frame, _ = codec.decode(wire)
    assert frame.cmd == const.CMD_HEART_BEAT


def test_frame34_decode_incomplete_raises() -> None:
    codec = Frame34Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    with pytest.raises(IncompleteFrame):
        codec.decode(wire[:10])


def test_frame34_decrypt_body_invalid_json_raises_protocol_error() -> None:
    with pytest.raises(ProtocolError, match="not JSON"):
        Frame34Codec.decrypt_body(b"not-json")
