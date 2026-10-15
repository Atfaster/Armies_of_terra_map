import unittest,sys,tempfile
from pathlib import Path
from zipfile import ZipFile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import season_archive as a
class LayoutTest(unittest.TestCase):
 def inspect(self,files):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'map.zip'
   with ZipFile(p,'w') as z:
    for name,data in files:z.writestr(name,data)
   with ZipFile(p) as z:return a.inspect(z)
 def test_nested_world_folder_and_other_layers(self):
  items,report=self.inspect([('JourneyMap/mp/Steel~Soil/overworld/day/0,0.png',b'p'),('JourneyMap/mp/Steel~Soil/overworld/cache/r.0.0.mca',b'c'),('JourneyMap/mp/Steel~Soil/addon-data/minecolonies/overworld/border.json',b'[]'),('JourneyMap/mp/Steel~Soil/overworld/night/0,0.png',b'n'),('JourneyMap/mp/Steel~Soil/DIM-1/day/0,0.png',b'n')])
  self.assertEqual(report['cache_files'],1);self.assertEqual(report['ignored_files'],2);self.assertEqual(len(items),3)
 def test_flat_surface_export_like_sinshluhi(self):
  _,r=self.inspect([('day/0,0.png',b'p'),('cache/r.0.0.mca',b'c'),('border.json',b'[]')]);self.assertEqual(r['cache_files'],1);self.assertEqual(r['border_file'],'border.json');self.assertFalse(r['warnings'])
 def test_flat_files_and_case_insensitive_folder_names(self):
  _,r=self.inspect([('0,0.PNG',b'p'),('r.0.0.mca',b'c'),('border.json',b'[]')]);self.assertEqual(r['day_folder'],'.');self.assertEqual(r['cache_files'],1)
  _,r=self.inspect([('Export/Overworld/Day/0,0.PNG',b'p'),('Export/Overworld/Cache/r.0.0.mca',b'c'),('Export/addon-data/minecolonies/overworld/border.json',b'[]')]);self.assertEqual(r['cache_files'],1)
 def test_multiple_worlds_are_not_guessed(self):
  with self.assertRaises(a.ArchiveLayoutError):self.inspect([('old/day/0,0.png',b'p'),('new/overworld/day/0,0.png',b'q')])
 def test_duplicate_border_data_must_agree(self):
  _,r=self.inspect([('day/0,0.png',b'p'),('border.json',b'[]'),('addon-data/minecolonies/overworld/border.json',b'[]')]);self.assertEqual(r['identical_duplicates'],1)
  with self.assertRaises(a.ArchiveLayoutError):self.inspect([('day/0,0.png',b'p'),('border.json',b'[]'),('addon-data/minecolonies/overworld/border.json',b'[1]')])
 def test_unsafe_archive_and_night_only_are_rejected(self):
  for paths in ([('../day/0,0.png',b'p')],[('night/0,0.png',b'p')],[('C:/day/0,0.png',b'p')]):
   with self.assertRaises(a.ArchiveLayoutError):self.inspect(paths)
if __name__=='__main__':unittest.main()
