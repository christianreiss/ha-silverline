"""Round-trip and handshake tests for the v3.5 frame codec (6699 / AES-GCM)."""

from __future__ import annotations

import json
import struct

import pytest

from pysilverline import const
from pysilverline.client import _unwrap_dps
from pysilverline.exceptions import IncompleteFrame, InvalidAuth, ProtocolError
from pysilverline.models import DeviceState
from pysilverline.protocol import (
    Frame35Codec,
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    derive_session_key_35,
)

KEY = "0123456789abcdef"
KEY_B = KEY.encode()


# ---------------------------------------------------------------------------
# GCM helpers
# ---------------------------------------------------------------------------


def test_gcm_round_trip() -> None:
    iv = b"\x00" * 12
    aad = b"header"
    pt = b'{"dps":{"1":true}}'
    ct, tag = aes_gcm_encrypt(pt, KEY_B, iv, aad)
    assert ct != pt
    assert aes_gcm_decrypt(ct, KEY_B, iv, aad, tag) == pt


def test_gcm_decrypt_wrong_key_raises_invalid_auth() -> None:
    iv = b"\x00" * 12
    ct, tag = aes_gcm_encrypt(b"data", KEY_B, iv, b"")
    with pytest.raises(InvalidAuth):
        aes_gcm_decrypt(ct, b"abcdefghijklmnop", iv, b"", tag)


def test_gcm_decrypt_tampered_tag_raises_invalid_auth() -> None:
    iv = b"\x00" * 12
    ct, tag = aes_gcm_encrypt(b"data", KEY_B, iv, b"")
    bad_tag = bytes([tag[0] ^ 0xFF]) + tag[1:]
    with pytest.raises(InvalidAuth):
        aes_gcm_decrypt(ct, KEY_B, iv, b"", bad_tag)


def test_gcm_decrypt_tampered_aad_raises_invalid_auth() -> None:
    iv = b"\x00" * 12
    aad = b"correct-aad"
    ct, tag = aes_gcm_encrypt(b"data", KEY_B, iv, aad)
    with pytest.raises(InvalidAuth):
        aes_gcm_decrypt(ct, KEY_B, iv, b"wrong-aad", tag)


# ---------------------------------------------------------------------------
# Session key derivation
# ---------------------------------------------------------------------------


