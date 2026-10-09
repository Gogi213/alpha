"""TK-071 п.3: сводка пар A=A стенда под живым prod → stdout (пары из /data/tk071/live/pairs.txt)."""
import json, os, re

S, O = "/data/sched", "/data/tk051/stand-out"
dl = []
for l in open("/data/tk071/live/pairs.txt"):
    k = l.split()[0][4:]
    r = {}
    for s in "ab":
        m = re.search(rf" {s}=(\d+):(\w*):prod_end=(\S*)", l)
        i = m.group(1)
        own = {x.split()[0]: x.split()[1] for x in open(f"{S}/own/{i}") if x.startswith("usage_usec")}
        wall = None
        for d in os.listdir(O):
            if d.startswith(f"live{k}{s}-d15"):
                wall = float([x.split()[1] for x in open(f"{O}/{d}/metrics.txt") if x.startswith("wall_s")][0])
        v = json.load(open(f"{S}/validity/{i}.json"))
        r[s] = dict(id=i, usec=int(own["usage_usec"]), wall=wall, ok=v["ok"], why=v["why"], prod_end=m.group(3))
    du, dw = 100 * (r["a"]["usec"] / r["b"]["usec"] - 1), 100 * (r["a"]["wall"] / r["b"]["wall"] - 1)
    both = r["a"]["ok"] and r["b"]["ok"]
    print(f"pair{k}: {l.split()[1]}  usage {du:+.3f} %  wall {dw:+.3f} %  ok×2 {both}  {r['a']['why'] or ''}{r['b']['why'] or ''}")
    if both:
        dl.append((abs(du), abs(dw)))
print(f"пар ok×2: {len(dl)}; max|Δusage| {max((x[0] for x in dl), default=None)}; max|Δwall| {max((x[1] for x in dl), default=None)}")
