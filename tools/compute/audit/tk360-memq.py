#!/usr/bin/env python3
"""TK-360: проход валидности СПОТА с учётом памяти (по tk345-memq.py без подхвата чужих задач). Задача = (сутки, символ) × F1 F2 F3
подряд (tk360-validity-spot.sh, TAG=символ). Резерв = max(пик VmHWM × 1,15 + 0,5; известный пик символа × KW; 1); новая задача — если
сумма резервов + её резерв ≤ BUDGET и MemAvailable > резерв + KILL; при MemAvailable < KILL снимается самая молодая (без done — повтор).
Параметры NX BUDGET R0 KILL KW перечитываются каждый такт из <выход>/memq.cfg.
  tk360-memq.py <выход-каталог> <сутки через запятую>   env: NX (15) BUDGET (80) R0 (3.0) KILL (2.5) KW (0.9) PASS ONLY=<файл «сутки символ»> DRY=1"""
import os, subprocess, sys, time, glob
O = sys.argv[1]; days = sys.argv[2].split(","); CFG = f"{O}/memq.cfg"
P = dict(NX=15, BUDGET=80, R0=3.0, KILL=2.5, KW=0.9)
for k in P: P[k] = float(os.environ.get(k, P[k]))
ONLY = {tuple(l.split()) for l in open(os.environ["ONLY"]) if l.strip()} if os.environ.get("ONLY") else None
PASS = os.environ.get("PASS", "/data/tk0360/tk360-validity-spot.sh"); GB = 1048576.0; FAMS = ("F1", "F2", "F3")
MON = {"01": "jan", "02": "feb", "03": "mar", "04": "apr", "05": "may", "06": "jun", "07": "jul", "08": "aug", "09": "sep"}
def load_cfg():
    try:
        for l in open(CFG):
            k, _, v = l.strip().partition("=")
            if k in P and v: P[k] = float(v)
    except Exception: pass
os.makedirs(O, exist_ok=True)
if not os.path.exists(CFG):
    open(CFG, "w").write("".join(f"{k}={v:g}\n" for k, v in P.items()))
load_cfg()
def root(d): return f"/data/tk0319/e-{MON[d[5:7]]}/study/root-{d}"
def is_done(d, sym): return all(os.path.exists(f"{O}/{d}-{sym}-{fam}/done") for fam in FAMS)
def scan():  # сессия -> (VmHWM ГБ, VmRSS ГБ) по всем процессам сессии
    r = {}
    for p in os.listdir("/proc"):
        if not p.isdigit(): continue
        try:
            sid = int(open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[3]); h = rs = 0
            for l in open(f"/proc/{p}/status"):
                if l.startswith("VmHWM"): h = int(l.split()[1])
                elif l.startswith("VmRSS"): rs = int(l.split()[1])
            a = r.get(sid, (0, 0)); r[sid] = (max(a[0], h / GB), a[1] + rs / GB)
        except Exception: pass
    return r
def avail():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): return int(l.split()[1]) / GB
sympeak = {}
def note_peak(sym, gb):
    if gb > sympeak.get(sym, 0): sympeak[sym] = gb
try:
    for l in open(f"{O}/memq.txt"):
        w = l.split()
        if len(w) >= 6 and w[2].startswith("rc=") and w[-1].startswith("peak_gb="): note_peak(w[1], float(w[-1][8:]))
except FileNotFoundError: pass
run = {}; peak = {}; failed = []; t0 = time.time(); done_n = 0; killed = 0
queue = []
for d in days:
    fs = sorted(glob.glob(root(d) + "/*.binlog"), key=lambda f: -os.path.getsize(f))
    for f in fs:
        sym = os.path.basename(f)[: -len(f"-{d}.binlog")]
        if ONLY is not None and (d, sym) not in ONLY: continue
        if not is_done(d, sym): queue.append((d, sym, 0))
print(f"задач {len(queue)} известных пиков {len(sympeak)}", flush=True)
if os.environ.get("DRY"): print(queue[:5], P); sys.exit(0)
def rsv(k, h): return max(h * 1.15 + 0.5, sympeak.get(k[1], P["R0"]) * P["KW"], 1.0)
while queue or run:
    load_cfg(); sc = scan()
    for k in list(run):
        t = run[k]; pg = t["p"].pid
        peak[k] = max(peak.get(k, 0), sc.get(pg, (0, 0))[0])
        rc = t["p"].poll()
        if rc is not None:
            ok = is_done(*k)
            note_peak(k[1], peak[k])
            with open(f"{O}/memq.txt", "a") as f: f.write(f"{k[0]} {k[1]} rc={rc} ok={ok} s={int(time.time()-t['t'])} peak_gb={peak[k]:.2f}\n")
            if not ok and not t.get("killed") and t["try"] < 2: queue.append((k[0], k[1], t["try"] + 1))
            elif not ok: failed.append(k)
            del run[k]; done_n += ok
    if run and avail() < P["KILL"]:
        k = max(run, key=lambda x: run[x]["t"]); os.killpg(run[k]["p"].pid, 9); run[k]["killed"] = True; killed += 1
        with open(f"{O}/memq.txt", "a") as f: f.write(f"KILL {k} avail<{P['KILL']:g}\n")
    tot = sum(rsv(k, peak.get(k, 0)) for k in run)
    while queue and len(run) < P["NX"]:
        d, sym, tr = queue[0]; nr = rsv((d, sym), 0)
        if tot + nr > P["BUDGET"] or avail() < nr + P["KILL"]: break
        queue.pop(0)
        env = dict(os.environ, TAG=sym, GT="1")
        p = subprocess.Popen(["bash", PASS, d, O, sym], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        run[(d, sym)] = {"p": p, "t": time.time(), "try": tr}; tot += nr
    with open(f"{O}/memq.status", "w") as f:
        f.write(f"t={int(time.time()-t0)}s queue={len(queue)} run={len(run)} ok={done_n} failed={len(failed)} killed={killed} reserve={tot:.1f} used_rss={sum(sc.get(run[k]['p'].pid, (0, 0))[1] for k in run):.1f} avail={avail():.1f} cfg={P}\n")
    time.sleep(5)
print(f"готово ok={done_n} failed={failed}", flush=True)
open(f"{O}/done", "w").write("ok\n" if not failed else "failed\n")
sys.exit(1 if failed else 0)
