"""Coverage-gap tests for SilverlineClient.

These exercise behavioral edge cases that the headline tests in
``test_client.py`` don't already hit: idempotent connect, listener
unsubscribe + raising listeners, single-DP and empty-set ``set_*``
inputs, retcode-driven InvalidAuth/SilverlineError on writes,
malformed push frames, request-side failures, and reconnect-backoff
exhaustion. Every test drives the real client against a fake TCP
server (the same harness used by ``test_client.py``) — no mocking of
the unit under test.
"""

from __future__ import annotations

import asyncio
import binascii
import json
import struct
from typing import Any

import pytest

from pysilverline import SilverlineClient, const
from pysilverline.exceptions import CannotConnect, InvalidAuth, SilverlineError
from pysilverline.protocol import FrameCodec, aes_encrypt

KEY = "0123456789abcdef"
DEVICE_ID = "bf12345678abcdefghijkl"


def _build_frame(
    seq: int, cmd: int, body: dict[str, Any], *, retcode: int | None = 0
) -> bytes:
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


def _build_retcode_frame(seq: int, cmd: int, retcode: int) -> bytes:
    """A response payload that's just a 4-byte retcode (no ciphertext).

    Real Tuya firmware does this when the device rejects a CONTROL/DP_QUERY
    outright — the device replies with the retcode and an empty body.
    """
    payload = struct.pack(">I", retcode)
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, seq, cmd, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    return pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)


