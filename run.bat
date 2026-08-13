@echo off
chcp 65001 >nul
title 丰裕之角桌宠 - 麟
cd /d "%~dp0"
echo 启动中... 无 Key 也能离线使用，首次运行请先执行 install.bat 安装依赖
python oc.py
if errorlevel 1 (
    echo.
    echo [提示] 启动失败：请先运行 install.bat 安装依赖，
    echo       或检查 Python 是否正确安装并加入 PATH。
    pause
)
