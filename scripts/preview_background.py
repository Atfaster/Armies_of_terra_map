"""Preview coordinate calibration without changing JourneyMap or the published site."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-x", type=int, required=True)
    parser.add_argument("--max-x", type=int, required=True)
    parser.add_argument("--min-z", type=int, required=True)
    parser.add_argument("--max-z", type=int, required=True)
    parser.add_argument("--image", type=Path, default=ROOT / "imports/image full map.png")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.max_x <= args.min_x or args.max_z <= args.min_z:
        parser.error("Maximum coordinates must be greater than minimum coordinates")
    season = ROOT / "seasons/steel-soil"
    current = json.loads((season / "current.json").read_text(encoding="utf-8"))
    site = season / current["site"]
    size, header = 1600, 70
    with Image.open(args.image) as source:
        source_size = source.size
        base = source.convert("RGBA").resize((size, size), Image.Resampling.BILINEAR)
    combined = base.copy()
    span_x, span_z = args.max_x - args.min_x, args.max_z - args.min_z
    for tile in json.loads((site / "tiles.json").read_text(encoding="utf-8")):
        x = round((tile["x"] * 512 - args.min_x) * size / span_x)
        z = round((tile["y"] * 512 - args.min_z) * size / span_z)
        right = round(((tile["x"] + 1) * 512 - args.min_x) * size / span_x)
        bottom = round(((tile["y"] + 1) * 512 - args.min_z) * size / span_z)
        if right <= 0 or bottom <= 0 or x >= size or z >= size:
            continue
        with Image.open(site / tile["file"]) as image:
            reduced = image.convert("RGBA").resize((right - x, bottom - z), Image.Resampling.NEAREST)
            combined.paste(reduced, (x, z), reduced.getchannel("A"))
    canvas = Image.new("RGB", (size * 2, size + header), "#17212b")
    canvas.paste(base, (0, header))
    canvas.paste(combined, (size, header))
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 16), f"BlueMap: X {args.min_x}..{args.max_x}; Z {args.min_z}..{args.max_z}", font=font, fill="white")
    draw.text((size + 18, 16), "JourneyMap exploration over BlueMap", font=font, fill="white")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.output, quality=92)
    args.output.with_suffix(".json").write_text(json.dumps({
        "image": str(args.image), "source_size": source_size,
        "bounds": {"min_x": args.min_x, "max_x": args.max_x, "min_z": args.min_z, "max_z": args.max_z},
        "journeymap_build": current["build_id"],
        "blocks_per_source_pixel": {"x": span_x / source_size[0], "z": span_z / source_size[1]},
        "preview_only": True}, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
