[CmdletBinding()]
param([ValidateSet('start', 'stop', 'status')][string]$Action = 'start')

# One entry point; ownership/cleanup stays in the existing tested launcher.
$ErrorActionPreference = 'Stop'
$demoEntryLauncher = Join-Path $PSScriptRoot 'scripts\operations\public_demo.ps1'
$demoEntryPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$demoEntryScript = Join-Path $PSScriptRoot 'scripts\operations\public_demo.py'
if ($Action -eq 'stop') {
    & $demoEntryLauncher -Stop
    exit $LASTEXITCODE
}
if ($Action -eq 'status') {
    & $demoEntryLauncher -Status
    exit $LASTEXITCODE
}

function Read-DemoEntryState {
    $demoEntryOutput = & $demoEntryPython $demoEntryScript --status
    if ($LASTEXITCODE -ne 0) { throw 'Demo status unavailable.' }
    return ($demoEntryOutput | ConvertFrom-Json)
}
$demoEntryState = Read-DemoEntryState
if (-not $demoEntryState.active) {
    & $demoEntryLauncher -Background
    $demoEntryState = Read-DemoEntryState
}
if (-not $demoEntryState.active -or $demoEntryState.phase -ne 'running') {
    $demoEntryDeadline = [DateTime]::UtcNow.AddSeconds(95)
    do {
        Start-Sleep -Milliseconds 1000
        $demoEntryState = Read-DemoEntryState
        if ($demoEntryState.active -and $demoEntryState.phase -eq 'running') { break }
    } while ([DateTime]::UtcNow -lt $demoEntryDeadline)
}
if (-not $demoEntryState.active -or $demoEntryState.phase -ne 'running') {
    throw 'Demo did not become ready. Run .\demo.ps1 status for diagnostics.'
}
Write-Output ('데모 주소: {0}' -f $demoEntryState.url)
Write-Output 'PC와 연구실 모델 연결이 켜져 있을 때 접속할 수 있습니다. 종료: .\demo.ps1 stop'
