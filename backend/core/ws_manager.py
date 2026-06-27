"""WebSocket connection manager — tracks live clients per (symbol, resolution)."""
import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._conns: dict[tuple[str, str], list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, symbol: str, resolution: str) -> None:
        await ws.accept()
        self._conns.setdefault((symbol, resolution), []).append(ws)
        log.info(f"WS +connect {symbol}:{resolution}  total={self._total()}")

    def disconnect(self, ws: WebSocket, symbol: str, resolution: str) -> None:
        lst = self._conns.get((symbol, resolution), [])
        if ws in lst:
            lst.remove(ws)
        log.info(f"WS -disconnect {symbol}:{resolution}  total={self._total()}")

    async def broadcast(self, symbol: str, resolution: str, bar: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._conns.get((symbol, resolution), []):
            try:
                await ws.send_json(bar)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._conns[(symbol, resolution)].remove(ws)

    def _total(self) -> int:
        return sum(len(v) for v in self._conns.values())
