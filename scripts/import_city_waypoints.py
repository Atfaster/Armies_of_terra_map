#!/usr/bin/env python3
from __future__ import annotations

import argparse
import colorsys
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


WAYPOINT_PATTERN = re.compile(r"^(?P<slug>.+)_(?P<x>-?\d+)-(?P<y>-?\d+)-(?P<z>-?\d+)$")


@dataclass
class ParsedName:
    city_id: str
    label: str
    nation_id: str | None
    nation_name: str | None
    status: str
    tags: list[str]


def slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = slug.replace("_", "-")
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^0-9a-zа-яё-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-") or "city"


def prettify(value: str) -> str:
    text = value.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", text)


def maybe_fix_mojibake(value: str) -> str:
    if "Ð" not in value and "Ñ" not in value:
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value


def nation_color(nation_id: str) -> str:
    hue = (sum(ord(ch) for ch in nation_id) % 360) / 360.0
    red, green, blue = colorsys.hls_to_rgb(hue, 0.52, 0.65)
    return "#{:02x}{:02x}{:02x}".format(int(red * 255), int(green * 255), int(blue * 255))


def parse_waypoint_name(name: str) -> ParsedName:
    name = maybe_fix_mojibake(name)
    tokens = name.split("-")
    body = tokens[1:]
    status = "active"
    tags: list[str] = []
    nation_name = None
    nation_id = None

    filtered: list[str] = []
    index = 0
    while index < len(body):
        token = body[index]
        if token in {"ruin", "bankrupt"}:
            status = "ruins"
            tags.append(token)
            index += 1
            continue
        if token == "nation" and index + 1 < len(body):
            nation_name = prettify(body[index + 1])
            nation_id = slugify(body[index + 1])
            index += 2
            continue
        if token == "shop":
            tags.append("shop")
            index += 1
            continue
        filtered.append(token)
        index += 1

    label = prettify("-".join(filtered))
    city_id = slugify(label)

    if city_id in {"расприваченный-город", "unclaimed-city"}:
        status = "ruins"
        tags.append("unclaimed")

    return ParsedName(
        city_id=city_id,
        label=label,
        nation_id=nation_id,
        nation_name=nation_name,
        status=status,
        tags=sorted(set(tags)),
    )


def import_waypoints(source_dir: Path) -> dict:
    waypoint_files = sorted(source_dir.glob("town*.json"))
    if not waypoint_files:
        raise FileNotFoundError(f"No town*.json files found in {source_dir}")

    nations: dict[str, dict] = {}
    cities: list[dict] = []
    dates: set[str] = set()

    for waypoint_file in waypoint_files:
        payload = json.loads(waypoint_file.read_text(encoding="utf-8"))
        match = WAYPOINT_PATTERN.match(waypoint_file.stem)
        if not match:
            continue

        parsed = parse_waypoint_name(match.group("slug"))
        state_date = datetime.fromtimestamp(
            waypoint_file.stat().st_mtime, tz=timezone.utc
        ).date().isoformat()
        dates.add(state_date)

        if parsed.nation_id and parsed.nation_id not in nations:
            nations[parsed.nation_id] = {
                "id": parsed.nation_id,
                "name": parsed.nation_name,
                "color": nation_color(parsed.nation_id),
            }

        cities.append(
            {
                "id": parsed.city_id,
                "name": parsed.label,
                "kind": "city",
                "x": int(payload["x"]),
                "y": int(payload["z"]),
                "states": [
                    {
                        "from": state_date,
                        "to": None,
                        "status": parsed.status,
                        "nation_id": parsed.nation_id,
                        "label": parsed.label,
                        "tags": parsed.tags,
                        "source": {
                            "type": "journeymap-waypoint",
                            "file": waypoint_file.name,
                            "waypoint_id": payload.get("id"),
                        },
                    }
                ],
            }
        )

    ordered_dates = sorted(dates)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "timeline": {
            "dates": ordered_dates,
            "default_date": ordered_dates[-1],
        },
        "nations": sorted(nations.values(), key=lambda item: item["name"]),
        "cities": sorted(cities, key=lambda item: item["name"].lower()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    dataset = import_waypoints(args.source)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
