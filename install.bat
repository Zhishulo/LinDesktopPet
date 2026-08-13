@echo off
chcp 65001 >nul
title 丰裕之角桌宠 - 一键安装
cd /d "%~dp0"

echo ==============================================
echo   正在检查 Python 环境……
echo ==============================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 没有检测到 Python！
    echo 请先到 https://www.python.org/downloads/ 安装 Python 3.9 或更高版本。
    echo 安装时务必勾选 "Add Python to PATH" 选项，装完再运行本文件。
    pause
    exit /b 1
)

echo 开始安装依赖库，请耐心等待（约1-3分钟）……
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install PyQt5 PyQt5-Qt5 requests psutil pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo ==============================================
echo   安装完成！现在可以双击 run.bat 启动桌宠啦～
echo   没有 API Key 也能离线使用，右键 API 管理可配置多家 AI 接口。
echo ==============================================
pause
