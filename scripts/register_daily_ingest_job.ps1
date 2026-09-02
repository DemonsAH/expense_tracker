# 注册"每日自动处理小票"的 Windows 计划任务。
# 用法（PowerShell，项目根目录下）：
#   .\scripts\register_daily_ingest_job.ps1
#   .\scripts\register_daily_ingest_job.ps1 -At "23:30" -Preprocess -UseLlmParser
# 删除任务：  Unregister-ScheduledTask -TaskName "ExpenseTrackerDailyIngest"

param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$At = "22:00",
    [switch]$Preprocess,
    [switch]$UseLlmParser,
    [string]$TaskName = "ExpenseTrackerDailyIngest"
)

$exe = Join-Path $ProjectRoot "dist\ExpenseTrackerCLI.exe"
if (-not (Test-Path $exe)) {
    throw "CLI exe not found: $exe (先执行 ExpenseTrackerCLI.spec 打包)"
}

# 用 cmd /c 先切换工作目录，保证 CLI 里默认的相对路径
# (data/receipts.json、owners.json) 指向项目根目录。
$inner = "cd /d `"$ProjectRoot`" && `"$exe`" run-ingest-job `"receipt_input\未处理`" " +
    "--processed-dir `"receipt_input\已处理`" --keep-failures"
if ($Preprocess) { $inner += " --preprocess" }
if ($UseLlmParser) { $inner += " --use-llm-parser" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$inner`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "每天 $At 自动处理 receipt_input/未处理 下的小票图片" | Out-Null

Write-Host "已注册计划任务: $TaskName"
Write-Host "每天 $At 执行: $inner"
Write-Host "查看:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "删除:  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
