$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'environment.ps1')
if (-not (Test-Path Env:FITNESS_BODY_LEAN)) {$env:FITNESS_BODY_LEAN='1'}
if (-not (Test-Path Env:FITNESS_MOVEMENT_GUIDANCE)) {$env:FITNESS_MOVEMENT_GUIDANCE='1'}
$FitnessPython=Join-Path $FitnessProjectRoot '.venv\Scripts\python.exe'
if ($env:FITNESS_PYTHON) {$FitnessPython=[IO.Path]::GetFullPath($env:FITNESS_PYTHON)}
if (-not (Test-Path -LiteralPath $FitnessPython)) {throw '请先双击准备运行环境.cmd安装依赖。'}
& $FitnessPython -B -m fitness_app @args
exit $LASTEXITCODE
