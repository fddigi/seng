#!/usr/bin/env python3
"""Hash a password with PBKDF2-HMAC-SHA256, in the exact format the Cloudflare
Worker expects (see worker/src/auth.ts `hashPassword`/`verifyPassword`):

    pbkdf2$<iterations>$<saltB64url>$<hashB64url>

Used by infra/add-user.sh. Implemented in Python's stdlib (hashlib, always
available alongside the rest of this repo's Python tooling) instead of shelling
out to `openssl kdf`, whose PBKDF2 CLI support varies across OpenSSL/LibreSSL
versions - this keeps the hash format identical and reproducible everywhere.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys

# Must match PBKDF2_ITERATIONS / HASH_BYTE_LENGTH in worker/src/auth.ts exactly.
# 100_000, NOT higher: Cloudflare Workers' actual production crypto.subtle
# enforces a HARD ceiling of 100_000 PBKDF2 iterations. A previous 210_000
# value passed every test (plain Node, wrangler dev) - neither enforces this
# limit - while every real login failed in the actual deployed Worker. See
# auth.ts's matching comment and SCRAPING_LESSONS.md before raising this.
ITERATIONS = 100_000
SALT_BYTES = 16
KEY_BYTES = 32


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=KEY_BYTES
    )
    return f"pbkdf2${ITERATIONS}${b64url(salt)}${b64url(derived)}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: hash_password.py <password>", file=sys.stderr)
        sys.exit(1)
    print(hash_password(sys.argv[1]))
