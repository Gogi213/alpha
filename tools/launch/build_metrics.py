#!/usr/bin/env python3
"""TK-244, В-9: метрики по хешам Летописи из поштучных closes (cap 0) -> <registry>/metrics.jsonl. Запускать на calc заданием alsched
(--kind gate --cores 1), не из браузера и не из веб-процесса. Определения — metrics.py.
  build_metrics.py --out metrics.jsonl [--routes routes.json] [--months 2026-01,…] ИСТОЧНИК…
ИСТОЧНИК = <каталог>/<шаблон с {m}> (месяц ГГГГ-ММ), по порядку приоритета; вариант хеша = «состояние@форма» из routes.json
(lines / monthly, как postload.build). Хеш попадает в файл, только если вариант найден в источнике хотя бы за один месяц; на странице
метрики показываются, когда найденные месяцы = месяцы с $ в Летописи и Σ$ closes сходится с pnl_usd канона (letopis(), launch.py).
TK-258: --direct <шаблон с {mon}> (jan…sep, повторять) — closes R1 (psim p12-r1-psim.py): ключ файла = хеш клетки, B1 пропускается, месяц файла без
хеша = 0 сделок (psim пишет только варианты со сделками; load_r1.py пишет такой месяц нулём) — по всем 9 месяцам; хеш уже взят из ИСТОЧНИКОВ — не трогается.
TK-268: --alias-cells <cells.jsonl> — ключ файла = имя клетки (R1 TK-182: g04-k20), а не хеш: имя -> id по cells.jsonl (set_name p12-…, форма
однозначна); без флага поведение прежнее. В --direct шаблоне допустим и {m} (ГГГГ-ММ)."""
import argparse
import glob
import json
import os
import sys

MON = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metrics  # noqa: E402


def variant_key(line):
    form, _, st = line.partition(" ")
    return f"{st}@{form}"


def cell_alias(path):
    """TK-268: имя клетки (form) -> хеш (id) для closes с ключом-именем; неоднозначное имя пропускается."""
    ids = {}
    with open(path, encoding="utf-8") as f:
        for line in filter(str.strip, f):
            d = json.loads(line)
            if (d.get("set_name") or "").startswith("p12-"):
                ids.setdefault(d["form"], set()).add(d["id"])
    return {k: next(iter(v)) for k, v in ids.items() if len(v) == 1}


def direct_closes(patterns, months, alias=None):
    """--direct: [(шаблон с {mon})] -> ({хеш: {месяц: [[мс,$,…]…]}}, {хеш: {каталоги}}); хеш = ключ файла, cap 0, месяц без хеша в найденном файле = []."""
    got, src_of = {}, {}
    for pat in patterns:
        d, _, name = pat.rpartition("/")
        seen = {}   # хеш -> {месяц: сделки}
        found = []
        for m in months:
            files = sorted(glob.glob(os.path.join(d, name.replace("{mon}", MON[int(m[5:]) - 1]).replace("{m}", m))))
            if not files:
                continue
            found.append(m)
            for h, per in json.load(open(files[0], encoding="utf-8")).items():
                if h != "B1":
                    seen.setdefault((alias or {}).get(h, h), {})[m] = per.get(m, {}).get("0", [])
        for h, mm in seen.items():
            if h not in got:
                got[h] = {m: mm.get(m, []) for m in found}
                src_of.setdefault(h, set()).add(d)
    return got, src_of


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*")
    ap.add_argument("--direct", action="append", default=[])
    ap.add_argument("--alias-cells", default=None)
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
    dgot, dsrc = direct_closes(a.direct, months, cell_alias(a.alias_cells) if a.alias_cells else None)
    for h, mm in dgot.items():
        if h not in got:
            got[h] = mm
            src_of[h] = dsrc[h]
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
