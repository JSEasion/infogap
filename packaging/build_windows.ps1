# 构建 Windows 桌面版：dist\InfoGap\ + 安装包 dist\InfoGap-Setup-<ver>.exe
# 用法（在 Windows 上，项目根目录执行）：powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# 前置：已安装 Python 3.10+ 并在 PATH；安装了 Inno Setup 6 则产出安装器，否则产出 zip
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } else { "python" }

Write-Host "▶ 使用 Python: $Python"
& $Python -c "import PyInstaller, webview" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "▶ 安装打包依赖"
  & $Python -m pip install -e "$Root[desktop,pack]"
  if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}

Write-Host "▶ PyInstaller 打包"
& $Python -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" "$PSScriptRoot\market_lens.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) { $Iscc = "${env:ProgramFiles}\Inno Setup 6\ISCC.exe" }

if (Test-Path $Iscc) {
  Write-Host "▶ Inno Setup 生成安装器"
  # 简中语言包不在 Inno 官方安装里（非官方翻译），构建时下载；失败则生成英文安装器
  $isl = "$PSScriptRoot\ChineseSimplified.isl"
  if (-not (Test-Path $isl)) {
    try {
      Invoke-WebRequest -Uri "https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/ChineseSimplified.isl" `
        -OutFile $isl -UseBasicParsing
    } catch { Write-Host "⚠ 中文语言包下载失败：$($_.Exception.Message)" }
  }
  if (Test-Path $isl) {
    & $Iscc "$PSScriptRoot\installer.iss"
  } else {
    Write-Host "⚠ 退回英文安装器"
    $tmp = "$PSScriptRoot\installer_en.iss"
    (Get-Content "$PSScriptRoot\installer.iss" -Raw -Encoding UTF8) `
      -replace 'Name: "chinesesimplified";[^\r\n]*\r?\n', '' | Set-Content $tmp -Encoding UTF8
    & $Iscc $tmp
    Remove-Item $tmp -ErrorAction SilentlyContinue
  }
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup 失败" }
} else {
  Write-Host "⚠ 未找到 Inno Setup 6，改为输出 zip（免安装版）"
  if (Test-Path "$Root\dist\InfoGap-win64.zip") { Remove-Item "$Root\dist\InfoGap-win64.zip" }
  Compress-Archive -Path "$Root\dist\infogap" -DestinationPath "$Root\dist\InfoGap-win64.zip"
}

Write-Host "✔ 完成，产物在 dist\ 目录"
