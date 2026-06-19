"""End-to-end integration tests for SilverlineClient over Tuya v3.4 (55AA / ECB + HMAC).

These exercise the *client* handshake, auto-probe and fallback paths that the
codec-level ``test_protocol_34`` never touches.

``FakeTuya34Server`` is the oracle, and its faithfulness is the whole point —
because there is NO real v3.4 hardware (the live device is the v3.3 PC-SLP090N).
The fake mirrors TinyTuya's device side exactly on the two details that bit the
v3.5 implementation:

  * it decodes the START/FINISH negotiation frames with the *real* key and
    switches to the derived ECB session key only *after* FINISH is processed.
    A client that (wrongly) encrypted FINISH under the session key would fail
    the fake's real-key decode → handshake fails → ``get_status`` times out.
  * it answers data requests by *echoing the request seqno* (v3.4 is gated
    ``version < 3.5`` in TinyTuya's ``_get_retcode``, so it echoes — unlike
    v3.5's global counter). The client's exact ``(seq, cmd)`` match resolves it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import struct
from typing import Any

import pytest

from pysilverline import SilverlineClient, const
from pysilverline.exceptions import CannotConnect, InvalidAuth
from pysilverline.protocol import (
    Frame34Codec,
    aes_decrypt,
    aes_encrypt,
    derive_session_key_34,
)

KEY = "0123456789abcdef"
KEY_B = KEY.encode()
DEVICE_ID = "bf12345678abcdefghijkl"
# Fixed device nonce so derivations are deterministic across the test run.
REMOTE_NONCE = bytes(range(16, 32))
_SHA = hashlib.sha256


def _encode_34(
    seq: int, cmd: int, plaintext: bytes, key: bytes, *, retcode: int | None = None
) -> bytes:
    """Build one 55AA HMAC frame with an explicit seq and encryption/HMAC key.

    ``plaintext`` is AES-ECB-encrypted with ``key``; an optional unencrypted
    ``retcode`` is prepended (device→client frames carry one); the trailer is a
    32-byte HMAC-SHA256 over header+payload, also keyed with ``key`` (the real
    key during the handshake, the session key afterwards).
    """
    ciphertext = aes_encrypt(plaintext, key)
    payload = b"" if retcode is None else struct.pack(">I", retcode)
    payload += ciphertext
    size = len(payload) + 36  # 32-byte HMAC + 4-byte suffix
    header = struct.pack(">IIII", const.FRAME_PREFIX, seq, cmd, size)
    pre = header + payload
    mac = hmac.new(key, pre, _SHA).digest()
    return pre + mac + struct.pack(">I", const.FRAME_SUFFIX)


class FakeTuya34Server:
    """A tiny TCP server speaking Tuya local protocol v3.4."""

    def __init__(self, *, resp_key: bytes | None = None) -> None:
        self.handlers: dict[int, Any] = {}
        self.received: list[tuple[int, int, dict[str, Any]]] = []
        # When set, encrypt the NEG_RESP under this (wrong) key so the client
        # cannot authenticate it — simulates a wrong local_key.
        self._resp_key = resp_key
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0
        self.finish_decoded_with_real_key = False
        self.finish_hmac_ok = False
        self.session_key: bytes | None = None
        self.connections = 0
        self.drop_after_handshake = False
        # When True, close the TCP connection right after sending the first data
        # response — mirrors the real v3.4 WBR3 firmware's request-scoped socket.
        self.close_after_response = False
        self._writer: asyncio.StreamWriter | None = None

    async def __aenter__(self) -> "FakeTuya34Server":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def push(self, cmd: int, body: dict[str, Any], *, retcode: int | None) -> None:
        """Send a spontaneous frame under the session key.

        Real v3.4 firmware encrypts the version header inside STATUS pushes (the
        cmd is not header-less), so the fake does too — exercising the client's
        post-decrypt header strip on the push path."""
        if self._writer is None or self.session_key is None:
            raise RuntimeError("push before handshake complete")
        plaintext = const.PROTOCOL_34_HEADER + json.dumps(body).encode()
        wire = _encode_34(
            0x9999, cmd, plaintext, self.session_key, retcode=retcode
        )
        self._writer.write(wire)
        await self._writer.drain()

    async def push_malformed(self) -> None:
        """Send a session-key frame with a corrupted suffix (clean desync drop)."""
        if self._writer is None or self.session_key is None:
            raise RuntimeError("push before handshake complete")
        wire = bytearray(
            _encode_34(
                0x1234, const.CMD_STATUS, b'{"dps":{}}', self.session_key, retcode=0
            )
        )
        wire[-2] ^= 0xFF
        self._writer.write(bytes(wire))
        await self._writer.drain()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writer = writer
        self.connections += 1
        # Codec starts on the real key — every negotiation frame must decode
        # under it; we switch to the session key only after FINISH.
        codec = Frame34Codec(KEY)
        local_nonce = b""
        session_key: bytes | None = None
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                while len(buf) >= 52:  # 16-byte header + 36-byte trailer minimum
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        # Real-key decode failure: the peer encrypted a
                        # negotiation frame with the wrong key (the
                        # FINISH-under-session-key bug) or it is not a v3.4
                        # peer. A real device drops the socket — so do we, which
                        # turns the bug into an observable RED.
                        return
                    buf = bytearray(remainder)

                    if frame.cmd == const.SESS_KEY_NEG_START:
                        local_nonce = aes_decrypt(frame.payload, KEY_B)
                        inner = REMOTE_NONCE + hmac.new(
                            KEY_B, local_nonce, _SHA
                        ).digest()
                        resp_key = self._resp_key or KEY_B
                        writer.write(
                            _encode_34(
                                frame.seq,
                                const.SESS_KEY_NEG_RESP,
                                inner,
                                resp_key,
                                retcode=0,
                            )
                        )
                        await writer.drain()
                    elif frame.cmd == const.SESS_KEY_NEG_FINISH:
                        # Reaching here means FINISH decoded under the REAL key.
                        self.finish_decoded_with_real_key = True
                        got = aes_decrypt(frame.payload, KEY_B)
                        expected = hmac.new(KEY_B, REMOTE_NONCE, _SHA).digest()
                        self.finish_hmac_ok = hmac.compare_digest(got, expected)
                        session_key = derive_session_key_34(
                            local_nonce, REMOTE_NONCE, KEY_B
                        )
                        self.session_key = session_key
                        codec.update_session_key(session_key)
                        if self.drop_after_handshake and self.connections == 1:
                            return
                    else:
                        body = codec.split_request_payload(frame.payload)
                        decrypted = codec.decrypt_body(body) if body else {}
                        self.received.append((frame.seq, frame.cmd, decrypted))
                        handler = self.handlers.get(frame.cmd)
                        if handler is not None:
                            assert session_key is not None
                            # v3.4 ECHOES the request seqno (TinyTuya's <3.5 gate).
                            response = handler(frame.seq, decrypted, session_key)
                            if response is not None:
                                writer.write(response)
                                await writer.drain()
                        if self.close_after_response:
                            # Real v3.4 firmware hangs up after each response.
                            return
        finally:
            writer.close()


def _dp_query_handler(dps: dict[str, Any]) -> Any:
    def handler(seq: int, body: dict[str, Any], session_key: bytes) -> bytes:
        # DP_QUERY responses are header-less.
        payload = json.dumps({"devId": DEVICE_ID, "dps": dps}).encode()
        return _encode_34(seq, const.CMD_DP_QUERY, payload, session_key, retcode=0)

    return handler


def _dps_from_control_new(body: dict[str, Any]) -> dict[str, Any]:
    """Pull the dps out of a v3.4 CONTROL_NEW (protocol:5) write body."""
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("dps"), dict):
        return data["dps"]
    return body.get("dps", {})


def _control_new_handler() -> Any:
    """Ack a CONTROL_NEW write with a dedicated CONTROL_NEW frame (retcode 0)."""

    def handler(seq: int, body: dict[str, Any], session_key: bytes) -> bytes:
        payload = const.PROTOCOL_34_HEADER + json.dumps(
            {"dps": _dps_from_control_new(body)}
        ).encode()
        return _encode_34(seq, const.CMD_CONTROL_NEW, payload, session_key, retcode=0)

    return handler


def _control_new_ack_via_status_handler() -> Any:
    """Ack a CONTROL_NEW write by echoing state via a STATUS push instead of a
    dedicated ACK — the common real-device behavior. The STATUS push carries a
    device-global seqno (not the request's) and the ``data.dps`` wrapper, so it
    exercises the client's cmd-fallback correlation and data.dps unwrapping."""

    def handler(seq: int, body: dict[str, Any], session_key: bytes) -> bytes:
        dps = _dps_from_control_new(body)
        payload = const.PROTOCOL_34_HEADER + json.dumps({"data": {"dps": dps}}).encode()
        return _encode_34(0xB00C, const.CMD_STATUS, payload, session_key, retcode=0)

    return handler


# ---------------------------------------------------------------------------
# Handshake + data round-trips
# ---------------------------------------------------------------------------


async def test_v34_autoprobe_handshake_and_get_status() -> None:
    """Auto-probe (no pinned version) negotiates v3.4 and reads DPs end to end."""
    async with FakeTuya34Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler(
            {"1": True, "4": "Heat", "3": 27, "2": 30}
        )
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version=None,  # auto-probe: v3.5 fails, v3.4 wins
            request_timeout=2.0,
        )
        await client.connect()
        try:
            assert client.detected_version == "3.4"
            state = await client.get_status()
            assert state.power is True
            assert state.mode == "Heat"
            assert state.temp_current == 27
            assert state.temp_set == 30
            # Handshake proof: a data round-trip is only possible because the
            # device decoded FINISH under the real key (then switched to the
            # session key) and the FINISH HMAC verified.
            assert server.finish_decoded_with_real_key is True
            assert server.finish_hmac_ok is True
        finally:
            await client.disconnect()


