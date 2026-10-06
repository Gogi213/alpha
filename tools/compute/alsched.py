#!/usr/bin/env python3
"""alsched.py (TK-071, В-189): планировщик сервера счёта вместо замка-флока benchrun.

Заявка: класс prod (производство: ядра/память/диск по заявке, пакуется на свободные ядра) или measure (замер: эксклюзивное
окно — всё производство на freeze, замер получает все ядра, по выходу размораживается). Всё запускается через него.

  alsched.py submit --name N --max-runtime 2h [--cls prod|measure] [--cores 4] [--mem 8] [--disk hdd1|hdd2|none] [--cwd D] -- команда…
  alsched.py wave|stand --max-runtime 30m команда…   # = замер: подать, дождаться окна, показать вывод, вернуть код (обёртка вместо benchrun)
  alsched.py ps | cancel <id> | daemon
Состояние: $SCHED_DIR (/data/sched): jobs/<id>.json (создаёт CLI, дальше пишет только демон), cancel/<id>, logs/<id>.log, rc/<id>.
"""
import argparse, json, math, os, subprocess, sys, time

DIR = os.environ.get("SCHED_DIR", "/data/sched")
NCPU, MEM_GB, TICK = 16, 56, int(os.environ.get("SCHED_TICK", "5"))
DISK_SLOTS = int(os.environ.get("SCHED_DISK_SLOTS", "0"))   # заданий на диск; 0 = без лимита до калибровки (В-178), число не выдумываем
FREEZE_PAT = (os.environ["SCHED_PAT"].split(",") if os.environ.get("SCHED_PAT")   # SCHED_PAT — только для smoke
              else ["tk0*", "t4*", "t5*", "run-*", "tk048-*"])   # как benchrun2: всё, кроме alpha-*
LEGACY_PAT = FREEZE_PAT


class Core:
    """Решения без побочных эффектов: be — бэкенд (systemd или макет)."""

    def __init__(self, be, ncpu=NCPU, mem=MEM_GB, disk_slots=DISK_SLOTS):
        self.be, self.ncpu, self.mem, self.slots = be, ncpu, mem, disk_slots
        self.jobs = {}
        self.frozen = False
        self.last = None
        self.snaps = {}

    def add(self, j):
        self.jobs.setdefault(j["id"], j)

    def running(self, cls):
        return [j for j in self.jobs.values() if j["state"] == "running" and j["cls"] == cls]

    def tick(self):
        be, now = self.be, self.be.now()
        dt = 0 if self.last is None else now - self.last
        self.last = now
        for j in self.running("prod"):                    # бюджет max_runtime — время БЕЗ заморозки
            if not self.frozen:
                j["active_s"] = j.get("active_s", 0) + dt
            if j["active_s"] > j["max_runtime"]:
                be.kill(j)
        for j in self.running("measure"):                 # замер не замораживается: бюджет = стена
            if now - j["t_start"] > j["max_runtime"]:
                be.kill(j)
        for j in self.jobs.values():
            if j["state"] == "queued" and be.cancelled(j):
                j.update(state="done", rc=-15, t_end=now)
            elif j["state"] == "running":
                rc = be.done(j)
                if rc is not None:
                    j.update(state="done", rc=rc, t_end=now, cpus=[])
                    if j["cls"] == "measure" and j["id"] in self.snaps:
                        j["valid"] = be.win_end(j, self.snaps.pop(j["id"]))
        if self.running("measure"):
            return
        mq = sorted((j for j in self.jobs.values() if j["state"] == "queued" and j["cls"] == "measure"),
                    key=lambda j: j["t_submit"])
        if mq:
            if not self.frozen:
                be.freeze_all()
                self.frozen = True
            j = mq[0]
            j.update(state="running", t_start=now, cpus=list(range(self.ncpu)))
            self.snaps[j["id"]] = be.win_begin(j)
            be.start(j)
            return
        if self.frozen:
            be.thaw_all()
            self.frozen = False
            for j in self.running("prod"):
                be.extend(j, j["max_runtime"] - j.get("active_s", 0))
        used = {c for j in self.running("prod") for c in j["cpus"]}
        mem = sum(j["mem"] for j in self.running("prod"))
        legacy = math.ceil(be.legacy_busy())
        for j in sorted((j for j in self.jobs.values() if j["state"] == "queued"),
                        key=lambda j: (j.get("prio", 5), j["t_submit"])):
            free = [c for c in range(self.ncpu) if c not in used]
            if len(free) - legacy < j["cores"] or mem + j["mem"] > self.mem:
                continue
            if self.slots and j["disk"] != "none" and sum(1 for r in self.running("prod") if r["disk"] == j["disk"]) >= self.slots:
                continue
            j.update(state="running", t_start=now, cpus=free[: j["cores"]])
            used |= set(j["cpus"])
            mem += j["mem"]
            be.start(j)

    def free_cores(self):
        return self.ncpu - sum(len(j["cpus"]) for j in self.running("prod")) - math.ceil(self.be.legacy_busy())


