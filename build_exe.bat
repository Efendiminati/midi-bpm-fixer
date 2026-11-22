@echo off
REM Build a single-file Windows .exe into dist\
REM Requires: python -m pip install mido pyinstaller
setlocal

cd /d "%~dp0"

echo Building MIDI BPM Fixer...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "MIDI BPM Fixer" ^
  --icon "assets\icon.ico" ^
  midi_bpm_fixer.py

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Done: dist\MIDI BPM Fixer.exe
endlocal
