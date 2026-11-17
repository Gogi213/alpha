"""Остаток arb-holding против известных сбоев сбора и сети (TK-534 v6, вопрос координатора: 71 % остатка — 16.11).

Остаток = эпизоды «от нуля», θ = 0,5 %, ≥ 5 с, прошедшие все слои (keep — прочтение «сырьё», keep_p — прочтение «цена»), строгий вариант.
Что считает (всё по эпизодам flag-strict.pkl):
  1. по суткам GMT+4 (как в таблицах отчёта) и по суткам UTC: остаток, из него — пересекающий сбой (запас 0 и ±120 с, как слой gap) и вне сбоев;
  2. по часам UTC за 16.11 GMT+4 (15.11 20:00 UTC … 17.11 00:00 UTC): остаток, доля Gate, секунды сбоев в часе;
  3. концентрация: пары / монеты в остатке по суткам, доля трёх крупнейших монет;
  4. минуты «сбор у биржи деградировал» из series-minutes.jsonl (gaps > 0: хотя бы один шард встал ≥ 3 с): доля остатка и всех эпизодов,
     задетых такой минутой ± 60 с, против контролей со сдвигом по времени (±3 / ±6 / ±12 ч);
  5. разрыв «сырьё / цена»: эпизоды, прошедшие все слои кроме stale; сколько снимает каждое прочтение; по какой бирже нога с неменяющейся ценой.
Запуск: python3 hold_incident.py <flag-strict.pkl> <incidents.txt> <каталог series-minutes: <биржа>.jsonl> > incident.md
"""
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from hold_common import EX5, TZ_MS  # noqa: E402

TH, VIEW, MIN_DUR = 50.0, "z", 5_000
MARGINS = (0, 120_000)
SHIFTS_H = (-12, -6, -3, 3, 6, 12)
OTHER = ("gap", "contract", "tradfi", "delist", "funding", "mark", "trend")


