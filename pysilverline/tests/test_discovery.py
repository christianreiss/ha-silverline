"""Tests for the UDP discovery listener.

A real broadcast captured from a live PC-SLP090N on 2026-05-22 anchors
the encrypted-decode path so any future codec drift is caught.
"""

from __future__ import annotations

import asyncio
import binascii
import json
import struct
from typing import Any
from unittest.mock import patch

import pytest

from pysilverline import const
from pysilverline.discovery import (
    UDP_DISCOVERY_KEY,
    DiscoveryInfo,
    _decode_broadcast,
    discover_once,
)
from pysilverline.protocol import aes_encrypt

# A captured live UDP/6667 broadcast from the user's PC-SLP090N.
# Decrypts to {"ip":"10.2.1.98","gwId":"bf90769136c9ac3653oqwj",...}.
LIVE_BROADCAST = bytes.fromhex(
    "000055aa00000000000000130000009c000000006f02543bfe9d2ab4a04bf16f8867c44c"
    "9969edaf006b881dc7e3e0702c06fb879717e14e4c8fab8bc25f0ab1242cba941a7bcb75"
    "ef5bb8eb330ad344dd0b106868c2c8903dec4bff0294046f69a2112cb14d42dae779bd09"
    "7a67839861716672dad090fa96230f965190ab4e70ed4f7f61a87e102e9488471c99fade"
    "ba005d13bcc651832dd9b60158654b01a12a9a520237a3fb0000aa55"
)


def _build_broadcast(
    body: dict[str, Any], *, cmd: int = 0x13, encrypted: bool = True
) -> bytes:
    """Wrap a JSON body in the Tuya UDP frame format used by real devices."""
    plaintext = json.dumps(body, separators=(",", ":")).encode()
    inner = aes_encrypt(plaintext, UDP_DISCOVERY_KEY) if encrypted else plaintext
    payload = struct.pack(">I", 0) + inner  # 4-byte zero retcode + body
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, cmd, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    return pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)


def test_decode_live_encrypted_broadcast() -> None:
    """The real captured frame decodes to a DiscoveryInfo with the
    expected fields."""
    info = _decode_broadcast(LIVE_BROADCAST, encrypted=True)
    assert info == DiscoveryInfo(
        device_id="bf90769136c9ac3653oqwj",
        ip="10.2.1.98",
        version="3.3",
        product_key="3bhylhz5zhogklel",
        encrypt=True,
    )


def test_decode_synthetic_encrypted_broadcast() -> None:
    body = {
        "ip": "192.168.1.42",
        "gwId": "bf1111111111111111aaaa",
        "version": "3.3",
        "productKey": "abc",
        "encrypt": True,
    }
    frame = _build_broadcast(body, encrypted=True)
    info = _decode_broadcast(frame, encrypted=True)
    assert info is not None
    assert info.device_id == "bf1111111111111111aaaa"
    assert info.ip == "192.168.1.42"
    assert info.product_key == "abc"


def test_decode_plain_broadcast() -> None:
    """UDP/6666 frames are not encrypted — the body after the retcode
    is JSON plaintext directly."""
    body = {
        "ip": "192.168.1.99",
        "gwId": "bf2222222222222222bbbb",
        "version": "3.3",
    }
    frame = _build_broadcast(body, encrypted=False)
    info = _decode_broadcast(frame, encrypted=False)
    assert info is not None
    assert info.device_id == "bf2222222222222222bbbb"
    assert info.ip == "192.168.1.99"


def test_decode_rejects_short_frame() -> None:
    assert _decode_broadcast(b"\x00\x00", encrypted=True) is None


def test_decode_rejects_bad_prefix() -> None:
    frame = _build_broadcast({"gwId": "x", "ip": "1.2.3.4"})
    # Corrupt the prefix.
    bad = b"\xde\xad\xbe\xef" + frame[4:]
    assert _decode_broadcast(bad, encrypted=True) is None


def test_decode_rejects_bad_crc() -> None:
    frame = bytearray(_build_broadcast({"gwId": "x", "ip": "1.2.3.4"}))
    frame[-8] ^= 0xFF  # flip a CRC byte
    assert _decode_broadcast(bytes(frame), encrypted=True) is None


def test_decode_rejects_bad_suffix() -> None:
    frame = bytearray(_build_broadcast({"gwId": "x", "ip": "1.2.3.4"}))
    frame[-4:] = b"\x00\x00\x00\x00"  # zero out the suffix
    assert _decode_broadcast(bytes(frame), encrypted=True) is None


def test_decode_rejects_missing_required_fields() -> None:
    """JSON without gwId or ip is treated as not-a-device."""
    no_gw = _build_broadcast({"ip": "1.2.3.4"})
    assert _decode_broadcast(no_gw, encrypted=True) is None
    no_ip = _build_broadcast({"gwId": "x"})
    assert _decode_broadcast(no_ip, encrypted=True) is None


def test_decode_rejects_garbage_random_bytes() -> None:
    """Random 200-byte buffer shouldn't crash the decoder."""
    import os
    for _ in range(20):
        assert _decode_broadcast(os.urandom(200), encrypted=True) is None


def test_decode_rejects_undecryptable_ciphertext() -> None:
    """A correctly-framed packet whose body is encrypted with a different
    key (e.g. another app on the LAN) must drop silently."""
    plaintext = b'{"gwId":"x","ip":"1.2.3.4"}'
    wrong_key = b"\x00" * 16
    ciphertext = aes_encrypt(plaintext, wrong_key)
    payload = struct.pack(">I", 0) + ciphertext
    size = len(payload) + 8
    header = struct.pack(">IIII", const.FRAME_PREFIX, 0, 0x13, size)
    pre_crc = header + payload
    crc = binascii.crc32(pre_crc) & 0xFFFFFFFF
    frame = pre_crc + struct.pack(">II", crc, const.FRAME_SUFFIX)
    assert _decode_broadcast(frame, encrypted=True) is None


async def test_discover_once_returns_unique_devices() -> None:
    """Three broadcasts of the same device collapse to one DiscoveryInfo."""
    info_a = DiscoveryInfo(device_id="bf_aaa", ip="1.1.1.1")
    info_b = DiscoveryInfo(device_id="bf_bbb", ip="2.2.2.2")
    captured: list[DiscoveryInfo] = [info_a, info_a, info_a, info_b]

    async def fake_bind(queue: asyncio.Queue[DiscoveryInfo]):
        for item in captured:
            queue.put_nowait(item)

        class _Stub:
            def close(self) -> None:
                pass

        return _Stub(), _Stub()

    with patch(
        "pysilverline.discovery._bind_listeners", side_effect=fake_bind
    ):
        result = await discover_once(timeout=0.1)
    assert {d.device_id for d in result} == {"bf_aaa", "bf_bbb"}


async def test_discover_once_returns_empty_on_silent_lan() -> None:
    """Nothing on the wire → empty list, no exceptions."""

    async def fake_bind(queue: asyncio.Queue[DiscoveryInfo]):
        class _Stub:
            def close(self) -> None:
                pass

        return _Stub(), _Stub()

    with patch(
        "pysilverline.discovery._bind_listeners", side_effect=fake_bind
    ):
        result = await discover_once(timeout=0.05)
    assert result == []
