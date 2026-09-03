# 组装便携目录：out\ExpenseTrackerPortable\
# 便携目录结构（拷到别的 Windows 电脑即可运行，无需安装 Python）：
#   ExpenseTrackerGUI.exe / ExpenseTrackerCLI.exe / ExpenseTrackerUpload.exe
#   owners.json            归属人配置（换机按需手工编辑）
#   owners.example.json    当前配置的参考副本
#   .env.example           DeepSeek key 模板（另存为 .env 并填写）
#   ocr_service/           可选：本地 GLM-OCR 模型（约 2.5GB，模型单独下载）
#   scripts/               计划任务注册脚本 / 手动启动上传服务
#
# 用法（PowerShell，项目根）：
#   .\scripts\build_portable.ps1
#   .\scripts\build_portable.ps1 -CopyOcrService   # 一并拷贝本地模型(约2.5GB)
#   .\scripts\build_portable.ps1 -NoCurrentOwners  # 不带当前 owners.json（首启自动生成单人模板）

param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$CopyOcrService,
    [switch]$NoCurrentOwners
)

$out = Join-Path $ProjectRoot "out\ExpenseTrackerPortable"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $out | Out-Null
Write-Host "生成便携目录: $out"

# 1) 三个 exe（必须已打包）
$dist = Join-Path $ProjectRoot "dist"
foreach ($name in @("ExpenseTrackerGUI.exe", "ExpenseTrackerCLI.exe", "ExpenseTrackerUpload.exe")) {
    $exe = Join-Path $dist $name
    if (-not (Test-Path $exe)) {
        throw "缺少 dist\$name：请先用 build_venv 打包"
    }
    Copy-Item $exe (Join-Path $out $name)
}

# 2) owners.json（默认带当前配置）与参考样例
if (-not $NoCurrentOwners -and (Test-Path (Join-Path $ProjectRoot "owners.json"))) {
    Copy-Item (Join-Path $ProjectRoot "owners.json") (Join-Path $out "owners.json")
    Write-Host "  已拷贝 owners.json（当前归属人配置）"
}
if (Test-Path (Join-Path $ProjectRoot "owners.json")) {
    Copy-Item (Join-Path $ProjectRoot "owners.json") (Join-Path $out "owners.example.json")
}

# 3) .env.example（不含真实 key）
$envExample = @(
    "# DeepSeek 官方 API（GUI 的 external parser 用）。不填则无法使用 DeepSeek 解析。",
    "DEEPSEEK_API_KEY=",
    "DEEPSEEK_BASE_URL=https://api.deepseek.com",
    "EXPENSE_TRACKER_LLM_MODEL="
)
Set-Content -Path (Join-Path $out ".env.example") -Value $envExample -Encoding utf8

# 4) 可选：本地 OCR 模型（体积大，默认不拷；模型单独下载后放入便携根目录 ocr_service/）
if ($CopyOcrService) {
    $ocr = Join-Path $dist "ocr_service"
    if (Test-Path $ocr) {
        Write-Host "  正在拷贝 ocr_service（约 2.5GB，请稍候）..."
        Copy-Item $ocr (Join-Path $out "ocr_service") -Recurse
    }
    else {
        Write-Warning "未找到 dist\ocr_service，跳过模型拷贝"
    }
}

# 5) 辅助脚本与说明
New-Item -ItemType Directory -Force -Path (Join-Path $out "scripts") | Out-Null
Copy-Item (Join-Path $ProjectRoot "scripts\register_daily_ingest_job.ps1") (Join-Path $out "scripts\register_daily_ingest_job.ps1")
Copy-Item (Join-Path $ProjectRoot "scripts\register_upload_server_job.ps1") (Join-Path $out "scripts\register_upload_server_job.ps1")
$bat = "@echo off`r`nrem manually start the upload server (portable layout)`r`ncd /d `"%~dp0..`"`r`nstart `"ExpenseTrackerUpload`" ExpenseTrackerUpload.exe`r`necho Upload server started. Phone browser: http://LAN_IP:8765/`r`npause"
Set-Content -Path (Join-Path $out "scripts\start_upload.bat") -Value $bat -Encoding ascii

# 6) 使用说明
$readme = @'
Expense Tracker - 便携目录（无需安装 Python）

1) 运行
   双击 ExpenseTrackerGUI.exe 即可使用（编辑 / 校对 / 报表 / 手机上传）。

2) 目录里生成的数据（无需手动创建）
   data/receipts.json   小票库
   receipt_input/未处理  手机上传照片（上传服务保存到这里）
   receipt_input/已处理  自动识别后的图片归档
   receipt_input/已校对  人工核对(Verified)后的图片归档
   reports/             月度报表

   这些目录/文件都在本便携目录内自动创建，运行时全部读写本地，不依赖外部路径。

3) 把旧机器的数据带过来（可选）
   直接整目录拷到便携根对应位置即可：
     data\receipts.json      旧小票库
     receipt_input\          未处理/已处理/已校对 整目录（含图片）
     reports\                （可选）历史报表
   GUI 首次启动会自动把库中旧机器的绝对盘符路径改写为便携目录相对路径，
   因此拷贝到任何位置/电脑都能正常显示图片，无需手工改配置。

4) 归属人(owners.json)
   首次运行时若不存在会自动生成单人模板（Me / marker=M）。
   多成员共用的拷贝示例见 owners.example.json，按同格式编辑本目录 owners.json 即可。

5) 手机上传
   双击 scripts\start_upload.bat 手动开启（手机与本机同一 Wi-Fi，
   浏览器访问 http://本机局域网IP:8765/）。
   如需每天定时 21:00 自动开启 / 23:00 关闭，在 PowerShell 中执行：
     .\scripts\register_upload_server_job.ps1

6) 每日自动识别未处理照片（22:00）
   在 PowerShell 中执行（需联网且 .env 配置了 DEEPSEEK_API_KEY）：
     .\scripts\register_daily_ingest_job.ps1
   任务命令会自动选择根目录的 ExpenseTrackerCLI.exe。
   注意：任务使用你的 Windows 账户与固定路径，换机/移动目录后需重新注册。

7) 完整本地识别（可选，模型单独下载）
   若需要本地 GLM-OCR 识别（不依赖 DeepSeek 也能跑规则解析），把
   ocr_service 目录（含 llama 模型，约 2.5GB）放入本目录根：
     ocr_service\llama\llama-mtmd-cli.exe
     ocr_service\llama\models\GLM-OCR-Q8_0.gguf
     ocr_service\llama\models\mmproj-GLM-OCR-Q8_0.gguf
   模型下载地址与放置指引见仓库 README（ocr_service/llama/models 章节）。

8) 配置 DeepSeek（可选但推荐）
   复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY，GUI 勾选的
   "Use DeepSeek V4-Flash (external)" 解析才可用（本地规则解析对手机拍摄
   的德语小票支持较弱）。
'@
Set-Content -Path (Join-Path $out "README.txt") -Value $readme -Encoding utf8

Write-Host ""
Write-Host "完成。便携目录: $out"
Write-Host "拷贝该目录到目标电脑后：双击 ExpenseTrackerGUI.exe 即可。"
