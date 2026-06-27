# start.ps1 — Khởi động Claude CLI Bridge + MT5 Worker cùng lúc (chạy trên HOST).
#
#   Dùng:  ./start.ps1                # mở 2 cửa sổ riêng, xem log trực tiếp
#          ./start.ps1 -Background    # chạy ẩn, log ghi ra file *.log
#
# Lưu ý: backend (podman compose up -d server) là tiến trình RIÊNG — script này
# chỉ lo 2 thứ chạy trên host: bridge (gọi Claude subscription) + worker (MT5).

param(
    [switch]$Background
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# Tránh console Windows crash khi in tiếng Việt
$env:PYTHONIOENCODING = "utf-8"

# Kiểm tra điều kiện tiên quyết
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Warning "Khong tim thay 'claude' CLI tren PATH — bridge se loi. Dang nhap Claude Code truoc."
}

# Guard chong chay trung (idempotent): bo qua cai nao da chay.
$bridgeUp = $null -ne (Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue)
$workerUp = $null -ne (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                       Where-Object { $_.CommandLine -like "*worker.py*" })
if ($bridgeUp) { Write-Host "Bridge da chay (cong 8088) — bo qua." -ForegroundColor Yellow }
if ($workerUp) { Write-Host "Worker da chay (worker.py)   — bo qua." -ForegroundColor Yellow }

$bridgeCmd = "python -m uvicorn claude_bridge.service:app --host 0.0.0.0 --port 8088"
$workerCmd = "python worker.py"

if ($Background) {
    Write-Host "Khoi dong nen (log -> bridge.log, worker.log)..." -ForegroundColor Cyan
    # Chay THANG python (khong qua powershell wrapper) → .pids giu dung PID giet duoc.
    $pids = @()
    if (-not $bridgeUp) {
        $bridge = Start-Process python -PassThru -WindowStyle Hidden `
            -ArgumentList "-m","uvicorn","claude_bridge.service:app","--host","0.0.0.0","--port","8088" `
            -RedirectStandardOutput "$root\bridge.log" -RedirectStandardError "$root\bridge.err.log"
        $pids += $bridge.Id; Write-Host "Bridge PID=$($bridge.Id)" -ForegroundColor Green
    }
    if (-not $workerUp) {
        $worker = Start-Process python -PassThru -WindowStyle Hidden `
            -ArgumentList "worker.py" `
            -RedirectStandardOutput "$root\worker.out.log" -RedirectStandardError "$root\worker.err.log"
        $pids += $worker.Id; Write-Host "Worker PID=$($worker.Id)" -ForegroundColor Green
    }
    if ($pids) { ($pids -join "`n") | Set-Content "$root\.pids" -Encoding ascii }
    Write-Host "Dung lai: Get-Content .pids | Stop-Process" -ForegroundColor DarkGray
}
else {
    Write-Host "Mo cua so cho cac thanh phan chua chay..." -ForegroundColor Cyan
    if (-not $bridgeUp) {
        Start-Process powershell -ArgumentList @(
            "-NoExit", "-NoProfile", "-Command",
            "`$Host.UI.RawUI.WindowTitle='Claude Bridge'; Set-Location '$root'; `$env:PYTHONIOENCODING='utf-8'; $bridgeCmd"
        )
    }
    if (-not $workerUp) {
        Start-Process powershell -ArgumentList @(
            "-NoExit", "-NoProfile", "-Command",
            "`$Host.UI.RawUI.WindowTitle='MT5 Worker'; Set-Location '$root'; `$env:PYTHONIOENCODING='utf-8'; $workerCmd"
        )
    }
    Write-Host "Da mo. Bridge: http://127.0.0.1:8088/health" -ForegroundColor Green
}

# ── Dong bo IP bridge cho backend container (best-effort) ───────────────────────
# Container Podman goi bridge qua IP gateway WSL (Windows host nhin tu Podman
# machine). IP nay DOI khi WSL restart → tinh lai moi lan start, ghi vao .env,
# recreate server neu doi. Bo qua neu podman/AI_BACKEND khong dung local.
if (Get-Command podman -ErrorAction SilentlyContinue) {
    $envPath = "$root\.env"
    $isLocal = (Get-Content $envPath | Where-Object { $_ -match '^\s*AI_BACKEND\s*=\s*local\s*$' })
    if ($isLocal) {
        try {
            $route = podman machine ssh 'ip route | grep default' 2>$null
            if ($route -match 'via (\d+\.\d+\.\d+\.\d+)') {
                $newUrl = "http://$($Matches[1]):8088"
                $lines  = Get-Content $envPath
                $cur    = ($lines | Where-Object { $_ -match '^\s*LOCAL_CLAUDE_URL\s*=' }) -replace '^\s*LOCAL_CLAUDE_URL\s*=\s*', ''
                if ($cur.Trim() -ne $newUrl) {
                    $lines = $lines | ForEach-Object {
                        if ($_ -match '^\s*LOCAL_CLAUDE_URL\s*=') { "LOCAL_CLAUDE_URL=$newUrl" } else { $_ }
                    }
                    [System.IO.File]::WriteAllLines($envPath, [string[]]$lines)  # UTF-8 no BOM
                    Write-Host "Bridge IP doi -> $newUrl ; recreate backend..." -ForegroundColor Yellow
                    podman compose up -d server *>$null
                } else {
                    Write-Host "Backend->bridge URL da dung: $newUrl" -ForegroundColor DarkGray
                }
            }
        } catch {
            Write-Warning "Khong dong bo duoc bridge IP cho container: $_"
        }
    }
}
