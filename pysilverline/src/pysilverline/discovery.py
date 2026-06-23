"""UDP broadcast discovery for Tuya v3.3 devices.

Every Tuya device that's running the standard Tuya local stack
(WBR3 / Realtek-based modules in particular) broadcasts a small JSON
blob announcing its presence every ~10-30 seconds, encrypted with a
*static* AES-128-ECB key shared across the entire Tuya ecosystem:

    UDP_DISCOVERY_KEY = MD5(b"yGAdlopoPVldABfn")

That key is published in tinytuya and tuya-local; it's the same on
every Tuya device, regardless of cloud account.

Frame format on the wire (verified live against a Poolex PC-SLP090N
on 2026-05-22):

    [prefix 0x000055AA][seq][cmd=0x13][size][payload][crc32][suffix 0x0000AA55]
    payload = [4 zero bytes (retcode)][AES-128-ECB ciphertext]

Note that this is subtly different from the TCP push frames — the UDP
payload has NO inner ``3.3`` header between the retcode and the
ciphertext.

Decoded JSON fields seen in the wild:

    {
        "ip":         "10.2.1.98",
        "gwId":       "bf90769136c9ac3653oqwj",
        "active":     2,
        "ablility":   0,        # sic — Tuya typo, sometimes "ablilty"
        "encrypt":    true,
        "productKey": "3bhylhz5zhogklel",
        "version":    "3.3"
    }
"""

from __future__ import annotations

import asyncio
import binascii
import hashlib
import json
import logging
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass

from . import const
from .protocol import aes_decrypt

_LOGGER = logging.getLogger(__name__)

CMD_UDP: int = 0x13
UDP_DISCOVERY_KEY: bytes = hashlib.md5(b"yGAdlopoPVldABfn").digest()


@dataclass(slots=True, kw_only=True, frozen=True)
class DiscoveryInfo:
    """One device announcement parsed from a UDP broadcast."""

    device_id: str
    ip: str
    version: str = "3.3"
    product_key: str | None = None
    encrypt: bool = True


def _decode_broadcast(data: bytes, *, encrypted: bool) -> DiscoveryInfo | None:
    """Parse a single UDP datagram. Returns None on any malformation —
    discovery must tolerate any garbage that lands on the listening port."""
    if len(data) < 24:
        return None
    try:
        prefix, _seq, _cmd, size = struct.unpack(">IIII", data[:16])
    except struct.error:
        return None
    if prefix != const.FRAME_PREFIX:
        return None
    total = 16 + size
    if len(data) < total:
        return None
    # Last 8 bytes are crc32 + suffix; validate CRC so we don't decrypt junk.
    expected_crc, suffix = struct.unpack(">II", data[total - 8 : total])
    if suffix != const.FRAME_SUFFIX:
        return None
    if expected_crc != binascii.crc32(data[: total - 8]) & 0xFFFFFFFF:
        return None
    payload = data[16 : total - 8]
    if len(payload) < 4:
        return None
    # UDP discovery payloads always carry a 4-byte zero retcode; the rest
    # is either plaintext JSON (port 6666) or AES-128-ECB ciphertext (port 6667).
    body = payload[4:]
    if encrypted:
        if len(body) == 0 or len(body) % 16 != 0:
            return None
        try:
            plaintext = aes_decrypt(body, UDP_DISCOVERY_KEY)
        except Exception:  # noqa: BLE001 — codec raises ProtocolError/ValueError/InvalidAuth
            return None
    else:
        plaintext = body
    try:
        parsed: object = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    gw_id = parsed.get("gwId")
    ip = parsed.get("ip")
    if not isinstance(gw_id, str) or not isinstance(ip, str):
        return None
    # Reject hostile broadcasts that try to embed control characters
    # in identifier fields (log-injection vector — these strings get
    # logged at INFO by the integration).
    if not gw_id.isprintable() or not ip.isprintable():
        return None
    product_key_raw = parsed.get("productKey")
    product_key = (
        product_key_raw
        if isinstance(product_key_raw, str) and product_key_raw.isprintable()
        else None
    )
    return DiscoveryInfo(
        device_id=gw_id,
        ip=ip,
        version=str(parsed.get("version", "3.3")),
        product_key=product_key,
        encrypt=bool(parsed.get("encrypt", encrypted)),
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Pushes parsed DiscoveryInfo events onto an asyncio.Queue."""

    def __init__(self, queue: asyncio.Queue[DiscoveryInfo], *, encrypted: bool) -> None:
        self._queue = queue
        self._encrypted = encrypted

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        info = _decode_broadcast(data, encrypted=self._encrypted)
        if info is None:
            return
        try:
            self._queue.put_nowait(info)
        except asyncio.QueueFull:
            _LOGGER.debug("discovery queue full; dropping %s", info.device_id)


async def _bind_listeners(
    queue: asyncio.Queue[DiscoveryInfo],
) -> tuple[asyncio.DatagramTransport, asyncio.DatagramTransport]:
    """Bind to both Tuya discovery ports. Returns the two transports
    so callers can close them when done.

    ``reuse_port=True`` maps to ``SO_REUSEPORT`` (Linux/BSD/macOS) and
    will raise on Windows core installs — fine for HA OS / Supervised /
    Container deployments, which are the supported targets. The Linux
    kernel load-balances inbound datagrams across all sockets bound to
    the same port, so a co-resident Tuya integration (tinytuya etc.)
    sharing this port may steal a fraction of broadcasts; the Tuya
    device repeats every ~25 s so the next sweep picks them up.
    """
    loop = asyncio.get_running_loop()
    t_plain, _ = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(queue, encrypted=False),
        local_addr=("0.0.0.0", const.DISCOVERY_PORT_PLAIN),
        allow_broadcast=True,
        reuse_port=True,
    )
    t_enc, _ = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(queue, encrypted=True),
        local_addr=("0.0.0.0", const.DISCOVERY_PORT_ENCRYPTED),
        allow_broadcast=True,
        reuse_port=True,
    )
    return t_plain, t_enc


async def discover_once(timeout: float = 15.0) -> list[DiscoveryInfo]:
    """Listen for UDP broadcasts for ``timeout`` seconds.

    Returns the set of unique devices seen (deduplicated by ``device_id``).
    Returns an empty list if no devices announce in the window. Never
    raises on garbage input.
    """
    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue()
    t_plain, t_enc = await _bind_listeners(queue)
    seen: dict[str, DiscoveryInfo] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                info = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            seen[info.device_id] = info
    finally:
        t_plain.close()
        t_enc.close()
    return list(seen.values())


async def discover() -> AsyncIterator[DiscoveryInfo]:
    """Listen indefinitely for UDP broadcasts.

    Yields every parsed announcement (no deduplication — callers that
    care can track ``device_id``s themselves). Cancel the task to stop.
    """
    queue: asyncio.Queue[DiscoveryInfo] = asyncio.Queue()
    t_plain, t_enc = await _bind_listeners(queue)
    try:
        while True:
            info = await queue.get()
            yield info
    finally:
        t_plain.close()
        t_enc.close()
