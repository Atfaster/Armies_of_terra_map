import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import season_data as d
import season_capture as c
import season_resources as resources
import season_log as log


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.baseline=self.root/'old/r.0.0.mca';self.current=self.root/'new/r.0.0.mca'
        self.cache=self.root/'cache';self.cache.mkdir()
        def col(name):return d.Tag(10,{'top_y':d.Tag(3,64),'block':d.Tag(10,{'Name':d.Tag(8,name)})})
        self.chunks={0:(123,d.Tag(10,{'0,0':col('minecraft:stone')})),16:(124,d.Tag(10,{'256,0':col('minecraft:dirt')}))}
        d.write_region(self.baseline,self.chunks);d.write_region(self.current,self.chunks)
    def tearDown(self):self.temp.cleanup()
    def worker(self,n):
        target=self.root/f'out{n}/r.0.0.mca'
        task={'source':str(self.current),'baseline':str(self.baseline),'destination':str(target),'cache':str(self.cache)}
        return c._worker(task),target
    def test_unchanged_chunks_skip_nbt_and_warm_baseline_is_cached(self):
        result,target=self.worker(1)
        self.assertEqual(result['skipped_chunks'],2);self.assertFalse(target.exists())
        with patch.object(d,'parse_nbt',side_effect=AssertionError('no NBT parsing on warm identical chunks')):
            result,target=self.worker(2)
        self.assertTrue(result['cached_baseline']);self.assertFalse(target.exists())
    def test_changed_columns_match_legacy_delta_bytes(self):
        self.chunks[0][1].value['0,0'].value['top_y']=d.Tag(3,65)
        d.write_region(self.current,self.chunks)
        result,target=self.worker(1)
        expected=self.root/'expected/r.0.0.mca'
        d.write_region(expected,{0:self.chunks[0]})
        self.assertEqual(target.read_bytes(),expected.read_bytes())
        self.assertEqual(result['columns'],1);self.assertEqual(result['skipped_chunks'],1)
    def test_corrupt_baseline_index_is_rebuilt(self):
        self.worker(1)
        next(self.cache.glob('baseline-*.json.gz')).write_bytes(b'broken')
        result,_=self.worker(2)
        self.assertFalse(result['cached_baseline']);self.assertEqual(result['skipped_chunks'],2)
    def test_parallel_capture_uses_frozen_records_and_skips_empty_files(self):
        baseline=self.root/'baseline';world=self.root/'world';records={};previous={}
        for rx in (0,1):
            name=f'overworld/cache/r.{rx}.0.mca'
            column=d.Tag(10,{'top_y':d.Tag(3,64)})
            d.write_region(baseline/name,{0:(123,d.Tag(10,{f'{rx*512},0':column}))})
            column.value['top_y']=d.Tag(3,65)
            d.write_region(world/name,{0:(123,d.Tag(10,{f'{rx*512},0':column}))})
        records=d.capture(world)
        records['overworld/cache/r.2.0.mca']={'payload':b'','sha256':d.sha(b''),'size':0}
        # Changes to live files after capture must not alter worker inputs.
        for path in world.rglob('*.mca'):path.write_bytes(b'live file changed')
        delta=self.root/'delta';delta.mkdir()
        with patch.object(c,'_root',return_value=self.cache):
            issues=c.capture_regions(self.root,{'surface_workers':2},records,baseline,delta,self.root,{},previous)
        self.assertEqual(issues,[])
        self.assertEqual(len(list(delta.rglob('*.mca'))),2)
        for path in delta.rglob('*.mca'):
            chunks=d.read_region(path)
            self.assertEqual(next(iter(chunks[0][1].value.values())).value['top_y'].value,65)
    def test_semantic_float_equality_matches_original(self):
        self.assertEqual(c.column_hash(d.Tag(10,{'v':d.Tag(6,-0.0)})),c.column_hash(d.Tag(10,{'v':d.Tag(6,0.0)})))
        self.assertIsNone(c.column_hash(d.Tag(10,{'v':d.Tag(6,float('nan'))})))
        self.assertNotEqual(c.column_hash(d.Tag(3,1)),c.column_hash(d.Tag(4,1)))
    def test_history_index_reuses_and_invalidates_changed_original(self):
        season=self.root/'season';source=season/'sources/a/one'
        raw=source/'original-extra/overworld/cache/r.0.0.mca';raw.parent.mkdir(parents=True);raw.write_bytes(b'one')
        d.save_json(source/'manifest.json',{'extras':[]})
        with patch.object(c,'_root',return_value=self.cache):
            first=c.retained_hashes(season,'a')
            with patch.object(d,'sha',wraps=d.sha) as calls:
                self.assertEqual(first,c.retained_hashes(season,'a'))
                self.assertFalse(any(args[0][0]==b'one' for args in calls.call_args_list))
            raw.write_bytes(b'changed-original')
            self.assertNotEqual(first,c.retained_hashes(season,'a'))
    def test_auto_cpu_target_memory_cap_and_manual_override(self):
        with patch.object(resources.os,'cpu_count',return_value=16),patch.object(resources,'available_memory',return_value=16*1024**3):
            self.assertEqual(resources.choose_workers({},99),12)
        with patch.object(resources.os,'cpu_count',return_value=16),patch.object(resources,'available_memory',return_value=2*1024**3):
            self.assertEqual(resources.choose_workers({},99),3)
            self.assertEqual(resources.choose_workers({'surface_workers':8},99),8)
    def test_success_errors_and_failed_stage_colours(self):
        stdout=io.StringIO();stderr=io.StringIO()
        with patch.dict(os.environ,{'SEASON_COLOR':'1'}),contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            log.success('done');log.error('failed')
            with self.assertRaises(ValueError):log.run_stage('Broken stage',lambda:(_ for _ in ()).throw(ValueError('test')))
        self.assertIn('\033[32m',stdout.getvalue());self.assertIn('\033[31m',stderr.getvalue())
        self.assertIn('Broken stage failed',stderr.getvalue())


if __name__=='__main__':unittest.main()
