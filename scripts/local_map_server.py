from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
GEOGRAPHY_PATH = ROOT / "data" / "geography.json"
CITIES_PATH = ROOT / "data" / "cities.json"


class LocalMapRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/save-geography":
            self.handle_save_geography()
            return

        if self.path == "/api/save-cities":
            self.handle_save_cities()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def read_json_body(self) -> dict:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
            raise

        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON payload: {error}")
            raise

    def send_json_response(self, payload: dict) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def handle_save_geography(self) -> None:
        try:
            payload = self.read_json_body()
        except Exception:
            return

        if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
            self.send_error(HTTPStatus.BAD_REQUEST, "Payload must be a GeoJSON FeatureCollection")
            return

        GEOGRAPHY_PATH.parent.mkdir(parents=True, exist_ok=True)
        GEOGRAPHY_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.send_json_response(
            {
                "ok": True,
                "path": str(GEOGRAPHY_PATH.relative_to(ROOT)).replace("\\", "/"),
            }
        )

    def handle_save_cities(self) -> None:
        try:
            payload = self.read_json_body()
        except Exception:
            return

        if not isinstance(payload, dict):
            self.send_error(HTTPStatus.BAD_REQUEST, "Payload must be a JSON object")
            return
        if not isinstance(payload.get("cities"), list):
            self.send_error(HTTPStatus.BAD_REQUEST, "Payload must contain a cities array")
            return
        if not isinstance(payload.get("nations"), list):
            self.send_error(HTTPStatus.BAD_REQUEST, "Payload must contain a nations array")
            return
        if not isinstance(payload.get("timeline"), dict):
            self.send_error(HTTPStatus.BAD_REQUEST, "Payload must contain a timeline object")
            return

        CITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        CITIES_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.send_json_response(
            {
                "ok": True,
                "path": str(CITIES_PATH.relative_to(ROOT)).replace("\\", "/"),
            }
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the Armies of Terra map locally with direct geography and city saving."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), LocalMapRequestHandler)
    print(f"Serving {ROOT} at http://{args.host}:{args.port}")
    print(f"Direct geography save endpoint: http://{args.host}:{args.port}/api/save-geography")
    print(f"Direct city save endpoint: http://{args.host}:{args.port}/api/save-cities")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
