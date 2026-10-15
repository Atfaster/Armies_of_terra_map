"""JourneyMap 5.10.3 surface NBT, MineColonies observations and safe client updates.
Only overworld surface cache and colony borders are shared. No world terrain.
"""
from __future__ import annotations
import gzip, hashlib, io, json, math, os, re, struct, time, uuid, zlib
from dataclasses import dataclass
from pathlib import Path
from PIL import Image

MCA = re.compile(r'^r\.(-?\d+)\.(-?\d+)\.mca$')
COLUMN = re.compile(r'^(-?\d+),(-?\d+)$')
MAX_NBT = 32 * 1024 * 1024
BORDER = 'addon-data/minecolonies/overworld/border.json'
_NBT_NUMBERS = {fmt:struct.Struct('>'+fmt) for fmt in ('b','h','i','q','f','d','B','H')}
_NBT_SCALAR_FORMATS = ('','b','h','i','q','f','d')

@dataclass
class Tag:
    kind: int
    value: object

def sha(payload):
    return hashlib.sha256(payload).hexdigest()

def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))

def parse_nbt(payload):
    stream = io.BytesIO(payload)
    def take(n):
        if n < 0 or n > MAX_NBT: raise ValueError('Invalid NBT length')
        b = stream.read(n)
        if len(b) != n: raise ValueError('Truncated NBT')
        return b
    def number(fmt):
        reader = _NBT_NUMBERS[fmt]
        return reader.unpack(take(reader.size))[0]
    def text(): return take(number('H')).decode('utf-8')
    def value(kind, depth=0):
        if depth > 64: raise ValueError('NBT nesting too deep')
        if 1 <= kind <= 6: return number(_NBT_SCALAR_FORMATS[kind])
        if kind == 7: return take(number('i'))
        if kind == 8: return text()
        if kind == 9:
            typ, length = number('B'), number('i')
            if not 0 <= length <= 1_000_000: raise ValueError('Invalid NBT list')
            return (typ, [value(typ, depth+1) for _ in range(length)])
        if kind == 10:
            out = {}
            while True:
                typ = number('B')
                if typ == 0: return out
                key = text()
                if key in out: raise ValueError('Duplicate NBT field')
                out[key] = Tag(typ, value(typ, depth+1))
        if kind in (11, 12):
            length = number('i')
            if not 0 <= length <= 1_000_000: raise ValueError('Invalid NBT array')
            return [number('i' if kind == 11 else 'q') for _ in range(length)]
        raise ValueError('Unsupported NBT tag')
    kind = number('B'); name = text()
    if kind != 10: raise ValueError('Chunk must be a compound')
    result = Tag(kind, value(kind))
    if stream.read(1): raise ValueError('Trailing NBT bytes')
    return result

def encode_nbt(root):
    def text(s):
        b=s.encode('utf-8')
        return struct.pack('>H',len(b))+b
    def val(kind, v):
        if 1 <= kind <= 6: return _NBT_NUMBERS[_NBT_SCALAR_FORMATS[kind]].pack(v)
        if kind==7: return struct.pack('>i',len(v))+v
        if kind==8: return text(v)
        if kind==9: return bytes([v[0]])+struct.pack('>i',len(v[1]))+b''.join(val(v[0],a) for a in v[1])
        if kind==10: return b''.join(bytes([t.kind])+text(k)+val(t.kind,t.value) for k,t in sorted(v.items()))+b'\0'
        if kind in (11,12): return struct.pack('>i',len(v))+b''.join(struct.pack('>i' if kind==11 else '>q',a) for a in v)
        raise ValueError('Unsupported tag')
    return b'\x0a\0\0'+val(10,root.value)

def plain(tag):
    if tag.kind==10: return {k:plain(v) for k,v in tag.value.items()}
    if tag.kind==9: return tag.value[1]
    return tag.value

def bounded_decompress(payload, codec):
    if codec == 3:
        if len(payload)>MAX_NBT: raise ValueError('NBT too large')
        return payload
    if codec not in (1,2): raise ValueError('Unsupported MCA compression / external chunk')
    d=zlib.decompressobj(31 if codec==1 else 15)
    out=d.decompress(payload,MAX_NBT+1)
    if len(out)>MAX_NBT or not d.eof or d.unused_data: raise ValueError('Invalid or oversized compressed NBT')
    return out