def test_derive_session_key_35_known_vector() -> None:
    """Verify session key derivation matches TinyTuya's logic.

    XOR nonces → AES-GCM encrypt with real key (IV = nonce[:12]) →
    first 16 bytes of (ciphertext+tag) output = session key.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    local_nonce = bytes(range(16))
    remote_nonce = bytes(range(16, 32))
    real_key = KEY_B

    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    iv = local_nonce[:12]
    expected = AESGCM(real_key).encrypt(iv, xored, None)[:16]

    assert derive_session_key_35(local_nonce, remote_nonce, real_key) == expected


def test_derive_session_key_35_differs_with_different_nonces() -> None:
    nonce_a = b"\x01" * 16
    nonce_b = b"\x02" * 16
    key1 = derive_session_key_35(nonce_a, nonce_b, KEY_B)
    key2 = derive_session_key_35(nonce_b, nonce_a, KEY_B)
    assert key1 != key2  # order matters (XOR is commutative, but IV differs)


# ---------------------------------------------------------------------------
# Frame35Codec — encode / decode round trip
# ---------------------------------------------------------------------------


def test_codec_rejects_short_key() -> None:
    with pytest.raises(ValueError):
        Frame35Codec("short")


def test_frame35_control_roundtrip_with_inner_header() -> None:
    codec = Frame35Codec(KEY)
    body = {"dps": {"1": True, "2": 28}}
    wire = codec.encode(const.CMD_CONTROL, body)

    # Wire must start with 6699 prefix and end with 9966 suffix
    assert wire[:4] == b"\x00\x00\x66\x99"
    assert wire[-4:] == b"\x00\x00\x99\x66"

    # The version header is ENCRYPTED — it must not appear in the wire.
    assert const.PROTOCOL_35_HEADER not in wire

    frame, remainder = codec.decode(wire)
    assert remainder == b""
    assert frame.cmd == const.CMD_CONTROL
    # Control writes carry the 15-byte version header before the JSON.
    assert frame.payload.startswith(const.PROTOCOL_35_HEADER)
    assert json.loads(frame.payload[len(const.PROTOCOL_35_HEADER) :]) == body


def test_frame35_dp_query_roundtrip_without_header() -> None:
    codec = Frame35Codec(KEY)
    body = {"gwId": "x", "devId": "x"}
    wire = codec.encode(const.CMD_DP_QUERY, body)
    frame, _ = codec.decode(wire)
    # DP_QUERY is header-less; the decrypted plaintext is JSON directly. This is
    # why reads worked on real v3.5 firmware while header-less writes did not.
    assert not frame.payload.startswith(const.PROTOCOL_35_HEADER)
    assert json.loads(frame.payload) == body


def test_frame35_control_new_plaintext_matches_tinytuya() -> None:
    """The decrypted CONTROL_NEW plaintext is byte-identical to the tinytuya
    write that powered Paulus385's unit on (issue #7).

    tinytuya put ``b"3.5" + 12*b"\\x00"`` + the compact protocol-5 envelope on the
    wire for ``set_value(1, True)`` and the heat pump switched on; pysilverline's
    header-less write was rejected (0x01000000). This pins our outbound plaintext
    to that confirmed-working byte sequence so the header can never regress.
    """
    codec = Frame35Codec(KEY)
    # Same envelope set_multiple builds, with a fixed timestamp for determinism.
    body = {"protocol": 5, "t": 1782749201, "data": {"dps": {"1": True}}}
    wire = codec.encode(const.CMD_CONTROL_NEW, body)
    frame, _ = codec.decode(wire)

    expected_json = b'{"protocol":5,"t":1782749201,"data":{"dps":{"1":true}}}'
    assert frame.payload == const.PROTOCOL_35_HEADER + expected_json
    # Header is exactly b"3.5" + 12 zero bytes (all-zero reserved, per tinytuya).
    assert const.PROTOCOL_35_HEADER == b"3.5" + b"\x00" * 12


def test_frame35_encode_decode_empty_body() -> None:
    codec = Frame35Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    frame, _ = codec.decode(wire)
    assert frame.cmd == const.CMD_HEART_BEAT
    assert json.loads(frame.payload) == {}


def test_frame35_encode_raw_roundtrip() -> None:
    codec = Frame35Codec(KEY)
    nonce = b"\xab" * 16
    wire = codec.encode_raw(const.SESS_KEY_NEG_START, nonce)
    frame, _ = codec.decode(wire)
    assert frame.cmd == const.SESS_KEY_NEG_START
    assert frame.payload == nonce


def test_frame35_seq_increments() -> None:
    codec = Frame35Codec(KEY)
    w1 = codec.encode(const.CMD_HEART_BEAT, {})
    w2 = codec.encode(const.CMD_HEART_BEAT, {})
    seq1 = struct.unpack(">I", w1[6:10])[0]
    seq2 = struct.unpack(">I", w2[6:10])[0]
    assert seq2 == seq1 + 1


def test_frame35_extract_seq_from_wire() -> None:
    codec = Frame35Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    seq = codec.extract_seq_from_wire(wire)
    assert seq == struct.unpack(">I", wire[6:10])[0]


def test_frame35_decode_incomplete_raises() -> None:
    codec = Frame35Codec(KEY)
    wire = codec.encode(const.CMD_HEART_BEAT, {})
    with pytest.raises(IncompleteFrame):
        codec.decode(wire[:10])


def test_frame35_decode_bad_prefix_raises() -> None:
    codec = Frame35Codec(KEY)
    wire = bytearray(codec.encode(const.CMD_HEART_BEAT, {}))
    wire[2] ^= 0xFF  # corrupt prefix
    with pytest.raises(ProtocolError, match="bad v3.5 prefix"):
        codec.decode(bytes(wire))


def test_frame35_decode_bad_suffix_raises() -> None:
    codec = Frame35Codec(KEY)
    wire = bytearray(codec.encode(const.CMD_HEART_BEAT, {}))
    wire[-2] ^= 0xFF  # corrupt suffix
    with pytest.raises(ProtocolError, match="bad v3.5 suffix"):
        codec.decode(bytes(wire))


def test_frame35_decode_wrong_key_raises_invalid_auth() -> None:
    enc_codec = Frame35Codec(KEY)
    dec_codec = Frame35Codec("fedcba9876543210")
    wire = enc_codec.encode(const.CMD_HEART_BEAT, {})
    with pytest.raises(InvalidAuth):
        dec_codec.decode(wire)


def test_frame35_decode_two_frames_in_buffer() -> None:
    codec = Frame35Codec(KEY)
    w1 = codec.encode(const.CMD_HEART_BEAT, {})
    w2 = codec.encode(const.CMD_DP_QUERY, {"gwId": "x"})
    buf = w1 + w2
    f1, rem = codec.decode(buf)
    f2, rem2 = codec.decode(rem)
    assert rem2 == b""
    assert f1.cmd == const.CMD_HEART_BEAT
    assert f2.cmd == const.CMD_DP_QUERY


# ---------------------------------------------------------------------------
# Session key update
# ---------------------------------------------------------------------------


def test_frame35_session_key_update() -> None:
    enc_codec = Frame35Codec(KEY)
    dec_codec = Frame35Codec(KEY)

    # Before update — both use real key
    wire = enc_codec.encode(const.CMD_HEART_BEAT, {})
    dec_codec.decode(wire)  # should not raise

    # After enc updates to a new session key, dec must also update
    session_key = b"\xff" * 16
    enc_codec.update_session_key(session_key)
    wire2 = enc_codec.encode(const.CMD_HEART_BEAT, {})
    with pytest.raises(InvalidAuth):
        dec_codec.decode(wire2)  # wrong key → tag mismatch

    dec_codec.update_session_key(session_key)
    frame, _ = dec_codec.decode(wire2)
    assert frame.cmd == const.CMD_HEART_BEAT


def test_frame35_reset_restores_real_key() -> None:
    codec = Frame35Codec(KEY)
    enc_codec = Frame35Codec(KEY)

    session_key = b"\xaa" * 16
    enc_codec.update_session_key(session_key)
    wire = enc_codec.encode(const.CMD_HEART_BEAT, {})

    # Real-key codec can't decode session-key frame
    with pytest.raises(InvalidAuth):
        codec.decode(wire)

    # After reset, session-key frame is still unreadable (reset restores real key)
    codec.update_session_key(session_key)
    codec.reset()
    with pytest.raises(InvalidAuth):
        codec.decode(wire)

    # But with session key applied, it decodes fine
    codec.update_session_key(session_key)
    frame, _ = codec.decode(wire)
    assert frame.cmd == const.CMD_HEART_BEAT


# ---------------------------------------------------------------------------
# split_response_payload / split_request_payload / decrypt_body
# ---------------------------------------------------------------------------


def test_frame35_split_response_payload_strips_retcode_for_control() -> None:
    retcode = (0).to_bytes(4, "big")
    body = b'{"result":"ok"}'
    payload = retcode + body
    rc, extracted = Frame35Codec.split_response_payload(const.CMD_CONTROL, payload)
    assert rc == 0
    assert extracted == body


def test_frame35_split_response_payload_no_retcode_for_status() -> None:
    payload = b'{"dps":{"1":true}}'
    rc, extracted = Frame35Codec.split_response_payload(const.CMD_STATUS, payload)
    assert rc is None
    assert extracted == payload


def test_frame35_split_request_payload_strips_retcode_prefix() -> None:
    retcode = b"\x00\x00\x00\x00"
    json_body = b'{"dps":{"1":true}}'
    payload = retcode + json_body
    assert Frame35Codec.split_request_payload(payload) == json_body


def test_frame35_split_request_payload_no_strip_when_starts_with_brace() -> None:
    payload = b'{"dps":{}}'
    assert Frame35Codec.split_request_payload(payload) == payload


# JetLine Selection FI (productKey 3bhylhz5zhogklel) v3.5 STATUS pushes arrive,
# after GCM decryption, as a 4-byte zero retcode + a 15-byte Tuya version header
# ("3.5" + 12 reserved) + a protocol-4 envelope. Captured on the HA community
# forum (topic 1011340, post #18). split_request_payload must peel both layers.


def _jetline_fi_status_body(dps: dict[str, int]) -> bytes:
    """Decrypted STATUS-push body mirroring the forum capture's framing."""
    retcode = b"\x00\x00\x00\x00"
    # "3.5" + the exact 12 reserved bytes seen on the wire (a counter + const).
    version_header = b"3.5" + bytes.fromhex("000000000000f93000000001")
    envelope = {"protocol": 4, "t": 1782549444, "data": {"dps": dps}}
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    return retcode + version_header + body


