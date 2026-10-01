[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(ParameterSetName = 'Start')]
    [switch]$Background,
    [Parameter(ParameterSetName = 'Start')]
    [ValidateRange(0.1, 86400)]
    [double]$MaxRuntimeSeconds,
    [Parameter(Mandatory = $true, ParameterSetName = 'Stop')]
    [switch]$Stop,
    [Parameter(Mandatory = $true, ParameterSetName = 'Status')]
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$demoProjectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$demoPython = Join-Path $demoProjectRoot '.venv\Scripts\python.exe'
$demoLauncher = Join-Path $PSScriptRoot 'public_demo.py'
if (-not (Test-Path -LiteralPath $demoPython -PathType Leaf)) {
    throw 'Project Python runtime unavailable.'
}
$demoArguments = @($demoLauncher)
if ($Stop) { $demoArguments += '--stop' }
if ($Status) { $demoArguments += '--status' }
if ($PSBoundParameters.ContainsKey('MaxRuntimeSeconds')) {
    $demoArguments += '--max-runtime-seconds'
    $demoArguments += $MaxRuntimeSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
}
if ($Background) {
    $demoProcessArguments = @(('"{0}"' -f $demoLauncher))
    if ($demoArguments.Count -gt 1) {
        $demoProcessArguments += $demoArguments[1..($demoArguments.Count - 1)]
    }
    $demoProcess = Start-Process -FilePath $demoPython -ArgumentList $demoProcessArguments `
        -WorkingDirectory $demoProjectRoot -WindowStyle Hidden -PassThru
    @{ event = 'public_demo_launcher_started'; launcher_pid = $demoProcess.Id } | ConvertTo-Json -Compress
} else {
    $demoStopAttempts = 0
    do {
        & $demoPython @demoArguments
        $demoExitCode = $LASTEXITCODE
        if (-not $Stop -or $demoExitCode -ne 3 -or $demoStopAttempts -ge 10) { break }
        # The new lock holder is still publishing its identity. Retry only the
        # explicit Stop retry result, then return failure if it remains busy.
        $demoStopAttempts += 1
        Start-Sleep -Milliseconds 100
    } while ($true)
    exit $demoExitCode
}