def read_region(path, issues=None, skip_payloads=None):
    match=MCA.fullmatch(path.name)
    if not match: raise ValueError('Invalid region name')
    rx,rz=map(int,match.groups()); data=path.read_bytes()
    if not data: return {}
    if len(data)<8192: raise ValueError('Truncated region header')
    offsets=struct.unpack('>1024I',data[:4096]); times=struct.unpack('>1024I',data[4096:8192])
    chunks={}; occupied=set()
    for i,offset in enumerate(offsets):
        if not offset: continue
        try:
            sector,count=offset>>8,offset&255
            if sector<2 or not count: raise ValueError('Invalid region offset')
            sectors=set(range(sector,sector+count))
            if occupied.intersection(sectors): raise ValueError('Overlapping region chunks')
            occupied.update(sectors)
            pos=sector*4096
            if pos+5>len(data): raise ValueError('Missing region payload')
            length=struct.unpack('>I',data[pos:pos+4])[0]
            if length<1 or length>count*4096-4 or pos+4+length>len(data): raise ValueError('Invalid chunk length')
            if skip_payloads and skip_payloads.get(i)==sha(data[pos+4:pos+4+length]):
                continue
            root=parse_nbt(bounded_decompress(data[pos+5:pos+4+length],data[pos+4]))
            cx,cz=rx*32+i%32,rz*32+i//32
            for key,tag in root.value.items():
                m=COLUMN.fullmatch(key)
                if m:
                    x,z=map(int,m.groups())
                    if tag.kind!=10 or x//512!=rx or z//512!=rz: raise ValueError('Column outside region')
            chunks[i]=(times[i],root)
        except (ValueError, zlib.error, UnicodeError, struct.error) as error:
            if issues is None: raise
            issues.append({'region':path.name,'slot':i,'error':str(error)})
    return chunks

def write_region(path,chunks):
    header=bytearray(8192); body=bytearray(); sector=2
    for i,(timestamp,root) in sorted(chunks.items()):
        compressed=zlib.compress(encode_nbt(root),6)
        payload=struct.pack('>I',len(compressed)+1)+b'\x02'+compressed
        count=math.ceil(len(payload)/4096)
        if count>255: raise ValueError('External chunks not supported')
        struct.pack_into('>I',header,i*4,(sector<<8)|count)
        struct.pack_into('>I',header,4096+i*4,timestamp)
        body.extend(payload); body.extend(b'\0'*(count*4096-len(payload))); sector+=count
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(header+body)

def validate_border(path):
    items=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(items,list): raise ValueError('MineColonies border must be a list')
    result=[];keys=set()
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('id'),int) or not isinstance(item.get('dimension'),str): raise ValueError('Invalid colony identity')
        key=(item['dimension'],item['id'])
        if key in keys: raise ValueError('Duplicate colony ID')
        keys.add(key)
        chunks=item.get('chunks',[])
        if not isinstance(chunks,list) or any(type(n)!=int or not -(1<<63)<=n<(1<<64) for n in chunks): raise ValueError('Invalid colony chunks')
        if not isinstance(item.get('colony_name',''),str): raise ValueError('Invalid colony name')
        if 'colour' in item and type(item['colour'])!=int: raise ValueError('Invalid colony colour')
        result.append({**item,'chunks':sorted(set(chunks))})
    return result

def unpack_chunk(n):
    x=n&0xffffffff;z=(n>>32)&0xffffffff
    return x-(1<<32) if x>=1<<31 else x,z-(1<<32) if z>=1<<31 else z

def selected_files(world):
    cache=world/'overworld/cache'
    result=[p for p in sorted(cache.glob('*.mca')) if MCA.fullmatch(p.name)]
    border=world/BORDER
    if border.is_file(): result.append(border)
    return result

