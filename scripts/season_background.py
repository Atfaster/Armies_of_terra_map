"""Coordinate-aligned reference raster. Never participates in exploration priority."""
import hashlib
import json
from functools import reduce
from pathlib import Path
from PIL import Image, ImageChops


class Background:
    def __init__(self, image, bounds, tile_size=512):
        self.image = image.convert("RGBA")
        self.bounds = bounds
        self.size = tile_size
        if bounds["max_x"] <= bounds["min_x"] or bounds["max_z"] <= bounds["min_z"]:
            raise ValueError("Invalid background bounds")
        self.identity = hashlib.sha256(self.image.tobytes() + json.dumps(
            {"bounds": bounds, "tile_size": tile_size, "resampling": "nearest-v1"}, sort_keys=True).encode()).hexdigest()

    def names(self):
        b, s = self.bounds, self.size
        for z in range(b["min_z"] // s, (b["max_z"] - 1) // s + 1):
            for x in range(b["min_x"] // s, (b["max_x"] - 1) // s + 1):
                yield f"{x},{z}.png"

    def tile(self, name):
        x, z = map(int, Path(name).stem.split(","))
        s, b = self.size, self.bounds
        left, top = x * s, z * s
        result = Image.new("RGBA", (s, s))
        l, t = max(left, b["min_x"]), max(top, b["min_z"])
        r, bottom = min(left + s, b["max_x"]), min(top + s, b["max_z"])
        if l >= r or t >= bottom:
            return result
        # Sample the small original directly. No 25024x25024 world allocation.
        sx = self.image.width / (b["max_x"] - b["min_x"])
        sz = self.image.height / (b["max_z"] - b["min_z"])
        patch = self.image.transform((r - l, bottom - t), Image.Transform.EXTENT,
            ((l - b["min_x"]) * sx, (t - b["min_z"]) * sz,
             (r - b["min_x"]) * sx, (bottom - b["min_z"]) * sz), Image.Resampling.NEAREST)
        result.paste(patch, (l - left, t - top))
        return result

    def remove(self, image, name):
        """Conservatively keep pixels distinguishable from the reference raster."""
        raw = image.convert("RGBA")
        reference = self.tile(name)
        difference = reduce(ImageChops.lighter, ImageChops.difference(raw, reference).split())
        matching = ImageChops.multiply(difference.point(lambda a: 0 if a else 255),
                                      reference.getchannel("A").point(lambda a: 255 if a else 0))
        result = raw.copy()
        result.paste((0, 0, 0, 0), (0, 0, self.size, self.size), matching)
        return result

    def metadata(self):
        return {"id": self.identity, "bounds": self.bounds, "source_size": list(self.image.size),
                "resampling": "nearest", "priority": "below all JourneyMap pixels",
                "counts_as_exploration": False}


def load(season, config):
    settings = config.get("background")
    if not settings:
        return None
    path = (season / settings["image"]).resolve()
    if not path.is_relative_to(season.resolve()):
        raise ValueError("Background source escaped season")
    if hashlib.sha256(path.read_bytes()).hexdigest() != settings["sha256"]:
        raise ValueError("Background source checksum mismatch")
    with Image.open(path) as image:
        return Background(image, settings["bounds"], config["tile_size"])
