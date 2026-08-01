@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   Tactile Sensor Viz - Build Script
echo ========================================
echo.

cd /d "%~dp0"
cd ..

if not exist "main.py" (
    echo [ERROR] main.py not found in project root!
    echo Current dir: %cd%
    pause
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv virtual environment not found!
    echo Please run: uv venv
    pause
    exit /b 1
)

echo [1/3] Checking logo.ico...
if exist "tactile_vis\logo.ico" (
    echo [OK] Found logo.ico
) else (
    echo [WARNING] logo.ico not found, will use default icon
)

echo.
echo [2/3] Ensuring PyInstaller is installed...
uv pip install pyinstaller

if errorlevel 1 (
    echo [ERROR] PyInstaller install failed!
    pause
    exit /b 1
)

echo.
echo [3/3] Building...
uv run pyinstaller --clean tactile_vis\tactile_vis.spec

if errorlevel 1 (
    echo [ERROR] Build failed! See errors above.
    pause
    exit /b 1
)

echo.
if exist "dist\触觉传感器可视化平台.exe" (
    echo [OK] Output: dist\触觉传感器可视化平台.exe
    for %%A in ("dist\触觉传感器可视化平台.exe") do (
        echo     Size: %%~zA bytes
    )
) else (
    echo [ERROR] Output file not found!
)

echo.
echo ========================================
pause