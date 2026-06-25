#!/usr/bin/env python3
"""Generate VAPID key pair for Web Push. Run once, add output to .env on the VPS."""

import base64

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid

v = Vapid()
v.generate_keys()
pub_b64 = (
    base64.urlsafe_b64encode(
        v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    .rstrip(b"=")
    .decode()
)
print(f"VAPID_PUBLIC_KEY={pub_b64}")
print("# Private key written to vapid_private.pem")
print("# Set VAPID_PRIVATE_KEY=/app/data/vapid_private.pem in .env")