def judge_window(d, ncpu=NCPU, tol=0.01):
    """d: cpu_s (занято на хосте за окно), own_cpu_s (юнит замера), wall_s, foreign_units (посторонние активные юниты в окне),
    disk_b (прочитано с дисков хоста), own_disk_b (читал юнит замера). Помеха = чужое ЦП / (стена × ядра) и чужое чтение
    диска / всё чтение окна (только если замер сам читает диск); замороженные юниты не в счёт; допуск tol = 1 % (гейт TK-071). → (годна, причины)."""
    why = []
    if d["foreign_units"]:
        why.append("посторонние юниты в окне: " + ",".join(sorted(d["foreign_units"])[:5]))
    cap = d["wall_s"] * ncpu
    if cap > 0 and (d["cpu_s"] - d["own_cpu_s"]) / cap > tol:
        why.append(f"чужое ЦП {(d['cpu_s'] - d['own_cpu_s']) / cap * 100:.1f} % ядер окна")
    if d["own_disk_b"] > 0 and d["disk_b"] > 0 and (d["disk_b"] - d["own_disk_b"]) / d["disk_b"] > tol:
        why.append(f"чужое чтение диска {(d['disk_b'] - d['own_disk_b']) / d['disk_b'] * 100:.1f} %")
    return (not why, why)


def warn_for(job, free, queued_prod):
    if job["cls"] == "prod" and job["cores"] == 1 and free >= 8 and not queued_prod:
        return (f"ПРЕДУПРЕЖДЕНИЕ: заявка на 1 ядро, свободно {free} из {NCPU}. Подбери параллельность "
                f"(tools/compute/calibrate.sh, В-178) и подай --cores N или несколько заданий.")
    return None


# ---------- systemd-бэкенд ----------
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, stdin=subprocess.DEVNULL)


