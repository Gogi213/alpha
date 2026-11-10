#!/usr/bin/env python3
"""TK-522 (В-125…В-127): сборка `<база>[.k2.k2|.k1p60].naive.csv` выхода `orch.py --naive-only` в один файл (csv.gz).
Запуск: naive_collect.py <out_orch> <выход.csv.gz> [pairs.csv]. Строки: ctl (real | K2 | K1+60000), asset, chunk, pair + колонки write_naive (ядро, main.rs); `<база>.k2.naive.csv` (настоящие эпизоды прогона варианта K2) пропускается как дубль real.
Ключ к `<база>` и `<база>.path.csv` — (pair, theta, t0_ms); возвращает число строк по ctl."""
import collections, csv, gzip, os, sys

EXTRA = "exA,exB,ctA,ctB,sec_in_hour,spr_a0_bps,spr_b0_bps"   # с pairs.csv (запрос CEO 10.11): биржи ног, монет в единице размера (OKX ctVal·ctMult; Gate L1 уже в монетах — 1), секунда часа входа, спред ноги на t0, бпс


def pair_info(path):
    """pairs.csv -> {pair: (exA, exB, ctA, ctB)}"""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["pair"]: (r["exA"], r["exB"], r["ctA"], r["ctB"]) for r in csv.DictReader(f)}


def extra(info, pair, cols, line):
    """значения EXTRA к строке naive.csv; нет колонок расширенного ядра (прежний файл) — пусто"""
    ex = info[pair]
    if "a_bid0" not in cols:
        return ",".join([*ex, "", "", ""])
    v = line.rstrip("\n").split(",")
    g = lambda k: float(v[cols[k]])
    spr = lambda b, a: f"{2e4 * (g(a) - g(b)) / (g(a) + g(b)):.4f}"
    return ",".join([*ex, str(int(v[cols["t0_ms"]]) // 1000 % 3600), spr("a_bid0", "a_ask0"), spr("b_bid0", "b_ask0")])

CTL = (("k2.k2", "K2"), ("k1p60", "K1+60000"), ("k2", None), ("drain", None))


def main(out, dst, pairs=None):
    n = collections.Counter()
    head = None
    info = pair_info(pairs) if pairs else None
    with gzip.open(dst, "wt", newline="") as w:
        for root, _, fs in sorted(os.walk(out)):
            for f in sorted(fs):
                if not f.endswith(".naive.csv"):
                    continue
                stem = f[: -len(".naive.csv")]
                pair, _, var = stem.partition(".")
                ctl = "real"
                if var:
                    hit = [c for v, c in CTL if var == v]
                    if not hit or hit[0] is None:
                        continue
                    ctl = hit[0]
                rel = os.path.relpath(root, out).replace(os.sep, "/").split("/")
                asset, chunk = (rel + ["", ""])[:2]
                with open(os.path.join(root, f), encoding="utf-8") as r:
                    h = r.readline().rstrip("\n")
                    if head is None:
                        head = h
                        cols = {k: i for i, k in enumerate(h.split(","))}
                        w.write(f"ctl,asset,chunk,pair,{h}" + (f",{EXTRA}" if info else "") + "\n")
                    elif h != head:
                        sys.exit(f"{root}/{f}: шапка иная")
                    for line in r:
                        w.write(f"{ctl},{asset},{chunk},{pair},{line.rstrip(chr(10))}" + (f",{extra(info, pair, cols, line)}" if info else "") + "\n")
                        n[ctl] += 1
    print(dict(n))


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
