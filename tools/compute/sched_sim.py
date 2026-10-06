#!/usr/bin/env python3
"""Макет очереди (TK-071): сегодняшний сценарий 06.10 на виртуальном времени, ядро — то же `sched.Core`.
Проверки гейта: волна/стенд, поданные при свободной очереди замеров, стартуют ≤ 120 с; загрузка ядер при наличии
производства ≥ 80 % (вне окон замера); помеха замеру (производство не на паузе во время замера) = 0 с."""
import sys
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alsched as S

NCPU = 16


class SimBE:
    def __init__(self):
        self.t = 0.0
        self.jobs = {}
        self.frozen = False
        self.legacy = 0.0
        self.started = []
        self.killed = []

    def now(self): return self.t
    def cancelled(self, j): return False
    def legacy_busy(self): return self.legacy
    def start(self, j): self.jobs[j["id"]] = dict(left=j["dur"])
    def freeze_all(self): self.frozen = True
    def thaw_all(self): self.frozen = False

    def kill(self, j): self.jobs[j["id"]]["left"] = 0; self.killed.append(j["id"])
    def extend(self, j, remaining): pass
    def win_begin(self, j): return None
    def win_end(self, j, s): return dict(ok=True, why=[])

    def done(self, j):
        return 0 if self.jobs[j["id"]]["left"] <= 0 else None

    def advance(self, core, dt):
        for j in core.jobs.values():
            if j["state"] == "running" and (j["cls"] == "measure" or not self.frozen):
                self.jobs[j["id"]]["left"] -= dt
        self.t += dt


def job(i, name, cls, cores, mem, disk, dur, t, mr=None):
    return dict(max_runtime=mr or dur * 2, active_s=0, id=i, name=name, cls=cls, cores=cores if cls == "prod" else NCPU, mem=mem, disk=disk, cmd="", cwd="/",
                prio=0 if cls == "measure" else 5, state="queued", t_submit=t, cpus=[], dur=dur)


def run():
    be = SimBE()
    core = S.Core(be, ncpu=NCPU, mem=56, disk_slots=int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    H = 3600
    plan = [  # (подача, задание)
        (0, job("r1m%02d" % m, "R1-m%d" % m, "prod", 2, 6, "hdd1" if m % 2 else "hdd2", 2 * H, 0)) for m in range(10)
    ] + [
        (0, job("opis", "opis-bazy", "prod", 1, 2, "hdd1", 1800, 0)),
        (1800, job("build", "sborka-b15", "prod", 4, 8, "hdd2", 1500, 1800)),
        (2400, job("wave1", "wave", "measure", NCPU, 40, "none", 300, 2400)),
        (3600, job("wave2", "wave", "measure", NCPU, 40, "none", 300, 3600)),
        (4200, job("stand", "stand", "measure", NCPU, 40, "none", 600, 4200)),
    ]
    pending = sorted(plan, key=lambda p: p[0])
    busy_s = prod_present_s = meas_overlap = 0.0
    dt = S.TICK
    end = 8 * H
    while be.t < end:
        while pending and pending[0][0] <= be.t:
            j = pending.pop(0)[1]
            j["t_submit"] = be.t
            core.add(j)
        core.tick()
        has_prod = sum(j["cores"] for j in core.jobs.values() if j["cls"] == "prod" and j["state"] in ("queued", "running")) >= NCPU
        meas = bool(core.running("measure"))
        if has_prod and not meas:
            prod_present_s += dt
            busy_s += sum(len(j["cpus"]) for j in core.running("prod")) * dt / NCPU
        if meas and not be.frozen and core.running("prod"):
            meas_overlap += dt
        be.advance(core, dt)
    waits = {j["id"]: j["t_start"] - j["t_submit"] for j in core.jobs.values() if j["cls"] == "measure"}
    util = busy_s / prod_present_s if prod_present_s else 0
    print("ожидание замеров, с:", {k: round(v) for k, v in waits.items()})
    print(f"загрузка ядер при спросе производства ≥ 16 ядер (вне окон замера): {util * 100:.1f} %")
    print(f"помеха замерам (производство не на паузе в окне): {meas_overlap:.0f} с")
    print("R1 закончен:", all(j["state"] == "done" for j in core.jobs.values()), f"в {be.t / H:.1f} ч модельного времени")
    good = dict(wall_s=300, cpu_s=4800, own_cpu_s=4790, disk_b=1e9, own_disk_b=1e9, ios=8000, own_ios=8000, foreign_units=set())
    bad_cpu = dict(good, cpu_s=4800 + 300 * 16 * 0.02, own_cpu_s=4800)
    bad_disk = dict(good, disk_b=1.2e9)
    bad_unit = dict(good, foreign_units={"tk064-pool-chain.service"})
    bad_ios = dict(good, disk_b=1.001e9, ios=9000)       # байт +0,1 %, операций +11 %: поиски HDD
    bad_thaw = dict(good, forced_thaw=True)
    bad_swap = dict(good, swap_pages=120)
    peak_ok = S.peak_counts(300, 20.0, 0.0) and not S.peak_counts(25, 0.01, 0.0) and not S.peak_counts(300, 5.0, 8.0)
    BG = 0.75
    bg_only = dict(good, wall_s=60, cpu_s=960, own_cpu_s=958, ios=300 + int(BG * 60), own_ios=300, disk_b=1e9 + 180000)     # тёплая: фон без чужих
    bg_plus = dict(bg_only, ios=bg_only["ios"] + 60)                                              # фон + чужие 4К (60 × 21,8 мс / 60 с ≈ 2,2 % времени диска)
    chk = [S.judge_window(good)[0], not S.judge_window(bad_cpu)[0], not S.judge_window(bad_disk)[0],
           not S.judge_window(bad_unit)[0],
           not S.judge_window(bad_ios)[0], not S.judge_window(bad_thaw)[0], not S.judge_window(bad_swap)[0], peak_ok, S.judge_window(dict(good, swap_pages=0))[0],
           S.judge_window(bg_only, bg_ops_s=BG)[0], not S.judge_window(bg_plus, bg_ops_s=BG)[0],
           not S.judge_window(bg_only, bg_ops_s=0.0)[0],
           any("кто читал диск" in w and "session-1.scope 900 оп" in w for w in S.judge_window(dict(bad_ios, culprits=[dict(cgroup="/user.slice/session-1.scope", ops=900, mb=3.6)]))[1])]
    print("проверка волны (годна/ЦП/диск/чужой юнит/чужие операции/страховочная разморозка/своп в окне — нет/нулевой своп — годна/пик памяти только с настоящей волны/фон без чужих — годна/фон+чужие — нет/без вычета фона — нет/назван виновник):", chk)
    kb = SimBE(); kc = S.Core(kb, ncpu=NCPU, mem=56)
    kc.add(job("long", "long", "prod", 2, 2, "none", 10 * H, 0, mr=1800)); kc.add(job("w", "wave", "measure", NCPU, 1, "none", 600, 0, mr=300))
    for _ in range(600):
        kc.tick(); kb.advance(kc, S.TICK)
    killed_ok = set(kb.killed) == {"long", "w"}
    print("max_runtime: убиты", sorted(kb.killed), "ок" if killed_ok else "ПРОВАЛ")
    ok = all(chk) and killed_ok and all(w <= 120 for w in waits.values()) and util >= 0.8 and meas_overlap == 0
    print("ГЕЙТ:", "ок" if ok else "ПРОВАЛ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
