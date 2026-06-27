# stop.ps1 — Dừng Claude CLI Bridge + MT5 Worker (chạy trên HOST).
#
#   Dùng:  ./stop.ps1            # dừng cả bridge + worker
#          ./stop.ps1 -Bridge    # chỉ dừng bridge
#          ./stop.ps1 -Worker    # chỉ dừng worker
#
# Đối xứng với start.ps1. KHÔNG đụng tới backend container (đó là tiến trình riêng,
# dừng bằng `podman compose stop server`). Tìm tiến trình theo NHIỀU cách để chắc
# chắn giết được dù khởi động ở mode nào:
#   • bridge : tiến trình đang LISTEN cổng 8088 (OwningProcess) — đáng tin nhất.
#   • worker : python.exe có CommandLine chứa "worker.py".
#   • .pids  : PID do `start.ps1 -Background` ghi lại (fallback/bổ sung).

param(
    [switch]$Bridge,
    [switch]$Worker
)

$ErrorActionPreference = "SilentlyContinue"
$root = $PSScriptRoot
Set-Location $root

# Không cờ nào → dừng cả hai.
if (-not $Bridge -and -not $Worker) { $Bridge = $true; $Worker = $true }

$killed = @{}   # PID -> mô tả, tránh giết trùng + báo cáo

function Stop-Pid($procId, $label) {
    if (-not $procId) { return }
    if ($script:killed.ContainsKey([int]$procId)) { return }
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $p) { return }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    $script:killed[[int]$procId] = $label
    Write-Host "Da dung $label (PID=$procId)" -ForegroundColor Green
}

# ── Bridge: theo cổng 8088 ──────────────────────────────────────────────────────
if ($Bridge) {
    $conns = Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) { Stop-Pid $c.OwningProcess "bridge (cong 8088)" }
}

# ── Worker: theo CommandLine python *worker.py* ─────────────────────────────────
if ($Worker) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like "*worker.py*" }
    foreach ($pr in $procs) { Stop-Pid $pr.ProcessId "worker (worker.py)" }
}

# ── Fallback: PID trong .pids (mode -Background) ────────────────────────────────
$pidFile = Join-Path $root ".pids"
if (Test-Path $pidFile) {
    foreach ($line in (Get-Content $pidFile)) {
        $procId = ($line).Trim()
        if ($procId -match '^\d+$') { Stop-Pid ([int]$procId) "tu .pids" }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

if ($killed.Count -eq 0) {
    Write-Host "Khong co bridge/worker nao dang chay." -ForegroundColor Yellow
} else {
    Write-Host "Xong — da dung $($killed.Count) tien trinh." -ForegroundColor Cyan
}
