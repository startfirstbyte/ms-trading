"""
Claude CLI Bridge — chạy trên HOST (nơi `claude` đã đăng nhập subscription).

Backend (trong container Podman) gọi sang đây thay vì gọi thẳng Anthropic API,
để dùng quota subscription phẳng thay vì tính tiền theo token.

Cách chạy (trên host, KHÔNG trong container):
    pip install fastapi uvicorn
    uvicorn claude_bridge.service:app --host 0.0.0.0 --port 8088

Lưu ý bảo mật: bind 0.0.0.0 để container truy cập được qua host-gateway,
NHƯNG phải chặn cổng 8088 khỏi LAN bằng firewall — bất kỳ ai gọi được endpoint
này đều chạy được Claude với quyền trên máy bạn.
"""
import asyncio
import json
import logging
import os
import shutil

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [bridge] %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Claude CLI Bridge")

# Giới hạn số process `claude` chạy đồng thời. Monitor loop của backend bắn tối đa
# 3 symbol × 5 TF = 15 call cùng lúc. CPU dư (16 core) nhưng RAM mới là giới hạn
# (~431 MB/process claude) → mặc định 8 (~3.5 GB peak). Chỉnh qua env BRIDGE_CONCURRENCY.
_MAX_CONCURRENCY = int(os.environ.get("BRIDGE_CONCURRENCY", "8"))
_sem = asyncio.Semaphore(_MAX_CONCURRENCY)

# Resolve đường dẫn tuyệt đối tới claude.exe một lần (create_subprocess_exec cần exact path).
_CLAUDE = shutil.which("claude")


class AnalyzeRequest(BaseModel):
    system: str
    prompt: str
    model: str = "sonnet"       # pin model — KHÔNG để CLI tự nhảy Opus
    timeout: int = 250


class AnalyzeResponse(BaseModel):
    result: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float | None = None


@app.get("/health")
async def health():
    return {"ok": _CLAUDE is not None, "claude_path": _CLAUDE,
            "max_concurrency": _MAX_CONCURRENCY}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    if not _CLAUDE:
        raise HTTPException(500, "claude CLI not found on host PATH")

    args = [
        _CLAUDE, "-p", req.prompt,
        "--output-format", "json",
        "--model", req.model,
        # Thay TOÀN BỘ system prompt mặc định của Claude Code → bỏ agent harness
        # (cắt ~24k token cache-creation overhead mỗi call).
        "--system-prompt", req.system,
        "--tools", "none",          # task chỉ cần text/JSON, không cần tool
    ]

    async with _sem:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,   # tránh CLI chờ stdin 3s
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=req.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, f"claude CLI timeout sau {req.timeout}s")

    if proc.returncode != 0:
        msg = err.decode(errors="ignore")[:400]
        log.error(f"claude exit {proc.returncode}: {msg}")
        raise HTTPException(502, f"claude CLI failed: {msg}")

    try:
        d = json.loads(out.decode(errors="ignore"))
    except json.JSONDecodeError:
        raise HTTPException(502, f"claude trả về non-JSON: {out[:200]!r}")

    if d.get("is_error"):
        raise HTTPException(502, f"claude error: {d.get('result', '')[:300]}")

    usage = d.get("usage", {}) or {}
    return AnalyzeResponse(
        result=d.get("result", ""),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        model=req.model,
        cost_usd=d.get("total_cost_usd"),
    )
