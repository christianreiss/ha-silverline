"""v3.5 end-to-end edge cases — parity with test_client_more.py (v3.3).

test_client_35.py covers the v3.5-specific surface (handshake, auto-probe,
fallback, get_status/set/push). These add the shared-machinery behaviors that
were previously proven only on the v3.3 path: request timeout, heartbeat over
the session key, reconnect WITH a fresh re-handshake, and malformed-frame
desync. Each rides on the negotiated session key, so they exercise the v3.5
codec end to end rather than re-testing protocol-agnostic plumbing in a vacuum.

The fake v3.5 device, its key timing, and the DP-query handler are reused from
test_client_35.py — there is one v3.5 lib fake server, not several.
"""

from __future__ import annotations

import asyncio

import pytest

import pysilverline.client as client_mod
from pysilverline import SilverlineClient, const
from pysilverline.exceptions import CannotConnect, InvalidAuth
from tests.test_client_35 import (
    DEVICE_ID,
    KEY,
    FakeTuya35Server,
    _dp_query_handler,
)


async def test_v35_get_status_times_out_when_device_silent() -> None:
    """Handshake succeeds, but the device never answers the DP_QUERY → the
    request times out as CannotConnect (no hang, no wrong exception)."""
    async with FakeTuya35Server() as server:
        # No CMD_DP_QUERY handler registered → the server records the query but
        # never replies.
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=0.2,
        )
        await client.connect()
        try:
            assert client.detected_version == "3.5"
            with pytest.raises(CannotConnect):
                await client.get_status()
            # The query did reach the device (decrypted under the session key).
            assert any(cmd == const.CMD_DP_QUERY for _, cmd, _ in server.received)
        finally:
            await client.disconnect()


async def test_v35_heartbeat_is_sent_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Heartbeats are emitted on the interval AND encrypted with the session
    key — the device decodes them, which a real-key/wrong-key frame couldn't."""
    monkeypatch.setattr(client_mod, "_HEARTBEAT_INTERVAL", 0.05)
    async with FakeTuya35Server() as server:
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
            for _ in range(100):
                if any(cmd == const.CMD_HEART_BEAT for _, cmd, _ in server.received):
                    break
                await asyncio.sleep(0.02)
            heartbeats = [r for r in server.received if r[1] == const.CMD_HEART_BEAT]
            assert heartbeats, "no heartbeat frame decoded under the session key"
        finally:
            await client.disconnect()


async def test_v35_reconnect_re_handshakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the socket drops, the client reconnects and runs a SECOND full
    handshake — proving the session key is renegotiated per TCP connection
    (connect() resets the v3.5 codec to the real key before each handshake)."""
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (0.05, 0.05, 0.05, 0.05))
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True, "3": 25})
        server.drop_after_handshake = True  # hang up connection #1 post-handshake
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
            for _ in range(150):
                if server.connections >= 2 and client.connected:
                    break
                await asyncio.sleep(0.02)
            assert server.connections >= 2, "client did not reconnect/re-handshake"
            assert server.finish_decoded_with_real_key is True
            assert server.finish_hmac_ok is True
            # The freshly negotiated session key works end to end.
            state = await client.get_status()
            assert state.power is True
            assert state.temp_current == 25
        finally:
            await client.disconnect()


async def test_v35_reconnect_survives_invalid_auth_and_surfaces_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected re-handshake (rotated key) must NOT kill the reconnect task,
    and must still reach the reauth path.

    While the handshake keeps failing, ``get_status`` raises ``InvalidAuth``
    (not a bare "not connected" ``CannotConnect``) so the HA coordinator can
    raise ConfigEntryAuthFailed and prompt reauth. And because the retry loop
    stays alive, a transient failure self-heals: once the device accepts the
    key again, the client reconnects with no outside help.

    Finally, the successful re-handshake must CLEAR the recorded auth
    failure (connect() resets _reconnect_auth_error): a later, ordinary
    outage with the still-correct key surfaces as a plain CannotConnect,
    not a stale InvalidAuth that would falsely re-trigger reauth.
    """
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (0.05, 0.05))
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True})
        server.drop_after_handshake = True  # hang up connection #1 post-handshake
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
            # From now on every NEG_RESP is encrypted under a wrong key, so
            # each reconnect handshake raises InvalidAuth inside connect().
            server._resp_key = b"wrongkeywrongk!!"
            # Connection #2 is the first failed handshake; #3 only happens if
            # the reconnect loop survived #2's InvalidAuth and dialed again.
            for _ in range(200):
                if server.connections >= 3:
                    break
                await asyncio.sleep(0.02)
            assert server.connections >= 3, (
                "reconnect task died after the first InvalidAuth handshake"
            )
            task = client._reconnect_task
            assert task is not None and not task.done(), (
                "reconnect task is gone — InvalidAuth killed the retry loop"
            )
            # The poll path surfaces the auth failure — the reauth trigger.
            with pytest.raises(InvalidAuth):
                await client.get_status()
            # Device accepts the key again (transient corruption cleared):
            # the still-running loop reconnects on its own.
            server._resp_key = None
            for _ in range(200):
                if client.connected:
                    break
                await asyncio.sleep(0.02)
            assert client.connected, "reconnect loop never self-healed"
            state = await client.get_status()
            assert state.power is True
            # A second, unrelated outage with the CORRECT key: the device
            # goes dark (no new connections) and the live socket drops.
            assert server._server is not None
            server._server.close()  # refuse all further reconnect attempts
            assert server._writer is not None
            server._writer.close()  # drop the live connection
            for _ in range(200):
                if not client.connected:
                    break
                await asyncio.sleep(0.02)
            assert not client.connected, "second connection drop never registered"
            # The self-heal handshake cleared the auth failure, so this is a
            # plain not-connected CannotConnect — NOT a stale InvalidAuth.
            with pytest.raises(CannotConnect):
                await client.get_status()
        finally:
            await client.disconnect()