async def test_v34_pinned_handshake_and_get_status() -> None:
    """Pinning protocol_version='3.4' skips the probe and negotiates directly."""
    async with FakeTuya34Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": False})
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        await client.connect()
        try:
            assert client.detected_version == "3.4"
            assert (await client.get_status()).power is False
        finally:
            await client.disconnect()


async def test_v34_set_multiple_uses_control_new() -> None:
    """v3.4 writes go via CONTROL_NEW (0x0D) with a protocol:5 / data.dps body."""
    async with FakeTuya34Server() as server:
        server.handlers[const.CMD_CONTROL_NEW] = _control_new_handler()
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        await client.connect()
        try:
            await client.set_multiple({const.DP_TEMP_SET: 28, const.DP_POWER: True})
            # The device received exactly one CONTROL_NEW (not legacy CONTROL),
            # carrying both DPs inside the protocol:5 data.dps wrapper.
            controls = [r for r in server.received if r[1] == const.CMD_CONTROL_NEW]
            assert len(controls) == 1
            assert not [r for r in server.received if r[1] == const.CMD_CONTROL]
            body = controls[0][2]
            assert body["protocol"] == 5
            assert body["data"]["dps"] == {"2": 28, "1": True}
            # Optimistic local merge reflects the write.
            assert client.state.temp_set == 28
            assert client.state.power is True
        finally:
            await client.disconnect()


