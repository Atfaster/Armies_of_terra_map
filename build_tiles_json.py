import json
import re
from pathlib import Path

TILE_DIR = Path(r"C:\Users\atfas\Drive\Games\Minecraft\Armies of Terra\WorldMap\leaflet_map\tiles")
OUT_FILE = TILE_DIR.parent / "tiles.json"

pattern = re.compile(r"^(-?\d+),(-?\d+)\.png$", re.IGNORECASE)

tiles = []

for file in TILE_DIR.iterdir():
    if not file.is_file():
        continue

    m = pattern.match(file.name)
    if not m:
        continue

    x = int(m.group(1))
    y = int(m.group(2))

    tiles.append({
        "file": f"tiles/{file.name}",
        "x": x,
        "y": y
    })

tiles.sort(key=lambda t: (t["y"], t["x"]))

with OUT_FILE.open("w", encoding="utf-8") as f:
    json.dump(tiles, f, ensure_ascii=False, indent=2)

print(f"Saved {len(tiles)} tiles to {OUT_FILE}")