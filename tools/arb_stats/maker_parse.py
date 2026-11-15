"""Разбор сделок четырёх бирж для режима «мейкер + хедж» (пакетно): trade*.tsv.zst -> <work>/state, <work>/parsed.

Запуск: python3 maker_parse.py <work> [PAR] [старт_мс]. Дочитывает (можно запускать повторно). Файлы — запуски сборщиков с метки MINRUN.
"""
import glob
import os
import re
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parse_trades  # noqa: E402
from arb_live import MINRUN, START_MS, days_between, symmap_for  # noqa: E402
from common import EXCHANGES, ROOTS  # noqa: E402

TRADE = re.compile(r"trade\d+-(\d+)\.tsv\.zst$")
WANT = {}


def _run(job):
    ex, day, f, state_dir, out_dir, keep_from = job
    return parse_trades.run(ex, day, f, WANT[(ex, day)], state_dir, out_dir, keep_from)


def main(work, par=3, start_ms=START_MS - 5 * 60_000):
    now_ms = int(__import__("time").time() * 1000)
    days = days_between(start_ms, now_ms)
    jobs = []
    for ex in EXCHANGES:
        for day in days:
            for f in glob.glob(f"{ROOTS[ex]}/{day}/trade*.tsv.zst"):
                m = TRADE.search(f)
                if m and int(m.group(1)) >= MINRUN:
                    jobs.append((ex, day, f, os.path.getsize(f)))
    jobs.sort(key=lambda j: -j[3])
    for day in days:
        sm, _ = symmap_for(day)
        for ex in EXCHANGES:
            WANT[(ex, day)] = {s.encode(): s for s in sm[ex]}
    Path(work, "state").mkdir(parents=True, exist_ok=True)
    args = [(ex, day, f, str(Path(work, "state")), str(Path(work, "parsed")), start_ms) for ex, day, f, _ in jobs]
    with Pool(par) as p:
        kept = sum(p.map(_run, args, chunksize=1))
    print({"files": len(jobs), "kept": kept})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3, int(sys.argv[3]) if len(sys.argv) > 3 else START_MS - 5 * 60_000)