class SystemdBackend:
    def __init__(self):
        self.frozen_list = os.path.join(DIR, "frozen.json")
        self._cpu = {}
        self._t = time.time()
        self._legacy = 0.0
        self._last_own = {}

    def now(self):
        return time.time()

    def cancelled(self, j):
        return os.path.exists(f"{DIR}/cancel/{j['id']}")

    def unit(self, j):
        return ("alpha-sm-" if j["cls"] == "measure" else "tk0s-") + f"{j['name']}-{j['id']}"

    def start(self, j):
        os.makedirs(f"{DIR}/logs", exist_ok=True)
        os.makedirs(f"{DIR}/rc", exist_ok=True)
        cpus = ",".join(map(str, j["cpus"]))
        inner = f"{j['cmd']}\nrc=$?; echo $rc > {DIR}/rc/{j['id']}; exit $rc"
        if j["cls"] == "measure":
            self.arm_failsafe(j["max_runtime"] + 2 * TICK)
        r = sh("systemd-run", f"--unit={self.unit(j)}", "--collect", f"--working-directory={j['cwd']}",
               "-p", f"RuntimeMaxSec={int(j['max_runtime'])}", "-p", "IOAccounting=yes", "-p", "CPUAccounting=yes",
               "-p", f"AllowedCPUs={cpus}", "-p", f"MemoryMax={j['mem']}G", "-p", f"CPUQuota={len(j['cpus']) * 100}%",
               "-p", f"StandardOutput=append:{DIR}/logs/{j['id']}.log", "-p", "StandardError=inherit",
               "bash", "-c", inner)
        if r.returncode:
            open(f"{DIR}/rc/{j['id']}", "w").write("125\n")

    def kill(self, j):
        sh("systemctl", "stop", self.unit(j))
        open(f"{DIR}/rc/{j['id']}", "w").write("124\n")

    def extend(self, j, remaining):
        """после разморозки: RuntimeMaxSec считается от старта юнита по стене, заморозка его съела — пересчитать."""
        el = self.now() - j["t_start"]
        sh("systemctl", "set-property", "--runtime", self.unit(j), f"RuntimeMaxSec={int(el + max(remaining, 1))}")

    def arm_failsafe(self, secs):
        """аренда с TTL: если демон умрёт при замороженном производстве — таймер сам разморозит."""
        sh("systemctl", "stop", "alpha-sm-failsafe.timer")
        sh("systemd-run", "--unit=alpha-sm-failsafe", "--collect", f"--on-active={int(secs)}s",
           "-E", f"SCHED_DIR={DIR}", "python3", os.path.abspath(__file__), "thaw")

    def cpu_host(self):
        f = open("/proc/stat").readline().split()[1:]
        return (sum(map(int, f)) - int(f[3]) - int(f[4])) / os.sysconf("SC_CLK_TCK")

    def disk_host(self):
        tot = 0
        for l in open("/proc/diskstats"):
            p = l.split()
            if len(p) > 5 and (p[2].startswith("sd") or p[2].startswith("nvme")) and not p[2][-1].isdigit():
                tot += int(p[5]) * 512
        return tot

    def unit_cpu_io(self, j):
        u = self.unit(j)
        c = sh("systemctl", "show", "-p", "CPUUsageNSec", "--value", u).stdout.strip()
        cg = sh("systemctl", "show", "-p", "ControlGroup", "--value", u).stdout.strip()
        rb = 0
        try:
            if cg in ("", "/"):
                raise OSError
            for l in open(f"/sys/fs/cgroup{cg}/io.stat"):
                rb += sum(int(x.split("=")[1]) for x in l.split() if x.startswith("rbytes="))
        except OSError:
            pass
        return (int(c) / 1e9 if c.isdigit() else 0.0), rb

    def foreign_units(self, j):
        own = {self.unit(j) + ".service"}
        return [u for u in self.units(FREEZE_PAT) if u not in own and not u.startswith("alpha-")
                and sh("systemctl", "show", "-p", "FreezerState", "--value", u).stdout.strip() != "frozen"]   # замороженный не мешает

    def win_begin(self, j):
        return dict(t=time.time(), cpu=self.cpu_host(), disk=self.disk_host(), units=set(self.foreign_units(j)))

    def win_end(self, j, s0):
        t1 = time.time()
        own_cpu, own_rb = self._last_own.get(j["id"], (0.0, 0))
        d = dict(wall_s=t1 - s0["t"], cpu_s=self.cpu_host() - s0["cpu"], own_cpu_s=own_cpu,
                 disk_b=self.disk_host() - s0["disk"], own_disk_b=own_rb,
                 foreign_units=s0["units"] | set(self.foreign_units(j)))
        ok, why = judge_window(d)
        os.makedirs(f"{DIR}/validity", exist_ok=True)
        json.dump(dict(ok=ok, why=why, **{k: (sorted(v) if isinstance(v, set) else v) for k, v in d.items()}),
                  open(f"{DIR}/validity/{j['id']}.json", "w"), ensure_ascii=False)
        return dict(ok=ok, why=why)

    def done(self, j):
        p = f"{DIR}/rc/{j['id']}"
        if j["cls"] == "measure":      # юнит с --collect исчезает по выходу — снять ЦП/диск юнита, пока он жив
            try:
                c, r = self.unit_cpu_io(j)
                c0, r0 = self._last_own.get(j["id"], (0.0, 0))
                self._last_own[j["id"]] = (max(c, c0), max(r, r0))
            except Exception:
                pass
        if os.path.exists(p):
            return int(open(p).read().strip() or 0)
        if sh("systemctl", "is-active", self.unit(j)).stdout.strip() not in ("active", "activating"):
            return 143
        return None

    def units(self, pats):
        out = sh("systemctl", "list-units", "--type=service", "--state=active", "--no-legend", "--plain", *pats).stdout
        return [l.split()[0] for l in out.splitlines() if l.strip()]

    def freeze_all(self):
        us = [u for u in self.units(FREEZE_PAT) if not u.startswith("alpha-")
              and sh("systemctl", "show", "-p", "FreezerState", "--value", u).stdout.strip() != "frozen"]   # чужую заморозку (benchrun) не трогаем и не размораживаем
        for u in us:
            sh("systemctl", "freeze", u)
        json.dump(us, open(self.frozen_list, "w"))

    def thaw_all(self):
        sh("systemctl", "stop", "alpha-sm-failsafe.timer")
        if os.path.exists(self.frozen_list):
            for u in json.load(open(self.frozen_list)):
                sh("systemctl", "thaw", u)
            os.remove(self.frozen_list)

    def legacy_busy(self):
        """Ядер, занятых чужими (не из очереди) счётными юнитами: прирост CPUUsageNSec за интервал; кэш на ≥ TICK."""
        t = time.time()
        if t - self._t < TICK and self._legacy:
            return self._legacy
        tot = 0
        for u in self.units(LEGACY_PAT):
            if u.startswith("tk0s-"):
                continue
            v = sh("systemctl", "show", "-p", "CPUUsageNSec", "--value", u).stdout.strip()
            if v.isdigit():
                tot += (int(v) - self._cpu.get(u, int(v))) if u in self._cpu else 0
                self._cpu[u] = int(v)
        dt = max(t - self._t, 1e-3)
        self._legacy, self._t = tot / 1e9 / dt, t
        return self._legacy


