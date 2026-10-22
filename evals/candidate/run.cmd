@echo off
rem One-command reproduction for C-TH-01 (Windows cmd).
rem Steps: contract/candidate tests -> runs/before -> verified audit -> changed scenes.
rem On zh-CN Windows PYTHONUTF8=1 is required, otherwise Path.read_text()
rem decodes the Chinese data as GBK and raises UnicodeDecodeError.
setlocal
cd /d "%~dp0..\.."
if "%PYTHONUTF8%"=="" set "PYTHONUTF8=1"

echo.
echo [1/4] task.py check
uv run --quiet --offline --no-project python task.py check
if errorlevel 1 exit /b 1

echo.
echo [2/4] task.py run --out runs/before
uv run --quiet --offline --no-project python task.py run --out runs/before
if errorlevel 1 exit /b 1

echo.
echo [3/4] evals/candidate/audit.py
uv run --quiet --offline --no-project python evals/candidate/audit.py
if errorlevel 1 exit /b 1

echo.
echo [4/4] evals/candidate/review.py diff
uv run --quiet --offline --no-project python evals/candidate/review.py diff runs/before/traces.jsonl runs/after/traces.jsonl
if errorlevel 1 exit /b 1

echo.
echo done. results: runs\ and evals\candidate\results\
endlocal