async def test_v34_set_multiple_acked_via_status_push() -> None:
    """A v3.4 device that acks a write with a STATUS push (not a CONTROL_NEW
    frame) still resolves the write — the client correlates the push to the
    outstanding CONTROL_NEW by cmd and unwraps the data.dps echo."""
    async with FakeTuya34Server() as server:
        server.handlers[const.CMD_CONTROL_NEW] = _control_new_ack_via_status_handler()
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        await client.connect()
        try:
            await client.set_multiple({const.DP_TEMP_SET: 29})
            assert client.state.temp_set == 29
        finally:
            await client.disconnect()


@pytest.mark.parametrize("with_retcode", [True, False])
async def test_v34_push_is_dispatched_to_listener(with_retcode: bool) -> None:
    """A spontaneous device push over v3.4 reaches listeners, with or without
    the optional 4-byte retcode prefix (the len%16 heuristic handles both)."""
    async with FakeTuya34Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True})
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        seen: list[Any] = []
        client.add_listener(lambda state: seen.append(state))
        await client.connect()
        try:
            await client.get_status()  # establishes session_key on the server
            await server.push(
                const.CMD_STATUS, {"dps": {"3": 31}}, retcode=0 if with_retcode else None
            )
            for _ in range(50):
                if seen:
                    break
                await asyncio.sleep(0.02)
            assert seen, "push was never dispatched"
            assert seen[-1].temp_current == 31
        finally:
            await client.disconnect()


