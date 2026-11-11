param()
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$workspace=Join-Path $projectRoot 'data\annotation\x-anylabeling'
$exe='E:\01_SoftWare\01_Engineering\X-AnyLabeling\X-AnyLabeling.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'X-AnyLabeling尚未安装完成' }
if (-not (Test-Path -LiteralPath (Join-Path $workspace 'manifest.json'))) { throw '请先准备本项目标注材料' }
$applicationData=Join-Path $workspace '.application'
$env:TEMP=Join-Path $applicationData 'temp';$env:TMP=$env:TEMP
if (-not (Test-Path -LiteralPath $env:TEMP)) { New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null }
$env:APPDATA=Join-Path $applicationData 'roaming'
$env:LOCALAPPDATA=Join-Path $applicationData 'local'
$env:XDG_CACHE_HOME=Join-Path $applicationData 'cache'
$env:HF_HOME=Join-Path $projectRoot '.cache\huggingface'
$env:TORCH_HOME=Join-Path $projectRoot '.cache\torch'
$env:CUDA_CACHE_PATH=Join-Path $projectRoot '.cache\cuda'
$arguments=@('--work-dir',('"'+$workspace+'"'),'--config',('"'+(Join-Path $workspace '.xanylabelingrc')+'"'),'--no-auto-update-check')
$process=Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $workspace -WindowStyle Normal -PassThru
$process | Select-Object Id,Path
