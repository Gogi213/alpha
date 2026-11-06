#!/usr/bin/env python3
"""TK-493 w4 (D-01, П-37 r3 §4.1 [Н42]): отдельные колонки `pre_listing` (0|1) и `pre_listing_src` в instruments.csv - признак пред-листинга
из ПЕРВИЧНЫХ полей биржи (asset_class.prelisting: Bybit status=PreLaunch / isPreListing=true, Binance underlyingType=PREMARKET, OKX ruleType=pre_market,
Aster underlyingSubType=pre-launch; плюс Kraken category=Pre-IPO и Hyperliquid perpCategories=preipo). Текст `note` как прозу не разбирает.

  python inst_prelisting_col.py --src DIR --dst DIR      читает DIR/<биржа>.csv или DIR/<биржа>/instruments.csv, пишет копии с колонками в --dst/<биржа>.csv
  python inst_prelisting_col.py --check DIR             проверка: колонки есть и совпадают с функцией (код выхода 1 при расхождении)
Исходные файлы не меняются, если --dst другой каталог. Окончания строк (CRLF) и порядок прежних колонок сохраняются; повторный запуск идемпотентен.
Только stdlib (Steam Deck, Python 3.14)."""
import argparse, csv, io, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asset_class as AC  # noqa: E402

COLS = ("pre_listing", "pre_listing_src")


def find_files(d):
    """-> {имя: путь}: DIR/<имя>.csv (плоский каталог data/xdl9/inst) либо DIR/<имя>/instruments.csv (выгрузка xdl)."""
    out = {}
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if n.endswith(".csv") and os.path.isfile(p):
            out[n[:-4]] = p
        elif os.path.isfile(os.path.join(p, "instruments.csv")):
            out[n] = os.path.join(p, "instruments.csv")
    return out


def read(path):
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    rdr = csv.DictReader(io.StringIO(text, newline=""))
    return rdr.fieldnames, list(rdr), eol


def convert(path, dst):
    """-> (строк, помечено, расхождений с уже имеющейся колонкой)."""
    fields, rows, eol = read(path)
    mism = flagged = 0
    for r in rows:
        pre, src = AC.prelisting(r)
        if r.get("pre_listing") not in (None, "") and r.get("pre_listing") != str(pre):
            mism += 1
        r["pre_listing"], r["pre_listing_src"] = str(pre), src
        flagged += pre
    out_fields = list(fields) + [c for c in COLS if c not in fields]
    if dst:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=out_fields, lineterminator=eol)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, dst)
    return len(rows), flagged, mism


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--dst")
    ap.add_argument("--check")
    a = ap.parse_args()
    bad = 0
    if a.check:
        for ex, p in find_files(a.check).items():
            fields, rows, _ = read(p)
            if not all(c in fields for c in COLS):
                print("check %-12s НЕТ колонок %s" % (ex, COLS))
                bad += 1
                continue
            n_bad = sum(1 for r in rows if (r["pre_listing"], r["pre_listing_src"]) != tuple(str(x) for x in AC.prelisting(r)))
            print("check %-12s строк %4d, помечено %3d, расхождений с функцией %d" % (ex, len(rows), sum(int(r["pre_listing"]) for r in rows), n_bad))
            bad += n_bad
        return 1 if bad else 0
    for ex, p in find_files(a.src).items():
        dst = os.path.join(a.dst, ex + ".csv") if a.dst else None      # выход всегда плоский: <dst>/<биржа>.csv (как data/xdl9/inst)
        n, fl, mm = convert(p, dst)
        print("%-12s строк %4d, pre_listing=1: %3d, расхождений со старой колонкой %d -> %s" % (ex, n, fl, mm, dst or "(только подсчёт)"))
        bad += mm
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
