"""Проверка данных окна по запускам сборщика: на самых «толстых» символах каждой биржи — по метке запуска число записей, лаг приёма (p50/p99, доля > 2 с),
запись/с и границы по времени биржи. Запуск: python3 basis_split_check.py <parsed книг> <t0_мс> <t1_мс> [N=3]. Печатает таблицу markdown."""
import glob
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import EXCHANGES  # noqa: E402
from gaps_scan import busiest  # noqa: E402

RUN = re.compile(r"-book\d+-(\d+)\.\d+\.npz$")


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d.%m %H:%M")


def main(parsed, t0, t1, n=3):
    print("| биржа | символы | запуск (метка) | записей | с … по (UTC, время биржи) | записей/с | лаг p50, мс | лаг p99, мс | доля лага > 2 с |")
    print("|---|---|---|---|---|---|---|---|---|")
    for ex in EXCHANGES:
        syms = busiest(parsed, ex, n)
        runs = {}
        for s in syms:
            for f in glob.glob(str(Path(parsed, ex, s + ".*.npz"))):
                m = RUN.search(f)
                if not m:
                    continue
                z = np.load(f)
                k = (z["e"] >= t0) & (z["e"] < t1)
                if k.any():
                    runs.setdefault(int(m.group(1)), []).append((z["e"][k], z["lag"][k]))
        for lab in sorted(runs):
            e = np.concatenate([a for a, _ in runs[lab]])
            lag = np.concatenate([b for _, b in runs[lab]])
            span = (e.max() - e.min()) / 1000
            print(f"| {ex} | {len(syms)} | {lab} | {len(e)} | {fmt(e.min())} … {fmt(e.max())} | {len(e) / len(syms) / max(span, 1):.1f} | "
                  f"{np.percentile(lag, 50):.0f} | {np.percentile(lag, 99):.0f} | {(lag > 2000).mean() * 100:.2f} % |")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 3)
