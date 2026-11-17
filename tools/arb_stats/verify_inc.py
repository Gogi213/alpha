"""Сверка живого парсера (parse_inc) с проверенным (parse_book) на настоящем файле: записи до границы end_ms должны совпасть полностью.

Запуск на srv: python3 verify_inc.py <биржа> <день> <файл book> <минут от начала> <рабочий каталог>
Старый разбор останавливается на end_ms (приём), новый читает файл целиком двумя проходами (второй — дочитывание растущего файла).
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import parse_book  # noqa: E402
import parse_inc  # noqa: E402
from common import build_symmap  # noqa: E402


def first_rx(path):
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    cum = 0
    for line in p.stdout:
        cum += int(line.split(b"\t")[1])
        if line[:1] == b"b":
            p.kill()
            return cum // 1_000_000
    return None


def load(d):
    r = {}
    for f in sorted(Path(d).glob("*.npz")):
        sym = f.name.split(".")[0]
        z = np.load(f)
        r.setdefault(sym, []).append({k: z[k] for k in z.files})
    return {s: {k: np.concatenate([c[k] for c in v]) for k in v[0]} for s, v in r.items()}


def main(ex, day, path, minutes, work):
    work = Path(work)
    end_ms = first_rx(path) + int(minutes) * 60_000
    parse_book.run(ex, day, end_ms, str(work / "old"), "t", [path])
    sm = build_symmap(day)[ex]
    want = {s.encode(): s for s in sm}
    (work / "state").mkdir(parents=True, exist_ok=True)
    n1 = parse_inc.run(ex, day, path, want, work / "state", work / "new", 0)
    n2 = parse_inc.run(ex, day, path, want, work / "state", work / "new", 0)
    old, new = load(work / "old" / ex), load(work / "new" / ex)
    bad = rows = 0
    for s, a in old.items():
        b = new.get(s)
        m = b["rx"] <= end_ms
        for k in a:
            if b is None or not np.array_equal(a[k], b[k][m]):
                bad += 1
                print("MISMATCH", s, k, len(a[k]), None if b is None else int(m.sum()))
        rows += len(a["rx"])
    print({"symbols_old": len(old), "symbols_new": len(new), "rows_compared": rows, "mismatches": bad, "new_rows_pass1": n1, "new_rows_pass2": n2})


if __name__ == "__main__":
    main(*sys.argv[1:6])
