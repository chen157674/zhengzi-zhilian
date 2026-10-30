$FitnessProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not (Test-Path -LiteralPath (Join-Path $FitnessProjectRoot 'fitness_app\app.py'))) { throw '应用目录结构无效。' }
foreach ($FitnessRelativeDirectory in @('.tmp\build', '.cache\pip', '.cache\npm', '.cache\matplotlib', '.cache\xdg')) {
    $FitnessOutputDirectory = [IO.Path]::GetFullPath((Join-Path $FitnessProjectRoot $FitnessRelativeDirectory))
    if (-not $FitnessOutputDirectory.StartsWith($FitnessProjectRoot + '\')) { throw '输出路径越界。' }
    if (-not (Test-Path -LiteralPath $FitnessOutputDirectory)) {
        New-Item -ItemType Directory -Path $FitnessOutputDirectory -Force | Out-Null
    }
}
$env:TEMP = Join-Path $FitnessProjectRoot '.tmp\build'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUTF8 = '1'
$env:PIP_CACHE_DIR = Join-Path $FitnessProjectRoot '.cache\pip'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_REQUIRE_VIRTUALENV = '1'
$env:npm_config_cache = Join-Path $FitnessProjectRoot '.cache\npm'
$env:npm_config_update_notifier = 'false'
$env:MPLCONFIGDIR = Join-Path $FitnessProjectRoot '.cache\matplotlib'
$env:XDG_CACHE_HOME = Join-Path $FitnessProjectRoot '.cache\xdg'
$env:CUDA_CACHE_PATH = Join-Path $FitnessProjectRoot '.cache\cuda'
$env:TORCH_HOME = Join-Path $FitnessProjectRoot '.cache\torch'
$env:TORCHINDUCTOR_CACHE_DIR = Join-Path $FitnessProjectRoot '.cache\torchinductor'
$env:TRITON_CACHE_DIR = Join-Path $FitnessProjectRoot '.cache\triton'
$env:HF_HOME = Join-Path $FitnessProjectRoot '.cache\huggingface'
$env:HF_HUB_CACHE = Join-Path $FitnessProjectRoot '.cache\huggingface\hub'
$env:HF_DATASETS_CACHE = Join-Path $FitnessProjectRoot '.cache\huggingface\datasets'