def test_frame35_split_request_payload_strips_version_header_with_retcode() -> None:
    body = _jetline_fi_status_body({"104": 74})
    stripped = Frame35Codec.split_request_payload(body)
    assert stripped.startswith(b'{"protocol":4')
    assert Frame35Codec.decrypt_body(stripped)["data"]["dps"] == {"104": 74}


def test_frame35_split_request_payload_strips_version_header_without_retcode() -> None:
    # Defensive: same header with no leading retcode peels at offset 0.
    body = b"3.5" + bytes.fromhex("000000000000f93000000001") + b'{"dps":{"2":28}}'
    assert Frame35Codec.split_request_payload(body) == b'{"dps":{"2":28}}'


def test_frame35_split_request_payload_keeps_unrecognized_framing() -> None:
    # Neither a version header nor a retcode+JSON shape: returned unchanged so
    # the caller logs and drops it rather than mis-slicing.
    payload = b"\x00\x01\x02\x03\x04\x05\x06"
    assert Frame35Codec.split_request_payload(payload) == payload


def test_jetline_fi_status_push_flows_into_device_state() -> None:
    # Full pipeline: split_request_payload -> decrypt_body -> _unwrap_dps ->
    # DeviceState. Proves the protocol-4 "data.dps" envelope behind the version
    # header lands real telemetry, which the old code dropped as undecryptable.
    body = _jetline_fi_status_body(
        {"104": 74, "103": 27, "102": 32, "111": 242, "120": 215}
    )
    decoded = Frame35Codec.decrypt_body(Frame35Codec.split_request_payload(body))
    dps = _unwrap_dps(decoded)
    assert dps == {"104": 74, "103": 27, "102": 32, "111": 242, "120": 215}
    state = DeviceState.from_dps(dps)
    assert state.discharge_temp == 74  # DP 104
    assert state.pool_temp == 27  # DP 103
    assert state.ambient_temp == 32  # DP 102
    assert state.water_pump is True  # DP 111 (non-zero int)
    assert state.total_hours == 215  # DP 120


def test_frame35_decrypt_body_parses_json() -> None:
    body = b'{"dps":{"2":28}}'
    assert Frame35Codec.decrypt_body(body) == {"dps": {"2": 28}}


def test_frame35_decrypt_body_empty_returns_empty_dict() -> None:
    assert Frame35Codec.decrypt_body(b"") == {}


def test_frame35_decrypt_body_invalid_json_raises_protocol_error() -> None:
    with pytest.raises(ProtocolError):
        Frame35Codec.decrypt_body(b"not json")
