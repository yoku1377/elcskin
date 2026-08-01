@echo off
setlocal

:: Keil UV4.exe 路径
set "UV=C:\Users\11657\AppData\Local\Keil_v5\UV4\UV4.exe"

:: Keil 工程路径
set "UV_PRO_PATH=D:\TacTech\2-firmware\TacAI-H2.2\MDK-ARM\TacAI-H2.uvprojx"

:: Target 名称，必须和 Keil 里 Target 下拉框完全一致
set "TARGET=TacAI-H2"

:: 日志
set "LOG=%~dp0flash_log.txt"

echo Flashing STM32 by Keil...

break > "%LOG%"

"%UV%" -j0 -f "%UV_PRO_PATH%" -t"%TARGET%" -o"%LOG%"

set "KEIL_ERR=%ERRORLEVEL%"

type "%LOG%"
echo Keil ERRORLEVEL = %KEIL_ERR%

exit /b %KEIL_ERR%