#!/usr/bin/env python3
"""TK-581: «дыры у ликвидных символов» по картам `sym` из coverage.py (символ × секунда БИРЖИ, E = приём − lag; в карту попадают записи, принятые до конца окна скана).
Для окна [с, по] (UTC, секунды суток) и типа записей (b — книга, m — mark):
  ликвидные символы = запись есть минимум в 95 % E-секунд (блоков) за 120 с [с−150, с−30) ДО окна (эпизодов в них нет — проверяет вызывающий);
  у них в окне: пустых символо-секунд / (символов × длительность) — доля; самая длинная дыра подряд (с); число символов с дырой >= 5 с; число символов без записи за всё окно; записей в окне против ожидаемых (ожидание = число записей этих же символов в секунду за те 120 с × длительность окна).
Это оценка потери книги/mark, не зависящая от общего уровня рынка: ликвидный символ в обычную секунду обновляется; пустая секунда в эпизод и пустая секунда в контроле сравниваются.
Для медленных потоков (mark Gate — раз в несколько секунд) секунда как единица не годится: `--block N` считает «записи нет» по блокам из N секунд (ликвидный = запись в ≥ 95 % блоков за 120 с до окна;
пусто = блок без записи; самая длинная дыра — в секундах).
python3 cov_sym.py <cov-*.json ...> --ep HH:MM:SS-HH:MM:SS [--ep ...] [--ctl HH:MM:SS-HH:MM:SS ...] [--types bm] [--block N]"""
import json, sys, base64, zlib, collections

MARGIN = 300  # как в coverage.py
PRE = 120
PRE_GAP = 30  # окно «до» кончается за 30 с до начала: пауза бывает раньше, чем её увидел netwatch (Binance mark, эпизод 5: молчание с 21:47:40)
LIQ = 0.95
args = sys.argv[1:]
files = [a for a in args if a.endswith(".json")]
h = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
eps, ctl = [], []
types = "bm"
BLK = 1
for i, a in enumerate(args):
    if a in ("--ep", "--ctl"):
        spec, _, pre_end = args[i + 1].partition("@")  # --ep A-B@C: окно «до» для ликвидных кончается в C (по умолчанию A−30 с)
        lo, hi = spec.split("-")
        (eps if a == "--ep" else ctl).append((h(lo), h(hi), h(pre_end) if pre_end else None))
    elif a == "--types":
        types = args[i + 1]
    elif a == "--block":
        BLK = int(args[i + 1])
hms = lambda s: f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def load_maps(path):
    d = json.load(open(path))
    w0 = d["w0_ms"] // 1000
    day0 = w0 - (w0 % 86400)
    maps = {"b": {}, "m": {}}
    for f in d["files"]:
        for k in maps:
            for sy, z in f["sym"].get(k, {}).items():
                a = zlib.decompress(base64.b64decode(z))
                old = maps[k].get(sy)
                maps[k][sy] = a if old is None else bytes(min(255, x + y) for x, y in zip(old, a))
    return d["exchange"], maps, w0 - day0


def longest_zero_run(b):
    best = cur = 0
    for x in b:
        if x == 0:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def window(maps_k, off, lo, hi, pre_end=None):
    """off — секунда суток начала скана (w0). j = секунда суток − off + MARGIN. Единица — блок из BLK секунд."""
    j0, j1 = lo - off + MARGIN, hi - off + MARGIN + 1
    pe = (pre_end - off + MARGIN) if pre_end else j0 - PRE_GAP
    p0 = pe - PRE
    npre, nb = PRE // BLK, (j1 - j0) // BLK
    empty = longest = ge5 = allempty = n = 0
    exp = act = 0.0
    for s, a in maps_k.items():
        if sum(1 for i in range(npre) if any(a[p0 + i * BLK:p0 + (i + 1) * BLK])) < LIQ * npre:
            continue
        n += 1
        seg = bytes(0 if any(a[j0 + i * BLK:j0 + (i + 1) * BLK]) else 1 for i in range(nb))
        z = sum(seg)
        empty += z
        r = longest_zero_run(bytes(1 - x for x in seg))
        longest = max(longest, r * BLK)
        if r * BLK >= 5:
            ge5 += 1
        if z == nb:
            allempty += 1
        exp += sum(a[p0:pe]) / PRE * (j1 - j0)
        act += sum(a[j0:j1])
    return n, nb, empty, (100.0 * empty / (n * nb) if n else 0.0), longest, ge5, allempty, act, exp


for p in files:
    ex, maps, off = load_maps(p)
    print(f"### {ex}")
    for k in types:
        kn = {"b": "книга", "m": "mark"}[k]
        print(f"  [{kn}] символов в карте {len(maps[k])}; единица — {BLK} с")
        rows = {"эпизод": [], "контроль": []}
        for tag, lst in (("эпизод", eps), ("контроль", ctl)):
            for lo, hi, pre_end in lst:
                n, dur, empty, pct, longest, ge5, allempty, act, exp = window(maps[k], off, lo, hi, pre_end)
                rows[tag].append((lo, hi, n, dur, empty, pct, longest, ge5, allempty))
                print(f"    {tag:8s} {hms(lo)}–{hms(hi)} ({dur * BLK:>2} с): ликвидных {n:>4}; пустых символо-с {empty:>6} = {pct:5.1f} %; "
                      f"самая длинная дыра {longest:>2} с; символов с дырой ≥ 5 с: {ge5:>4}; без записей всё окно: {allempty:>4}; записей {act:>8.0f} из {exp:>8.0f} = {100.0 * act / exp if exp else 0:5.1f} %")
        if rows["контроль"]:
            c = rows["контроль"]
            print(f"    шум (контроль, {len(c)} окон): пустых {min(x[5] for x in c):.1f}–{max(x[5] for x in c):.1f} %, "
                  f"самая длинная дыра {min(x[6] for x in c)}–{max(x[6] for x in c)} с, с дырой ≥ 5 с {min(x[7] for x in c)}–{max(x[7] for x in c)}, "
                  f"ликвидных {min(x[2] for x in c)}–{max(x[2] for x in c)}")
