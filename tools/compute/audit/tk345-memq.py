#!/usr/bin/env python3
"""TK-345: проход валидности с учётом памяти. Задача = (сутки, один символ) × семейства F1 F2 F3 подряд (pass.sh, TAG=символ).
Память задаёт самый тяжёлый символ (SOL один 5,2 ГБ, пик задачи до 6,3 ГБ): запуск новой задачи — только если сумма резервов
(новая/молодая — R0, прочая — прошлый пик VmHWM × 1,15 + 0,5 ГБ) + R0 ≤ BUDGET; при MemAvailable < KILL ГБ снимается самая
молодая задача (без done — подхватится повтором).
  tk345-memq.py <выход-каталог> <сутки через запятую>       env: NX (15) BUDGET (57) R0 (7.0) KILL (2.5) PASS GRP
"""
import os, subprocess, sys, time, glob
O = sys.argv[1]; days = sys.argv[2].split(","); NX = int(os.environ.get("NX", 15))
BUDGET = float(os.environ.get("BUDGET", 57)); R0 = float(os.environ.get("R0", 7.0)); KILL = float(os.environ.get("KILL", 2.5))
PASS = os.environ.get("PASS", "/data/tk0345/tk345-validity-pass.sh"); GB = 1048576.0
os.makedirs(O, exist_ok=True)
def root(d):
    mon = {"01": "jan", "02": "feb"}[d[5:7]]
    return f"/data/tk046/{mon}/home/alpha/epochs/e-{mon}/study/root-{d}"
queue = []
for d in days:
    fs = sorted(glob.glob(root(d) + "/*.binlog"), key=lambda f: -os.path.getsize(f))
    for f in fs:
        sym = os.path.basename(f).rsplit("-", 3)[0]
        if not all(os.path.exists(f"{O}/{d}-{sym}-{fam}/done") for fam in ("F1", "F2", "F3")):
            queue.append((d, sym, 0))
print(f"задач {len(queue)}", flush=True)
def hwm_pgid(pg):
    m = 0
    for p in os.listdir("/proc"):
        if not p.isdigit(): continue
        try:
            st = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
            if int(st[3]) != pg: continue
            for l in open(f"/proc/{p}/status"):
                if l.startswith("VmHWM"): m = max(m, int(l.split()[1]))
        except Exception: pass
    return m / GB
def avail():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): return int(l.split()[1]) / GB
run = {}; peak = {}; failed = []; t0 = time.time(); done_n = 0; killed = 0
def reserve(k):
    t = run[k]
    return R0 if time.time() - t["t"] < 90 and peak.get(k, 0) < 1 else max(peak.get(k, 0) * 1.15 + 0.5, 1.0)
while queue or run:
    for k in list(run):
        t = run[k]; pg = t["p"].pid
        peak[k] = max(peak.get(k, 0), hwm_pgid(pg))
        rc = t["p"].poll()
        if rc is not None:
            ok = all(os.path.exists(f"{O}/{k[0]}-{k[1]}-{fam}/done") for fam in ("F1", "F2", "F3"))
            with open(f"{O}/memq.txt", "a") as f: f.write(f"{k[0]} {k[1]} rc={rc} ok={ok} s={int(time.time()-t['t'])} peak_gb={peak[k]:.2f}\n")
            if not ok and not t.get("killed") and t["try"] < 2: queue.append((k[0], k[1], t["try"] + 1))
            elif not ok: failed.append(k)
            del run[k]; done_n += ok
    if run and avail() < KILL:
        k = max(run, key=lambda x: run[x]["t"]); os.killpg(run[k]["p"].pid, 9); run[k]["killed"] = True; killed += 1
        with open(f"{O}/memq.txt", "a") as f: f.write(f"KILL {k} avail<{KILL}\n")
    while queue and len(run) < NX and sum(reserve(k) for k in run) + R0 <= BUDGET and avail() > R0 + KILL:
        d, sym, tr = queue.pop(0)
        env = dict(os.environ, TAG=sym, PROBE_SYMS=sym, GRP="1")
        p = subprocess.Popen(["bash", PASS, d, "0", O], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        run[(d, sym)] = {"p": p, "t": time.time(), "try": tr}
    with open(f"{O}/memq.status", "w") as f:
        f.write(f"t={int(time.time()-t0)}s queue={len(queue)} run={len(run)} ok={done_n} failed={len(failed)} killed={killed} reserve={sum(reserve(k) for k in run):.1f} avail={avail():.1f}\n")
    time.sleep(5)
print(f"готово ok={done_n} failed={failed}", flush=True)
open(f"{O}/done", "w").write("ok\n" if not failed else "failed\n")
sys.exit(1 if failed else 0)
