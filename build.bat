@echo off
chcp 65001 >nul
rem 本文件以 UTF-8 保存，上面 chcp 切换控制台到 UTF-8 代码页，否则中文会显示为乱码
cd /d "%~dp0"
rem 打包为单文件、无控制台窗口的 exe（需先安装: pip install pyinstaller）
pyinstaller -F -w --name CampusNet_AutoLogin run.py
echo.
echo 打包完成: dist\CampusNet_AutoLogin.exe
pause
