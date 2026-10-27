"""Download a verified artifact using resumable HTTP byte ranges."""
import argparse
import concurrent.futures
import hashlib
from pathlib import Path
import time
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("destination", type=Path)
parser.add_argument("--sha512")
parser.add_argument("--sha256")
args = parser.parse_args()
destination = args.destination
destination.parent.mkdir(parents=True, exist_ok=True)
algorithm = "sha512" if args.sha512 else "sha256"
expected = args.sha512 or args.sha256
if destination.exists() and expected:
    with destination.open("rb") as source:
        actual = hashlib.file_digest(source, algorithm).hexdigest()
    if actual.lower() == expected.lower():
        print("Already verified:", destination, flush=True)
        raise SystemExit(0)

with urllib.request.urlopen(urllib.request.Request(args.url, headers={"Range": "bytes=0-0"}), timeout=40) as response:
    if response.status != 206:
        raise RuntimeError("Server does not support HTTP range requests")
    size = int(response.headers["Content-Range"].rsplit("/", 1)[1])

parts = destination.parent / (destination.name + ".parts")
parts.mkdir(exist_ok=True)
chunk_size = 2 * 1024 * 1024
chunks = [(index, offset, min(size, offset + chunk_size) - 1) for index, offset in enumerate(range(0, size, chunk_size))]
started = time.monotonic()

def fetch(chunk):
    index, first, last = chunk
    target = parts / f"{index:05d}.part"
    count = last - first + 1
    if target.exists() and target.stat().st_size == count:
        return count
    for attempt in range(5):
        try:
            request = urllib.request.Request(args.url, headers={"Range": f"bytes={first}-{last}"})
            with urllib.request.urlopen(request, timeout=45) as response:
                if response.status != 206 or response.headers.get("Content-Range") != f"bytes {first}-{last}/{size}":
                    raise RuntimeError("Unexpected range response")
                data = response.read()
            if len(data) != count:
                raise RuntimeError("Incomplete download range")
            target.write_bytes(data)
            return count
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1 + attempt)

print(f"Downloading {size / 1024**2:.1f} MiB using eight connections", flush=True)
complete = 0
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    futures = [pool.submit(fetch, chunk) for chunk in chunks]
    for future in concurrent.futures.as_completed(futures):
        complete += future.result()
        if complete // (16 * 1024**2) != (complete - future.result()) // (16 * 1024**2) or complete == size:
            print(f"{complete / size:.0%}: {complete / 1024**2:.1f} MiB, {time.monotonic() - started:.0f}s", flush=True)

stage = destination.with_name(destination.name + ".assembling")
with stage.open("wb") as output:
    for index, _, _ in chunks:
        output.write((parts / f"{index:05d}.part").read_bytes())
with stage.open("rb") as source:
    actual = hashlib.file_digest(source, algorithm).hexdigest()
if expected and actual.lower() != expected.lower():
    raise RuntimeError(f"Checksum mismatch: {actual}")
stage.replace(destination)
print(f"Verified {algorithm}: {actual}", flush=True)
print("Saved:", destination, flush=True)
