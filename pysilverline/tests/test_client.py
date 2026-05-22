"""Integration tests for SilverlineClient against a fake Tuya v3.3 server."""

from __future__ import annotations

import asyncio
import binascii
import json
import struct
from typing import Any

import pytest

from pysilverline import SilverlineClient, const
from pysilverline.exceptions import CannotConnect, InvalidAuth
from pysilverline.protocol import aes_encrypt

KEY = "0123456789abcdef"
DEVICE_ID = "bf12345678abcdefghijkl"


def _build_frame(seq: int, cmd: int, body: dict[str, Any], *, retcode: int | None = 0) -> bytes:
    plaintext = json.dumps(body).encode()
    ciphertext = aes_encrypt(plaintext, KEY.encode())
    payload = b""
    if retcode is not None:
        payload += struct.pack(">I", retcode)
    payload += ciphertext
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, seq, cmd, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    return pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)


class FakeTuyaServer:
    """A tiny TCP server that decodes incoming v3.3 frames and replies.

    Behavior is configured via callbacks per command code.
    """

    def __init__(self) -> None:
        self.handlers: dict[int, Any] = {}
        self.received: list[tuple[int, int, dict[str, Any]]] = []
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0

    async def __aenter__(self) -> "FakeTuyaServer":
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
        from pysilverline.protocol import FrameCodec  # local import for codec parity

        codec = FrameCodec(KEY)
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                while len(buf) >= 24:
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        return
                    buf = bytearray(remainder)
                    bare = codec.split_request_payload(frame.payload)
                    decrypted = codec.decrypt_body(bare) if bare else {}
                    self.received.append((frame.seq, frame.cmd, decrypted))
                    handler = self.handlers.get(frame.cmd)
                    if handler is None:
                        continue
                    response = handler(frame.seq, decrypted)
                    if response is not None:
                        writer.write(response)
                        await writer.drain()
        finally:
            writer.close()


async def test_get_status_round_trip() -> None:
    async with FakeTuyaServer() as server:
        server.handlers[const.CMD_DP_QUERY] = lambda seq, body: _build_frame(
            seq, const.CMD_DP_QUERY, {"devId": DEVICE_ID, "dps": {"1": True, "4": "Heat", "3": 27}}
        )

        client = SilverlineClient(
            host="127.0.0.1", port=server.port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=2.0,
        )
        await client.connect()
        try:
            state = await client.get_status()
            assert state.power is True
            assert state.mode == "Heat"
            assert state.temp_current == 27
        finally:
            await client.disconnect()


async def test_poll_merges_with_prior_push_state() -> None:
    """Tuya firmware variants exist where certain DPs only ride along
    in spontaneous STATUS pushes and never in DP_QUERY responses. The
    poll path must merge (overlay) the response onto the prior state
    rather than replacing, otherwise those push-only DPs would flicker
    to None on every 30s poll.
    """
    pushed_to: list[Any] = []

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        from pysilverline.protocol import FrameCodec

        # 1) Send a STATUS push the moment the client connects, carrying
        #    a DP the eventual DP_QUERY response will deliberately omit.
        push = _build_frame(
            seq=999,
            cmd=const.CMD_STATUS,
            body={"dps": {"110": 850, "1": True}},
            retcode=None,
        )
        writer.write(push)
        await writer.drain()
        pushed_to.append(True)

        # 2) Then handle the DP_QUERY normally — but the response only
        #    carries DPs 1 + 4 + 3, omitting the fan_speed (DP 110)
        #    that the device announced via push.
        codec = FrameCodec(KEY)
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                while len(buf) >= 24:
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        return
                    buf = bytearray(remainder)
                    if frame.cmd == const.CMD_DP_QUERY:
                        writer.write(
                            _build_frame(
                                frame.seq,
                                const.CMD_DP_QUERY,
                                {"dps": {"1": True, "4": "Heat", "3": 26}},
                            )
                        )
                        await writer.drain()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = SilverlineClient(
            host="127.0.0.1",
            port=port,
            device_id=DEVICE_ID,
            local_key=KEY,
            request_timeout=1.0,
        )
        await client.connect()
        try:
            # Let the push frame land in the client state first.
            for _ in range(40):
                if client.state.fan_speed == 850:
                    break
                await asyncio.sleep(0.025)
            assert (
                client.state.fan_speed == 850
            ), "push frame did not populate state.fan_speed"

            # Now poll. The DP_QUERY response omits DP 110; with merge,
            # the previously pushed fan_speed must survive. Before the
            # merge fix, get_status replaced the whole state and fan_speed
            # would have flipped to None.
            state = await client.get_status()
            assert state.mode == "Heat"
            assert state.fan_speed == 850, (
                "poll wiped push-only DP from state — merge regression"
            )
        finally:
            await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


