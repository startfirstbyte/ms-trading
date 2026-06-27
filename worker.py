"""
Cache Worker — chạy trên Windows.
Kết nối MT5, backfill lịch sử, poll realtime → gửi lên server qua HTTP webhook.
Không có Redis client — server chịu trách nhiệm lưu trữ.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("worker.log", mode="a", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

SYMBOLS = ["XAUUSD", "BTCUSD", "USOIL"]

RESOLUTIONS: dict[str, int] = {
    "1":  mt5.TIMEFRAME_M1,
    "3":  mt5.TIMEFRAME_M3,
    "5":  mt5.TIMEFRAME_M5,
    "15": mt5.TIMEFRAME_M15,
    "60": mt5.TIMEFRAME_H1,
}

BACKFILL_DAYS: dict[str, int] = {
    "1":  7,
    "3":  14,
    "5":  30,
    "15": 30,
    "60": 30,
}

SERVER_URL     = os.environ.get("SERVER_URL", "http://localhost:8000")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "dev-secret")
POLL_INTERVAL  = 1       # giây
BATCH_SIZE     = 500     # số bars mỗi request backfill
REQUEST_TIMEOUT = 10     # giây


# ── HTTP session (persistent connection, tự reconnect) ─────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {WEBHOOK_SECRET}",
    })
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(total=3, backoff_factor=0.5)
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def post(session: requests.Session, path: str, body: dict) -> bool:
    try:
        r = session.post(f"{SERVER_URL}{path}", data=json.dumps(body, default=float), timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning(f"POST {path} failed: {e}")
        return False


# ── MT5 ────────────────────────────────────────────────────────────────────────

def connect_mt5() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    login = os.environ.get("MT5_LOGIN")
    if login:
        ok = mt5.login(
            int(login),
            password=os.environ["MT5_PASSWORD"],
            server=os.environ["MT5_SERVER"],
        )
        if not ok:
            raise RuntimeError(f"MT5 login() failed: {mt5.last_error()}")
    log.info(f"MT5 connected → {mt5.terminal_info().name}")


def _to_bar(rate) -> dict:
    return {
        "time":   int(rate["time"]) * 1000,
        "open":   float(rate["open"]),
        "high":   float(rate["high"]),
        "low":    float(rate["low"]),
        "close":  float(rate["close"]),
        "volume": int(rate["tick_volume"]),
    }


# ── Backfill ───────────────────────────────────────────────────────────────────

def backfill(session: requests.Session) -> None:
    log.info("Starting backfill…")
    now = datetime.now(timezone.utc)

    for symbol in SYMBOLS:
        for res, tf in RESOLUTIONS.items():
            date_from = now - timedelta(days=BACKFILL_DAYS[res])
            rates = mt5.copy_rates_range(symbol, tf, date_from, now)

            if rates is None or len(rates) == 0:
                log.warning(f"  No data: {symbol}:{res}")
                continue

            # Bỏ bar cuối (đang live), gửi theo batch
            closed_rates = rates[:-1]
            bars = [_to_bar(r) for r in closed_rates]
            total = len(bars)

            for i in range(0, total, BATCH_SIZE):
                chunk = bars[i : i + BATCH_SIZE]
                ok = post(session, "/webhook/bars/batch", {
                    "symbol":     symbol,
                    "resolution": res,
                    "bars":       chunk,
                })
                if ok:
                    log.info(f"  {symbol}:{res} → batch {i//BATCH_SIZE + 1}: {len(chunk)} bars")
                else:
                    log.error(f"  {symbol}:{res} → batch failed, skipping")
                    break

    log.info("Backfill complete.")


# ── Poll loop ──────────────────────────────────────────────────────────────────

def run() -> None:
    session = make_session()

    # Verify server reachable
    try:
        session.get(f"{SERVER_URL}/api/config", timeout=5).raise_for_status()
        log.info(f"Server reachable → {SERVER_URL}")
    except Exception as e:
        raise RuntimeError(f"Cannot reach server at {SERVER_URL}: {e}")

    connect_mt5()
    backfill(session)

    # Theo dõi bar đã đóng và quote đã gửi để tránh duplicate
    last_closed: dict[tuple[str, str], int] = {}
    last_quote:  dict[str, tuple[float, float]] = {}  # symbol → (bid, ask)

    log.info("Starting poll loop (every 1s)…")
    cycle = 0
    posts_ok = 0
    posts_fail = 0
    while True:
        loop_start = time.monotonic()
        cycle += 1

        for symbol in SYMBOLS:
            live_bars:   dict[str, dict] = {}
            closed_bars: dict[str, dict] = {}

            for res, tf in RESOLUTIONS.items():
                rates = mt5.copy_rates_from_pos(symbol, tf, 0, 2)
                if rates is None or len(rates) < 1:
                    log.warning(f"MT5 copy_rates {symbol}:{res} returned no data: {mt5.last_error()}")
                    continue

                # Live bar (đang hình thành)
                live_bars[res] = _to_bar(rates[-1])

                # Closed bar (nếu vừa đóng bar mới)
                if len(rates) >= 2:
                    bar = _to_bar(rates[-2])
                    key = (symbol, res)
                    if last_closed.get(key) != bar["time"]:
                        last_closed[key] = bar["time"]
                        closed_bars[res] = bar

            # Quote — chỉ gửi khi bid/ask thay đổi
            tick = mt5.symbol_info_tick(symbol)
            quote = None
            if tick:
                prev = last_quote.get(symbol)
                if prev != (tick.bid, tick.ask):
                    last_quote[symbol] = (tick.bid, tick.ask)
                    quote = {"bid": tick.bid, "ask": tick.ask, "time": tick.time * 1000}

            # Gửi 1 request/symbol/giây
            payload: dict = {"live": live_bars}
            if closed_bars:
                payload["closed"] = closed_bars
            if quote:
                payload["quote"] = quote

            if post(session, f"/webhook/tick/{symbol}", payload):
                posts_ok += 1
            else:
                posts_fail += 1

        elapsed = time.monotonic() - loop_start

        # Heartbeat mỗi 30 cycle (~30s)
        if cycle % 30 == 0:
            log.info(
                f"heartbeat cycle={cycle} posts_ok={posts_ok} posts_fail={posts_fail} "
                f"last_loop={elapsed:.2f}s"
            )
            posts_ok = posts_fail = 0

        time.sleep(max(0.0, POLL_INTERVAL - elapsed))


if __name__ == "__main__":
    run()
