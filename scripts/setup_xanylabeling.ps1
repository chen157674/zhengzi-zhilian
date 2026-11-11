param([switch]$DownloadOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\07_Projects\05_PersonalProjects\10_fitness-assistant'
$packageRoot = 'D:\software_installation_package'
$packageDirectory = Join-Path $packageRoot '54_X-AnyLabeling'
$applicationRoot = 'E:\01_SoftWare\01_Engineering\X-AnyLabeling'
$version = '4.2.2'
$filename = 'X-AnyLabeling-v4.2.2-Windows-CPU-Setup.exe'
$url = 'https://github.com/CVHub520/X-AnyLabeling/releases/download/v4.2.2/X-AnyLabeling-v4.2.2-Windows-CPU-Setup.exe'
$expectedHash = 'cc5e16c539b4dab220ff5bbed7f966cedb7d0e4fc3b4c2a2e06cecbda565e221'
$expectedSize = 349876199
$evidence = Join-Path $projectRoot 'data\verification\round3\xanylabeling-install.json'
$setupLog = Join-Path $projectRoot 'data\verification\round3\xanylabeling-install.log'
$installer = Join-Path $packageDirectory $filename
$partial = $installer + '.part'
foreach ($parent in @($projectRoot,$packageRoot,(Split-Path -Parent $applicationRoot))) {
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "安装父目录不存在：$parent" }
}
if (-not (Test-Path -LiteralPath $packageDirectory)) { New-Item -ItemType Directory -Path $packageDirectory | Out-Null }
$record = @{version=$version;source=$url;expected_sha256=$expectedHash;expected_bytes=$expectedSize;installer=$installer;application_root=$applicationRoot;phase='download'}
if ((Test-Path -LiteralPath $partial) -and -not (Test-Path -LiteralPath $evidence)) { throw '下载中间文件归属不明，不覆盖' }
$record | ConvertTo-Json | Set-Content -LiteralPath $evidence -Encoding utf8NoBOM
if (-not (Test-Path -LiteralPath $installer)) {
    & curl.exe --fail --location --silent --show-error --retry 3 --retry-all-errors --retry-delay 2 --connect-timeout 20 --speed-time 60 --speed-limit 8192 --max-time 1200 --max-filesize $expectedSize --continue-at - --output $partial $url
    if ($LASTEXITCODE -ne 0) { throw "官方下载未完成，保留本次中间文件供续传，curl=$LASTEXITCODE" }
    if ((Get-Item -LiteralPath $partial).Length -ne $expectedSize) { throw '安装包字节数不匹配，不执行' }
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw '安装包SHA不匹配，不执行' }
    Move-Item -LiteralPath $partial -Destination $installer
}
if ((Get-Item -LiteralPath $installer).Length -ne $expectedSize -or (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw '现有安装包校验失败，不覆盖或执行' }
$record.phase='download_verified';$record | ConvertTo-Json | Set-Content -LiteralPath $evidence -Encoding utf8NoBOM
if ($DownloadOnly) { $record | ConvertTo-Json -Compress; exit 0 }
if (Test-Path -LiteralPath $applicationRoot) { throw '软件目录已经存在，先核实版本，不覆盖' }
$env:TEMP=Join-Path $projectRoot '.tmp\runtime';$env:TMP=$env:TEMP
$arguments=@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/UPDATESTAGE=1',('/DIR="'+$applicationRoot+'"'),('/LOG="'+$setupLog+'"'))
$process=Start-Process -FilePath $installer -ArgumentList $arguments -WorkingDirectory $packageDirectory -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "安装器未成功，exit=$($process.ExitCode)，日志：$setupLog" }
$exe=Join-Path $applicationRoot 'X-AnyLabeling.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw '安装器返回成功但未找到目标exe' }
$record.phase='extracted';$record.exe=$exe;$record.setup_exit_code=$process.ExitCode
$record | ConvertTo-Json | Set-Content -LiteralPath $evidence -Encoding utf8NoBOM
$record | ConvertTo-Json -Compress
