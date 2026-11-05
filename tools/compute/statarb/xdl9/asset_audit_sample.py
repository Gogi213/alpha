#!/usr/bin/env python3
"""TK-493 F5, G4: выборка аудита - 50 случайных (seed 37) и 30 пограничных пар из asset_pairs.csv (формирование, торговое окно не читается).
Случайные: из всех строк «между биржами». Пограничные: 30 строк с n_ret >= n_ret_min и совпавшим классом, у которых |ln(S / S_mid)| наименьшее
(по обе стороны порога). Вывод: data/xdl9/audit_sample.csv + печать для ручного просмотра (цены, корреляция, имена)."""
import csv, json, math, os, random, sys
import asset_lib as L

def main():
    pairs = list(csv.DictReader(open(os.path.join(L.DATA, "asset_pairs.csv"), encoding="utf-8")))
    amap = {(r["exchange"], r["symbol"]): r for r in csv.DictReader(open(os.path.join(L.DATA, "asset_map.csv"), encoding="utf-8"))}
    cal = json.load(open(os.path.join(L.DATA, "calib_summary.json"), encoding="utf-8"))
    mid = cal["S"]["mid"]
    cross = [r for r in pairs if r["scope"] == "между биржами"]
    rnd = random.Random(37)
    rand = rnd.sample(cross, 50)
    cand = [r for r in cross if r["class_match"] == "1" and int(r["n_ret"]) >= 300 and r["S"] not in ("", "nan")]
    cand.sort(key=lambda r: abs(math.log(max(float(r["S"]), 1e-9) / mid)))
    bound = cand[:30]
    out = []
    for kind, lst in (("случайная", rand), ("пограничная", bound)):
        for n, r in enumerate(lst, 1):
            a = amap[(r["ex1"], r["sym1"])]; b = amap[(r["ex2"], r["sym2"])]
            out.append(dict(id="%s-%02d" % ("R" if kind == "случайная" else "B", n), kind=kind, key=r["key"], ex1=r["ex1"], sym1=r["sym1"],
                            base1=a["base"], cls1=a["class"], q1=a["quote"], role1=a["role"], px1=a["price_med"], listed1=a["listed_ms"], zero1=a["zero_share"], note1=a["class_src"][:40],
                            ex2=r["ex2"], sym2=r["sym2"], base2=b["base"], cls2=b["class"], q2=b["quote"], role2=b["role"], px2=b["price_med"], listed2=b["listed_ms"], zero2=b["zero_share"], note2=b["class_src"][:40],
                            n_ret=r["n_ret"], corr=r["corr"], ratio=r["med_ratio"], dev=r["dev"], S=r["S"], sd_lr=r["sd_lr"], sd_l1=r["sd_l1"], verdict=r["verdict"], c1d2=r["c1d2"],
                            source=r["source"], why=r["why"]))
    with open(os.path.join(L.DATA, "audit_sample.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)
    import datetime
    def d(ms):
        try: return datetime.datetime.fromtimestamp(int(ms)/1000, datetime.timezone.utc).strftime("%m-%d")
        except Exception: return "-"
    for o in out:
        print("%s %-9s %s:%s[%s/%s %s %s] x %s:%s[%s/%s %s %s] px=%s/%s lst=%s/%s z=%s/%s n=%s c=%s r=%s S=%s sd_lr=%s sd_l1=%s => %s c1d2=%s %s" % (
            o["id"], o["key"][:10], o["ex1"][:4], o["sym1"][:22], o["cls1"][:4], o["base1"][:10], o["role1"][:3], o["q1"],
            o["ex2"][:4], o["sym2"][:22], o["cls2"][:4], o["base2"][:10], o["role2"][:3], o["q2"], o["px1"], o["px2"], d(o["listed1"]), d(o["listed2"]),
            o["zero1"][:4], o["zero2"][:4], o["n_ret"], o["corr"][:5], o["ratio"][:8], o["S"][:6], o["sd_lr"][:7], o["sd_l1"][:6], o["verdict"][:14], o["c1d2"], o["source"][:30]))

if __name__ == "__main__":
    main()
