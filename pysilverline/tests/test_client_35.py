"""End-to-end integration tests for SilverlineClient over Tuya v3.5 (6699 / GCM).

These exercise the *client* handshake and auto-probe paths (client.py lines
130-158 and 192-269) that the codec-level ``test_protocol_35`` never touches.

The ``FakeTuya35Server`` is the oracle, and its key-switch timing is the whole
point: a real v3.5 device decrypts the START/RESP/FINISH negotiation frames
with the *real* local key, switching to the derived session key only for data
frames *after* FINISH (verified against TinyTuya's ``_negotiate_session_key``).
The fake server mirrors that exactly. If it instead switched to the session key
right after sending RESP, it would happily decode a client that (wrongly)
encrypts FINISH under the session key, and these tests would prove nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import struct
from typing import Any

import pytest

from pysilverline import SilverlineClient, const
from pysilverline.exceptions import CannotConnect
from pysilverline.protocol import (
    Frame35Codec,
    aes_gcm_encrypt,
    derive_session_key_35,
)

KEY = "0123456789abcdef"
KEY_B = KEY.encode()
DEVICE_ID = "bf12345678abcdefghijkl"
# Fixed device nonce so derivations are deterministic across the test run.
REMOTE_NONCE = bytes(range(16, 32))


def _encode_35(seq: int, cmd: int, plaintext: bytes, key: bytes) -> bytes:
    """Build one 6699 frame with an explicit seq and encryption key.

    Mirrors ``Frame35Codec._build_frame`` but lets the caller pin ``seq`` (the
    server emits a device-global response seqno via ``_next_resp_seq``, NOT an
    echo of the request — see that method) and the key, since the server must
    respond under whichever key (real vs session) is currently live.
    """
    iv = os.urandom(12)
    length = 12 + len(plaintext) + 16
    header = struct.pack(">IHIII", const.FRAME_PREFIX_35, 0, seq, cmd, length)
    aad = header[4:]
    ciphertext, tag = aes_gcm_encrypt(plaintext, key, iv, aad)
    return header + iv + ciphertext + tag + struct.pack(">I", const.FRAME_SUFFIX_35)


class FakeTuya35Server:
    """A tiny TCP server speaking Tuya local protocol v3.5.

    Performs the device side of the 3-message session-key negotiation, then
    decodes data frames under the session key and replies via per-command
    handlers. Handlers receive ``(seq, decrypted_body, session_key)`` and
    return raw response bytes (or ``None``).
    """

    def __init__(
        self, *, retcode_in_resp: bool = False, resp_key: bytes | None = None
    ) -> None:
        self.handlers: dict[int, Any] = {}
        self.received: list[tuple[int, int, dict[str, Any]]] = []
        self._retcode_in_resp = retcode_in_resp
        # When set, encrypt the NEG_RESP frame under this (wrong) key to
        # simulate a device whose response the client cannot decrypt.
        self._resp_key = resp_key
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0
        # Observability for assertions / debugging.
        self.finish_decoded_with_real_key = False
        self.finish_hmac_ok = False
        self.session_key: bytes | None = None
        # Number of TCP connections accepted — lets a test assert that the
        # client re-handshakes on reconnect (connections >= 2).
        self.connections = 0
        # When True, the device hangs up right after the FIRST connection's
        # handshake completes, forcing the client to reconnect + re-handshake.
        self.drop_after_handshake = False
        # A writer we can push spontaneous frames through, set once connected.
        self._writer: asyncio.StreamWriter | None = None
        # Device-global response seqno. Real v3.5 devices answer with their own
        # monotonically-increasing counter, NOT the request's seqno (TinyTuya
        # XenonDevice._get_retcode only compares seqno for version < 3.5). It
        # starts well above the client's handful of per-connection request seqs
        # so a response seq never coincides with a pending request seq — a
        # client that (wrongly) matches responses by seqno then times out,
        # turning the correlation bug into an observable RED.
        self._resp_seq = 0x8000

    def _next_resp_seq(self) -> int:
        self._resp_seq += 1
        return self._resp_seq

    async def __aenter__(self) -> "FakeTuya35Server":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def push(self, cmd: int, body: dict[str, Any]) -> None:
        """Send a spontaneous (device-initiated) frame under the session key."""
        if self._writer is None or self.session_key is None:
            raise RuntimeError("push before handshake complete")
        wire = _encode_35(0xABCD, cmd, json.dumps(body).encode(), self.session_key)
        self._writer.write(wire)
        await self._writer.drain()

    async def push_malformed(self) -> None:
        """Send a session-key frame with a corrupted v3.5 suffix.

        ``Frame35Codec.decode`` validates the suffix before decrypting, so this
        raises ``ProtocolError`` in the client read loop (the clean desync-drop
        path), not a GCM ``InvalidAuth``.
        """
        if self._writer is None or self.session_key is None:
            raise RuntimeError("push before handshake complete")
        wire = bytearray(
            _encode_35(0x1234, const.CMD_STATUS, b'{"dps":{}}', self.session_key)
        )
        wire[-2] ^= 0xFF  # corrupt the 9966 suffix
        self._writer.write(bytes(wire))
        await self._writer.drain()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writer = writer
        self.connections += 1
        # The server's codec starts on the real key — every negotiation frame
        # (START / FINISH) MUST decode under it. We only switch after FINISH.
        codec = Frame35Codec(KEY)
        local_nonce = b""
        session_key: bytes | None = None
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                while len(buf) >= 18:
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        # A real-key decode failure here means the peer
                        # encrypted a negotiation frame with the wrong key
                        # (the FINISH-under-session-key bug) — or it's a v3.3
                        # peer whose 55AA prefix we can't parse. Either way the
                        # device drops the socket. Closing here is what turns
                        # the bug into an observable RED (client's first data
                        # request then times out).
                        return
                    buf = bytearray(remainder)

                    if frame.cmd == const.SESS_KEY_NEG_START:
                        local_nonce = frame.payload  # 16-byte client nonce
                        resp = (
                            REMOTE_NONCE
                            + hmac.new(KEY_B, local_nonce, hashlib.sha256).digest()
                        )
                        if self._retcode_in_resp:
                            resp = b"\x00\x00\x00\x00" + resp
                        resp_key = self._resp_key or codec._key
                        writer.write(
                            _encode_35(
                                frame.seq, const.SESS_KEY_NEG_RESP, resp, resp_key
                            )
                        )
                        await writer.drain()
                    elif frame.cmd == const.SESS_KEY_NEG_FINISH:
                        # If we got here, the FINISH frame decoded under the
                        # REAL key — exactly what a real device requires.
                        self.finish_decoded_with_real_key = True
                        expected = hmac.new(
                            KEY_B, REMOTE_NONCE, hashlib.sha256
                        ).digest()
                        self.finish_hmac_ok = hmac.compare_digest(
                            frame.payload, expected
                        )
                        # Only NOW switch to the session key, for data frames.
                        session_key = derive_session_key_35(
                            local_nonce, REMOTE_NONCE, KEY_B
                        )
                        self.session_key = session_key
                        codec.update_session_key(session_key)
                        if self.drop_after_handshake and self.connections == 1:
                            # Hang up on the first connection only, so the
                            # client must reconnect and negotiate a fresh
                            # session key on connection #2.
                            return
                    else:
                        body = codec.split_request_payload(frame.payload)
                        decrypted = codec.decrypt_body(body) if body else {}
                        self.received.append((frame.seq, frame.cmd, decrypted))
                        handler = self.handlers.get(frame.cmd)
                        if handler is not None:
                            assert session_key is not None
                            # Respond with a device-global seqno, NOT the
                            # request's — see _next_resp_seq.
                            response = handler(
                                self._next_resp_seq(), decrypted, session_key
                            )
                            if response is not None:
                                writer.write(response)
                                await writer.drain()
        finally:
            writer.close()


def _dp_query_handler(dps: dict[str, Any]) -> Any:
    def handler(seq: int, body: dict[str, Any], session_key: bytes) -> bytes:
        payload = (
            struct.pack(">I", 0) + json.dumps({"devId": DEVICE_ID, "dps": dps}).encode()
        )
        return _encode_35(seq, const.CMD_DP_QUERY, payload, session_key)

    return handler


def _control_new_handler(ack: bytes = b"\x01\x00\x00\x00") -> Any:
    def handler(seq: int, body: dict[str, Any], session_key: bytes) -> bytes:
        # Real v3.5 firmware accepts control writes on CONTROL_NEW (0x0d) wrapped
        # in a protocol-5 envelope (``data.dps``), not the v3.3 CONTROL (0x07).
        # The default ack mirrors the device in issue #7 (productKey
        # b4zr9ugt1q8xn9af): a 4-byte 01 00 00 00 that the codec peels as a
        # 0x01000000 "retcode" even though the write applied. A previous version
        # of this test answered with a 4-byte ZERO retcode, which was circular —
        # it only proved the client accepts the exact ack the mock chose to send.
        return _encode_35(seq, const.CMD_CONTROL_NEW, ack, session_key)

    return handler


# ---------------------------------------------------------------------------
# Handshake + data round-trips
# ---------------------------------------------------------------------------


async def test_v35_autoprobe_handshake_and_get_status() -> None:
    """Auto-probe (no pinned version) negotiates v3.5 and reads DPs end to end."""
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler(
            {"1": True, "4": "Heat", "3": 27, "2": 30}
        )
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version=None,  # auto-probe
            request_timeout=2.0,
        )
        await client.connect()
        try:
            assert client.detected_version == "3.5"

            state = await client.get_status()
            assert state.power is True
            assert state.mode == "Heat"
            assert state.temp_current == 27
            assert state.temp_set == 30

            # The handshake proof: a successful data round-trip is only
            # possible because the device-side decoded FINISH under the real
            # key (then switched to the session key) and the FINISH HMAC
            # verified. Asserted after get_status so the server has provably
            # processed FINISH (no race on connect() return).
            assert server.finish_decoded_with_real_key is True
            assert server.finish_hmac_ok is True
        finally:
            await client.disconnect()


async def test_v35_pinned_handshake_and_get_status() -> None:
    """Pinning protocol_version='3.5' skips the probe and negotiates directly."""
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": False})
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=2.0,
        )
        await client.connect()
        try:
            assert client.detected_version == "3.5"
            state = await client.get_status()
            assert state.power is False
        finally:
            await client.disconnect()


async def test_v35_response_seq_does_not_echo_request() -> None:
    """Regression: v3.5 responses carry a device-global seqno, not our echo.

    The fake answers DP_QUERY with ``_next_resp_seq()`` — a counter starting at
    0x8000, far above the client's per-connection request seqs — so the
    response seq provably never equals the request seq. A client that
    correlates responses by seqno (the v3.3 model) would never resolve the
    request future and ``get_status`` would raise ``CannotConnect`` on timeout.
    This passes only because the client matches v3.5 responses by cmd. Verified
    against TinyTuya ``XenonDevice._get_retcode`` (seqno compared only for
    ``version < 3.5``).
    """
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler(
            {"1": True, "4": "Heat", "3": 27}
        )
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=2.0,
        )
        await client.connect()
        try:
            state = await client.get_status()
            assert state.power is True
            assert state.mode == "Heat"
            assert state.temp_current == 27
            # Prove the premise: the server's response seq was its own global
            # counter (>= 0x8001), never the request's small seqno.
            assert server._resp_seq >= 0x8001
        finally:
            await client.disconnect()


async def test_v35_handshake_with_retcode_in_resp() -> None:
    """Some firmwares prefix the NEG_RESP payload with a 4-byte retcode."""
    async with FakeTuya35Server(retcode_in_resp=True) as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True})
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=2.0,
        )
        await client.connect()
        try:
            assert client.detected_version == "3.5"
            state = await client.get_status()
            assert state.power is True
        finally:
            await client.disconnect()


async def test_v35_set_multiple_round_trip() -> None:
    """A CONTROL_NEW write negotiates and is accepted over v3.5.

    Real v3.5 firmware mirrors TinyTuya (CONTROL→CONTROL_NEW for version >= 3.4):
    control writes go out on opcode 0x0d wrapped in a protocol-5 envelope, not
    the v3.3 0x07 CONTROL. A device that only handles 0x0d leaves a 0x07 write
    unanswered → ``timeout waiting for cmd 0x07`` (forum report, v3.5 pump).

    The mock answers CONTROL_NEW with the real device's ``01 00 00 00`` ack
    (issue #7), which the codec peels as a 0x01000000 "retcode". This call must
    therefore NOT raise: a non-zero retcode on a v3.5 control-ack is not a
    failure signal (see ``set_multiple``). A green run guards the intended wire
    behaviour but does not substitute for validation on a real v3.5 device.
    """
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_CONTROL_NEW] = _control_new_handler()
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=2.0,
        )
        await client.connect()
        try:
            await client.set_multiple({const.DP_TEMP_SET: 28, const.DP_POWER: True})
            # The device received exactly one CONTROL_NEW carrying both DPs in
            # the protocol-5 envelope (raw DP ids under data.dps).
            controls = [r for r in server.received if r[1] == const.CMD_CONTROL_NEW]
            assert len(controls) == 1
            assert controls[0][2]["protocol"] == 5
            assert controls[0][2]["data"]["dps"] == {"2": 28, "1": True}
            # Optimistic local merge reflects the write.
            assert client.state.temp_set == 28
            assert client.state.power is True
        finally:
            await client.disconnect()


async def test_v35_push_is_dispatched_to_listener() -> None:
    """A spontaneous device push over v3.5 reaches registered listeners."""
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True})
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=2.0,
        )
        seen: list[Any] = []
        client.add_listener(lambda state: seen.append(state))
        await client.connect()
        try:
            await client.get_status()  # establishes session_key on server side
            await server.push(const.CMD_STATUS, {"dps": {"3": 31}})
            # Give the read loop a moment to dispatch.
            for _ in range(50):
                if seen:
                    break
                await asyncio.sleep(0.02)
            assert seen, "push was never dispatched"
            assert seen[-1].temp_current == 31
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# Auto-probe fallback to v3.3 and pinned-version failure
# ---------------------------------------------------------------------------


async def test_autoprobe_falls_back_to_v33(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the v3.5 handshake fails, an unpinned client falls back to v3.3."""
    # Reuse the v3.3 fake server from the sibling test module.
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
            protocol_version=None,  # auto-probe; v3.5 must fail then fall back
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


async def test_v35_undecryptable_resp_raises_invalid_auth() -> None:
    """A NEG_RESP the client can't decrypt surfaces as InvalidAuth — the
    reauth-trigger path — not CannotConnect and not a silent v3.3 fallback.

    Even under auto-probe (protocol_version=None), a GCM tag mismatch during
    the handshake must propagate as InvalidAuth so the integration prompts the
    user to re-enter the local key, rather than masquerading as a v3.3 device.
    """
    from pysilverline.exceptions import InvalidAuth

    async with FakeTuya35Server(resp_key=b"wrongkeywrongk!!") as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version=None,  # auto-probe must NOT swallow this as fallback
            request_timeout=2.0,
        )
        with pytest.raises(InvalidAuth):
            await client.connect()
        await client.disconnect()


async def test_pinned_v35_against_v33_server_raises() -> None:
    """Pinning v3.5 against a v3.3-only device fails loudly (no silent fallback)."""
    from tests.test_client import FakeTuyaServer

    async with FakeTuyaServer() as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=2.0,
        )
        with pytest.raises(CannotConnect):
            await client.connect()
        await client.disconnect()
