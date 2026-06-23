"""Shared cryptographic primitives for the Tuya local protocol codecs.

AES-128-ECB with PKCS#7 (v3.3/v3.4) and AES-128-GCM (v3.5), plus the two
per-connection session-key derivations (v3.4 ECB, v3.5 GCM).
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import hmac

from ..exceptions import InvalidAuth, ProtocolError

_BLOCK_SIZE = 16  # AES-128 block size
_GCM_TAG_SIZE = 16
_GCM_IV_SIZE = 12


def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = _BLOCK_SIZE - (len(data) % _BLOCK_SIZE)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % _BLOCK_SIZE != 0:
        raise ProtocolError("ciphertext length not a multiple of block size")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > _BLOCK_SIZE:
        raise ProtocolError("invalid PKCS#7 padding")
    if not hmac.compare_digest(data[-pad_len:], bytes([pad_len]) * pad_len):
        raise ProtocolError("corrupt PKCS#7 padding")
    return data[:-pad_len]


def _make_cipher(key: bytes) -> Cipher[modes.ECB]:
    if len(key) != 16:
        raise ValueError(f"local_key must be 16 bytes, got {len(key)}")
    return Cipher(algorithms.AES(key), modes.ECB())


def aes_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB encrypt with PKCS#7 padding."""
    encryptor = _make_cipher(key).encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def aes_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB decrypt with PKCS#7 unpadding."""
    decryptor = _make_cipher(key).decryptor()
    raw = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(raw)


def derive_session_key_34(
    local_nonce: bytes, remote_nonce: bytes, real_key: bytes
) -> bytes:
    """Derive the v3.4 per-connection session key from the exchanged nonces.

    XOR the two 16-byte nonces and AES-ECB-encrypt the result with the real
    key — no padding, no IV, a single 16-byte block out. Mirrors TinyTuya's
    ``_negotiate_session_key_generate_finalize`` for v3.4
    (``cipher.encrypt(local_nonce ^ remote_nonce, use_base64=False, pad=False)``).
    Differs from :func:`derive_session_key_35`, which uses GCM with an IV slice.
    """
    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    encryptor = _make_cipher(real_key).encryptor()
    return encryptor.update(xored) + encryptor.finalize()


def aes_gcm_encrypt(
    plaintext: bytes, key: bytes, iv: bytes, aad: bytes
) -> tuple[bytes, bytes]:
    """AES-128-GCM encrypt; returns (ciphertext, tag)."""
    ct_and_tag = AESGCM(key).encrypt(iv, plaintext, aad)
    return ct_and_tag[:-_GCM_TAG_SIZE], ct_and_tag[-_GCM_TAG_SIZE:]


def aes_gcm_decrypt(
    ciphertext: bytes, key: bytes, iv: bytes, aad: bytes, tag: bytes
) -> bytes:
    """AES-128-GCM decrypt; raises InvalidAuth on tag mismatch."""
    try:
        return AESGCM(key).decrypt(iv, ciphertext + tag, aad)
    except Exception as err:
        raise InvalidAuth("GCM tag mismatch — local_key likely wrong") from err


def derive_session_key_35(
    local_nonce: bytes, remote_nonce: bytes, real_key: bytes
) -> bytes:
    """Derive the v3.5 per-connection session key from the exchanged nonces.

    XOR nonces, AES-GCM-encrypt the result with the real key (IV = first 12
    bytes of local nonce), and take bytes 12..28 of the full (IV||CT||tag)
    output — i.e. the 16-byte ciphertext slice.  Mirrors TinyTuya's
    ``_negotiate_session_key_generate_finalize`` for v3.5.
    """
    xored = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    iv = local_nonce[:_GCM_IV_SIZE]
    # AESGCM.encrypt returns ciphertext(16B) + tag(16B) for a 16-byte plaintext;
    # the session key is CT[0:16]. TinyTuya frames the same bytes as
    # (IV||CT||tag)[12:28] — the 12-byte IV prefix never goes on the wire here,
    # so we slice the ciphertext directly instead of prepending it.
    ct_tag = AESGCM(real_key).encrypt(iv, xored, None)
    return ct_tag[:_BLOCK_SIZE]  # first 16 bytes = ciphertext = session key
