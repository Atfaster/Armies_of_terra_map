#!/usr/bin/env python3
from __future__ import annotations

import argparse
from io import BytesIO
import json
import shutil
import subprocess
import sqlite3
import sys
import zlib
from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from zipfile import ZipFile

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path(
    r"C:\Users\atfas\curseforge\minecraft\Instances\Armies of Terra\journeymap\data\mp\Armies~of~Terra\overworld\day"
)
DEFAULT_TILES_DIR = ROOT / "tiles"
DEFAULT_TILES_JSON = ROOT / "tiles.json"
DEFAULT_STATE_DB = ROOT / "data" / "tile_merge_state.sqlite3"
DEFAULT_BUILD_SCRIPT = ROOT / "build_map_assets.py"
DEFAULT_INBOX_DIR = ROOT / "imports" / "journeymap_inbox"
DEFAULT_EXPORT_DIR = Path(
    r"C:\Users\atfas\Drive\Games\Minecraft\Armies of Terra\Journeymap saves"
)

TILE_NAME_PATTERN = re.compile(r"^(-?\d+),(-?\d+)\.png$", re.IGNORECASE)
PNG_SUFFIX = ".png"
ZIP_SUFFIX = ".zip"


@dataclass(frozen=True)
class TileFile:
    name: str
    path: Path
    x: int
    y: int
    mtime: int
    payload: bytes | None = None


@dataclass(frozen=True)
class InboxItem:
    path: Path
    kind: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge JourneyMap day tiles into the repository tiles directory. "
            "Opaque pixels from newer source files override older pixels."
        )
    )
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--tiles-dir", type=Path, default=DEFAULT_TILES_DIR)
    parser.add_argument("--tiles-json", type=Path, default=DEFAULT_TILES_JSON)
    parser.add_argument("--state-db", type=Path, default=DEFAULT_STATE_DB)
    parser.add_argument("--inbox-dir", type=Path, default=DEFAULT_INBOX_DIR)
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=DEFAULT_EXPORT_DIR,
        help="Directory where merged tile PNGs are copied for teammate distribution.",
    )
    parser.add_argument(
        "--keep-inbox",
        action="store_true",
        help="Keep processed archives and folders in the inbox directory.",
    )
    parser.add_argument(
        "--skip-previews",
        action="store_true",
        help="Do not rebuild preview images after merge.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and compare tiles without writing changes.",
    )
    return parser.parse_args()


def iter_tile_files(source_dir: Path) -> list[TileFile]:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir}")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {source_dir}")

    tiles: list[TileFile] = []
    for path in sorted(source_dir.glob(f"*{PNG_SUFFIX}")):
        match = TILE_NAME_PATTERN.match(path.name)
        if not match:
            continue
        stat = path.stat()
        tiles.append(
            TileFile(
                name=path.name,
                path=path,
                x=int(match.group(1)),
                y=int(match.group(2)),
                mtime=int(stat.st_mtime),
                payload=None,
            )
        )
    return tiles


def iter_tile_files_recursive(source_dir: Path) -> list[TileFile]:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir}")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {source_dir}")

    tiles: list[TileFile] = []
    for path in sorted(source_dir.rglob(f"*{PNG_SUFFIX}")):
        match = TILE_NAME_PATTERN.match(path.name)
        if not match:
            continue
        stat = path.stat()
        tiles.append(
            TileFile(
                name=path.name,
                path=path,
                x=int(match.group(1)),
                y=int(match.group(2)),
                mtime=int(stat.st_mtime),
                payload=None,
            )
        )
    return tiles


def zip_info_mtime_seconds(info) -> int:
    dt = datetime(*info.date_time)
    return int(dt.timestamp())


def iter_zip_tile_files(zip_path: Path) -> list[TileFile]:
    tiles: list[TileFile] = []
    with ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            match = TILE_NAME_PATTERN.match(name)
            if not match:
                continue
            tiles.append(
                TileFile(
                    name=name,
                    path=zip_path,
                    x=int(match.group(1)),
                    y=int(match.group(2)),
                    mtime=zip_info_mtime_seconds(info),
                    payload=archive.read(info),
                )
            )
    return tiles


def collect_inbox_items(inbox_dir: Path) -> list[InboxItem]:
    if not inbox_dir.exists():
        inbox_dir.mkdir(parents=True, exist_ok=True)
        return []
    if not inbox_dir.is_dir():
        raise NotADirectoryError(f"Inbox path is not a directory: {inbox_dir}")

    items: list[InboxItem] = []
    for path in sorted(inbox_dir.iterdir()):
        if path.is_dir():
            items.append(InboxItem(path=path, kind="directory"))
            continue
        if path.is_file() and TILE_NAME_PATTERN.match(path.name):
            items.append(InboxItem(path=path, kind="tile"))
            continue
        if path.is_file() and path.suffix.lower() == ZIP_SUFFIX:
            items.append(InboxItem(path=path, kind="zip"))
    return items


