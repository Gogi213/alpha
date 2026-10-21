#!/usr/bin/env python3
"""TK-345 прогон валидности: инварианты по выходу tk345-validity-day.sh (<O>/A/out/<набор>/{rounds,forms}.csv, <O>/B — повтор).
  tk345-validity-check.py <O>
(1) детерминизм A=B (md5 rounds/forms по наборам); (2) фильтр режет: n_signals набора ⊆ родителя по (символ, форма), n_signals+n_skipped
одно на все наборы; (3) стоп: доля стопов с удержанием ≤ 1 с от t0 по семействам форм, стоп с выгодным знаком; (4) PnL: gross из entry/exit
минус net_bps по (вход, причина) — min/медиана/max остатка (издержки). Ни одного порога не вводится: печатаются числа и нарушения вложенности."""
import csv, glob, hashlib, os, statistics, sys, collections

O = sys.argv[1]
NOB = "--no-b" in sys.argv   # повтор B не подавался: детерминизм проверяется отдельной пробой
PARENT = {"age1800": "base", "age7200": "age1800", "usd30k": "base", "flow100": "base", "fr": "base", "btcup": "base", "btcdn": "base",
          "ret1hup": "base", "ret1hdn": "base", "age1800usd30k": "age1800"}
EXTRA_PARENT = {"age1800usd30k": "usd30k"}


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def rd(p):
    with open(p, newline="", encoding="utf-8") as fh:
        rows = [ln for ln in fh if not ln.startswith("#")]
    return list(csv.DictReader(rows))


sets = sorted(os.path.basename(p) for p in glob.glob(f"{O}/A/out/*") if os.path.isdir(p))
print(f"наборы: {len(sets)}: {' '.join(sets)}")
bad = 0

print("\n== (1) детерминизм A=B" + (" — пропущено (--no-b)" if NOB else ""))
for s in ([] if NOB else sets):
    for f in ("rounds.csv", "forms.csv"):
        a, b = f"{O}/A/out/{s}/{f}", f"{O}/B/out/{s}/{f}"
        ok = os.path.exists(a) and os.path.exists(b) and md5(a) == md5(b)
        if not ok:
            bad += 1
        print(f"  {s}/{f}: {'ок' if ok else 'КРАСНОЕ'}")

print("\n== (2) фильтр режет")
forms = {}
for s in sets:
    forms[s] = {(r["symbol"], r["form"]): r for r in rd(f"{O}/A/out/{s}/forms.csv")}
nf = {s: len(forms[s]) for s in sets}
print("  строк forms:", nf)
tot = collections.defaultdict(set)
for s in sets:
    for k, r in forms[s].items():
        tot[k].add(int(r["n_signals"]) + int(r["n_skipped"]))
v = sum(1 for k, t in tot.items() if len(t) > 1)
bad += v
print(f"  n_signals+n_skipped одно на все наборы: нарушений {v} из {len(tot)} (символ, форма)")
for s, p in PARENT.items():
    for pp in (p, EXTRA_PARENT.get(s)):
        if s not in forms or not pp or pp not in forms:
            continue
        viol = [(k, int(forms[s][k]["n_signals"]), int(forms[pp][k]["n_signals"])) for k in forms[s] if k in forms[pp]
                and int(forms[s][k]["n_signals"]) > int(forms[pp][k]["n_signals"])]
        sn = sum(int(r["n_signals"]) for r in forms[s].values()); pn = sum(int(r["n_signals"]) for r in forms[pp].values())
        bad += len(viol)
        print(f"  {s} ⊆ {pp}: нарушений {len(viol)} из {len(forms[s])}; Σn {sn} ≤ {pn}"
              + (f"; пример {viol[0]}" if viol else ""))
if "btcup" in forms and "btcdn" in forms and "base" in forms:
    su = sum(int(r["n_signals"]) for r in forms["btcup"].values()); sd = sum(int(r["n_signals"]) for r in forms["btcdn"].values())
    sb = sum(int(r["n_signals"]) for r in forms["base"].values())
    print(f"  btcup+btcdn = {su}+{sd} = {su + sd} против base {sb} (равенство в 0 считается дважды; нет значения режима — в n_skipped)")

print("\n== (3) стоп и тейк: удержание от t0 (≤ 1 с) и знак")
fam = collections.defaultdict(lambda: [0, 0, 0, 0])   # n кругов, стопов, стопов ≤1 с, стопов с выгодным знаком
res = collections.defaultdict(list)                  # (вход, причина) -> остаток издержек


def sign(d):
    try:
        return 1.0 if float(d) > 0 else -1.0
    except ValueError:
        return 1.0 if d.lower().startswith("l") or d == "buy" else -1.0


for s in sets:
    for r in rd(f"{O}/A/out/{s}/rounds.csv"):
        parts = r["form"].split("-")
        entry = parts[0] if parts[0] in ("market",) or parts[0].startswith("ladder") else "single"
        stop = parts[1] if entry != "single" else parts[0]
        key = f"{s}:{stop}"
        g = fam[key]
        g[0] += 1
        try:
            hold = (int(r["exit_ns"]) - int(r["t0_ns"])) / 1e9
            sg = sign(r["dir"]); ep = float(r["entry_vwap"] or r["entry_px"]); xp = float(r["exit_px"])
            gross = sg * (xp / ep - 1) * 1e4
            res[(entry, r["reason"])].append(gross - float(r["net_bps"]))
        except (ValueError, ZeroDivisionError):
            continue
        if r["reason"] == "stop":
            g[1] += 1
            if hold <= 1:
                g[2] += 1
            if gross > 0:
                g[3] += 1
agg = collections.defaultdict(lambda: [0, 0, 0, 0])
for k, g in fam.items():
    st = k.split(":", 1)[1]
    for i in range(4):
        agg[st][i] += g[i]
for st, g in sorted(agg.items()):
    sh = f"{100 * g[2] / g[1]:.1f}%" if g[1] else "—"
    print(f"  стоп {st}: кругов {g[0]}, стопов {g[1]}, ≤1 с {g[2]} ({sh}), стоп с gross>0 {g[3]}")

print("\n== (4) PnL: gross(entry,exit) − net_bps = издержки по (вход, причина): n, min, медиана, max")
for (e, why), xs in sorted(res.items()):
    print(f"  {e:7s} {why:18s} n={len(xs):6d} min={min(xs):8.3f} med={statistics.median(xs):8.3f} max={max(xs):8.3f}")
print(f"\nнарушений автопроверки (1)(2): {bad}")
sys.exit(1 if bad else 0)
