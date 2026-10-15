import unittest,tempfile,json,sys,gzip,struct
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import season_data as d
import build_season as b

class DataTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.season=self.root/'season';self.season.mkdir()
  self.config={'source_order':['a','b'],'tile_size':512,'dimension':'overworld','layer':'day'}
 def tearDown(self):self.tmp.cleanup()
 def column(self,name='tfc:grass/loam',y=63):
  return d.Tag(10,{'top_y':d.Tag(3,y),'biome_name':d.Tag(8,'tfc:low_canyons'),'blockstates':d.Tag(10,{str(y):d.Tag(10,{'Name':d.Tag(8,name),'Properties':d.Tag(10,{'snowy':d.Tag(8,'false')})})})})
 def snapshot(self,source,key,priority,columns=None,border=None,ops=None):
  target=self.season/'sources'/source/key;extras=[]
  if columns is not None:
   name='overworld/cache/r.-1.-1.mca';path=target/'extra'/name
   d.write_region(path,{1023:(123,d.Tag(10,{'pos':d.Tag(11,[-1,-1]),**columns}))})
   extras.append({'name':name,'mtime_ns':priority,'sha256':d.sha(path.read_bytes())})
  if border is not None or ops is not None:
   name='addon-data/minecolonies/overworld/border.json' if border is not None else 'border-operations.json';path=target/'extra'/name
   d.save_json(path,border if border is not None else ops)
   extras.append({'name':name,'mtime_ns':priority,'sha256':d.sha(path.read_bytes())})
  d.save_json(target/'manifest.json',{'files':[],'extras':extras,'source_id':source})
 def build(self):
  staged=self.root/'build';site=staged/'site';site.mkdir(parents=True)
  report=d.build(self.season,self.config,staged,site);return staged,site,report
 def test_roundtrip_negative_coordinates_properties_and_web_identity(self):
  col=self.column();self.snapshot('a','one',1,{'-1,-1':col})
  staged,site,report=self.build();cache=d.read_region(staged/'team-data/overworld/cache/r.-1.-1.mca')
  self.assertEqual(cache[1023][1].value['-1,-1'],col)
  data=json.loads(gzip.decompress((site/'surface/-1,-1.json.gz').read_bytes()))
  self.assertEqual(data['cells'],[[262143,0]])
  self.assertEqual(data['palette'][0],['tfc:grass/loam',{'snowy':'false'},63,'tfc:low_canyons',None])
  self.assertEqual(report['surface_columns'],1)
 def test_unchanged_rewritten_region_does_not_override_other_player(self):
  self.snapshot('a','old',1,{'-1,-1':self.column(),' -2,-1'.strip():self.column()})
  self.snapshot('b','new',2,{'-1,-1':self.column('minecraft:stone')})
  self.snapshot('a','rewrite',3,{'-1,-1':self.column(),'-2,-1':self.column('minecraft:sand')})
  staged,_,_=self.build();cache=d.read_region(staged/'team-data/overworld/cache/r.-1.-1.mca')
  self.assertEqual(d.surface_record(cache[1023][1].value['-1,-1'])[0],'minecraft:stone')
  self.assertEqual(d.surface_record(cache[1023][1].value['-2,-1'])[0],'minecraft:sand')
 def test_partial_colonies_merge_and_explicit_release_and_ownership_change(self):
  colony=lambda n,chunks:{'dimension':'minecraft:overworld','id':n,'colony_name':'Colony '+str(n),'chunks':chunks}
  self.snapshot('a','first',1,border=[colony(1,[1,2])])
  self.snapshot('b','second',2,border=[colony(1,[3]),colony(2,[4])])
  self.snapshot('a','updated',3,border=[colony(1,[1])])
  self.snapshot('b','changed-owner',4,ops=[{'colony':{k:v for k,v in colony(2,[]).items() if k!='chunks'},'add':[1],'remove':[]}])
  staged,site,_=self.build();data=json.loads((staged/'team-data/addon-data/minecolonies/overworld/border.json').read_text())
  self.assertEqual(data[0]['chunks'],[3]);self.assertEqual(data[1]['chunks'],[1,4])
  self.assertEqual(len(json.loads((site/'colonies.json').read_text())[0]['edges']),4)
  self.assertEqual(d.unpack_chunk(0xffffffffffffffff),(-1,-1))
 def test_whole_overworld_import_ignores_caves_and_keeps_cache(self):
  image=self.root/'tile.png';Image.new('RGBA',(512,512),(1,2,3,255)).save(image)
  region=self.root/'r.-1.-1.mca';d.write_region(region,{1023:(123,d.Tag(10,{'-1,-1':self.column()}))})
  archive=self.root/'a.zip'
  with ZipFile(archive,'w') as z:
   z.write(image,'overworld/day/-1,-1.png');z.write(image,'overworld/night/-1,-1.png');z.write(image,'overworld/3/-1,-1.png');z.write(region,'overworld/cache/r.-1.-1.mca')
   z.writestr('addon-data/minecolonies/overworld/border.json','[]')
  b.snapshot_zip(self.season,'a',archive,self.config)
  manifests=list((self.season/'sources/a').glob('*/manifest.json'));data=json.loads(manifests[0].read_text())
  self.assertEqual(len(data['files']),1);self.assertEqual(len(data['extras']),2)
 def test_truncated_and_external_region_rejected(self):
  path=self.root/'r.0.0.mca';path.write_bytes(b'bad')
  with self.assertRaises(ValueError):d.read_region(path)
  header=bytearray(8192);struct.pack_into('>I',header,0,(2<<8)|1)
  path.write_bytes(header+struct.pack('>I',2)+b'\x82x')
  with self.assertRaises(ValueError):d.read_region(path)
 def test_capture_delta_and_second_install_do_not_promote_merged_data(self):
  world=self.root/'world';(world/'overworld/day').mkdir(parents=True)
  cache=world/'overworld/cache/r.-1.-1.mca';d.write_region(cache,{1023:(123,d.Tag(10,{'-1,-1':self.column()}))})
  d.save_json(self.season/'client.json',{'source_id':'a','day_folder':str(world/'overworld/day')})
  temp=self.root/'temp';temp.mkdir();plan=d.prepare_client(self.season,self.config,temp)
  staged,site,report=self.build();release=self.season/'builds'/('a'*20);release.parent.mkdir();staged.rename(release)
  d.install(plan,release,self.season,lambda:None)
  second=self.root/'second';second.mkdir();d.prepare_client(self.season,self.config,second)
  self.assertEqual(len(list((self.season/'sources/a').glob('*/manifest.json'))),1)
  chunks=d.read_region(cache);chunks[1023][1].value['-1,-1']=self.column('minecraft:stone');d.write_region(cache,chunks)
  third=self.root/'third';third.mkdir();d.prepare_client(self.season,self.config,third)
  self.assertEqual(len(list((self.season/'sources/a').glob('*/manifest.json'))),2)
 def test_race_refuses_metadata_install_and_recovery_preserves_new_research(self):
  world=self.root/'world';(world/'overworld/day').mkdir(parents=True)
  border=world/'addon-data/minecolonies/overworld/border.json';d.save_json(border,[])
  plan={'world':world,'records':d.capture(world)}
  release=self.season/'builds'/('a'*20);d.save_json(release/'team-data/addon-data/minecolonies/overworld/border.json',[{'id':1,'dimension':'minecraft:overworld','chunks':[1]}])
  d.save_json(border,[{'id':2,'dimension':'minecraft:overworld','chunks':[2]}])
  with self.assertRaises(RuntimeError):d.install(plan,release,self.season,lambda:None)
  plan={'world':world,'records':d.capture(world)}
  d.save_json(self.season/'client.json',{'source_id':'a','day_folder':str(world/'overworld/day')})
  real=d.os.replace
  def fail(src,dst):
   if Path(dst)==border:raise OSError('interrupted')
   return real(src,dst)
  with patch.object(d.os,'replace',side_effect=fail):
   with self.assertRaises(OSError):d.install(plan,release,self.season,lambda:None)
  self.assertEqual(json.loads((self.season/'extra_client_state.json').read_text())['status'],'interrupted')
  d.save_json(border,[{'id':3,'dimension':'minecraft:overworld','chunks':[3]}])
  with self.assertRaises(RuntimeError):d.recover(self.season,lambda:None)

 def test_misplaced_original_columns_are_written_into_their_actual_chunk(self):
  target=self.season/'sources/a/one';path=target/'extra/overworld/cache/r.-1.-1.mca'
  d.write_region(path,{990:(123,d.Tag(10,{'pos':d.Tag(4,123),'-1,-1':self.column()}))})
  d.save_json(target/'manifest.json',{'files':[],'extras':[{'name':'overworld/cache/r.-1.-1.mca','mtime_ns':1,'sha256':d.sha(path.read_bytes())}]})
  staged,_,_=self.build();chunks=d.read_region(staged/'team-data/overworld/cache/r.-1.-1.mca')
  self.assertEqual(list(chunks),[1023]);self.assertEqual(d.unpack_chunk(chunks[1023][1].value['pos'].value),(-1,-1))
 def test_block_information_is_not_paired_with_a_different_surface_image(self):
  self.snapshot('a','one',1,{'-1,-1':self.column()})
  proof=self.season/'sources/a/one/day/-1,-1.png';proof.parent.mkdir();Image.new('RGBA',(512,512),(1,2,3,255)).save(proof)
  staged=self.root/'build';site=staged/'site';(site/'tiles').mkdir(parents=True)
  Image.new('RGBA',(512,512),(5,6,7,255)).save(site/'tiles/-1,-1.png')
  report=d.build(self.season,self.config,staged,site)
  self.assertEqual(report['surface_columns'],0);self.assertEqual(report['unmatched_image_columns'],1)
 def test_salvage_reports_bad_chunk_and_keeps_valid_chunk(self):
  path=self.root/'r.-1.-1.mca'
  d.write_region(path,{1022:(123,d.Tag(10,{'-17,-1':self.column()})),1023:(124,d.Tag(10,{'-1,-1':self.column()}))})
  data=bytearray(path.read_bytes());offset=struct.unpack_from('>I',data,1023*4)[0]>>8
  struct.pack_into('>I',data,offset*4096,0xffffffff);path.write_bytes(data)
  with self.assertRaises(ValueError):d.read_region(path)
  issues=[];chunks=d.read_region(path,issues)
  self.assertEqual(list(chunks),[1022]);self.assertEqual(issues[0]['slot'],1023)
 def test_archive_retention_preserves_original_and_salvages_valid_chunks(self):
  world=self.root/'incoming';path=world/'overworld/cache/r.-1.-1.mca'
  d.write_region(path,{1022:(123,d.Tag(10,{'-17,-1':self.column()})),1023:(124,d.Tag(10,{'-1,-1':self.column()}))})
  data=bytearray(path.read_bytes());offset=struct.unpack_from('>I',data,1023*4)[0]>>8
  struct.pack_into('>I',data,offset*4096,0xffffffff);path.write_bytes(data)
  target=self.root/'retained';issues=[]
  with self.assertRaises(ValueError):d.retain_extras(target,world)
  records=d.retain_extras(target,world,issues=issues)
  repaired=target/'extra/overworld/cache/r.-1.-1.mca'
  self.assertEqual(list(d.read_region(repaired)),[1022])
  self.assertEqual((target/'original-extra/overworld/cache/r.-1.-1.mca').read_bytes(),bytes(data))
  self.assertEqual(records[0]['sha256'],d.sha(repaired.read_bytes()))
  self.assertEqual(records[0]['size'],repaired.stat().st_size)
  self.assertEqual(issues[0]['slot'],1023)
 def test_recovery_finishes_a_partial_metadata_install(self):
  world=self.root/'world';(world/'overworld/day').mkdir(parents=True)
  border=world/'addon-data/minecolonies/overworld/border.json';d.save_json(border,[])
  d.save_json(self.season/'client.json',{'source_id':'a','day_folder':str(world/'overworld/day')})
  release=self.season/'builds'/('a'*20);d.save_json(release/'team-data/addon-data/minecolonies/overworld/border.json',[{'id':1,'dimension':'minecraft:overworld','chunks':[1]}])
  real=d.os.replace
  def fail(src,dst):
   if Path(dst)==border:raise OSError('interrupted')
   return real(src,dst)
  with patch.object(d.os,'replace',side_effect=fail):
   with self.assertRaises(OSError):d.install({'world':world,'records':d.capture(world)},release,self.season,lambda:None)
  d.recover(self.season,lambda:None)
  self.assertEqual(json.loads((self.season/'extra_client_state.json').read_text())['status'],'complete')
  self.assertEqual(border.read_bytes(),(release/'team-data/addon-data/minecolonies/overworld/border.json').read_bytes())

 def test_large_download_uses_one_release_asset_without_large_git_file(self):
  staged=self.root/'release';site=staged/'site';site.mkdir(parents=True)
  d.save_json(staged/'team-data'/d.BORDER,[])
  with patch.object(b,'DIRECT_ZIP_LIMIT',1):
   archive=b.package_download(staged,site,[],'a'*20,'instructions')
  info=json.loads((site/'downloads/latest.json').read_text())
  self.assertEqual(info['storage'],'github-release');self.assertIn('map-'+('a'*20),info['url'])
  self.assertFalse((site/'downloads'/b.DOWNLOAD_NAME).exists())
  with ZipFile(archive) as z:self.assertEqual(z.read(d.BORDER),b'[]')

if __name__=='__main__':unittest.main()
