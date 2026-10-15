"""Independent region workers and disposable, content-addressed surface cache."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from PIL import Image
import season_data as d

CACHE_FORMAT = 1


def _compute_region(name, files, final_path, work):
    MCA, COLUMN, Tag = d.MCA, d.COLUMN, d.Tag
    read_region, write_region = d.read_region, d.write_region
    json_bytes, surface_record = d.json_bytes, d.surface_record
    site = Path(final_path).parent.parent
    winners={};roots={};seen={};chunk_times={};proof_images={};inconsistent=0
    for priority,path,source,proof in files:
        path=Path(path);proof=Path(proof) if proof else None
        snapshot=path.parents[3];rx,rz=map(int,MCA.fullmatch(name).groups());tile=f'{rx},{rz}.png'
        if proof and proof not in proof_images:
            with Image.open(proof) as image:proof_images[proof]=image.convert('RGBA').load()
        prior=seen.setdefault(source,{})
        for i,(timestamp,root) in sorted(read_region(path).items(),key=lambda item:(item[1][0],item[0])):
            roots[i]={k:v for k,v in root.value.items() if not COLUMN.fullmatch(k)}
            chunk_times[i]=max(chunk_times.get(i,0),timestamp)
            for key,col in root.value.items():
                if not COLUMN.fullmatch(key):continue
                if prior.get(key)==col:continue
                prior[key]=col
                # Source snapshot time orders observations; unchanged columns
                # do not become fresh just because their region was rewritten.
                observed=priority[0] if snapshot.name.startswith('extra-') else (timestamp*1_000_000_000 if timestamp else priority[0])
                column_priority=(observed,priority[0],priority[1],priority[2])
                if key not in winners or column_priority>winners[key][0]:winners[key]=(column_priority,i,col,proof)
    chunks={};palette=[];lookup={};rows=[]
    rx,rz=map(int,MCA.fullmatch(name).groups())
    tile=f'{rx},{rz}.png';final=None
    if (site/'tiles'/tile).exists():
        with Image.open(site/'tiles'/tile) as image:final=image.convert('RGBA').load()
    for key,(_,i,col,proof) in sorted(winners.items()):
        x,z=map(int,key.split(','));local=(x-rx*512,z-rz*512)
        if final and proof and proof_images[proof][local]!=final[local]:
            inconsistent+=1;continue
        original_i=i;i=((x//16)&31)+((z//16)&31)*32
        chunk_times[i]=max(chunk_times.get(i,0),chunk_times[original_i])
        if i not in chunks:
            meta={k:v for k,v in roots.get(original_i,{}).items() if k!='pos'}
            packed=((z//16)&0xffffffff)<<32 | ((x//16)&0xffffffff)
            if packed>=1<<63:packed-=1<<64
            meta['pos']=Tag(4,packed)
            chunks[i]=Tag(10,meta)
        chunks[i].value[key]=col
        record=surface_record(col)
        if record:
            token=json_bytes(record)
            if token not in lookup:lookup[token]=len(palette);palette.append(record)
            x,z=map(int,key.split(','));index=(z-rz*512)*512+x-rx*512
            rows.append([index,lookup[token]])
    write_region(work/'region.mca',{i:(chunk_times[i],root) for i,root in chunks.items()})
    if rows:
        payload=json_bytes({'palette':palette,'cells':sorted(rows)})
        (work/'surface.json.gz').write_bytes(gzip.compress(payload,mtime=0))
    return {'columns':len(rows),'unmatched':inconsistent}

def _metadata_hash(entry):
    return d.sha(d.json_bytes({k:v for k,v in entry.items() if k != 'metadata_sha256'}))


def _valid_entry(directory, key, name):
    try:
        if directory.is_symlink():
            return None
        entry = json.loads((directory/'entry.json').read_text(encoding='utf-8'))
        if entry['format'] != CACHE_FORMAT or entry['key'] != key or entry['name'] != name:
            return None
        if entry['metadata_sha256'] != _metadata_hash(entry):
            return None
        if any(type(entry[k]) != int or not 0 <= entry[k] <= 512*512 for k in ('columns','unmatched')):
            return None
        expected = {'region.mca'} | ({'surface.json.gz'} if entry['columns'] else set())
        if set(entry['files']) != expected:
            return None
        for filename, digest in entry['files'].items():
            path = directory/filename
            if path.is_symlink() or d.sha(path.read_bytes()) != digest:
                return None
        return entry
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _worker(task):
    root = Path(task['cache_root'])
    directory = root/task['key']
    work = root/('.working-'+uuid.uuid4().hex)
    work.mkdir()
    started = time.perf_counter()
    try:
        result = _compute_region(task['name'], task['files'], task['final_path'], work)
        entry = dict(result, format=CACHE_FORMAT, key=task['key'], name=task['name'],
                     files={p.name:d.sha(p.read_bytes()) for p in sorted(work.iterdir()) if p.is_file()})
        entry['metadata_sha256'] = _metadata_hash(entry)
        d.save_json(work/'entry.json',entry)
        if directory.exists():
            if _valid_entry(directory,task['key'],task['name']) is None:
                # Preserve an invalid entry for diagnosis; never trust its outputs.
                directory.rename(root/('.invalid-'+uuid.uuid4().hex))
            elif _valid_entry(directory,task['key'],task['name']) == entry:
                return entry, time.perf_counter()-started
            else:
                directory.rename(root/('.invalid-'+uuid.uuid4().hex))
        work.rename(directory)
        return entry, time.perf_counter()-started
    finally:
        if work.exists():
            if work.resolve().parent != root.resolve() or work.is_symlink():
                raise ValueError('Surface cache working directory escaped its root')
            shutil.rmtree(work)


def _task(name, files, site, cache_root, algorithm):
    rx,rz = map(int,d.MCA.fullmatch(name).groups())
    tile = f'{rx},{rz}.png'
    observations=[];identity=[]
    for priority,path,source in sorted(files):
        snapshot=path.parents[3]
        proof=next((snapshot/folder/tile for folder in ('proof-day','original-day','day')
                    if (snapshot/folder/tile).is_file()),None)
        observations.append((priority,str(path),source,str(proof) if proof else None))
        identity.append({'priority':priority,'source':source,'snapshot':snapshot.name,
                         'mca':d.sha(path.read_bytes()),'proof':d.sha(proof.read_bytes()) if proof else None})
    final=site/'tiles'/tile
    key=d.sha(d.json_bytes({'format':CACHE_FORMAT,'algorithm':algorithm,'region':name,
                           'observations':identity,'final':d.sha(final.read_bytes()) if final.exists() else None}))
    return {'name':name,'key':key,'files':observations,'final_path':str(final),
            'cache_root':str(cache_root)}


def merge_regions(season, config, grouped, output, surface, site):
    started=time.perf_counter()
    project_key=d.sha(str(season.resolve()).casefold().encode())[:20]
    cache_base=Path(tempfile.gettempdir()).resolve()/'steel-soil-surface-cache'
    cache_root=cache_base/project_key
    if cache_base.is_symlink() or cache_root.is_symlink():
        raise ValueError('Surface cache directories cannot be symlinks')
    cache_root.mkdir(parents=True,exist_ok=True)
    if cache_root.is_symlink():
        raise ValueError('Surface cache root cannot be a symlink')
    algorithm=d.sha(Path(__file__).read_bytes()+Path(d.__file__).read_bytes())
    from season_resources import choose_workers
    largest=max((sum(path.stat().st_size for _,path,_ in files) for files in grouped.values()),default=0)
    workers=choose_workers(config,len(grouped),largest)
    reuse=config.get('surface_cache',True)
    if type(reuse) != bool:
        raise ValueError('surface_cache must be true or false')
    tasks=[_task(name,files,site,cache_root,algorithm) for name,files in sorted(grouped.items())]
    results={};pending=[];cached=0;completed=0

    def install(task, entry, elapsed, reused):
        nonlocal completed,cached
        directory=cache_root/task['key']
        target=output/'overworld/cache'/task['name']
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(directory/'region.mca',target)
        rx,rz=map(int,d.MCA.fullmatch(task['name']).groups())
        if entry['columns']:
            shutil.copyfile(directory/'surface.json.gz',surface/f'{rx},{rz}.json.gz')
        results[task['name']]=entry
        completed+=1;cached+=int(reused)
        print(f"Surface merge: region {completed}/{len(tasks)} ({task['name']}) "
              f"[{'cache' if reused else 'rebuilt'}; {elapsed:.1f}s]",flush=True)

    for task in tasks:
        entry=_valid_entry(cache_root/task['key'],task['key'],task['name']) if reuse else None
        if entry is not None:install(task,entry,0,True)
        else:pending.append(task)
    actual_workers=min(workers,len(pending))
    if actual_workers <= 1:
        for task in pending:
            entry,elapsed=_worker(task);install(task,entry,elapsed,False)
    else:
        # Child processes receive only paths and priorities, never large NBT trees.
        with ProcessPoolExecutor(max_workers=actual_workers) as pool:
            futures={pool.submit(_worker,task):task for task in pending}
            for future in as_completed(futures):
                entry,elapsed=future.result()
                install(futures[future],entry,elapsed,False)
    tiles={};columns=0;inconsistent=0
    for name,entry in sorted(results.items()):
        rx,rz=map(int,d.MCA.fullmatch(name).groups())
        if entry['columns']:tiles[f'{rx},{rz}']=f'{rx},{rz}.json.gz'
        columns+=entry['columns'];inconsistent+=entry['unmatched']
    print(f'Surface merge complete: {cached} cached, {len(pending)} rebuilt, '
          f'{actual_workers} workers, {time.perf_counter()-started:.1f}s',flush=True)
    return tiles,columns,inconsistent
