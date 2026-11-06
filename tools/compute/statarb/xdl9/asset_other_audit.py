#!/usr/bin/env python3
"""TK-493 w4 (хвост F5, D-01): состав класса `other` на 8 минутных биржах и аудит 20 случайных контрактов (seed 37 - номер протокола, как в F5 G4).

ТОЛЬКО формирование 01.10-14.10 (вход: instruments.csv, готовые таблицы формирования asset_map.csv, asset_other_resolution.csv);
торговое окно не читается. Стандартная библиотека, ничего не считает по рядам цен.

  python asset_other_audit.py --out DIR     пишет в DIR: other_composition.csv (биржа x источник класса x итог), other_audit20.csv (выборка 20 + суждение)
Суждение пишется рукой в asset_other_audit_truth.csv (exchange, symbol, truth, truth_basis; truth = «в тест» | «вне» | «неясно» | «нет данных»);
скрипт подклеивает его к выборке и считает согласие.
"""
import argparse, collections, csv, glob, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asset_class as AC  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "data", "xdl9")
EXCH = ["binance", "bybit", "okx", "bingx", "aster", "kraken", "lighter", "bitget"]
SEED = 37
N_SAMPLE = 20
TRUTH = os.path.join(HERE, "asset_other_audit_truth.csv")


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    inst = []
    for fn in sorted(glob.glob(os.path.join(DATA, "inst", "*.csv"))):
        inst.extend(read(fn))
    pb = AC.derive_private_bases(inst)
    res = AC.load_resolution()
    amap = {(r["exchange"], r["symbol"]): r for r in read(os.path.join(DATA, "asset_map.csv"))}
    oth = []
    for r in inst:
        if r["class"] != "other" or r["exchange"] not in EXCH:
            continue
        am = amap.get((r["exchange"], r["symbol"]))
        c = AC.classify(r, pb, res)
        n_valid = int(am["n_valid"]) if am else 0
        if c.kind == "other_out" and n_valid == 0:
            fate = "вне: нет валидных минут на формировании"
        elif c.kind == "other_out":
            fate = "вне: класс не определён измерением"
        elif c.kind == "other_in":
            fate = "в тест: " + c.big
        elif c.kind == "pre_listing":
            fate = "вне: пред-листинг (первичное поле)"
        elif c.kind == "private_base":
            fate = "вне: приватная база (ключ)"
        else:
            fate = "вне: " + c.kind
        oth.append(dict(exchange=r["exchange"], symbol=r["symbol"], base=r["base"], class_src=r["class_src"], n_valid=n_valid,
                        price_med=am["price_med"] if am else "", fate=fate, role=am["role"] if am else "", big=am["big"] if am else "",
                        basis=(res.get((r["exchange"], r["symbol"])) or ("", "", ""))[2] or c.reason))
    oth.sort(key=lambda d: (d["exchange"], d["symbol"]))
    # ---- состав
    cnt = collections.Counter((d["exchange"], d["class_src"].split("|")[0][:60], d["fate"]) for d in oth)
    with open(os.path.join(a.out, "other_composition.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["exchange", "class_src", "fate", "contracts"])
        for (ex, cs, fate), n in sorted(cnt.items()):
            w.writerow([ex, cs, fate, n])
    # ---- выборка
    rnd = random.Random(SEED)
    sample = rnd.sample(oth, N_SAMPLE)
    sample.sort(key=lambda d: (d["exchange"], d["symbol"]))
    truth = {}
    if os.path.exists(TRUTH):
        for r in read(TRUTH):
            truth[(r["exchange"], r["symbol"])] = r
    cols = ["exchange", "symbol", "class_src", "n_valid", "price_med", "fate", "basis", "truth", "truth_basis", "agree"]
    n_agree = n_judged = n_other = 0
    with open(os.path.join(a.out, "other_audit20.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for d in sample:
            t = truth.get((d["exchange"], d["symbol"]))
            tv, tb = (t["truth"], t["truth_basis"]) if t else ("", "")
            # согласие: истина «в тест» <-> итог «в тест»; «неясно» и «нет данных» (у контракта нет валидных минут) в согласие не входят
            agree = ""
            if tv in ("в тест", "вне"):
                n_judged += 1
                agree = int((tv == "в тест") == d["fate"].startswith("в тест"))
                n_agree += agree
            elif tv:
                n_other += 1
            w.writerow([d["exchange"], d["symbol"], d["class_src"], d["n_valid"], d["price_med"], d["fate"], d["basis"][:160], tv, tb, agree])
    tot = collections.Counter(d["fate"].split(":")[0] + ": " + d["fate"].split(": ", 1)[1].split(" (")[0] for d in oth)
    print("other на 8 минутных биржах: %d; по биржам %s" % (len(oth), dict(collections.Counter(d["exchange"] for d in oth))))
    print("итог:", dict(tot))
    print("выборка %d (seed %d): суждение «в тест/вне» %d, из них согласие %d; «неясно/нет данных» %d" % (len(sample), SEED, n_judged, n_agree, n_other))


if __name__ == "__main__":
    main()
