#!/usr/bin/env python3
"""TK-493 w4 (хвост F7 / M-08): точность правила «США / не-США» из F7 (sel_catalog.py: метки биржи + доля дисперсии в кассе США) на 40 активах
с известной биржей листинга. ТОЛЬКО формирование: вход - data/xdl9/sel/session_assets.csv (решение правила по активам, формирование 01.10-14.10),
instruments.csv (метки региона, которые отдают сами биржи), суждение asset_us_rule_truth.csv (пишется рукой: тикер/название -> биржа листинга).
Торговое окно не читается. Стандартная библиотека.

Выборка (seed 37 - номер протокола, как в F5 G4): активы с решением «не-США» и с решением «США» перемешиваются по отдельности (random.Random(37).sample
от списка, отсортированного по asset_id); в каждой страте берутся первые 20, у которых истина известна (США | не-США | США (АДР)); «частная» и
«неизвестно» пропускаются и считаются. АДР иностранной компании (основной листинг вне США) - спорный случай: в согласие идёт как «США», отдельно помечен.

  python asset_us_rule_audit.py --out DIR     -> DIR/us_rule_audit40.csv, us_rule_nonus_flagged.csv, us_rule_label_misses.csv; метрики в stdout
"""
import argparse, collections, csv, glob, os, random, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asset_class as AC  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "data", "xdl9")
SEED = 37
N_PER_STRATUM = 20
TRUTH = os.path.join(HERE, "asset_us_rule_truth.csv")
KNOWN = ("США", "не-США", "США (АДР)")


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def labels():
    """Метки региона из первичных полей бирж: Bybit region=, Bitget `листинг XX` (note), Binance *_EQUITY (class_src). -> {ключ актива: {метки}}."""
    lab = collections.defaultdict(set)
    for fn in sorted(glob.glob(os.path.join(DATA, "inst", "*.csv"))):
        for r in read(fn):
            if r["exchange"] == "hyperliquid":
                continue
            k, _ = AC.norm_key(r)
            m = re.search(r"region=(\w*) ticker", r["note"])
            if r["exchange"] == "bybit" and m and m.group(1):
                lab[k].add("bybit:" + m.group(1))
            m = re.search(r"листинг ([A-Z]+)", r["note"])
            if r["exchange"] == "bitget" and m:
                lab[k].add("bitget:" + m.group(1))
            m = re.search(r"(HK|KR|CN)_EQUITY", r["class_src"])
            if r["exchange"] == "binance" and m:
                lab[k].add("binance:" + m.group(1))
    return lab


def agree_of(decision, truth):
    return int((decision == "не-США") == (truth == "не-США"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sa = sorted(read(os.path.join(DATA, "sel", "session_assets.csv")), key=lambda r: r["asset_id"])
    truth = {r["asset"]: r for r in read(TRUTH)}
    lab = labels()
    key = lambda r: r["asset_id"].split("|")[0]          # noqa: E731
    cause = lambda r: "метка биржи" if r["tags"] else "данные (доля дисперсии в кассе)"       # noqa: E731
    rnd = random.Random(SEED)
    strata = {d: [r for r in sa if r["decision"] == d] for d in ("не-США", "США")}
    sample, skipped = [], collections.Counter()
    for d in ("не-США", "США"):
        order = rnd.sample(strata[d], len(strata[d]))
        got = 0
        for r in order:
            t = truth.get(key(r))
            if t is None or t["truth"] not in KNOWN:
                skipped[(d, t["truth"] if t else "нет суждения")] += 1
                continue
            sample.append((d, r, t))
            got += 1
            if got == N_PER_STRATUM:
                break
    cols = ["asset", "rule_decision", "rule_cause", "median_var_share_session", "measured", "labels", "truth", "truth_basis", "agree", "disputed_adr"]
    with open(os.path.join(a.out, "us_rule_audit40.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for d, r, t in sample:
            w.writerow([key(r), d, cause(r) if d == "не-США" else r["why"].split(":")[0], r["median_var_share_session"], r["measured"],
                        ";".join(sorted(lab.get(key(r), []))), t["truth"], t["truth_basis"], agree_of(d, t["truth"]), int(t["truth"] == "США (АДР)")])
    # ---- метрики выборки
    print("выборка %d активов (seed %d); пропущено при отборе: %s" % (len(sample), SEED, dict(skipped)))
    for d in ("не-США", "США"):
        s = [(r, t) for dd, r, t in sample if dd == d]
        ok = sum(agree_of(d, t["truth"]) for _, t in s)
        adr = sum(1 for _, t in s if t["truth"] == "США (АДР)")
        print("  решение %-7s: %d активов, согласие с истиной %d (%.0f %%); из них спорных АДР %d" % (d, len(s), ok, 100.0 * ok / max(len(s), 1), adr))
    tot_ok = sum(agree_of(d, t["truth"]) for d, _, t in sample)
    print("  всего: согласие %d из %d (%.0f %%)" % (tot_ok, len(sample), 100.0 * tot_ok / len(sample)))
    print("  расхождения (решение -> истина):", "; ".join("%s: %s -> %s" % (key(r), d, t["truth"]) for d, r, t in sample if not agree_of(d, t["truth"])))
    # ---- все активы с решением «не-США»: полная проверка суждением
    flagged = [r for r in sa if r["decision"] == "не-США"]
    with open(os.path.join(a.out, "us_rule_nonus_flagged.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["asset", "big", "exchanges", "rule_cause", "median_var_share_session", "labels", "truth", "truth_basis"])
        for r in flagged:
            t = truth.get(key(r), {})
            w.writerow([key(r), r["big"], r["exchanges"], cause(r), r["median_var_share_session"], ";".join(sorted(lab.get(key(r), []))),
                        t.get("truth", ""), t.get("truth_basis", "")])
    by = collections.defaultdict(collections.Counter)
    for r in flagged:
        by[cause(r)][truth.get(key(r), {}).get("truth", "нет суждения")] += 1
    print("все %d активов с решением «не-США» (суждение по тикеру/названию):" % len(flagged))
    for c, cnt in by.items():
        n = sum(cnt.values())
        print("  %-34s %3d: не-США %d, США %d, США (АДР) %d, частная %d" % (c, n, cnt["не-США"], cnt["США"], cnt["США (АДР)"], cnt["частная"]))
    # ---- активы с решением «США», у которых биржа сама называет нe-США регион
    misses = []
    for r in sa:
        if r["decision"] != "США":
            continue
        non = sorted(x for x in lab.get(key(r), []) if not x.endswith(":US"))
        if non:
            misses.append([key(r), r["big"], r["exchanges"], r["median_var_share_session"], r["why"][:60], ";".join(non)])
    with open(os.path.join(a.out, "us_rule_label_misses.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["asset", "big", "exchanges", "median_var_share_session", "rule_why", "non_us_labels"])
        w.writerows(misses)
    lab_non = {k for k, v in lab.items() if any(not x.endswith(":US") for x in v)} & {key(r) for r in sa}
    flagged_keys = {key(r) for r in flagged}
    print("активы с меткой биржи не-США: %d; правило их пометило не-США: %d (%.0f %%), пропустило: %d (с данными %d, без данных %d)" % (
        len(lab_non), len(lab_non & flagged_keys), 100.0 * len(lab_non & flagged_keys) / len(lab_non), len(misses),
        sum(1 for m in misses if m[3]), sum(1 for m in misses if not m[3])))


if __name__ == "__main__":
    main()
