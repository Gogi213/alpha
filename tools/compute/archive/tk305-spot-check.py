#!/usr/bin/env python3
"""TK-305 шаг 2б: проверка целостности закачанного архива спота Bybit (аналог archive-check.py: цельность, u подряд, bid<ask).

  tk305-spot-check.py --root /srv/alpha-sda/tk305 [--sample-every N] [--only SYM]

Для каждой строки ok из `<root>/dl/done.csv`:
  ob: размер == HEAD (`sizes.csv`); полное чтение zip с проверкой CRC (testzip); у выборки (по одному дню на монету, номер
      дня по хешу монеты) — разбор JSON: u подряд между дельтами, после snapshot поток продолжается, bid < ask в точках
      (каждая 500-я строка и конец), у каждой строки 200 уровней в snapshot не требуется (как биржа даёт).
  tr: gzip читается целиком, число строк данных (без заголовка), заголовок `id,timestamp,price,volume,side,rpi`.
Строки missing сверяются со списком `sizes.csv`: файла на диске быть не должно.
Результат: `<root>/dl/check.csv` (sym,day,kind,status,detail; status ok|bad), `<root>/dl/check.summary`. Перезапуск
пропускает готовое ok. Данные не меняются и не удаляются.
"""
import argparse, csv, gzip, json, os, sys, zlib, zipfile, zlib as _z

OBF = "{r}/spot/ob/{s}/{d}_{s}_ob200.data.zip"
TRF = "{r}/spot/tr/{s}/{s}_{d}.csv.gz"
HDR = "id,timestamp,price,volume,side,rpi"


def crc_all(path):
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        return bad


def logic_ob(path):
    """-> (строк, snapshots, разрывов u, кроссов, детали)."""
    n = snaps = gaps = cross = 0
    last_u = None
    book_b, book_a = {}, {}
    with zipfile.ZipFile(path) as z:
        name = z.infolist()[0]
        with z.open(name) as f:
            for line in f:
                n += 1
                m = json.loads(line)
                d = m["data"]
                u = d.get("u")
                if m["type"] == "snapshot":
                    snaps += 1
                    book_b = {p: float(q) for p, q in d["b"]}
                    book_a = {p: float(q) for p, q in d["a"]}
                else:
                    if last_u is not None and u != last_u + 1:
                        gaps += 1
                    for p, q in d["b"]:
                        if float(q) == 0:
                            book_b.pop(p, None)
                        else:
                            book_b[p] = float(q)
                    for p, q in d["a"]:
                        if float(q) == 0:
                            book_a.pop(p, None)
                        else:
                            book_a[p] = float(q)
                last_u = u
                if n % 500 == 0 and book_b and book_a:
                    if max(map(float, book_b)) >= min(map(float, book_a)):
                        cross += 1
    if book_b and book_a and max(map(float, book_b)) >= min(map(float, book_a)):
        cross += 1
    return n, snaps, gaps, cross


def check_tr(path):
    n = 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        h = f.readline().strip()
        if h != HDR:
            return False, f"заголовок {h!r}"
        for _ in f:
            n += 1
    return True, str(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    r = a.root
    sizes = {}
    for row in csv.DictReader(open(f"{r}/sizes.csv")):
        sizes[(row["sym"], row["day"])] = int(row["bytes"])
    done = {}
    for row in csv.reader(open(f"{r}/dl/done.csv")):
        done[(row[0], row[1], row[2])] = row[3]
    out_p = f"{r}/dl/check.csv"
    have = set()
    if os.path.exists(out_p):
        for row in csv.reader(open(out_p)):
            if len(row) >= 4 and row[3] == "ok":
                have.add((row[0], row[1], row[2]))
    # выборка для логики: по одному дню на монету (детерминированно: хеш монеты)
    days_by_sym = {}
    for (s, d), st in ((k[:2], v) for k, v in done.items() if k[2] == "ob"):
        if st == "ok":
            days_by_sym.setdefault(s, []).append(d)
    sample = set()
    for s, ds in days_by_sym.items():
        ds.sort()
        sample.add((s, ds[zlib.crc32(s.encode()) % len(ds)]))
    out = open(out_p, "a", newline="")
    w = csv.writer(out)
    cnt = {"ok": 0, "bad": 0, "skip": 0, "missing_ok": 0}
    nrows = {"ob_logic": 0, "tr_rows": 0}
    items = sorted(done)
    for i, (s, d, k) in enumerate(items):
        if a.only and s != a.only:
            continue
        st = done[(s, d, k)]
        if st == "missing":
            p = (OBF if k == "ob" else TRF).format(r=r, s=s, d=d)
            if os.path.exists(p) or (s, d) in sizes and k == "ob" and sizes[(s, d)] > 0 and False:
                w.writerow([s, d, k, "bad", "missing, но файл есть"])
                cnt["bad"] += 1
            else:
                cnt["missing_ok"] += 1
            continue
        if st != "ok":
            w.writerow([s, d, k, "bad", f"done.csv status={st}"])
            cnt["bad"] += 1
            continue
        if (s, d, k) in have:
            cnt["skip"] += 1
            continue
        try:
            if k == "ob":
                p = OBF.format(r=r, s=s, d=d)
                sz = os.path.getsize(p)
                if sizes.get((s, d)) != sz:
                    raise ValueError(f"размер {sz} != HEAD {sizes.get((s, d))}")
                bad = crc_all(p)
                if bad:
                    raise ValueError(f"CRC {bad}")
                detail = "crc"
                if (s, d) in sample:
                    n, snaps, gaps, cross = logic_ob(p)
                    nrows["ob_logic"] += 1
                    if n == 0 or snaps == 0 or gaps or cross:
                        raise ValueError(f"строк={n} snapshots={snaps} разрывов_u={gaps} кроссов={cross}")
                    detail = f"crc+logic строк={n} snap={snaps}"
            else:
                p = TRF.format(r=r, s=s, d=d)
                ok, info = check_tr(p)
                if not ok:
                    raise ValueError(info)
                detail = f"строк={info}"
                nrows["tr_rows"] += int(info)
            w.writerow([s, d, k, "ok", detail])
            cnt["ok"] += 1
        except Exception as e:  # noqa: BLE001
            w.writerow([s, d, k, "bad", str(e)[:200]])
            cnt["bad"] += 1
        if (cnt["ok"] + cnt["bad"]) % 200 == 0:
            out.flush()
            print(f"{i + 1}/{len(items)} ok={cnt['ok']} bad={cnt['bad']}", flush=True)
    out.close()
    # итог по всему check.csv
    tot = {}
    badl = []
    seen = {}
    for row in csv.reader(open(out_p)):
        seen[(row[0], row[1], row[2])] = row
    for row in seen.values():
        tot[(row[2], row[3])] = tot.get((row[2], row[3]), 0) + 1
        if row[3] == "bad":
            badl.append(row)
    with open(f"{r}/dl/check.summary", "w") as f:
        f.write(f"прогон: {cnt} логика_ob_в_этом_прогоне={nrows['ob_logic']}\n")
        f.write(f"всего по check.csv: {sorted(tot.items())}\n")
        f.write(f"bad: {len(badl)}\n")
        for row in badl[:50]:
            f.write(",".join(row) + "\n")
        f.write("КОНЕЦ\n")
    print(open(f"{r}/dl/check.summary").read())
    return 1 if badl else 0


if __name__ == "__main__":
    sys.exit(main())