async def test_set_dp_sends_control_and_merges_state() -> None:
    async with FakeTuyaServer() as server:
        server.handlers[const.CMD_DP_QUERY] = lambda seq, body: _build_frame(
            seq, const.CMD_DP_QUERY, {"dps": {"1": False}}
        )
        server.handlers[const.CMD_CONTROL] = lambda seq, body: _build_frame(
            seq, const.CMD_CONTROL, {}
        )

        client = SilverlineClient(
            host="127.0.0.1", port=server.port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=2.0,
        )
        await client.connect()
        try:
            await client.get_status()
            await client.set_multiple({1: True, 4: "BoostHeat"})
            assert client.state.power is True
            assert client.state.mode == "BoostHeat"
            # Verify the wire: the device received a CONTROL frame with both DPs
            control_frames = [r for r in server.received if r[1] == const.CMD_CONTROL]
            assert len(control_frames) == 1
            _, _, body = control_frames[0]
            assert body["dps"] == {"1": True, "4": "BoostHeat"}
        finally:
            await client.disconnect()


async def test_push_listener_receives_spontaneous_status() -> None:
    pushed: list[Any] = []

    async def push_on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        push = _build_frame(
            seq=999, cmd=const.CMD_STATUS,
            body={"dps": {"3": 31, "1": True}},
            retcode=None,
        )
        writer.write(push)
        await writer.drain()
        try:
            while True:
                if not await reader.read(4096):
                    return
        except (OSError, ConnectionError):
            return

    server_obj = await asyncio.start_server(push_on_connect, "127.0.0.1", 0)
    port = server_obj.sockets[0].getsockname()[1]
    try:
        client = SilverlineClient(
            host="127.0.0.1", port=port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=2.0,
        )
        client.add_listener(lambda s: pushed.append(s))
        await client.connect()
        try:
            for _ in range(40):
                if pushed:
                    break
                await asyncio.sleep(0.025)
            assert pushed, "push listener was not invoked"
            assert pushed[-1].temp_current == 31
            assert pushed[-1].power is True
        finally:
            await client.disconnect()
    finally:
        server_obj.close()
        await server_obj.wait_closed()


async def test_invalid_auth_on_decryption_failure() -> None:
    """When the device replies with ciphertext encrypted under a different
    key, the codec raises InvalidAuth and the caller can trigger reauth."""
    async with FakeTuyaServer() as server:
        wrong_key_server_codec_key = b"WRONGWRONGWRONG1"

        def bad_response(seq: int, body: dict[str, Any]) -> bytes:
            plaintext = json.dumps({"dps": {"1": True}}).encode()
            ciphertext = aes_encrypt(plaintext, wrong_key_server_codec_key)
            payload = struct.pack(">I", 0) + ciphertext
            size = len(payload) + 8
            header = struct.pack(">IIII", const.FRAME_PREFIX, seq, const.CMD_DP_QUERY, size)
            pre = header + payload
            crc = binascii.crc32(pre) & 0xFFFFFFFF
            return pre + struct.pack(">II", crc, const.FRAME_SUFFIX)

        server.handlers[const.CMD_DP_QUERY] = bad_response

        client = SilverlineClient(
            host="127.0.0.1", port=server.port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=2.0,
        )
        await client.connect()
        try:
            with pytest.raises(InvalidAuth):
                await client.get_status()
        finally:
            await client.disconnect()


