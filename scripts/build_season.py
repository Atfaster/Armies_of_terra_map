"""Snapshot original day tiles and build isolated JourneyMap / static-site releases.

Retained sources, previous releases and old-season data are preserved.
By default, fresh local changes are captured and the configured client is updated.
Pixel priority: original file mtime_ns, configured source rank, snapshot digest.
Rebuilding all retained snapshots preserves the per-pixel provenance without SQLite.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image
import season_client
import season_background
import season_data
import season_archive
from season_log import run_stage, success, error, enable_console

ROOT = Path(__file__).resolve().parents[1]
TILE = re.compile(r"^(-?\d+),(-?\d+)\.png$", re.I)
DOWNLOAD_NAME = "Steel-Soil-JourneyMap-latest.zip"
DIRECT_ZIP_LIMIT = 95_000_000


def installation_text(key, tile_count):
    return ("TerraFirmaColonies: Steel & Soil — JourneyMap 1.20.1-5.10.3-forge\n"
            "1. Закройте Minecraft. Сначала отправьте свои новые исследования в общую сборку.\n"
            "2. Скопируйте папку сервера в другое место — это резервная копия.\n"
            "3. Скопируйте overworld и addon-data из ZIP в папку сервера своей игры\n"
            "   внутри journeymap/data/mp с заменой совпадающих файлов.\n"
            "   У atfaster сервер называется Steel~Soil; у вас имя может отличаться.\n"
            "   Если addon-data в ZIP нет, копируйте только overworld.\n"
            "4. Остальные файлы оставьте на месте. Запустите Minecraft.\n"
            "В архиве дневная карта, доступные сведения о блоках и известные границы колоний.\n"
            "Личные метки, настройки, ночь и пещеры в архив не входят.\n"
            "Подложка BlueMap — ориентир; для неё нет вымышленных сведений о блоках.\n"
            "Для передачи своей карты: закройте игру, выделите overworld и addon-data\n"
            "в папке сервера, сожмите в ZIP, назовите своим ником и отправьте сборщику карты.\n"
            f"Сборка: {key}; тайлов: {tile_count}.\n")


def package_download(staged, site, entries, key, instructions):
    """Derived download assets do not participate in the release ID (no cycle).

    Fixed ZIP timestamps make identical releases byte-identical on rebuild.
    All derived assets still appear in report.sha256 and are verified on publish.
    """
    archive_path = staged / f"steel-soil-journeymap-{key}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        members = [("INSTALL.txt", instructions.encode("utf-8-sig"))]
        members.extend(("overworld/day/" + Path(tile["file"]).name,
                        (site / tile["file"]).read_bytes()) for tile in entries)
        members.extend((p.relative_to(staged / "team-data").as_posix(), p.read_bytes())
                       for p in sorted((staged / "team-data").rglob("*")) if p.is_file())
        for name, content in members:
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    downloads = site / "downloads"
    downloads.mkdir()
    release_asset = archive_path.stat().st_size >= DIRECT_ZIP_LIMIT
    if not release_asset:
        shutil.copyfile(archive_path, downloads / DOWNLOAD_NAME)
    metadata = {
        "build_id": key, "tiles": len(entries), "file": DOWNLOAD_NAME,
        "bytes": archive_path.stat().st_size, "sha256": digest(archive_path)}
    if release_asset:
        metadata["storage"] = "github-release"
        metadata["release_tag"] = "map-" + key
        metadata["url"] = "https://github.com/Atfaster/terrafirmacolonies-map/releases/download/" + metadata["release_tag"] + "/" + DOWNLOAD_NAME
    if (site / "background.json").exists():
        metadata["background"] = True
        exploration = json.loads((site / "exploration.json").read_text(encoding="utf-8"))
        metadata["explored_tiles"] = exploration["tiles"]
        metadata["explored_blocks"] = exploration["blocks"]
    write_json(downloads / "latest.json", metadata)
    return archive_path


def deliver_after_build(season, report, dry_run=False):
    """Copy a complete verified ZIP to the configured Drive desktop folder.

    This confirms the local copy, not completion of Google Drive cloud sync.
    """
    settings = season / "delivery.json"
    if dry_run or not settings.exists():
        return False
    folder = Path(json.loads(settings.read_text(encoding="utf-8"))["folder"]).resolve()
    release = season / "builds" / report["build_id"]
    metadata = json.loads((release / "site/downloads/latest.json").read_text(encoding="utf-8"))
    source = release / ("steel-soil-journeymap-" + report["build_id"] + ".zip")
    expected = metadata["sha256"]
    if metadata["build_id"] != report["build_id"] or source.stat().st_size != metadata["bytes"] or digest(source) != expected:
        raise ValueError("Delivery source checksum or build mismatch")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / DOWNLOAD_NAME
    if not target.exists() or digest(target) != expected:
        # Stage outside DriveFS, then atomically replace the complete ZIP.
        with workspace_stage(folder) as temp:
            staged = Path(temp) / DOWNLOAD_NAME
            shutil.copyfile(source, staged)
            if digest(staged) != expected:
                raise ValueError("Team folder copy checksum mismatch")
            os.replace(staged, target)
    if digest(target) != expected:
        raise ValueError("Team folder final checksum mismatch")
    season_client.json_write(season / "delivery_state.json", {
        "build_id": report["build_id"], "folder": str(folder),
        "file": DOWNLOAD_NAME, "sha256": expected, "bytes": metadata["bytes"],
        "copied_at": datetime.now(timezone.utc).isoformat(), "status": "local-copy-verified"})
    print(f"Team ZIP copied and verified: {target}")
    print("Wait for Google Drive synchronization before announcing the update.")
    return True


def publish_after_build(dry_run, no_publish, season_id):
    if dry_run or no_publish:
        return
    if season_id != "steel-soil":
        raise ValueError("Automatic publication is configured only for steel-soil; use --no-publish")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", str(ROOT / "scripts" / "publish_season.ps1")])
    if result.returncode:
        raise RuntimeError("Local merge and ZIP are ready, but publication failed. "
                           "Retry: powershell -ExecutionPolicy Bypass -File scripts/publish_season.ps1")


@contextmanager
def workspace_stage(parent):
    # Python 3.14 TemporaryDirectory applies private Windows ACLs. A release
    # moved out of it would remain unreadable to the desktop user's processes.
    # Keep temporary trees outside DriveFS: it adds protected desktop.ini files
    # and read-only directory flags while we are composing a release.
    temp_parent = Path(tempfile.gettempdir()).resolve()
    if temp_parent.drive.lower() != parent.resolve().drive.lower():
        raise ValueError("TEMP and season must be on the same drive for atomic activation")
    stage = temp_parent / ("steel-soil-stage-" + uuid.uuid4().hex)
    stage.mkdir()
    try:
        yield stage
    finally:
        if stage.exists():
            if stage.resolve().parent != temp_parent:
                raise ValueError("Staging directory escaped its intended parent")
            def clear_readonly(function, path, error):
                resolved = Path(path).resolve()
                if not resolved.is_relative_to(stage.resolve()):
                    raise ValueError("Cleanup path escaped staging directory")
                if not isinstance(error, PermissionError):
                    raise error
                os.chmod(path, stat.S_IWRITE)
                function(path)
            shutil.rmtree(stage, onexc=clear_readonly)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot(season, source_id, source, config, original_archive=None, backdrop=None, extra_world=None, inspection=None):
    if source_id not in config["source_order"]:
        raise ValueError("Source must be listed in config.source_order")
    backdrop = backdrop or season_background.load(season, config)
    records = []
    # Only direct children: never flatten caves, night or another dimension.
    for path in sorted(source.glob("*.png")):
        if not TILE.fullmatch(path.name):
            continue
        with Image.open(path) as image:
            image.load()
            if image.size != (config["tile_size"],) * 2 or image.mode != "RGBA":
                raise ValueError(f"Expected RGBA {config['tile_size']}px tile: {path}")
        stat = path.stat()
        records.append({"name": path.name, "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns, "sha256": digest(path)})
    if not records:
        raise ValueError(f"No day tiles in {source}")
    extras_identity = season_data.capture(extra_world) if extra_world else {}
    identity = {"import_format": 3, "extras": {n:r["sha256"] for n,r in extras_identity.items()}, "files": records, "background_removed": backdrop.identity} if backdrop else {"import_format": 3, "files": records, "extras": {n:r["sha256"] for n,r in extras_identity.items()}}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    target = season / "sources" / source_id / key
    if target.exists():
        print(f"Snapshot already retained: {source_id}/{key}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with workspace_stage(target.parent) as temp:
        staged = Path(temp) / "snapshot"
        (staged / "day").mkdir(parents=True)
        for record in records:
            copied = staged / "day" / record["name"]
            shutil.copy2(source / record["name"], copied)
            if digest(copied) != record["sha256"]:
                raise ValueError("Source changed while copying; close Minecraft and retry")
            if backdrop:
                originals = staged / "original-day"
                originals.mkdir(exist_ok=True)
                shutil.copy2(copied, originals / record["name"])
                record["original_sha256"] = record["sha256"]
                with Image.open(copied) as raw:
                    filtered = backdrop.remove(raw, record["name"])
                filtered.save(copied)
                record["sha256"] = digest(copied)
                record["size"] = copied.stat().st_size
        archive_issues = []
        extras = season_data.retain_extras(staged, extra_world, issues=archive_issues) if extra_world else []
        if original_archive:
            shutil.copy2(original_archive, staged / "source.zip")
        write_json(staged / "manifest.json", {"source_id": source_id,
                   "original_path": str((original_archive or source).resolve()),
                   "archive_sha256": digest(original_archive) if original_archive else None,
                   "background_removed": backdrop.identity if backdrop else None,
                   "dimension": config["dimension"],
                   "layer": config["layer"], "files": records, "extras": extras, "import_format": 3,
                   "salvaged_chunks": archive_issues})
        if inspection:
            inspection = {**inspection, "salvaged_chunks": archive_issues}
            write_json(staged / "import-report.json", inspection)
        staged.rename(target)
    print(f"Retained {len(records)} original tiles: {source_id}/{key}")


def snapshot_zip(season, source_id, archive_path, config, backdrop=None):
    # Only explicit surface paths; caves and other layers in whole-overworld ZIPs
    # are retained in the original archive but never mistaken for day tiles.
    with ZipFile(archive_path) as archive, tempfile.TemporaryDirectory() as temp:
        world = Path(temp)
        selected, inspection = season_archive.inspect(archive)
        print("Archive inspection: " + json.dumps({k:v for k,v in inspection.items() if k != "selected_paths"}, ensure_ascii=False))
        for info, original_name, name in selected:
            path = world / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(info))
            stamp = datetime(*info.date_time).timestamp()
            os.utime(path, (stamp, stamp))
        snapshot(season, source_id, world / "overworld/day", config,
                 original_archive=archive_path, backdrop=backdrop, extra_world=world, inspection=inspection)


def import_inbox(season, config, stage, dry_run=False):
    """Import original ZIPs from the season inbox; retained archive hashes dedupe them."""
    inbox = ROOT / "imports" / config["id"]
    known = {json.loads(p.read_text(encoding="utf-8")).get("archive_sha256")
             for p in (season / "sources").glob("*/*/manifest.json")}
    effective = {**config, "source_order": list(config["source_order"])}
    destination = stage / "inbox-season" if dry_run else season
    imported = False
    backdrop = season_background.load(season, config)
    for archive in sorted(inbox.glob("*.zip")):
        sha = digest(archive)
        if sha in known:
            manifests = [json.loads(p.read_text(encoding="utf-8")) for p in (season / "sources").glob("*/*/manifest.json")]
            parsed = any(m.get("archive_sha256") == sha and m.get("import_format", 1) >= 3 for m in manifests)
            with ZipFile(archive) as original:
                additional = any(i.filename.replace("\\", "/").endswith("border.json") or i.filename.lower().endswith(".mca") for i in original.infolist())
            if additional and not parsed:
                print(f"Reimporting additional data from previously imported ZIP: {archive.name}")
            else:
                print(f"Already imported: {archive.name}")
                continue
        with ZipFile(archive) as contents:
            if any(Path(i.filename).name.upper() == "INSTALL.TXT" for i in contents.infolist()):
                raise ValueError(f"{archive.name}: merged distribution ZIP is not an original player map")
        slug = re.sub(r"[^a-z0-9-]+", "-", archive.stem.lower()).strip("-")
        source_id = "zip-" + (slug or hashlib.sha256(archive.stem.encode()).hexdigest()[:12])
        if source_id not in effective["source_order"]:
            effective["source_order"].append(source_id)
        snapshot_zip(destination, source_id, archive, effective, backdrop=backdrop)
        if digest(archive) != sha:
            raise ValueError(f"Archive changed during import: {archive.name}")
        if not dry_run:
            season_client.json_write(season / "config.json", effective)
        known.add(sha)
        imported = True
        print(f"{'Would import' if dry_run else 'Imported'} inbox ZIP: {archive.name} as {source_id}")
    if not dry_run:
        return collect(season, effective)
    return collect(destination, effective) if imported else ({}, [])


def collect(season, config):
    grouped, sources = {}, []
    for rank, source_id in enumerate(config["source_order"]):
        for manifest in sorted((season / "sources" / source_id).glob("*/manifest.json")):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            sources.append({"source_id": source_id, "snapshot": manifest.parent.name,
                            "tiles": len(data["files"])})
            for record in data["files"]:
                path = manifest.parent / "day" / record["name"]
                if digest(path) != record["sha256"]:
                    raise ValueError(f"Snapshot checksum mismatch: {path}")
                priority = (record.get("merge_priority_ns", record["mtime_ns"]), rank, manifest.parent.name)
                grouped.setdefault(record["name"], []).append((priority, path))
    if not grouped:
        raise ValueError("No snapshots. Use snapshot before build.")
    return grouped, sources


def move_processed_archives(season, report):
    """Move only ZIP bytes included in this completed, published release."""
    inbox = (ROOT / "imports" / report["season"]).resolve()
    processed = inbox / "processed"
    known = set()
    for source in report["sources"]:
        manifest = season / "sources" / source["source_id"] / source["snapshot"] / "manifest.json"
        sha = json.loads(manifest.read_text(encoding="utf-8")).get("archive_sha256")
        if sha:
            known.add(sha)
    moved = 0
    for archive in sorted(inbox.glob("*.zip")):
        if archive.is_symlink() or archive.resolve().parent != inbox:
            raise ValueError("Import archive escaped the inbox")
        expected = digest(archive)
        if expected not in known:
            print(f"Left in inbox (not included in published build): {archive.name}")
            continue
        processed.mkdir(exist_ok=True)
        if processed.resolve().parent != inbox or processed.is_symlink():
            raise ValueError("Processed directory escaped the inbox")
        destination = processed / archive.name
        suffix = 0
        while destination.exists():
            suffix += 1
            extra = "" if suffix == 1 else f"-{suffix}"
            destination = processed / f"{archive.stem}-{expected[:12]}{extra}{archive.suffix}"
        # Same-filesystem rename; Windows refuses to overwrite an existing file.
        archive.rename(destination)
        if digest(destination) != expected:
            if not archive.exists():
                destination.rename(archive)
            raise RuntimeError("Archive changed during transfer; original bytes retained")
        moved += 1
        print(f"Processed archive: {destination}")
    print(f"Moved {moved} processed ZIP(s); retained source snapshots are unchanged")


def merge_images(paths, size):
    result = Image.new("RGBA", (size, size))
    for _, path in sorted(paths):
        with Image.open(path) as raw:
            raw.load()
            if raw.size != (size, size) or raw.mode != "RGBA":
                raise ValueError(f"Invalid tile: {path}")
            # Binary mask matches the old merge: any non-zero alpha is explored.
            result.paste(raw, (0, 0), raw.getchannel("A").point(lambda a: 255 if a else 0))
    return result


def build_site(staged, grouped, config, season):
    site = staged / "site"
    (site / "tiles").mkdir(parents=True)
    (site / "previews").mkdir()
    shutil.copytree(season / "data", site / "data", ignore=shutil.ignore_patterns("desktop.ini"))
    shutil.copytree(season / "vendor", site / "vendor", ignore=shutil.ignore_patterns("desktop.ini"))
    size = config["tile_size"]
    backdrop = season_background.load(season, config)
    if backdrop:
        (site / "explored").mkdir()
        write_json(site / "background.json", backdrop.metadata())
    entries = []
    names = sorted(set(grouped) | (set(backdrop.names()) if backdrop else set()))
    print(f"Building {len(names)} map tiles...", flush=True)
    explored_pixels, explored_tiles = 0, 0
    for tile_number, name in enumerate(names, 1):
        image = merge_images(grouped.get(name, []), size)
        alpha = image.getchannel("A").point(lambda a: 255 if a else 0)
        if alpha.getbbox():
            explored_tiles += 1
            explored_pixels += alpha.histogram()[255]
            if backdrop:
                image.save(site / "explored" / name)
        if backdrop:
            composite = backdrop.tile(name)
            composite.paste(image, (0, 0), alpha)
            image = composite
        if not image.getchannel("A").getbbox():
            continue
        image.save(site / "tiles" / name)
        x, z = map(int, TILE.fullmatch(name).groups())
        entries.append({"file": "tiles/" + name, "x": x, "y": z})
        if tile_number % 250 == 0:
            print(f"Prepared {tile_number}/{len(names)} tiles", flush=True)
    entries.sort(key=lambda t: (t["y"], t["x"]))
    if not entries:
        raise ValueError("No explored pixels")
    write_json(site / "tiles.json", entries)
    write_json(site / "exploration.json", {"tiles": explored_tiles, "blocks": explored_pixels,
               "background_counts_as_exploration": False})
    minx, maxx = min(t["x"] for t in entries), max(t["x"] for t in entries)
    minz, maxz = min(t["y"] for t in entries), max(t["y"] for t in entries)
    width, height = (maxx - minx + 1) * size, (maxz - minz + 1) * size
    # Build each overview directly; never allocate a full-world RGBA canvas.
    for factor in (4, 8, 16):
        print(f"Building overview at 1/{factor} scale...", flush=True)
        preview = Image.new("RGB", (width // factor, height // factor), (221, 221, 221))
        for tile in entries:
            with Image.open(site / tile["file"]) as raw:
                reduced = raw.resize((size // factor,) * 2, Image.Resampling.LANCZOS)
                preview.paste(reduced, ((tile["x"] - minx) * size // factor,
                              (tile["y"] - minz) * size // factor), reduced.getchannel("A"))
        preview.save(site / "previews" / f"overview_{factor}x.jpg", quality=85, optimize=True)
    write_json(site / "previews" / "overview_meta.json", {
        "tile_size": size, "min_tx": minx, "max_tx": maxx, "min_ty": minz, "max_ty": maxz,
        "min_x": minx * size, "max_x": (maxx + 1) * size,
        "min_y": minz * size, "max_y": (maxz + 1) * size,
        "full_width": width, "full_height": height})
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    for url, filename in {
        "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css": "leaflet.css",
        "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js": "leaflet.js",
        "https://unpkg.com/@geoman-io/leaflet-geoman-free@2.18.3/dist/leaflet-geoman.css": "leaflet-geoman.css",
        "https://unpkg.com/@geoman-io/leaflet-geoman-free@2.18.3/dist/leaflet-geoman.min.js": "leaflet-geoman.min.js",
    }.items():
        page = page.replace(url, "vendor/" + filename)
    page = page.replace("Armies Of Terra World Map", html.escape(config["title"]) + " World Map")
    page = page.replace("const EDITOR_IS_LOCAL = isLocalEnvironment();",
                        "const EDITOR_IS_LOCAL = isLocalEnvironment() && new URLSearchParams(location.search).get('view') !== 'public';")
    page = re.sub(r'const DEFAULT_TIMELINE_DATE = "[^\"]+";',
                  "const DEFAULT_TIMELINE_DATE = " + json.dumps(config["initial_snapshot_date"]) + ";", page)
    page = re.sub(r'const SERVER_OPEN_DATE = "[^"]+";',
                  "const SERVER_OPEN_DATE = " + json.dumps(config["server_open_date"]) + ";", page)
    page = page.replace("dates.add(SERVER_OPEN_DATE);", "if (SERVER_OPEN_DATE) dates.add(SERVER_OPEN_DATE);")
    if config["world_border"] is None:
        page = page.replace("worldBorder: L.layerGroup().addTo(map)", "worldBorder: L.layerGroup()")
        page = page.replace("      renderWorldBorder();", "      // New season border is not confirmed.")
        page = page.replace("      mapBounds.extend(getWorldBorderBounds());", "")
        page = page.replace('      "World border": layers.worldBorder', '')
    else:
        for key, value in config["world_border"].items():
            if key in ("minX", "maxX", "minY", "maxY"):
                page = re.sub(rf'{key}: -?\d+', f'{key}: {int(value)}', page, count=1)
    city_data = json.loads((season / "data" / "cities.json").read_text(encoding="utf-8"))
    css = "<style>\n"
    if not city_data.get("cities"):
        css += ".filter-panel,.timeline-panel{display:none}\n"
    css += ("@media(max-width:720px){.status-box{max-width:260px;font-size:11px}"
            ".geography-panel{left:56px!important;right:12px!important;top:140px!important;"
            "max-height:calc(100vh - 180px);overflow:auto}.timeline-panel{box-sizing:border-box}}\n</style>")
    page = page.replace("</head>", css + "\n</head>")
    page = page.replace("<body>", '<body>\n<a class="team-download" href="download.html">Скачать карту для JourneyMap</a>')
    page = page.replace("</head>", '<style>.team-download{position:fixed;bottom:52px;left:12px;z-index:1100;'
                        'background:#17212b;color:#fff;padding:10px 14px;border-radius:6px;'
                        'font:14px Arial,sans-serif;text-decoration:none;box-shadow:0 2px 8px #0005}'
                        '.team-download:hover{background:#2b485e}</style>\n</head>')
    page = page.replace("        configureNationEditorPanels();", "        configureNationEditorPanels();\n"
                        "        if (matchMedia('(max-width:720px)').matches) {\n"
                        "          document.getElementById('toggle-geography-editor').click();\n"
                        "        }")
    page = page.replace("Math.round(latlng.lng)", "Math.floor(latlng.lng)").replace("Math.round(-latlng.lat)", "Math.floor(-latlng.lat)")
    page = page.replace("</body>", '<script src="team-data.js"></script></body>')
    shutil.copyfile(season / "team-data.js" if (season / "team-data.js").exists() else ROOT / "seasons/steel-soil/team-data.js", site / "team-data.js")
    (site / "index.html").write_text(page, encoding="utf-8")
    shutil.copyfile(season / "download.html", site / "download.html")
    (site / ".nojekyll").touch()
    if backdrop:
        page = (site / "index.html").read_text(encoding="utf-8")
        page = page.replace('Скачать карту для JourneyMap', 'Скачать карту с подложкой')
        (site / "index.html").write_text(page, encoding="utf-8")
    write_json(site / "season.json", {k: v for k, v in config.items() if k not in ("source_order", "background")})
    return entries, site


def build(season, config, dry_run=False, skip_client=False, import_zips=False):
    if not skip_client and not dry_run:
        season_client.require_closed()
    grouped, sources = collect(season, config)
    season.joinpath("builds").mkdir(exist_ok=True)
    with workspace_stage(season / "builds") as temp:
        if import_zips:
            inbox_grouped, inbox_sources = run_stage("Import ZIPs", import_inbox, season, config, Path(temp), dry_run)
            if dry_run:
                for name, paths in inbox_grouped.items():
                    grouped.setdefault(name, []).extend(paths)
                sources.extend(inbox_sources)
            else:
                grouped, sources = inbox_grouped, inbox_sources
        extra_plan = None
        client_plan = None
        config = {**config, "source_order": list(dict.fromkeys(config["source_order"] + [r["source_id"] for r in sources]))}
        if not skip_client:
            client_plan = run_stage("Capture client tiles", season_client.prepare, season, config, grouped, Path(temp))
            season_client.add_inputs(client_plan, grouped, sources, season, config, dry_run)
        if not skip_client:
            extra_plan = run_stage("Capture client surface data", season_data.prepare_client, season, config, Path(temp), dry_run, client_plan["records"])
        if extra_plan and not dry_run:
            grouped, sources = collect(season, config)
        staged = Path(temp) / "release"
        staged.mkdir()
        entries, site = run_stage("Build map tiles and overviews", build_site, staged, grouped, config, season)
        extra_report = run_stage("Merge surface data and colonies", season_data.build, season, config, staged, site, [p for p in ((extra_plan.get("extra_season") if extra_plan else None), Path(temp) / "inbox-season" if dry_run and import_zips else None) if p is not None])
        if extra_plan:
            season_data.ensure_unchanged(extra_plan)
        if client_plan:
            season_client.verify_output(client_plan, site)
        checksums = {str(p.relative_to(site)).replace("\\", "/"): digest(p)
                     for p in sorted(site.rglob("*")) if p.is_file()}
        key = hashlib.sha256(json.dumps({"site": checksums,
            "installation": installation_text("", len(entries)), "team_data": {p.relative_to(staged / "team-data").as_posix(): digest(p) for p in sorted((staged / "team-data").rglob("*")) if p.is_file()}, "zip_format": "fixed-timestamps-v2"},
            sort_keys=True).encode()).hexdigest()[:20]
        report = {"build_id": key, "season": config["id"], "sources": sources,
                  "unique_input_tiles": len(grouped), "output_tiles": len(entries),
                  "overlapping_tiles": sum(len(v) > 1 for v in grouped.values()),
                  "priority": "merge_priority_ns for fresh client deltas, otherwise mtime_ns; source rank, snapshot digest; nonzero alpha replaces",
                  "site_bytes": sum(p.stat().st_size for p in site.rglob("*") if p.is_file()),
                  "sha256": checksums}
        report["exploration"] = json.loads((site / "exploration.json").read_text(encoding="utf-8"))
        if config.get("background"):
            report["background"] = json.loads((site / "background.json").read_text(encoding="utf-8"))
        if extra_plan and extra_plan.get("salvaged_chunks"):
            extra_report["salvaged_chunks"] = extra_plan["salvaged_chunks"]
        report["shared_data"] = extra_report
        report["team_data_sha256"] = {p.relative_to(staged / "team-data").as_posix(): digest(p)
                                      for p in sorted((staged / "team-data").rglob("*")) if p.is_file()}
        instructions = installation_text(key, len(entries))
        (staged / "INSTALL.txt").write_text(instructions, encoding="utf-8-sig")
        zip_path = run_stage("Package team ZIP", package_download, staged, site, entries, key, instructions)
        checksums.update({str(p.relative_to(site)).replace("\\", "/"): digest(p)
                          for p in sorted((site / "downloads").iterdir())})
        report["site_bytes"] = sum(p.stat().st_size for p in site.rglob("*") if p.is_file())
        if report["site_bytes"] >= 1_000_000_000:
            raise ValueError("Site exceeds GitHub Pages 1 GB limit")
        if zip_path.stat().st_size >= 2_000_000_000:
            raise ValueError("Team ZIP exceeds the release asset limit")
        write_json(staged / "report.json", report)
        # Verify every ZIP tile against the website release before activating.
        zip_path = next(staged.glob("*.zip"))
        with ZipFile(zip_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP integrity failure")
            for name, expected in report["team_data_sha256"].items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise ValueError("ZIP/metadata checksum mismatch")
            for tile in entries:
                if hashlib.sha256(archive.read("overworld/day/" + Path(tile["file"]).name)).hexdigest() != checksums[tile["file"]]:
                    raise ValueError("ZIP/site checksum mismatch")
        success("Team ZIP integrity and checksums verified")
        if dry_run:
            if client_plan:
                report["client_check"] = {"fresh_tiles": len(client_plan["deltas"]),
                    "fresh_pixels": sum(r["fresh_pixels"] for r in client_plan["deltas"]),
                    "coverage_and_fresh_pixels": "PASS", "writes": False}
            print(json.dumps({k: v for k, v in report.items() if k != "sha256"}, ensure_ascii=False, indent=2))
            return
        target = season / "builds" / key
        if target.exists():
            existing = json.loads((target / "report.json").read_text(encoding="utf-8"))
            if existing["sha256"] != checksums:
                raise ValueError("Existing release differs")
            for name, sha in checksums.items():
                if digest(target / "site" / name) != sha:
                    raise ValueError("Existing release checksum mismatch")
        else:
            staged.rename(target)
        if extra_plan:
            season_data.ensure_unchanged(extra_plan)
        if client_plan:
            client_result = run_stage("Update client tiles", season_client.install, client_plan, target / "site", season, report)
            write_json(target / "client-update.json", client_result)
            print("Client updated: " + json.dumps(client_result, ensure_ascii=False))
        if extra_plan:
            print("Client surface data updated: " + json.dumps(run_stage("Update client surface data", season_data.install, extra_plan, target, season, season_client.require_closed), ensure_ascii=False))
        export = ROOT / "exports" / "Steel-Soil-JourneyMap-latest.zip"
        export.parent.mkdir(exist_ok=True)
        export_staged = Path(temp) / "team-latest.zip"
        shutil.copy2(target / zip_path.name, export_staged)
        if digest(export_staged) != digest(target / zip_path.name):
            raise ValueError("Distribution copy checksum mismatch")
        os.replace(export_staged, export)
        pointer = season / "current.json.tmp"
        write_json(pointer, {"build_id": key, "site": f"builds/{key}/site",
                            "updated_at": datetime.now(timezone.utc).isoformat()})
        os.replace(pointer, season / "current.json")
        print(f"Build {key}: {len(entries)} tiles; site {report['site_bytes']} bytes")
        print(f"Site: {target / 'site'}")
        print(f"Team ZIP: {target / zip_path.name}")
        print(f"Latest team ZIP: {export}")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="steel-soil")
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot")
    snap.add_argument("source_id")
    snap.add_argument("day_folder", type=Path)
    zip_snap = commands.add_parser("snapshot-zip")
    zip_snap.add_argument("source_id")
    zip_snap.add_argument("archive", type=Path)
    release = commands.add_parser("build")
    release.add_argument("--dry-run", action="store_true")
    release.add_argument("--import-zips", action="store_true", help="Import new original ZIPs from imports/<season> before merging")
    release.add_argument("--no-publish", action="store_true", help="Skip GitHub publication; still copy ZIP to the configured team folder")
    release.add_argument("--no-cloudflare", action="store_true", help="Skip configured Cloudflare publication")
    release.add_argument("--skip-client", action="store_true", help="Build from retained sources without reading or writing the client")
    release.add_argument("--surface-workers", type=lambda v: None if v == "auto" else int(v), choices=[None,*range(1,65)], default=argparse.SUPPRESS, metavar="auto|1..64",
                         help="Surface capture/merge processes (default: auto, 80%% CPU with RAM limit)")
    release.add_argument("--rebuild-surface", action="store_true",
                         help="Recompute surface regions instead of reusing their cache")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.season):
        parser.error("Invalid season id")
    season = ROOT / "seasons" / args.season
    config = json.loads((season / "config.json").read_text(encoding="utf-8"))
    if args.command == "build":
        if hasattr(args, "surface_workers"):
            config = {**config, "surface_workers": args.surface_workers}
        if args.rebuild_surface:
            config = {**config, "surface_cache": False}
    with season_client.job_lock(season):
        if args.command == "snapshot":
            client_settings = season / "client.json"
            if client_settings.exists() and (season / "client_state.json").exists():
                client = json.loads(client_settings.read_text(encoding="utf-8"))
                if args.day_folder.resolve() == Path(client["day_folder"]).resolve():
                    raise ValueError("Use build to import only fresh client changes; do not snapshot installed merged tiles")
            snapshot(season, args.source_id, args.day_folder, config)
        elif args.command == "snapshot-zip":
            snapshot_zip(season, args.source_id, args.archive, config)
        else:
            report = build(season, config, args.dry_run, args.skip_client, args.import_zips)
            delivered = run_stage("Copy team ZIP to Drive folder", deliver_after_build, season, report, args.dry_run)
            run_stage("GitHub publication disabled" if args.no_publish or args.dry_run else "Publish GitHub map", publish_after_build, args.dry_run, args.no_publish, args.season)
            if not args.dry_run and not args.no_cloudflare and (season / "cloudflare.json").exists():
                from publish_cloudflare import publish as publish_cloudflare
                run_stage("Cloudflare publication", publish_cloudflare, season)
            if args.import_zips and not (args.dry_run or args.skip_client) and (delivered or not args.no_publish):
                run_stage("Move processed ZIPs", move_processed_archives, season, report)


if __name__ == "__main__":
    enable_console()
    try:
        main()
    except Exception:
        import traceback
        error(traceback.format_exc())
        raise SystemExit(1)