def capture(world):
    records={}
    for path in selected_files(world):
        before=path.stat();payload=path.read_bytes();after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise RuntimeError('Client metadata changed while reading')
        records[path.relative_to(world).as_posix()]={'payload':payload,'sha256':sha(payload),'mtime_ns':after.st_mtime_ns,'size':len(payload)}
    return records

def retain_extras(target,world,priority_ns=None,validated=False,issues=None):
    records=[]
    for index,(name,record) in enumerate(capture(world).items()):
        if index % 10 == 0: print(f'Validate incoming metadata: {index} files',flush=True)
        path=world/name
        if name.endswith('.mca'):
            if not validated:
                region_issues=[] if issues is not None else None
                chunks=read_region(path,region_issues)
                if region_issues:
                    issues.extend(region_issues)
                    original=target/'original-extra'/name
                    original.parent.mkdir(parents=True,exist_ok=True)
                    original.write_bytes(record['payload'])
                    if chunks:
                        repaired=target/'extra'/name
                        write_region(repaired,chunks)
                        payload=repaired.read_bytes()
                        record={**record,'payload':payload,'size':len(payload),'sha256':sha(payload)}
                    print(f'Archive salvage: {path.name}: {len(region_issues)} damaged chunks omitted; {len(chunks)} valid chunks retained',flush=True)
                if not chunks: continue
        else: validate_border(path)
        copied=target/'extra'/name;copied.parent.mkdir(parents=True,exist_ok=True);copied.write_bytes(record['payload'])
        entry={'name':name,**{k:v for k,v in record.items() if k!='payload'}}
        if priority_ns is not None: entry['merge_priority_ns']=priority_ns
        records.append(entry)
    return records

def inputs(season,config):
    out=[]
    for rank,source_id in enumerate(config['source_order']):
        for manifest in sorted((season/'sources'/source_id).glob('*/manifest.json')):
            data=json.loads(manifest.read_text(encoding='utf-8'))
            for proof in data.get('proofs',[]):
                if not re.fullmatch(r'-?\d+,-?\d+\.png',proof['name']) or sha((manifest.parent/'proof-day'/proof['name']).read_bytes())!=proof['sha256']:raise ValueError('Surface proof checksum mismatch')
            for record in data.get('extras',[]):
                name=record['name']
                if not (name in (BORDER,'border-operations.json') or (name.startswith('overworld/cache/') and MCA.fullmatch(Path(name).name) and len(Path(name).parts)==3)): raise ValueError('Unsafe extra path')
                path=manifest.parent/'extra'/name
                if sha(path.read_bytes())!=record['sha256']: raise ValueError('Extra snapshot checksum mismatch')
                priority=(record.get('merge_priority_ns',record['mtime_ns']),rank,manifest.parent.name)
                out.append((name,priority,path,source_id))
    return sorted(out,key=lambda a:(a[1],a[0]))

