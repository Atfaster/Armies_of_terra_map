"""Capture local changes, preserve their pixels and safely install a release."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from functools import reduce
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageChops
import season_background

TILE = re.compile(r"^(-?\d+),(-?\d+)\.png$", re.I)


@contextmanager
def job_lock(season):
    key = hashlib.sha256(str(season.resolve()).casefold().encode()).hexdigest()[:20]
    path = Path(tempfile.gettempdir()) / ("steel-soil-merge-" + key + ".lock")
    with path.open("a+b") as handle:
        if not path.stat().st_size:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another merge or client recovery is already running") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            # DriveFS briefly opens new JSON files without delete sharing.
            time.sleep(min(0.1 * (attempt + 1), 0.3))


def require_closed():
    if os.name != "nt":
        raise RuntimeError("Client installation currently supports Windows only")
    command = ("$ErrorActionPreference='Stop'; $p=@(Get-CimInstance Win32_Process "
               "-Filter \"Name = 'java.exe' OR Name = 'javaw.exe'\" | Where-Object { "
               "$_.CommandLine -match '(?i)(--gameDir|net\\.minecraft\\.client|forgewrapper|fabricmc\\.loader)' }); "
               "if ($p.Count -gt 0) { 'running' } else { 'closed' }")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, errors="replace")
    if result.returncode != 0 or result.stdout.strip() not in ("running", "closed"):
        raise RuntimeError("Cannot check Minecraft processes; client files were not replaced")
    if result.stdout.strip() == "running":
        raise RuntimeError("Close Minecraft before updating client tiles")


def capture(folder, size):
    if not folder.is_dir():
        raise ValueError(f"Client day folder missing: {folder}")
    records = {}
    for path in sorted(folder.glob("*.png")):
        if not TILE.fullmatch(path.name):
            continue
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("Client changed while reading; close Minecraft and retry")
        with Image.open(BytesIO(payload)) as image:
            image.load()
            if image.mode != "RGBA" or image.size != (size, size):
                raise ValueError(f"Invalid client tile: {path.name}")
        records[path.name] = {"payload": payload, "sha256": sha(payload),
                              "mtime_ns": after.st_mtime_ns, "size": after.st_size}
    if not records:
        raise ValueError("Client has no day tiles")
    return records


def signatures(records):
    return {name: (record["sha256"], record["mtime_ns"], record["size"])
            for name, record in records.items()}


def ensure_unchanged(plan):
    if signatures(capture(plan["folder"], plan["size"])) != signatures(plan["records"]):
        raise RuntimeError("Client tiles changed since capture; no further replacements; rerun with Minecraft closed")


def changed_pixels(current, baseline):
    difference = reduce(ImageChops.lighter, ImageChops.difference(current, baseline).split())
    mask = ImageChops.multiply(difference.point(lambda v: 255 if v else 0),
                              current.getchannel("A").point(lambda v: 255 if v else 0))
    delta = Image.new("RGBA", current.size)
    delta.paste(current, (0, 0), mask)
    return delta, mask.histogram()[255]


def prepare(season, config, grouped, temporary):
    settings_path = season / "client.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    source_id = settings["source_id"]
    if source_id not in config["source_order"]:
        raise ValueError("Client source is not in source_order")
    folder = Path(settings["day_folder"]).resolve()
    if folder.name != "day" or folder.parent.name != "overworld":
        raise ValueError("Client target must be overworld/day")
    size = config["tile_size"]
    records = capture(folder, size)
    state_path = season / "client_state.json"
    baseline = {}
    baseline_id = None
    backdrop = season_background.load(season, config)
    baseline_explored = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") != "complete":
            raise RuntimeError("Previous client installation incomplete; see client_state.json and its backup")
        if Path(state["day_folder"]).resolve() != folder:
            raise ValueError("Client folder differs from recorded installation")
        baseline_id = state["build_id"]
        if not re.fullmatch(r"[a-f0-9]{20}", baseline_id):
            raise ValueError("Invalid installed build id")
        site = season / "builds" / baseline_id / "site"
        has_background = (site / "background.json").exists()
        baseline_report = json.loads((site.parent / "report.json").read_text(encoding="utf-8")) if has_background else None
        for name, expected in state["installed_sha256"].items():
            if not TILE.fullmatch(name):
                raise ValueError("Invalid baseline tile name")
            path = site / "tiles" / name
            if sha(path.read_bytes()) != expected:
                raise ValueError("Installed baseline checksum mismatch")
            baseline[name] = path
            if has_background:
                real = site / "explored" / name
                if real.exists():
                    if sha(real.read_bytes()) != baseline_report["sha256"]["explored/" + name]:
                        raise ValueError("Installed exploration checksum mismatch")
                    baseline_explored[name] = real
            else:
                baseline_explored[name] = path
    else:
        # Initial installation: compare with retained personal originals, not
        # another player's tiles or the previously generated public release.
        priorities = {}
        for manifest in sorted((season / "sources" / source_id).glob("*/manifest.json")):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("kind") == "client-delta":
                continue
            for record in data["files"]:
                priority = (record["mtime_ns"], manifest.parent.name)
                if priority > priorities.get(record["name"], (0, "")):
                    priorities[record["name"]] = priority
                    baseline[record["name"]] = manifest.parent / "day" / record["name"]
                    baseline_explored[record["name"]] = baseline[record["name"]]
    delta_dir = temporary / "client-delta" / "day"
    delta_dir.mkdir(parents=True)
    deltas = []
    coverage_masks = {}
    for name, record in records.items():
        with Image.open(BytesIO(record["payload"])) as raw:
            current = raw.copy()
        if name in baseline:
            with Image.open(baseline[name]) as raw:
                previous = raw.convert("RGBA")
        else:
            previous = Image.new("RGBA", (size, size))
            if backdrop:
                current = backdrop.remove(current, name)
        delta, count = changed_pixels(current, previous)
        if backdrop:
            coverage = Image.new("L", (size, size))
            if name in baseline_explored:
                with Image.open(baseline_explored[name]) as real:
                    coverage = real.getchannel("A").point(lambda a: 255 if a else 0)
            coverage = ImageChops.lighter(coverage, delta.getchannel("A").point(lambda a: 255 if a else 0))
            if coverage.getbbox():
                mask_path = temporary / "client-coverage" / name
                mask_path.parent.mkdir(exist_ok=True)
                coverage.save(mask_path)
                coverage_masks[name] = mask_path
        if count:
            path = delta_dir / name
            delta.save(path)
            deltas.append({"name": name, "size": path.stat().st_size, "mtime_ns": record["mtime_ns"],
                           "sha256": sha(path.read_bytes()), "fresh_pixels": count})
    raw_key = sha(json.dumps({"baseline": baseline_id, "records": signatures(records)}, sort_keys=True).encode())
    maximum = max(p[0][0] for paths in grouped.values() for p in paths)
    priority = max(maximum + 1, max(r["mtime_ns"] for r in records.values()))
    for record in deltas:
        record["merge_priority_ns"] = priority
    # If a build failed before installation, reuse its identical retained delta.
    for manifest in sorted((season / "sources" / source_id).glob("*/manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("capture_key") == raw_key:
            deltas = data["files"]
            delta_dir = manifest.parent / "day"
            break
    plan = {"folder": folder, "size": size, "source_id": source_id, "records": records,
            "capture_key": raw_key, "deltas": deltas, "delta_dir": delta_dir,
            "baseline_id": baseline_id}
    if backdrop:
        plan["coverage_masks"] = coverage_masks
    ensure_unchanged(plan)
    return plan


def add_inputs(plan, grouped, sources, season, config, dry_run):
    if not plan["deltas"]:
        return
    key = plan["capture_key"][:20]
    target = season / "sources" / plan["source_id"] / key
    if not dry_run and not target.exists():
        staged = plan["delta_dir"].parent
        # May be a reused retained delta; only new stages are moved.
        json_write(staged / "manifest.json", {"source_id": plan["source_id"], "kind": "client-delta",
                   "original_path": str(plan["folder"]), "capture_key": plan["capture_key"],
                   "dimension": "overworld", "layer": "day", "files": plan["deltas"]})
        target.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(target)
        plan["delta_dir"] = target / "day"
    rank = config["source_order"].index(plan["source_id"])
    for record in plan["deltas"]:
        path = plan["delta_dir"] / record["name"]
        if not any(existing == path for _, existing in grouped.get(record["name"], [])):
            grouped.setdefault(record["name"], []).append(
                ((record["merge_priority_ns"], rank, key), path))
    if not any(s["snapshot"] == key and s["source_id"] == plan["source_id"] for s in sources):
        sources.append({"source_id": plan["source_id"], "snapshot": key,
                        "tiles": len(plan["deltas"]), "kind": "client-delta"})


def verify_output(plan, site):
    for name, record in plan["records"].items():
        if "coverage_masks" in plan:
            if name not in plan["coverage_masks"]:
                continue
            with Image.open(plan["coverage_masks"][name]) as raw:
                local_mask = raw.copy()
        else:
            with Image.open(BytesIO(record["payload"])) as raw:
                local_mask = raw.getchannel("A").point(lambda v: 255 if v else 0)
        if not local_mask.getbbox():
            continue
        with Image.open(site / "tiles" / name) as output:
            mask = output.getchannel("A").point(lambda v: 255 if v else 0)
            if ImageChops.subtract(local_mask, mask).getbbox():
                raise ValueError(f"Client coverage would be lost: {name}")
    for record in plan["deltas"]:
        with Image.open(plan["delta_dir"] / record["name"]) as delta, Image.open(site / "tiles" / record["name"]) as output:
            difference = reduce(ImageChops.lighter, ImageChops.difference(delta, output).split())
            mask = delta.getchannel("A").point(lambda v: 255 if v else 0)
            if ImageChops.multiply(difference, mask).getbbox():
                raise ValueError(f"Fresh client pixels would be replaced: {record['name']}")


def install(plan, site, season, report, closed_check=require_closed):
    closed_check()
    ensure_unchanged(plan)
    verify_output(plan, site)
    checksums = {Path(name).name: expected for name, expected in report["sha256"].items()
                 if name.startswith("tiles/")}
    for name, expected in checksums.items():
        if not TILE.fullmatch(name) or sha((site / "tiles" / name).read_bytes()) != expected:
            raise ValueError("Release tile checksum mismatch")
    state_path = season / "client_state.json"
    if state_path.exists():
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        if (prior.get("status") == "complete" and prior.get("build_id") == report["build_id"]
                and all(plan["records"].get(name, {}).get("sha256") == expected for name, expected in checksums.items())):
            return {"updated_tiles": 0, "client_tiles": len(plan["records"]), "backup": prior["backup"],
                    "fresh_tiles": 0, "fresh_pixels": 0}
    backup = season / "client_backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    (backup / "day").mkdir(parents=True)
    for name, record in plan["records"].items():
        path = backup / "day" / name
        path.write_bytes(record["payload"])
        os.utime(path, ns=(record["mtime_ns"], record["mtime_ns"]))
        if sha(path.read_bytes()) != record["sha256"]:
            raise ValueError("Client backup verification failed")
    json_write(backup / "manifest.json", {"day_folder": str(plan["folder"]), "files": {
        name: {k: v for k, v in record.items() if k != "payload"} for name, record in plan["records"].items()}})
    if state_path.exists():
        (backup / "previous_client_state.json").write_bytes(state_path.read_bytes())
    closed_check()
    ensure_unchanged(plan)
    journal = {"status": "pending", "build_id": report["build_id"], "day_folder": str(plan["folder"]),
               "backup": str(backup), "installed_sha256": checksums, "written": [],
               "recovery_origin_backup": str(plan.get("recovery_origin_backup", backup)),
               "fresh_snapshot": plan["capture_key"][:20] if plan["deltas"] else None}
    json_write(state_path, journal)
    try:
        for name, expected in checksums.items():
            target = plan["folder"] / name
            original = plan["records"].get(name)
            if original:
                if sha(target.read_bytes()) != original["sha256"] or target.stat().st_mtime_ns != original["mtime_ns"]:
                    raise RuntimeError(f"Client changed before replacing {name}")
            elif target.exists():
                raise RuntimeError(f"New client tile appeared: {name}")
            if original and original["sha256"] == expected:
                continue
            temporary = target.with_name(name + ".map-update-" + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes((site / "tiles" / name).read_bytes())
                if sha(temporary.read_bytes()) != expected:
                    raise ValueError("Staged client tile checksum mismatch")
                if original:
                    os.utime(temporary, ns=(original["mtime_ns"], original["mtime_ns"]))
                    if sha(target.read_bytes()) != original["sha256"] or target.stat().st_mtime_ns != original["mtime_ns"]:
                        raise RuntimeError(f"Client changed during staging {name}")
                elif target.exists():
                    raise RuntimeError(f"Client tile appeared during staging {name}")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            journal["written"].append(name)
            # Pending journal + backup + expected hashes allow recovery without
            # repeatedly rewriting a small synced file for every PNG.
        closed_check()
        actual = capture(plan["folder"], plan["size"])
        for name, expected in checksums.items():
            if actual[name]["sha256"] != expected:
                raise RuntimeError("Client changed during installation; backup retained")
        if set(actual) != set(plan["records"]) | set(checksums):
            raise RuntimeError("Client file set changed during installation")
        journal["status"] = "complete"
        json_write(state_path, journal)
    except Exception:
        journal["status"] = "interrupted"
        json_write(state_path, journal)
        raise
    return {"updated_tiles": len(journal["written"]), "client_tiles": len(actual), "backup": str(backup),
            "fresh_tiles": len(plan["deltas"]), "fresh_pixels": sum(r["fresh_pixels"] for r in plan["deltas"])}


def recover(season, config, closed_check=require_closed):
    closed_check()
    state = json.loads((season / "client_state.json").read_text(encoding="utf-8"))
    if state.get("status") not in ("pending", "interrupted"):
        raise ValueError("No interrupted installation to recover")
    settings = json.loads((season / "client.json").read_text(encoding="utf-8"))
    folder = Path(settings["day_folder"]).resolve()
    if folder != Path(state["day_folder"]).resolve():
        raise ValueError("Recovery target differs from configured client")
    backup = Path(state["backup"]).resolve()
    if not backup.is_relative_to((season / "client_backups").resolve()):
        raise ValueError("Backup escaped season")
    backup = Path(state.get("recovery_origin_backup", str(backup))).resolve()
    if not backup.is_relative_to((season / "client_backups").resolve()):
        raise ValueError("Original recovery backup escaped season")
    original_manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    originals = {}
    for name, record in original_manifest["files"].items():
        if not TILE.fullmatch(name):
            raise ValueError("Invalid backup filename")
        payload = (backup / "day" / name).read_bytes()
        if sha(payload) != record["sha256"]:
            raise ValueError("Original client backup checksum mismatch")
        originals[name] = {**record, "payload": payload}
    build_id = state["build_id"]
    if not re.fullmatch(r"[a-f0-9]{20}", build_id):
        raise ValueError("Invalid recovery build id")
    release = season / "builds" / build_id
    report = json.loads((release / "report.json").read_text(encoding="utf-8"))
    checksums = {Path(name).name: expected for name, expected in report["sha256"].items() if name.startswith("tiles/")}
    if checksums != state["installed_sha256"]:
        raise ValueError("Recovery release differs from pending journal")
    records = capture(folder, config["tile_size"])
    if not set(originals).issubset(records) or not set(records).issubset(set(originals) | set(checksums)):
        raise RuntimeError("Client file set changed after interruption; recovery refused")
    for name, record in records.items():
        allowed = {checksums.get(name), originals.get(name, {}).get("sha256")}
        if record["sha256"] not in allowed:
            raise RuntimeError(f"Fresh client changes after interruption: {name}; recovery refused")
    baseline_id = None
    previous_path = backup / "previous_client_state.json"
    if previous_path.exists():
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        baseline_id = previous.get("build_id") if previous.get("status") == "complete" else None
    capture_key = sha(json.dumps({"baseline": baseline_id, "records": signatures(originals)}, sort_keys=True).encode())
    delta_root = season / "sources" / settings["source_id"] / (state.get("fresh_snapshot") or capture_key[:20])
    deltas = []
    if (delta_root / "manifest.json").exists():
        data = json.loads((delta_root / "manifest.json").read_text(encoding="utf-8"))
        if data.get("capture_key") != capture_key:
            raise ValueError("Recovery fresh snapshot does not match original client backup")
        deltas = data["files"]
        for record in deltas:
            if not TILE.fullmatch(record["name"]) or sha((delta_root / "day" / record["name"]).read_bytes()) != record["sha256"]:
                raise ValueError("Recovery fresh delta checksum mismatch")
    elif state.get("fresh_snapshot"):
        raise ValueError("Fresh recovery snapshot missing")
    plan = {"folder": folder, "size": config["tile_size"], "source_id": settings["source_id"],
            "records": records, "deltas": deltas, "delta_dir": delta_root / "day", "capture_key": capture_key,
            "recovery_origin_backup": backup}
    result = install(plan, release / "site", season, report, closed_check=closed_check)
    print(json.dumps({"recovered_build": build_id, **result}, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["recover"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "seasons" / "steel-soil"
    with job_lock(root):
        recover(root, json.loads((root / "config.json").read_text(encoding="utf-8")))