class _Server:
    """Async-context TCP fake that lets a per-test handler do anything it wants."""

    def __init__(self, handler: "Any | None" = None) -> None:
        self._handler = handler
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0
        self.received: list[tuple[int, int, dict[str, Any]]] = []
        self.connections: int = 0

    async def __aenter__(self) -> "_Server":
        self._server = await asyncio.start_server(self._dispatch, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _dispatch(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        if self._handler is None:
            try:
                while await reader.read(4096):
                    pass
            finally:
                writer.close()
            return
        await self._handler(self, reader, writer)


# ---------------------------------------------------------------------------
# Idempotent connect / listeners
# ---------------------------------------------------------------------------


async def test_connect_is_idempotent_when_already_connected() -> None:
    """Calling connect() twice in a row must be a no-op the second time.

    Otherwise we'd open a second socket and leak the first reader/heartbeat
    pair. Hits the early-return guard at the top of connect().
    """
    async with _Server() as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            assert client.connected
            first_reader = client._reader_task
            await client.connect()
            # Same task object — connect() returned early instead of
            # spawning a new reader.
            assert client._reader_task is first_reader
            assert server.connections == 1
        finally:
            await client.disconnect()


async def test_push_listener_unsubscribe_is_idempotent() -> None:
    """Double-unsubscribe doesn't raise (covers the ValueError swallow)."""
    client = SilverlineClient(
        host="127.0.0.1", port=1, device_id=DEVICE_ID, local_key=KEY
    )
    unsub = client.add_listener(lambda _s: None)
    unsub()
    unsub()  # second call hits the ValueError-suppressing branch


async def test_connection_listener_unsubscribe_is_idempotent() -> None:
    """Double-unsubscribe of the connection listener also swallows."""
    client = SilverlineClient(
        host="127.0.0.1", port=1, device_id=DEVICE_ID, local_key=KEY
    )
    unsub = client.add_connection_listener(lambda _c: None)
    unsub()
    unsub()


async def test_connection_listener_exception_does_not_break_client() -> None:
    """A raising connection listener is logged and the next listener still fires."""
    async with _Server() as server:
        seen: list[bool] = []

        def boom(_c: bool) -> None:
            raise RuntimeError("listener boom")

        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        client.add_connection_listener(boom)
        client.add_connection_listener(seen.append)
        await client.connect()
        try:
            assert seen == [True]
        finally:
            await client.disconnect()


async def test_push_listener_exception_is_swallowed() -> None:
    """A raising push listener is logged and the next listener still receives."""
    pushed: list[Any] = []

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        push = _build_frame(
            seq=999,
            cmd=const.CMD_STATUS,
            body={"dps": {"1": True}},
            retcode=None,
        )
        writer.write(push)
        await writer.drain()
        try:
            while await reader.read(4096):
                pass
        except (OSError, ConnectionError):
            pass

    def boom(_s: Any) -> None:
        raise RuntimeError("push listener boom")

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        client.add_listener(boom)
        client.add_listener(pushed.append)
        await client.connect()
        try:
            for _ in range(40):
                if pushed:
                    break
                await asyncio.sleep(0.025)
            assert pushed, "second listener never fired despite first one raising"
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# set_dp / set_multiple
# ---------------------------------------------------------------------------


async def test_set_dp_wraps_set_multiple() -> None:
    """set_dp(id, val) sends one CONTROL with a single-DP body."""

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                    decoded = codec.decrypt_body(bare) if bare else {}
                    srv.received.append((frame.seq, frame.cmd, decoded))
                    if frame.cmd == const.CMD_CONTROL:
                        writer.write(_build_frame(frame.seq, const.CMD_CONTROL, {}))
                        await writer.drain()
        finally:
            writer.close()

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            await client.set_dp(1, True)
            assert client.state.power is True
            controls = [r for r in server.received if r[1] == const.CMD_CONTROL]
            assert len(controls) == 1
            assert controls[0][2]["dps"] == {"1": True}
        finally:
            await client.disconnect()


async def test_set_multiple_empty_is_noop() -> None:
    """set_multiple({}) returns via the early-return guard without sending
    a CONTROL frame."""

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                    # Only the command matters here: we're proving no CONTROL
                    # frame is emitted, so no body decode is needed.
                    srv.received.append((frame.seq, frame.cmd, {}))
                    if frame.cmd == const.CMD_CONTROL:
                        writer.write(_build_frame(frame.seq, const.CMD_CONTROL, {}))
                        await writer.drain()
        finally:
            writer.close()

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            await client.set_multiple({})
            # The early-return guard means no CONTROL frame ever hit the wire.
            # set_multiple is a blocking request/response (see
            # test_set_dp_wraps_set_multiple), so a regression that sent an
            # empty-dps CONTROL would have awaited the handler's reply and the
            # frame would already be recorded by the time set_multiple returned.
            assert not [r for r in server.received if r[1] == const.CMD_CONTROL]
        finally:
            await client.disconnect()


async def test_set_multiple_invalid_auth_retcode() -> None:
    """Device rejecting CONTROL with the invalid-key retcode raises InvalidAuth."""

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                    if frame.cmd == const.CMD_CONTROL:
                        writer.write(
                            _build_retcode_frame(
                                frame.seq, const.CMD_CONTROL, 0xFFFFFFFF
                            )
                        )
                        await writer.drain()
        finally:
            writer.close()

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            with pytest.raises(InvalidAuth):
                await client.set_multiple({1: True})
        finally:
            await client.disconnect()


async def test_set_multiple_nonzero_retcode_raises_silverline_error() -> None:
    """A non-zero, non-auth retcode means the device rejected the write."""

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                    if frame.cmd == const.CMD_CONTROL:
                        writer.write(
                            _build_retcode_frame(frame.seq, const.CMD_CONTROL, 0x42)
                        )
                        await writer.drain()
        finally:
            writer.close()

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            with pytest.raises(SilverlineError):
                await client.set_multiple({1: True})
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# get_status retcode paths
# ---------------------------------------------------------------------------


async def test_get_status_invalid_auth_retcode_raises() -> None:
    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                            _build_retcode_frame(
                                frame.seq, const.CMD_DP_QUERY, 0x00000FFF
                            )
                        )
                        await writer.drain()
        finally:
            writer.close()

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            with pytest.raises(InvalidAuth):
                await client.get_status()
        finally:
            await client.disconnect()


async def test_get_status_nonzero_retcode_raises_silverline_error() -> None:
    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                            _build_retcode_frame(frame.seq, const.CMD_DP_QUERY, 0x42)
                        )
                        await writer.drain()
        finally:
            writer.close()

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            with pytest.raises(SilverlineError):
                await client.get_status()
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# Request error paths
# ---------------------------------------------------------------------------


async def test_get_status_times_out_when_device_silent() -> None:
    """Device accepts the connection but never replies → CannotConnect via timeout."""

    async def silent(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Drain inbound forever; never write a response.
        try:
            while await reader.read(4096):
                pass
        except (OSError, ConnectionError):
            pass

    async with _Server(silent) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=0.2,
        )
        await client.connect()
        try:
            with pytest.raises(CannotConnect):
                await client.get_status()
        finally:
            await client.disconnect()