def prepare_client(season,config,temp,dry_run=False,day_records=None):
    settings=json.loads((season/'client.json').read_text(encoding='utf-8'))
    world=Path(settings['day_folder']).resolve().parent.parent
    state_path=season/'extra_client_state.json';previous={};baseline=None
    if state_path.exists():
        state=json.loads(state_path.read_text(encoding='utf-8'))
        if state.get('status')!='complete': raise RuntimeError('Metadata installation interrupted; run python scripts/season_data.py recover')
        if Path(state['world']).resolve()!=world: raise ValueError('Extra client target changed')
        baseline=season/'builds'/state['build_id']/'team-data'
        previous=state['installed_sha256']
    records=capture(world)
    identity=sha(json_bytes({'baseline':previous,'records':{n:r['sha256'] for n,r in records.items()}}))[:20]
    source_id=settings['source_id']
    retained=season/'sources'/source_id/('extra-'+identity)
    if (retained/'manifest.json').exists():
        data=json.loads((retained/'manifest.json').read_text(encoding='utf-8'))
        return {'world':world,'records':records,'extra_season':None,'salvaged_chunks':data.get('salvaged_chunks',[])}
    delta_world=temp/'extra-delta';delta_world.mkdir()
    maximum=max((p[1][0] for p in inputs(season,config)),default=0)
    priority=max(time.time_ns(),maximum+1)
    from season_capture import retained_hashes as known_hashes, capture_regions
    retained_hashes=known_hashes(season,source_id)
    issues=capture_regions(season,config,records,baseline,delta_world,temp,retained_hashes,previous)
    operations=[]
    for name,record in records.items():
        if name.endswith('.mca') or previous.get(name)==record['sha256']: continue
        source=world/name
        current=validate_border(source)
        old=validate_border(baseline/name) if baseline and (baseline/name).exists() else []
        oldmap={(c['dimension'],c['id']):c for c in old}
        # A client observes ownership chunk by chunk; absence in another player's
        # file is not deletion. Only changes against this installed baseline remove.
        currentmap={(c['dimension'],c['id']):c for c in current}
        for key,c in currentmap.items():
            prior=oldmap.get(key,{'chunks':[]})
            added=set(c['chunks'])-set(prior['chunks']);removed=set(prior['chunks'])-set(c['chunks'])
            if added or removed or {k:v for k,v in c.items() if k!='chunks'}!={k:v for k,v in prior.items() if k!='chunks'}:
                operations.append({'colony':{k:v for k,v in c.items() if k!='chunks'},'add':sorted(added),'remove':sorted(removed)})
        # The addon normally retains empty colony entries. Entire missing entries
        # may be permission/visibility changes, so never infer server deletion.
        if operations:save_json(delta_world/'border-operations.json',operations)
    extras=retain_extras(temp/'extra-retained',delta_world,priority,validated=True)
    if operations:
        payload=(delta_world/'border-operations.json').read_bytes()
        path=temp/'extra-retained/extra/border-operations.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
        extras.append({'name':'border-operations.json','sha256':sha(payload),'size':len(payload),'mtime_ns':priority,'merge_priority_ns':priority})
    identity=sha(json_bytes({'baseline':previous,'records':{n:r['sha256'] for n,r in records.items()}}))[:20]
    source_id=settings['source_id'];target=(temp/'extra-season' if dry_run else season)/'sources'/source_id/('extra-'+identity)
    if extras and not (target/'manifest.json').exists():
        target.mkdir(parents=True)
        for name,record in records.items():
            original=target/'original-extra'/name;original.parent.mkdir(parents=True,exist_ok=True);original.write_bytes(record['payload'])
        for r in extras:
            src=temp/'extra-retained/extra'/r['name'];dst=target/'extra'/r['name'];dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(src.read_bytes())
        proofs=[]
        for name in sorted({Path(r['name']).name for r in extras if r['name'].endswith('.mca')}):
            rx,rz=map(int,MCA.fullmatch(name).groups());tile=f'{rx},{rz}.png'
            if day_records and tile in day_records:
                payload=day_records[tile]['payload'];dst=target/'proof-day'/tile;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(payload)
                proofs.append({'name':tile,'sha256':sha(payload)})
        save_json(target/'manifest.json',{'salvaged_chunks':issues,'proofs':proofs,'source_id':source_id,'files':[],'extras':extras,'archive_sha256':None,'layer':'day','dimension':'overworld'})
    return {'world':world,'records':records,'extra_season':temp/'extra-season' if dry_run else None,'salvaged_chunks':issues}

def ensure_unchanged(plan):
    actual=capture(plan['world'])
    if {n:(r['sha256'],r['mtime_ns']) for n,r in actual.items()}!={n:(r['sha256'],r['mtime_ns']) for n,r in plan['records'].items()}:raise RuntimeError('Client blocks or colony boundaries changed; close Minecraft and retry')

def surface_record(column):
    c=plain(column);height=c.get('top_y',c.get('surface_y'));states=c.get('blockstates',{})
    state=states.get(str(height)) if height is not None else None
    if not isinstance(state,dict):state=c.get('block')
    if not isinstance(state,dict) or not isinstance(state.get('Name'),str):return None
    return [state['Name'],state.get('Properties',{}),height,c.get('biome_name'),c.get('surface_y')]

