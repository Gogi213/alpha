"""Critic checks on the live arb state (copy of state.pkl + arb.json). Read-only; run on srv with nice 19."""
import json
import pickle
import shutil
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, "/opt/arb-tab/tools/arb_stats")
import arb_daemon  # noqa: E402
import arb_inc  # noqa: E402,F401
import arb_live  # noqa: E402
import core  # noqa: E402

OUT = "/tmp/arbreview"
shutil.copy("/data/arb-tab/live/arb.json", f"{OUT}/arb.json")
shutil.copy("/data/arb-tab/live/state.pkl", f"{OUT}/state.pkl")
shutil.copy("/data/arb-tab/live/turnover.json", f"{OUT}/turnover.json")
doc = json.load(open(f"{OUT}/arb.json"))
W = pickle.load(open(f"{OUT}/state.pkl", "rb"))
t1 = W.t_a
t0 = max(arb_live.START_MS, t1 - arb_live.WINDOW_MS)
print("arb.json t1", doc["t1"], "state t_a", t1, "same pass" if doc["t1"] == t1 else "DIFFERENT PASS")
rows = {(r["c"], r["a"], r["b"]): r for r in doc["rows"]}
P = lambda *a: print(*a, flush=True)  # noqa: E731

# 1. reproduce table numbers independently from closed/open episodes
mism = Counter()
allc, allo = [], []
for key, ps in W.pairs.items():
    r = rows.get(key)
    if r is None:
        continue
    cl = [e for e in ps.closed if e[0] >= t0]
    op = [(o[0], d, t1 - o[0], o[3], o[1], o[2]) for d, o in ps.open.items() if o is not None and o[0] >= t0]
    ts = np.array([e[0] for e in cl] + [o[0] for o in op])
    cnt = [int((ts >= t1 - h * 3600_000).sum()) for h in (1, 4, 6, 24)]
    if cnt != r["cnt"]:
        mism["cnt"] += 1
    pk = np.array([e[4] for e in cl] + [o[3] for o in op])
    if len(pk):
        mine = [round(float(np.median(pk)), 1), round(float(np.percentile(pk, 95)), 1), round(float(pk.max()), 1)]
        if r["pk"] is None or any(abs(a - b) > 0.051 for a, b in zip(mine, r["pk"])):
            mism["pk"] += 1
    if cl:
        net = np.array([e[5] for e in cl]) - 12.6
        mine = [round(float(np.median(net)), 1), round(float(np.percentile(net, 95)), 1)]
        if r["net"] is None or any(abs(a - b) > 0.051 for a, b in zip(mine, r["net"])):
            mism["net"] += 1
    for e in cl:
        allc.append((key, e))
    for o in op:
        allo.append((key, o, ps.sc, ps.bc))
P("rows in arb.json", len(rows), "pairs in state", len(W.pairs), "mismatches", dict(mism))

# 2. open (censored) episodes: how many, how old, effect on tau and net
P("\n== censoring")
P("closed eps in window", len(allc), "open eps in window", len(allo))
age_h = np.array([(t1 - o[0]) / 3600_000 for _, o, _, _ in allo])
if len(age_h):
    P("open age h: p50 %.2f p90 %.2f max %.2f; >1h %d >6h %d" % (np.median(age_h), np.percentile(age_h, 90), age_h.max(), (age_h > 1).sum(), (age_h > 6).sum()))
pairs_open_old = sum(1 for _, o, _, _ in allo if t1 - o[0] > 6 * 3600_000)
P("pairs with an open episode older than 6 h", pairs_open_old)
# per-pair effect of censoring on tau median: closed-only vs closed+open (open tau = age, a lower bound)
dtau, dnet, nbig = [], [], 0
per = defaultdict(lambda: ([], []))
for k, e in allc:
    per[k][0].append(e)
for k, o, sc, bc in allo:
    per[k][1].append((o, sc, bc))
