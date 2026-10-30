param([string]$Python = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'environment.ps1')
$FitnessVenvPython = Join-Path $FitnessProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $FitnessVenvPython)) {
    if (-not $Python) {
        $FitnessLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($FitnessLauncher) {
            $FitnessCandidate = & $FitnessLauncher.Source -3.12 -c 'import sys;print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $FitnessCandidate) { $Python = $FitnessCandidate.Trim() }
        }
        if (-not $Python) { $Python = 'python' }
    }
    $FitnessCommand = Get-Command $Python -ErrorAction Stop
    $FitnessVersion = & $FitnessCommand.Source -c 'import sys;print("%d.%d"%sys.version_info[:2])'
    if ($FitnessVersion.Trim() -ne '3.12') { throw '请使用Python 3.12，或通过-Python指定其解释器路径。' }
    & $FitnessCommand.Source -m venv (Join-Path $FitnessProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '虚拟环境创建失败。' }
}
& $FitnessVenvPython -m pip install -r (Join-Path $FitnessProjectRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖安装未完成。' }
& $FitnessVenvPython -B (Join-Path $PSScriptRoot 'setup_resources.py')
if ($LASTEXITCODE -ne 0) { throw '模型资源准备未完成。' }
Write-Host '准备完成。运行 start.cmd --init-admin 创建本人管理员，再运行 start.cmd。'
