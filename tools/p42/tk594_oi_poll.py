"""П-42 §13.20 (TK-594): живой опрос `/futures/data/openInterestHist` (period=5m) — задержка появления строки для правила лага L.

Каждые STEP с по каждой монете запрашивает последние 3 строки и пишет в jsonl: время опроса (мс), монета, список `timestamp`. Обработка — `tk594_oi_poll_report.py`.
Запуск (srv): systemd-run --unit=tk594-oi-poll -p Nice=19 --collect python3 tk594_oi_poll.py /data/tk594/oi_poll.jsonl 7200
"""
import json
import sys
import time
import urllib.request

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT", "1000PEPEUSDT", "SUIUSDT"]
STEP = 15
URL = "https://fapi.binance.com/futures/data/openInterestHist?symbol={}&period=5m&limit=3"


def main():
    out, dur = sys.argv[1], int(sys.argv[2])
    t_end = time.time() + dur
    with open(out, "a", buffering=1) as fh:
        while time.time() < t_end:
            t0 = time.time()
            for s in COINS:
                try:
                    with urllib.request.urlopen(URL.format(s), timeout=10) as r:
                        rows = json.load(r)
                    rec = {"poll_ms": int(time.time() * 1000), "sym": s, "ts": [int(x["timestamp"]) for x in rows],
                           "oi": [x["sumOpenInterest"] for x in rows]}
                except Exception as e:  # noqa: BLE001
                    rec = {"poll_ms": int(time.time() * 1000), "sym": s, "err": str(e)[:200]}
                fh.write(json.dumps(rec) + "\n")
            time.sleep(max(0.0, STEP - (time.time() - t0)))


if __name__ == "__main__":
    main()
