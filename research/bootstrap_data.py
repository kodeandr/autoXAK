"""Download public VED files, verify Git blob hashes, extract CSVs safely."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent


def verify(path, entry):
    size = path.stat().st_size
    if size != entry["bytes"]:
        raise ValueError(f"Unexpected size: {path.name}: {size}")
    blob = hashlib.sha1(f"blob {size}\0".encode())
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            blob.update(block)
            sha256.update(block)
    if blob.hexdigest() != entry["git_blob_sha1"]:
        raise ValueError(f"Git blob hash mismatch: {path.name}")
    return sha256.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--extract", action="store_true")
    args = parser.parse_args()
    raw = ROOT / "data/raw"
    raw.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "data_manifest.json").read_text())
    verified = []
    for entry in manifest["files"]:
        path = raw / entry["local"]
        if not path.exists() and args.download:
            url = "https://github.com/gsoh/VED/raw/refs/heads/master/" + entry["remote"]
            request = urllib.request.Request(url, headers={"User-Agent": "AutoXAK-research/0.1"})
            temp = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(request, timeout=90) as response, temp.open("wb") as out:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    out.write(block)
            verify(temp, entry)
            temp.replace(path)
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}; run with --download or copy the original file.")
        digest = verify(path, entry)
        print(f"Verified {path.name}", flush=True)
        verified.append({**entry, "sha256": digest})
        if args.extract and path.suffix == ".7z":
            import py7zr
            destination = raw / "dynamic"
            destination.mkdir(exist_ok=True)
            with py7zr.SevenZipFile(path) as archive:
                infos = archive.list()
                for item in infos:
                    target = (destination / item.filename).resolve()
                    if not target.is_relative_to(destination.resolve()) or target.suffix != ".csv":
                        raise ValueError(f"Unexpected archive member: {item.filename}")
                needed = [item.filename for item in infos if not (destination / item.filename).exists()
                          or (destination / item.filename).stat().st_size != item.uncompressed]
                if needed:
                    print(f"Extracting {len(needed)} CSVs from {path.name}", flush=True)
                    archive.extract(path=destination, targets=needed)
    (raw / "verified_manifest.json").write_text(json.dumps(verified, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
