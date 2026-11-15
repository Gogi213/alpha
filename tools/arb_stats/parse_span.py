"""Разбор книг и сделок СТАРЫХ запусков сборщиков (метка запуска < MINRUN) за заданный промежуток — дельта к уже разобранному (live/parsed, w2/parsed).

Запуск: python3 parse_span.py <work> <старт_мс> [PAR]. Пишет <work>/b/{state,parsed} (книги) и <work>/w/{state,parsed} (сделки) тем же видом кусков, что parse_inc / parse_trades.
Берутся файлы запусков с меткой < MINRUN, у которых время закрытия (mtime) позже старт − WARM_MS; записи раньше старт − WARM_MS отбрасываются при чтении (прогрев центра).
Повторный запуск дочитывает (состояние файлов — в state). Записи запусков ≥ MINRUN в этом промежутке — уже в живом разборе (live/parsed, w2/parsed).
"""
import glob
import os
import re
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parse_inc  # noqa: E402
import parse_trades  # noqa: E402
from arb_live import MINRUN, WARM_MS, days_between, symmap_for  # noqa: E402
from common import EXCHANGES, ROOTS  # noqa: E402

FILE = re.compile(r"(book|trade)\d+-(\d+)\.tsv\.zst$")
WANT = {}


def _run(job):
    kind, ex, day, f, state_dir, out_dir, keep_from = job
    mod = parse_inc if kind == "book" else parse_trades
    return mod.run(ex, day, f, WANT[(ex, day)], state_dir, out_dir, keep_from)


def main(work, start_ms, par=3):
    keep_from = start_ms - WARM_MS
    now_ms = int(__import__("time").time() * 1000)
    days = days_between(keep_from, now_ms)
    jobs = []
    for ex in EXCHANGES:
        for day in days:
            for f in glob.glob(f"{ROOTS[ex]}/{day}/*.tsv.zst"):
                m = FILE.search(f)
                if m and int(m.group(2)) < MINRUN and os.path.getmtime(f) * 1000 >= keep_from:
                    jobs.append((m.group(1), ex, day, f, os.path.getsize(f)))
    jobs.sort(key=lambda j: -j[4])
    for day in days:
        sm, _ = symmap_for(day)
        for ex in EXCHANGES:
            WANT[(ex, day)] = {s.encode(): s for s in sm[ex]}
    for sub in ("b", "w"):
        Path(work, sub, "state").mkdir(parents=True, exist_ok=True)
    args = [(k, ex, day, f, str(Path(work, "b" if k == "book" else "w", "state")), str(Path(work, "b" if k == "book" else "w", "parsed")), keep_from)
            for k, ex, day, f, _ in jobs]
    with Pool(par) as p:
        kept = sum(p.map(_run, args, chunksize=1))
    print({"files": len(jobs), "books": sum(1 for j in jobs if j[0] == "book"), "trades": sum(1 for j in jobs if j[0] == "trade"), "kept": kept})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 3)
