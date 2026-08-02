"""Coverage-gap tests for the UDP discovery listener.

These exercise:
- the _decode_broadcast guard for struct-malformed headers, short
  payload, undersize total-size, encrypted body not a multiple of 16,
  non-dict JSON, and non-string gwId/ip;
- the _DiscoveryProtocol class behavior (datagram_received, QueueFull);
- the live datagram endpoint binding helper bound to ephemeral ports
  via monkeypatch (so we don't bind UDP/6666 + UDP/6667 directly —
  those need privileges on some systems and might collide with a
  co-resident Tuya tool);
- discover() — the async-iterator variant — yielding then cancelling.
"""

from __future__ import annotations

import asyncio
import binascii
import json
import struct
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest

from pysilverline import const
from pysilverline.discovery import (
    UDP_DISCOVERY_KEY,
    DiscoveryInfo,
    _decode_broadcast,
    _DiscoveryProtocol,
    discover,
)
from pysilverline.protocol import aes_encrypt


def _build_broadcast(
    body: dict[str, Any], *, cmd: int = 0x13, encrypted: bool = True
) -> bytes:
    plaintext = json.dumps(body, separators=(",", ":")).encode()
    inner = aes_encrypt(plaintext, UDP_DISCOVERY_KEY) if encrypted else plaintext
    payload = struct.pack(">I", 0) + inner
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, cmd, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    return pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)


# ---------------------------------------------------------------------------
# _decode_broadcast — the still-uncovered guard branches
# ---------------------------------------------------------------------------


def test_decode_rejects_non_utf8_plaintext_payload() -> None:
    """Plain (unencrypted) frame whose body isn't valid UTF-8 → drop."""
    # Frame-shaped plaintext that won't parse as JSON.
    payload = struct.pack(">I", 0) + b"\xff\xfe\xfd\xfc"
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, 0x13, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    frame = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)
    assert _decode_broadcast(frame, encrypted=False) is None


def test_decode_rejects_undersized_total_size() -> None:
    """Header claims a frame longer than the buffer — bail out."""
    # 24 bytes minimum, but size field promises more.
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, 0x13, 256)
    short = header + b"\x00" * 8  # well short of the promised 256
    assert _decode_broadcast(short, encrypted=True) is None


def test_decode_rejects_zero_length_encrypted_body() -> None:
    """Encrypted frame whose ciphertext body is empty — multiple-of-16
    check rejects, so decryption never happens."""
    payload = struct.pack(">I", 0)  # 4-byte retcode + zero body
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, 0x13, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    frame = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)
    assert _decode_broadcast(frame, encrypted=True) is None


def test_decode_rejects_misaligned_encrypted_body() -> None:
    """Ciphertext length that isn't a multiple of 16 — AES would reject;
    we short-circuit before decrypting."""
    payload = struct.pack(">I", 0) + b"\x00" * 7  # 7-byte fake ciphertext
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, 0x13, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    frame = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)
    assert _decode_broadcast(frame, encrypted=True) is None


def test_decode_rejects_payload_shorter_than_retcode() -> None:
    """Total payload < 4 bytes — there's no room for the leading retcode."""
    # Build a frame whose size implies a payload of exactly 0 bytes
    # (impossible for a real Tuya frame; we want the "len(payload) < 4" branch).
    payload = b""
    size = len(payload) + 8  # 8 just for footer
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, 0x13, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    frame = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)
    assert _decode_broadcast(frame, encrypted=True) is None


def test_decode_rejects_non_object_decrypted_json() -> None:
    """JSON decodes to a list, not an object — not a device announcement."""
    plaintext = b"[1,2,3]"
    inner = aes_encrypt(plaintext, UDP_DISCOVERY_KEY)
    payload = struct.pack(">I", 0) + inner
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, 0x13, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    frame = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)
    assert _decode_broadcast(frame, encrypted=True) is None


def test_decode_rejects_non_string_gwid_or_ip() -> None:
    """If gwId or ip is present but not a string, treat as not-a-device."""
    weird = _build_broadcast({"gwId": 12345, "ip": "10.0.0.1"})
    assert _decode_broadcast(weird, encrypted=True) is None
    weird2 = _build_broadcast({"gwId": "x", "ip": 12345})
    assert _decode_broadcast(weird2, encrypted=True) is None


def test_decode_ignores_non_string_product_key() -> None:
    """A productKey field of the wrong type collapses to None — does not
    crash the parser."""
    body = {"gwId": "bf_x", "ip": "1.2.3.4", "productKey": 42}
    frame = _build_broadcast(body, encrypted=True)
    info = _decode_broadcast(frame, encrypted=True)
    assert info is not None
    assert info.product_key is None


# ---------------------------------------------------------------------------
# _DiscoveryProtocol behavior
# ---------------------------------------------------------------------------


def test_discovery_protocol_drops_bad_datagrams() -> None:
    """A protocol instance receiving garbage just ignores it."""
    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue()
    proto = _DiscoveryProtocol(queue, encrypted=True)
    proto.datagram_received(b"\x00\x00", ("10.0.0.1", 6667))
    assert queue.empty()


def test_discovery_protocol_enqueues_decoded_info() -> None:
    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue()
    proto = _DiscoveryProtocol(queue, encrypted=True)
    proto.datagram_received(
        _build_broadcast({"gwId": "bf_z", "ip": "5.6.7.8"}),
        ("5.6.7.8", 6667),
    )
    info = queue.get_nowait()
    assert info.device_id == "bf_z"
    assert info.ip == "5.6.7.8"


