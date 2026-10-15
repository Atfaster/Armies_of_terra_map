import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import season_background
import build_season


class BackgroundTest(unittest.TestCase):
    def test_negative_coordinates_edges_and_adjacent_tile_sampling(self):
        source = Image.new("RGBA", (4, 4))
        for z in range(4):
            for x in range(4):
                source.putpixel((x, z), (x + 1, z + 1, 7, 255))
        bounds = {"min_x": -3, "max_x": 5, "min_z": -3, "max_z": 5}
        bg = season_background.Background(source, bounds, 4)
        self.assertEqual(len(list(bg.names())), 9)
        for name in bg.names():
            tile = bg.tile(name)
            tx, tz = map(int, Path(name).stem.split(","))
            for z in range(4):
                for x in range(4):
                    wx, wz = tx * 4 + x, tz * 4 + z
                    if -3 <= wx < 5 and -3 <= wz < 5:
                        expected = source.getpixel((int((wx + 3 + .5) / 2), int((wz + 3 + .5) / 2)))
                    else:
                        expected = (0, 0, 0, 0)
                    self.assertEqual(tile.getpixel((x, z)), expected, (name, x, z))

    def test_background_removal_and_true_pixel_overlay(self):
        bg = season_background.Background(Image.new("RGB", (2, 2), (10, 20, 30)),
             {"min_x": 0, "max_x": 2, "min_z": 0, "max_z": 2}, 2)
        composite = bg.tile("0,0.png")
        composite.putpixel((1, 1), (200, 4, 7, 128))
        real = bg.remove(composite, "0,0.png")
        self.assertEqual(real.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(real.getpixel((1, 1)), (200, 4, 7, 128))
        output = bg.tile("0,0.png")
        output.paste(real, (0, 0), real.getchannel("A").point(lambda a: 255 if a else 0))
        self.assertEqual(output.tobytes(), composite.tobytes())

    def test_import_removes_background_but_preserves_raw_originals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "incoming"
            source.mkdir()
            original = source / "0,0.png"
            raw = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
            raw.putpixel((0, 0), (200, 4, 7, 255))
            raw.save(original)
            bg = season_background.Background(Image.new("RGB", (2, 2), (10, 20, 30)),
                 {"min_x": 0, "max_x": 2, "min_z": 0, "max_z": 2}, 2)
            season = root / "season"
            config = {"source_order": ["team"], "tile_size": 2, "dimension": "overworld", "layer": "day"}
            build_season.snapshot(season, "team", source, config, backdrop=bg)
            manifest = next((season / "sources").glob("*/*/manifest.json"))
            data = json.loads(manifest.read_text())
            self.assertEqual(data["background_removed"], bg.identity)
            self.assertEqual((manifest.parent / "original-day/0,0.png").read_bytes(), original.read_bytes())
            with Image.open(manifest.parent / "day/0,0.png") as filtered:
                self.assertEqual(filtered.getchannel("A").histogram()[255], 1)
