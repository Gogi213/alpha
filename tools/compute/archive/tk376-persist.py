#!/usr/bin/env python3
"""TK-376 п.7: страта open_gap 50–100 по монетам. usage: tk376-persist.py episodes.csv OUT.json. Монето-сутки с суммарной длительностью эпизодов (open_gap>=50) >= 50 % суток = «постоянный сдвиг»; net_1 страты без них, и по монетам (доля суток с эпизодом 50–100)."""
import sys, csv, json, collections, statistics, random
ep, out = sys.argv[1:3]
rows = [r for r in csv.DictReader(open(ep)) if r['net_1'] != '' and 50 <= abs(float(r['open_gap'])) < 100]
cd_dur = collections.defaultdict(float); cd_n = collections.Counter(); cd_net = collections.defaultdict(list)
for r in rows:
    k = (r['net'], r['coin'], r['day']); cd_dur[k] += float(r['dur_s']); cd_n[k] += 1; cd_net[k].append(float(r['net_1']))
pers = {k for k, d in cd_dur.items() if d >= 43200}
def mean_ci(keys):
    xs = [(sum(cd_net[k]), len(cd_net[k])) for k in keys]; n = sum(b for _, b in xs); m = sum(a for a, _ in xs) / n if n else None
    rnd = random.Random(5); bs = []
    for _ in range(500):
        s = [xs[rnd.randrange(len(xs))] for _ in xs]; bs.append(sum(a for a, _ in s) / max(sum(b for _, b in s), 1))
    bs.sort(); return dict(n=n, cd=len(xs), mean=round(m, 2), lo=round(bs[12], 2), hi=round(bs[487], 2)) if n else None
allk = set(cd_net); res = dict(all=mean_ci(allk), persistent=mean_ci(pers) if pers else None, nonpersistent=mean_ci(allk - pers))
bycoin = collections.defaultdict(lambda: [0, 0.0, set()])
for k in allk: bycoin[k[:2]][0] += cd_n[k]; bycoin[k[:2]][1] += cd_dur[k]; bycoin[k[:2]][2].add(k[2])
tot = collections.Counter()
res['coins_with_persistent_days'] = len({k[:2] for k in pers}); res['coins_total'] = len(bycoin)
top = sorted(((len({k for k in pers if k[:2] == c}), c) for c in bycoin), reverse=True)[:15]
res['top_persistent_coins'] = [(c[0] + ':' + c[1], n) for n, c in top]
# net_1 без постоянных, по сети
for net in ('ethereum', 'base', 'bsc'):
    ks = {k for k in allk - pers if k[0] == net}
    res['nonpers_' + net] = mean_ci(ks) if ks else None
json.dump(res, open(out, 'w'), ensure_ascii=False, indent=1); print(json.dumps(res, ensure_ascii=False)[:1800])
