import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import publish_cloudflare as c


class CloudflareTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.season=self.root/'season'
        self.build='a'*20
        self.site=self.season/'builds'/self.build/'site'
        (self.site/'downloads').mkdir(parents=True)
        (self.site/'index.html').write_bytes(b'map')
        self.original={'build_id':self.build,'storage':'github-release','url':'https://github.com/old','release_tag':'old','sha256':'b'*64}
        (self.site/'downloads/latest.json').write_text(json.dumps(self.original))
        (self.site/'downloads/team.zip').write_bytes(b'archive')
        (self.season/'current.json').write_text(json.dumps({'build_id':self.build,'site':f'builds/{self.build}/site'}))
        (self.site.parent/'report.json').write_text(json.dumps({'sha256':{'index.html':hashlib.sha256(b'map').hexdigest()}}))
        self.config={'enabled':False,'protection_verified':False,'project_name':'test-team','account_id':'a'*32}
        (self.season/'cloudflare.json').write_text(json.dumps(self.config))

    def test_prepare_preserves_release_and_rewrites_drive_link_without_zip(self):
        output=self.root/'upload'
        build,count=c.prepare(self.season,output,self.config)
        self.assertEqual(build,self.build)
        self.assertFalse((output/'downloads/team.zip').exists())
        changed=json.loads((output/'downloads/latest.json').read_text())
        self.assertEqual(changed['storage'],'google-drive')
        self.assertEqual(changed['url'],c.DRIVE_URL)
        self.assertNotIn('release_tag',changed)
        self.assertEqual(json.loads((self.site/'downloads/latest.json').read_text()),self.original)
        self.assertEqual(json.loads((output/'deployment.json').read_text())['build_id'],build)

    def test_invalid_release_checksum_stops_preparation(self):
        (self.site/'index.html').write_bytes(b'changed')
        with self.assertRaises(ValueError):c.prepare(self.season,self.root/'upload',self.config)

    def test_asset_limit_blocks_upload(self):
        with (self.site/'large.bin').open('wb') as f:f.truncate(25*1024**2+1)
        with self.assertRaises(ValueError):c.prepare(self.season,self.root/'upload',self.config)

    def test_disabled_publication_does_not_contact_cloudflare(self):
        with patch.object(c,'require_access') as probe:
            self.assertFalse(c.publish(self.season))
            probe.assert_not_called()

    def test_unverified_access_blocks_publication(self):
        self.config['enabled']=True
        (self.season/'cloudflare.json').write_text(json.dumps(self.config))
        with patch.object(c.subprocess,'run') as upload:
            with self.assertRaises(RuntimeError):c.publish(self.season)
            upload.assert_not_called()

    def test_public_content_or_unrelated_redirect_is_rejected(self):
        for code,location in [(200,''),(404,''),(302,'https://other.example/login')]:
            with self.subTest(code=code,location=location):
                response=MagicMock();response.code=code;response.headers={'Location':location};response.__enter__.return_value=response
                with patch.object(c.urllib.request,'build_opener') as factory:
                    factory.return_value.open.return_value=response
                    with self.assertRaises(RuntimeError):c.require_access('https://test-team.pages.dev/tiles/0,0.png')

    def test_access_gateway_redirect_is_accepted(self):
        response=MagicMock();response.code=302;response.headers={'Location':'https://team.cloudflareaccess.com/cdn-cgi/access/login/test-team.pages.dev'};response.__enter__.return_value=response
        with patch.object(c.urllib.request,'build_opener') as factory:
            factory.return_value.open.return_value=response
            c.require_access('https://test-team.pages.dev/surface/index.json')


if __name__=='__main__':unittest.main()