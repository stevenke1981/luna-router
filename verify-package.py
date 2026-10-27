#!/usr/bin/env python3
"""Check release manifest without network, third-party libs, or model calls."""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
manifest = root / "MANIFEST.sha256"
if not manifest.exists():
    sys.exit("MANIFEST.sha256 not found")
seen = set()
count = 0
for line in manifest.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    digest, sep, filename = line.partition("  ")
    target = root / filename
    if (not sep or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            or filename.startswith("/") or ".." in Path(filename).parts
            or filename in seen or not target.is_file()):
        sys.exit("INVALID: " + filename)
    seen.add(filename)
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        sys.exit("MISMATCH: " + filename)
    count += 1
print(f"PASS: verified {count} manifest entries")
