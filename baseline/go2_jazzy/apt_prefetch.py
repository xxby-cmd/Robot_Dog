#!/usr/bin/env python3
"""Fetch apt's own resolved package plan concurrently, checking its checksums."""
import concurrent.futures
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.request

plan = subprocess.check_output(['apt-get', '--print-uris', '--yes', '--no-install-recommends',
                                '--download-only', 'install', *sys.argv[1:]], text=True)
items = [shlex.split(line) for line in plan.splitlines() if line.startswith("'")]
cache = Path('/var/cache/apt/archives')

def fetch(item):
    url, name, size, checksum = item
    algorithm, expected = checksum.split(':', 1)
    algorithm = {'MD5Sum':'md5', 'SHA256':'sha256', 'SHA512':'sha512'}.get(algorithm, algorithm.lower())
    destination = cache / name
    if destination.exists() and hashlib.new(algorithm, destination.read_bytes()).hexdigest() == expected:
        return int(size)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as stream:
                data = stream.read()
            if len(data) != int(size) or hashlib.new(algorithm, data).hexdigest() != expected:
                raise ValueError('Package size or checksum mismatch: ' + name)
            temporary = destination.with_suffix('.prefetch')
            temporary.write_bytes(data)
            temporary.replace(destination)
            return len(data)
        except Exception:
            if attempt == 4:
                raise
            time.sleep(attempt+1)

print(f'APT_PREFETCH packages={len(items)} bytes={sum(int(item[2]) for item in items)}', flush=True)
downloaded = 0
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    for count, future in enumerate(concurrent.futures.as_completed([pool.submit(fetch,item) for item in items]), 1):
        downloaded += future.result()
        if count % 30 == 0 or count == len(items):
            print(f'APT_PREFETCH {count}/{len(items)} {downloaded/1024**2:.0f} MiB', flush=True)
