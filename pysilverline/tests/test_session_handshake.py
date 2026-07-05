"""Unit tests for the session-key handshake failure branches.

The fake-server suites (``test_client_34`` / ``test_client_35``) prove the
happy paths end to end; these drive ``handshake_34`` / ``handshake_35``
directly with an in-memory reader/writer to reach the failure branches a
well-behaved fake server never produces: dead sockets mid-handshake, wrong
response commands, truncated or forged RESP payloads, and response timeouts.
Every one of these must return False (or raise InvalidAuth where the key is
provably wrong) so ``negotiate`` can fall back or fail loudly.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Callable

import pytest

from pysilverline import const, session
from pysilverline.exceptions import InvalidAuth
from pysilverline.protocol import Frame34Codec, Frame35Codec, aes_decrypt

KEY = "0123456789abcdef"
KEY_B = KEY.encode()
HOST = "127.0.0.1"
REMOTE_NONCE = bytes(range(16, 32))


class _Writer:
    """StreamWriter stand-in: records writes, optionally fails one drain,
    and lets a device-side script feed the reader in response to a write."""

    def __init__(
        self,
        reader: asyncio.StreamReader | None = None,
        *,
        fail_on_drain: int = 0,
        respond: Callable[[bytes], bytes | None] | None = None,
    ) -> None:
        self._reader = reader
        self._fail_on_drain = fail_on_drain
        self._respond = respond
        self._drains = 0
        self.wires: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.wires.append(data)

    async def drain(self) -> None:
        self._drains += 1
        if self._drains == self._fail_on_drain:
            raise OSError("broken pipe")
        if self._respond is not None and self._reader is not None:
            wire = self._respond(self.wires[-1])
            if wire:
                self._reader.feed_data(wire)


# ---------------------------------------------------------------------------
# v3.5 (6699 / GCM)
# ---------------------------------------------------------------------------


def _resp_35(payload: bytes, cmd: int = const.SESS_KEY_NEG_RESP) -> bytes:
    """Encode one device-side v3.5 frame under the real key."""
    return Frame35Codec(KEY).encode_raw(cmd, payload)


async def test_v35_start_write_failure_returns_false() -> None:
    """A socket that dies on the START write fails soft (probe can fall back)."""
    writer = _Writer(fail_on_drain=1)
    ok = await session.handshake_35(
        asyncio.StreamReader(), writer, Frame35Codec(KEY), HOST
    )
    assert ok is False


async def test_v35_resp_timeout_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that never answers NEG_START times out to False, not a hang."""
    monkeypatch.setattr(session, "_HANDSHAKE_TIMEOUT", 0.05)
    ok = await session.handshake_35(
        asyncio.StreamReader(), _Writer(), Frame35Codec(KEY), HOST
    )
    assert ok is False


async def test_v35_wrong_resp_cmd_returns_false() -> None:
    """A decodable frame that is not SESS_KEY_NEG_RESP aborts the handshake."""
    reader = asyncio.StreamReader()
    reader.feed_data(_resp_35(b"\x00" * 48, cmd=const.CMD_STATUS))
    ok = await session.handshake_35(reader, _Writer(), Frame35Codec(KEY), HOST)
    assert ok is False


async def test_v35_short_resp_payload_returns_false() -> None:
    """A RESP too short to carry nonce+HMAC (48 bytes) is rejected."""
    reader = asyncio.StreamReader()
    reader.feed_data(_resp_35(b"\x00" * 20))
    ok = await session.handshake_35(reader, _Writer(), Frame35Codec(KEY), HOST)
    assert ok is False


async def test_v35_bad_inner_hmac_returns_false() -> None:
    """A RESP whose HMAC does not cover our nonce is a forgery — no session."""
    reader = asyncio.StreamReader()
    reader.feed_data(_resp_35(REMOTE_NONCE + b"\x00" * 32))
    ok = await session.handshake_35(reader, _Writer(), Frame35Codec(KEY), HOST)
    assert ok is False


async def test_v35_finish_write_failure_returns_false() -> None:
    """A valid RESP followed by a dead socket on FINISH still fails soft."""
    reader = asyncio.StreamReader()
    device = Frame35Codec(KEY)

    def respond(wire: bytes) -> bytes:
        frame, _ = device.decode(wire)
        assert frame.cmd == const.SESS_KEY_NEG_START
        resp = REMOTE_NONCE + hmac.new(KEY_B, frame.payload, hashlib.sha256).digest()
        return device.encode_raw(const.SESS_KEY_NEG_RESP, resp)

    writer = _Writer(reader, respond=respond, fail_on_drain=2)
    ok = await session.handshake_35(reader, writer, Frame35Codec(KEY), HOST)
    assert ok is False