def ms(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def load_incidents(path):
    out = []
    for ln in open(path, encoding="utf-8"):
        if not ln.strip() or ln.startswith("#"):
            continue
        a, b, kind, exs, label = ln.split(None, 4)
        bad = int(label.split("=")[1]) if label.startswith("bad=") else None
        out.append({"a": ms(a), "b": ms(b), "kind": kind, "ex": None if exs == "*" else set(exs.split(",")), "label": label.strip(), "bad": bad})
    return out


def touches(t, t_end, exA, exB, inc, margin):
    """Эпизод [t, t_end] ± margin пересекает хотя бы один из сбоев inc, действующий на биржу A или B (ex=None — на все)."""
    hit = np.zeros(len(t), bool)
    for i in inc:
        on = np.ones(len(t), bool) if i["ex"] is None else np.isin(exA, list(i["ex"])) | np.isin(exB, list(i["ex"]))
        hit |= on & (t - margin < i["b"]) & (t_end + margin > i["a"])
    return hit


def load_deg(d):
    """series-minutes.jsonl -> {биржа: отсортированные индексы минут с gaps > 0} и покрытые минуты."""
    deg, cov = {}, {}
    for ex in EX5:
        rows = [r for r in (json.loads(x) for x in open(Path(d, f"{ex}.jsonl"), encoding="utf-8") if x.strip()) if "t" in r]
        m = np.array([r["t"] // 60 for r in rows], np.int64)
        g = np.array([r.get("gaps", 0) for r in rows])
        deg[ex] = np.unique(m[g > 0])
        cov[ex] = np.unique(m)
    return deg, cov


def load_starts(d, lo="2026-11-15T12:00:00Z", hi="2026-11-17T06:00:00Z"):
    """строки start series-minutes (запуск сборщика): {биржа: [(unix с, версия сборки)]} в окне [lo, hi]."""
    out = {}
    for ex in EX5:
        rows = [json.loads(x) for x in open(Path(d, f"{ex}.jsonl"), encoding="utf-8") if x.startswith('{"start"')]
        out[ex] = [(r["start"], r["version"]) for r in rows if ms(lo) // 1000 <= r["start"] <= ms(hi) // 1000]
    return out


def deg_touch(t, t_end, exA, exB, deg, pad=60_000):
    """Есть ли у любой из двух бирж минута с паузой шарда в [t − pad, t_end + pad]."""
    hit = np.zeros(len(t), bool)
    lo, hi = (t - pad) // 60_000, (t_end + pad) // 60_000
    for ex in EX5:
        on = (exA == ex) | (exB == ex)
        a = deg[ex]
        j = np.searchsorted(a, lo, "left")
        hit |= on & (j < len(a)) & (a[np.minimum(j, max(len(a) - 1, 0))] <= hi)
    return hit


def fmt(x):
    return f"{x:,}".replace(",", " ")


def pct(a, b):
    return f"{100 * a / b:.1f}" if b else "—"


def main(flagp, incp, serd):
    tab = pickle.load(open(flagp, "rb"))
    df = tab["df"]
    df = df[(df["th"] == TH) & (df["view"] == VIEW) & (df["dur"] >= MIN_DUR)].reset_index(drop=True)
    inc = load_incidents(incp)
    starts = load_starts(serd)
    inc_big = [i for i in inc if i["kind"] != "net" or i["bad"] >= 15]
    t = df["t"].to_numpy().astype(np.int64)
    te = t + df["dur"].to_numpy().astype(np.int64)
    exA, exB = df["exA"].to_numpy(), df["exB"].to_numpy()
    df["dayG"] = pd.to_datetime(t + TZ_MS, unit="ms").strftime("%d.%m")
    df["dayU"] = pd.to_datetime(t, unit="ms").strftime("%d.%m")
    df["hourU"] = pd.to_datetime(t, unit="ms").strftime("%d.%m %H")
    for name, sel in (("big", inc_big), ("all", inc)):
        for m in MARGINS:
            df[f"i_{name}_{m}"] = touches(t, te, exA, exB, sel, m)
    print(f"Эпизоды «от нуля», θ = 0,5 %, ≥ 5 с, строгий вариант (в окне до слоёв): {fmt(len(df))}; остаток по сырью {fmt(int(df['keep'].sum()))}, по цене {fmt(int(df['keep_p'].sum()))}.")
    print(f"Сбои (`arb-holding-incidents-2026-11-17.txt`): всего {len(inc)} (сеть ≥ 10 плохих с, остановки Binance, пауза Gate, обрывы); «большие» (сеть ≥ 15 плохих с + остановки + обрывы): {len(inc_big)}.\n")

    for dcol, title in (("dayG", "сутки GMT+4 (как в таблицах отчёта)"), ("dayU", "сутки UTC")):
        for rd, kcol in (("сырьё", "keep"), ("цена", "keep_p")):
            print(f"### Остаток по сбоям — {title}, прочтение «{rd}»\n")
            print("| сутки | эпизодов до слоёв | остаток | из него пересекает большой сбой (запас 0 / ±120 с) | вне больших сбоев (±120 с) | доля вне, % | пересекает любой сбой из списка (±120 с) | вне любых, % |")
            print("|---|---|---|---|---|---|---|---|")
            for day, g in df.groupby(dcol, sort=False):
                k = g[g[kcol]]
                if not len(g):
                    continue
                b0, b1, a1 = int(k["i_big_0"].sum()), int(k["i_big_120000"].sum()), int(k["i_all_120000"].sum())
                print(f"| {day} | {fmt(len(g))} | {fmt(len(k))} | {b0} / {b1} | {fmt(len(k) - b1)} | {pct(len(k) - b1, len(k))} | {a1} | {pct(len(k) - a1, len(k))} |")
            print()
    print("### Остаток (сырьё) по часам UTC за сутки 16.11 GMT+4 (15.11 20:00 … 16.11 20:00 UTC) и 17.11 GMT+4 (с 20:00 UTC)\n")
    print("| час UTC | эпизодов до слоёв | остаток | доля Gate в ногах, % | в остатке: пересекает большой сбой ±120 с | секунд сбоев (большие) в часе |")
    print("|---|---|---|---|---|---|")
    k = df[df["keep"]]
    lo_h = ms("2026-11-15T20:00:00Z") // 3_600_000
    hi_h = ms("2026-11-17T00:00:00Z") // 3_600_000
    for h in range(lo_h, hi_h):
        a, b = h * 3_600_000, (h + 1) * 3_600_000
        g = df[(df["t"] >= a) & (df["t"] < b)]
        kk = k[(k["t"] >= a) & (k["t"] < b)]
        secs = 0
        spans = sorted((max(i["a"], a), min(i["b"], b)) for i in inc_big if i["b"] > a and i["a"] < b)
        cur = a
        for s0, s1 in spans:
            s0 = max(s0, cur)
            if s1 > s0:
                secs += (s1 - s0) // 1000
                cur = s1
        gate = ((kk["exA"] == "gate") | (kk["exB"] == "gate")).sum()
        print(f"| {datetime.fromtimestamp(a / 1000, timezone.utc):%d.%m %H} | {fmt(len(g))} | {fmt(len(kk))} | {pct(gate, len(kk))} | {int(kk['i_big_120000'].sum())} | {secs} |")
    print()

    print("### Начало всплеска 16.11: часы UTC 15.11 18:00 … 16.11 06:00\n")
    print("Эпизоды до слоёв: монет и пар с эпизодами в часе; остаток (сырьё); снятие stale. Смена сборки сборщика (строки `start` в series-minutes, UTC): " + "; ".join(
        f"{ex} {', '.join(f'{datetime.fromtimestamp(x[0], timezone.utc):%d.%m %H:%M} {x[1]}' for x in starts[ex])}" for ex in EX5) + ".\n")
    print("| час UTC | эпизодов | монет | пар | остаток (сырьё) | снято stale |")
    print("|---|---|---|---|---|---|")
    hh = pd.to_datetime(t, unit="ms").floor("h")
    for h0 in pd.date_range("2026-11-15 18:00", "2026-11-16 06:00", freq="h"):
        g = df[hh == h0]
        print(f"| {h0:%d.%m %H} | {fmt(len(g))} | {g['coin'].nunique()} | {g.groupby(['coin', 'exA', 'exB']).ngroups} | {fmt(int(g['keep'].sum()))} | {fmt(int((g['layer'] == 'stale').sum()))} |")
    print()
    print("### Концентрация остатка (сырьё)\n")
    print("| сутки GMT+4 | остаток | пар | монет | три крупнейшие монеты, % остатка | доля пар с Gate, % | с BingX, % |")
    print("|---|---|---|---|---|---|---|")
    for day, g in k.groupby("dayG", sort=False):
        top = g["coin"].value_counts()
        print(f"| {day} | {fmt(len(g))} | {g.groupby(['coin', 'exA', 'exB']).ngroups} | {g['coin'].nunique()} | {pct(int(top.head(3).sum()), len(g))} | "
              f"{pct(int(((g['exA'] == 'gate') | (g['exB'] == 'gate')).sum()), len(g))} | {pct(int(((g['exA'] == 'bingx') | (g['exB'] == 'bingx')).sum()), len(g))} |")
    print()
    g16 = k[k["dayG"] == "16.11"]
    print("Топ-8 монет остатка 16.11 GMT+4 (эпизодов): " + ", ".join(f"{c} {n}" for c, n in g16["coin"].value_counts().head(8).items()) + "\n")

    deg, cov = load_deg(serd)
    print("### Минуты «сбор деградировал» (series-minutes, gaps > 0: хоть один шард встал ≥ 3 с), задетые эпизоды ± 60 с\n")
    print("Доля эпизодов, у которых хоть у одной биржи ног есть такая минута в [начало − 60 с; конец + 60 с]; контроль — те же эпизоды, сдвинутые по времени на ±3 / ±6 / ±12 ч (среднее по сдвигам; сдвинутый эпизод берёт ту же пару бирж).\n")
    print("| группа | эпизодов | задето, % | контроль со сдвигом, % | отношение |")
    print("|---|---|---|---|---|")
    d16 = (df["dayG"] == "16.11").to_numpy()
    for name, mask in (("остаток (сырьё), 16.11", df["keep"] & d16), ("остаток (сырьё), другие сутки", df["keep"] & ~d16 & (df["dayG"] != "17.11")),
                       ("остаток (сырьё), 17.11", df["keep"] & (df["dayG"] == "17.11")), ("все эпизоды до слоёв, 16.11", d16),
                       ("все эпизоды до слоёв, другие сутки", ~d16 & (df["dayG"] != "17.11"))):
        mk = np.asarray(mask)
        if not mk.any():
            continue
        h0 = deg_touch(t[mk], te[mk], exA[mk], exB[mk], deg).mean() * 100
        ctl = np.mean([deg_touch(t[mk] + s * 3_600_000, te[mk] + s * 3_600_000, exA[mk], exB[mk], deg).mean() * 100 for s in SHIFTS_H])
        print(f"| {name} | {fmt(int(mk.sum()))} | {h0:.1f} | {ctl:.1f} | {h0 / ctl:.2f} |" if ctl else f"| {name} | {fmt(int(mk.sum()))} | {h0:.1f} | 0 | — |")
    print()

    # разрыв «сырьё / цена»
    oth = np.zeros(len(df), bool)
    for f in OTHER:
        oth |= df[f"f_{f}"].to_numpy()
    n0 = ~oth
    sr, sp = df["f_stale"].to_numpy(), df["f_stale_price"].to_numpy()
    print("### Разрыв прочтений «сырьё» и «цена»\n")
    print("| что | эпизодов |")
    print("|---|---|")
    print(f"| прошли все слои, кроме stale (gap, contract, tradfi, delist, funding, mark, trend) | {fmt(int(n0.sum()))} |")
    print(f"| из них нога молчала > 5 с по сырью (снимает «сырьё») | {fmt(int((n0 & sr).sum()))} |")
    print(f"| из них лучшая цена ноги не менялась > 5 с (снимает «цена») | {fmt(int((n0 & sp).sum()))} |")
    print(f"| снимает только «цена» (записи ноги идут, цена стоит) | {fmt(int((n0 & sp & ~sr).sum()))} |")
    print(f"| снимает только «сырьё» (молчание без цены > 5 с: цена до молчания была свежей) | {fmt(int((n0 & sr & ~sp).sum()))} |")
    print(f"| остаток «сырьё» / «цена» | {fmt(int((n0 & ~sr).sum()))} / {fmt(int((n0 & ~sp).sum()))} |\n")
    only_p = df[n0 & sp & ~sr]
    ageA, ageB = only_p["ageA"].to_numpy(), only_p["ageB"].to_numpy()
    legA, legB = ageA > 5_000, ageB > 5_000
    print("Эпизоды, которые снимает только «цена»: нога с неменяющейся ценой (биржа; эпизод считается по каждой такой ноге).\n")
    print("| биржа ноги | эпизодов | доля, % |")
    print("|---|---|---|")
    cnt = {}
    for ex in EX5:
        cnt[ex] = int(((only_p["exA"] == ex) & legA).sum() + ((only_p["exB"] == ex) & legB).sum())
    tot = sum(cnt.values())
    for ex in EX5:
        print(f"| {ex} | {fmt(cnt[ex])} | {pct(cnt[ex], tot)} |")
    age = np.maximum(np.where(legA, ageA, 0), np.where(legB, ageB, 0)) / 1000
    print(f"\nВозраст неменяющейся лучшей цены на начало эпизода (с), только-«цена»: медиана {np.median(age):.1f}; квартили {np.percentile(age, 25):.1f}…{np.percentile(age, 75):.1f}; 90-й процентиль {np.percentile(age, 90):.1f}; доля > 60 с {100 * (age > 60).mean():.1f} %.")
    print("По суткам GMT+4 (только-«цена» / остаток «сырьё»): " + "; ".join(f"{d}: {int((only_p['dayG'] == d).sum())} / {int(df[df['keep'] & (df['dayG'] == d)].shape[0])}" for d in df["dayG"].unique()) + "\n")
    ks = df[df["keep"]]
    st = int(((ks["ageA"] > 5_000) | (ks["ageB"] > 5_000)).sum())
    print(f"Остаток «сырьё» {fmt(len(ks))}: у {fmt(st)} ({pct(st, len(ks))} %) цена хотя бы одной ноги на старте не менялась > 5 с, остальные {fmt(len(ks) - st)} = остаток «цена» ({fmt(int(df['keep_p'].sum()))}).")


if __name__ == "__main__":
    main(*sys.argv[1:4])
