"""MD5-crypt, for setting a radio's admin password.

airOS stores ``users.N.password`` as an MD5-crypt hash and authenticates by
comparing ``crypt(entered, stored_salt)`` against it. Verified against a real
Bullet AC IP67: regenerating its stored value with this module, and independently
with ``openssl passwd -1 -salt EC25aZzE 'dredge101!'``, both reproduce
``$1$EC25aZzE$xu1o7GxJU2vdvIE/iEP4/0`` byte for byte.

Implemented here rather than via :mod:`crypt` because that module exists in the
runtime image (Python 3.11) but was **removed in 3.12+**, where the tests run — so
using it would leave this code path unexercised locally, which is exactly how a
password bug reaches a mast-mounted radio.

:func:`verify` is the important one. It lets the reconciler ask "does the radio's
existing hash already match the configured password?" and skip the write, rather
than rewriting (and rebooting) every radio just because its salt differs.
"""

from __future__ import annotations

import hashlib
import re
import secrets

#: The non-standard base64 alphabet crypt(3) uses.
ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
SALT_CHARS = ITOA64
#: ``$id$salt$digest``.
CRYPT_RE = re.compile(r"^\$([0-9a-z]{1,2})\$([^$]+)\$([^$]+)$")


def _to64(value: int, count: int) -> str:
    return "".join(ITOA64[(value >> (6 * i)) & 0x3F] for i in range(count))


def make_salt(length: int = 8) -> str:
    """A random salt in crypt's alphabet."""
    return "".join(secrets.choice(SALT_CHARS) for _ in range(length))


def md5_crypt(password: str, salt: str | None = None) -> str:
    """Hash ``password`` as ``$1$salt$digest``, generating a salt if none given."""
    if salt is None:
        salt = make_salt()
    pw, sb = password.encode(), salt.encode()

    ctx = hashlib.md5(pw + b"$1$" + sb)
    alt = hashlib.md5(pw + sb + pw).digest()
    remaining = len(pw)
    while remaining > 0:
        ctx.update(alt[: min(remaining, 16)])
        remaining -= 16
    bits = len(pw)
    while bits:
        ctx.update(b"\x00" if bits & 1 else pw[:1])
        bits >>= 1
    digest = ctx.digest()

    # 1000 rounds, exactly as crypt_md5 specifies. Deliberately not "optimised".
    for i in range(1000):
        c = hashlib.md5()
        c.update(pw if i & 1 else digest)
        if i % 3:
            c.update(sb)
        if i % 7:
            c.update(pw)
        c.update(digest if i & 1 else pw)
        digest = c.digest()

    out = ""
    for a, b, cc in ((0, 6, 12), (1, 7, 13), (2, 8, 14), (3, 9, 15), (4, 10, 5)):
        out += _to64((digest[a] << 16) | (digest[b] << 8) | digest[cc], 4)
    out += _to64(digest[11], 2)
    return f"$1${salt}${out}"


def is_hash(value: str) -> bool:
    """Whether ``value`` looks like a crypt hash rather than a passphrase."""
    return bool(CRYPT_RE.match(value or ""))


def verify(password: str, stored: str) -> bool:
    """Whether ``stored`` is a hash of ``password``, reusing its own salt.

    Only ``$1$`` (MD5-crypt) can be checked — that is what airOS writes. Any other
    scheme returns False, which makes the caller re-set the password rather than
    silently assume a match it cannot confirm.
    """
    m = CRYPT_RE.match(stored or "")
    if not m or m.group(1) != "1":
        return False
    return md5_crypt(password, m.group(2)) == stored
