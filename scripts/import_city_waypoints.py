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
RESERVED_TOKENS = {"nation", "ruin", "bankrupt", "shop", "capital"}
SNAPSHOT_KEYS = ("status", "nation_id", "label", "tags", "is_capital")
TIMELINE_DATE_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
DEFAULT_TIMELINE_DATE = "27.03.2026"


@dataclass
class ParsedName:
    city_id: str
    label: str
    nation_id: str | None
    nation_name: str | None
    status: str
    tags: list[str]
    is_capital: bool


def slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = slug.replace("_", "-")
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^0-9a-zа-яё-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-") or "city"


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


def parse_timeline_date(value: str) -> datetime:
    return datetime.strptime(value, "%d.%m.%Y")


def timeline_sort_key(value: str) -> tuple[int, int, int]:
    parsed = parse_timeline_date(value)
    return (parsed.year, parsed.month, parsed.day)


def tokenize_name(value: str) -> list[str]:
    normalized = maybe_fix_mojibake(value).replace("_", " ").replace("-", " ").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return [token for token in normalized.split(" ") if token]


def parse_waypoint_text(value: str) -> ParsedName:
    tokens = tokenize_name(value)
    if tokens and tokens[0].lower() == "town":
        tokens = tokens[1:]

    status = "active"
    tags: list[str] = []
    city_tokens: list[str] = []
    nation_tokens: list[str] = []
    is_capital = False

    index = 0
    while index < len(tokens):
        token = tokens[index]
        token_key = token.lower()

        if token_key == "ruin":
            status = "ruins"
            tags.append("ruin")
            index += 1
            continue

        if token_key == "bankrupt":
            status = "bankrupt"
            tags.append("bankrupt")
            index += 1
            continue

        if token_key == "shop":
            tags.append("shop")
            index += 1
            continue

        if token_key == "capital":
            is_capital = True
            index += 1
            continue

        if token_key == "nation":
            index += 1
            while index < len(tokens):
                candidate = tokens[index]
                if candidate.lower() in RESERVED_TOKENS:
                    break
                nation_tokens.append(candidate)
                index += 1
            continue

        city_tokens.append(token)
        index += 1

    label = " ".join(city_tokens).strip() or "Unnamed city"
    nation_name = " ".join(nation_tokens).strip() or None
    city_id = slugify(label)
    nation_id = slugify(nation_name) if nation_name else None

    if city_id in {"расприваченный-город", "unclaimed-city", "unknown"}:
        status = "ruins"
        tags.append("unclaimed")

    return ParsedName(
        city_id=city_id,
        label=label,
        nation_id=nation_id,
        nation_name=nation_name,
        status=status,
        tags=sorted(set(tags)),
        is_capital=is_capital,
    )


def parse_waypoint_name(file_slug: str, payload_name: str | None) -> ParsedName:
    sources = []
    if payload_name:
        sources.append(payload_name)
    sources.append(file_slug)

    for source in sources:
        parsed = parse_waypoint_text(source)
        if parsed.label != "Unnamed city":
            return parsed

    return parse_waypoint_text(file_slug)


def build_current_dataset(source_dir: Path, snapshot_date: str | None) -> dict:
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

        parsed = parse_waypoint_name(match.group("slug"), payload.get("name"))
        state_date = snapshot_date or datetime.fromtimestamp(
            waypoint_file.stat().st_mtime, tz=timezone.utc
        ).strftime("%d.%m.%Y")
        dates.add(state_date)

        if parsed.nation_id and parsed.nation_id not in nations:
            nations[parsed.nation_id] = {
                "id": parsed.nation_id,
                "name": parsed.nation_name,
                "color": nation_color(parsed.nation_id),
                "founded": None,
            }

        cities.append(
            {
                "id": parsed.city_id,
                "name": parsed.label,
                "founder": None,
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
                        "is_capital": parsed.is_capital,
                        "mayor": None,
                        "source": {
                            "type": "journeymap-waypoint",
                            "file": waypoint_file.name,
                            "waypoint_id": payload.get("id"),
                            "waypoint_name": payload.get("name"),
                        },
                    }
                ],
            }
        )

    ordered_dates = sorted(dates, key=timeline_sort_key)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "timeline": {
            "dates": ordered_dates,
            "default_date": ordered_dates[-1],
        },
        "nations": sorted(nations.values(), key=lambda item: item["name"] or item["id"]),
        "cities": sorted(cities, key=lambda item: item["name"].lower()),
    }


