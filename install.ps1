# Voice2Text 一键安装脚本
# 用法：克隆仓库后，在仓库目录运行  .\install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "=== Voice2Text 安装 ===" -ForegroundColor Cyan

# 1. 确保 uv 可用
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "未找到 uv，正在安装..." -ForegroundColor Yellow
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. 创建虚拟环境
Write-Host "创建 Python 3.11 虚拟环境..."
Set-Location $root
uv venv --python 3.11

# 3. 安装依赖
Write-Host "安装依赖（约 1-3 分钟）..."
$env:VIRTUAL_ENV = "$root\.venv"
uv pip install -r requirements.txt

# 4. 生成个性化术语表
if (-not (Test-Path "$root\glossary.txt")) {
    Copy-Item "$root\glossary.example.txt" "$root\glossary.txt"
    Write-Host "已生成 glossary.txt（可编辑加入你的常用专有名词）"
}

Write-Host ""
Write-Host "=== 安装完成 ===" -ForegroundColor Green
Write-Host "启动方式：双击 start.bat，或运行  .\run.ps1"
Write-Host "首次启动会自动下载语音模型（约 1.5GB），请耐心等待。"
Write-Host "配置文件 config.toml 会在首次启动时自动生成。"
