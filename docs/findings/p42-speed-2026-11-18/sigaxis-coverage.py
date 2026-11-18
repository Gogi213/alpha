import sys, os, re, collections
sys.path.insert(0, "tools/p42")
import retitr_full_run as rr
rf = rr.rf; rc = rf.rc
spec = rf.Spec.load(rr.GRIDS)
base = {s: dict(kv.split("=", 1) for kv in rf.cfg_keys(spec, s, {}).split()) for s in rf.SIDES}
SIGAX = ["e", "natr_min", "t1h", "t24"]
sigmode = dict(kv.split("=", 1) for kv in rc.SIGMODE.split()) if isinstance(rc.SIGMODE, str) else dict(rc.SIGMODE)
print("SIGMODE:", rc.SIGMODE)
cov = collections.Counter(); tot = collections.Counter(); unmatched = 0
varDir = "/data/p42-tk599/var"
lo, hi = int(sys.argv[1]), int(sys.argv[2])
for n in range(lo, hi + 1):
    p = "%s/b%04d.txt" % (varDir, n)
    if not os.path.exists(p):
        continue
    for ln in open(p, encoding="utf-8").read().splitlines():
        if not ln.strip():
            continue
        vid, _, keys = ln.partition(" ")
        kd = dict(kv.split("=", 1) for kv in keys.split())
        found = None
        for side in rf.SIDES:
            for sig in (False, True):
                kk = " ".join("%s=%s" % kv for kv in kd.items() if not (sig and kv[0] in sigmode))
                if rf.Req(side, kk, sig).vid == vid:
                    found = (side, sig, kd); break
            if found: break
        if not found:
            unmatched += 1; continue
        side, sig, kd = found
        tot[(side, sig)] += 1
        var = [a for a in SIGAX if a in kd and kd[a] != base[side].get(a)]
        for a in var:
            cov[(a, side, sig)] += 1
        if not var:
            cov[("(базовый сигнал)", side, sig)] += 1
print("unmatched:", unmatched)
for k in sorted(tot): print("всего", k, tot[k])
for a in SIGAX + ["(базовый сигнал)"]:
    print("%-16s" % a, " ".join("%s%s=%d" % (s, "_sig" if g else "", cov[(a, s, g)]) for s in rf.SIDES for g in (False, True)))