# ---------- CLI ----------
def jpath(i):
    return f"{DIR}/jobs/{i}.json"


def load_all():
    os.makedirs(f"{DIR}/jobs", exist_ok=True)
    return [json.load(open(f"{DIR}/jobs/{f}")) for f in sorted(os.listdir(f"{DIR}/jobs")) if f.endswith(".json")]


def write_job(j):
    tmp = jpath(j["id"]) + ".tmp"
    json.dump(j, open(tmp, "w"), ensure_ascii=False)
    os.replace(tmp, jpath(j["id"]))


def parse_dur(s):
    m = {"s": 1, "m": 60, "h": 3600}
    return float(s[:-1]) * m[s[-1]] if s and s[-1] in m else float(s)


def submit(cls, name, cores, mem, disk, cwd, cmd, max_runtime, prio=5):
    os.makedirs(f"{DIR}/jobs", exist_ok=True)
    jid = time.strftime("%m%d%H%M%S") + f"{os.getpid() % 1000:03d}"
    j = dict(id=jid, name=name, cls=cls, cores=cores if cls == "prod" else NCPU, mem=mem, disk=disk, cwd=cwd, cmd=cmd,
             prio=prio, max_runtime=max_runtime, active_s=0, state="queued", t_submit=time.time(), cpus=[])
    jobs = load_all()
    free = NCPU - sum(len(x.get("cpus", [])) for x in jobs if x["state"] == "running")
    w = warn_for(j, free, any(x["state"] == "queued" and x["cls"] == "prod" for x in jobs))
    write_job(j)
    if w:
        print(w, file=sys.stderr)
    return j


