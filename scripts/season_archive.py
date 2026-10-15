"""Discover one JourneyMap overworld surface dataset without guessing dimensions."""
from pathlib import PurePosixPath
import re
from zipfile import ZipFile

TILE=re.compile(r'^-?\d+,-?\d+\.png$',re.I)
MCA=re.compile(r'^r\.-?\d+\.-?\d+\.mca$',re.I)
BORDER='addon-data/minecolonies/overworld/border.json'
EXCLUDED={'night','biome','topo','nether','the_nether','the_end','end','dim-1','dim1','dim1'}

class ArchiveLayoutError(ValueError):pass

def inspect(archive):
    entries=[]
    for info in archive.infolist():
        name=info.filename.replace('\\','/')
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or ':' in name:raise ArchiveLayoutError('Unsafe archive path: '+name)
        if info.is_dir():continue
        if (info.external_attr>>16)&0o170000==0o120000:raise ArchiveLayoutError('Archive contains a symbolic link: '+name)
        entries.append((info,path))
    candidates={}
    for info,path in entries:
        if not TILE.fullmatch(path.name):continue
        parts=[p.lower() for p in path.parts[:-1]]
        if any(p in EXCLUDED for p in parts):continue
        if path.parent.name.lower()=='day':candidates.setdefault(path.parent,[]).append((info,path))
    # Explicit overworld paths take precedence over named other dimensions.
    if not candidates:
        flat=[(i,p) for i,p in entries if len(p.parts)==1 and TILE.fullmatch(p.name)]
        if flat:candidates={PurePosixPath('.'):flat}
    if len(candidates)!=1:
        choices=', '.join(str(p) for p in sorted(candidates)) or 'none'
        raise ArchiveLayoutError('Expected one surface day map. Found: '+choices+'. Export one server: select overworld and addon-data, or day + cache + border.json.')
    day,tiles=next(iter(candidates.items()))
    dimension=day.parent if str(day)!='.' else PurePosixPath('.')
    world=dimension.parent if dimension.name.lower()=='overworld' else dimension
    cache_dirs={str(p).casefold() for p in (dimension/'cache',world/'cache',world/'overworld/cache')}
    selected=[(i,p,'overworld/day/'+p.name.lower()) for i,p in tiles]
    cache=[];borders=[]
    for info,path in entries:
        if MCA.fullmatch(path.name) and (str(path.parent).casefold() in cache_dirs or (len(path.parts)==1 and str(day)=='.')):
            cache.append((info,path,'overworld/cache/'+path.name.lower()))
        if path.name.lower()=='border.json':
            accepted={world/'border.json',world/'addon-data/border.json',world/BORDER,dimension/'border.json'}
            if str(path).casefold() in {str(p).casefold() for p in accepted}:borders.append((info,path,BORDER))
    # A separately named addon folder is accepted only when it has a unique
    # overworld border; another world's/dimension's files are never selected.
    if not borders:
        possible=[(i,p,BORDER) for i,p in entries if p.name.lower()=='border.json'
                  and not any(part.lower() in EXCLUDED for part in p.parts[:-1])
                  and (p.is_relative_to(world) if str(world)!='.' else True)]
        if len(possible)==1:borders=possible
        elif len(possible)>1:raise ArchiveLayoutError('Several colony border files; keep only the current overworld border.json')
    selected+=cache+borders
    resolved={};total=0;duplicates=0
    for info,path,target in selected:
        total+=info.file_size
        if info.file_size>128*1024*1024 or total>1024*1024*1024:raise ArchiveLayoutError('Selected map data exceeds size limit')
        if target in resolved:
            old=resolved[target]
            if old[0].file_size!=info.file_size or old[0].CRC!=info.CRC or archive.read(old[0])!=archive.read(info):
                raise ArchiveLayoutError('Conflicting copies of '+target+': '+str(old[1])+' and '+str(path))
            duplicates+=1
            if info.date_time>old[0].date_time:resolved[target]=(info,path,target)
        else:resolved[target]=(info,path,target)
    selected=list(resolved.values())
    report={'format':3,'day_folder':str(day),'world_root':str(world),'day_tiles':len(tiles),
            'cache_files':sum(t.endswith('.mca') for _,_,t in selected),
            'border_file':str(borders[0][1]) if borders else None,'selected_bytes':sum(i.file_size for i,_,_ in selected),
            'ignored_files':len(entries)-len(selected)-duplicates,'identical_duplicates':duplicates,
            'selected_paths':{target:str(path) for _,path,target in selected},'warnings':[]}
    if not cache:report['warnings'].append('No surface cache: block information will not be added')
    if not borders:report['warnings'].append('No colony border file: boundaries will not be added')
    return selected,report

if __name__=='__main__':
    import argparse,json
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('archive');args=parser.parse_args()
    with ZipFile(args.archive) as archive:
        _,report=inspect(archive);report.pop('selected_paths');print(json.dumps(report,ensure_ascii=False,indent=2))
