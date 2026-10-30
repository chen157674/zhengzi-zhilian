$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'environment.ps1')
$FitnessEnvPath = Join-Path $FitnessProjectRoot '.env'
$FitnessAllowedNames = @('FITNESS_BODY_LEAN','FITNESS_MOVEMENT_GUIDANCE','FITNESS_PRETRAINED_CORRECTION','FITNESS_ENABLE_CORRECTION_LABELS','FITNESS_DEEPSEEK_API_KEY')
if (Test-Path -LiteralPath $FitnessEnvPath) {
    foreach ($FitnessLine in [IO.File]::ReadAllLines($FitnessEnvPath)) {
        if ($FitnessLine -match '^([A-Z_]+)=(.*)$' -and $FitnessAllowedNames -contains $Matches[1]) {
            [Environment]::SetEnvironmentVariable($Matches[1],$Matches[2].Trim(),'Process')
        }
    }
}
if (-not $env:FITNESS_BODY_LEAN) { $env:FITNESS_BODY_LEAN = '1' }
if (-not $env:FITNESS_MOVEMENT_GUIDANCE) { $env:FITNESS_MOVEMENT_GUIDANCE = '1' }
& (Join-Path $FitnessProjectRoot '.venv\Scripts\python.exe') -B -m fitness_app @args
exit $LASTEXITCODE
