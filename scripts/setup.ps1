param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'environment.ps1')
$FitnessVenvPython = Join-Path $FitnessProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $FitnessVenvPython)) {
    $FitnessCommand = Get-Command $Python -ErrorAction Stop
    & $FitnessCommand.Source -m venv (Join-Path $FitnessProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '虚拟环境创建失败。' }
}
& $FitnessVenvPython -m pip install -r (Join-Path $FitnessProjectRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖安装未完成。' }
& $FitnessVenvPython -B (Join-Path $PSScriptRoot 'setup_resources.py')
if ($LASTEXITCODE -ne 0) { throw '模型资源准备未完成。' }
Write-Host '准备完成。运行 start.cmd --init-admin 创建本人管理员，再运行 start.cmd。'
