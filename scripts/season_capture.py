"""Capture only changed surface columns using region workers and baseline indexes."""
import gzip
import json
import math
import os
import struct
import tempfile
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import season_data as d
from season_resources import choose_workers
from season_log import success


def _root(season):
    base = Path(tempfile.gettempdir()).resolve() / 'steel-soil-capture-cache'
    root = base / d.sha(str(season.resolve()).casefold().encode())[:20]
    if base.is_symlink() or root.is_symlink():
        raise ValueError('Capture cache cannot be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load(path):
    try:
        record = json.loads(gzip.decompress(path.read_bytes()))
        if record['sha256'] == d.sha(d.json_bytes(record['data'])):
            return record['data']
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        pass
    return None


def _save(path, data):
    data = json.loads(d.json_bytes(data))
    temporary = path.with_name('.working-' + uuid.uuid4().hex)
    try:
        temporary.write_bytes(gzip.compress(d.json_bytes({'data': data, 'sha256': d.sha(d.json_bytes(data))}), compresslevel=3, mtime=0))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def column_hash(column):
    has_nan = False
    def value(kind, v):
        nonlocal has_nan
        if kind == 10:
            return {k: [t.kind, value(t.kind, t.value)] for k, t in v.items()}
        if kind == 9:
            return [v[0], [value(v[0], item) for item in v[1]]]
        if kind == 7:
            return v.hex()
        if kind in (5, 6):
            if math.isnan(v): has_nan = True
            if v == 0: return 0.0
        return v
    payload = d.json_bytes([column.kind, value(column.kind, column.value)])
    # A freshly parsed NaN never compares equal to the baseline in the old code.
    return None if has_nan else d.sha(payload)


def payload_hashes(path):
    raw = path.read_bytes()
    if len(raw) < 8192: return {}
    result = {}
    for i, offset in enumerate(struct.unpack('>1024I', raw[:4096])):
        sector, count = offset >> 8, offset & 255
        pos = sector * 4096
        if sector < 2 or not count or pos+5 > len(raw): continue
        length = struct.unpack('>I', raw[pos:pos+4])[0]
        if 1 <= length <= count*4096-4 and pos+4+length <= len(raw):
            result[i] = d.sha(raw[pos+4:pos+4+length])
    return result


def baseline_index(path, root):
    if path is None: return {}, {}, False
    algorithm = d.sha(Path(__file__).read_bytes() + Path(d.__file__).read_bytes())
    key = d.sha(d.json_bytes([path.name, d.sha(path.read_bytes()), algorithm]))
    cache = root / ('baseline-' + key + '.json.gz')
    index = _load(cache)
    if index is not None:
        return index['columns'], {int(k): v for k, v in index['payloads'].items()}, True
    chunks = d.read_region(path)
    columns = {str(i): {k: column_hash(v) for k, v in tag.value.items() if d.COLUMN.fullmatch(k)}
               for i, (_, tag) in chunks.items()}
    payloads = {i: h for i, h in payload_hashes(path).items() if all(v is not None for v in columns[str(i)].values())}
    _save(cache, {'columns': columns, 'payloads': payloads})
    return columns, payloads, False


def _worker(task):
    started = time.perf_counter()
    source, destination, root = map(Path, (task['source'], task['destination'], task['cache']))
    baseline = Path(task['baseline']) if task['baseline'] else None
    old, identical, reused = baseline_index(baseline, root)
    raw_hashes = payload_hashes(source)
    skipped = sum(identical.get(i) == h for i, h in raw_hashes.items())
    issues = []
    current = d.read_region(source, issues, skip_payloads=identical)
    changed_columns = 0
    if baseline is None and not issues:
        if current:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        changed_columns = sum(bool(d.COLUMN.fullmatch(k)) for _, tag in current.values() for k in tag.value)
    else:
        changes = {}
        for i, (timestamp, tag) in current.items():
            prior = old.get(str(i), {})
            changed = {}
            for k, v in tag.value.items():
                if not d.COLUMN.fullmatch(k): continue
                fingerprint = column_hash(v)
                if fingerprint is None or prior.get(k) != fingerprint: changed[k] = v
            changed_columns += len(changed)
            if changed:
                changed.update({k: v for k, v in tag.value.items() if not d.COLUMN.fullmatch(k)})
                changes[i] = (timestamp, d.Tag(10, changed))
        if changes: d.write_region(destination, changes)
    return {'name': source.name, 'seconds': time.perf_counter()-started, 'columns': changed_columns,
            'skipped_chunks': skipped, 'cached_baseline': reused, 'issues': issues}


def retained_hashes(season, source_id):
    cache = _root(season) / ('history-' + d.sha(source_id.encode()) + '.json.gz')
    previous = _load(cache) or {}
    updated = {}; result = {}
    for manifest in (season/'sources'/source_id).glob('*/manifest.json'):
        data = json.loads(manifest.read_text(encoding='utf-8'))
        for r in data.get('extras', []):
            if r['name'].endswith('.mca'): result.setdefault(r['name'], set()).add(r['sha256'])
        for path in (manifest.parent/'original-extra/overworld/cache').glob('*.mca'):
            key = path.relative_to(season).as_posix(); stat = path.stat()
            record = previous.get(key)
            if not record or record['size'] != stat.st_size or record['mtime_ns'] != stat.st_mtime_ns:
                record = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': d.sha(path.read_bytes())}
            updated[key] = record
            result.setdefault('overworld/cache/'+path.name, set()).add(record['sha256'])
    _save(cache, updated)
    return result


def capture_regions(season, config, records, baseline, delta_world, temp, known, previous):
    started = time.perf_counter(); root = _root(season); tasks = []
    snapshot = temp/'capture-input'
    for name, record in records.items():
        if not name.endswith('.mca') or previous.get(name) == record['sha256'] or record['sha256'] in known.get(name, set()): continue
        # Empty current files contain no observations; do not parse the baseline.
        if not record['payload']: continue
        source = snapshot/name; source.parent.mkdir(parents=True, exist_ok=True); source.write_bytes(record['payload'])
        old = baseline/name if baseline and (baseline/name).is_file() else None
        tasks.append({'source': str(source), 'destination': str(delta_world/name), 'baseline': str(old) if old else None,
                      'cache': str(root), 'estimated': record['size'] + (old.stat().st_size if old else 0)})
    workers = choose_workers(config, len(tasks), max((t['estimated'] for t in tasks), default=0))
    mode = 'manual' if config.get('surface_workers') is not None else 'auto: 80% logical CPUs, RAM limit'
    print(f'Surface capture: {len(tasks)} changed regions, {workers} workers ({mode})', flush=True)
    results = []
    def complete(result):
        results.append(result)
        print(f"Surface capture: {len(results)}/{len(tasks)} ({result['name']}); {result['seconds']:.1f}s; "
              f"{result['columns']} fresh columns; {result['skipped_chunks']} unchanged chunks; "
              f"baseline {'cached' if result['cached_baseline'] else 'indexed'}", flush=True)
    if workers <= 1:
        for task in tasks: complete(_worker(task))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(_worker, task) for task in tasks]): complete(future.result())
    success(f'Surface capture: {len(tasks)} regions, {sum(r["columns"] for r in results)} fresh columns, {time.perf_counter()-started:.1f}s')
    return [issue for r in sorted(results, key=lambda r: r['name']) for issue in r['issues']]