def ps():
    now = time.time()
    rows = load_all()
    run = [j for j in rows if j["state"] == "running"]
    busy = sum(len(j["cpus"]) for j in run)
    print(f"ядра: занято {busy}/{NCPU} по заявкам; в очереди {sum(j['state'] == 'queued' for j in rows)}")
    for j in rows:
        if j["state"] == "done" and now - j.get("t_end", now) > 3600:
            continue
        age = now - (j.get("t_start") if j["state"] == "running" else j["t_submit"])
        print(f"{j['id']} {j['state']:7} {j['cls']:7} ядер={len(j['cpus']) or j['cores']:2} {j['name']:20} {age / 60:6.1f} мин "
              f"rc={j.get('rc', '')} {j['cmd'][:60]}")


def daemon():
    be = SystemdBackend()
    core = Core(be)
    be.thaw_all()
    seen = {}
    while True:
        for j in load_all():
            if j["id"] not in core.jobs:
                core.add(j)
        core.tick()
        for j in core.jobs.values():
            if seen.get(j["id"]) != (j["state"], j.get("rc")):
                seen[j["id"]] = (j["state"], j.get("rc"))
                write_job(j)
        time.sleep(TICK)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    if a[0] in ("wave", "stand"):
        if len(a) < 4 or a[1] != "--max-runtime":
            print(f"alsched.py {a[0]} --max-runtime <срок: 90s|30m|2h> команда… (срок обязателен: окно замера = аренда с TTL)")
            return 2
        j = submit("measure", a[0], NCPU, 56, "none", os.getcwd(), " ".join(map(shell_quote, a[3:])), parse_dur(a[2]), prio=0)
        log = f"{DIR}/logs/{j['id']}.log"
        pos = 0
        while True:
            cur = next(x for x in load_all() if x["id"] == j["id"])
            if os.path.exists(log):
                with open(log, "rb") as f:
                    f.seek(pos)
                    d = f.read()
                    pos += len(d)
                    sys.stdout.buffer.write(d)
                    sys.stdout.flush()
            if cur["state"] == "done":
                v = cur.get("valid") or {}
                if v and not v["ok"]:
                    print("ВОЛНА НЕДЕЙСТВИТЕЛЬНА: " + "; ".join(v["why"]), file=sys.stderr)
                return cur["rc"] if not (v and not v["ok"]) else (cur["rc"] or 3)
            time.sleep(1)
    if a[0] == "ps":
        return ps() or 0
    if a[0] == "cancel":
        os.makedirs(f"{DIR}/cancel", exist_ok=True)
        open(f"{DIR}/cancel/{a[1]}", "w").close()
        return 0
    if a[0] == "thaw":     # страховочный таймер аренды и ручная разморозка
        SystemdBackend().thaw_all()
        return 0
    if a[0] == "daemon":
        return daemon()
    if a[0] == "submit":
        sep = a.index("--")
        p = argparse.ArgumentParser()
        p.add_argument("--name", required=True)
        p.add_argument("--max-runtime", required=True, help="срок активной работы: 90s|30m|2h (без времени под заморозкой)")
        p.add_argument("--cls", default="prod", choices=["prod", "measure"])
        p.add_argument("--cores", type=int, default=1)
        p.add_argument("--mem", type=int, default=8)
        p.add_argument("--disk", default="none")
        p.add_argument("--cwd", default=os.getcwd())
        p.add_argument("--prio", type=int, default=5)
        o = p.parse_args(a[1:sep])
        j = submit(o.cls, o.name, o.cores, o.mem, o.disk, o.cwd, " ".join(map(shell_quote, a[sep + 1:])), parse_dur(o.max_runtime), o.prio)
        print(j["id"])
        return 0
    print(__doc__)
    return 2


def shell_quote(s):
    import shlex
    return shlex.quote(s)


if __name__ == "__main__":
    sys.exit(main())
