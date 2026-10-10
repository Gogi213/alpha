"""TK-071 п.3: ждёт конца пар A=A (метки p1a..p3b, p1c — повтор p1a) и пишет /data/tk071/pairs/summary3.txt:
instructions:u (perf), usage_usec юнита (own/<id>), годность окна (validity/<id>.json), разница пар в %."""
import glob, json, os, time

D, S = "/data/tk071/pairs", "/data/sched"
want = ["r1a", "r1b", "r2a", "r2b"]
t0 = time.time()
while not all(os.path.exists(f"{D}/{k}.out") and "end " in open(f"{D}/{k}.out").read() for k in want):
    if time.time() - t0 > 7200:
        break
    time.sleep(20)
jobs = {}
for f in glob.glob(f"{S}/jobs/*.json"):
    j = json.load(open(f))
    if j["name"].startswith("pair"):
        jobs[j["name"]] = j
rows = {}
for k in want:
    n = "pair" + k
    j = jobs.get(n)
    ins = usec = None
    try:
        ins = int(open(f"{D}/{k}.perf").read().split("instructions")[0].split("\n")[-1].split(",")[0])
    except (OSError, ValueError):
        for l in open(f"{D}/{k}.perf"):
            if "instructions" in l:
                ins = int(l.split(",")[0])
    if j:
        try:
            for l in open(f"{S}/own/{j['id']}"):
                if l.startswith("usage_usec"):
                    usec = int(l.split()[1])
        except OSError:
            pass
        try:
            v = json.load(open(f"{S}/validity/{j['id']}.json"))
        except OSError:
            v = {}
    rows[k] = dict(id=j and j["id"], ins=ins, usec=usec, valid=v.get("ok"), why=v.get("why"), wall=j and round(j.get("t_end", 0) - j.get("t_start", 0)))
out = [json.dumps(rows, ensure_ascii=False, indent=1)]
for a, b in (("r1a", "r1b"), ("r2a", "r2b")):
    ra, rb = rows.get(a), rows.get(b)
    if ra and rb and ra["ins"] and rb["ins"] and ra["usec"] and rb["usec"]:
        out.append(f"{a}/{b}: instr {100 * (ra['ins'] / rb['ins'] - 1):+.3f} %, usage_usec {100 * (ra['usec'] / rb['usec'] - 1):+.3f} %")
open(f"{D}/summary3.txt", "w").write("\n".join(out) + "\n")
