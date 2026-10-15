import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import season_client as client
from build_season import merge_images


class ClientUpdateTest(unittest.TestCase):
    def test_installed_background_is_not_new_research_but_changed_pixels_are(self):
        real_path = self.site / "explored/0,0.png"
        real_path.parent.mkdir()
        real_path.write_bytes((self.site / "tiles/0,0.png").read_bytes())
        with Image.open(real_path) as raw:
            real = raw.copy()
        backdrop = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
        backdrop.paste(real, (0, 0), real.getchannel("A").point(lambda a: 255 if a else 0))
        backdrop.save(self.site / "tiles/0,0.png")
        client.json_write(self.site / "background.json", {})
        self.report["sha256"] = {"tiles/0,0.png": client.sha((self.site / "tiles/0,0.png").read_bytes()),
                                 "explored/0,0.png": client.sha(real_path.read_bytes())}
        client.json_write(self.site.parent / "report.json", self.report)
        bg_path = self.season / "background.png"
        Image.new("RGB", (2, 2), (10, 20, 30)).save(bg_path)
        self.config["background"] = {"image": "background.png", "sha256": client.sha(bg_path.read_bytes()),
            "bounds": {"min_x": 0, "max_x": 2, "min_z": 0, "max_z": 2}}
        client.install(self.plan, self.site, self.season, self.report, closed_check=lambda: None)
        repeat = self.root / "background-repeat"
        repeat.mkdir()
        plan = client.prepare(self.season, self.config, self.grouped, repeat)
        self.assertEqual(plan["deltas"], [])
        self.assertEqual(Image.open(plan["coverage_masks"]["0,0.png"]).histogram()[255], 3)
        # Simulate JourneyMap replacing a previously coarse pixel during exploration.
        backdrop.putpixel((0, 1), (99, 98, 97, 255))
        backdrop.save(self.folder / "0,0.png")
        fresh = self.root / "background-fresh"
        fresh.mkdir()
        plan = client.prepare(self.season, self.config, self.grouped, fresh)
        self.assertEqual(sum(r["fresh_pixels"] for r in plan["deltas"]), 1)
        self.assertEqual(Image.open(plan["coverage_masks"]["0,0.png"]).histogram()[255], 4)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.season = self.root / "season"
        self.folder = self.root / "client" / "overworld" / "day"
        self.folder.mkdir(parents=True)
        self.season.mkdir()
        self.config = {"tile_size": 2, "source_order": ["player", "team"]}
        client.json_write(self.season / "client.json", {"source_id": "player", "day_folder": str(self.folder)})
        self.original = self.season / "sources" / "player" / "original" / "day" / "0,0.png"
        self.original.parent.mkdir(parents=True)
        old = Image.new("RGBA", (2, 2))
        old.putpixel((0, 0), (100, 0, 0, 255))
        old.save(self.original)
        client.json_write(self.original.parent.parent / "manifest.json", {
            "files": [{"name": "0,0.png", "mtime_ns": 100, "sha256": client.sha(self.original.read_bytes())}]})
        fresh = old.copy()
        fresh.putpixel((0, 0), (200, 0, 0, 255))
        fresh.putpixel((1, 0), (255, 255, 0, 255))
        fresh.save(self.folder / "0,0.png")
        self.team = self.root / "team.png"
        other = Image.new("RGBA", (2, 2))
        other.putpixel((0, 0), (0, 0, 255, 255))
        other.putpixel((1, 1), (0, 255, 0, 255))
        other.save(self.team)
        self.grouped = {"0,0.png": [((100, 0, "old"), self.original), ((10**20, 1, "future"), self.team)]}
        self.sources = []
        self.work = self.root / "work"
        self.work.mkdir()
        self.plan = client.prepare(self.season, self.config, self.grouped, self.work)
        client.add_inputs(self.plan, self.grouped, self.sources, self.season, self.config, False)
        self.build_id = "a" * 20
        self.site = self.season / "builds" / self.build_id / "site"
        (self.site / "tiles").mkdir(parents=True)
        merge_images(self.grouped["0,0.png"], 2).save(self.site / "tiles" / "0,0.png")
        self.report = {"build_id": self.build_id, "sha256": {
            "tiles/0,0.png": client.sha((self.site / "tiles" / "0,0.png").read_bytes())}}

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_pixels_win_and_repeat_does_not_promote_merged_pixels(self):
        client.verify_output(self.plan, self.site)
        result = client.install(self.plan, self.site, self.season, self.report, closed_check=lambda: None)
        self.assertEqual(result["fresh_pixels"], 2)
        with Image.open(self.folder / "0,0.png") as output:
            self.assertEqual(output.getpixel((0, 0)), (200, 0, 0, 255))
            self.assertEqual(output.getpixel((1, 0)), (255, 255, 0, 255))
            self.assertEqual(output.getpixel((1, 1)), (0, 255, 0, 255))
        backup = Path(result["backup"]) / "day" / "0,0.png"
        self.assertEqual(client.sha(backup.read_bytes()), self.plan["records"]["0,0.png"]["sha256"])
        repeated = self.root / "repeat"
        repeated.mkdir()
        second = client.prepare(self.season, self.config, self.grouped, repeated)
        self.assertEqual(second["deltas"], [])
        # JourneyMap can rewrite identical pixels with a new file timestamp.
        os.utime(self.folder / "0,0.png", None)
        third = self.root / "third"
        third.mkdir()
        self.assertEqual(client.prepare(self.season, self.config, self.grouped, third)["deltas"], [])

    def test_changed_client_or_new_tile_aborts_before_writing(self):
        original = (self.folder / "0,0.png").read_bytes()
        Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(self.folder / "1,0.png")
        with self.assertRaisesRegex(RuntimeError, "changed since capture"):
            client.install(self.plan, self.site, self.season, self.report, closed_check=lambda: None)
        self.assertEqual((self.folder / "0,0.png").read_bytes(), original)
        self.assertFalse((self.season / "client_state.json").exists())

    def test_color_change_aborts_and_running_game_aborts(self):
        tile = self.folder / "0,0.png"
        Image.new("RGBA", (2, 2), (4, 5, 6, 255)).save(tile)
        edited = tile.read_bytes()
        with self.assertRaises(RuntimeError):
            client.install(self.plan, self.site, self.season, self.report, closed_check=lambda: None)
        self.assertEqual(tile.read_bytes(), edited)
        def running():
            raise RuntimeError("Close Minecraft")
        with self.assertRaisesRegex(RuntimeError, "Close Minecraft"):
            client.install(self.plan, self.site, self.season, self.report, closed_check=running)

    def test_wrong_output_rejected(self):
        Image.new("RGBA", (2, 2)).save(self.site / "tiles" / "0,0.png")
        with self.assertRaisesRegex(ValueError, "coverage would be lost"):
            client.verify_output(self.plan, self.site)

    def test_identical_retry_reuses_delta_priority(self):
        retry_work = self.root / "retry"
        retry_work.mkdir()
        retry = client.prepare(self.season, self.config, self.grouped, retry_work)
        self.assertEqual(retry["deltas"], self.plan["deltas"])

    def test_parallel_merge_is_blocked_and_lock_releases(self):
        with client.job_lock(self.season):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                with client.job_lock(self.season):
                    self.fail("Second merge acquired lock")
        with client.job_lock(self.season):
            pass

    def test_recovery_verifies_partial_install_and_refuses_new_research(self):
        client.json_write(self.site.parent / "report.json", self.report)
        calls = 0
        def interrupt_after_write():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("Simulated interrupted final check")
        with self.assertRaises(RuntimeError):
            client.install(self.plan, self.site, self.season, self.report, closed_check=interrupt_after_write)
        self.assertEqual(json.loads((self.season / "client_state.json").read_text())["status"], "interrupted")
        tile = self.folder / "0,0.png"
        installed = tile.read_bytes()
        Image.new("RGBA", (2, 2), (33, 44, 55, 255)).save(tile)
        changed = tile.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Fresh client changes"):
            client.recover(self.season, self.config, closed_check=lambda: None)
        self.assertEqual(tile.read_bytes(), changed)
        tile.write_bytes(installed)
        result = client.recover(self.season, self.config, closed_check=lambda: None)
        self.assertEqual(result["updated_tiles"], 0)
        self.assertEqual(json.loads((self.season / "client_state.json").read_text())["status"], "complete")

    def test_interrupted_install_keeps_backup_and_newly_appeared_tile(self):
        other = self.site / "tiles" / "1,0.png"
        Image.new("RGBA", (2, 2), (8, 9, 10, 255)).save(other)
        self.report["sha256"]["tiles/1,0.png"] = client.sha(other.read_bytes())
        original_replace = os.replace
        late = self.folder / "1,0.png"
        def replace_with_late_tile(source, target):
            original_replace(source, target)
            if Path(target) == self.folder / "0,0.png":
                Image.new("RGBA", (2, 2), (77, 88, 99, 255)).save(late)
        with patch.object(client.os, "replace", side_effect=replace_with_late_tile):
            with self.assertRaisesRegex(RuntimeError, "New client tile appeared"):
                client.install(self.plan, self.site, self.season, self.report, closed_check=lambda: None)
        with Image.open(late) as actual:
            self.assertEqual(actual.getpixel((0, 0)), (77, 88, 99, 255))
        state = json.loads((self.season / "client_state.json").read_text())
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["written"], ["0,0.png"])
        self.assertEqual(client.sha((Path(state["backup"]) / "day" / "0,0.png").read_bytes()),
                         self.plan["records"]["0,0.png"]["sha256"])
        retry = self.root / "interrupted-retry"
        retry.mkdir()
        with self.assertRaisesRegex(RuntimeError, "installation incomplete"):
            client.prepare(self.season, self.config, self.grouped, retry)


if __name__ == "__main__":
    unittest.main()
