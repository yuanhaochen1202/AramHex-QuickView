# Windows 用户版构建。使用 -PortableOnly 只生成便携包；安装包另需 Inno Setup 6。
# 输出位于 build/release/user。-TestTag test.1 可生成带调试标记的本地测试包。
param([string]$PythonExe, [string]$InnoCompiler, [switch]$PortableOnly, [string]$TestTag,
      [ValidateSet('user')][string]$Channel = 'user')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'  # 控制台不是 UTF-8 时（如 GitHub 的 Windows 构建机）输出中文也不能崩
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$Channel = $Channel.ToLowerInvariant()
$channelLabel = '用户版'

function Reset-BuildDirectory([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $buildRoot = [IO.Path]::GetFullPath((Join-Path $root 'build')) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理构建目录之外的路径：$resolved"
    }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    New-Item -ItemType Directory -Path $resolved -Force | Out-Null
}

$py = if ($PythonExe) { (Get-Command $PythonExe).Source } else { Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path $py)) { throw "缺少 Python：先运行 python -m venv .venv 并安装 requirements-build.txt" }
& $py -m PyInstaller --version
if ($LASTEXITCODE) { throw '缺少 PyInstaller：pip install -r requirements-build.txt' }

# 测试标记写进 lolhex/_build.py（不进仓库）；不带 -TestTag 时删掉上次留下的，免得正式包带上测试号
$buildInfo = Join-Path $root 'lolhex\_build.py'
if ($TestTag) {
    if ($TestTag -notmatch '^test\.\d+$') { throw '-TestTag 要写成 test.<数字>，例如 test.1' }
    Set-Content -LiteralPath $buildInfo -Encoding utf8 -Value "TEST_TAG = `"$TestTag`"  # packaging/build.ps1 -TestTag 生成，测试包专用"
} elseif (Test-Path -LiteralPath $buildInfo) {
    Remove-Item -LiteralPath $buildInfo
}
$version = (& $py -c "import pathlib,lolhex; assert pathlib.Path(lolhex.__file__).resolve() == pathlib.Path('lolhex/__init__.py').resolve(), 'Python imported lolhex outside the current checkout'; print(lolhex.__version__)").Trim()
if ($LASTEXITCODE) { throw 'Python 没有使用当前源码目录，不能打包' }
Write-Host "版本 $version，渠道 $Channel（$channelLabel）"
if (-not (Test-Path (Join-Path $root 'lolhex\data\bundled\latest.json'))) {
    throw '缺少内置统计数据：先运行 python scripts/update_data.py'
}
& $py -c "from lolhex.data.public import Dataset, BUNDLED_DIR; d = Dataset(BUNDLED_DIR, 'bundled'); print('内置数据', d.patch, d.snapshot_at)"
if ($LASTEXITCODE) { throw '内置统计数据校验失败' }

$originalPath = $env:PATH
$originalPythonPath = $env:PYTHONPATH
$originalPyinstallerConfig = $env:PYINSTALLER_CONFIG_DIR
try {
    $basePython = (& $py -c 'import sys; print(sys.base_prefix)').Trim()
    $env:PATH = @((Split-Path $py), $basePython, (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'
    $env:PYTHONPATH = $null
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $root "build\pyinstaller-cache\$Channel"
    Push-Location packaging
    try {
        & $py -m PyInstaller lolhex.spec --clean --noconfirm --distpath "..\build\dist\$Channel" --workpath "..\build\work\$Channel" -- --channel $Channel
        $code = $LASTEXITCODE
    } finally { Pop-Location }
} finally {
    $env:PATH = $originalPath
    $env:PYTHONPATH = $originalPythonPath
    $env:PYINSTALLER_CONFIG_DIR = $originalPyinstallerConfig
}
if ($code) { throw 'PyInstaller 打包失败' }
$dist = Join-Path $root "build\dist\$Channel\AramHex"
# 许可必须在安装版和便携版使用的同一份 dist 中完整可读；缺失时停止发布。
# 根目录保存项目/数据许可，第三方原文保存在 _internal/THIRD-PARTY-LICENSES。
& $py (Join-Path $root 'packaging\license_bundle.py') --root $root --dist $dist
if ($LASTEXITCODE) { throw '发布许可文件复制或校验失败，不能生成安装包' }
$buildMetadataPath = Join-Path $dist 'build.json'
if (Test-Path -LiteralPath $buildMetadataPath) { Remove-Item -LiteralPath $buildMetadataPath }

# 真实 Windows 窗口插件下跑一遍 Qt（QtCore/Gui/Widgets/Network），再跑一遍 OpenCV 和文字识别
$report = Join-Path $root "build\gui-smoke-$Channel.json"
if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
$smoke = Start-Process -FilePath (Join-Path $dist 'AramHex.exe') -ArgumentList @('--self-test-gui', ('"' + $report + '"')) -WindowStyle Hidden -PassThru
if (-not $smoke.WaitForExit(180000)) { $smoke.Kill(); throw '自检超时' }
if ($smoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw 'Qt GUI 自检失败，不生成安装包' }
$gui = Get-Content -Raw -Encoding UTF8 -LiteralPath $report | ConvertFrom-Json
if (-not $gui.success -or $gui.version -ne $version -or $gui.platform -ne 'windows' -or -not $gui.ocr_ok) { throw "自检结果不对（界面或文字识别）：$($gui | ConvertTo-Json -Compress)" }
Write-Host "自检通过：Qt $($gui.qt)，文字识别读到「$($gui.ocr_text)」"

if ($TestTag) {
    # 放进同一份程序目录，测试安装版和便携版都默认开启调试；运行时已经支持这个标记。
    [IO.File]::WriteAllBytes((Join-Path $dist 'debug.txt'), [byte[]]@())
}

$setup = $null
if (-not $PortableOnly) {
    $iscc = @($InnoCompiler, (Join-Path $root '.tools\inno\ISCC.exe'), "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
        Where-Object { $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw '缺少 Inno Setup 6：winget install --id JRSoftware.InnoSetup -e' }
    & $iscc "/DAppVersion=$version" "/DBuildChannel=$Channel" packaging\LolHex.iss
    if ($LASTEXITCODE) { throw 'Inno Setup 编译失败' }
}

$out = Join-Path $root "build\release\$Channel"
Reset-BuildDirectory $out
if (-not $PortableOnly) {
    $setup = "AramHex-Setup-$version-$Channel.exe"
    Copy-Item (Join-Path $root "build\installer\$Channel\$setup") (Join-Path $out $setup)
}

# 便携版：同一份程序目录 + portable.txt（有它时配置、日志、数据都放在程序目录的 userdata 下）
$portableFolder = 'AramHex'
$stage = Join-Path $root "build\portable\$Channel\$portableFolder"
Reset-BuildDirectory $stage
Copy-Item -Recurse (Join-Path $dist '*') $stage
Set-Content -LiteralPath (Join-Path $stage 'portable.txt') -Encoding utf8 -Value @"
这是便携版：配置、日志和下载的数据都放在本目录的 userdata 文件夹里，不写系统目录。
删除整个文件夹即可卸载。当前渠道：$channelLabel。
发现新版时会弹窗询问，确认后从腾讯云 COS 下载经签名清单校验的更新。数据库按原提供方地址正常更新。
程序版本检查失败也等下次启动，运行期间不定时检查、不自动重试、不自动安装；用户点击确认的这次更新可继续完成。
"@
$zip = "AramHex-Portable-$version-$Channel.zip"
Compress-Archive -Path $stage -DestinationPath (Join-Path $out $zip) -CompressionLevel Optimal

$sums = foreach ($name in @($setup, $zip) | Where-Object { $_ }) {
    '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $out $name)).Hash.ToLowerInvariant(), $name
}
Set-Content -LiteralPath (Join-Path $out 'SHA256SUMS.txt') -Encoding ascii -Value $sums
# 发布前用构建记录验证 EXE 的版本、渠道及内容。打包完成后才写入 dist，避免把本机路径放进便携包。
$programPath = Join-Path $dist 'AramHex.exe'
$channelOutputs = [ordered]@{}
$channelOutputs[$Channel] = [ordered]@{
    path = [IO.Path]::GetFullPath($programPath)
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $programPath).Hash.ToLowerInvariant()
}
$metadata = [ordered]@{ version = $version; outputs = $channelOutputs }
[IO.File]::WriteAllText($buildMetadataPath, ($metadata | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
Get-ChildItem $out | ForEach-Object { '{0}  {1:N1} MB' -f $_.Name, ($_.Length / 1MB) }
# 测试标记只给这一次打包用，打完删掉；中途失败留下的，下次不带 -TestTag 打包时开头也会删
if (Test-Path -LiteralPath $buildInfo) { Remove-Item -LiteralPath $buildInfo }