async def test_v34_lazy_reconnect_after_idle_close() -> None:
    """The v3.4 device closes TCP after each response; the client reconnects
    lazily on the next poll and re-handshakes, without flapping 'unavailable'.

    Proves the request-scoped socket lifecycle: a clean peer-close is treated as
    idle (no connection-lost notification), and the next get_status transparently
    opens a fresh connection (connection count climbs each poll).
    """
    async with FakeTuya34Server() as server:
        server.close_after_response = True
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True, "3": 26})
        lost: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        client.add_connection_listener(lambda up: lost.append(up))
        await client.connect()
        try:
            # First poll on connection #1.
            assert (await client.get_status()).temp_current == 26
            # Give the read loop a moment to observe the device's idle close.
            for _ in range(50):
                if not client.connected:
                    break
                await asyncio.sleep(0.02)
            assert not client.connected  # socket torn down quietly
            # Second poll must transparently re-open + re-handshake (connection #2).
            assert (await client.get_status()).temp_current == 26
            assert server.connections >= 2
            # The idle close must NOT have surfaced as a connection-lost (False).
            assert False not in lost
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# Auto-probe ordering, fallback, and pinned-version failure
# ---------------------------------------------------------------------------


async def test_v34_autoprobe_falls_back_to_v33() -> None:
    """A v3.3-only device: auto-probe tries v3.5, then v3.4, then lands on v3.3.

    The v3.4 probe sends a 55AA 0x03 frame the v3.3 fake cannot parse, so it
    drops the socket — exactly the fallback path that must NOT misfire as
    InvalidAuth (55AA framing is shared with v3.3)."""
    from tests.test_client import FakeTuyaServer, _build_frame

    async with FakeTuyaServer() as server:
        server.handlers[const.CMD_DP_QUERY] = lambda seq, body: _build_frame(
            seq,
            const.CMD_DP_QUERY,
            {"devId": DEVICE_ID, "dps": {"1": True, "4": "Cool"}},
        )
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version=None,
            request_timeout=2.0,
        )
        await client.connect()
        try:
            assert client.detected_version == "3.3"
            state = await client.get_status()
            assert state.power is True
            assert state.mode == "Cool"
        finally:
            await client.disconnect()


async def test_pinned_v34_against_v33_server_raises() -> None:
    """Pinning v3.4 against a v3.3-only device fails loudly (no silent fallback)."""
    from tests.test_client import FakeTuyaServer

    async with FakeTuyaServer() as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        with pytest.raises(CannotConnect):
            await client.connect()
        await client.disconnect()


async def test_pinned_v34_wrong_key_raises_invalid_auth() -> None:
    """A NEG_RESP the client can't authenticate surfaces as InvalidAuth when
    v3.4 is pinned — the reauth-trigger path, not CannotConnect."""
    async with FakeTuya34Server(resp_key=b"wrongkeywrongk!!") as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        with pytest.raises(InvalidAuth):
            await client.connect()
        await client.disconnect()