worse = 0
for k, (cl, op) in per.items():
    if not op or not cl:
        continue
    tc = np.array([e[2] for e in cl]) / 1000
    ta = np.r_[tc, [o[2] / 1000 for o, _, _ in op]]
    dtau.append(np.median(ta) / max(np.median(tc), 1e-3))
    # mark-to-market of open episodes with last known Sell/Buy
    nc = np.array([e[5] for e in cl]) - 12.6
    mtm = []
    for o, sc, bc in op:
        d = o[1]
        if sc is None or bc is None:
            continue
        mtm.append((o[4] - bc if d == 1 else sc - o[5]) - 12.6)
    if mtm:
        dnet.append((np.median(nc), np.median(np.r_[nc, mtm]), min(mtm)))
        if min(mtm) < -100:
            nbig += 1
P("pairs with open eps: %d; tau median ratio (with open / closed-only) p50 %.2f p90 %.2f max %.1f" % (len(dtau), np.median(dtau), np.percentile(dtau, 90), max(dtau)) if dtau else "none")
if dnet:
    a = np.array(dnet)
    P("net median closed-only vs with open marked-to-market: mean shift %.2f bps; pairs with an open ep at MTM < −100 bps: %d; worst MTM %.1f" % ((a[:, 1] - a[:, 0]).mean(), nbig, a[:, 2].min()))

# 3. convergence time distribution of closed episodes; by venue pair
P("\n== tau of closed episodes (fee-threshold)")
tau = np.array([e[2] for _, e in allc])
net = np.array([e[5] for _, e in allc]) - 12.6
for lim in (50, 100, 200, 500, 1000, 5000, 60000):
    P("tau < %6d ms: %.1f %% of closed; of net>0 eps: %.1f %%" % (lim, 100 * (tau < lim).mean(), 100 * (tau[net > 0] < lim).mean()))
byv = defaultdict(list)
for k, e in allc:
    byv[(k[1], k[2])].append((e[2], e[5] - 12.6))
P("venue pair | closed eps | tau p50 ms | share tau<1s | net p50 | share net>0 | net>0 & tau<1s")
for v, x in sorted(byv.items(), key=lambda kv: -len(kv[1])):
    a = np.array(x)
    P("%-16s %7d %8.0f %6.1f%% %7.1f %6.1f%% %6.1f%%" % ("-".join(v), len(a), np.median(a[:, 0]), 100 * (a[:, 0] < 1000).mean(), np.median(a[:, 1]), 100 * (a[:, 1] > 0).mean(), 100 * ((a[:, 1] > 0) & (a[:, 0] < 1000)).mean()))
P("all: net p50 %.2f, mean %.2f, share>0 %.1f%%, sum of positive nets / sum |nets| %.2f" % (np.median(net), net.mean(), 100 * (net > 0).mean(), net[net > 0].sum() / np.abs(net).sum()))
# gross structure: gross = (S_i - c_i) + (c_j - B_j) + drift; entry overshoot = peak at entry?
pk = np.array([e[4] for _, e in allc])
P("peak p50 %.1f bps, entry is at >= 12.6; eps with peak >= 30: %d (%.1f %%)" % (np.median(pk), (pk >= 30).sum(), 100 * (pk >= 30).mean()))

# 4. dead minutes of exchanges and episodes starting in/next to them
P("\n== collection gaps (Alive) in window")
m_lo, m_hi = t0 // 60000, t1 // 60000
for ex, a in W.alive.a.items():
    i0 = m_lo - W.alive.base
    seg = a[max(i0, 0): m_hi - W.alive.base]
    dead = np.flatnonzero(~seg)
    P(ex, "minutes", len(seg), "dead", len(dead), "first dead", [(int(m_lo + x)) for x in dead[:5]])
near = Counter()
for k, e in allc + [(k, (o[0], o[1], o[2], True, o[3], None, None)) for k, o, _, _ in allo]:
    m = e[0] // 60000
    for ex in (k[1], k[2]):
        a = W.alive.a.get(ex)
        i = m - W.alive.base
        if a is not None and 0 <= i < len(a):
            lo, hi = max(i - 2, 0), min(i + 3, len(a))
            if not a[lo:hi].all():
                near[ex] += 1
