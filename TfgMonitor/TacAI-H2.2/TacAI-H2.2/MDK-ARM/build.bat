@echo off
setlocal

set "UV=C:\Users\11657\AppData\Local\Keil_v5\UV4\UV4.exe"
set "UV_PRO_PATH=D:\TacTech\2-firmware\TacAI-H2.2\MDK-ARM\TacAI-H2.uvprojx"
set "LOG=%cd%\build_log.txt"

echo Init building
echo.

break > "%LOG%"

"%UV%" -j0 -b "%UV_PRO_PATH%" -o"%LOG%"

type "%LOG%"
echo Keil ERRORLEVEL = %ERRORLEVEL%
echo Done