#!/usr/bin/env python3
"""G-022 turbo: table of an A/B run (`abrun.sh`): per window, old (A) against new (B) — fast share, lag p50 / p99, late, coverage, REST missing — and the verdict.

  abtable.py <run dir> [--old A] [--new B]

Verdict rule (fixed before the run): the new flags are better if the mean coverage (cov_any, book cells against the union of both runs) of B >= that of A over the windows
AND the book lag p99 of B is lower than that of A in at least 4 of 5 windows. Windows are compared only side by side (A and B run in the same minutes); the fast share per window is printed.
"""
import argparse
import glob
import json
import os
import re


def load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--old", default="A")
    ap.add_argument("--new", default="B")
    a = ap.parse_args()
    wins = sorted(int(re.search(r"win(\d+)\.json", f).group(1)) for f in glob.glob(os.path.join(a.dir, "win*.json")))
    print(f"| окно | быстрых подкл. из {a.old} / {a.new} | быстрых принятых {a.old} / {a.new} | лаг книги p50, мс {a.old} / {a.new} | лаг книги p99, мс {a.old} / {a.new} | поздних (>2 с) {a.old} / {a.new} | покрытие cov_any {a.old} / {a.new}, % | cov_timely {a.old} / {a.new}, % | REST пропущено {a.old} / {a.new} | замен пути (path_switch + path_upgrade) {a.old} / {a.new} | отложено ленивой {a.new} | таймауты ping {a.old} / {a.new} |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    better_p99, covs = 0, {a.old: [], a.new: []}
    n = 0
    for k in wins:
        w = load(os.path.join(a.dir, f"win{k}.json"))
        if not w:
            continue
        r = w["runs"]
        o, nw = r.get(a.old), r.get(a.new)
        if not o or not nw:
            continue
        rest = {L: load(os.path.join(a.dir, f"rest-{L}-{k}.json")) for L in (a.old, a.new)}

        def miss(L):
            t = (rest[L] or {}).get("total")
            if not t:
                return "—"
            return str(t["missing_after_covered"] if k == 1 and "missing_after_covered" in t else t["missing_in_file"]) + f" из {t['rest']}"

        def ev(x, *names):
            return sum(x["events"].get(nm, 0) for nm in names)

        def sh(x, key):
            if x[key] is None:
                return "—"
            return f"{x[key] * 100:.0f} % ({x['fast_dials']}/{x['dials']})" if key == "fast_share" else f"{x[key] * 100:.0f} %"

        n += 1
        if o["lag_p99"] is not None and nw["lag_p99"] is not None and nw["lag_p99"] < o["lag_p99"]:
            better_p99 += 1
        for L, x in ((a.old, o), (a.new, nw)):
            covs[L].append(x["cov_any"] if x["cov_any"] is not None else 0.0)
        print(f"| {k} | {sh(o, 'fast_share')} / {sh(nw, 'fast_share')} | {sh(o, 'accepted_fast_share')} / {sh(nw, 'accepted_fast_share')} | {o['lag_p50']} / {nw['lag_p50']} | {o['lag_p99']} / {nw['lag_p99']} | {o['late']} / {nw['late']} | "
              f"{o['cov_any'] * 100:.3f} / {nw['cov_any'] * 100:.3f} | {o['cov_timely'] * 100:.3f} / {nw['cov_timely'] * 100:.3f} | {miss(a.old)} / {miss(a.new)} | {ev(o, 'path_switch', 'path_upgrade')} / {ev(nw, 'path_switch', 'path_upgrade')} | {ev(nw, 'replace_deferred')} | "
              f"{ev(o, 'timeout')} / {ev(nw, 'timeout')} |")
    if n:
        mo, mn = sum(covs[a.old]) / n, sum(covs[a.new]) / n
        ok = mn >= mo and better_p99 >= 4
        print(f"\nСредняя cov_any: {a.old} {mo * 100:.3f} %, {a.new} {mn * 100:.3f} %; p99 лага книги ниже у {a.new} в {better_p99} из {n} окон. "
              f"Вердикт по заранее заданному правилу: {a.new} {'лучше' if ok else 'не лучше'} {a.old}.")


if __name__ == "__main__":
    main()