async def test_v35_disconnect_clears_stale_reconnect_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disconnect() during a still-rejected re-handshake clears the recorded
    auth failure.

    Unlike the self-heal path above, the correct key is never restored: the
    consumer gives up and calls ``disconnect`` while the reconnect loop is
    still failing with InvalidAuth. An explicit disconnect ends the session
    cleanly, so a later poll must report a plain not-connected
    ``CannotConnect`` — not a stale ``InvalidAuth`` from a session the caller
    deliberately tore down.
    """
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (0.05, 0.05))
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True})
        server.drop_after_handshake = True  # hang up connection #1 post-handshake
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
            # Every subsequent NEG_RESP is encrypted under a wrong key, so
            # each reconnect handshake raises InvalidAuth inside connect().
            server._resp_key = b"wrongkeywrongk!!"
            for _ in range(200):
                if client._reconnect_auth_error is not None:
                    break
                await asyncio.sleep(0.02)
            # The client is in the auth-failure state: polls raise InvalidAuth.
            with pytest.raises(InvalidAuth):
                await client.get_status()
        finally:
            # Tear down WITHOUT restoring the key — the loop never self-heals.
            await client.disconnect()
        # The explicit disconnect cleared the recorded handshake failure:
        # a poll now raises a plain CannotConnect, not a stale InvalidAuth.
        with pytest.raises(CannotConnect):
            await client.get_status()


async def test_v35_malformed_frame_drops_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame with a corrupted v3.5 suffix desyncs the stream → the client
    drops the connection (and signals listeners), rather than soldiering on."""
    # Long backoff so the dropped state is observable before any reconnect.
    monkeypatch.setattr(client_mod, "_RECONNECT_BACKOFF", (5.0,))
    async with FakeTuya35Server() as server:
        server.handlers[const.CMD_DP_QUERY] = _dp_query_handler({"1": True})
        events: list[bool] = []
        client = SilverlineClient(
            host="127.0.0.1",
            port=server.port,
            device_id=DEVICE_ID,
            local_key=KEY,
            protocol_version="3.5",
            request_timeout=2.0,
        )
        client.add_connection_listener(events.append)
        await client.connect()
        try:
            await client.get_status()  # establishes the live session path
            await server.push_malformed()
            for _ in range(100):
                if not client.connected:
                    break
                await asyncio.sleep(0.02)
            assert not client.connected, "malformed frame did not drop the socket"
            assert events and events[-1] is False
        finally:
            await client.disconnect()
