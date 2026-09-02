# Register daily scheduled tasks to auto start/stop the upload server.
#   - ExpenseTrackerUploadStart : every day 21:00, start the upload server
#   - ExpenseTrackerUploadStop  : every day 23:00, stop the upload server
# Usage (PowerShell, project root):
#   .\scripts\register_upload_server_job.ps1
#   .\scripts\register_upload_server_job.ps1 -StartAt "21:00" -StopAt "23:00"
# Remove:
#   Unregister-ScheduledTask -TaskName "ExpenseTrackerUploadStart" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "ExpenseTrackerUploadStop"  -Confirm:$false

param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$StartAt = "21:00",
    [string]$StopAt = "23:00"
)

$exe = Join-Path $ProjectRoot "dist\ExpenseTrackerUpload.exe"
if (-not (Test-Path $exe)) {
    throw "Upload exe not found: $exe (build with ExpenseTrackerUpload.spec first)"
}

$startCmd = "Start-Process -FilePath '$exe' -WorkingDirectory '$ProjectRoot' -WindowStyle Hidden"
$stopCmd = "Stop-Process -Name ExpenseTrackerUpload -Force -ErrorAction SilentlyContinue"

$startAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$startCmd`""
$startTrigger = New-ScheduledTaskTrigger -Daily -At $StartAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "ExpenseTrackerUploadStart" -Action $startAction `
    -Trigger $startTrigger -Settings $settings `
    -Description "Start ExpenseTrackerUpload server daily at $StartAt" | Out-Null

$stopAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$stopCmd`""
$stopTrigger = New-ScheduledTaskTrigger -Daily -At $StopAt
Register-ScheduledTask -TaskName "ExpenseTrackerUploadStop" -Action $stopAction `
    -Trigger $stopTrigger -Settings $settings `
    -Description "Stop ExpenseTrackerUpload server daily at $StopAt" | Out-Null

Write-Host "Registered:"
Write-Host "  ExpenseTrackerUploadStart  daily $StartAt -> $startCmd"
Write-Host "  ExpenseTrackerUploadStop   daily $StopAt  -> $stopCmd"
Write-Host "View: Get-ScheduledTask -TaskName 'ExpenseTrackerUpload*'"
