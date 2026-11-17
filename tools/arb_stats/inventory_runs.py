"""Перечень запусков сборщиков по биржам: время начала (метка запуска), конца (mtime последнего файла), число файлов book, размер, дни, пропуск до предыдущего запуска.

Запуск на srv: python3 inventory_runs.py [день_от=2026-11-12] [день_до=2026-11-15]. Только чтение имён и mtime (без распаковки).
"""
import collections
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOTS  # noqa: E402


def ts(x):
    return datetime.fromtimestamp(x, timezone.utc).strftime("%d.%m %H:%M:%S")


def main(d0="2026-11-12", d1="2026-11-15"):
    d, days = datetime.fromisoformat(d0).date(), []
    while d <= datetime.fromisoformat(d1).date():
        days.append(d.isoformat())
        d += timedelta(days=1)
    for ex, root in ROOTS.items():
        runs = collections.defaultdict(lambda: {"files": 0, "size": 0, "end": 0, "days": set()})
        for day in days:
            for f in glob.glob(f"{root}/{day}/book*.tsv.zst"):
                m = re.search(r"book(\d+)-(\d+)\.tsv\.zst$", f)
                r = runs[int(m.group(2))]
                r["files"] += 1
                r["size"] += os.path.getsize(f)
                r["end"] = max(r["end"], os.path.getmtime(f))
                r["days"].add(day[-2:])
        print("==", ex, "пулы:", sorted(os.path.basename(p) for p in glob.glob(root + "/pool-*.json")))
        prev = None
        for rid in sorted(runs):
            r = runs[rid]
            gap = "" if prev is None else f" пропуск_до_запуска={rid - prev:.0f}с"
            print(f"run {rid} {ts(rid)} -> {ts(r['end'])} файлов={r['files']} {r['size'] / 1e9:.2f}ГБ дни={sorted(r['days'])}{gap}")
            prev = r["end"]


if __name__ == "__main__":
    main(*sys.argv[1:3])
