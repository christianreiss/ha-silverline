"""High-level async client for a Poolex Silverline / Tuya heat pump (v3.3, v3.4, v3.5)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from . import const
from .exceptions import (
    CannotConnect,
    IncompleteFrame,
    InvalidAuth,
    ProtocolError,
    SilverlineError,
)
from .layouts import DpLayout, LAYOUT_STANDARD
from .models import DeviceState
from .protocol import (
    Frame,
    Frame34Codec,
    Frame35Codec,
    FrameCodec,
    derive_session_key_34,
    derive_session_key_35,
    is_invalid_auth_retcode,
)

_LOGGER = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT: float = 10.0
_HEARTBEAT_INTERVAL: float = 10.0
_RECONNECT_BACKOFF: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0)
_HANDSHAKE_TIMEOUT: float = 5.0  # per-probe timeout for v3.5 negotiation
_READ_CHUNK: int = 4096
# Hard cap on the inbound buffer when no complete frame has decoded yet. A
# legitimate frame is < 64 KiB (see protocol._MAX_FRAME_SIZE); 256 KiB gives
# us comfortable slack but still bounds memory growth from a hostile peer
# that dribbles bytes after claiming an oversize header.
_MAX_READ_BUFFER: int = 256 * 1024

PushListener = Callable[[DeviceState], None]
ConnectionListener = Callable[[bool], None]


def _close_writer_silent(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
    except OSError:
        pass


class SilverlineClient:
    """Async client for one Tuya device (v3.3, v3.4, or v3.5, auto-detected).

    Lifecycle: ``connect()`` opens a persistent socket, runs the v3.4/v3.5
    handshake if applicable, and starts a background reader.
    ``get_status`` / ``set_dp`` / ``set_multiple`` issue commands.
    Spontaneous DP pushes are forwarded to listeners registered via
    ``add_listener``.  ``disconnect()`` shuts everything down.

    Pass ``protocol_version="3.3"``, ``"3.4"``, or ``"3.5"`` to pin the
    version; omit it (or pass ``None``) to auto-probe — v3.5 is tried first,
    then v3.4, then v3.3.
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

        # Reset session-key codecs before each new connection so a stale session
        # key from a previous TCP session is never reused.
        self._codec_34.reset()
        self._codec_35.reset()

        reader, writer = await self._open_tcp()

        pinned = self._protocol_version
        detected = self._detected_version

        try:
            if pinned == "3.3" or detected == "3.3":
                self._codec = self._codec_33
                self._detected_version = "3.3"
            elif pinned == "3.5" or detected == "3.5":
                if not await self._handshake_35(reader, writer):
                    raise CannotConnect(f"v3.5 handshake with {self.host} failed")
                self._codec = self._codec_35
                self._detected_version = "3.5"
            elif pinned == "3.4" or detected == "3.4":
                if not await self._handshake_34(reader, writer):
                    raise CannotConnect(f"v3.4 handshake with {self.host} failed")
                self._codec = self._codec_34
                self._detected_version = "3.4"
            else:
                # Auto-probe: 3.5 → 3.4 → 3.3 (fresh TCP socket per attempt).
                if await self._handshake_35(reader, writer):
                    self._codec = self._codec_35
                    self._detected_version = "3.5"
                else:
                    _close_writer_silent(writer)
                    reader, writer = await self._open_tcp()
                    if await self._handshake_34(reader, writer):
                        self._codec = self._codec_34
                        self._detected_version = "3.4"
                    else:
                        _close_writer_silent(writer)
                        reader, writer = await self._open_tcp()
                        self._codec = self._codec_33
                        self._detected_version = "3.3"
        except InvalidAuth:
            _close_writer_silent(writer)
            raise
        except Exception:
            _close_writer_silent(writer)
            raise

        self._reader = reader
        self._writer = writer
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"silverline-read-{self.host}"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"silverline-hb-{self.host}"
        )
        self._notify_connection(True)

    async def _open_tcp(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self._timeout,
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise CannotConnect(f"connect {self.host}:{self.port}: {err}") from err

    async def _handshake_35(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        """Perform the v3.5 three-message session-key negotiation.

        Returns True on success.  Propagates InvalidAuth (wrong key).
        Returns False on any other failure (wrong protocol version, timeout,
        network error) so the caller can fall back to v3.3.
        """
        local_nonce = os.urandom(16)
        codec = self._codec_35

        # --- Step 1: send SESS_KEY_NEG_START (cmd 0x03) ---
        try:
            wire = codec.encode_raw(const.SESS_KEY_NEG_START, local_nonce)
            writer.write(wire)
            await writer.drain()
        except (OSError, ConnectionError):
            return False

        # --- Step 2: receive SESS_KEY_NEG_RESP (cmd 0x04) ---
        buf = bytearray()
        try:
            frame = await asyncio.wait_for(
                self._recv_35_frame(reader, codec, buf),
                timeout=_HANDSHAKE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return False
        except InvalidAuth:
            raise
        except Exception:
            return False

        if frame.cmd != const.SESS_KEY_NEG_RESP:
            return False

        # Decrypted payload: [retcode(4)] + remote_nonce(16) + HMAC-SHA256(32)
        raw = frame.payload
        if len(raw) >= 52 and raw[0:1] != b"{":
            raw = raw[4:]  # strip retcode
        if len(raw) < 48:
            return False

        remote_nonce = raw[:16]
        expected_hmac = _hmac.new(
            self._codec_35._real_key, local_nonce, hashlib.sha256
        ).digest()
        if not _hmac.compare_digest(expected_hmac, raw[16:48]):
            _LOGGER.debug("v3.5 handshake HMAC mismatch for %s", self.host)
            return False

        # --- Step 3: derive session key, send SESS_KEY_NEG_FINISH (cmd 0x05) ---
        # The FINISH frame itself is still encrypted with the REAL key — the
        # device only switches to the session key for data frames *after* it
        # has decoded FINISH (mirrors TinyTuya's _negotiate_session_key, where
        # self.local_key is reassigned in finalize() only after FINISH is sent).
        # Switching the codec before encoding FINISH would ship it under the
        # session key, which a real device cannot decrypt → handshake fails.
        session_key = derive_session_key_35(
            local_nonce, remote_nonce, self._codec_35._real_key
        )

        finish_hmac = _hmac.new(
            self._codec_35._real_key, remote_nonce, hashlib.sha256
        ).digest()
        try:
            wire = codec.encode_raw(const.SESS_KEY_NEG_FINISH, finish_hmac)
            writer.write(wire)
            await writer.drain()
        except (OSError, ConnectionError):
            return False

        # Only now switch to the session key — it lands before connect() starts
        # the read loop, so there is no race with inbound data frames.
        codec.update_session_key(session_key)
        return True

    async def _handshake_34(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        """Perform the v3.4 three-message session-key negotiation (55AA/ECB).

        Returns True on success. Propagates InvalidAuth (wrong key).
        Returns False on any other failure so the caller can fall back.
        """
        local_nonce = os.urandom(16)
        codec = self._codec_34

        try:
            wire = codec.encode_raw(const.SESS_KEY_NEG_START, local_nonce)
            writer.write(wire)
            await writer.drain()
        except (OSError, ConnectionError):
            return False

        buf = bytearray()
        try:
            frame = await asyncio.wait_for(
                self._recv_34_frame(reader, codec, buf),
                timeout=_HANDSHAKE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return False
        except InvalidAuth:
            raise
        except Exception:
            return False

        if frame.cmd != const.SESS_KEY_NEG_RESP:
            return False

        raw = frame.payload
        if len(raw) >= 52 and raw[0:1] != b"{":
            raw = raw[4:]
        if len(raw) < 48:
            return False

        remote_nonce = raw[:16]
        expected_hmac = _hmac.new(
            self._codec_34._real_key, local_nonce, hashlib.sha256
        ).digest()
        if not _hmac.compare_digest(expected_hmac, raw[16:48]):
            _LOGGER.debug("v3.4 handshake HMAC mismatch for %s", self.host)
            return False

        session_key = derive_session_key_34(
            local_nonce, remote_nonce, self._codec_34._real_key
        )

        finish_hmac = _hmac.new(
            self._codec_34._real_key, remote_nonce, hashlib.sha256
        ).digest()
        try:
            wire = codec.encode_raw(const.SESS_KEY_NEG_FINISH, finish_hmac)
            writer.write(wire)
            await writer.drain()
        except (OSError, ConnectionError):
            return False

        codec.update_session_key(session_key)
        return True

    @staticmethod
    async def _recv_34_frame(
        reader: asyncio.StreamReader,
        codec: Frame34Codec,
        buf: bytearray,
    ) -> Frame:
        """Accumulate bytes until one complete v3.4 frame decodes."""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                raise CannotConnect("connection closed during handshake")
            buf.extend(chunk)
            try:
                frame, _ = codec.decode(bytes(buf))
                return frame
            except IncompleteFrame:
                continue

    @staticmethod
    async def _recv_35_frame(
        reader: asyncio.StreamReader,
        codec: Frame35Codec,
        buf: bytearray,
    ) -> Frame:
        """Accumulate bytes from ``reader`` until one complete v3.5 frame decodes."""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                raise CannotConnect("connection closed during handshake")
            buf.extend(chunk)
            try:
                frame, _ = codec.decode(bytes(buf))
                return frame
            except IncompleteFrame:
                continue

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
        dps = decoded.get("dps", {}) if isinstance(decoded, dict) else {}
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
        body = {
            "devId": self.device_id,
            "gwId": self.device_id,
            "uid": "",
            "t": int(time.time()),
            "dps": dps,
        }
        frame = await self._request(const.CMD_CONTROL, body)
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
            await self.connect()
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
                    drop_connection = True
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
                    except ProtocolError as err:
                        # Bad prefix / suffix / CRC / oversize means we
                        # are desynchronized (or talking to something
                        # hostile). There is no safe recovery from
                        # mid-stream garbage, so drop the connection and
                        # let the reconnect path re-establish a fresh
                        # session.
                        _LOGGER.warning(
                            "dropping connection on malformed frame: %s", err
                        )
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
                # TinyTuya's default v3.4 flow treats sockets as
                # request-scoped. The WBR3 pool firmware mirrors that by
                # closing TCP after a response. Keep entities available and
                # reconnect lazily on the next request instead of surfacing a
                # one-second unavailable blip every poll.
                writer = self._writer
                if writer is not None:
                    _close_writer_silent(writer)
                self._reader = None
                self._writer = None
                return
            self._on_connection_dropped()

    def _take_pending(self, cmd: int, seq: int) -> asyncio.Future[Frame] | None:
        """Pop the request future a response with ``(cmd, seq)`` belongs to.

        v3.3 devices echo our request seqno, so an exact ``(seq, cmd)``
        match is unambiguous.

        v3.4 and v3.5 devices instead answer with their own global,
        monotonically increasing seqno that bears no relation to the
        request's — confirmed on live Poolex v3.4 firmware (WBR3, productKey
        ``wfzeiyn1ed3axxde``) and TinyTuya ``XenonDevice._get_retcode`` for
        v3.5. For those versions we correlate by cmd alone, resolving the
        OLDEST outstanding request of that cmd.

        Limitation (v3.5 only): with no seqno to reject on, a late response to a
        timed-out request can resolve a *later* same-cmd request's future. This
        is benign here — our requests are full-state snapshots (DP_QUERY) or
        idempotent writes (CONTROL), self-correcting on the next poll — and
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
        if frame.cmd in (const.CMD_CONTROL, const.CMD_DP_QUERY, const.CMD_DP_REFRESH):
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
            dps = decoded.get("dps", {}) if isinstance(decoded, dict) else {}
            if not isinstance(dps, dict) or not dps:
                return
            self._state = self._state.merge(dps, layout=self._dp_layout)
            for listener in list(self._listeners):
                try:
                    listener(self._state)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("push listener raised")

    async def _heartbeat_loop(self) -> None:
        # The observed Tuya v3.4 WBR3 pool firmware closes the TCP session
        # shortly after our encrypted HEART_BEAT frame. DP pushes plus the
        # regular 30s poll keep the connection active enough without it.
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