P("episodes with entry within ±2 min of a dead minute of either venue:", dict(near))

# 5. different-asset filter: centre level and drift per pair
P("\n== centre level / drift per pair (bps)")
lev = []
for k, ps in W.pairs.items():
    c = ps.cen[~np.isnan(ps.cen)]
    if len(c) < 60:
        continue
    lev.append((k, float(np.median(c)), float(np.percentile(c, 99) - np.percentile(c, 1)), k in rows))
lev.sort(key=lambda x: -abs(x[1]))
P("pairs with |median centre| > 100 bps:", sum(1 for x in lev if abs(x[1]) > 100), "> 300:", sum(1 for x in lev if abs(x[1]) > 300))
for x in lev[:15]:
    P("  %s %s-%s centre %.0f range %.0f in table %s" % (x[0][0], x[0][1], x[0][2], x[1], x[2], x[3]))
lev.sort(key=lambda x: -x[2])
P("largest centre range p1..p99:")
for x in lev[:15]:
    P("  %s %s-%s centre %.0f range %.0f in table %s" % (x[0][0], x[0][1], x[0][2], x[1], x[2], x[3]))

# 6. false rejects: both symbols ready, pair absent
P("\n== pairs absent though both sides ready")
day = arb_live.days_between(t1, t1)[0]
_, coins = arb_daemon.coin_map(day)
rej = []
for coin, per_ex in coins.items():
    exs = [ex for ex in arb_daemon.EXCHANGES if ex in per_ex and arb_daemon.ready(W, ex, per_ex[ex][0])]
    for i in range(len(exs)):
        for j in range(i + 1, len(exs)):
            ka, kb = (exs[i], per_ex[exs[i]][0]), (exs[j], per_ex[exs[j]][0])
            if (coin, exs[i], exs[j]) in W.pairs:
                continue
            sa, sb = W.syms[ka], W.syms[kb]
            now = None
            if sa.carry and sb.carry:
                now = ((sa.carry[1] + sa.carry[2]) / 2) / ((sb.carry[1] + sb.carry[2]) / 2)
            rej.append((coin, exs[i], exs[j], sa.mid / sb.mid, now))
P("absent pairs", len(rej), "of which current-price ratio inside (0.9, 1.1):", sum(1 for r in rej if r[4] and 0.9 < r[4] < 1.1))
for r in rej:
    if r[4] and 0.9 < r[4] < 1.1:
        P("  FALSE REJECT? %s %s-%s mid-ratio(frozen) %.3f now %.3f" % r)
for r in rej[:40]:
    if not (r[4] and 0.9 < r[4] < 1.1):
        P("  reject %s %s-%s frozen %.3f now %s" % (r[0], r[1], r[2], r[3], "%.3f" % r[4] if r[4] else None))
# pairs in table whose current ratio is outside (0.9, 1.1)
bad_now = []
for (coin, ea, eb), ps in W.pairs.items():
    ka, kb = (ea, coins.get(coin, {}).get(ea, (None,))[0]), (eb, coins.get(coin, {}).get(eb, (None,))[0])
    sa, sb = W.syms.get(ka), W.syms.get(kb)
    if sa and sb and sa.carry and sb.carry:
        rr = ((sa.carry[1] + sa.carry[2]) / 2) / ((sb.carry[1] + sb.carry[2]) / 2)
        if not 0.9 < rr < 1.1:
            bad_now.append((coin, ea, eb, rr))
P("accepted pairs whose current price ratio is outside (0.9,1.1):", bad_now[:20])

# 7. top table rows sanity (what the owner sees first)
P("\n== top-20 rows by 24h count")
top = sorted(doc["rows"], key=lambda r: -r["cnt"][3])[:20]
for r in top:
    P(r["c"], r["a"], r["b"], r["cnt"], r["big"], r["pk"], r["tau"], r["net"])
P("rows with net p50 > 0:", sum(1 for r in doc["rows"] if r["net"] and r["net"][0] > 0), "of", sum(1 for r in doc["rows"] if r["net"]))
