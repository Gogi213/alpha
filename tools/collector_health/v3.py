"""Данные страницы здоровья v3: столбики по 15 минут за сутки (96 штук) на биржу и на сеть.

Источники: health.json коллектора (метка свежести, версия, окна 1 ч), `series-minutes.jsonl` в корне данных (поминутный ряд нового
коллектора: потерянные мс, поздние записи, таймауты по видам, метки запусков), `events-*.jsonl` (история до ряда: паузы и таймауты;
поздних там нет — null), `/data/netwatch/<день UTC>.jsonl` (потери и отклик до шлюза, Google и краёв бирж).
`python3 v3.py --dump` печатает ответ /api/v3.
"""
import argparse
import bisect
import glob
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

SLOT = 900
NS = 96
MIN = 60
ICMP = ("gateway", "neutral")
NET_BAD = 0.15          # слот сети красный при ≥ 15 % потерянных замеров (принято координатором 13.11)
NET_MIN_N = 100         # слот сети без замеров (меньше N) — «нет журнала»
SYN_MS = 700            # TCP-замер дольше — потерян SYN (повтор через 1 с)
NET_NAMES = [("gateway", "шлюз srv"), ("neutral", "Google"), ("binance", "Binance"), ("bybit", "Bybit"), ("okx", "OKX"), ("bingx", "BingX")]


