#!/usr/bin/env python3
"""TK-015, П-07 поправка 7: markdown-отчёт месяца (или полугодия) из json `p07-h1-read.py` — таблица для Судьи.

    python tools/compute/p07-h1-report.py docs/findings/p07-h1-jan-2026-09-28.json [gate.txt] > раздел.md
    python tools/compute/p07-h1-report.py --half docs/findings/p07-h1-half-2026-09-28.json > раздел.md
"""
import json
import sys

NAMES = [("main", "главный В-104 (вердикт)"), ("b-base", "база Г-85б σ-лестница (вердикт)"),
         ("b-k1", "Г-85б K ≤ 1 (описание)"), ("a-fr1", "Г-85а `fr+1` (описание)")]


def num(x, d=3):
    return "—" if x is None else f"{x:.{d}f}".replace(".", ",").replace("-", "−")


def usd(u):
    lo, hi = u["ci95"]
    sg = lambda x: ("+" if x is not None and round(x) > 0 else "") + num(x, 0)
    return f"{sg(u['est'])} [{sg(lo)}; {sg(hi)}]"


def month(path, gate=None):
    r = json.load(open(path, encoding="utf-8"))
    m = r["_meta"]
    out = [f"## {m['month']} — {m['days'][0]} … {m['days'][1]}", ""]
    out.append(f"- Пул: {m['pool_coins']} монет (вне пула: {m['drop']}); поздние (с первых своих суток): "
               + (", ".join(f"{k} с {v}" for k, v in sorted(m["pool_late"].items())) or "нет") + ".")
    if m.get("drop_no_candles"):
        out.append("- Рынка ещё не было (dc250dc, поправка 7 п. 7): "
                   + "; ".join(f"{k} — {v}" for k, v in sorted(m["drop_no_candles"].items())) + ".")
    out.append(f"- Холодный старт (без переноса): {', '.join(m['cold_start_days'])}.")
    if gate:
        g = open(gate, encoding="utf-8").read().splitlines()
        stop = [l for l in g if "СТОП" in l]
        out.append(f"- Ворота данных: {g[-1] if g else '—'}" + (f"; стопы: {'; '.join(stop)}" if stop else "") + ".")
    out += ["", "| форма | сделок | доля > 5 сут | искл. мин / макс | $ [95 %] | наиб. ожидание | §10 |",
            "|---|---|---|---|---|---|---|"]
    for k, name in NAMES:
        f = r[k]
        kp = f.get("kpi") or {}
        out.append(f"| {name} | {f['n_trades']} | {num(kp.get('frac_gt_5d'))} | {num(kp.get('excl_min'))} / "
                   f"{num(kp.get('excl_max'))} | {usd(f['usd'])} | {(kp.get('max_wait_text') or '—').replace('.', ',')} | "
                   f"{kp.get('state') or '—'} |")
    out += ["", "| пара (описание) | Δ доли | Δ$ по суткам [95 %] | Δ доли без суток: мин / макс |", "|---|---|---|---|"]
    for k, p in r["pairs"].items():
        ds = [x["d"] for x in p["d_frac_without_day"] if x["d"] is not None]
        out.append(f"| {k} | {num(p['d_frac'])} | {usd(p['d_usd'])} | "
                   f"{num(min(ds) if ds else None)} / {num(max(ds) if ds else None)} |")
    out += ["", *[f"- {n}" for n in m["notes"]]]
    return "\n".join(out)


def half(path):
    r = json.load(open(path, encoding="utf-8"))
    m = r["_meta"]
    out = ["## Полугодие", "", f"Прочитано: {', '.join(m['months_read'])}; стоп ворот: "
           f"{', '.join(m['months_gate_stop']) or 'нет'}.", "",
           "| форма | F | вердикт | $ за полугодие [95 %] | месяцев в минусе | состояния по месяцам |",
           "|---|---|---|---|---|---|"]
    for k, name in NAMES:
        f = r[k]
        st = ", ".join(f"{mo} {s}" for mo, s in f["states"].items())
        out.append(f"| {name} | {f.get('F', '—')} | {f.get('verdict', '—')} | {usd(f['usd'])} | "
                   f"{f['months_minus']} из {len(f['states'])} | {st} |")
    out += ["", *[f"- {n}" for n in m["notes"]]]
    return "\n".join(out)


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.stdout.reconfigure(encoding="utf-8")
    print(half(a[1]) if a[0] == "--half" else month(a[0], a[1] if len(a) > 1 else None))