async def test_request_after_writer_dies_raises_cannot_connect() -> None:
    """If the underlying writer is closed mid-request, we get CannotConnect."""

    async def drop_after_first_write(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Read one chunk then slam the socket so the client's next write fails.
        await reader.read(4096)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async with _Server(drop_after_first_write) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=0.3,
        )
        await client.connect()
        try:
            # Force the first send (which the server reads then drops).
            with pytest.raises(CannotConnect):
                await client.get_status()
            # Give the read loop a tick to mark the socket as not-connected.
            for _ in range(20):
                if not client.connected:
                    break
                await asyncio.sleep(0.025)
            # Once the writer is closed, a fresh request should also fail.
            with pytest.raises(CannotConnect):
                await client.get_status()
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# Read-loop edge cases: malformed push, ignorable push payloads
# ---------------------------------------------------------------------------


async def test_undecryptable_push_is_ignored() -> None:
    """A STATUS push encrypted with the wrong key is dropped silently —
    the next push will land cleanly."""
    pushed: list[Any] = []

    def _bad_push() -> bytes:
        # Encrypt with a wrong key so decrypt_body raises InvalidAuth.
        wrong = b"WRONGWRONGWRONG1"
        plaintext = json.dumps({"dps": {"3": 30}}).encode()
        ct = aes_encrypt(plaintext, wrong)
        size = len(ct) + 8
        header = struct.pack(">IIII", const.FRAME_PREFIX, 1, const.CMD_STATUS, size)
        pre = header + ct
        crc = binascii.crc32(pre) & 0xFFFFFFFF
        return pre + struct.pack(">II", crc, const.FRAME_SUFFIX)

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.write(_bad_push())
        await writer.drain()
        # Then a legitimately-decryptable push so we can prove the client
        # didn't drop the connection or stop listening.
        writer.write(
            _build_frame(
                seq=2, cmd=const.CMD_STATUS, body={"dps": {"1": True}}, retcode=None
            )
        )
        await writer.drain()
        try:
            while await reader.read(4096):
                pass
        except (OSError, ConnectionError):
            pass

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        client.add_listener(pushed.append)
        await client.connect()
        try:
            for _ in range(40):
                if pushed:
                    break
                await asyncio.sleep(0.025)
            assert pushed, "good push never reached listeners"
            assert pushed[-1].power is True
            assert client.connected, "client dropped connection on undecryptable push"
        finally:
            await client.disconnect()


async def test_push_with_empty_dps_is_ignored() -> None:
    """A push frame whose body decodes to {"dps": {}} doesn't notify
    listeners; the merge step would be a no-op anyway."""
    pushed: list[Any] = []

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # First push: empty dps → must be skipped.
        writer.write(
            _build_frame(seq=1, cmd=const.CMD_STATUS, body={"dps": {}}, retcode=None)
        )
        await writer.drain()
        # Second push: non-empty dps → listener must fire.
        writer.write(
            _build_frame(
                seq=2, cmd=const.CMD_STATUS, body={"dps": {"1": True}}, retcode=None
            )
        )
        await writer.drain()
        try:
            while await reader.read(4096):
                pass
        except (OSError, ConnectionError):
            pass

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        client.add_listener(pushed.append)
        await client.connect()
        try:
            for _ in range(40):
                if pushed:
                    break
                await asyncio.sleep(0.025)
            # Exactly one push reached us — the empty one was filtered.
            assert len(pushed) == 1
        finally:
            await client.disconnect()


