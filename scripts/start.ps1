. (Join-Path $PSScriptRoot 'environment.ps1')
Set-Location -LiteralPath $FitnessProjectRoot
$FitnessPython = Join-Path $FitnessProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $FitnessPython)) { throw '项目环境尚未准备，请先完成开发步骤 01。' }
& $FitnessPython -B -m fitness_app @args
exit $LASTEXITCODE
