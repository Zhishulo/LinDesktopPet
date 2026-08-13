@echo off
chcp 65001 >nul
title 丰裕之角桌宠 - 一键打包(兼容中文用户名)
cd /d "%~dp0"

set "BUILD_DIR=C:\LinBuild"
set "VENV_DIR=C:\LinVenv"

echo [1/4] 复制项目到纯英文路径 %BUILD_DIR% ...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
mkdir "%BUILD_DIR%"
copy /y "*.py"                  "%BUILD_DIR%\"                    >nul
copy /y "*.png"                 "%BUILD_DIR%\"                    >nul
copy /y "favicon.ico"           "%BUILD_DIR%\"                    >nul
echo 完成。

echo [2/4] 准备独立打包环境（第一次约2-5分钟，之后秒开）...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    python -m venv "%VENV_DIR%"
    "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    "%VENV_DIR%\Scripts\python.exe" -m pip install PyQt5 PyQt5-Qt5 requests psutil pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)
echo 完成。

echo [3/4] 开始打包（需要几分钟，请耐心等待）...
cd /d "%BUILD_DIR%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%VENV_DIR%\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed --noupx --name "LinDesktopPet" --icon "favicon.ico" --add-data "*.png;." oc.py
if errorlevel 1 (
    echo.
    echo [错误] 打包失败！请把上方红色报错发给我。
    pause
    exit /b 1
)

echo [4/4] 把成品复制回你的桌宠文件夹...
copy /y "%BUILD_DIR%\dist\LinDesktopPet.exe" "%~dp0LinDesktopPet.exe" >nul
echo.
echo ==============================================
echo   打包完成！
echo   成品在：%~dp0LinDesktopPet.exe
echo   已使用图标：favicon.ico
echo   把这个 exe + 使用说明.txt 发给朋友即可。
echo   所有 .py 模块与图片已自动打包，首次运行会自动生成数据文件。
echo ==============================================
pause
