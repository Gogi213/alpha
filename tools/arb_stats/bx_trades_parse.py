"""Разбор сделок BingX (сырьё /data/collect/bingx/data) в куски по символам: все запуски окна, независимо от прежних состояний разбора.

Запуск: python3 bx_trades_parse.py <work> <t0_мс> [PAR]   -> <work>/state, <work>/parsed/bingx/<символ>.<метка>.<номер>.npz (e, et, rx, p, q, m, как parse_trades.py).
Берутся все файлы trade*.tsv.zst дней t0…сейчас (запуски с любой меткой), записи с приёмом ≥ t0; верхняя граница — при загрузке.
"""
import glob
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parse_trades  # noqa: E402
from arb_live import days_between, symmap_for  # noqa: E402
from common import ROOTS  # noqa: E402

WANT = {}


def _run(job):
    day, f, state_dir, out_dir, keep_from = job
    return parse_trades.run("bingx", day, f, WANT[day], state_dir, out_dir, keep_from)


def main(work, t0, par=4):
    days = days_between(t0, int(time.time() * 1000))
    jobs = []
    for day in days:
        sm, _ = symmap_for(day)
        WANT[day] = {s.encode(): s for s in sm["bingx"]}
        for f in sorted(glob.glob(f"{ROOTS['bingx']}/{day}/trade*.tsv.zst")):
            jobs.append((day, f, str(Path(work, "state")), str(Path(work, "parsed")), t0 - 60_000))
    Path(work, "state").mkdir(parents=True, exist_ok=True)
    with Pool(par) as p:
        kept = sum(p.map(_run, jobs, chunksize=1))
    print({"files": len(jobs), "kept": kept, "days": days})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 4)