def test_discovery_protocol_drops_when_queue_full() -> None:
    """A bounded queue that's already full drops the latest broadcast;
    the device will repeat in ~25 s so this is recoverable."""
    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue(maxsize=1)
    proto = _DiscoveryProtocol(queue, encrypted=True)
    proto.datagram_received(
        _build_broadcast({"gwId": "bf_a", "ip": "1.1.1.1"}),
        ("1.1.1.1", 6667),
    )
    # Second one must not raise even though the queue is full.
    proto.datagram_received(
        _build_broadcast({"gwId": "bf_b", "ip": "2.2.2.2"}),
        ("2.2.2.2", 6667),
    )
    assert queue.qsize() == 1


# ---------------------------------------------------------------------------
# discover() — async iterator variant
# ---------------------------------------------------------------------------


async def test_discover_once_breaks_on_expired_deadline() -> None:
    """A non-positive timeout means the deadline check at the top of the
    loop body fires before any queue read. Returns immediately with the
    items already queued up."""
    from pysilverline.discovery import discover_once

    info_a = DiscoveryInfo(device_id="bf_x", ip="10.0.0.1")

    async def fake_bind(queue: asyncio.Queue[DiscoveryInfo]) -> tuple[Any, Any]:
        # Pre-fill the queue, then return; with a negative timeout the
        # remaining-time check trips on the first loop iteration without
        # us getting a chance to drain the queue.
        queue.put_nowait(info_a)

        class _Stub:
            def close(self) -> None: ...

        return _Stub(), _Stub()

    with patch("pysilverline.discovery._bind_listeners", side_effect=fake_bind):
        # Timeout of 0 → first remaining = 0 → break immediately. We never
        # consume the queued item, so the returned list is empty.
        result = await discover_once(timeout=0.0)
    assert result == []


async def test_discover_yields_then_cancels() -> None:
    """The async-iterator form yields whatever the bound listeners feed
    into the shared queue and shuts the transports down on cancellation."""
    captured = [
        DiscoveryInfo(device_id="bf_p", ip="9.9.9.9"),
        DiscoveryInfo(device_id="bf_q", ip="9.9.9.10"),
    ]
    closed: list[str] = []

    async def fake_bind(queue: asyncio.Queue[DiscoveryInfo]) -> tuple[Any, Any]:
        assert queue.maxsize == 256
        for item in captured:
            queue.put_nowait(item)

        class _StubTransport:
            def __init__(self, label: str) -> None:
                self._label = label

            def close(self) -> None:
                closed.append(self._label)

        return _StubTransport("plain"), _StubTransport("enc")

    seen: list[DiscoveryInfo] = []
    with patch("pysilverline.discovery._bind_listeners", side_effect=fake_bind):
        agen: AsyncGenerator[DiscoveryInfo, None] = discover()
        try:
            seen.append(await asyncio.wait_for(agen.__anext__(), timeout=1.0))
            seen.append(await asyncio.wait_for(agen.__anext__(), timeout=1.0))
        finally:
            await agen.aclose()
    assert [d.device_id for d in seen] == ["bf_p", "bf_q"]
    # Both bound transports were closed on shutdown.
    assert sorted(closed) == ["enc", "plain"]


# ---------------------------------------------------------------------------
# _bind_listeners — real datagram endpoints on the loopback interface
# ---------------------------------------------------------------------------


async def test_bind_listeners_binds_two_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_bind_listeners opens two UDP sockets, one plain and one encrypted.

    We monkey-patch the real Tuya discovery ports onto OS-assigned
    ephemeral ports so the test doesn't need privileged ports or
    coexist with a real device on the LAN. The successful return of
    two transports is the test — coverage flows out of that.
    """
    # Pick two free ephemeral ports by binding+immediately releasing.
    import socket

    import pysilverline.discovery as discovery_mod

    def _pick() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    plain_port = _pick()
    enc_port = _pick()
    monkeypatch.setattr(discovery_mod.const, "DISCOVERY_PORT_PLAIN", plain_port)
    monkeypatch.setattr(discovery_mod.const, "DISCOVERY_PORT_ENCRYPTED", enc_port)

    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue()
    t_plain, t_enc = await discovery_mod._bind_listeners(queue)
    try:
        # Sanity: both transports have the expected bound port.
        assert t_plain.get_extra_info("sockname")[1] == plain_port
        assert t_enc.get_extra_info("sockname")[1] == enc_port
    finally:
        t_plain.close()
        t_enc.close()


async def test_bind_listeners_actually_receives_real_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a UDP packet sent to the bound encrypted port lands in
    the queue, having flowed through datagram_received → _decode_broadcast.
    This is what makes discover()/discover_once() actually work."""
    import socket

    import pysilverline.discovery as discovery_mod

    def _pick() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    plain_port = _pick()
    enc_port = _pick()
    monkeypatch.setattr(discovery_mod.const, "DISCOVERY_PORT_PLAIN", plain_port)
    monkeypatch.setattr(discovery_mod.const, "DISCOVERY_PORT_ENCRYPTED", enc_port)

    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue()
    t_plain, t_enc = await discovery_mod._bind_listeners(queue)
    try:
        # Fire a real UDP datagram at the encrypted port and wait for the
        # decoded DiscoveryInfo to appear in the queue.
        frame = _build_broadcast(
            {"gwId": "bf_real", "ip": "10.42.42.42"}, encrypted=True
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(frame, ("127.0.0.1", enc_port))
        info = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert info.device_id == "bf_real"
        assert info.ip == "10.42.42.42"
    finally:
        t_plain.close()
        t_enc.close()
