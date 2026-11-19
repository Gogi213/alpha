#!/usr/bin/env python3
"""TK-626 / П-42 §13.28.1 п. 2 и 5: подготовка данных точки вперёд — вселенная `U_k = B ∪ N_k` и деревья окон. Без P&L, движок не запускается.
Оркестрация и сеть — `tk626_prep.sh` (листинг S3 `tk621_list.py`, скачивание `tk621_dl.py`, REST-добор `tk621_restfill.py`, `breakout convert`); здесь — чистая логика (тестируется на синтетике):

  python3 tk626_prep.py universe --list-dir L --base-dir B --d 2026-11-30 --out U [--prev U_prev]   # symbols-{all,crypto,tradfi}.txt, contracts.csv, L/missing.tsv, L/gaps.tsv
  python3 tk626_prep.py check --out P --universe U --d 2026-11-30 [--windows w1]                                                   # ворота подготовки: каталоги у каждого контракта, нет суток позже D
  python3 tk626_prep.py tree --out P --universe U --d 2026-11-30 --windows w1,w2 --freeze F [--dl DL] [--rest RF]                   # P/<окно>/{aggtrades,klines}: ссылки на опубликованные файлы

Прирост `N_k` — USDT-перпетуалы с опубликованными aggTrades хотя бы в одни сутки 14.11…D, которых нет в базе B (775, TK-621) и у которых нет суток 01.07–13.11; класс — `contractType == TRADIFI_PERPETUAL`
по exchangeInfo в момент первого появления (дальше — из `contracts.csv` предыдущей точки; смена класса — остановка), нет в exchangeInfo — крипто. Порядок монет: база как в базовом списке, затем прирост по (первые сутки, символ),
поэтому список точки k — префикс списка точки k + 1. Класс базовых контрактов — из базовых списков, не из exchangeInfo."""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import tk616_forward as fw  # noqa: E402

UNI = fw.UNI_NAMES
FIRST_BASE, LAST_BASE = "2026-07-01", fw.LISTING_AFTER                     # окно членства базы 01.07–13.11
FIRST_FWD = (fw.d(fw.LISTING_AFTER) + datetime.timedelta(days=1)).isoformat()        # 14.11
TREES_OLD = ("/data/p42-tk621/wc", "/data/p42-tk621/wb", "/data/p42-tk621/wa")      # деревья TK-621 (≤ 13.11), первый найденный
GuardError = rf.GuardError


def read_symbols(path):
    return [x for x in open(path, encoding="utf-8").read().replace("\n", ",").split(",") if x]


def read_listing(list_dir):
    """`contracts.json` и `files.tsv` листинга `tk621_list.py` → (контракты, {(вид, символ): {сутки: (размер, etag)}})."""
    contracts = json.load(open(os.path.join(list_dir, "contracts.json"), encoding="utf-8"))["contracts"]
    files = {}
    for line in open(os.path.join(list_dir, "files.tsv"), encoding="utf-8"):
        kind, sym, day, size, etag = line.rstrip("\n").split("\t")
        files.setdefault((kind, sym), {})[day] = (int(size), etag)
    return contracts, files


def read_prev_classes(prev_dir):
    """{символ: tradfi 0/1} из `contracts.csv` предыдущей точки (класс записан при первом появлении и не пересматривается)."""
    if not prev_dir:
        return {}
    return {r["symbol"]: int(r["tradfi"]) for r in csv.DictReader(open(os.path.join(prev_dir, "contracts.csv"), encoding="utf-8"))}


def build_universe(contracts, files, base, d, prev_classes=None):
    """base — {имя: [монеты]} (all / crypto / tradfi, как в базовых файлах); d — последняя дата данных точки. Возвращает ({имя: [монеты]}, [строки contracts.csv])."""
    prev_classes = prev_classes or {}
    base_all = set(base["all"])
    agg = {s: sorted(x for x in days if FIRST_BASE <= x <= d) for (k, s), days in files.items() if k == "aggTrades"}
    new = sorted((days[0], s) for s, days in agg.items() if s not in base_all and days and days[-1] >= FIRST_FWD)
    rows, lists = [], {n: list(base[n]) for n in UNI}
    for s in base["all"]:
        days = agg.get(s, [])
        rows.append({"symbol": s, "in_base": 1, "tradfi": int(s in set(base["tradfi"])), "days_0107_1311": sum(1 for x in days if x <= LAST_BASE),
                     "first_day": days[0] if days else "", "last_day": days[-1] if days else "", "contract_type": (contracts.get(s) or {}).get("contract_type") or "",
                     "status": (contracts.get(s) or {}).get("status") or ""})
    for first, s in new:
        c = contracts.get(s) or {}
        tf = int(c.get("contract_type") == "TRADIFI_PERPETUAL")
        if s in prev_classes and prev_classes[s] != tf:
            raise GuardError("прирост %s: класс в предыдущей точке %s, по exchangeInfo сейчас %s — остановка, сообщение координатору" % (s, prev_classes[s], tf))
        tf = prev_classes.get(s, tf)
        days = agg[s]
        if any(x <= LAST_BASE for x in days):
            raise GuardError("прирост %s: есть сутки до 13.11 (%s) — должен был быть в базе" % (s, [x for x in days if x <= LAST_BASE][:3]))
        lists["all"].append(s)
        lists["tradfi" if tf else "crypto"].append(s)
        rows.append({"symbol": s, "in_base": 0, "tradfi": tf, "days_0107_1311": 0, "first_day": days[0], "last_day": days[-1], "contract_type": c.get("contract_type") or "",
                     "status": c.get("status") or ""})
    return lists, rows


