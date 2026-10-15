"""Keep old-season working data (including unsaved geography) and original tools."""
import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / "backups" / "armies-of-terra-before-steel-soil-20261002.zip"
if target.exists():
    raise SystemExit(f"Backup already exists, kept: {target}")
target.parent.mkdir(exist_ok=True)
files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
checksums = {}
with ZipFile(target, "x", ZIP_DEFLATED) as archive:
    for name in filter(None, files):
        if name == "scripts/local_map_server.py":
            payload = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT)
        else:
            payload = (ROOT / name).read_bytes()
        archive.writestr(name, payload)
        checksums[name] = hashlib.sha256(payload).hexdigest()
    archive.writestr("backup-manifest.json", json.dumps({
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "note": "Working geography preserved; local_map_server restored from HEAD in archive.",
        "sha256": checksums}, indent=2))
with ZipFile(target) as archive:
    if archive.testzip() is not None:
        raise SystemExit("Backup failed integrity test")
print(f"Verified backup: {target}; {len(checksums)} files; {target.stat().st_size} bytes")
