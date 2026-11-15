@echo off
REM Local multi-version test runner: 3.4 / 3.7 / 3.12 (CI does not run tests)
REM 3.4 uses a venv (official typing backport installed); edit paths below if needed.
REM NOTE: keep this file ASCII-only -- cmd parses .bat files in the ANSI codepage.
REM NOTE: keep CRLF line endings -- cmd cannot resolve goto labels in LF-only files.
REM NOTE: verdict is taken from unittest's own OK/FAILED summary line, not the
REM       process exit code -- the vendor DLL can pollute the exit path with
REM       harmless shutdown noise on some machines.
REM NOTE: Python 3.4 on some machines occasionally dies with a native crash
REM       (heap corruption, random position) before printing its summary line.
REM       That is an environment issue, not a test failure, so a missing OK
REM       summary triggers a retry (up to 3 attempts). A real regression fails
REM       every attempt.
setlocal enabledelayedexpansion
set PY34=D:\venv34\Scripts\python.exe
set PY37=D:\Python\Python37\python.exe
set PY312=D:\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8

cd /d "%~dp0.."

set FAILED=0

echo [1/3] Python 3.4 (%PY34%)
set TRIED=0
:run34
set /a TRY34=0
%PY34% -m unittest discover -s tests > "%TEMP%\pyzk20r-test-34.log" 2>&1
findstr /b "FAILED" "%TEMP%\pyzk20r-test-34.log" >nul && set /a TRY34=1
findstr /b "OK" "%TEMP%\pyzk20r-test-34.log" >nul || set /a TRY34=1
if "!TRY34!"=="0" goto done34
if "!TRIED!"=="2" goto fail34
set /a TRIED+=1
echo [retry] Python 3.4 produced no OK summary (suspected native crash), retrying...
goto run34
:done34
findstr /b "OK" "%TEMP%\pyzk20r-test-34.log"
goto after34
:fail34
set FAILED=1
echo [FAIL] Python 3.4 failed after retry, see %TEMP%\pyzk20r-test-34.log
:after34
echo.
echo [2/3] Python 3.7 (%PY37%)
%PY37% -m unittest discover -s tests > "%TEMP%\pyzk20r-test-37.log" 2>&1
findstr /b "FAILED" "%TEMP%\pyzk20r-test-37.log" >nul && set FAILED=1
findstr /b "OK" "%TEMP%\pyzk20r-test-37.log" >nul || set FAILED=1
findstr /b "OK" "%TEMP%\pyzk20r-test-37.log"
echo.
echo [3/3] Python 3.12 (%PY312%)
%PY312% -m unittest discover -s tests > "%TEMP%\pyzk20r-test-312.log" 2>&1
findstr /b "FAILED" "%TEMP%\pyzk20r-test-312.log" >nul && set FAILED=1
findstr /b "OK" "%TEMP%\pyzk20r-test-312.log" >nul || set FAILED=1
findstr /b "OK" "%TEMP%\pyzk20r-test-312.log"
echo.

if not "%FAILED%"=="0" (
    echo [FAIL] some versions failed, see logs in %TEMP%
    exit /b 1
)
echo [OK] all versions passed (3.4 / 3.7 / 3.12)
endlocal