async def test_connect_failure_raises_cannot_connect() -> None:
    client = SilverlineClient(
        host="127.0.0.1", port=1,  # nothing listens on port 1
        device_id=DEVICE_ID, local_key=KEY,
        request_timeout=0.5,
    )
    with pytest.raises(CannotConnect):
        await client.connect()


async def test_request_before_connect_raises() -> None:
    client = SilverlineClient(
        host="127.0.0.1", port=1,
        device_id=DEVICE_ID, local_key=KEY,
    )
    with pytest.raises(CannotConnect):
        await client.get_status()


async def test_connection_listener_receives_connect_event() -> None:
    """A successful connect() fires the connection listener with True."""
    async with FakeTuyaServer() as server:
        events: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1", port=server.port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=1.0,
        )
        client.add_connection_listener(events.append)
        await client.connect()
        try:
            assert events == [True]
        finally:
            await client.disconnect()


async def test_connection_listener_unsubscribe() -> None:
    """The unsubscribe callable removes the listener."""
    async with FakeTuyaServer() as server:
        events: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1", port=server.port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=1.0,
        )
        unsub = client.add_connection_listener(events.append)
        unsub()
        await client.connect()
        try:
            assert events == []
        finally:
            await client.disconnect()


async def test_get_status_survives_tcp_fragmented_response() -> None:
    """TCP is allowed to split any frame across read boundaries — the
    client must wait for the rest of the bytes instead of treating a
    partial buffer as a malformed frame and dropping the connection.

    Regression test for the case where FrameCodec.decode used to raise
    ProtocolError("frame truncated") on an incomplete buffer, which the
    read loop then handled identically to a real spec violation.
    """

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        from pysilverline.protocol import FrameCodec

        codec = FrameCodec(KEY)
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                while len(buf) >= 24:
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        return
                    buf = bytearray(remainder)
                    if frame.cmd == const.CMD_DP_QUERY:
                        response = _build_frame(
                            frame.seq,
                            const.CMD_DP_QUERY,
                            {"dps": {"1": True, "4": "Heat", "3": 26}},
                        )
                        # Split the response across two writes with a
                        # delay between them — simulates a real TCP
                        # MSS-sized fragment landing in our client's
                        # buffer before the rest of the frame arrives.
                        split = len(response) // 2
                        writer.write(response[:split])
                        await writer.drain()
                        await asyncio.sleep(0.05)
                        writer.write(response[split:])
                        await writer.drain()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = SilverlineClient(
            host="127.0.0.1",
            port=port,
            device_id=DEVICE_ID,
            local_key=KEY,
            request_timeout=2.0,
        )
        await client.connect()
        try:
            state = await client.get_status()
            assert state.mode == "Heat"
            assert state.temp_current == 26
            assert state.power is True
            # The connection must still be up — a malformed-frame
            # mishandling would have closed the socket out from under us.
            assert client.connected
        finally:
            await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