def write_universe(out, lists, rows):
    os.makedirs(out, exist_ok=True)
    for n in UNI:
        with open(os.path.join(out, "symbols-%s.txt" % n), "w", encoding="utf-8", newline="\n") as f:
            f.write(",".join(lists[n]) + "\n")
    with open(os.path.join(out, "contracts.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def missing_rows(files, syms, d):
    """Файлы листинга контрактов вселенной за 14.11…D (то, что скачивается вперёд): (вид, символ, сутки, размер, etag)."""
    out = []
    for s in syms:
        for kind in ("aggTrades", "klines"):
            for day, (size, etag) in sorted(files.get((kind, s), {}).items()):
                if FIRST_FWD <= day <= d:
                    out.append((kind, s, day, size, etag))
    return out


def gap_rows(files, syms, d):
    """Провалы внутри листинга (сутки между первым и последним опубликованным в 14.11…D; хвост после последнего — не провал: делистинг или ещё не опубликовано, печатается отдельно)."""
    gaps, tail = [], []
    for s in syms:
        for kind in ("aggTrades", "klines"):
            days = sorted(x for x in files.get((kind, s), {}) if FIRST_FWD <= x <= d)
            if not days:
                continue
            a, b = fw.d(days[0]), fw.d(days[-1])
            have = set(days)
            gaps += [(kind, s, (a + datetime.timedelta(i)).isoformat()) for i in range((b - a).days + 1) if (a + datetime.timedelta(i)).isoformat() not in have]
            if days[-1] < d and fw.d(d) - b <= datetime.timedelta(days=3):
                tail.append((kind, s, days[-1]))
    return gaps, tail


def day_list(t0, last):
    a = fw.d(t0)
    b = min(last, a + datetime.timedelta(days=rf.TRADE_END))
    return [(a + datetime.timedelta(i)).isoformat() for i in range((b - a).days + 1)]


def first_existing(paths):
    for p in paths:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def build_tree(wdir, syms, days, kl_src, tr_src, fund_src=()):
    """`<wdir>/{klines,aggtrades}/<символ>/…` — ссылки на опубликованные файлы (первый найденный источник; нет файла — нет ссылки). Шаблоны источников: `{s}`, `{d}`. Каталог — у каждого контракта.
    Возвращает ({вид: файлов}, [(вид, символ, сутки) без файла])."""
    cnt, miss = {"klines": 0, "aggtrades": 0, "funding": 0}, []
    for s in syms:
        os.makedirs(os.path.join(wdir, "klines", s), exist_ok=True)
        os.makedirs(os.path.join(wdir, "aggtrades", s), exist_ok=True)
        for day in days:
            p = first_existing([t.format(s=s, d=day) for t in kl_src])
            if p:
                os.symlink(os.path.realpath(p), os.path.join(wdir, "klines", s, day + ".csv"))
                cnt["klines"] += 1
            else:
                miss.append(("klines", s, day))
            p = first_existing([t.format(s=s, d=day) for t in tr_src])
            if p:
                os.symlink(os.path.realpath(p), os.path.join(wdir, "aggtrades", s, "%s-aggTrades-%s.zip" % (s, day)))
                cnt["aggtrades"] += 1
            else:
                miss.append(("aggtrades", s, day))
        f = first_existing([t.format(s=s) for t in fund_src])
        if f:
            os.symlink(os.path.realpath(f), os.path.join(wdir, "klines", s, "funding.csv"))
            cnt["funding"] += 1
    return cnt, miss


def sources(dl, rest):
    kl = ["%s/klines/{s}/{d}.csv" % w for w in TREES_OLD] + ["%s/klines-csv/{s}/{d}.csv" % dl, "%s/klines-csv/{s}/{d}.csv" % rest]
    tr = ["%s/aggtrades/{s}/{s}-aggTrades-{d}.zip" % w for w in TREES_OLD] + ["%s/aggtrades/{s}/{s}-aggTrades-{d}.zip" % dl]
    fund = ["%s/klines/{s}/funding.csv" % w for w in TREES_OLD]
    return kl, tr, fund


def cmd_universe(a):
    contracts, files = read_listing(a.list_dir)
    base = {n: read_symbols(os.path.join(a.base_dir, "symbols-%s.txt" % n)) for n in UNI}
    lists, rows = build_universe(contracts, files, base, a.d, read_prev_classes(a.prev))
    write_universe(a.out, lists, rows)
    new = [r for r in rows if not r["in_base"]]
    miss = missing_rows(files, lists["all"], a.d)
    gaps, tail = gap_rows(files, lists["all"], a.d)
    with open(os.path.join(a.list_dir, "missing.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.writelines("\t".join(map(str, r)) + "\n" for r in miss)
    with open(os.path.join(a.list_dir, "gaps.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.writelines("\t".join(r) + "\n" for r in gaps)
    absent = sorted(set(base["all"]) - {s for (k, s), v in files.items() if k == "aggTrades" and any(FIRST_BASE <= x <= a.d for x in v)})
    print("вселенная до %s: all %d (база %d + прирост %d), crypto %d, tradfi %d; прирост TradFi %d" % (a.d, len(lists["all"]), len(base["all"]), len(new), len(lists["crypto"]), len(lists["tradfi"]),
                                                                                              sum(r["tradfi"] for r in new)))
    print("к скачиванию %d файлов; провалов внутри листинга %d; хвост без суток D (последние 3 суток, возможное запаздывание публикации) %d; базовых без суток в листинге %d %s" % (
        len(miss), len(gaps), len(tail), len(absent), absent[:5]))
    return 0


def cmd_tree(a):
    fz = fw.load_freeze(a.freeze)
    pt = next((p for p in fz["points"] if p["d"] == a.d), None)
    if (pt is None or a.windows) and not a.allow_test:
        raise GuardError("дата %s не совпадает с датой точки заморозки или заданы --windows: для проверки скрипта до точки — `--allow-test`" % a.d)
    wins = {w["name"]: w for w in fz["windows"]}
    names = a.windows.split(",") if a.windows else (pt or {}).get("windows", [])
    if not names:
        raise GuardError("не заданы окна (--windows)")
    syms = read_symbols(os.path.join(a.universe, "symbols-all.txt"))
    last = fw.d(a.d)
    kl, tr, fund = sources(a.dl, a.rest)
    for n in names:
        w = wins[n]
        wdir = os.path.join(a.out, n)
        if os.path.exists(wdir):
            raise GuardError("каталог окна %s уже есть: дерево не перестраивается поверх" % wdir)
        days = day_list(w["t0"], last)
        cnt, miss = build_tree(wdir, syms, days, kl, tr, fund)
        print("окно %s T0 %s: сутки %s…%s, монет %d, минуток %d, сделок %d, funding %d, нет файла %d" % (n, w["t0"], days[0], days[-1], len(syms), cnt["klines"], cnt["aggtrades"], cnt["funding"], len(miss)))
        with open(os.path.join(a.out, "nofile-%s.tsv" % n), "w", encoding="utf-8", newline="\n") as f:
            f.writelines("\t".join(r) + "\n" for r in miss)
    return 0


def cmd_check(a):
    """Ворота подготовки (§13.28.1 п. 5): у каждого контракта `all_k` есть каталог в `aggtrades/` и `klines/` каждого окна; файлов позже D и монет вне `all_k` нет (`check_tree_days`)."""
    fz = fw.load_freeze(a.freeze)
    syms = read_symbols(os.path.join(a.universe, "symbols-all.txt"))
    pt = next((p for p in fz["points"] if p["d"] == a.d), None)
    names = a.windows.split(",") if a.windows else (pt or {}).get("windows", [])
    wins = [w for w in fw.windows_of(fz) if w.name in names]
    if not wins or len(wins) != len(names):
        raise GuardError("окна %s не из заморозки" % names)
    for w in wins:
        for sub in ("aggtrades", "klines"):
            lack = [s for s in syms if not os.path.isdir(os.path.join(a.out, w.name, sub, s))]
            if lack:
                raise GuardError("окно %s: у %d контрактов нет каталога %s, например: %s" % (w.name, len(lack), sub, lack[:3]))
    n = fw.check_tree_days(a.out, wins, fw.d(a.d), allowed=set(syms))
    print("ворота подготовки: окна %s, контрактов %d, файлов с сутками %d, суток позже %s и монет вне all нет" % (names, len(syms), n, a.d))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    u = sp.add_parser("universe")
    u.add_argument("--list-dir", required=True)
    u.add_argument("--base-dir", default=fw.BASE_DIR)
    u.add_argument("--d", required=True)
    u.add_argument("--out", required=True)
    u.add_argument("--prev", default="")
    t = sp.add_parser("tree")
    t.add_argument("--out", required=True)
    t.add_argument("--universe", required=True)
    t.add_argument("--d", required=True)
    t.add_argument("--windows", default="")
    t.add_argument("--freeze", default=fw.FREEZE)
    t.add_argument("--dl", default="/data/p42-tk616/fwd/dl")
    t.add_argument("--rest", default="/data/p42-tk616/fwd/rest")
    t.add_argument("--allow-test", action="store_true")
    c = sp.add_parser("check")
    c.add_argument("--out", required=True)
    c.add_argument("--universe", required=True)
    c.add_argument("--d", required=True)
    c.add_argument("--windows", default="")
    c.add_argument("--freeze", default=fw.FREEZE)
    a = ap.parse_args(argv)
    try:
        return {"universe": cmd_universe, "tree": cmd_tree, "check": cmd_check}[a.cmd](a)
    except GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
