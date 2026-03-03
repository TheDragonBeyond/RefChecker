@echo off
TITLE RefChecker Installer
CLS

ECHO ========================================================
ECHO    RefChecker - Automated Installer (Windows)
ECHO ========================================================
ECHO.

:: 1. Check if Python is installed and meets minimum version
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    ECHO [ERROR] Python is not found in your PATH.
    ECHO Please install Python 3.10+ from python.org and check "Add Python to PATH" during installation.
    PAUSE
    EXIT /B 1
)

:: Display detected Python version
FOR /F "tokens=2 delims= " %%A IN ('python --version 2^>^&1') DO SET PYVER=%%A
ECHO Detected Python %PYVER%

:: 2. Create Virtual Environment
ECHO.
ECHO [1/5] Creating virtual environment (venv)...
IF NOT EXIST "venv" (
    python -m venv venv
    IF %ERRORLEVEL% NEQ 0 (
        ECHO [ERROR] Failed to create virtual environment.
        ECHO Ensure the 'venv' module is available: python -m ensurepip
        PAUSE
        EXIT /B 1
    )
) ELSE (
    ECHO     - Virtual environment already exists. Skipping creation.
)

:: 3. Activate and Install Dependencies
ECHO [2/5] Upgrading PIP and installing dependencies...
call venv\Scripts\activate.bat

python -m pip install --upgrade pip
IF %ERRORLEVEL% NEQ 0 (
    ECHO [WARNING] Failed to upgrade pip. Continuing with existing version...
)

if exist requirements.txt (
    pip install --upgrade -r requirements.txt
    IF %ERRORLEVEL% NEQ 0 (
        ECHO.
        ECHO [ERROR] Dependency installation failed.
        ECHO Some libraries require Microsoft Visual C++ Build Tools.
        ECHO Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
        ECHO After installing Build Tools, re-run this script.
        PAUSE
        EXIT /B 1
    )
) else (
    ECHO [WARNING] requirements.txt not found! Installation might be incomplete.
)

:: 4. Create a Launcher Script
ECHO [3/5] Creating startup launcher (RefChecker.bat)...
(
echo @echo off
echo call "%%~dp0venv\Scripts\activate.bat"
echo python "%%~dp0app.py"
echo IF %%ERRORLEVEL%% NEQ 0 PAUSE
) > RefChecker.bat

:: 5. Create a Windows shortcut with icon (if icon exists)
ECHO [4/5] Creating desktop-friendly shortcut...
SET ICON_FILE=RefChecker_icon.png
SET ICO_FILE=RefChecker_icon.ico

:: Generate .ico from .png using Python/Pillow (if available)
IF EXIST "%ICON_FILE%" (
    python -c "from PIL import Image; img = Image.open('%ICON_FILE%'); img.save('%ICO_FILE%', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])" 2>nul
    IF EXIST "%ICO_FILE%" (
        ECHO     - Icon file generated: %ICO_FILE%
        :: Create a VBScript to generate a .lnk shortcut with the icon
        > create_shortcut.vbs (
            echo Set ws = CreateObject("WScript.Shell"^)
            echo Set shortcut = ws.CreateShortcut(ws.CurrentDirectory ^& "\RefChecker.lnk"^)
            echo shortcut.TargetPath = ws.CurrentDirectory ^& "\RefChecker.bat"
            echo shortcut.WorkingDirectory = ws.CurrentDirectory
            echo shortcut.IconLocation = ws.CurrentDirectory ^& "\%ICO_FILE%"
            echo shortcut.Description = "Launch RefChecker"
            echo shortcut.WindowStyle = 7
            echo shortcut.Save
        )
        cscript //nologo create_shortcut.vbs
        del create_shortcut.vbs
        IF EXIST "RefChecker.lnk" (
            ECHO     - Shortcut created: RefChecker.lnk
        )
    ) ELSE (
        ECHO     - [INFO] Pillow not available. Skipping icon conversion.
        ECHO       Install Pillow for icon support: pip install Pillow
    )
) ELSE (
    ECHO     - [INFO] Icon file not found (%ICON_FILE%^). Skipping shortcut creation.
)

:: 6. Final Check
ECHO [5/5] Installation Complete!
ECHO.
ECHO ========================================================
ECHO  Setup Finished Successfully.
ECHO  To start the program:
ECHO    - Double-click 'RefChecker.bat'
IF EXIST "RefChecker.lnk" (
ECHO    - Or double-click 'RefChecker' shortcut (has icon^)
)
ECHO ========================================================
PAUSE