async def test_reconnect_on_peer_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing the socket from the server side triggers a reconnect.

    Listener sees False then True; the second connect produces a fresh
    DP_QUERY result the caller can read.
    """
    import pysilverline.client as client_mod
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (0.05, 0.05, 0.05))

    connection_count = 0
    close_first = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        codec_key = KEY
        from pysilverline.protocol import FrameCodec
        codec = FrameCodec(codec_key)
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                while len(buf) >= 24:
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        return
                    buf = bytearray(remainder)
                    if frame.cmd == const.CMD_DP_QUERY:
                        writer.write(_build_frame(
                            frame.seq, const.CMD_DP_QUERY,
                            {"dps": {"1": True, "4": "Heat", "3": 26}},
                        ))
                        await writer.drain()
                        if connection_count == 1:
                            close_first.set()
                            return  # force peer-close after answering once
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        events: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1", port=port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=1.0,
        )
        client.add_connection_listener(events.append)
        await client.connect()
        await client.get_status()  # consumes the first response
        # Wait for the server to close our socket.
        await asyncio.wait_for(close_first.wait(), timeout=1.0)
        # Wait for the reconnect listener to fire True a second time.
        for _ in range(80):
            if events.count(True) >= 2 and False in events:
                break
            await asyncio.sleep(0.05)
        try:
            assert False in events, f"never saw disconnect event; events={events}"
            assert events.count(True) >= 2, f"never reconnected; events={events}"
            assert connection_count >= 2
            # The reconnected client can serve a fresh DP_QUERY.
            state = await client.get_status()
            assert state.mode == "Heat"
        finally:
            await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


async def test_oversize_frame_header_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostile peer that claims a multi-GiB frame size must not cause
    the client to hang waiting for the bytes; it should detect the
    oversize header via FrameCodec.decode and drop the socket within
    a short timeout."""
    import pysilverline.client as client_mod
    # Long-ish backoff so a reconnect attempt doesn't race the test.
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (5.0,))

    peer_closed = asyncio.Event()

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Header claims a ~4 GiB frame; we follow it with 8 junk bytes
        # so the client's read buffer crosses the 24-byte threshold and
        # FrameCodec.decode actually runs (and rejects the size).
        header = struct.pack(
            ">IIII", const.FRAME_PREFIX, 1, const.CMD_STATUS, 0xFFFFFFFF
        )
        writer.write(header + b"\x00" * 8)
        try:
            await writer.drain()
        except (OSError, ConnectionError):
            pass
        # Wait for the client to close on us (EOF on our reader). If the
        # client hung instead, this never fires and the test times out.
        try:
            while True:
                got = await reader.read(4096)
                if not got:
                    peer_closed.set()
                    return
        except (OSError, ConnectionError):
            peer_closed.set()
            return

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        events: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1", port=port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=1.0,
        )
        client.add_connection_listener(events.append)
        await client.connect()
        try:
            await asyncio.wait_for(peer_closed.wait(), timeout=2.0)
            # The read loop's finally clause fires _on_connection_dropped
            # which notifies listeners with False.
            for _ in range(40):
                if False in events:
                    break
                await asyncio.sleep(0.025)
            assert False in events, f"never saw disconnect; events={events}"
        finally:
            await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


async def test_back_to_back_drops_keep_triggering_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that drops every connection must keep re-triggering reconnects.

    Regression test for the race where the new socket dies *before*
    ``_reconnect_loop`` returns: at that instant the reconnect task is
    still the current task, so ``_on_connection_dropped`` suppresses the
    drop signal. Without resetting ``self._reconnect_task`` to ``None``
    on exit, no further reconnect is ever scheduled. With the fix in
    place, three connections happen well inside one second.
    """
    import pysilverline.client as client_mod
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (0.02, 0.02, 0.02))

    connection_count = 0

    async def drop_immediately(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal connection_count
        connection_count += 1
        # Close the writer before reading anything; the client will see EOF.
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    server = await asyncio.start_server(drop_immediately, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = SilverlineClient(
            host="127.0.0.1", port=port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=0.2,
        )
        await client.connect()
        try:
            for _ in range(50):
                if connection_count >= 3:
                    break
                await asyncio.sleep(0.02)
            assert connection_count >= 3, (
                f"reconnect chain stalled: connection_count={connection_count}"
            )
        finally:
            await client.disconnect()
    finally:
        server.close()
        await server.wait_closed()


async def test_disconnect_cancels_reconnect_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit disconnect() stops the reconnect loop mid-backoff."""
    import pysilverline.client as client_mod
    # Long backoffs so we definitely catch the task in flight.
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (5.0, 5.0, 5.0))

    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        # Close immediately to trigger reconnect.
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = SilverlineClient(
            host="127.0.0.1", port=port,
            device_id=DEVICE_ID, local_key=KEY,
            request_timeout=1.0,
        )
        await client.connect()
        # Wait for the peer-close to be observed and reconnect to be scheduled.
        for _ in range(40):
            if client._reconnect_task is not None and not client._reconnect_task.done():
                break
            await asyncio.sleep(0.025)
        assert client._reconnect_task is not None
        assert not client._reconnect_task.done()
        await client.disconnect()
        # disconnect() awaits the reconnect task, so it must be done now.
        assert client._reconnect_task is None
    finally:
        server.close()
        await server.wait_closed()
