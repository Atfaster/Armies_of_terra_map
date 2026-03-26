import json
import re
from pathlib import Path
from PIL import Image

ROOT = Path(r"C:\Users\atfas\Drive\Games\Minecraft\Armies of Terra\WorldMap\leaflet_map")
TILES_DIR = ROOT / "tiles"
PREVIEWS_DIR = ROOT / "previews"

TILES_JSON = ROOT / "tiles.json"

TILE_SIZE = 512
PREVIEW_FACTORS = [4, 8, 16]

PREVIEWS_DIR.mkdir(exist_ok=True)

pattern = re.compile(r"^(-?\d+),(-?\d+)\.png$", re.IGNORECASE)

tiles = []

print("Scanning tiles...")

for file in TILES_DIR.iterdir():
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

# --- Сохраняем tiles.json ---
with TILES_JSON.open("w", encoding="utf-8") as f:
    json.dump(tiles, f, ensure_ascii=False, indent=2)

print(f"Saved tiles.json ({len(tiles)} tiles)")

if not tiles:
    raise RuntimeError("Нет тайлов")

# --- Определяем границы ---
min_tx = min(t["x"] for t in tiles)
min_ty = min(t["y"] for t in tiles)
max_tx = max(t["x"] for t in tiles)
max_ty = max(t["y"] for t in tiles)

tiles_w = (max_tx - min_tx + 1)
tiles_h = (max_ty - min_ty + 1)

full_w = tiles_w * TILE_SIZE
full_h = tiles_h * TILE_SIZE

print(f"Full size: {full_w} x {full_h}")

# --- Собираем карту ---
canvas = Image.new("RGBA", (full_w, full_h), (0, 0, 0, 0))

for i, tile in enumerate(tiles, start=1):
    img_path = ROOT / tile["file"]

    if not img_path.exists():
        print(f"[WARN] Missing: {img_path}")
        continue

    img = Image.open(img_path).convert("RGBA")

    px = (tile["x"] - min_tx) * TILE_SIZE
    py = (tile["y"] - min_ty) * TILE_SIZE

    canvas.alpha_composite(img, (px, py))

    if i % 50 == 0 or i == len(tiles):
        print(f"Processed {i}/{len(tiles)}")

# --- Полный PNG (опционально) ---
full_png = PREVIEWS_DIR / "overview_full.png"
canvas.save(full_png)
print(f"Saved {full_png}")

# --- Preview уровни ---
for factor in PREVIEW_FACTORS:
    out_w = max(1, full_w // factor)
    out_h = max(1, full_h // factor)

    preview = canvas.resize((out_w, out_h), Image.Resampling.LANCZOS)

    # ВАЖНО: белый фон
    rgb = Image.new("RGB", preview.size, (221, 221, 221))
    rgb.paste(preview, mask=preview.getchannel("A"))

    out_file = PREVIEWS_DIR / f"overview_{factor}x.jpg"
    rgb.save(out_file, quality=85, optimize=True)

    print(f"Saved {out_file} -> {out_w}x{out_h}")

# --- meta ---
meta = {
    "tile_size": TILE_SIZE,
    "min_tx": min_tx,
    "min_ty": min_ty,
    "max_tx": max_tx,
    "max_ty": max_ty,
    "min_x": min_tx * TILE_SIZE,
    "min_y": min_ty * TILE_SIZE,
    "max_x": (max_tx + 1) * TILE_SIZE,
    "max_y": (max_ty + 1) * TILE_SIZE,
    "full_width": full_w,
    "full_height": full_h,
}

with (PREVIEWS_DIR / "overview_meta.json").open("w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)

print("Saved overview_meta.json")

print("DONE")