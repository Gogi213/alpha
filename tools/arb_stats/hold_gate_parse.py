"""Разбор книг Gate (futures.book_ticker, с 15.11 05:49 UTC) тем же кодом, что у остальных бирж (parse_inc: куски <символ>.<метка>.<номер>.npz).

Запуск: python3 hold_gate_parse.py <выход: каталог с state/ и parsed/> [PAR]
Символы — все из пулов Gate за дни сбора (имя в файле как есть). Файлы дочитываются с места остановки (состояние в <выход>/state), повторный запуск дополняет.
"""
import glob
import json
import re
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parse_inc  # noqa: E402
from hold_common import ROOTS5  # noqa: E402

ROOT = ROOTS5["gate"]
G = {}


def _job(j):
    day, f = j
    return parse_inc.run("gate", day, f, G["want"], G["state"], G["parsed"], 0)


def main(out, par=3):
    syms = set()
    for p in glob.glob(f"{ROOT}/pool-2026-*.json"):
        syms |= set(json.loads(Path(p).read_text(encoding="utf-8"))["pool"])
    G["want"] = {s.encode(): s for s in syms}
    G["state"], G["parsed"] = str(Path(out, "state")), str(Path(out, "parsed"))
    Path(G["state"]).mkdir(parents=True, exist_ok=True)
    jobs = []
    for d in sorted(glob.glob(f"{ROOT}/2026-*")):
        for f in sorted(glob.glob(f"{d}/book*.tsv.zst")):
            if re.search(r"book\d+-\d+\.tsv\.zst$", f):
                jobs.append((Path(d).name, f))
    with Pool(par) as p:
        kept = p.map(_job, jobs, chunksize=1)
    print({"symbols": len(syms), "files": len(jobs), "kept": sum(kept)})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