def jload(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tail_lines(path, max_bytes):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > max_bytes else lines


def read_series(root):
    """(минуты {минута: [lost_ms, late[4], to_alive, to_silent]}, метки запуска [(сек, версия)])."""
    rows, starts = {}, []
    for ln in tail_lines(os.path.join(root, "series-minutes.jsonl"), 400_000):
        try:
            v = json.loads(ln)
        except ValueError:
            continue
        if "start" in v:
            starts.append((v["start"], v.get("version", "")))
        elif "t" in v:
            to = v.get("to") or [0, 0]
            rows[v["t"] // MIN] = [v.get("lost_ms", 0), list(v.get("late") or [0, 0, 0, 0]), to[0], to[1]]
    return rows, starts


def read_events(root, since_s):
    """События журнала шардов с меткой ≥ since_s: паузы (мс на обнаружении), таймауты, запуски/остановки; и первая метка журнала."""
    gaps, tos, marks, first = [], [], [], None
    for f in glob.glob(os.path.join(root, "events-*.jsonl")):
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for ln in fh:
                i = ln.find('"ts_ms":')
                if i < 0:
                    continue
                try:
                    ts = int(ln[i + 8:].split(",", 1)[0].split("}", 1)[0]) / 1000
                except ValueError:
                    continue
                first = ts if first is None else min(first, ts)
                if ts < since_s:
                    continue
                if '"ev":"gap"' in ln:
                    try:
                        gaps.append((ts, json.loads(ln).get("gap_ms", 0)))
                    except ValueError:
                        pass
                elif '"ev":"timeout"' in ln:
                    try:
                        tos.append((ts, bool(json.loads(ln).get("data_alive", True))))
                    except ValueError:
                        pass
                elif '"ev":"started"' in ln:
                    marks.append(ts)
    return gaps, tos, marks, first


def minutes_for(root, now):
    """Поминутная картина суток: {минута: [lost_ms, late|None, to_alive, to_silent, источник]} и метки запусков."""
    s_end = -(-int(now) // SLOT) * SLOT
    s0 = s_end - NS * SLOT
    rows, starts = read_series(root)
    gaps, tos, marks, first = read_events(root, s0 - 7200)
    out = {}
    if first is not None:
        for m in range(max(s0, int(first)) // MIN, int(now) // MIN + 1):
            out[m] = [0, None, 0, 0, "ev"]
        for ts, ms in gaps:
            if int(ts) // MIN in out:
                out[int(ts) // MIN][0] += ms
        for ts, alive in tos:
            if int(ts) // MIN in out:
                out[int(ts) // MIN][2 if alive else 3] += 1
    for m, (lost, late, ta, ts_) in rows.items():
        if m * MIN >= s0 and m * MIN < s_end:
            out[m] = [lost, sum(late), ta, ts_, "ser"]
    # метки запусков: ряд (с версией) и события `started`; близкие (< 10 мин) — один тик
    ticks = sorted({int(t) for t, _ in starts} | {int(t) for t in marks})
    merged = []
    for t in ticks:
        if t >= s0 and (not merged or t - merged[-1] > 600):
            merged.append(t)
    return out, merged, s0


def bars_from_minutes(mins, s0, restarts, late_min=1):
    """Столбик (15 мин): [флаг, потеряно мс, поздних, таймаутов «пинг пропал», «молчала», перезапуск, флаг поздних]. Поздние красят столбик, только если в какой-то минуте их ≥ late_min
    (порог thresholds.json `late_min_flag`: BingX даёт всплески ≈ 9,6 с по ≈ 0,014 % записей — не провал; число ниже порога видно в подсказке)."""
    bars = []
    for k in range(NS):
        a = s0 + k * SLOT
        cov = [mins[m] for m in range(a // MIN, (a + SLOT) // MIN) if m in mins]
        if not cov:
            bars.append([2, 0, None, 0, 0, 0, 0])
            continue
        lost = sum(c[0] for c in cov)
        ser = [c for c in cov if c[4] == "ser"]
        late = sum(c[1] for c in ser) if ser else None
        r = 1 if any(a <= t < a + SLOT for t in restarts) else 0
        late_flag = 1 if any(c[1] >= late_min for c in ser) else 0
        bars.append([1 if (lost > 0 or late_flag) else 0, lost, late, sum(c[2] for c in cov), sum(c[3] for c in cov), r, late_flag])
    return bars


class Net:
    """Замеры сети: слоты по 15 минут за сутки и последний час; файл читается с места, где остановились."""

    def __init__(self, directory):
        self.dir = directory
        self.pos = {}
        self.slots = defaultdict(lambda: [0, 0])
        self.recent = deque()

    def feed(self, now):
        for day in (time.gmtime(now - 86400), time.gmtime(now)):
            path = os.path.join(self.dir, time.strftime("%Y-%m-%d.jsonl", day))
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            off = self.pos.get(path, 0)
            if size < off:
                off = 0
            if size == off:
                continue
            with open(path, "rb") as f:
                f.seek(off)
                data = f.read(size - off)
            cut = data.rfind(b"\n")
            if cut < 0:
                continue
            self.pos[path] = off + cut + 1
            for ln in data[:cut].decode("utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                n = r.get("n")
                if "meta" in r or n is None:
                    continue
                rtt = r.get("rtt")
                lost = rtt is None or (n not in ICMP and rtt > SYN_MS)
                s = int(r["ts"]) // SLOT * SLOT
                c = self.slots[s]
                c[0] += 1
                c[1] += lost
                self.recent.append((r["ts"], n, rtt, lost))
        while self.recent and self.recent[0][0] < now - 3600:
            self.recent.popleft()
        for s in [s for s in self.slots if s < now - 86400 - SLOT]:
            del self.slots[s]

    def payload(self, now, s0):
        self.feed(now)
        bars = []
        for k in range(NS):
            a = s0 + k * SLOT
            n, lost = self.slots.get(a, (0, 0))
            if n < NET_MIN_N:
                bars.append([2, 0, None, 0, 0, 0])
            else:
                pct = lost / n * 100
                bars.append([1 if lost / n >= NET_BAD else 0, round(pct, 1), None, 0, 0, 0])
        by = defaultdict(list)
        for ts, n, rtt, lost in self.recent:
            by[n].append((rtt, lost))
        det = []
        for key, name in NET_NAMES:
            v = by.get(key)
            if not v:
                continue
            good = sorted(r for r, lo in v if not lo and r is not None)
            run = runs = 0
            for _, lo in v:
                run = run + 1 if lo else 0
                runs += run == 3
            det.append({"name": name, "loss_pct": round(sum(lo for _, lo in v) / len(v) * 100, 1), "series": runs,
                        "p50": good[len(good) // 2] if good else None, "p99": good[int(len(good) * 0.99)] if good else None})
        return {"bars": bars, "detail": det, "since": min((s for s, c in self.slots.items() if c[0] >= NET_MIN_N), default=None)}


def exchange_row(ex, name, health, root, now, s0_shared, thr=None):
    thr = thr or {}
    mins, restarts, s0 = minutes_for(root, now)
    bars = bars_from_minutes(mins, s0, restarts, max(1, int(thr.get("late_min_flag", 1))))
    h = health or {}
    ws = h.get("ws", {})
    fresh = h.get("fresh", {}).get("kinds", {})
    late_1h = sum(k.get("late_1h", 0) for k in fresh.values())
    late_24h = sum(k.get("late_24h", 0) for k in fresh.values())
    cut1 = int(now) // MIN - 60
    lost_1h = sum(c[0] for m, c in mins.items() if m >= cut1) / 1000
    lost_24h = sum(c[0] for c in mins.values()) / 1000
    age = (now * 1000 - h.get("ts_ms", 0)) / 1000 if h else 1e9
    sym = h.get("symbols", {})
    live_bad = age > 30 or (sym.get("with_data_1m", 1) < sym.get("subscribed", 0))
    late_bad = late_1h > 0 and late_1h >= max(1, int(thr.get("late_1h_flag", 1)))   # порог за час: поздние ниже него — только число
    ok = not live_bad and lost_1h == 0 and not late_bad and not ws.get("gaps_1h")
    gaps = sorted(((m, c[0]) for m, c in mins.items() if c[0] > 0), reverse=True)[:6]
    shards = h.get("shards") or []
    proc = h.get("process") or {}
    return {
        "id": ex, "name": name, "ok": ok, "live_bad": live_bad, "late_bad": late_bad, "lost_1h_s": round(lost_1h, 1), "bars": bars, "restarts": restarts,
        "detail": {
            "lost_24h_s": round(lost_24h, 1), "gaps": [[m * MIN, ms] for m, ms in gaps], "late_1h": late_1h, "late_24h": late_24h,
            "late_max_1h_s": max([k.get("max_1h_ms") or 0 for k in fresh.values()] or [0]) / 1000,
            "to_alive_24h": ws.get("timeouts_alive_24h"), "to_silent_24h": ws.get("timeouts_silent_24h"), "reconnects_1h": ws.get("reconnects_1h"),
            "version": proc.get("version"), "started": (h.get("started_ms") or 0) / 1000, "symbols": [sym.get("with_data_1m"), sym.get("subscribed")],
            "msgs_s": round(sum(s.get("msgs_10s", 0) for s in shards) / 10), "lag_in": [h.get("lag_in_ms", {}).get("p50"), h.get("lag_in_ms", {}).get("p99")],
            "ping": [ws.get("ping_ms", {}).get("p50"), ws.get("ping_ms", {}).get("max")], "cpu": proc.get("cpu_cores"),
            "log_from": min((m for m in mins), default=None) and min(mins) * MIN,
        },
    }


def payload(health_dir, exchanges, roots, net, now=None, thr=None):
    now = time.time() if now is None else now
    s0 = -(-int(now) // SLOT) * SLOT - NS * SLOT
    rows, all_restarts = [], []
    for e in exchanges:
        ex = e["id"]
        h = jload(os.path.join(health_dir, ex + ".json"))
        root = roots.get(ex)
        if h is None or not root:
            continue
        r = exchange_row(ex, e.get("name", ex), h, root, now, s0, thr)
        rows.append(r)
        all_restarts += r["restarts"]
    ticks = []
    for t in sorted(all_restarts):
        if not ticks or t - ticks[-1] > 600:
            ticks.append(t)
    return {"now": now, "s0": s0, "slot": SLOT, "rows": rows, "net": net.payload(now, s0), "restarts": ticks}


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--health-dir", default="/data/turbo-probe/health-soak")
    ap.add_argument("--exchanges", default=str(here / "exchanges.json"))
    ap.add_argument("--roots", default=str(here / "roots.json"))
    ap.add_argument("--netwatch", default="/data/netwatch")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()
    d = payload(a.health_dir, jload(a.exchanges) or [], jload(a.roots) or {}, Net(a.netwatch))
    print(json.dumps(d, ensure_ascii=False))


if __name__ == "__main__":
    main()
