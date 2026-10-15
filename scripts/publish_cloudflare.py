"""Publish only the built map to Cloudflare Pages; keep the team ZIP on Drive."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from season_log import enable_console, success, error

ROOT = Path(__file__).resolve().parents[1]
DRIVE_URL = 'https://drive.google.com/file/d/1myClE6nE_bEmebolmFMdzWoyAWm8Q7xA/view'


def settings(season):
    data = json.loads((season/'cloudflare.json').read_text(encoding='utf-8'))
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,57}', data['project_name']):
        raise ValueError('Invalid Pages project name')
    if not re.fullmatch(r'[a-f0-9]{32}', data['account_id']):
        raise ValueError('Invalid Cloudflare account ID')
    return data


def prepare(season, destination, config):
    current = json.loads((season/'current.json').read_text(encoding='utf-8'))
    build_id = current['build_id']
    if not re.fullmatch(r'[a-f0-9]{20}', build_id):
        raise ValueError('Invalid current build ID')
    release = season/'builds'/build_id
    site = release/'site'
    if site.resolve() != (season/current['site']).resolve():
        raise ValueError('Current release site path mismatch')
    report = json.loads((release/'report.json').read_text(encoding='utf-8'))
    for name, expected in report['sha256'].items():
        path = site/name
        if not path.resolve().is_relative_to(site.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Release checksum mismatch: '+name)
    files = [p for p in site.rglob('*') if p.is_file()]
    if any(p.is_symlink() or p.is_junction() for p in site.rglob('*')):
        raise ValueError('Release contains filesystem links')
    selected = [p for p in files if p.suffix.lower() != '.zip']
    if len(selected)+1 > 20000 or any(p.stat().st_size > 25*1024**2 for p in selected):
        raise ValueError('Map exceeds Cloudflare Pages Free asset limits')
    destination.mkdir(parents=True, exist_ok=False)
    for source in selected:
        target = destination/source.relative_to(site)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    latest = destination/'downloads/latest.json'
    metadata = json.loads(latest.read_text(encoding='utf-8'))
    metadata.update(storage='google-drive', url=config.get('download_url', DRIVE_URL))
    metadata.pop('release_tag', None)
    latest.write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (destination/'deployment.json').write_text(json.dumps({'build_id':build_id,'files':len(selected)}), encoding='utf-8')
    return build_id, len(selected)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require_access(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.pages.dev'):
        raise ValueError('Access probe must use the Pages HTTPS hostname')
    try:
        # Cloudflare rejects Python's default user agent (error 1010).
        # Keep probes anonymous and identify the publisher explicitly.
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 SteelSoilPublisher/1.0'})
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        location = urllib.parse.urlsplit(response.headers.get('Location', ''))
        if response.code not in (302,303,307,308) or location.scheme != 'https' or not (location.hostname or '').endswith('.cloudflareaccess.com') or '/cdn-cgi/access/login/' not in location.path:
            raise RuntimeError('Access protection is not confirmed for '+url+'; map upload stopped')


def publish(season):
    config = settings(season)
    if not config.get('enabled'):
        return False
    if config.get('protection_verified') is not True:
        raise RuntimeError('Configure and verify Cloudflare Access before enabling map publication')
    hostname = config['project_name']+'.pages.dev'
    # Check the production hostname and wildcard preview protection independently.
    for host in (hostname, 'access-check.'+hostname):
        for path in ('/', '/tiles/0,0.png', '/surface/index.json', '/colonies.json'):
            require_access('https://'+host+path)
    node = shutil.which('node')
    wrangler = Path(os.environ.get('LOCALAPPDATA',''))/'steel-soil-cloudflare-tools/node_modules/wrangler/bin/wrangler.js'
    if not node or not wrangler.is_file():
        raise RuntimeError('Install Wrangler first; see docs/cloudflare.md')
    env = {**os.environ,'CLOUDFLARE_ACCOUNT_ID':config['account_id'],'WRANGLER_SEND_METRICS':'false'}
    with tempfile.TemporaryDirectory(prefix='steel-soil-cloudflare-') as temp:
        output = Path(temp)/'site'
        build_id, count = prepare(season, output, config)
        result = subprocess.run([node,str(wrangler),'pages','deploy',str(output),'--project-name',config['project_name'],'--branch','main','--commit-dirty=true'],cwd=temp,env=env)
        if result.returncode:
            raise RuntimeError('Cloudflare upload failed. Retry: python scripts/publish_cloudflare.py')
    from season_client import json_write
    json_write(season/'cloudflare_state.json', {'build_id':build_id,'project_name':config['project_name'],'files':count,'status':'uploaded-access-prechecked'})
    success('Cloudflare map uploaded: '+build_id)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', type=Path, help='Prepare upload folder without publishing; destination must not exist')
    args = parser.parse_args()
    season = ROOT/'seasons/steel-soil'
    if args.prepare:
        build_id, count = prepare(season, args.prepare, settings(season))
        success(f'Prepared Cloudflare map: {build_id}, {count} files')
    else:
        with __import__('season_client').job_lock(season):
            if not publish(season): print('Cloudflare publication is disabled')


if __name__ == '__main__':
    enable_console()
    try: main()
    except Exception as exc:
        error(str(exc))
        raise SystemExit(1)
