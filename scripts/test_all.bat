@echo off
REM Local multi-version test runner: 3.4 / 3.7 / 3.12 (CI does not run tests)
REM 3.4 uses a venv (official typing backport installed); edit paths below if needed.
REM NOTE: keep this file ASCII-only -- cmd parses .bat files in the ANSI codepage.
REM NOTE: verdict is taken from unittest's own OK/FAILED summary line, not the
REM       process exit code -- the vendor DLL can pollute the exit path with
REM       harmless shutdown noise on some machines.
setlocal enabledelayedexpansion
set PY34=D:\venv34\Scripts\python.exe
set PY37=D:\Python\Python37\python.exe
set PY312=D:\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8

cd /d "%~dp0.."

set FAILED=0

echo [1/3] Python 3.4 (%PY34%)
%PY34% -m unittest discover -s tests > "%TEMP%\pyzk20r-test-34.log" 2>&1
findstr /b "FAILED" "%TEMP%\pyzk20r-test-34.log" >nul && set FAILED=1
findstr /b "OK" "%TEMP%\pyzk20r-test-34.log" >nul || set FAILED=1
findstr /b "OK FAILED" "%TEMP%\pyzk20r-test-34.log"
echo.
echo [2/3] Python 3.7 (%PY37%)
%PY37% -m unittest discover -s tests > "%TEMP%\pyzk20r-test-37.log" 2>&1
findstr /b "FAILED" "%TEMP%\pyzk20r-test-37.log" >nul && set FAILED=1
findstr /b "OK" "%TEMP%\pyzk20r-test-37.log" >nul || set FAILED=1
findstr /b "OK FAILED" "%TEMP%\pyzk20r-test-37.log"
echo.
echo [3/3] Python 3.12 (%PY312%)
%PY312% -m unittest discover -s tests > "%TEMP%\pyzk20r-test-312.log" 2>&1
findstr /b "FAILED" "%TEMP%\pyzk20r-test-312.log" >nul && set FAILED=1
findstr /b "OK" "%TEMP%\pyzk20r-test-312.log" >nul || set FAILED=1
findstr /b "OK FAILED" "%TEMP%\pyzk20r-test-312.log"
echo.

if not "%FAILED%"=="0" (
    echo [FAIL] some versions failed, see logs in %TEMP%
    exit /b 1
)
echo [OK] all versions passed (3.4 / 3.7 / 3.12)
endlocal
