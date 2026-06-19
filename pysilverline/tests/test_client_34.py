"""End-to-end integration tests for SilverlineClient over Tuya v3.4 (55AA / ECB)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import struct
from typing import Any

import pytest

from pysilverline import SilverlineClient, const
from pysilverline.exceptions import CannotConnect
from pysilverline.protocol import Frame34Codec, aes_encrypt, derive_session_key_34

KEY = "0123456789abcdef"
KEY_B = KEY.encode()
DEVICE_ID = "bf12345678abcdefghijkl"
REMOTE_NONCE = bytes(range(16, 32))


def _build_frame_34(
    seq: int, cmd: int, plaintext: bytes, session_key: bytes, *, retcode: int | None = 0
) -> bytes:
    import hashlib
    import hmac as hmac_mod

    blob = plaintext
    if cmd not in const.CMDS_WITHOUT_HEADER_V34:
        blob = const.PROTOCOL_34_HEADER + blob
    ciphertext = aes_encrypt(blob, session_key)
    footer_size = struct.calcsize(">32sI")
    size = len(ciphertext) + footer_size
    if retcode is not None:
        size += 4
    header = struct.pack(">IIII", const.FRAME_PREFIX, seq, cmd, size)
    if retcode is not None:
        body = header + struct.pack(">I", retcode) + ciphertext
    else:
        body = header + ciphertext
    mac = hmac_mod.new(session_key, body, hashlib.sha256).digest()
    return body + struct.pack(">32sI", mac, const.FRAME_SUFFIX)


class FakeTuya34Server:
    """TCP server speaking Tuya v3.4 — mirrors FakeTuya35Server key timing."""

    def __init__(
        self, *, retcode_in_resp: bool = False, close_after_response: bool = False
    ) -> None:
        self.handlers: dict[int, Any] = {}
        self.received: list[tuple[int, int, dict[str, Any]]] = []
        self._retcode_in_resp = retcode_in_resp
        self._close_after_response = close_after_response
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0
        self.finish_decoded_with_real_key = False
        self.finish_hmac_ok = False
        self.session_key: bytes | None = None
        self.connections = 0
        self._writer: asyncio.StreamWriter | None = None

    async def __aenter__(self) -> "FakeTuya34Server":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writer = writer
        self.connections += 1
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
                while len(buf) >= 16:
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        return
                    buf = bytearray(remainder)

                    if frame.cmd == const.SESS_KEY_NEG_START:
                        local_nonce = frame.payload[-16:]
                        resp = (
                            REMOTE_NONCE
                            + hmac.new(KEY_B, local_nonce, hashlib.sha256).digest()
                        )
                        if self._retcode_in_resp:
                            resp = b"\x00\x00\x00\x00" + resp
                        writer.write(
                            _build_frame_34(
                                frame.seq,
                                const.SESS_KEY_NEG_RESP,
                                resp,
                                KEY_B,
                                retcode=0,
                            )
                        )
                        await writer.drain()
                    elif frame.cmd == const.SESS_KEY_NEG_FINISH:
                        self.finish_decoded_with_real_key = True
                        expected = hmac.new(
                            KEY_B, REMOTE_NONCE, hashlib.sha256
                        ).digest()
                        self.finish_hmac_ok = hmac.compare_digest(
                            frame.payload[-32:], expected
                        )
                        session_key = derive_session_key_34(
                            local_nonce, REMOTE_NONCE, KEY_B
                        )
                        self.session_key = session_key
                        codec.update_session_key(session_key)
                    else:
                        body = codec.split_request_payload(frame.payload)
                        decrypted = codec.decrypt_body(body) if body else {}
                        self.received.append((frame.seq, frame.cmd, decrypted))
                        handler = self.handlers.get(frame.cmd)
                        if handler is not None:
                            assert session_key is not None
                            response = handler(frame.seq, decrypted, session_key)
                            if response is not None:
                                writer.write(response)
                                await writer.drain()
                                if self._close_after_response:
                                    return
        finally:
            writer.close()


def _dp_query_handler(dps: dict[str, Any]) -> Any:
    def handler(seq: int, body: dict[str, Any], session_key: bytes) -> bytes:
        plaintext = json.dumps({"devId": DEVICE_ID, "dps": dps}).encode()
        return _build_frame_34(
            seq, const.CMD_DP_QUERY, plaintext, session_key, retcode=0
        )

    return handler


async def test_v34_autoprobe_handshake_and_get_status() -> None:
    async with FakeTuya34Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler(
            {"1": True, "4": "Heat", "3": 27, "2": 30}
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
            assert client.detected_version == "3.4"
            state = await client.get_status()
            assert state.power is True
            assert state.mode == "Heat"
            assert state.temp_current == 27
            assert state.temp_set == 30
            assert server.finish_decoded_with_real_key is True
            assert server.finish_hmac_ok is True
        finally:
            await client.disconnect()


async def test_v34_pinned_handshake_and_get_status() -> None:
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
            state = await client.get_status()
            assert state.power is False
        finally:
            await client.disconnect()


async def test_v34_peer_close_after_query_is_silent_and_next_poll_reconnects() -> None:
    async with FakeTuya34Server(close_after_response=True) as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler(
            {"1": True, "4": "Heat", "3": 29, "2": 30}
        )
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.4",
            request_timeout=2.0,
        )
        connection_events: list[bool] = []
        client.add_connection_listener(connection_events.append)
        await client.connect()
        try:
            state = await client.get_status()
            assert state.power is True
            # Let the read loop observe the peer's clean post-response close.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert connection_events == [True]
            assert client.connected is False

            state = await client.get_status()
            assert state.temp_current == 29
            assert server.connections == 2
            assert connection_events == [True, True]
        finally:
            await client.disconnect()


async def test_autoprobe_skips_v34_when_only_v33() -> None:
    """Auto-probe tries v3.5, then v3.4, then lands on v3.3."""
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
            assert state.mode == "Cool"
        finally:
            await client.disconnect()


async def test_pinned_v34_against_v33_server_raises() -> None:
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
