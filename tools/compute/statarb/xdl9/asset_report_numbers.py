#!/usr/bin/env python3
"""TK-493 F5: числа для отчёта xdl9-asset-map-2026-11-06.md и малые csv в docs/findings/xdl9-asset-map-2026-11-06/ (из data/xdl9/)."""
import csv, collections, hashlib, json, os, shutil, sys
import numpy as np
import asset_lib as L

OUTDOC = os.path.join(L.ROOT, "docs", "findings", "xdl9-asset-map-2026-11-06")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    os.makedirs(OUTDOC, exist_ok=True)
    amap = list(csv.DictReader(open(os.path.join(L.DATA, "asset_map.csv"), encoding="utf-8")))
    pairs = list(csv.DictReader(open(os.path.join(L.DATA, "asset_pairs.csv"), encoding="utf-8")))
    md = []
    md.append("### Т1. Контракты по биржам (из instruments.csv со свечами формирования)\n")
    md.append("| биржа | файлов свечей | основных | дублей | исключено | из них: вне теста | без валидных минут |")
    md.append("|---|---|---|---|---|---|---|")
    for ex in L.EXCH:
        v = [r for r in amap if r["exchange"] == ex]
        c = collections.Counter(r["role"] for r in v)
        oot = sum(1 for r in v if r["role"] == "excluded" and r["reason"].startswith("вне теста"))
        nov = sum(1 for r in v if r["role"] == "excluded" and r["reason"].startswith("нет валидных"))
        md.append("| %s | %d | %d | %d | %d | %d | %d |" % (ex, len(v), c["main"], c["dup"], c["excluded"], oot, nov))
    tot = collections.Counter(r["role"] for r in amap)
    md.append("| **всего** | %d | %d | %d | %d | | |" % (len(amap), tot["main"], tot["dup"], tot["excluded"]))
    md.append("\n### Т2. Основные контракты: укрупнённый класс × биржа\n")
    classes = ["крипта", "акции+ETF", "индексы", "металлы+энергия", "фиат"]
    md.append("| класс | " + " | ".join(L.EXCH) + " | всего |")
    md.append("|---|" + "---|" * (len(L.EXCH) + 1))
    for cl in classes:
        row = []
        for ex in L.EXCH:
            row.append(sum(1 for r in amap if r["exchange"] == ex and r["big"] == cl and r["role"] == "main"))
        md.append("| %s | %s | %d |" % (cl, " | ".join(map(str, row)), sum(row)))
    md.append("\n### Т3. Исключённые контракты: причины\n")
    cc = collections.Counter()
    for r in amap:
        if r["role"] == "excluded":
            cc[(r["exchange"], r["reason"])] += 1
    byreason = collections.Counter()
    for (ex, key), n in cc.items():
        byreason[key] += n
    md.append("| причина | контрактов | по биржам |")
    md.append("|---|---|---|")
    for key, n in byreason.most_common():
        exs = ", ".join("%s %d" % (ex, m) for (ex, k), m in sorted(cc.items()) if k == key)
        md.append("| %s | %d | %s |" % (key, n, exs))
    md.append("\n### Т4. Дубли внутри биржи: причины\n")
    dc = collections.Counter()
    for r in amap:
        if r["role"] == "dup":
            dc[r["reason"].split(" [")[0]] += 1
    md.append("| причина | контрактов |")
    md.append("|---|---|")
    for k, n in dc.most_common():
        md.append("| %s | %d |" % (k, n))
    cross = [r for r in pairs if r["scope"] == "между биржами"]
    md.append("\n### Т5. Пары между биржами: вердикт × класс\n")
    vlab = ["одно", "пограничное", "разное", "нет данных", "класс не совпал"]

    def vkey(v):
        for k in vlab:
            if v.startswith(k):
                return k
        return v
    cnt = collections.Counter()
    for r in cross:
        cl = r["big1"] if r["class_match"] == "1" else "(разные классы)"
        cnt[(cl, vkey(r["verdict"]))] += 1
    md.append("| класс | " + " | ".join(vlab) + " | всего | годно для C1/D2 |")
    md.append("|---|" + "---|" * (len(vlab) + 2))
    for cl in classes + ["(разные классы)"]:
        row = [cnt[(cl, v)] for v in vlab]
        usable = sum(1 for r in cross if r["c1d2"] == "1" and (r["big1"] == cl))
        md.append("| %s | %s | %d | %d |" % (cl, " | ".join(map(str, row)), sum(row), usable))
    md.append("| **всего** | %s | %d | %d |" % (" | ".join(str(sum(cnt[(cl, v)] for cl in classes + ["(разные классы)"])) for v in vlab),
                                              len(cross), sum(1 for r in cross if r["c1d2"] == "1")))
    md.append("\n### Т6. Годные для C1/D2 пары: биржа × биржа\n")
    m = collections.Counter()
    for r in cross:
        if r["c1d2"] == "1":
            m[tuple(sorted((r["ex1"], r["ex2"])))] += 1
    md.append("| | " + " | ".join(L.EXCH) + " |")
    md.append("|---|" + "---|" * len(L.EXCH))
    for a in L.EXCH:
        md.append("| %s | %s |" % (a, " | ".join("" if a == b else str(m[tuple(sorted((a, b)))]) for b in L.EXCH)))
    nd = [r for r in cross if r["class_match"] == "1" and int(r["n_ret"]) < 300]
    bins = [0, 1, 10, 30, 100, 200, 300]
    h = np.histogram([int(r["n_ret"]) for r in nd], bins=bins)[0]
    md.append("\n### Т7. Пары с совпавшим классом, у которых общих доходностей меньше 300 (вердикт «нет данных»): %d\n" % len(nd))
    md.append("| n_ret | 0 | 1-9 | 10-29 | 30-99 | 100-199 | 200-299 |")
    md.append("|---|---|---|---|---|---|---|")
    md.append("| пар | " + " | ".join(str(int(x)) for x in h) + " |")
    kc = collections.defaultdict(set)
    for r in amap:
        if r["role"] in ("main", "dup"):
            kc[r["key"]].add(r["big"])
    coll = sorted(k for k, v in kc.items() if len(v) > 1)
    md.append("\n### Т8. Один ключ имени в нескольких классах (%d): %s\n" % (len(coll), ", ".join(coll)))
    open(os.path.join(OUTDOC, "_numbers.md"), "w", encoding="utf-8").write("\n".join(md))
    print("\n".join(md))

    with open(os.path.join(OUTDOC, "excluded.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["exchange", "symbol", "class", "big", "status", "n_valid", "reason"])
        for r in amap:
            if r["role"] == "excluded":
                w.writerow([r["exchange"], r["symbol"], r["class"], r["big"], r["status"], r["n_valid"], r["reason"]])
    with open(os.path.join(OUTDOC, "dups.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["exchange", "symbol", "main_symbol", "asset_id", "reason", "S_vs_main", "corr_vs_main", "ratio_vs_main", "n_ret",
                    "unverified", "leg_c1d2"])
        for r in amap:
            if r["role"] == "dup":
                w.writerow([r["exchange"], r["symbol"], r["main_symbol"], r["asset_id"], r["reason"], r["dup_S"], r["dup_corr"],
                            r["dup_ratio"], r["dup_n_ret"], r["dup_unverified"], r["leg_c1d2"]])
    keep = [r for r in cross if r["verdict"].startswith(("пограничное", "разное")) or r["verdict"].startswith("класс не совпал (связь есть)")]
    with open(os.path.join(OUTDOC, "ambiguous_pairs.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(keep[0].keys()))
        w.writeheader()
        w.writerows(keep)
    cm = [r for r in cross if r["class_match"] == "0"]
    with open(os.path.join(OUTDOC, "class_mismatch_pairs.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cm[0].keys()))
        w.writeheader()
        w.writerows(cm)
    for nm in ("calib_pairs.csv", "calib_summary.json", "calib_extended.csv", "audit_result.csv", "audit_sample.csv", "table_summary.json"):
        shutil.copy(os.path.join(L.DATA, nm), os.path.join(OUTDOC, nm))
    shutil.copy(os.path.join(L.HERE, "asset_alias.csv"), os.path.join(OUTDOC, "asset_alias.csv"))
    sc = list(csv.DictReader(open(os.path.join(L.DATA, "asset_scan.csv"), encoding="utf-8")))
    with open(os.path.join(OUTDOC, "scan_leftovers.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(sc[0].keys()))
        w.writeheader()
        w.writerows(sc)
    man = {}
    for nm in ("asset_map.csv", "asset_pairs.csv", "pairstats.csv", "asset_scan.csv", "calib_pairs.csv"):
        p = os.path.join(L.DATA, nm)
        man[nm] = dict(sha256=sha(p), bytes=os.path.getsize(p), rows=sum(1 for _ in open(p, encoding="utf-8")) - 1)
    for ex in L.EXCH:
        for ext in (".f32", ".idx.csv"):
            p = os.path.join(L.DATA, "asset", ex + ext)
            man["asset/" + ex + ext] = dict(sha256=sha(p), bytes=os.path.getsize(p))
    man["tools/compute/statarb/xdl9/asset_alias.csv"] = dict(sha256=sha(os.path.join(L.HERE, "asset_alias.csv")))
    json.dump(man, open(os.path.join(OUTDOC, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("манифест:", {k: v.get("rows") for k, v in man.items() if "rows" in v})


if __name__ == "__main__":
    main()