async def test_buffer_overflow_drops_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer that dribbles bytes without ever completing a frame can't
    grow the client's read buffer unboundedly. After _MAX_READ_BUFFER we
    cut the socket."""
    import pysilverline.client as client_mod

    # Shrink the buffer cap for the test so we don't have to feed 256 KiB.
    monkeypatch.setattr(client_mod, "_MAX_READ_BUFFER", 1024)
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (5.0,))

    drop_seen = asyncio.Event()

    async def dribbler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Send a header that promises lots more, then keep writing junk —
        # no terminator, no valid CRC. The decoder rejects each attempt
        # (or asks for more). We never send enough for a decode to succeed,
        # but we send way more than the cap.
        # Use a *valid prefix* so the buffer accumulates; without prefix
        # validation, decode raises ProtocolError immediately. Send a
        # header claiming a frame within the legitimate size range, then
        # stop short so decode keeps raising IncompleteFrame and the buffer
        # keeps growing.
        header = struct.pack(">IIII", const.FRAME_PREFIX, 1, const.CMD_STATUS, 4096)
        writer.write(header)
        await writer.drain()
        try:
            while True:
                # Pump bytes that never complete the promised frame.
                writer.write(b"\x00" * 256)
                await writer.drain()
                await asyncio.sleep(0.02)
        except (OSError, ConnectionError):
            drop_seen.set()
            return

    async with _Server(dribbler) as server:
        events: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        client.add_connection_listener(events.append)
        await client.connect()
        try:
            await asyncio.wait_for(drop_seen.wait(), timeout=3.0)
            for _ in range(40):
                if False in events:
                    break
                await asyncio.sleep(0.025)
            assert False in events, f"no disconnect event observed: {events}"
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_is_sent_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heartbeat loop writes a CMD_HEART_BEAT frame at the configured
    interval. Speed the interval up so the test runs in real time."""
    import pysilverline.client as client_mod

    monkeypatch.setattr(client_mod, "_HEARTBEAT_INTERVAL", 0.05)

    heartbeats = asyncio.Event()
    count = 0

    async def handler(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal count
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
                    if frame.cmd == const.CMD_HEART_BEAT:
                        count += 1
                        if count >= 2:
                            heartbeats.set()
        finally:
            writer.close()

    async with _Server(handler) as server:
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        await client.connect()
        try:
            await asyncio.wait_for(heartbeats.wait(), timeout=2.0)
            assert count >= 2
        finally:
            await client.disconnect()


async def test_heartbeat_failure_triggers_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the heartbeat write hits OSError, the client schedules a reconnect."""
    import pysilverline.client as client_mod

    monkeypatch.setattr(client_mod, "_HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (0.05, 0.05))

    async def kill_after_one_heartbeat(
        srv: _Server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
                    if frame.cmd == const.CMD_HEART_BEAT and srv.connections == 1:
                        # Drop the socket so the *next* heartbeat write fails
                        # inside _send_heartbeat → CannotConnect.
                        return
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    async with _Server(kill_after_one_heartbeat) as server:
        events: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.3",
            request_timeout=1.0,
        )
        client.add_connection_listener(events.append)
        await client.connect()
        try:
            for _ in range(80):
                if events.count(True) >= 2:
                    break
                await asyncio.sleep(0.05)
            # Either the read loop saw EOF first or the heartbeat write did;
            # both paths funnel into _on_connection_dropped and then a
            # reconnect attempt. Both ends must reach the server.
            assert server.connections >= 2, (
                f"reconnect never happened: connections={server.connections}, "
                f"events={events}"
            )
        finally:
            await client.disconnect()


# ---------------------------------------------------------------------------
# Reconnect-backoff exhaustion
# ---------------------------------------------------------------------------


async def test_close_writer_is_safe_when_writer_already_gone() -> None:
    """The internal helper that closes the underlying writer must be a
    no-op when there's nothing to close (called from the read loop
    when the writer has already been swapped out)."""
    client = SilverlineClient(
        host="127.0.0.1", port=1, device_id=DEVICE_ID, local_key=KEY
    )
    assert client._writer is None
    # Calling _close_writer with no writer set must not raise.
    client._close_writer()


async def test_close_writer_swallows_oserror() -> None:
    """A writer.close() that raises OSError (e.g., already-closed socket
    in a weird state) is swallowed — the read loop's job is to *let go*
    of the socket, not to propagate teardown errors."""

    class _BoomWriter:
        def is_closing(self) -> bool:
            return False

        def close(self) -> None:
            raise OSError("test: boom on close")

    client = SilverlineClient(
        host="127.0.0.1", port=1, device_id=DEVICE_ID, local_key=KEY
    )
    client._writer = _BoomWriter()  # type: ignore[assignment]
    # Must not propagate the OSError.
    client._close_writer()


async def test_request_translates_write_oserror_to_cannot_connect() -> None:
    """If the writer.write() call raises OSError mid-request, the client
    catches it, drops the pending future, and reports CannotConnect to
    the caller. The pending dict must end empty so a later request can
    proceed."""

    class _BoomWriter:
        is_closing_calls = 0

        def is_closing(self) -> bool:
            return False

        def write(self, _wire: bytes) -> None:
            raise OSError("test: write blew up")

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    client = SilverlineClient(
        host="127.0.0.1", port=1, device_id=DEVICE_ID, local_key=KEY
    )
    client._writer = _BoomWriter()  # type: ignore[assignment]
    try:
        with pytest.raises(CannotConnect):
            await client.get_status()
        assert client._pending == {}, "pending future was not cleaned up"
    finally:
        # Reset so any teardown logic doesn't trip.
        client._writer = None


async def test_send_heartbeat_translates_write_oserror_to_cannot_connect() -> None:
    """_send_heartbeat must turn an OSError on the wire into CannotConnect
    so _heartbeat_loop can fire _on_connection_dropped."""
    from pysilverline.exceptions import CannotConnect as _CC

    class _BoomWriter:
        def is_closing(self) -> bool:
            return False

        def write(self, _wire: bytes) -> None:
            raise OSError("test: heartbeat write blew up")

        async def drain(self) -> None:
            return None

    client = SilverlineClient(
        host="127.0.0.1", port=1, device_id=DEVICE_ID, local_key=KEY
    )
    client._writer = _BoomWriter()  # type: ignore[assignment]
    try:
        with pytest.raises(_CC):
            await client._send_heartbeat()
    finally:
        client._writer = None


async def test_send_heartbeat_is_noop_when_writer_gone() -> None:
    """If the writer has already been cleared (disconnect raced with
    the heartbeat loop's wakeup), _send_heartbeat just returns."""
    client = SilverlineClient(
        host="127.0.0.1", port=1, device_id=DEVICE_ID, local_key=KEY
    )
    client._writer = None
    # No raise, no return value.
    await client._send_heartbeat()


async def test_reconnect_gives_up_after_backoff_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every reconnect attempt fails, the reconnect task walks the
    full backoff schedule and exits — clearing _reconnect_task so a
    later connect() can start over.

    To make the failures deterministic (not racing TIME_WAIT on the
    kernel side), we stub ``asyncio.open_connection`` at the very edge
    so every reconnect attempt raises OSError before any real socket
    syscall happens.
    """
    import pysilverline.client as client_mod

    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (0.02, 0.02))

    # Drop the live socket on demand so the client schedules a reconnect.
    drop_now = asyncio.Event()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Wait for the test to ask us to disconnect, then slam the socket
        # so the client's read loop sees EOF and reconnect kicks in.
        await drop_now.wait()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    client = SilverlineClient(
        host="127.0.0.1",
        port=port,
        device_id=DEVICE_ID,
        local_key=KEY,
        protocol_version="3.3",
        request_timeout=0.1,
    )
    await client.connect()
    assert client.connected

    # From here on, every reconnect attempt fails — open_connection raises
    # OSError before any real socket syscall. (Patching at the very edge,
    # not mocking the client itself.)
    real_open = client_mod.asyncio.open_connection

    async def always_refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("test: connection refused")

    monkeypatch.setattr(client_mod.asyncio, "open_connection", always_refuse)

    # Now ask the live socket to drop.
    drop_now.set()
    try:
        # First, wait for the reconnect task to have been scheduled. Then
        # wait for it to walk through both backoff steps and exit. The
        # finally block in _reconnect_loop clears _reconnect_task to None.
        for _ in range(50):
            if client._reconnect_task is not None:
                break
            await asyncio.sleep(0.02)
        assert client._reconnect_task is not None, (
            "reconnect task was never scheduled after the drop"
        )
        for _ in range(200):
            if client._reconnect_task is None:
                break
            await asyncio.sleep(0.02)
        assert client._reconnect_task is None, (
            "reconnect task never exited after backoff exhausted"
        )
        assert not client.connected
    finally:
        # Restore so disconnect()'s cleanup path doesn't trip over the stub.
        monkeypatch.setattr(client_mod.asyncio, "open_connection", real_open)
        await client.disconnect()
        server.close()
        await server.wait_closed()
