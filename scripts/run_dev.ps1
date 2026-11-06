<#
  Spuštění NetMonitoru ze zdrojů na Windows (bez sestavení EXE) – pro rychlé ověření.
      powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1              # widget
      powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1 -Diagnose    # textová diagnostika
      powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1 -Test        # automatické testy
  Vytvoří .venv ve složce projektu; nic jiného v systému nemění.
#>
param([switch]$Diagnose, [switch]$Test)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path .venv)) {
    $ok = $false
    foreach ($v in @("3.13", "3.12")) {
        try { & py "-$v" -m venv .venv 2>$null; if ($LASTEXITCODE -eq 0 -and (Test-Path .venv\Scripts\python.exe)) { $ok = $true; break } } catch {}
    }
    if (-not $ok) { throw "Python 3.12+ nenalezen (py launcher). Instalace: winget install Python.Python.3.13" }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
    & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
}
$env:PYTHONPATH = (Resolve-Path src).Path
if ($Test) { & .\.venv\Scripts\python.exe -m pytest -q; exit $LASTEXITCODE }
if ($Diagnose) { & .\.venv\Scripts\python.exe -m netmonitor --diagnose; exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m netmonitor --debug
