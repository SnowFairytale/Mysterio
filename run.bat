@echo off
rem 双击启动桌宠（用 pythonw，不留控制台窗口）。想看日志就在命令行跑 python main.py
cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 (
  echo 没找到 pythonw。请先安装 Python 3.10+，或先激活装好依赖的虚拟环境。
  pause
  exit /b 1
)

start "" pythonw "%~dp0main.py"
