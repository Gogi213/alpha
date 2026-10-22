#!/usr/bin/env python3
"""TK-360: проход валидности СПОТА с учётом памяти (по tk345-memq.py без подхвата чужих задач). Задача = (сутки, символ) × F1 F2 F3
подряд (tk360-validity-spot.sh, TAG=символ). Резерв = max(пик VmHWM × 1,15 + 0,5; известный пик символа × KW; 1); новая задача — если
сумма резервов + её резерв ≤ BUDGET и avail() ≥ резерв + KILL и «занято сейчас + недобранное резервами идущих + резерв» + KILL ≤ предел юнита;
при avail() < KILL снимается самая молодая (повтор до 3 раз). avail() = min(MemAvailable машины, предел cgroup юнита − неосвобождаемое
занятое: memory.current − active_file − inactive_file; TK-360 OOM 23.10: MemAvailable машины внутри юнита с MemoryMax всегда «свободна»).
Параметры NX BUDGET R0 KILL KW перечитываются каждый такт из <выход>/memq.cfg (файл сильнее env — при смене умолчаний править и его).
  tk360-memq.py <выход-каталог> <сутки через запятую>   env: NX (15) BUDGET (48) R0 (3.0) KILL (4.0) KW (0.9) PASS ONLY=<файл «сутки символ»> DRY=1 REQUIRE_CG=1 (отказ, если предела cgroup нет)"""
import os, subprocess, sys, time, glob
O = sys.argv[1]; days = sys.argv[2].split(","); CFG = f"{O}/memq.cfg"
P = dict(NX=15, BUDGET=48, R0=3.0, KILL=4.0, KW=0.9)
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
def cg_dir():  # cgroup2 своего юнита; None — если не cgroup2
    try:
        for l in open("/proc/self/cgroup"):
            if l.startswith("0::"): return "/sys/fs/cgroup" + l.strip()[3:]
    except Exception: pass
CG = cg_dir()
def cg_used_limit():  # (неосвобождаемое занятое ГБ, предел ГБ) юнита; None — предела нет
    try:
        lim = open(f"{CG}/memory.max").read().strip()
        if lim == "max": return None
        cur = int(open(f"{CG}/memory.current").read()); fl = 0
        for l in open(f"{CG}/memory.stat"):
            w = l.split()
            if w[0] in ("active_file", "inactive_file"): fl += int(w[1])
        return (cur - fl) / 1073741824.0, int(lim) / 1073741824.0
    except Exception: return None
def avail():
    a = 1e9
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): a = int(l.split()[1]) / GB
    ul = cg_used_limit()
    return min(a, ul[1] - ul[0]) if ul else a
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
print(f"cgroup {CG} used/limit {cg_used_limit()} avail {avail():.1f}", flush=True)
if os.environ.get("REQUIRE_CG") and not cg_used_limit(): sys.exit("REQUIRE_CG: предела cgroup юнита не видно — отказ")
if os.environ.get("DRY"): print(queue[:5], P, f"avail={avail():.2f} cg={cg_used_limit()}"); sys.exit(0)
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
            if not ok and t["try"] < 3: queue.append((k[0], k[1], t["try"] + 1))
            elif not ok: failed.append(k)
            del run[k]; done_n += ok
    av = avail()
    if run and av < P["KILL"]:
        k = max(run, key=lambda x: run[x]["t"]); os.killpg(run[k]["p"].pid, 9); run[k]["killed"] = True; killed += 1
        with open(f"{O}/memq.txt", "a") as f: f.write(f"KILL {k} avail={av:.1f}<{P['KILL']:g}\n")
    tot = sum(rsv(k, peak.get(k, 0)) for k in run)
    ul = cg_used_limit()  # недобранное резервами идущих: задача растёт после допуска (1–3 → 6,7 ГБ, TK-360 OOM)
    pend = sum(max(0.0, rsv(k, peak.get(k, 0)) - sc.get(run[k]["p"].pid, (0, 0))[1]) for k in run)
    while queue and len(run) < P["NX"]:
        d, sym, tr = queue[0]; nr = rsv((d, sym), 0)
        if tot + nr > P["BUDGET"] or avail() < nr + P["KILL"]: break
        if ul and ul[0] + pend + nr + P["KILL"] > ul[1]: break
        queue.pop(0)
        env = dict(os.environ, TAG=sym, GT="1")
        p = subprocess.Popen(["bash", PASS, d, O, sym], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        run[(d, sym)] = {"p": p, "t": time.time(), "try": tr}; tot += nr; pend += nr
    with open(f"{O}/memq.status", "w") as f:
        f.write(f"t={int(time.time()-t0)}s queue={len(queue)} run={len(run)} ok={done_n} failed={len(failed)} killed={killed} reserve={tot:.1f} used_rss={sum(sc.get(run[k]['p'].pid, (0, 0))[1] for k in run):.1f} avail={avail():.1f} cg_used={ul[0] if ul else -1:.1f}/{ul[1] if ul else -1:.1f} pend={pend:.1f} cfg={P}\n")
    time.sleep(2)
print(f"готово ok={done_n} failed={failed}", flush=True)
open(f"{O}/done", "w").write("ok\n" if not failed else "failed\n")
sys.exit(1 if failed else 0)
