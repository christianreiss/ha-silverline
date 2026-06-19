"""Round-trip and structural tests for the v3.4 frame codec (55AA / AES-ECB + HMAC).

v3.4 reuses the v3.3 55AA envelope but swaps the 4-byte CRC32 trailer for a
32-byte keyed HMAC-SHA256, and moves the version header *inside* the AES
ciphertext. These tests pin those byte-level differences; the handshake and
session-key switch are exercised end to end in ``test_client_34``.
"""

from __future__ import annotations

import struct

import pytest

from pysilverline import const
from pysilverline.exceptions import IncompleteFrame, InvalidAuth, ProtocolError
from pysilverline.protocol import (
    Frame34Codec,
    aes_encrypt,
    derive_session_key_34,
)

KEY = "0123456789abcdef"
KEY_B = KEY.encode()


# ---------------------------------------------------------------------------
# Session key derivation
# ---------------------------------------------------------------------------


def test_derive_session_key_34_known_vector() -> None:
    """v3.4 session key = AES-ECB(real_key, local_nonce XOR remote_nonce), no pad."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    local_nonce = bytes(range(16))
    remote_nonce = bytes(range(16, 32))

    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    enc = Cipher(algorithms.AES(KEY_B), modes.ECB()).encryptor()
    expected = enc.update(xored) + enc.finalize()

    got = derive_session_key_34(local_nonce, remote_nonce, KEY_B)
    assert got == expected
    assert len(got) == 16  # single block, no PKCS#7 padding


def test_derive_session_key_34_order_matters() -> None:
    # XOR is commutative, but ECB(real_key, .) is not symmetric in arg order:
    # both calls XOR to the same value, so the keys are actually equal here —
    # what we assert is determinism, not that swapping changes the result.
    a = b"\x01" * 16
    b = b"\x02" * 16
    assert derive_session_key_34(a, b, KEY_B) == derive_session_key_34(b, a, KEY_B)
    assert derive_session_key_34(a, b, KEY_B) != derive_session_key_34(
        a, b"\x03" * 16, KEY_B
    )


# ---------------------------------------------------------------------------
# encode / decode round trip
# ---------------------------------------------------------------------------


def test_codec_rejects_short_key() -> None:
    with pytest.raises(ValueError):
        Frame34Codec("short")


def test_frame34_control_roundtrip_with_inner_header() -> None:
    codec = Frame34Codec(KEY)
    body = {"dps": {"1": True, "2": 28}}
    wire = codec.encode(const.CMD_CONTROL, body)

    # 55AA envelope, 32-byte HMAC trailer + 4-byte suffix → size = N + 36.
    assert wire[:4] == b"\x00\x00\x55\xaa"
    assert wire[-4:] == b"\x00\x00\xaa\x55"
    size = struct.unpack(">I", wire[12:16])[0]
    assert size == len(wire) - 16  # header is 16 bytes, size covers payload+trailer

    # The version header is ENCRYPTED for v3.4 — it must not appear in the wire.
    assert const.PROTOCOL_34_HEADER not in wire

    frame, remainder = codec.decode(wire)
    assert remainder == b""
    assert frame.cmd == const.CMD_CONTROL
    _retcode, ciphertext = codec.split_response_payload(frame.cmd, frame.payload)
    assert codec.decrypt_body(ciphertext) == body


def test_frame34_dp_query_roundtrip_without_header() -> None:
    codec = Frame34Codec(KEY)
    body = {"gwId": "x", "devId": "x"}
    wire = codec.encode(const.CMD_DP_QUERY, body)
    frame, _ = codec.decode(wire)
    # DP_QUERY is header-less; the decrypted plaintext is JSON directly.
    _rc, ciphertext = codec.split_response_payload(frame.cmd, frame.payload)
    assert codec.decrypt_body(ciphertext) == body


def test_frame34_encode_decode_empty_body() -> None:
    codec = Frame34Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    frame, _ = codec.decode(wire)
    assert frame.cmd == const.CMD_HEART_BEAT
    assert codec.decrypt_body(frame.payload) == {}


def test_frame34_encode_raw_roundtrip() -> None:
    """Handshake frames carry a raw (AES-encrypted, header-less) payload."""
    codec = Frame34Codec(KEY)
    nonce = b"\xab" * 16
    wire = codec.encode_raw(const.SESS_KEY_NEG_START, nonce)
    frame, _ = codec.decode(wire)
    assert frame.cmd == const.SESS_KEY_NEG_START
    # payload is the AES ciphertext of the nonce; aes_decrypt recovers it.
    from pysilverline.protocol import aes_decrypt

    assert aes_decrypt(frame.payload, KEY_B) == nonce


def test_frame34_seq_increments() -> None:
    codec = Frame34Codec(KEY)
    w1 = codec.encode(const.CMD_HEART_BEAT, {})
    w2 = codec.encode(const.CMD_HEART_BEAT, {})
    assert struct.unpack(">I", w2[4:8])[0] == struct.unpack(">I", w1[4:8])[0] + 1


def test_frame34_extract_seq_from_wire() -> None:
    codec = Frame34Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    assert codec.extract_seq_from_wire(wire) == struct.unpack(">I", wire[4:8])[0]


def test_frame34_decode_incomplete_raises() -> None:
    codec = Frame34Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    with pytest.raises(IncompleteFrame):
        codec.decode(wire[:20])


def test_frame34_decode_bad_prefix_raises() -> None:
    codec = Frame34Codec(KEY)
    wire = bytearray(codec.encode(const.CMD_HEART_BEAT, {}))
    wire[2] ^= 0xFF
    with pytest.raises(ProtocolError, match="bad prefix"):
        codec.decode(bytes(wire))


def test_frame34_decode_bad_suffix_raises() -> None:
    codec = Frame34Codec(KEY)
    wire = bytearray(codec.encode(const.CMD_HEART_BEAT, {}))
    wire[-2] ^= 0xFF
    with pytest.raises(ProtocolError, match="bad suffix"):
        codec.decode(bytes(wire))


def test_frame34_decode_too_small_size_raises() -> None:
    """A size smaller than the HMAC trailer (e.g. a v3.3 CRC frame) is rejected."""
    codec = Frame34Codec(KEY)
    # Forge a header claiming size=8 (a v3.3-shaped frame) with enough bytes.
    header = struct.pack(">IIII", const.FRAME_PREFIX, 1, const.CMD_HEART_BEAT, 8)
    with pytest.raises(ProtocolError, match="too small"):
        codec.decode(header + b"\x00" * 40)


def test_frame34_hmac_trailer_detects_payload_tamper() -> None:
    """Flipping a ciphertext byte invalidates the keyed HMAC → InvalidAuth."""
    codec = Frame34Codec(KEY)
    wire = bytearray(codec.encode(const.CMD_CONTROL, {"dps": {"1": True}}))
    wire[20] ^= 0x01  # somewhere inside the ciphertext
    with pytest.raises(InvalidAuth, match="HMAC mismatch"):
        codec.decode(bytes(wire))


def test_frame34_decode_wrong_key_raises_invalid_auth() -> None:
    enc = Frame34Codec(KEY)
    dec = Frame34Codec("fedcba9876543210")
    wire = enc.encode(const.CMD_HEART_BEAT, {})
    with pytest.raises(InvalidAuth):
        dec.decode(wire)


def test_frame34_decode_two_frames_in_buffer() -> None:
    codec = Frame34Codec(KEY)
    w1 = codec.encode(const.CMD_HEART_BEAT, {})
    w2 = codec.encode(const.CMD_DP_QUERY, {"gwId": "x"})
    f1, rem = codec.decode(w1 + w2)
    f2, rem2 = codec.decode(rem)
    assert rem2 == b""
    assert f1.cmd == const.CMD_HEART_BEAT
    assert f2.cmd == const.CMD_DP_QUERY


# ---------------------------------------------------------------------------
# Session key update / reset
# ---------------------------------------------------------------------------


def test_frame34_session_key_update() -> None:
    enc = Frame34Codec(KEY)
    dec = Frame34Codec(KEY)
    dec.decode(enc.encode(const.CMD_HEART_BEAT, {}))  # both on real key — fine

    session_key = b"\xff" * 16
    enc.update_session_key(session_key)
    wire = enc.encode(const.CMD_HEART_BEAT, {})
    with pytest.raises(InvalidAuth):
        dec.decode(wire)  # dec still on real key → HMAC + AES mismatch

    dec.update_session_key(session_key)
    frame, _ = dec.decode(wire)
    assert frame.cmd == const.CMD_HEART_BEAT


def test_frame34_reset_restores_real_key() -> None:
    codec = Frame34Codec(KEY)
    enc = Frame34Codec(KEY)
    enc.update_session_key(b"\xaa" * 16)
    wire = enc.encode(const.CMD_HEART_BEAT, {})

    with pytest.raises(InvalidAuth):
        codec.decode(wire)  # real key can't read a session-key frame

    codec.update_session_key(b"\xaa" * 16)
    codec.reset()  # back to real key
    with pytest.raises(InvalidAuth):
        codec.decode(wire)

    codec.update_session_key(b"\xaa" * 16)
    frame, _ = codec.decode(wire)
    assert frame.cmd == const.CMD_HEART_BEAT


# ---------------------------------------------------------------------------
# split_response_payload / split_request_payload / decrypt_body
# ---------------------------------------------------------------------------


def test_frame34_split_response_strips_retcode_for_control() -> None:
    codec = Frame34Codec(KEY)
    ciphertext = aes_encrypt(b'{"dps":{"1":true}}', KEY_B)  # 16-aligned
    payload = struct.pack(">I", 0) + ciphertext  # len % 16 == 4 → retcode present
    rc, body = codec.split_response_payload(const.CMD_CONTROL, payload)
    assert rc == 0
    assert body == ciphertext


def test_frame34_split_response_no_retcode_for_status() -> None:
    codec = Frame34Codec(KEY)
    ciphertext = aes_encrypt(b'{"dps":{}}', KEY_B)
    rc, body = codec.split_response_payload(const.CMD_STATUS, ciphertext)
    assert rc is None
    assert body == ciphertext


def test_frame34_split_response_control_new_bare_ack() -> None:
    """A v3.4 CONTROL_NEW ACK can be a bare 4-byte retcode with no JSON body.

    The %16==4 heuristic peels the retcode and leaves an empty ciphertext, so
    decrypt_body(b"") returns {} — set_multiple only needs retcode == 0.
    """
    codec = Frame34Codec(KEY)
    rc, body = codec.split_response_payload(const.CMD_CONTROL_NEW, struct.pack(">I", 0))
    assert rc == 0
    assert body == b""
    assert codec.decrypt_body(body) == {}


def test_frame34_split_response_control_new_with_body() -> None:
    codec = Frame34Codec(KEY)
    ciphertext = aes_encrypt(b'{"dps":{"2":28}}', KEY_B)
    payload = struct.pack(">I", 0) + ciphertext
    rc, body = codec.split_response_payload(const.CMD_CONTROL_NEW, payload)
    assert rc == 0
    assert body == ciphertext


def test_frame34_split_request_strips_retcode_when_misaligned() -> None:
    codec = Frame34Codec(KEY)
    ciphertext = aes_encrypt(b'{"dps":{"3":31}}', KEY_B)
    assert codec.split_request_payload(struct.pack(">I", 0) + ciphertext) == ciphertext


def test_frame34_split_request_no_strip_when_aligned() -> None:
    codec = Frame34Codec(KEY)
    ciphertext = aes_encrypt(b'{"dps":{}}', KEY_B)  # multiple of 16, no retcode
    assert codec.split_request_payload(ciphertext) == ciphertext


def test_frame34_decrypt_body_strips_inner_header() -> None:
    codec = Frame34Codec(KEY)
    plaintext = const.PROTOCOL_34_HEADER + b'{"dps":{"2":28}}'
    assert codec.decrypt_body(aes_encrypt(plaintext, KEY_B)) == {"dps": {"2": 28}}


def test_frame34_decrypt_body_without_header() -> None:
    codec = Frame34Codec(KEY)
    assert codec.decrypt_body(aes_encrypt(b'{"a":1}', KEY_B)) == {"a": 1}


def test_frame34_decrypt_body_empty_returns_empty_dict() -> None:
    assert Frame34Codec(KEY).decrypt_body(b"") == {}


def test_frame34_decrypt_body_wrong_key_raises_invalid_auth() -> None:
    ciphertext = aes_encrypt(b'{"a":1}', KEY_B)
    with pytest.raises(InvalidAuth):
        Frame34Codec("fedcba9876543210").decrypt_body(ciphertext)


def test_frame34_decrypt_body_garbage_json_raises_protocol_error() -> None:
    codec = Frame34Codec(KEY)
    with pytest.raises(ProtocolError):
        codec.decrypt_body(aes_encrypt(b"not json", KEY_B))
