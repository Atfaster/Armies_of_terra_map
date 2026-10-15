import subprocess
import unittest
from pathlib import Path


class ReleaseLookupTest(unittest.TestCase):
    def test_expected_missing_release_and_unexpected_error_under_stop(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
$ErrorActionPreference = 'Stop'
. .\scripts\github_release.ps1
function gh { $global:LASTEXITCODE=1; Write-Error 'release not found' }
if ($null -ne (Get-TeamMapRelease -Tag map-test -Repository test/repo)) { throw 'Expected missing release' }
if ($ErrorActionPreference -ne 'Stop') { throw 'Caller error policy changed' }
function gh { $global:LASTEXITCODE=0; '{"assets":[{"name":"map.zip","size":42}]}' }
$release = Get-TeamMapRelease -Tag map-test -Repository test/repo
if ($release.assets[0].name -ne 'map.zip' -or $release.assets[0].size -ne 42) { throw 'Invalid existing asset' }
function gh { $global:LASTEXITCODE=1; Write-Error 'authentication failed' }
$rejected = $false
try { Get-TeamMapRelease -Tag map-test -Repository test/repo } catch { $rejected = $_.Exception.Message -match 'authentication failed' }
if (-not $rejected) { throw 'Unexpected error swallowed' }
'''
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-Command', script], cwd=root, capture_output=True,
                                text=True, errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
