import importlib.util
import tempfile
import unittest
import sys
import os
import json
from unittest.mock import patch
from zipfile import ZipFile
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

spec = importlib.util.spec_from_file_location("build_season", Path(__file__).resolve().parents[1] / "scripts/build_season.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MergeTest(unittest.TestCase):
    def test_delivery_verifies_copy_and_reuses_identical_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            season = root / "season"
            target = root / "team"
            key = "a" * 20
            source = season / "builds" / key / f"steel-soil-journeymap-{key}.zip"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"verified complete archive")
            expected = module.digest(source)
            module.write_json(source.parent / "site/downloads/latest.json", {
                "build_id": key, "sha256": expected, "bytes": source.stat().st_size})
            module.write_json(season / "delivery.json", {"folder": str(target)})
            report = {"build_id": key}
            self.assertFalse(module.deliver_after_build(season, report, dry_run=True))
            self.assertFalse(target.exists())
            self.assertTrue(module.deliver_after_build(season, report))
            self.assertEqual(module.digest(target / module.DOWNLOAD_NAME), expected)
            state = json.loads((season / "delivery_state.json").read_text())
            self.assertEqual(state["status"], "local-copy-verified")
            self.assertEqual(state["sha256"], expected)
            with patch.object(module.os, "replace", side_effect=AssertionError("unchanged ZIP must not be replaced")), \
                 patch.object(module.season_client, "json_write"):
                self.assertTrue(module.deliver_after_build(season, report))
            source.write_bytes(b"corrupt source")
            with self.assertRaisesRegex(ValueError, "source checksum"):
                module.deliver_after_build(season, report)
            self.assertEqual(module.digest(target / module.DOWNLOAD_NAME), expected)

    def test_no_publish_delivery_success_moves_archives_and_failure_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            module.write_json(root / "seasons/steel-soil/config.json", {})
            with patch.object(module, "ROOT", root), \
                 patch.object(module, "build", return_value={}), \
                 patch.object(module, "deliver_after_build", return_value=True) as deliver, \
                 patch.object(module, "publish_after_build") as publish, \
                 patch.object(module, "move_processed_archives") as move, \
                 patch.object(sys, "argv", ["build_season.py", "build", "--import-zips", "--no-publish"]):
                module.main()
                move.assert_called_once()
                publish.assert_called_once_with(False, True, "steel-soil")
                move.reset_mock()
                publish.reset_mock()
                deliver.side_effect = RuntimeError("delivery failed")
                with self.assertRaisesRegex(RuntimeError, "delivery failed"):
                    module.main()
                move.assert_not_called()
                publish.assert_not_called()

    def test_delivery_replacement_failure_preserves_previous_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            season = root / "season"
            key = "b" * 20
            source = season / "builds" / key / f"steel-soil-journeymap-{key}.zip"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"new complete archive")
            target = root / "team"
            target.mkdir()
            (target / module.DOWNLOAD_NAME).write_bytes(b"previous complete archive")
            module.write_json(source.parent / "site/downloads/latest.json", {
                "build_id": key, "sha256": module.digest(source), "bytes": source.stat().st_size})
            module.write_json(season / "delivery.json", {"folder": str(target)})
            with patch.object(module.os, "replace", side_effect=PermissionError("Drive busy")):
                with self.assertRaises(PermissionError):
                    module.deliver_after_build(season, {"build_id": key})
            self.assertEqual((target / module.DOWNLOAD_NAME).read_bytes(), b"previous complete archive")
            self.assertFalse((season / "delivery_state.json").exists())

    def test_processed_preserves_collisions_and_leaves_unmerged_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inbox = root / "imports/test"
            processed = inbox / "processed"
            processed.mkdir(parents=True)
            archive = inbox / "day.zip"
            archive.write_bytes(b"included original ZIP")
            sha = module.digest(archive)
            (processed / "day.zip").write_bytes(b"older archive")
            (processed / f"day-{sha[:12]}.zip").write_bytes(b"another older archive")
            (inbox / "new.zip").write_bytes(b"arrived after merge")
            # An imported file that has since been replaced must remain pending.
            (inbox / "changed.zip").write_bytes(b"new contents")
            season = root / "season"
            manifest = season / "sources/player/snapshot/manifest.json"
            module.write_json(manifest, {"archive_sha256": sha})
            report = {"season": "test", "sources": [{"source_id": "player", "snapshot": "snapshot"}]}
            with patch.object(module, "ROOT", root):
                module.move_processed_archives(season, report)
            self.assertFalse(archive.exists())
            self.assertEqual((processed / f"day-{sha[:12]}-2.zip").read_bytes(), b"included original ZIP")
            self.assertEqual((processed / "day.zip").read_bytes(), b"older archive")
            self.assertEqual((processed / f"day-{sha[:12]}.zip").read_bytes(), b"another older archive")
            self.assertTrue((inbox / "new.zip").exists())
            self.assertTrue((inbox / "changed.zip").exists())
            self.assertTrue(manifest.exists())

    def test_transfer_runs_only_after_successful_full_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            module.write_json(root / "seasons/steel-soil/config.json", {})
            with patch.object(module, "ROOT", root), \
                 patch.object(module, "build", return_value={}) as build, \
                 patch.object(module, "publish_after_build") as publish, \
                 patch.object(module, "move_processed_archives") as move:
                for extra in ([], ["--dry-run"], ["--no-publish"], ["--skip-client"]):
                    move.reset_mock()
                    with patch.object(sys, "argv", ["build_season.py", "build", "--import-zips", *extra]):
                        module.main()
                    self.assertEqual(move.call_count, 0 if extra else 1)
                move.reset_mock()
                publish.side_effect = RuntimeError("publication failed")
                with patch.object(sys, "argv", ["build_season.py", "build", "--import-zips"]):
                    with self.assertRaisesRegex(RuntimeError, "publication failed"):
                        module.main()
                move.assert_not_called()
                publish.side_effect = None
                build.side_effect = RuntimeError("client update failed")
                with patch.object(sys, "argv", ["build_season.py", "build", "--import-zips"]):
                    with self.assertRaisesRegex(RuntimeError, "client update failed"):
                        module.main()
                move.assert_not_called()

    def test_inbox_dry_run_retains_nothing_and_real_import_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            season = root / "seasons/test"
            season.mkdir(parents=True)
            inbox = root / "imports/test"
            inbox.mkdir(parents=True)
            tile = root / "0,0.png"
            Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(tile)
            with ZipFile(inbox / "day.zip", "w") as archive:
                archive.write(tile, "day/0,0.png")
            config = {"id": "test", "tile_size": 2, "dimension": "overworld",
                      "layer": "day", "source_order": ["atfaster"]}
            module.write_json(season / "config.json", config)
            with patch.object(module, "ROOT", root):
                grouped, _ = module.import_inbox(season, config, root / "dry", True)
                self.assertIn("0,0.png", grouped)
                self.assertFalse((season / "sources").exists())
                self.assertEqual(json.loads((season / "config.json").read_text()), config)
                grouped, sources = module.import_inbox(season, config, root / "real")
                self.assertEqual(len(sources), 1)
                updated = json.loads((season / "config.json").read_text())
                self.assertEqual(updated["source_order"], ["atfaster", "zip-day"])
                # Renaming the same ZIP does not create another source or snapshot.
                (inbox / "day.zip").rename(inbox / "renamed.zip")
                _, sources = module.import_inbox(season, updated, root / "repeat")
                self.assertEqual(len(sources), 1)
                self.assertEqual(len(list((season / "sources").glob("*/*/manifest.json"))), 1)

    def test_inbox_rejects_merged_team_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inbox = root / "imports/test"
            inbox.mkdir(parents=True)
            with ZipFile(inbox / "team.zip", "w") as archive:
                archive.writestr("INSTALL.txt", "Merged archive")
            with patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "not an original player map"):
                    module.import_inbox(root / "season", {"id": "test", "source_order": []}, root / "stage")

    def test_download_is_reproducible_and_contains_only_install_and_day(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            site = root / "site"
            (site / "tiles").mkdir(parents=True)
            tile = site / "tiles/0,0.png"
            Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(tile)
            entries = [{"file": "tiles/0,0.png"}]
            package = module.package_download(root, site, entries, "example", "Инструкция\n")
            original = package.read_bytes()
            info = json.loads((site / "downloads/latest.json").read_text())
            self.assertEqual(info["sha256"], module.digest(package))
            self.assertEqual((site / "downloads" / module.DOWNLOAD_NAME).read_bytes(), original)
            with ZipFile(package) as archive:
                self.assertEqual(archive.namelist(), ["INSTALL.txt", "overworld/day/0,0.png"])
                self.assertEqual(archive.read("overworld/day/0,0.png"), tile.read_bytes())
                self.assertIn("Инструкция", archive.read("INSTALL.txt").decode("utf-8-sig"))
            os.utime(tile, (1000000, 1000000))
            # A new staging site simulates rebuilding without retained artifacts.
            (site / "downloads/latest.json").unlink()
            (site / "downloads" / module.DOWNLOAD_NAME).unlink()
            (site / "downloads").rmdir()
            module.package_download(root, site, entries, "example", "Инструкция\n")
            self.assertEqual(package.read_bytes(), original)

    def test_publication_skipped_for_dry_run_and_failure_is_reported(self):
        with patch.object(module.subprocess, "run") as run:
            module.publish_after_build(True, False, "steel-soil")
            module.publish_after_build(False, True, "steel-soil")
            run.assert_not_called()
            run.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, "Local merge and ZIP are ready"):
                module.publish_after_build(False, False, "steel-soil")
            run.assert_called_once()

    def test_union_newer_pixel_and_tie_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b, c = [root / name for name in ("a.png", "b.png", "c.png")]
            first = Image.new("RGBA", (2, 2))
            first.putpixel((0, 0), (255, 0, 0, 255))
            first.putpixel((1, 0), (255, 0, 0, 255))
            first.save(a)
            second = Image.new("RGBA", (2, 2))
            second.putpixel((0, 0), (0, 255, 0, 255))
            second.putpixel((1, 1), (0, 255, 0, 255))
            second.save(b)
            third = Image.new("RGBA", (2, 2))
            third.putpixel((0, 0), (0, 0, 255, 128))
            third.save(c)
            result = module.merge_images([((2, 1, "b"), b), ((1, 0, "a"), a), ((2, 2, "c"), c)], 2)
            self.assertEqual(result.getpixel((0, 0)), (0, 0, 255, 128))
            self.assertEqual(result.getpixel((1, 0)), (255, 0, 0, 255))
            self.assertEqual(result.getpixel((1, 1)), (0, 255, 0, 255))
            self.assertEqual(result.getpixel((0, 1)), (0, 0, 0, 0))
            reverse = module.merge_images([((2, 2, "c"), c), ((1, 0, "a"), a), ((2, 1, "b"), b)], 2)
            self.assertEqual(result.tobytes(), reverse.tobytes())

    def test_reject_wrong_dimension_and_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.png"
            Image.new("RGBA", (3, 3)).save(path)
            with self.assertRaises(ValueError):
                module.merge_images([((1, 0, "a"), path)], 2)
            Image.new("RGB", (2, 2)).save(path)
            with self.assertRaises(ValueError):
                module.merge_images([((1, 0, "a"), path)], 2)


if __name__ == "__main__":
    unittest.main()
