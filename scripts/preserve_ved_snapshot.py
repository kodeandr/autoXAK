"""Archive committed VED research without importing weights or changing the source."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


PREFIXES = ("research/ved_v1/", "app/model_artifacts/")
FILES = {
    "app/services/ved_research_estimate.py", "app/services/ved_speed_features.py",
    "docs/FUEL_MODEL.md", "docs/RESEARCH_ML_RATIONALE.md",
    "tests/test_research_fuel_v1.py", "requirements.txt",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(source, *args):
    return subprocess.run(["git", "-C", str(source), *args], check=True,
                          capture_output=True, timeout=30).stdout


def verify(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    archive = directory / "ved-source.zip"
    if sha256(archive.read_bytes()) != manifest["archive_sha256"]:
        raise ValueError("Archive checksum mismatch")
    with zipfile.ZipFile(archive) as source:
        if set(source.namelist()) != set(manifest["files"]):
            raise ValueError("Archive file list mismatch")
        for name, entry in manifest["files"].items():
            data = source.read(name)
            if sha256(data) != entry["sha256"] or len(data) != entry["bytes"]:
                raise ValueError("File checksum mismatch: " + name)
    return manifest


def create(source, ref, output):
    # Resolve the ref before creating any output; git show reads canonical blobs,
    # so CRLF checkout conversion cannot change the saved source hashes.
    commit = git(source, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    names = git(source, "ls-tree", "-r", "--name-only", "-z", commit).decode("utf-8").split("\0")
    names = sorted(name for name in names if name in FILES or name.startswith(PREFIXES))
    if not FILES.issubset(names) or "research/ved_v1/reports/ved_selected_model.joblib" not in names:
        raise ValueError("The source revision does not contain the required VED research")
    contents = {name: git(source, "show", commit + ":" + name) for name in names}
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "ved-source.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data)
    old = json.loads(contents["research/ved_v1/reports/research_manifest.json"])
    historical_checks = {}
    for relative, expected in old["sha256"].items():
        full = "research/ved_v1/" + relative
        if full not in contents:
            historical_checks[relative] = "not_archived"
        elif sha256(contents[full]) == expected:
            historical_checks[relative] = "matches"
        elif full.endswith(".json") and sha256(contents[full].replace(b"\n", b"\r\n")) == expected:
            historical_checks[relative] = "matches_with_crlf_line_endings"
        else:
            historical_checks[relative] = "differs"
    manifest = {
        "schema_version": 1, "source_revision": commit,
        "repository": "https://github.com/kodeandr/autoXAK",
        "archive_sha256": sha256(archive.read_bytes()),
        "files": {name: {"sha256": sha256(data), "bytes": len(data)} for name, data in contents.items()},
        "recorded_research_runtime": old["runtime"],
        "historical_manifest_checks": historical_checks,
        "scope": "committed_selected_files; raw datasets and untracked files remain in the original workspace",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verify(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        manifest = verify(args.verify)
    else:
        if not all((args.source, args.revision, args.output)):
            parser.error("--source, --revision and --output are required when creating")
        manifest = create(args.source, args.revision, args.output)
    print(json.dumps({"verified": True, "source_revision": manifest["source_revision"],
                      "file_count": len(manifest["files"]), "archive_sha256": manifest["archive_sha256"],
                      "historical_manifest_checks": manifest["historical_manifest_checks"]}, indent=2))


if __name__ == "__main__":
    main()
