"""Independently check explored-pixel union, release hashes and distribution ZIP."""
import hashlib
import gzip
import json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image, ImageChops
import season_background

ROOT = Path(__file__).resolve().parents[1]


def main():
    season = ROOT / "seasons" / "steel-soil"
    current = json.loads((season / "current.json").read_text(encoding="utf-8"))
    release = season / "builds" / current["build_id"]
    report = json.loads((release / "report.json").read_text(encoding="utf-8"))
    config = json.loads((season / "config.json").read_text(encoding="utf-8"))
    backdrop = season_background.load(season, config) if report.get("background") else None
    if backdrop:
        assert backdrop.metadata() == report["background"]
    for name, expected in report["sha256"].items():
        assert hashlib.sha256((release / "site" / name).read_bytes()).hexdigest() == expected, name
    masks = {}
    inputs = {}
    source_count = 0
    for source in report["sources"]:
        manifest = season / "sources" / source["source_id"] / source["snapshot"] / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        rank = config["source_order"].index(source["source_id"])
        for record in data["files"]:
            path = manifest.parent / "day" / record["name"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
            if record.get("original_sha256"):
                assert hashlib.sha256((manifest.parent / "original-day" / record["name"]).read_bytes()).hexdigest() == record["original_sha256"]
            priority = (record.get("merge_priority_ns", record["mtime_ns"]), rank, source["snapshot"])
            inputs.setdefault(record["name"], []).append((priority, path))
            with Image.open(path) as image:
                mask = image.getchannel("A").point(lambda a: 255 if a else 0)
                masks[record["name"]] = ImageChops.lighter(masks[record["name"]], mask) if record["name"] in masks else mask
            source_count += 1
    explored = 0
    for name, expected in masks.items():
        if not expected.getbbox():
            continue
        directory = "explored" if backdrop else "tiles"
        with Image.open(release / "site" / directory / name) as image:
            actual = image.getchannel("A").point(lambda a: 255 if a else 0)
            assert ImageChops.difference(expected, actual).getbbox() is None, f"Lost or invented coverage: {name}"
            # Independent replay checks RGBA priority, not only explored coverage.
            replay = Image.new("RGBA", image.size)
            for _, path in sorted(inputs[name]):
                with Image.open(path) as original:
                    replay.paste(original, (0, 0), original.getchannel("A").point(lambda a: 255 if a else 0))
            assert not any(band.getbbox() for band in ImageChops.difference(replay, image).split()), name
        explored += expected.histogram()[255]
    if backdrop:
        for tile in json.loads((release / "site/tiles.json").read_text(encoding="utf-8")):
            name = Path(tile["file"]).name
            expected = backdrop.tile(name)
            real_path = release / "site/explored" / name
            if real_path.exists():
                with Image.open(real_path) as real:
                    expected.paste(real, (0, 0), real.getchannel("A").point(lambda a: 255 if a else 0))
            with Image.open(release / "site" / tile["file"]) as actual:
                assert not any(band.getbbox() for band in ImageChops.difference(expected, actual).split()), f"Background overrode research: {name}"
    assert report["exploration"]["blocks"] == explored
    archive_path = next(release.glob("*.zip"))
    downloads = release / "site" / "downloads"
    download = json.loads((downloads / "latest.json").read_text(encoding="utf-8"))
    archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert download["build_id"] == current["build_id"]
    assert download["tiles"] == report["output_tiles"]
    assert download["file"] == "Steel-Soil-JourneyMap-latest.zip"
    assert download["sha256"] == archive_sha
    assert download["bytes"] == archive_path.stat().st_size
    if download.get("storage") != "github-release":
        assert hashlib.sha256((downloads / download["file"]).read_bytes()).hexdigest() == archive_sha
    else:
        assert download["release_tag"] == "map-" + current["build_id"]
        assert download["url"].endswith("/" + download["release_tag"] + "/" + download["file"])
    with ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        tile_names = [name for name in archive.namelist() if name.endswith(".png")]
        assert len(tile_names) == report["output_tiles"]
        for name in tile_names:
            key = "tiles/" + Path(name).name
            assert hashlib.sha256(archive.read(name)).hexdigest() == report["sha256"][key]
    for name,expected in report.get("team_data_sha256", {}).items():
        assert hashlib.sha256((release / "team-data" / name).read_bytes()).hexdigest() == expected
        with ZipFile(archive_path) as archive:
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
    surface_index = release / "site/surface/index.json"
    if surface_index.exists():
        metadata = json.loads(surface_index.read_text(encoding="utf-8"))
        count = 0
        for name in metadata["tiles"].values():
            block = json.loads(gzip.decompress((surface_index.parent / name).read_bytes()))
            positions = [row[0] for row in block["cells"]]
            assert positions == sorted(set(positions))
            assert all(0 <= pos < 512 * 512 and 0 <= entry < len(block["palette"]) for pos, entry in block["cells"])
            count += len(positions)
        assert count == metadata["columns"] == report["shared_data"]["surface_columns"]
    result = {"shared_data": report.get("shared_data", {}), "build_id": current["build_id"], "source_tiles": source_count,
              "output_tiles": report["output_tiles"], "explored_blocks": explored,
              "coverage_union": "PASS", "source_and_release_sha256": "PASS", "zip_matches_site": "PASS",
              "website_download_matches_zip": "PASS", "zip_sha256": archive_sha,
              "zip_bytes": archive_path.stat().st_size}
    if backdrop:
        result["background_below_all_research"] = "PASS"
        result["explored_tiles"] = report["exploration"]["tiles"]
    (release / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
