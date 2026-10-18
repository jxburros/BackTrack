# Runs the BackTrack training pipeline once, for Windows Task Scheduler (docs/training-at-home.md).
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File training\schedule\run-pipeline.ps1 `
#       -Config C:\Users\me\backtrack-training\pipeline.json [-Python C:\path\to\python.exe] [-Steps render,perform,...]
#
# Appends what it prints to <runs_dir>\scheduled.log (each run's own logs are in its run folder)
# and exits with the pipeline's exit code, so Task Scheduler's "Last Run Result" shows failures.

param(
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$Python = "python",
    [string]$Steps = ""
)

$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repo

# runs_dir from the config, for the log (the pipeline resolves it the same way: ~ and relative to the checkout)
$cfg = Get-Content -Raw -Path $Config | ConvertFrom-Json
$runs = if ($cfg.runs_dir) { $cfg.runs_dir } else { "~/backtrack-training/runs" }
$runs = $runs -replace '^~', $env:USERPROFILE
if (-not [System.IO.Path]::IsPathRooted($runs)) { $runs = Join-Path $repo $runs }
New-Item -ItemType Directory -Force -Path $runs | Out-Null
$log = Join-Path $runs "scheduled.log"

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
$pyArgs = @("-m", "training.pipeline", "--config", $Config)
if ($Steps) { $pyArgs += @("--steps", $Steps) }

"==== $(Get-Date -Format o) $Python $($pyArgs -join ' ')" | Out-File -FilePath $log -Append -Encoding utf8
# Windows PowerShell 5.1 turns a native program's stderr into errors; let them through as text.
$ErrorActionPreference = "Continue"
& $Python @pyArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
"==== exit $code" | Out-File -FilePath $log -Append -Encoding utf8
exit $code