def load_existing_history(path: Path) -> dict:
    if not path.exists():
        return {
            "schema_version": 1,
            "generated_at": None,
            "timeline": {"dates": [], "default_date": None},
            "nations": [],
            "cities": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {
            "schema_version": 1,
            "generated_at": None,
            "timeline": {"dates": [], "default_date": None},
            "nations": [],
            "cities": [],
        }
    payload.setdefault("timeline", {"dates": [], "default_date": None})
    payload.setdefault("nations", [])
    payload.setdefault("cities", [])
    return payload


def state_payload(state: dict) -> dict:
    return {key: state.get(key) for key in SNAPSHOT_KEYS}


def same_city_snapshot(existing_city: dict, incoming_city: dict) -> bool:
    existing_state = existing_city["states"][-1]
    incoming_state = incoming_city["states"][0]
    return (
        existing_city.get("name") == incoming_city.get("name")
        and existing_city.get("kind") == incoming_city.get("kind")
        and existing_city.get("x") == incoming_city.get("x")
        and existing_city.get("y") == incoming_city.get("y")
        and state_payload(existing_state) == state_payload(incoming_state)
    )


def merge_history(existing: dict, current: dict, game_date: str) -> dict:
    existing_by_id = {city["id"]: city for city in existing.get("cities", [])}
    current_by_id = {city["id"]: city for city in current.get("cities", [])}
    merged_cities: list[dict] = []

    for city_id in sorted(set(existing_by_id) | set(current_by_id)):
        old_city = existing_by_id.get(city_id)
        new_city = current_by_id.get(city_id)

        if old_city and new_city:
            city = json.loads(json.dumps(old_city, ensure_ascii=False))
            last_state = city["states"][-1]
            if not same_city_snapshot(city, new_city):
                if last_state.get("to") is None and last_state.get("from") != game_date:
                    last_state["to"] = game_date
                if last_state.get("from") == game_date:
                    city["states"][-1] = new_city["states"][0]
                else:
                    city["states"].append(new_city["states"][0])
            city["name"] = new_city["name"]
            city["founder"] = old_city.get("founder")
            city["kind"] = new_city["kind"]
            city["x"] = new_city["x"]
            city["y"] = new_city["y"]
            merged_cities.append(city)
            continue

        if new_city:
            merged_cities.append(new_city)
            continue

        city = json.loads(json.dumps(old_city, ensure_ascii=False))
        last_state = city["states"][-1]
        if last_state.get("to") is None and last_state.get("from") != game_date:
            last_state["to"] = game_date
        merged_cities.append(city)

    nations: dict[str, dict] = {}
    for nation in existing.get("nations", []):
        nations[nation["id"]] = nation
    for nation in current.get("nations", []):
        existing_nation = nations.get(nation["id"], {})
        nations[nation["id"]] = {
            **nation,
            "founded": existing_nation.get("founded", nation.get("founded")),
        }

    timeline_dates = sorted(set(existing.get("timeline", {}).get("dates", [])) | {game_date}, key=timeline_sort_key)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "timeline": {
            "dates": timeline_dates,
            "default_date": game_date,
        },
        "nations": sorted(nations.values(), key=lambda item: item["name"] or item["id"]),
        "cities": sorted(merged_cities, key=lambda item: item["name"].lower()),
    }


def overlay_existing_metadata(existing: dict, current: dict) -> dict:
    existing_by_id = {city["id"]: city for city in existing.get("cities", [])}
    existing_nations_by_id = {nation["id"]: nation for nation in existing.get("nations", [])}

    for nation in current.get("nations", []):
        old_nation = existing_nations_by_id.get(nation["id"])
        if old_nation and old_nation.get("founded"):
            nation["founded"] = old_nation.get("founded")

    for city in current.get("cities", []):
        old_city = existing_by_id.get(city["id"])
        if not old_city:
            continue

        if old_city.get("founder"):
            city["founder"] = old_city.get("founder")

        incoming_state = city["states"][0]
        for old_state in old_city.get("states", []):
            if (
                old_state.get("from") == incoming_state.get("from")
                and state_payload(old_state) == state_payload(incoming_state)
            ):
                incoming_state["mayor"] = old_state.get("mayor")
                break

    return current


def normalize_timeline_date(game_date: str | None) -> str:
    value = (game_date or DEFAULT_TIMELINE_DATE).strip()
    if not TIMELINE_DATE_PATTERN.fullmatch(value):
        raise SystemExit("Timeline date must use dd.mm.yyyy, for example 27.03.2026")
    try:
        parse_timeline_date(value)
    except ValueError as error:
        raise SystemExit(f"Invalid timeline date: {value}") from error
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--game-date", dest="game_date")
    parser.add_argument(
        "--history",
        action="store_true",
        help="Merge the current waypoint snapshot into historical city data instead of replacing it.",
    )
    args = parser.parse_args()

    effective_game_date = normalize_timeline_date(args.game_date)
    current = build_current_dataset(args.source, effective_game_date)
    dataset = current

    existing = load_existing_history(args.output)

    if args.history:
        dataset = merge_history(existing, current, effective_game_date)
    else:
        dataset = overlay_existing_metadata(existing, current)

    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
