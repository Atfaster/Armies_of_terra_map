param([switch]$CreateRepository)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'github_release.ps1')
$taskProject = Split-Path -Parent $PSScriptRoot
$taskSeason = Join-Path $taskProject 'seasons/steel-soil'
$taskCurrent = Get-Content -LiteralPath (Join-Path $taskSeason 'current.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRelease = Join-Path $taskSeason ('builds/' + $taskCurrent.build_id)
$taskReport = Get-Content -LiteralPath (Join-Path $taskRelease 'report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
python (Join-Path $PSScriptRoot 'verify_season.py')
if ($LASTEXITCODE -ne 0) { throw 'Release verification failed' }
$taskRepo = 'atfaster/terrafirmacolonies-map'
$taskCheckout = Join-Path $env:TEMP ('steel-soil-publish-' + [guid]::NewGuid().ToString('N'))
if ($CreateRepository) {
    New-Item -ItemType Directory -Path $taskCheckout | Out-Null
    git -C $taskCheckout init -b main
} else {
    gh repo clone $taskRepo $taskCheckout
}
if ($LASTEXITCODE -ne 0) { throw 'Checkout failed' }
$taskCheckout = (Resolve-Path -LiteralPath $taskCheckout).Path
$taskGitName = git -C $taskProject config user.name
$taskGitEmail = git -C $taskProject config user.email
if (-not $taskGitName -or -not $taskGitEmail) { throw 'Git author identity missing' }
git -C $taskCheckout config user.name $taskGitName
git -C $taskCheckout config user.email $taskGitEmail
git -C $taskCheckout config core.autocrlf false
if (-not $CreateRepository) {
    # Delete only tracked files in this disposable checkout. No recursive deletion.
    $taskTracked = (git -C $taskCheckout ls-files -z) -split "`0"
    foreach ($taskRelative in $taskTracked) {
        if (-not $taskRelative) { continue }
        $taskTarget = [IO.Path]::GetFullPath((Join-Path $taskCheckout $taskRelative))
        if (-not $taskTarget.StartsWith($taskCheckout + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Path escaped checkout' }
        if (Test-Path -LiteralPath $taskTarget -PathType Leaf) { Remove-Item -LiteralPath $taskTarget }
    }
}
foreach ($taskEntry in $taskReport.sha256.PSObject.Properties) {
    $taskSource = Join-Path (Join-Path $taskRelease 'site') $taskEntry.Name
    $taskTarget = Join-Path $taskCheckout $taskEntry.Name
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $taskTarget) | Out-Null
    Copy-Item -LiteralPath $taskSource -Destination $taskTarget
    if ((Get-FileHash -LiteralPath $taskTarget -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskEntry.Value) { throw 'Publication copy checksum mismatch' }
}
[IO.File]::WriteAllText((Join-Path $taskCheckout '.gitignore'), "desktop.ini`n", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $taskCheckout '.gitattributes'), "* -text`n", [Text.UTF8Encoding]::new($false))
$taskReadme = @"
# TerraFirmaColonies: Steel & Soil — World Map

Shared JourneyMap surface exploration, block metadata and known MineColonies borders.

Build: $($taskCurrent.build_id). Tiles: $($taskReport.output_tiles).

Download: https://atfaster.github.io/terrafirmacolonies-map/download.html
Stable ZIP: https://atfaster.github.io/terrafirmacolonies-map/downloads/Steel-Soil-JourneyMap-latest.zip

This repository contains the generated public website and the latest merged
JourneyMap ZIP. Original player snapshots and backups are retained separately.
Leaflet and Geoman licenses are included in vendor/.
"@
[IO.File]::WriteAllText((Join-Path $taskCheckout 'README.md'), $taskReadme, [Text.UTF8Encoding]::new($false))
git -C $taskCheckout add -A
if ($LASTEXITCODE -ne 0) { throw 'Staging failed' }
git -C $taskCheckout diff --cached --quiet
$taskDiffCode = $LASTEXITCODE
if ($taskDiffCode -gt 1) { throw 'Staged diff failed' }
if ($LASTEXITCODE -eq 1) {
    git -C $taskCheckout commit --quiet -m "Publish Steel & Soil map $($taskCurrent.build_id) ($($taskReport.output_tiles) tiles)"
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
}
if ($CreateRepository) {
    gh repo create $taskRepo --public --description 'TerraFirmaColonies: Steel & Soil shared JourneyMap world map'
    if ($LASTEXITCODE -ne 0) { throw 'Repository creation failed; local checkout retained' }
    git -C $taskCheckout remote add origin ('https://github.com/' + $taskRepo + '.git')
}
git -C $taskCheckout push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'Push failed; local checkout retained' }
$taskLocalHead = git -C $taskCheckout rev-parse HEAD
$taskRemoteHead = (git -C $taskCheckout ls-remote origin refs/heads/main) -split '\s+'
if ($taskLocalHead -ne $taskRemoteHead[0]) { throw 'Remote ref verification failed' }
if ($CreateRepository) {
    gh api ('repos/' + $taskRepo + '/pages') -X POST -f 'source[branch]=main' -f 'source[path]=/'
    if ($LASTEXITCODE -ne 0) { throw 'Pages activation failed' }
}
$taskDownload = Get-Content -LiteralPath (Join-Path $taskRelease 'site/downloads/latest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($taskDownload.storage -eq 'github-release') {
    $taskTag = $taskDownload.release_tag
    $taskAssetFolder = Join-Path $env:TEMP ('steel-soil-asset-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $taskAssetFolder | Out-Null
    $taskAsset = Join-Path $taskAssetFolder $taskDownload.file
    Copy-Item -LiteralPath (Join-Path $taskRelease ('steel-soil-journeymap-' + $taskCurrent.build_id + '.zip')) -Destination $taskAsset
    if ((Get-FileHash -LiteralPath $taskAsset -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskDownload.sha256) { throw 'Release asset checksum mismatch' }
    $taskExisting = Get-TeamMapRelease -Tag $taskTag -Repository $taskRepo
    if ($null -eq $taskExisting) {
        $taskNotes = Join-Path $taskAssetFolder 'notes.txt'
        [IO.File]::WriteAllText($taskNotes, "JourneyMap surface map, block data and colony borders. SHA-256: $($taskDownload.sha256)", [Text.UTF8Encoding]::new($false))
        gh release create $taskTag $taskAsset -R $taskRepo --target $taskLocalHead --title "Team map $($taskCurrent.build_id)" --notes-file $taskNotes
        if ($LASTEXITCODE -ne 0) { throw 'Release asset publication failed; local ZIP retained' }
    } else {
        $taskAssetEntry = @($taskExisting.assets | Where-Object { $_.name -eq $taskDownload.file })
        if ($taskAssetEntry.Count -eq 0) {
            gh release upload $taskTag $taskAsset -R $taskRepo
            if ($LASTEXITCODE -ne 0) { throw 'Release asset upload failed' }
        } else {
            if ([long]$taskAssetEntry[0].size -ne [long]$taskDownload.bytes) { throw 'Existing release asset differs; refused to overwrite it' }
            # Public verification below compares the complete existing asset SHA-256.
        }
    }
}
$taskRecord = @{ repository = $taskRepo; build_id = $taskCurrent.build_id; commit = $taskLocalHead; checkout = $taskCheckout }
$taskRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskSeason 'publication.json') -Encoding UTF8
Write-Output "Published commit verified: $taskLocalHead"
gh api ('repos/' + $taskRepo + '/pages') --jq '{status: .status, url: .html_url}'
if ($LASTEXITCODE -ne 0) { throw 'Pages status lookup failed; commit was uploaded' }
Write-Output 'Waiting for the ZIP to become available on GitHub Pages...'
python (Join-Path $PSScriptRoot 'verify_published.py') --release $taskRelease
if ($LASTEXITCODE -ne 0) { throw 'Commit uploaded, but public ZIP verification failed. Retry publication to check Pages again.' }
