#!/usr/bin/env python3
"""TK-244, В-9: метрики по хешам Летописи из поштучных closes (cap 0) -> <registry>/metrics.jsonl. Запускать на calc заданием alsched
(--kind gate --cores 1), не из браузера и не из веб-процесса. Определения — metrics.py.
  build_metrics.py --out metrics.jsonl [--routes routes.json] [--months 2026-01,…] ИСТОЧНИК…
ИСТОЧНИК = <каталог>/<шаблон с {m}> (месяц ГГГГ-ММ), по порядку приоритета; вариант хеша = «состояние@форма» из routes.json
(lines / monthly, как postload.build). Хеш попадает в файл, только если вариант найден в источнике хотя бы за один месяц; на странице
метрики показываются, когда найденные месяцы = месяцы с $ в Летописи и Σ$ closes сходится с pnl_usd канона (letopis(), launch.py)."""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metrics  # noqa: E402


def variant_key(line):
    form, _, st = line.partition(" ")
    return f"{st}@{form}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--routes", default=os.path.join(HERE, "routes.json"))
    ap.add_argument("--months", default=None)
    a = ap.parse_args(argv)
    doc = json.load(open(a.routes, encoding="utf-8"))
    months = a.months.split(",") if a.months else json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))["months"]
    keys = {}   # хеш -> {месяц: вариант}
    for h, ln in doc["lines"].items():
        if ln:
            keys[h] = {m: variant_key(ln) for m in months}
    for h, mm in doc["monthly"].items():
        keys[h] = {m: variant_key(ln) for m, ln in mm.items() if ln}
    got, src_of = {}, {}   # хеш -> {месяц: [[мс,$,монета]…]}
    for m in months:
        for si, src in enumerate(a.sources):
            d, _, pat = src.rpartition("/")
            files = sorted(glob.glob(os.path.join(d, pat.replace("{m}", m))))
            if not files:
                continue
            closes = json.load(open(files[0], encoding="utf-8"))
            for h, ks in keys.items():
                k = ks.get(m)
                if k in closes and m not in got.get(h, {}):
                    got.setdefault(h, {})[m] = closes[k].get(m, {}).get("0", [])
                    src_of.setdefault(h, set()).add(d)
            del closes
    n = 0
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for h in sorted(got):
            mt = metrics.hash_metrics(got[h])
            mt["hash"] = h
            mt["src"] = sorted(os.path.basename(os.path.dirname(x.rstrip("/"))) + "/" + os.path.basename(x.rstrip("/")) for x in src_of[h])
            mt["by_month"] = {mo: {"n": v["n"], "pnl": v["pnl"]} for mo, v in mt["by_month"].items()}
            f.write(json.dumps(mt, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} хешей с closes из {len(keys)} с маршрутом; {a.out}")


if __name__ == "__main__":
    main()
