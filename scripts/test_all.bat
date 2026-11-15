@echo off
REM Local multi-version test runner: 3.4 / 3.7 / 3.12 (CI does not run tests)
REM 3.4 uses a venv (official typing backport installed); edit paths below if needed.
REM NOTE: keep this file ASCII-only -- cmd parses .bat files in the ANSI codepage.
setlocal
set PY34=D:\venv34\Scripts\python.exe
set PY37=D:\Python\Python37\python.exe
set PY312=D:\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8

cd /d "%~dp0.."

set FAILED=0

echo [1/3] Python 3.4 (%PY34%)
%PY34% -m unittest discover -s tests || set FAILED=1
echo.
echo [2/3] Python 3.7 (%PY37%)
%PY37% -m unittest discover -s tests || set FAILED=1
echo.
echo [3/3] Python 3.12 (%PY312%)
%PY312% -m unittest discover -s tests || set FAILED=1
echo.

if "%FAILED%"=="1" (
    echo [FAIL] some versions failed
    exit /b 1
)
echo [OK] all versions passed (3.4 / 3.7 / 3.12)
endlocal
