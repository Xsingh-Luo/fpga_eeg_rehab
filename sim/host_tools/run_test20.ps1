# run_test20.ps1 —— 一组 20 trial 快速测试：采集 + 质量验收 + 快速识别自检
param(
    [string]$Port = "",
    [string]$Name = "subj01_test20"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "找不到 $py，请在项目仓库根目录运行"; exit 1 }
if (-not $Port) { $Port = Read-Host "输入 Cyton Dongle 的串口（如 COM4）" }
$stamp = Get-Date -Format "yyyyMMdd"

Write-Host "============================================================"
Write-Host " 1/3 采集一组 20 trial（左右各 10，约 4~5 分钟）"
Write-Host "============================================================"
& $py tools\openbci_record.py --port $Port --paradigm mi2 --trials-per-class 10 --blocks 1 --name $Name

$dir = Join-Path $root "data\raw\openbci\$stamp"
$bin = Join-Path $dir ($Name + "_raw.bin")
$ev  = Join-Path $dir ($Name + "_events.csv")
if (-not (Test-Path $bin)) {
    Write-Host "没有找到录制文件：$bin（串口没连上？看上面的报错）"
    exit 1
}

Write-Host ""
Write-Host "============================================================"
Write-Host " 2/3 数据质量验收（看『滤波后』那一组，PASS 才算信号可用）"
Write-Host "============================================================"
& $py tools\eeg_quality_check.py --bin $bin --events $ev

Write-Host ""
Write-Host "============================================================"
Write-Host " 3/3 20-trial 快速识别自检（LOO + 置换检验）"
Write-Host "============================================================"
& $py tools\quick_mi_test.py --bin $bin --events $ev

Write-Host ""
Write-Host "完成。把 $dir 目录（或上面的输出）发给 AI，继续正式训练/调试。"
