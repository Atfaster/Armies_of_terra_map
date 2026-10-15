function Get-TeamMapRelease {
    param([string]$Tag, [string]$Repository)

    # Windows PowerShell converts native stderr into ErrorRecord objects. An
    # absent release is expected here; keep Continue local to this lookup.
    $ErrorActionPreference = 'Continue'
    $taskResponse = @(& gh release view $Tag -R $Repository --json assets 2>&1)
    $taskStatus = $LASTEXITCODE
    $taskText = ($taskResponse | ForEach-Object { $_.ToString() }) -join "`n"
    if ($taskStatus -eq 0) {
        return ($taskText | ConvertFrom-Json -ErrorAction Stop)
    }
    if ($taskStatus -eq 1 -and $taskText.Trim() -eq 'release not found') {
        return $null
    }
    throw "GitHub release lookup failed (exit $taskStatus): $taskText"
}