def border_edges(chunks):
    cells={unpack_chunk(n) for n in chunks};edges=[]
    for x,z in sorted(cells):
        for nx,nz,a,b in ((x-1,z,(x*16,z*16),(x*16,(z+1)*16)),(x+1,z,((x+1)*16,z*16),((x+1)*16,(z+1)*16)),(x,z-1,(x*16,z*16),((x+1)*16,z*16)),(x,z+1,(x*16,(z+1)*16),((x+1)*16,(z+1)*16))):
            if (nx,nz) not in cells:edges.append([a,b])
    return edges

def build(season,config,staged,site,extra_season=None):
    all_inputs=inputs(season,config)
    for extra in extra_season or []:
        if extra.exists():all_inputs+=inputs(extra,config)
    all_inputs.sort(key=lambda a:(a[1],a[0]))
    grouped={};border_inputs=[]
    for name,p,path,source in all_inputs:
        if name.endswith('.mca'):grouped.setdefault(Path(name).name,[]).append((p,path,source))
        else:border_inputs.append((name,p,path,source))
    output=staged/'team-data';output.mkdir();surface=site/'surface';surface.mkdir()
    labels = season/'surface-labels.json'
    save_json(surface/'labels.json', json.loads(labels.read_text(encoding='utf-8')) if labels.exists() else {})
    from season_surface import merge_regions
    tiles,columns,inconsistent=merge_regions(season,config,grouped,output,surface,site)
    owners={};metadata={};metadata_priority={};history={}
    for name,priority,path,source in sorted(border_inputs,key=lambda a:a[1]):
        if name=='border-operations.json':operations=json.loads(path.read_text(encoding='utf-8'))
        else:
            current=validate_border(path);previous=history.setdefault(source,{})
            operations=[]
            for c in current:
                key=(c['dimension'],c['id']);prior=previous.get(key,{'chunks':[]})
                operations.append({'colony':{k:v for k,v in c.items() if k!='chunks'},'add':sorted(set(c['chunks'])-set(prior['chunks'])),'remove':sorted(set(prior['chunks'])-set(c['chunks']))})
                previous[key]=c
        for op in operations:
            c=op['colony'];key=(c['dimension'],c['id'])
            if priority>=metadata_priority.get(key,(-1,-1,'')):
                # Unknown names in partial observations should not erase known names.
                old=metadata.get(key,{})
                metadata[key]={**old,**c,'colony_name':c.get('colony_name') or old.get('colony_name','')};metadata_priority[key]=priority
            for n in op['remove']:
                ck=(c['dimension'],n);old=owners.get(ck)
                if old and old[1]==key and priority>=old[0]:owners[ck]=(priority,None)
            for n in op['add']:
                ck=(c['dimension'],n)
                if ck not in owners or priority>=owners[ck][0]:owners[ck]=(priority,key)
    colonies=[]
    for key,c in sorted(metadata.items()):
        chunks=sorted(n for (dim,n),(p,owner) in owners.items() if owner==key)
        colonies.append({**c,'chunks':chunks})
    if colonies:save_json(output/BORDER,colonies)
    web=[{**{k:v for k,v in c.items() if k!='chunks'},'chunks':len(c['chunks']),'edges':border_edges(c['chunks'])} for c in colonies if c['dimension']=='minecraft:overworld']
    save_json(site/'colonies.json',web)
    save_json(surface/'index.json',{'format':'jm-surface-palette-v1','tile_size':512,'tiles':tiles,'columns':columns})
    return {'surface_columns':columns,'surface_regions':len(tiles),'unmatched_image_columns':inconsistent,'colonies':len(web),'policy':'observations per column / chunk; unchanged observations not promoted; missing colonies are not deletion'}

