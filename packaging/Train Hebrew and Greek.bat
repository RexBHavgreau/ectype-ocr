@echo off
rem Ectype OCR: train the Hebrew and Greek models from your fonts and scanned editions.
rem The first time, a settings file opens in Notepad: give your fonts folder (and any scans),
rem save, and close Notepad. Run this again whenever you add fonts or scans; finished steps
rem are skipped. The report is saved as training-report.txt beside the settings.
setlocal
title Ectype OCR: training Hebrew and Greek
set "ECTYPE=%~dp0ectype.exe"
set "CONFIG=%LOCALAPPDATA%\Ectype OCR\training.ini"

if not exist "%ECTYPE%" (
  echo Can't find ectype.exe beside this file. Run it from the Ectype OCR program folder.
  pause
  exit /b 1
)

if not exist "%CONFIG%" (
  "%ECTYPE%" train-all --write-config "%CONFIG%" >nul
  echo A settings file has been made: %CONFIG%
  echo Notepad opens it now. Fill in your fonts folder ^(and any scans^), save, and close Notepad.
  start /wait notepad "%CONFIG%"
)

echo Training. This can take several hours; you can leave it running overnight.
echo Closing this window stops it; running this file again carries on where it left off.
echo.
"%ECTYPE%" train-all --config "%CONFIG%"
echo.
echo The report is in "%LOCALAPPDATA%\Ectype OCR\training-report.txt".
pause
