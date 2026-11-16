#!/usr/bin/env python3
"""G-022 pool-fix: задержка прихода и поток записей «топ-10 против остальных» в окне лестницы (одним скриптом по сырым файлам, п. 23).

  symlag.py <корень данных окна> <t0_ms> --top <символ,символ,…> [--from-s 10] [--to-s 175] [--out file.json]

Строки `b` (книга) и `t` (сделки) за [t0 + from, t0 + to] по времени приёма: на группу «топ» и «остальные» — число записей в секунду, p50 / p99 / max лага прихода (`lag`, мс, столбец 3 строки
после типа и dns), доля записей с лагом > 1 с. Книга и сделки — отдельно. Только чтение; `zstd -dc` на каждый файл.
"""
import argparse
import collections
import glob
import json
import os
import subprocess


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("t0", type=int)
    ap.add_argument("--top", required=True)
    ap.add_argument("--from-s", type=float, default=10)
    ap.add_argument("--to-s", type=float, default=175)
    ap.add_argument("--out")
    a = ap.parse_args()
    top = set(a.top.split(","))
    lo, hi = a.t0 + int(a.from_s * 1000), a.t0 + int(a.to_s * 1000)
    lag = {(k, g): [] for k in "bt" for g in ("top", "rest")}
    for f in sorted(glob.glob(os.path.join(a.root, "20*", "*.tsv.zst"))):
        t = 0
        p = subprocess.run(["zstd", "-dc", f], capture_output=True)
        for ln in p.stdout.decode("utf-8", "replace").split("\n"):
            if not ln:
                continue
            r = ln.split("\t")
            try:
                t += int(r[1])
            except (ValueError, IndexError):
                continue
            if r[0] not in ("b", "t") or len(r) < 5:
                continue
            ms = t // 1_000_000
            if not lo <= ms <= hi:
                continue
            try:
                lag[(r[0], "top" if r[4] in top else "rest")].append(float(r[2]))
            except ValueError:
                pass
    span = a.to_s - a.from_s
    out = {}
    for (k, g), xs in lag.items():
        out[f"{'book' if k == 'b' else 'trade'}_{g}"] = {
            "records_s": round(len(xs) / span, 1), "lag_p50_ms": pct(xs, 0.5), "lag_p99_ms": pct(xs, 0.99), "lag_max_ms": max(xs) if xs else None,
            "share_over_1s": round(sum(1 for x in xs if x > 1000) / len(xs), 5) if xs else None}
    print(json.dumps(out, ensure_ascii=False))
    if a.out:
        json.dump(out, open(a.out, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