def install(plan,release,season,closed_check):
    import season_client
    state_path=season/'extra_client_state.json'
    closed_check();ensure_unchanged(plan)
    expected={p.relative_to(release/'team-data').as_posix():sha(p.read_bytes()) for p in (release/'team-data').rglob('*') if p.is_file()}
    if all(plan['records'].get(n,{}).get('sha256')==h for n,h in expected.items()):
        season_client.json_write(state_path,{'status':'complete','world':str(plan['world']),'build_id':release.name,'installed_sha256':expected})
        return {'updated_files':0}
    backup=season/'client_backups'/('extra-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]);backup.mkdir(parents=True)
    for n,r in plan['records'].items():
        p=backup/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(r['payload'])
        if sha(p.read_bytes())!=r['sha256']:raise ValueError('Extra backup checksum mismatch')
    save_json(backup/'manifest.json',{'files':{n:{k:v for k,v in r.items() if k!='payload'} for n,r in plan['records'].items()}})
    if state_path.exists():(backup/'previous_state.json').write_bytes(state_path.read_bytes())
    state={'status':'pending','world':str(plan['world']),'build_id':release.name,'installed_sha256':expected,'backup':str(backup)}
    import season_client
    closed_check();ensure_unchanged(plan);season_client.json_write(state_path,state)
    try:
        for n,h in expected.items():
            dst=plan['world']/n;original=plan['records'].get(n)
            if dst.exists() and (not original or sha(dst.read_bytes())!=original['sha256']):raise RuntimeError('Fresh client metadata appeared during install')
            if original and not dst.exists():raise RuntimeError('Client metadata disappeared during install')
            if original and original['sha256']==h:continue
            dst.parent.mkdir(parents=True,exist_ok=True);tmp=dst.with_name(dst.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                tmp.write_bytes((release/'team-data'/n).read_bytes())
                if sha(tmp.read_bytes())!=h:raise ValueError('Extra staged checksum mismatch')
                if dst.exists() and (not original or sha(dst.read_bytes())!=original['sha256']):raise RuntimeError('Fresh metadata during staging')
                os.replace(tmp,dst)
            finally:tmp.unlink(missing_ok=True)
        closed_check()
        actual=capture(plan['world'])
        if set(actual)!=set(plan['records'])|set(expected):raise RuntimeError('Client metadata file set changed')
        if any(actual[n]['sha256']!=h for n,h in expected.items()):raise RuntimeError('Client metadata changed after installation')
        state['status']='complete';season_client.json_write(state_path,state)
    except Exception:
        state['status']='interrupted';season_client.json_write(state_path,state);raise
    return {'updated_files':sum(plan['records'].get(n,{}).get('sha256')!=h for n,h in expected.items()),'backup':str(backup)}

def recover(season,closed_check):
    import season_client
    state=json.loads((season/'extra_client_state.json').read_text(encoding='utf-8'))
    if state['status'] not in ('pending','interrupted'):raise ValueError('No interrupted metadata installation')
    closed_check();world=Path(state['world']).resolve()
    settings=json.loads((season/'client.json').read_text(encoding='utf-8'))
    if Path(settings['day_folder']).resolve().parent.parent!=world:raise ValueError('Recovery target differs')
    backup=Path(state['backup']).resolve()
    if not backup.is_relative_to((season/'client_backups').resolve()):raise ValueError('Invalid backup')
    manifest=json.loads((backup/'manifest.json').read_text(encoding='utf-8'))['files'];actual=capture(world)
    if not set(manifest).issubset(actual) or not set(actual).issubset(set(manifest)|set(state['installed_sha256'])):raise RuntimeError('New client metadata; recovery refused')
    for n,r in actual.items():
        if r['sha256'] not in {manifest.get(n,{}).get('sha256'),state['installed_sha256'].get(n)}:raise RuntimeError('Fresh metadata; recovery refused')
    if not re.fullmatch(r'[a-f0-9]{20}',state['build_id']):raise ValueError('Invalid build ID')
    release=season/'builds'/state['build_id']
    for n,h in state['installed_sha256'].items():
        path=release/'team-data'/n
        if not path.resolve().is_relative_to((release/'team-data').resolve()) or sha(path.read_bytes())!=h:raise ValueError('Recovery data mismatch')
    # Finish installation, with a new backup of this interrupted state.
    return install({'world':world,'records':actual},release,season,closed_check)

if __name__=='__main__':
    import argparse,season_client
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['recover']);parser.parse_args()
    season=Path(__file__).resolve().parents[1]/'seasons/steel-soil'
    with season_client.job_lock(season):print(recover(season,season_client.require_closed))
