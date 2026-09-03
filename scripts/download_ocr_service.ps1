# 在便携目录中下载 GLM-OCR 模型（可选 -Runtime 一并补齐 llama.cpp 运行时）。
#
# 用法（在便携目录的 PowerShell 中执行）：
#   .\scripts\download_ocr_service.ps1                 # 只下载模型(~1.4GB)
#   .\scripts\download_ocr_service.ps1 -Runtime        # 模型 + CUDA 运行时
#   .\scripts\download_ocr_service.ps1 -RuntimeCpu     # 模型 + CPU 运行时(无 N 卡)
#
# 下载内容：
#   - GLM-OCR-Q8_0.gguf / mmproj-GLM-OCR-Q8_0.gguf  -> ocr_service\llama\models\
#   - llama.cpp b10453 运行时 (mtmd/ggml 等)          -> ocr_service\llama\
#   - CUDA 12.4 运行库 DLL (cublas64_12 等，仅 -Runtime)

param(
    [switch]$Runtime,      # 额外下载 CUDA 12.4 版 llama.cpp 运行时
    [switch]$RuntimeCpu    # 额外下载 CPU 版 llama.cpp 运行时（与 -Runtime 二选一）
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$portableRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$llamaDir = Join-Path $portableRoot "ocr_service\llama"
$modelsDir = Join-Path $llamaDir "models"
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null

function Download-File {
    param([string]$Url, [string]$Dest, [int64]$MinBytes)
    if (Test-Path $Dest) {
        $size = (Get-Item $Dest).Length
        if ($size -ge $MinBytes) {
            Write-Host "  已存在，跳过: $Dest ($([math]::Round($size/1MB)) MB)"
            return
        }
        Remove-Item -Force $Dest
    }
    Write-Host "  下载中: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
    $size = (Get-Item $Dest).Length
    if ($size -lt $MinBytes) {
        Remove-Item -Force $Dest
        throw "下载不完整（$([math]::Round($size/1MB)) MB），已删除，请重试: $Url"
    }
    Write-Host "  完成: $([math]::Round($size/1MB)) MB"
}

# ---- 1) GLM-OCR 模型（两个 gguf，必需）----
Write-Host "[1/3] 下载 GLM-OCR 模型..."
$modelBase = "https://huggingface.co/ggml-org/GLM-OCR-GGUF/resolve/main"
Download-File -Url "$modelBase/GLM-OCR-Q8_0.gguf?download=true" `
    -Dest (Join-Path $modelsDir "GLM-OCR-Q8_0.gguf") -MinBytes 850MB
Download-File -Url "$modelBase/mmproj-GLM-OCR-Q8_0.gguf?download=true" `
    -Dest (Join-Path $modelsDir "mmproj-GLM-OCR-Q8_0.gguf") -MinBytes 430MB

# ---- 2) llama.cpp 运行时（可选）----
$needRuntime = $Runtime -or $RuntimeCpu
$zipDir = Join-Path $env:TEMP "expense_tracker_dl"
New-Item -ItemType Directory -Force -Path $zipDir | Out-Null

if ($needRuntime) {
    Write-Host "[2/3] 下载 llama.cpp b10453 运行时..."
    if ($RuntimeCpu) {
        $binZip = "llama-b10453-bin-win-cpu-x64.zip"
        $binUrl = "https://github.com/ggml-org/llama.cpp/releases/download/b10453/$binZip"
        $binMinBytes = 10MB   # CPU 版 zip 较小(~18MB)
        $cudart = $false
    }
    else {
        $binZip = "llama-b10453-bin-win-cuda-12.4-x64.zip"
        $binUrl = "https://github.com/ggml-org/llama.cpp/releases/download/b10453/$binZip"
        $binMinBytes = 200MB
        $cudart = $true
    }
    $binPath = Join-Path $zipDir $binZip
    Download-File -Url $binUrl -Dest $binPath -MinBytes $binMinBytes

    Write-Host "  解压运行时到 $llamaDir ..."
    $extractDir = Join-Path $zipDir "llama_bin"
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    Expand-Archive -Path $binPath -DestinationPath $extractDir -Force
    # llama.cpp 的 bin zip 内文件在 bin\ 子目录，统一拷贝 exe/dll 到 llama 目录
    $files = Get-ChildItem -Path $extractDir -Recurse -File |
        Where-Object { $_.Extension -in @(".exe", ".dll") }
    foreach ($f in $files) {
        $target = Join-Path $llamaDir $f.Name
        if (-not (Test-Path $target)) { Copy-Item $f.FullName $target }
    }

    if ($cudart) {
        Write-Host "[3/3] 下载 CUDA 12.4 运行库..."
        $cudartZip = "cudart-llama-bin-win-cuda-12.4-x64.zip"
        $cudartPath = Join-Path $zipDir $cudartZip
        Download-File -Url "https://github.com/ggml-org/llama.cpp/releases/download/b10453/$cudartZip" `
            -Dest $cudartPath -MinBytes 100MB
        $cudartExtract = Join-Path $zipDir "cudart"
        if (Test-Path $cudartExtract) { Remove-Item -Recurse -Force $cudartExtract }
        Expand-Archive -Path $cudartPath -DestinationPath $cudartExtract -Force
        foreach ($f in (Get-ChildItem -Path $cudartExtract -Recurse -File -Filter "*.dll")) {
            $target = Join-Path $llamaDir $f.Name
            if (-not (Test-Path $target)) { Copy-Item $f.FullName $target }
        }
    }
    Remove-Item -Recurse -Force $extractDir -ErrorAction SilentlyContinue
}
else {
    Write-Host "[2/3] 跳过运行时（已提供或稍后单独下载）"
    Write-Host "[3/3] 跳过 CUDA 运行库"
}

Remove-Item -Recurse -Force $zipDir -ErrorAction SilentlyContinue

# ---- 结果校验 ----
$cli = Join-Path $llamaDir "llama-mtmd-cli.exe"
Write-Host ""
Write-Host "完成。文件位置:"
Write-Host "  模型: $modelsDir"
$runtimeOk = $needRuntime -and (Test-Path $cli)
if ($runtimeOk) {
    Write-Host "  运行时: $llamaDir （llama-mtmd-cli.exe 已就绪）"
}
Write-Host ""
if ($needRuntime -and -not $runtimeOk) {
    Write-Warning "未找到 llama-mtmd-cli.exe：请检查解压内容，或改从开发机的 ocr_service\llama 直接拷贝。"
}
if (-not $needRuntime) {
    Write-Host "提示：本地 GLM-OCR 还需要 llama.cpp 运行时（llama-mtmd-cli.exe / mtmd.dll 等）。"
    Write-Host "完整一键补齐请执行： .\scripts\download_ocr_service.ps1 -Runtime"
}
Write-Host "验证：在 GUI 中打开一张小票 -> Trigger Ingestion 测试识别。"
