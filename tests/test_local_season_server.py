import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location("local_map_server", Path(__file__).resolve().parents[1] / "scripts/local_map_server.py")
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


class ServerTest(unittest.TestCase):
    def test_save_and_reload_use_season_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            site = root / "site"
            source = root / "source"
            site.mkdir()
            source.mkdir()
            server_module.ROOT = site
            server_module.GEOGRAPHY_PATH = source / "geography.json"
            server_module.CITIES_PATH = source / "cities.json"
            server = ThreadingHTTPServer(("127.0.0.1", 0), server_module.LocalMapRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                payloads = {"geography": {"type": "FeatureCollection", "features": []},
                            "cities": {"cities": [], "nations": [], "timeline": {"dates": []}}}
                for kind, payload in payloads.items():
                    request = Request(base + "/api/save-" + kind,
                                      data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        self.assertTrue(json.load(response)["ok"])
                    with urlopen(base + "/data/" + kind + ".json") as response:
                        self.assertEqual(json.load(response), payload)
                    self.assertEqual(json.loads((source / (kind + ".json")).read_text()), payload)
                self.assertFalse((site / "data").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
