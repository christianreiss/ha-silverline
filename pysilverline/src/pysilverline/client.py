"""High-level async client for a Poolex Silverline / Tuya heat pump (v3.3 and v3.5)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from . import const, session, transport
from .exceptions import (
    CannotConnect,
    IncompleteFrame,
    InvalidAuth,
    ProtocolError,
    SilverlineError,
)
from .layouts import LAYOUT_STANDARD, DpLayout
from .models import DeviceState
from .protocol import (
    Frame,
    Frame34Codec,
    Frame35Codec,
    FrameCodec,
    is_invalid_auth_retcode,
)
from .transport import close_writer_silent

_LOGGER = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT: float = 10.0
_HEARTBEAT_INTERVAL: float = 10.0
_RECONNECT_BACKOFF: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0)
_READ_CHUNK: int = 4096
# Hard cap on the inbound buffer when no complete frame has decoded yet. A
# legitimate frame is < 64 KiB (see protocol._MAX_FRAME_SIZE); 256 KiB gives
# us comfortable slack but still bounds memory growth from a hostile peer
# that dribbles bytes after claiming an oversize header.
_MAX_READ_BUFFER: int = 256 * 1024

PushListener = Callable[[DeviceState], None]
ConnectionListener = Callable[[bool], None]


def _unwrap_dps(decoded: Any) -> dict[str, Any]:
    """Extract the ``dps`` mapping from a decoded device body.

    v3.4 ``device22`` firmware wraps DPs as ``{"data": {"dps": {...}}}`` while
    v3.3 / v3.5 (and most v3.4 query responses) put them at the top level.
    Accept either shape.
    """
    if not isinstance(decoded, dict):
        return {}
    data = decoded.get("data")
    if isinstance(data, dict):
        inner = data.get("dps")
        if isinstance(inner, dict):
            return inner
    dps = decoded.get("dps", {})
    return dps if isinstance(dps, dict) else {}


class SilverlineClient:
    """Async client for one Tuya device (v3.3, v3.4 or v3.5, auto-detected).

    Lifecycle: ``connect()`` opens a persistent socket, runs the v3.4/v3.5
    session-key handshake if applicable, and starts a background reader.
    ``get_status`` / ``set_dp`` / ``set_multiple`` issue commands.
    Spontaneous DP pushes are forwarded to listeners registered via
    ``add_listener``.  ``disconnect()`` shuts everything down.

    Pass ``protocol_version="3.3"``, ``"3.4"`` or ``"3.5"`` to pin the version;
    omit it (or pass ``None``) to auto-probe — v3.5 is tried first, then v3.4,
    then plain v3.3. The detected version sticks across reconnects so the probe
    runs at most once.
    """

    def __init__(
        self,
        host: str,
        device_id: str,
        local_key: str,
        *,
        port: int = const.DEFAULT_PORT,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
        protocol_version: str | None = None,
        dp_layout: DpLayout | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self._timeout = request_timeout
        self._protocol_version = protocol_version  # None = auto-probe
        # Maps semantic DeviceState fields onto wire DP numbers; firmwares with
        # non-standard numbering (e.g. the v3.4 wfzeiyn pool firmware) pass a
        # custom layout. Defaults to the legacy numbering.
        self._dp_layout = dp_layout or LAYOUT_STANDARD

        self._codec_33 = FrameCodec(local_key)
        self._codec_34 = Frame34Codec(local_key)
        self._codec_35 = Frame35Codec(local_key)
        # Active codec — set during connect() after version detection.
        self._codec: FrameCodec | Frame34Codec | Frame35Codec = self._codec_33
        # Persists across reconnects once detected; starts as the pinned version.
        self._detected_version: str | None = protocol_version

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()

        # seq -> (request cmd, future). The cmd is kept so v3.5 responses,
        # which do NOT echo our seqno, can be correlated by cmd (see
        # ``_take_pending``).
        self._pending: dict[int, tuple[int, asyncio.Future[Frame]]] = {}
        self._listeners: list[PushListener] = []
        self._connection_listeners: list[ConnectionListener] = []
        self._state = DeviceState()
        self._closing = False
        self._connection_lost_handled = False

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def detected_version(self) -> str | None:
        """Protocol version detected on the last successful connect, or None."""
        return self._detected_version

    async def connect(self) -> None:
        """Open the TCP connection, negotiate protocol version, start reader."""
        if self.connected:
            return
        self._closing = False
        self._connection_lost_handled = False

        # Reset the session-key codecs to the real key before each new
        # connection so a stale session key from a previous TCP session is
        # never reused.
        self._codec_34.reset()
        self._codec_35.reset()

        reader, writer = await transport.open_tcp(self.host, self.port, self._timeout)
        reader, writer, self._codec, self._detected_version = await session.negotiate(
            reader=reader,
            writer=writer,
            host=self.host,
            pinned=self._protocol_version,
            known=self._detected_version,
            codec_33=self._codec_33,
            codec_34=self._codec_34,
            codec_35=self._codec_35,
            open_tcp=lambda: transport.open_tcp(self.host, self.port, self._timeout),
        )

        self._reader = reader
        self._writer = writer
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"silverline-read-{self.host}"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"silverline-hb-{self.host}"
        )
        self._notify_connection(True)

    async def disconnect(self) -> None:
        """Close the connection and stop background tasks.

        Cancels any in-flight reconnect task too — once ``disconnect`` is
        called, the client stays down until the caller invokes ``connect``
        again explicitly.
        """
        self._closing = True
        # Cancel all three background tasks together, then await them via
        # gather(return_exceptions=True) so that:
        #   * The CancelledError each task raises in response to our own
        #     cancel() is captured as a returned value, not re-raised.
        #   * If disconnect() itself is being cancelled by the caller, the
        #     outer CancelledError still propagates out of `await gather`
        #     — the previous `except (CancelledError, Exception)` swallowed
        #     it, making this coroutine effectively non-cancellable.
        tasks = [
            t
            for t in (
                self._heartbeat_task,
                self._reader_task,
                self._reconnect_task,
            )
            if t and not t.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._heartbeat_task = None
        self._reader_task = None
        self._reconnect_task = None

        for _cmd, fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CannotConnect("client disconnecting"))
        self._pending.clear()

        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None

    def add_listener(self, callback: PushListener) -> Callable[[], None]:
        """Register a synchronous callback for push DP updates.

        Returns an unsubscribe function.
        """
        self._listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def add_connection_listener(
        self, callback: ConnectionListener
    ) -> Callable[[], None]:
        """Register a synchronous callback for connection state changes.

        Invoked with ``True`` after a (re)connection succeeds and ``False``
        when the socket drops unexpectedly. Returns an unsubscribe function.
        """
        self._connection_listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._connection_listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def _notify_connection(self, connected: bool) -> None:
        for listener in list(self._connection_listeners):
            try:
                listener(connected)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("connection listener raised")

    async def get_status(self) -> DeviceState:
        """Issue a DP_QUERY and return the resulting DeviceState."""
        body = {
            "gwId": self.device_id,
            "devId": self.device_id,
            "uid": "",
            "t": int(time.time()),
        }
        frame = await self._request(const.CMD_DP_QUERY, body)
        retcode, ciphertext = self._codec.split_response_payload(
            frame.cmd, frame.payload
        )
        if is_invalid_auth_retcode(retcode):
            raise InvalidAuth(f"DP_QUERY rejected retcode={retcode}")
        # Mirror set_multiple: any other non-zero retcode is a device-side
        # failure we shouldn't paper over by decrypting an empty body.
        if retcode not in (None, 0):
            raise SilverlineError(f"DP_QUERY failed retcode=0x{retcode:08x}")
        decoded = self._codec.decrypt_body(ciphertext)
        dps = _unwrap_dps(decoded)
        if not isinstance(dps, dict):
            raise ProtocolError(f"unexpected dps payload: {decoded!r}")
        # Merge rather than replace: some Tuya firmware variants only
        # ship certain DPs in spontaneous STATUS pushes, not in
        # DP_QUERY responses. If we replaced wholesale, those push-only
        # DPs would flicker to None on every 30s poll. The push path
        # already merges (_dispatch in this module); the poll path
        # has to behave symmetrically.
        self._state = self._state.merge(dps, layout=self._dp_layout)
        return self._state

    async def set_dp(self, dp_id: int, value: bool | int | str) -> None:
        """Convenience wrapper around set_multiple for a single DP."""
        await self.set_multiple({dp_id: value})

    async def set_multiple(self, values: dict[int, bool | int | str]) -> None:
        """Send one CONTROL command updating multiple DPs atomically."""
        if not values:
            return
        dps = {str(k): v for k, v in values.items()}
        if self._detected_version in ("3.4", "3.5"):
            # v3.4/v3.5 firmware accepts writes via CONTROL_NEW (0x0d) wrapped in
            # a protocol-5 envelope; the local LAN API still carries raw DP ids
            # inside ``data.dps``. A plain CONTROL (0x07) — the v3.3 opcode — is
            # silently ignored by these stacks, so the write never gets a
            # response and times out ("waiting for cmd 0x07"). v3.4 confirmed on
            # real wfzeiyn hardware (@olomouckyorel); v3.5 mirrors TinyTuya
            # (CONTROL→CONTROL_NEW for version >= 3.4), pending confirmation on a
            # real v3.5 pump (forum report, Ha Zemos82, Silverline Full Inverter
            # 70).
            body: dict[str, Any] = {
                "protocol": 5,
                "t": int(time.time()),
                "data": {"dps": dps},
            }
            cmd = const.CMD_CONTROL_NEW
        else:
            body = {
                "devId": self.device_id,
                "gwId": self.device_id,
                "uid": "",
                "t": int(time.time()),
                "dps": dps,
            }
            cmd = const.CMD_CONTROL
        frame = await self._request(cmd, body)
        retcode, _ = self._codec.split_response_payload(frame.cmd, frame.payload)
        if is_invalid_auth_retcode(retcode):
            raise InvalidAuth(f"device rejected CONTROL retcode={retcode}")
        if retcode not in (None, 0):
            raise SilverlineError(f"CONTROL failed retcode=0x{retcode:08x}")
        # The device usually echoes the new state via a push frame within
        # ~200ms; merge optimistically so callers see the updated DPs even if
        # they query before the push arrives.
        self._state = self._state.merge(dps, layout=self._dp_layout)

    async def _request(self, cmd: int, body: dict[str, Any]) -> Frame:
        if not self.connected:
            if self._detected_version == "3.4":
                # v3.4 sockets are request-scoped: the device closes TCP after
                # each response (see _read_loop), so reconnect lazily here on the
                # next request rather than running a heartbeat/backoff loop.
                await self.connect()
            else:
                raise CannotConnect("not connected")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Frame] = loop.create_future()

        async with self._send_lock:
            wire = self._codec.encode(cmd, body)
            frame_seq = self._codec.extract_seq_from_wire(wire)
            self._pending[frame_seq] = (cmd, future)
            try:
                writer = self._writer
                if writer is None:
                    raise CannotConnect("not connected")
                writer.write(wire)
                await writer.drain()
            except (OSError, ConnectionError) as err:
                self._pending.pop(frame_seq, None)
                raise CannotConnect(f"send: {err}") from err

        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError as err:
            self._pending.pop(frame_seq, None)
            raise CannotConnect(f"timeout waiting for cmd 0x{cmd:02x}") from err

    def _close_writer(self) -> None:
        """Close the underlying writer, swallowing OS errors.

        Used from the read loop when we decide to bail out (oversize
        buffer, malformed frame); the disconnect path in the ``finally``
        block of ``_read_loop`` then notifies listeners and schedules a
        reconnect.
        """
        writer = self._writer
        if writer is None:
            return
        try:
            writer.close()
        except OSError:
            pass

    async def _read_loop(self) -> None:
        buf = bytearray()
        reader = self._reader
        if reader is None:
            return
        peer_closed = False
        drop_connection = False
        try:
            while not self._closing:
                try:
                    chunk = await reader.read(_READ_CHUNK)
                except (OSError, ConnectionError) as err:
                    _LOGGER.debug("read error: %s", err)
                    break
                if not chunk:
                    _LOGGER.debug("connection closed by peer")
                    peer_closed = True
                    break
                buf.extend(chunk)
                if len(buf) > _MAX_READ_BUFFER:
                    _LOGGER.warning(
                        "read buffer exceeded %d bytes without a complete frame; "
                        "closing connection",
                        _MAX_READ_BUFFER,
                    )
                    self._close_writer()
                    break
                while len(buf) >= 18:
                    try:
                        frame, remainder = self._codec.decode(bytes(buf))
                    except IncompleteFrame:
                        # Normal case under TCP fragmentation: the wire
                        # delivered the header but not yet the full body,
                        # or vice versa. Stop draining and wait for the
                        # next read to fill the gap.
                        break
                    except (ProtocolError, InvalidAuth) as err:
                        # Bad prefix / suffix / CRC / oversize (ProtocolError)
                        # means we are desynchronized. A keyed-MAC / AEAD
                        # failure (InvalidAuth) on the v3.4/v3.5 codecs *after*
                        # a successful handshake is wire corruption, not a wrong
                        # key — the session key already proved itself — so it is
                        # the same desync, not a reauth trigger. Either way there
                        # is no safe recovery from mid-stream garbage: drop and
                        # let the reconnect path re-establish a fresh session.
                        _LOGGER.warning("dropping connection on bad frame: %s", err)
                        buf.clear()
                        drop_connection = True
                        break
                    buf = bytearray(remainder)
                    self._dispatch(frame)
                if drop_connection:
                    self._close_writer()
                    break
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("read loop crashed")
        finally:
            for _cmd, fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CannotConnect("connection lost"))
            self._pending.clear()
            if (
                self._detected_version == "3.4"
                and peer_closed
                and not drop_connection
                and not self._closing
            ):
                # The v3.4 WBR3 pool firmware closes TCP after each response
                # (request-scoped sockets, like TinyTuya's default v3.4 flow).
                # Treat a clean peer-close as idle: tear the socket down quietly
                # and let the next request reconnect lazily (see _request),
                # instead of flapping the connection-lost listener and reconnect
                # backoff — which would otherwise blip "unavailable" every poll.
                writer = self._writer
                if writer is not None:
                    close_writer_silent(writer)
                self._reader = None
                self._writer = None
            else:
                self._on_connection_dropped()

    def _take_pending(self, cmd: int, seq: int) -> asyncio.Future[Frame] | None:
        """Pop the request future a response with ``(cmd, seq)`` belongs to.

        v3.3/v3.4 devices echo our request seqno, so an exact ``(seq, cmd)``
        match is unambiguous — it is always tried first, for every version.

        v3.5 devices instead answer with their own global, monotonically
        increasing seqno that bears no relation to the request's — confirmed
        against TinyTuya ``XenonDevice._get_retcode``, which gates its
        ``sent.seqno != msg.seqno`` check behind ``version < 3.5`` with the
        comment "v3.5 devices respond with a global incrementing seqno, not
        the sent seqno". For v3.5 we therefore correlate by cmd alone,
        resolving the OLDEST outstanding request of that cmd: a single TCP
        connection serialises requests, so the device answers in send order.
        Without this every v3.5 data request waits for a seqno echo that never
        arrives and times out — the integration connects, then is unusable.

        The same cmd-only fallback is enabled for v3.4 as insurance: the
        ``version < 3.5`` gate says v3.4 echoes the seqno (so the exact match
        above already wins on real silicon, and the fallback never fires), but
        we have no v3.4 hardware to confirm it. If some v3.4 firmware turned out
        not to echo, this keeps it usable instead of timing out on every poll.

        Limitation (fallback path only): with no seqno to reject on, a late
        response to a timed-out request can resolve a *later* same-cmd request's
        future. Benign here — our requests are full-state snapshots (DP_QUERY)
        or idempotent writes (CONTROL), self-correcting on the next poll — and
        tinytuya is looser still (no correlation at all).
        """
        entry = self._pending.get(seq)
        if entry is not None and entry[0] == cmd:
            del self._pending[seq]
            return entry[1]
        if self._detected_version in ("3.4", "3.5"):
            # dict preserves insertion order → first match is the oldest request
            match_seq = next(
                (s for s, (c, _f) in self._pending.items() if c == cmd), None
            )
            if match_seq is not None:
                return self._pending.pop(match_seq)[1]
        return None

    def _dispatch(self, frame: Frame) -> None:
        # Correlate a response to the request awaiting it. Push frames
        # (CMD_STATUS) carry their own seqs from the device and must never be
        # delivered to a request future; the cmd gate in front of the match
        # guarantees a push payload is never handed to a request that can't
        # decode it.
        if frame.cmd in (
            const.CMD_CONTROL,
            const.CMD_CONTROL_NEW,
            const.CMD_DP_QUERY,
            const.CMD_DP_REFRESH,
        ):
            fut = self._take_pending(frame.cmd, frame.seq)
            if fut is not None:
                if not fut.done():
                    fut.set_result(frame)
                return

        if frame.cmd in (const.CMD_STATUS, const.CMD_DP_REFRESH):
            ciphertext = self._codec.split_request_payload(frame.payload)
            try:
                decoded = self._codec.decrypt_body(ciphertext)
            except (InvalidAuth, ProtocolError):
                # InvalidAuth = wrong key (next poll will trigger reauth).
                # ProtocolError = AES decrypted but JSON parse failed —
                # transient corruption; ignore the push, the next one
                # will land cleanly.
                _LOGGER.debug("ignoring undecryptable push frame")
                return
            dps = _unwrap_dps(decoded)
            # v3.4 firmware often acks a CONTROL_NEW write by echoing state via
            # a STATUS push (sometimes several partial frames, one DP at a time)
            # rather than a dedicated ACK frame. Resolve any outstanding write
            # here, before the empty-dps early return below. Enabled for v3.5
            # too as insurance (unconfirmed on real v3.5 silicon): if a v3.5 pump
            # acks the same way, the dedicated-frame path in _dispatch never
            # fires and the write would otherwise time out; if it sends a real
            # 0x0d ACK instead, that path wins and this never matches.
            if self._detected_version in ("3.4", "3.5"):
                fut = self._take_pending(const.CMD_CONTROL_NEW, frame.seq)
                if fut is not None and not fut.done():
                    fut.set_result(
                        Frame(
                            seq=frame.seq,
                            cmd=const.CMD_CONTROL_NEW,
                            payload=b"\x00\x00\x00\x00",
                        )
                    )
            if not dps:
                return
            self._state = self._state.merge(dps, layout=self._dp_layout)
            for listener in list(self._listeners):
                try:
                    listener(self._state)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("push listener raised")

    async def _heartbeat_loop(self) -> None:
        # The observed v3.4 WBR3 pool firmware closes the TCP session shortly
        # after an encrypted HEART_BEAT. v3.4 sockets are request-scoped (we
        # reconnect lazily per poll, see _request/_read_loop), so a heartbeat
        # would only churn the connection — skip it entirely.
        if self._detected_version == "3.4":
            return
        try:
            while not self._closing and self.connected:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                if self._closing or not self.connected:
                    return
                try:
                    await self._send_heartbeat()
                except CannotConnect as err:
                    _LOGGER.debug("heartbeat failed: %s", err)
                    self._on_connection_dropped()
                    return
        except asyncio.CancelledError:
            raise

    async def _send_heartbeat(self) -> None:
        async with self._send_lock:
            writer = self._writer
            if writer is None:
                return
            wire = self._codec.encode(const.CMD_HEART_BEAT, {})
            try:
                writer.write(wire)
                await writer.drain()
            except (OSError, ConnectionError) as err:
                raise CannotConnect(f"heartbeat write: {err}") from err

    def _on_connection_dropped(self) -> None:
        """Called from inside the read/heartbeat tasks when the socket dies.

        Idempotent: a single drop triggers exactly one ``False`` listener
        callback and one reconnect task even though both background loops
        will eventually call this on their way out.
        """
        if self._closing or self._connection_lost_handled:
            return
        self._connection_lost_handled = True
        _LOGGER.warning("connection to %s lost; scheduling reconnect", self.host)
        self._notify_connection(False)
        # Schedule the reconnect from a fresh task so we don't block whichever
        # background loop just fell over.
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(),
                name=f"silverline-reconnect-{self.host}",
            )

    async def _reconnect_loop(self) -> None:
        """Walk the backoff schedule trying to reopen the socket.

        The body runs inside a ``try/finally`` that clears
        ``self._reconnect_task`` on exit. Without that, a peer that drops
        the freshly reconnected socket *before this coroutine returns*
        would have its ``_on_connection_dropped`` signal suppressed —
        that callback bails when ``self._reconnect_task`` is still
        running, leaving the client dead with no scheduled retry.
        """
        try:
            # Close the dead writer so the next connect() succeeds cleanly.
            if self._writer is not None:
                try:
                    self._writer.close()
                    await self._writer.wait_closed()
                except OSError:
                    pass
            self._reader = None
            self._writer = None
            # Reap the dead reader/heartbeat tasks before kicking new ones.
            for task_attr in ("_reader_task", "_heartbeat_task"):
                task: asyncio.Task[None] | None = getattr(self, task_attr)
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                setattr(self, task_attr, None)

            for delay in _RECONNECT_BACKOFF:
                if self._closing:
                    return
                await asyncio.sleep(delay)
                if self._closing:
                    return
                try:
                    await self.connect()
                except CannotConnect as err:
                    _LOGGER.debug("reconnect attempt failed: %s", err)
                    continue
                # connect() notifies True; refresh state so listeners see
                # fresh DPs. If the brand-new socket already died (the peer
                # closed it mid-reconnect, and our own reader fired
                # _on_connection_dropped while we were still the current
                # reconnect task — so the schedule check below was a no-op),
                # roll over to the next backoff iteration instead of
                # returning to a dead connection.
                try:
                    await self.get_status()
                except SilverlineError as err:
                    # SilverlineError covers CannotConnect / InvalidAuth /
                    # ProtocolError / bare device-side retcode failures.
                    # Any of them can land here transiently; we want the
                    # reconnect task to keep working through the backoff
                    # rather than die with an unhandled exception on a
                    # socket that's technically up.
                    _LOGGER.debug("post-reconnect refresh failed: %s", err)
                if not self.connected:
                    continue
                return
            _LOGGER.error(
                "exhausted reconnect backoff to %s; giving up until next connect()",
                self.host,
            )
        finally:
            # Clearing this here is what makes back-to-back drops keep
            # triggering reconnects: any drop signal that arrives after
            # this point sees no active reconnect task and schedules a
            # fresh one via _on_connection_dropped.
            self._reconnect_task = None