def collect_all_sources(
    source_dir: Path,
    inbox_dir: Path,
) -> tuple[list[TileFile], list[InboxItem]]:
    inbox_items = collect_inbox_items(inbox_dir)
    use_inbox_only = bool(inbox_items)
    tiles: list[TileFile] = []

    if not use_inbox_only:
        tiles.extend(iter_tile_files(source_dir))

    for item in inbox_items:
        if item.kind == "directory":
            tiles.extend(iter_tile_files_recursive(item.path))
            continue
        if item.kind == "tile":
            stat = item.path.stat()
            match = TILE_NAME_PATTERN.match(item.path.name)
            if match is None:
                continue
            tiles.append(
                TileFile(
                    name=item.path.name,
                    path=item.path,
                    x=int(match.group(1)),
                    y=int(match.group(2)),
                    mtime=int(stat.st_mtime),
                    payload=None,
                )
            )
            continue

        tiles.extend(iter_zip_tile_files(item.path))

    tiles.sort(key=lambda tile: (tile.mtime, tile.name, str(tile.path)))
    return tiles, inbox_items


def export_tiles(tiles_dir: Path, export_dir: Path, dry_run: bool) -> int:
    tile_paths = [
        path
        for path in sorted(tiles_dir.glob(f"*{PNG_SUFFIX}"))
        if TILE_NAME_PATTERN.match(path.name)
    ]
    if dry_run:
        return len(tile_paths)

    export_dir.mkdir(parents=True, exist_ok=True)
    for path in tile_paths:
        shutil.copy2(path, export_dir / path.name)
    return len(tile_paths)


def cleanup_inbox_items(
    inbox_items: list[InboxItem],
    keep_inbox: bool,
    dry_run: bool,
) -> None:
    if keep_inbox or dry_run:
        return

    for item in inbox_items:
        if item.kind == "directory":
            shutil.rmtree(item.path, ignore_errors=True)
        else:
            item.path.unlink(missing_ok=True)