async def test_recv_frame_accumulates_partial_frames() -> None:
    """_recv_frame keeps reading until a full frame decodes (TCP may split)."""
    codec = Frame35Codec(KEY)
    wire = Frame35Codec(KEY).encode_raw(const.SESS_KEY_NEG_RESP, b"\x00" * 48)
    reader = asyncio.StreamReader()
    reader.feed_data(wire[:10])
    task = asyncio.create_task(session._recv_frame(reader, codec, bytearray()))
    await asyncio.sleep(0.02)
    assert not task.done(), "returned before the frame was complete"
    reader.feed_data(wire[10:])
    frame = await asyncio.wait_for(task, timeout=1)
    assert frame.cmd == const.SESS_KEY_NEG_RESP


# ---------------------------------------------------------------------------
# v3.4 (55AA / ECB + HMAC trailer)
# ---------------------------------------------------------------------------


def _resp_34(payload: bytes, cmd: int = const.SESS_KEY_NEG_RESP) -> bytes:
    """Encode one device-side v3.4 frame (payload AES-ECB'd) under the real key."""
    return Frame34Codec(KEY).encode_raw(cmd, payload)


async def test_v34_start_write_failure_returns_false() -> None:
    writer = _Writer(fail_on_drain=1)
    ok = await session.handshake_34(
        asyncio.StreamReader(), writer, Frame34Codec(KEY), HOST, probe=True
    )
    assert ok is False


async def test_v34_resp_timeout_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session, "_HANDSHAKE_TIMEOUT", 0.05)
    ok = await session.handshake_34(
        asyncio.StreamReader(), _Writer(), Frame34Codec(KEY), HOST, probe=True
    )
    assert ok is False


async def test_v34_probe_swallows_trailer_auth_failure() -> None:
    """During a blind probe a trailer-HMAC failure means "probably not v3.4"
    (a v3.3 device shares the 55AA framing) — fall back, don't raise."""
    reader = asyncio.StreamReader()
    reader.feed_data(
        Frame34Codec("wrongkeywrongk!!").encode_raw(
            const.SESS_KEY_NEG_RESP, b"\x00" * 48
        )
    )
    ok = await session.handshake_34(
        reader, _Writer(), Frame34Codec(KEY), HOST, probe=True
    )
    assert ok is False


async def test_v34_wrong_resp_cmd_returns_false() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(_resp_34(b"\x00" * 48, cmd=const.CMD_STATUS))
    ok = await session.handshake_34(
        reader, _Writer(), Frame34Codec(KEY), HOST, probe=True
    )
    assert ok is False


async def test_v34_undecryptable_resp_probe_returns_false() -> None:
    """A RESP whose trailer verifies but whose body is not valid ciphertext
    (not block-aligned) fails soft during a probe."""
    reader = asyncio.StreamReader()
    reader.feed_data(
        Frame34Codec(KEY)._build_frame(const.SESS_KEY_NEG_RESP, b"\x00" * 8)
    )
    ok = await session.handshake_34(
        reader, _Writer(), Frame34Codec(KEY), HOST, probe=True
    )
    assert ok is False


async def test_v34_undecryptable_resp_required_raises_invalid_auth() -> None:
    """The same undecryptable RESP with v3.4 *required* means wrong key → reauth."""
    reader = asyncio.StreamReader()
    reader.feed_data(
        Frame34Codec(KEY)._build_frame(const.SESS_KEY_NEG_RESP, b"\x00" * 8)
    )
    with pytest.raises(InvalidAuth):
        await session.handshake_34(
            reader, _Writer(), Frame34Codec(KEY), HOST, probe=False
        )


async def test_v34_short_resp_plaintext_returns_false() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(_resp_34(b"\x00" * 10))
    ok = await session.handshake_34(
        reader, _Writer(), Frame34Codec(KEY), HOST, probe=True
    )
    assert ok is False


async def test_v34_bad_inner_hmac_returns_false() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(_resp_34(REMOTE_NONCE + b"\x00" * 32))
    ok = await session.handshake_34(
        reader, _Writer(), Frame34Codec(KEY), HOST, probe=True
    )
    assert ok is False


async def test_v34_finish_write_failure_returns_false() -> None:
    reader = asyncio.StreamReader()
    device = Frame34Codec(KEY)

    def respond(wire: bytes) -> bytes:
        frame, _ = device.decode(wire)
        assert frame.cmd == const.SESS_KEY_NEG_START
        local_nonce = aes_decrypt(frame.payload, KEY_B)
        resp = REMOTE_NONCE + hmac.new(KEY_B, local_nonce, hashlib.sha256).digest()
        return device.encode_raw(const.SESS_KEY_NEG_RESP, resp)

    writer = _Writer(reader, respond=respond, fail_on_drain=2)
    ok = await session.handshake_34(reader, writer, Frame34Codec(KEY), HOST, probe=True)
    assert ok is False
