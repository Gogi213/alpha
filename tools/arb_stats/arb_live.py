"""Живая вкладка «Арбитраж» страницы здоровья G-022: один проход раз в час (юнит arb-stats.timer).

1. этап A (parse_inc): дочитывает файлы книг пяти бирж с прошлого места, куски по символам — <live>/parsed;
2. удаляет куски старше окна (24 ч + прогрев центра);
3. этап B (analyze.compute): статистика по всем парам за окно [max(начало сбора, t1 − 24 ч), t1);
4. пишет <live>/arb.json (строки таблицы) и <live>/chart-<gen>.jsonl (график каждой пары: максимум Sell, минимум Buy, центр по слотам 3 мин).
Запуск: python3 arb_live.py <каталог live> [PAR]. Файл /data/perf/HOLD (ARB_HOLD) — проход пропускается и пишется в <live>/skips.log. Начало сбора — 14.11.2026 16:30 GMT+4 (приказ владельца 15.11); числа за 1/4/6 ч/сутки —
настоящие эпизоды за последние 1/4/6/24 ч (до 24 ч данных «сутки» считаются за то, что есть).
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze  # noqa: E402
import core  # noqa: E402
import parse_inc  # noqa: E402
from common import EXCHANGES, MINRUN, ROOTS, build_symmap, minrun  # noqa: E402,F401

START_MS = int(datetime(2026, 11, 14, 12, 30, tzinfo=timezone.utc).timestamp() * 1000)   # 14.11 16:30 GMT+4
WINDOW_MS = 24 * 3600_000
WARM_MS = 90 * 60_000        # прогрев центра (60 мин) + запас
PERIODS_H = (1, 4, 6, 24)
NAMES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "bingx": "BingX", "gate": "Gate"}
BOOK = re.compile(r"book\d+-(\d+)\.tsv\.zst$")
HOLD = os.environ.get("ARB_HOLD", "/data/perf/HOLD")   # есть файл — идёт замер производительности, проход пропускается
WANT = {}                    # {(биржа, день): {имя в файле (bytes): имя}} — задаётся до Pool, наследуется потомками


def days_between(a_ms, b_ms):
    d, end, out = datetime.fromtimestamp(a_ms / 1000, timezone.utc).date(), datetime.fromtimestamp(b_ms / 1000, timezone.utc).date(), []
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def symmap_for(day):
    """Список монет и множители по пулам дня; нет пула дня (граница суток) — ближайшего прошлого."""
    d = datetime.fromisoformat(day).date()
    for _ in range(5):
        try:
            return build_symmap(d.isoformat()), d.isoformat()
        except OSError:
            d -= timedelta(days=1)
    raise RuntimeError("нет пулов символов за последние дни")


def discover(days):
    jobs = []
    for ex in EXCHANGES:
        for day in days:
            for f in glob.glob(f"{ROOTS[ex]}/{day}/book*.tsv.zst"):
                m = BOOK.search(f)
                if m and int(m.group(1)) >= minrun(ex):
                    jobs.append((ex, day, f, os.path.getsize(f)))
    return sorted(jobs, key=lambda j: -j[3])


def _parse(job):
    ex, day, f, state_dir, out_dir, keep_from = job
    return parse_inc.run(ex, day, f, WANT[(ex, day)], state_dir, out_dir, keep_from)


def parse_all(live, now_ms, par):
    days = days_between(START_MS - WARM_MS, now_ms)
    jobs = discover(days)
    for day in days:
        sm, _ = symmap_for(day)
        for ex in EXCHANGES:
            WANT[(ex, day)] = {s.encode(): s for s in sm[ex]}
    Path(live, "state").mkdir(parents=True, exist_ok=True)
    args = [(ex, day, f, str(Path(live, "state")), str(Path(live, "parsed")), START_MS - WARM_MS) for ex, day, f, _ in jobs]
    if par <= 1:
        return len(jobs), sum(map(_parse, args))
    with Pool(par) as p:
        kept = sum(p.map(_parse, args, chunksize=1))
    return len(jobs), kept


def hook(row, bars, eps, hist):
    """Что оставляем от пары: числа за 1/4/6/24 ч, сводка эпизодов порога комиссий и ряд графика. Нет центра — пары нет."""
    if core.FEE_RT_BPS not in eps:
        return None
    t0, t1 = analyze.G["t0"], analyze.G["t1"]
    e = eps[core.FEE_RT_BPS]
    ts = np.array([r[0] for r in e], np.int64)
    cnt = [int((ts >= t1 - h * 3600_000).sum()) for h in PERIODS_H]
    S, B, C = core.chart_slots(bars, t0 // 60000, int(np.ceil((t1 - t0) / (core.SLOT_MIN * 60000))))
    return {"cnt": cnt, "big": core.big_counts(e, t1), "st": core.ep_table_stats(e), "S": S, "B": B, "C": C}


def r1(v):
    return None if v is None else round(v, 1)


def write_outputs(live, gen, t0, t1, det, period=3600):
    keys = sorted(det)
    chart = f"chart-{gen}.jsonl"
    rows, off = [], 0
    with open(Path(live, chart + ".tmp"), "wb") as f:
        for k in keys:
            d = det[k]
            line = (json.dumps({"S": d["S"], "B": d["B"], "C": d["C"]}, separators=(",", ":")) + "\n").encode()
            f.write(line)
            s = d["st"]
            rows.append({"c": k[0], "a": k[1], "b": k[2], "cnt": d["cnt"], "big": d["big"],
                         "pk": s["pk"] and [r1(x) for x in s["pk"]], "tau": s["tau"] and [round(x, 3) for x in s["tau"]],
                         "net": s["net"] and [r1(x) for x in s["net"]], "net250": s["net250"] and [r1(x) for x in s["net250"]], "o": off, "l": len(line)})
            off += len(line)
    os.replace(Path(live, chart + ".tmp"), Path(live, chart))
    doc = {"gen": gen, "t0": t0, "t1": t1, "start": START_MS, "slot": core.SLOT_MIN * 60, "fee": core.FEE_RT_BPS, "chart": chart,
           "names": NAMES, "written": int(time.time() * 1000), "period": period, "rows": rows}
    tmp = Path(live, "arb.json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, Path(live, "arb.json"))
    for old in Path(live).glob("chart-*.jsonl"):   # оставляем новый и предыдущий (страница могла уже получить прежний arb.json)
        if old.name != chart and old.stat().st_mtime < time.time() - 7200:
            old.unlink()
    return len(rows)


def main(live, par=3):
    Path(live).mkdir(parents=True, exist_ok=True)
    if os.path.exists(HOLD):
        msg = {"skipped": f"есть {HOLD} (замер производительности)", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        print(json.dumps(msg, ensure_ascii=False))
        with open(Path(live, "skips.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return
    import fcntl   # только Linux (поэтому здесь): замок от двух одновременных проходов
    lock = open(Path(live, "lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(json.dumps({"skipped": "прошлый проход ещё идёт"}))
        return
    t_start = time.time()
    now = int(t_start * 1000)
    t1 = (now - 60_000) // 60_000 * 60_000
    t0 = max(START_MS, t1 - WINDOW_MS)
    files, kept = parse_all(live, now, par)
    t_parse = time.time()
    removed = sum(parse_inc.prune(str(Path(live, "state")), str(Path(live, "parsed")), ex, t1 - WINDOW_MS - WARM_MS) for ex in EXCHANGES)
    _, day = symmap_for(days_between(now, now)[0])
    _, det, _, _, coins = analyze.compute(str(Path(live, "parsed")), day, t0, t1, "e", hook=hook, par=par, hold_ms=core.DELAY_MS)
    det = {k: v for k, v in det.items() if v}
    n = write_outputs(live, now, t0, t1, det)
    print(json.dumps({"files": files, "kept": kept, "removed": removed, "coins": len(coins), "pairs": n, "t0": t0, "t1": t1,
                      "parse_s": round(t_parse - t_start, 1), "total_s": round(time.time() - t_start, 1)}))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
