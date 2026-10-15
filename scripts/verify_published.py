"""Wait for Pages deployment and verify the actual public ZIP, not just Git refs."""
import argparse
import hashlib
import json
import time
from urllib.request import Request, urlopen

BASE = "https://atfaster.github.io/terrafirmacolonies-map/"


def verify(release, timeout=240):
    from pathlib import Path
    expected = json.loads((Path(release) / "site/downloads/latest.json").read_text(encoding="utf-8"))
    version = "?v=" + expected["build_id"]
    deadline = time.monotonic() + timeout
    last_error = "Pages has not served this release yet"
    while time.monotonic() < deadline:
        try:
            with urlopen(Request(BASE + "downloads/latest.json" + version,
                                 headers={"Cache-Control": "no-cache"}), timeout=20) as response:
                actual = json.load(response)
            if actual != expected:
                raise ValueError("Pages still serves a different release")
            sha, size = hashlib.sha256(), 0
            with urlopen(expected.get("url") or BASE + "downloads/" + expected["file"] + version, timeout=30) as response:
                while chunk := response.read(1048576):
                    sha.update(chunk)
                    size += len(chunk)
            if sha.hexdigest() != expected["sha256"] or size != expected["bytes"]:
                raise ValueError("Public ZIP checksum or size differs")
            report = json.loads((Path(release) / "report.json").read_text(encoding="utf-8"))
            for name in ("index.html", "download.html", "team-data.js", "colonies.json", "surface/index.json"):
                if name not in report["sha256"]: continue
                with urlopen(BASE + name + version, timeout=20) as response:
                    if hashlib.sha256(response.read()).hexdigest() != report["sha256"][name]:
                        raise ValueError("Public website differs: " + name)
            surface_path = Path(release) / "site/surface/index.json"
            if surface_path.exists():
                surface = json.loads(surface_path.read_text(encoding="utf-8"))
                if surface["tiles"]:
                    name = "surface/" + next(iter(surface["tiles"].values()))
                    with urlopen(BASE + name + version, timeout=20) as response:
                        if hashlib.sha256(response.read()).hexdigest() != report["sha256"][name]:
                            raise ValueError("Public surface region differs")
            print("Public ZIP verified: " + (expected.get("url") or BASE + "downloads/" + expected["file"]), flush=True)
            print(json.dumps(expected, indent=2), flush=True)
            return
        except (OSError, ValueError) as error:
            last_error = str(error)
            time.sleep(5)
    raise RuntimeError("Public download verification timed out: " + last_error)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    verify(args.release, args.timeout)
