#!/usr/bin/env python3
"""Часовая проверка коллектора (G-022): health.json + файлы данных за последний час -> `<out-dir>/accept-<биржа>-<UTC>.json` с вердиктом ok/fail. Общая для всех бирж, без запуска в потоке: ставится таймером после перезапуска боевого:
  systemd-run --on-active=1h --unit=accept-hour-<биржа> --nice=10 python accept_hour.py --exchange binance --root /data/bnws [--health /data/health/binance.json] [--out-dir /data/health]
Окно — последний час (`--window-s`), но не раньше старта процесса (health.started_ms). Вердикт ok, если: health свежий (≤ STALE_S); таймаутов, переподключений и переходов входа за час — 0; записей старше fresh.late_ms за час (мера `fresh`) — 0 по всем видам;
порядок ключей по символу в файле не нарушен. Пауз данных (gap), замен пути (path_switch), мгновенного «данные за минуту» и списка символов пула без записей книги в файлах окна вердикт не учитывает — диагностика (в файле):
тихий символ без обновлений в окне — норма (Bybit повторяет L1 с тем же u — дубль, в файл не пишется: на 3-минутном окне 5 из 413 символов без записей книги, 12.11), поэтому «все символы с данными» по файлам — вывод для оператора, не вердикт (WIP).
Файл с kind="accept_hour" страница здоровья пропускает (общий каталог /data/health). Код выхода: 0 — ok, 1 — fail.
"""
import argparse
import glob
import importlib
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from accept_ws import read_lines  # noqa: E402
from wscore import codec  # noqa: E402
from wscore.fresh import GRACE_S, LATE_MS  # noqa: E402

STALE_S = 30  # как порог страницы здоровья (thresholds.json)
WINDOW_MS = 3600_000
KIND = {"b": "book", "t": "trade", "m": "mark"}
RESULT_VERSION = 1


def load_pool(root):
    """Пул символов — последний pool-<день>.json корня (его пишет коллектор при старте)."""
    paths = sorted(glob.glob(os.path.join(root, "pool-*.json")))
    return json.load(open(paths[-1]))["pool"] if paths else []


def scan_files(root, since_ms, until_ms):
    """Записи файлов суток в окне: символы по видам, поздние (rx − E > LATE_MS) всего и вне первых GRACE_S после любой строки subscribed, максимумы лага, нарушения порядка ключей, число записей по типам."""
    rec, symbols = Counter(), {k: set() for k in "btm"}
    late = {k: {"late_all": 0, "late_after_grace": 0, "max_ms": None} for k in "btm"}
    seq = {"book_u_not_increasing": 0, "trade_a_not_increasing": 0, "mark_E_not_increasing": 0}
    lo_ns, hi_ns = since_ms * 1_000_000, until_ms * 1_000_000
    for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        if os.path.getmtime(p) * 1000 < since_ms:
            continue  # файл закончен до окна
        last, grace_until = {}, 0
        for r in codec.decode(read_lines(p)):
            if r["t"] == "c" and r["what"] == "subscribed":
                grace_until = r["rx_ns"] + int(GRACE_S * 1e9)
            if not lo_ns <= r["rx_ns"] <= hi_ns:
                continue
            t = r["t"]
            rec[t] += 1
            if t not in KIND:
                continue
            symbols[t].add(r["s"])
            lag = r["rx_ns"] // 1_000_000 - r["E"]
            lt = late[t]
            lt["max_ms"] = lag if lt["max_ms"] is None else max(lt["max_ms"], lag)
            if lag > LATE_MS:
                lt["late_all"] += 1
                lt["late_after_grace"] += r["rx_ns"] > grace_until
            key, name = (r["u"], "book_u_not_increasing") if t == "b" else (r["a"], "trade_a_not_increasing") if t == "t" else (r["E"], "mark_E_not_increasing")
            if (t, r["s"]) in last and key <= last[(t, r["s"])]:
                seq[name] += 1
            last[(t, r["s"])] = key
    return rec, symbols, late, seq


def scan_events(root, since_ms):
    ev = Counter()
    for p in glob.glob(os.path.join(root, "events-*.jsonl")):
        with open(p) as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if e.get("ts_ms", 0) >= since_ms:
                    ev[e["ev"]] += 1
    return dict(ev)


def verdict(health, age_s, files):
    """Причины провала по health и файлам; пусто — ok."""
    why = []
    if age_s > STALE_S:
        why.append(f"health не обновлялся {age_s} с")
    ws = health["ws"]
    for k in ("timeouts_1h", "reconnects_1h", "entrance_switches_1h"):
        if ws.get(k):
            why.append(f"{k}: {ws[k]}")
    for kind, v in ((health.get("fresh") or {}).get("kinds") or {}).items():
        if v.get("late_1h"):
            why.append(f"fresh {kind}: записей старше {LATE_MS // 1000} с за час: {v['late_1h']}")
    bad_seq = {k: v for k, v in files["sequence_checks"].items() if v}
    if bad_seq:
        why.append(f"порядок ключей нарушен: {bad_seq}")
    return why


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--health")
    ap.add_argument("--out-dir", default="/data/health")
    ap.add_argument("--now-ms", type=int, help="конец окна, мс (по умолчанию — сейчас; для проверки)")
    ap.add_argument("--window-s", type=int, default=WINDOW_MS // 1000, help="длина окна, с (по умолчанию час; 180 — окно приёмки)")
    a = ap.parse_args(argv)
    health_path = a.health or importlib.import_module(f"{a.exchange}_ws").Adapter.health_default
    with open(health_path) as f:
        health = json.load(f)
    now_ms = a.now_ms or int(time.time() * 1000)
    since_ms = max(now_ms - a.window_s * 1000, health["started_ms"])
    pool = load_pool(a.root)
    rec, symbols, late, seq = scan_files(a.root, since_ms, now_ms)
    files = {
        "records": dict(rec), "symbols_with_data": {KIND[k]: len(v) for k, v in symbols.items()}, "pool": len(pool),
        "pool_without_book_in_files": sorted(set(pool) - symbols["b"]), "late": {KIND[k]: v for k, v in late.items()}, "sequence_checks": seq,
    }
    age_s = max(0, (now_ms - health["ts_ms"]) // 1000)
    why = verdict(health, age_s, files)
    ws = health["ws"]
    out = {
        "schema": RESULT_VERSION, "kind": "accept_hour", "exchange": a.exchange, "ts_ms": now_ms,
        "utc": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now_ms / 1000)), "window_ms": [since_ms, now_ms], "window_s": round((now_ms - since_ms) / 1000),
        "verdict": "fail" if why else "ok", "fail_reasons": why,
        "health": {
            "age_s": age_s, "symbols": health["symbols"], "connections": ws["connections"],
            "timeouts_1h": ws["timeouts_1h"], "reconnects_1h": ws["reconnects_1h"], "entrance_switches_1h": ws.get("entrance_switches_1h", 0), "gaps_1h": ws.get("gaps_1h", 0),
            "fresh": {k: {"late_1h": v["late_1h"], "max_1h_ms": v["max_1h_ms"]} for k, v in ((health.get("fresh") or {}).get("kinds") or {}).items()},
            "lag_in_ms": health["lag_in_ms"], "lag_out_ms": health["lag_out_ms"],
        },
        "files": files, "events": scan_events(a.root, since_ms),
    }
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, f"accept-{a.exchange}-{out['utc']}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    print(f"{path}: {out['verdict']}" + ("" if not why else " — " + "; ".join(why)), flush=True)
    return 1 if why else 0  # fail виден и по коду выхода юнита (systemctl --failed)


if __name__ == "__main__":
    sys.exit(main())
