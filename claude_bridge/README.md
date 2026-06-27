# Claude CLI Bridge

Service nhỏ chạy **trên HOST** (không trong container) để backend gọi Claude qua
CLI subscription thay vì Anthropic API trả tiền theo token.

```
[Container backend / Podman]  --HTTP-->  [Bridge: host:8088]  --subprocess-->  claude.exe (đã auth)
        AI_BACKEND=local                  uvicorn claude_bridge.service:app
```

## Yêu cầu
- `claude` CLI đã đăng nhập subscription trên host (`claude` chạy được trong terminal).
- Python 3.11+ trên host.

## Chạy
```powershell
pip install -r claude_bridge/requirements.txt
uvicorn claude_bridge.service:app --host 0.0.0.0 --port 8088
```

Kiểm tra:
```powershell
Invoke-RestMethod http://127.0.0.1:8088/health
```

## Bảo mật
- Bind `0.0.0.0` để container vào được qua `host.containers.internal`, **nhưng phải
  chặn cổng 8088 khỏi LAN** bằng Windows Firewall. Ai gọi được endpoint này = chạy
  được Claude với quyền trên máy bạn.

## Backend bật local mode
Trong `.env` (hoặc compose env của service `server`):
```
AI_BACKEND=local
LOCAL_CLAUDE_URL=http://host.containers.internal:8088
AI_LOCAL_MODEL=sonnet
```
Đổi `AI_BACKEND=api` để quay lại gọi Anthropic API trực tiếp.
