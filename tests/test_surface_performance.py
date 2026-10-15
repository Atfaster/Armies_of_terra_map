import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import season_data as data
import season_surface as surface


class SurfacePerformanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.season = self.root / 'season'
        self.season.mkdir()
        self.config = {'source_order': ['a'], 'surface_workers': 1}
        self.inputs = []
        self.number = 0
        for rx in (0, 1):
            snapshot = self.season / 'sources/a/one'
            path = snapshot / f'extra/overworld/cache/r.{rx}.0.mca'
            column = data.Tag(10, {'top_y': data.Tag(3, 64),
                'blockstates': data.Tag(10, {'64': data.Tag(10, {'Name': data.Tag(8, 'minecraft:stone')})})})
            data.write_region(path, {0: (123, data.Tag(10, {f'{rx * 512},0': column}))})
            proof = snapshot / f'day/{rx},0.png'
            proof.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGBA', (512, 512), (1, 2, 3, 255)).save(proof)
            self.inputs.append((path.name, (1, 0, 'one'), path, 'a'))

    def tearDown(self):
        self.temp.cleanup()

    def build(self, config=None, final_colour=(1, 2, 3, 255)):
        self.number += 1
        staged = self.root / f'build-{self.number}'
        site = staged / 'site'
        (site / 'tiles').mkdir(parents=True)
        for rx in (0, 1):
            Image.new('RGBA', (512, 512), final_colour).save(site / f'tiles/{rx},0.png')
        grouped = {name: [(priority, path, source)] for name, priority, path, source in self.inputs}
        output = staged / 'team-data'
        output.mkdir()
        web = site / 'surface'
        web.mkdir()
        with contextlib.redirect_stdout(io.StringIO()) as log, \
             patch.object(surface.tempfile, 'gettempdir', return_value=str(self.root)):
            result = surface.merge_regions(self.season, config or self.config, grouped, output, web, site)
        digests = {p.relative_to(staged).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for parent in (output, web) for p in parent.rglob('*') if p.is_file()}
        return result, digests, log.getvalue()

    def test_warm_cache_is_identical_without_parsing(self):
        first = self.build()
        with patch.object(data, 'read_region', side_effect=AssertionError('cache must not parse NBT')):
            second = self.build()
        self.assertEqual(first[:2], second[:2])
        self.assertIn('2 cached, 0 rebuilt', second[2])

    def test_parallel_uncached_matches_serial_bytes(self):
        serial = self.build()
        parallel = self.build({**self.config, 'surface_workers': 2, 'surface_cache': False})
        self.assertEqual(serial[:2], parallel[:2])
        self.assertIn('0 cached, 2 rebuilt, 2 workers', parallel[2])

    def test_input_change_invalidates_only_affected_region(self):
        self.build()
        path = self.inputs[0][2]
        chunks = data.read_region(path)
        chunks[0][1].value['0,0'].value['top_y'] = data.Tag(3, 65)
        data.write_region(path, chunks)
        result = self.build()
        self.assertIn('1 cached, 1 rebuilt', result[2])

    def test_priority_change_invalidates_region(self):
        self.build()
        name, priority, path, source = self.inputs[0]
        self.inputs[0] = (name, (2, 0, 'one'), path, source)
        self.assertIn('1 cached, 1 rebuilt', self.build()[2])

    def test_final_image_change_invalidates_and_excludes_mismatch(self):
        self.build()
        result = self.build(final_colour=(4, 5, 6, 255))
        self.assertEqual(result[0], ({}, 0, 2))
        self.assertIn('0 cached, 2 rebuilt', result[2])

    def test_proof_change_invalidates_only_affected_region(self):
        self.build()
        Image.new('RGBA', (512, 512), (4, 5, 6, 255)).save(self.season / 'sources/a/one/day/0,0.png')
        result = self.build()
        self.assertEqual(result[0][1:], (1, 1))
        self.assertIn('1 cached, 1 rebuilt', result[2])

    def test_corrupted_output_is_rebuilt(self):
        first = self.build()
        cached = next((self.root / 'steel-soil-surface-cache').rglob('region.mca'))
        cached.write_bytes(b'corrupted')
        second = self.build()
        self.assertEqual(first[:2], second[:2])
        self.assertIn('1 cached, 1 rebuilt', second[2])

    def test_corrupted_manifest_is_rebuilt(self):
        first = self.build()
        cached = next((self.root / 'steel-soil-surface-cache').rglob('entry.json'))
        entry = json.loads(cached.read_text())
        entry['columns'] += 1
        cached.write_text(json.dumps(entry))
        second = self.build()
        self.assertEqual(first[:2], second[:2])
        self.assertIn('1 cached, 1 rebuilt', second[2])

    def test_invalid_worker_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'surface_workers'):
            self.build({**self.config, 'surface_workers': 0})


if __name__ == '__main__':
    unittest.main()
