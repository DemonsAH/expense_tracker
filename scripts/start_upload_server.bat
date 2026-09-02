@echo off
rem 手动启动"手机照片上传服务"（默认 8765 端口，保存到 receipt_input/未处理）
rem 启动后窗口会显示手机访问地址（http://<本机局域网IP>:8765/）
cd /d "%~dp0"
start "ExpenseTrackerUpload" dist\ExpenseTrackerUpload.exe
echo.
echo 上传服务已启动。手机与本机连同一 Wi-Fi 后，浏览器访问:
echo   http://本机局域网IP:8765/
echo 查看本机 IP:  ipconfig   (找 "IPv4 地址")
echo 停止服务:     任务管理器结束 ExpenseTrackerUpload，或
echo              Stop-Process -Name ExpenseTrackerUpload -Force
echo.
pause
