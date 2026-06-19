"""Tiny local Tuya v3.5 server for Home Assistant integration tests."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import struct
from typing import Any

from pysilverline import const
from pysilverline.protocol import (
    Frame35Codec,
    aes_gcm_encrypt,
    derive_session_key_35,
)

KEY = "0123456789abcdef"
KEY_B = KEY.encode()
DEVICE_ID = "bf12345678abcdefghijkl"
REMOTE_NONCE = bytes(range(16, 32))


def _encode_35(seq: int, cmd: int, plaintext: bytes, key: bytes) -> bytes:
    iv = os.urandom(12)
    length = 12 + len(plaintext) + 16
    header = struct.pack(">IHIII", const.FRAME_PREFIX_35, 0, seq, cmd, length)
    ciphertext, tag = aes_gcm_encrypt(plaintext, key, iv, header[4:])
    return header + iv + ciphertext + tag + struct.pack(">I", const.FRAME_SUFFIX_35)


class FakeTuya35Server:
    """Minimal TCP fake for the Tuya 6699/AES-GCM protocol."""

    def __init__(self, *, dps: dict[str, Any]) -> None:
        self.dps = dps
        self.port = 0
        self.queries = 0
        self.finish_decoded_with_real_key = False
        self.finish_hmac_ok = False
        self._server: asyncio.base_events.Server | None = None
        # Device-global response seqno: real v3.5 devices do NOT echo the
        # request's seqno (TinyTuya XenonDevice._get_retcode compares seqno
        # only for version < 3.5). Start above the client's request seqs.
        self._resp_seq = 0x8000

    def _next_resp_seq(self) -> int:
        self._resp_seq += 1
        return self._resp_seq

    async def __aenter__(self) -> "FakeTuya35Server":
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
        codec = Frame35Codec(KEY)
        local_nonce = b""
        session_key: bytes | None = None
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                buf.extend(chunk)
                while len(buf) >= 18:
                    try:
                        frame, remainder = codec.decode(bytes(buf))
                    except Exception:
                        return
                    buf = bytearray(remainder)

                    if frame.cmd == const.SESS_KEY_NEG_START:
                        local_nonce = frame.payload
                        resp = (
                            REMOTE_NONCE
                            + hmac.new(KEY_B, local_nonce, hashlib.sha256).digest()
                        )
                        writer.write(
                            _encode_35(
                                frame.seq,
                                const.SESS_KEY_NEG_RESP,
                                resp,
                                codec._key,
                            )
                        )
                        await writer.drain()
                        continue

                    if frame.cmd == const.SESS_KEY_NEG_FINISH:
                        self.finish_decoded_with_real_key = True
                        expected = hmac.new(
                            KEY_B, REMOTE_NONCE, hashlib.sha256
                        ).digest()
                        self.finish_hmac_ok = hmac.compare_digest(
                            frame.payload, expected
                        )
                        session_key = derive_session_key_35(
                            local_nonce, REMOTE_NONCE, KEY_B
                        )
                        codec.update_session_key(session_key)
                        continue

                    if frame.cmd == const.CMD_DP_QUERY and session_key is not None:
                        self.queries += 1
                        payload = (
                            struct.pack(">I", 0)
                            + json.dumps({"devId": DEVICE_ID, "dps": self.dps}).encode()
                        )
                        writer.write(
                            _encode_35(
                                self._next_resp_seq(),  # global seqno, not an echo
                                const.CMD_DP_QUERY,
                                payload,
                                session_key,
                            )
                        )
                        await writer.drain()
        finally:
            writer.close()
