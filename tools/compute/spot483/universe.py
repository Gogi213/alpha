#!/usr/bin/env python3
"""TK-483 (П-32 Д-1): вселенная спот-свечей Bybit — все перпы вселенной TK-476 (union-<биржа>.csv: монета прошла порог хоть в одном
месяце, в т.ч. делистнутые) → кандидат на споте Bybit. Спот-символ = база + USDT (множитель 1000/10000/1000000 перпа снят);
если у перпа был множитель, кандидатом идёт ещё и сырой символ. Источник «есть на споте»: instruments-info spot (идёт сейчас) и
листинг архива public.bybit.com/spot/ (есть когда-либо). Выход <out>/: universe.csv (по перпу), symbols.txt (кандидаты для hist.py).
usage: universe.py <каталог union-*.csv> <out>"""
import csv, json, os, re, sys, urllib.request

MULT = re.compile(r"^(1000000|100000|10000|1000)(?=[A-Z])")
SUFMULT = re.compile(r"(?<=[A-Z]{2})(1000000|100000|10000|1000)$")  # Bybit linear: SHIB1000USDT
EXCH = ("binance-um", "bybit-linear", "gate-futures_usdt")


def base_of(sym):
    """BTCUSDT / 1000PEPEUSDT / SHIB1000USDT / BTC_USDT -> (BTC, 1) / (PEPE, 1000) / (SHIB, 1000) / (BTC, 1)."""
    s = sym.replace("_", "")
    s = s[:-4] if s.endswith("USDT") else s
    m = MULT.match(s) or SUFMULT.search(s)
    if not m:
        return s, 1
    return (s[m.end():] if m.start() == 0 else s[:m.start()]), int(m.group(1))


def candidates(sym):
    b, mult = base_of(sym)
    out = [b + "USDT"]
    raw = sym.replace("_", "")
    if mult > 1 and raw not in out:
        out.append(raw)
    return out, b, mult


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode()


def spot_live():
    r = json.loads(get("https://api.bybit.com/v5/market/instruments-info?category=spot&limit=1000"))["result"]["list"]
    return {x["symbol"] for x in r}


def spot_archive():
    return set(re.findall(r'href="([^"/]+)"', get("https://public.bybit.com/spot/")))


def pick(cands, live, arch):
    """Первый кандидат, который есть на споте сейчас или в архиве; иначе None."""
    for c in cands:
        if c in live or c in arch:
            return c
    return None


def main():
    src, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    live, arch = spot_live(), spot_archive()
    rows, syms = [], set()
    for ex in EXCH:
        for r in csv.DictReader(open("%s/union-%s.csv" % (src, ex))):
            cands, b, mult = candidates(r["symbol"])
            sp = pick(cands, live, arch)
            if sp:
                syms.add(sp)
            rows.append([ex, r["symbol"], b, mult, sp or cands[0], int(sp in live) if sp else 0, int(sp in arch) if sp else 0])
    with open(out + "/universe.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["perp_exchange", "perp_symbol", "base", "mult", "spot_symbol", "spot_live", "spot_archive"])
        w.writerows(rows)
    open(out + "/symbols.txt", "w").write("".join(s + "\n" for s in sorted(syms)))
    print("перпов %d, баз %d, кандидатов на споте %d (идут сейчас %d, в архиве %d)" % (
        len(rows), len({r[2] for r in rows}), len(syms), len(syms & live), len(syms & arch)))


if __name__ == "__main__":
    main()
