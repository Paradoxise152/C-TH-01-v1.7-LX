# 一键复现（Windows PowerShell，在任意目录执行均可）
# C-TH-01：先跑契约/自建测试，再生成 runs/before，最后跑核实审计。
# zh-CN Windows 必须设 PYTHONUTF8=1，否则 voice_agent/evaluation.py 里的
# Path.read_text() 会用 GBK 解码中文数据并抛 UnicodeDecodeError。
# 若系统执行策略禁止脚本，用 `powershell -ExecutionPolicy Bypass -File run.ps1`
# 或直接运行同目录的 run.cmd（推荐）。

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root
if (-not $env:PYTHONUTF8) { $env:PYTHONUTF8 = '1' }

$commands = @(
    'uv run --quiet --offline --no-project python task.py check',
    'uv run --quiet --offline --no-project python task.py run --out runs/before',
    'uv run --quiet --offline --no-project python evals/candidate/audit.py',
    'uv run --quiet --offline --no-project python evals/candidate/review.py diff runs/before/traces.jsonl runs/after/traces.jsonl'
)

foreach ($command in $commands) {
    Write-Host ""
    Write-Host "PS> $command" -ForegroundColor Cyan
    Invoke-Expression $command
    if ($LASTEXITCODE -ne 0) {
        throw "command failed with exit code $LASTEXITCODE : $command"
    }
}

Write-Host ""
Write-Host "done. results: runs/ and evals/candidate/results/" -ForegroundColor Green