def connect_state_db(path: Path, dry_run: bool) -> sqlite3.Connection | None:
    if dry_run:
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tile_priority (
            tile_name TEXT PRIMARY KEY,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            payload BLOB NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    return conn


def decode_priorities(blob: bytes, pixel_count: int) -> array:
    raw = zlib.decompress(blob)
    priorities = array("I")
    priorities.frombytes(raw)
    if sys.byteorder != "little":
        priorities.byteswap()
    if len(priorities) != pixel_count:
        raise ValueError(
            f"Priority map size mismatch: expected {pixel_count}, got {len(priorities)}"
        )
    return priorities


def encode_priorities(priorities: array) -> bytes:
    payload = array("I", priorities)
    if sys.byteorder != "little":
        payload.byteswap()
    return zlib.compress(payload.tobytes(), level=9)


def bootstrap_priorities(image: Image.Image, fallback_mtime: int) -> array:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A").tobytes()
    priorities = array("I", [0]) * (rgba.size[0] * rgba.size[1])
    for index, alpha_value in enumerate(alpha):
        if alpha_value:
            priorities[index] = fallback_mtime
    return priorities


def load_existing_tile(dest_path: Path) -> tuple[tuple[int, int], bytearray, int] | None:
    if not dest_path.exists():
        return None

    image = Image.open(dest_path).convert("RGBA")
    return image.size, bytearray(image.tobytes()), int(dest_path.stat().st_mtime)


def load_priority_map(
    conn: sqlite3.Connection | None,
    tile_name: str,
    size: tuple[int, int],
    dest_bytes: bytearray | None,
    fallback_mtime: int,
) -> array:
    pixel_count = size[0] * size[1]

    if conn is not None:
        row = conn.execute(
            "SELECT payload, width, height FROM tile_priority WHERE tile_name = ?",
            (tile_name,),
        ).fetchone()
        if row is not None:
            payload, width, height = row
            if (width, height) == size:
                return decode_priorities(payload, pixel_count)

    if dest_bytes is None:
        return array("I", [0]) * pixel_count

    image = Image.frombytes("RGBA", size, bytes(dest_bytes))
    return bootstrap_priorities(image, fallback_mtime)


def merge_tile(
    source_tile: TileFile,
    dest_dir: Path,
    conn: sqlite3.Connection | None,
    dry_run: bool,
) -> tuple[bool, bool]:
    if source_tile.payload is None:
        source_image = Image.open(source_tile.path).convert("RGBA")
    else:
        source_image = Image.open(BytesIO(source_tile.payload)).convert("RGBA")
    size = source_image.size
    source_bytes = source_image.tobytes()
    dest_path = dest_dir / source_tile.name

    existing = load_existing_tile(dest_path)
    if existing is None:
        dest_size = size
        dest_bytes = bytearray(b"\x00" * (size[0] * size[1] * 4))
        fallback_mtime = 0
    else:
        dest_size, dest_bytes, fallback_mtime = existing
        if dest_size != size:
            raise ValueError(
                f"Tile size mismatch for {source_tile.name}: "
                f"existing {dest_size}, incoming {size}"
            )

    priorities = load_priority_map(
        conn=conn,
        tile_name=source_tile.name,
        size=size,
        dest_bytes=None if existing is None else dest_bytes,
        fallback_mtime=fallback_mtime,
    )

    changed = False
    any_opaque = False
    for pixel_index in range(size[0] * size[1]):
        offset = pixel_index * 4
        alpha = source_bytes[offset + 3]
        if alpha == 0:
            if dest_bytes[offset + 3]:
                any_opaque = True
            continue

        any_opaque = True
        if source_tile.mtime < priorities[pixel_index]:
            continue

        pixel = source_bytes[offset : offset + 4]
        if dest_bytes[offset : offset + 4] != pixel or priorities[pixel_index] != source_tile.mtime:
            dest_bytes[offset : offset + 4] = pixel
            priorities[pixel_index] = source_tile.mtime
            changed = True

    if not any_opaque:
        return False, False

    if dry_run:
        return changed, existing is None

    if changed or existing is None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        Image.frombytes("RGBA", size, bytes(dest_bytes)).save(dest_path)

    if conn is not None:
        conn.execute(
            """
            INSERT INTO tile_priority (tile_name, width, height, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tile_name) DO UPDATE SET
                width = excluded.width,
                height = excluded.height,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                source_tile.name,
                size[0],
                size[1],
                encode_priorities(priorities),
                source_tile.mtime,
            ),
        )

    return changed, existing is None


def rebuild_tiles_json(tiles_dir: Path, tiles_json_path: Path, dry_run: bool) -> int:
    entries: list[dict[str, int | str]] = []
    for path in sorted(tiles_dir.glob(f"*{PNG_SUFFIX}")):
        match = TILE_NAME_PATTERN.match(path.name)
        if not match:
            continue
        entries.append(
            {
                "file": f"tiles/{path.name}",
                "x": int(match.group(1)),
                "y": int(match.group(2)),
            }
        )

    entries.sort(key=lambda item: (item["y"], item["x"]))
    if not dry_run:
        tiles_json_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return len(entries)


def rebuild_previews(skip_previews: bool, dry_run: bool) -> None:
    if skip_previews or dry_run:
        return
    subprocess.run(
        [sys.executable, str(DEFAULT_BUILD_SCRIPT)],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    args = parse_args()
    source_tiles, inbox_items = collect_all_sources(args.source, args.inbox_dir)
    if not source_tiles:
        raise FileNotFoundError(
            f"No JourneyMap PNG tiles found in {args.source} or {args.inbox_dir}"
        )

    conn = connect_state_db(args.state_db, dry_run=args.dry_run)
    changed_tiles = 0
    new_tiles = 0

    try:
        for index, tile in enumerate(source_tiles, start=1):
            changed, created = merge_tile(
                source_tile=tile,
                dest_dir=args.tiles_dir,
                conn=conn,
                dry_run=args.dry_run,
            )
            if changed:
                changed_tiles += 1
            if created:
                new_tiles += 1
            if index % 50 == 0 or index == len(source_tiles):
                print(f"Processed {index}/{len(source_tiles)} tiles")

        if conn is not None:
            conn.commit()
        total_tiles = rebuild_tiles_json(args.tiles_dir, args.tiles_json, dry_run=args.dry_run)

        mode = "Dry run" if args.dry_run else "Merge complete"
        print(f"{mode}: scanned {len(source_tiles)} source tiles")
        print(f"Changed tiles: {changed_tiles}")
        print(f"New tiles: {new_tiles}")
        print(f"Indexed tiles: {total_tiles}")
        print(f"Inbox items: {len(inbox_items)}")
        if not args.dry_run:
            print(f"State DB: {args.state_db}")
            rebuild_previews(skip_previews=args.skip_previews, dry_run=args.dry_run)
            if not args.skip_previews:
                print(f"Previews rebuilt with: {DEFAULT_BUILD_SCRIPT}")
            exported_tiles = export_tiles(
                tiles_dir=args.tiles_dir,
                export_dir=args.export_dir,
                dry_run=args.dry_run,
            )
            print(f"Exported tiles: {exported_tiles}")
            print(f"Export directory: {args.export_dir}")
            cleanup_inbox_items(
                inbox_items=inbox_items,
                keep_inbox=args.keep_inbox,
                dry_run=args.dry_run,
            )
            if inbox_items and not args.keep_inbox:
                print(f"Inbox cleaned: {args.inbox_dir}")
            else:
                print(f"Inbox kept: {args.inbox_dir}")
        else:
            cleanup_inbox_items(
                inbox_items=inbox_items,
                keep_inbox=True,
                dry_run=True,
            )
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
