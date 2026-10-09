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
        self.alerts = []
        self.legacy_u = {}
        self.fz = set()
        self.cpu = {}
        self.cpu_rate = {}
        self.tickets = []
        self.mem_used = {}

    def job_cpu(self, j): return self.cpu.get(j["id"], 0.0)
    def waiting_tickets(self): return self.tickets
    def now(self): return self.t
    def alert(self, t): self.alerts.append(t)
    def cancelled(self, j): return False
    def legacy_busy(self): return self.legacy + sum(c for u, c in self.legacy_u.items() if u not in self.fz)
    def unit(self, j): return j["id"]
    def load_preempt(self): return {}
    def save_preempt(self, d): pass
    def freeze_unit(self, u): self.fz.add(u)
    def thaw_unit(self, u): self.fz.discard(u)
    def legacy_units(self): return [(u, c) for u, c in self.legacy_u.items() if u not in self.fz]
    def start(self, j): self.jobs[j["id"]] = dict(left=j["dur"])
    def freeze_all(self): self.frozen = True
    def thaw_all(self): self.frozen = False

    def kill(self, j): self.jobs[j["id"]]["left"] = 0; self.killed.append(j["id"])
    def extend(self, j, remaining): pass
    mem_av, mem_swap = 36.0, 0.0
    def mem_state(self): return self.mem_av, self.mem_swap
    def job_mem(self, j): return self.mem_used.get(j["id"], 0.0)
    def win_begin(self, j): return None
    def win_end(self, j, s): return dict(ok=True, why=[])

    def busy_fact(self):
        return min(1.0, sum(self.cpu_rate.get(j, 0) for j, v in self.jobs.items() if v["left"] > 0 and j not in self.fz) / NCPU) if not self.frozen else 0.0

    def done(self, j):
        return 0 if self.jobs[j["id"]]["left"] <= 0 else None

    def advance(self, core, dt):
        for j in core.jobs.values():
            if j["state"] == "running" and (j["cls"] == "measure" or not self.frozen) and j["id"] not in self.fz:
                self.jobs[j["id"]]["left"] -= dt
                self.cpu[j["id"]] = self.cpu.get(j["id"], 0.0) + dt * self.cpu_rate.get(j["id"], j["cores"])
        self.t += dt


def job(i, name, cls, cores, mem, disk, dur, t, mr=None):
    return dict(max_runtime=mr or dur * 2, active_s=0, id=i, name=name, cls=cls, cores=cores if cls == "prod" else NCPU, mem=mem, disk=disk, cmd="", cwd="/",
                prio=0 if cls == "measure" else 5, state="queued", t_submit=t, cpus=[], dur=dur)


def wide_behind_narrow(prio_wide):
    """Случай 21:58: 16 узких идут, 100 узких в очереди (prio 5), позже подана широкая на 4 ядра. Backfill: широкая (prio выше)
    стартует, когда освободятся 4 ядра, узкие не отодвигают её старт."""
    be = SimBE(); core = S.Core(be, ncpu=NCPU, mem=56)
    for i in range(116):
        core.add(job("n%03d" % i, "narrow", "prod", 1, 2, "none", 600, 0, mr=1200))
    wide = job("wide", "wide", "prod", 4, 8, "none", 900, 60, mr=3600); wide["prio"] = prio_wide
    for _ in range(int(7200 / S.TICK)):
        if be.t == 60:
            wide["t_submit"] = be.t; core.add(wide)
        core.tick(); be.advance(core, S.TICK)
        if wide["state"] != "queued":
            break
    return wide.get("t_start", 9e9) - 60, be.alerts


def preempt_case(prio=3):
    """Случай 22:35: R1 (12 по квоте) + retry (3) + одна узкая держат машину; головная 4 ядра prio 3 не стартует ≥ PREEMPT_S —
    замораживаются узкая и retry (не R1), головная стартует; по её концу всё оттаивает."""
    be = SimBE(); be.legacy_u = {"R1": 12, "retry": 3}
    core = S.Core(be, ncpu=NCPU, mem=56)
    core.add(job("n0", "narrow", "prod", 1, 2, "none", 10 ** 6, 0, mr=10 ** 7))
    wide = job("wide", "wide", "prod", 4, 8, "none", 900, 0, mr=3600); wide["prio"] = prio
    core.add(wide)
    t_start = t_thaw = None
    for _ in range(int(5400 / S.TICK)):
        core.tick(); be.advance(core, S.TICK)
        if prio == 5 and be.fz and not "n0" in be.fz:
            return None, be.fz, None, wide["state"], None
        if t_start is None and wide["state"] == "running":
            t_start = be.t
            frozen_then = set(be.fz)
        if t_start and not be.fz and t_thaw is None:
            t_thaw = be.t
    if prio == 5:
        return t_start, set(be.fz), None, wide["state"], None
    return t_start, frozen_then, t_thaw, wide["state"], core.jobs["n0"].get("frozen_for")


def underuse_case():
    """п.8(б): заявка на 8 ядер занимает 2 (25 %) — сигнал после UNDER_S; заявка на 8 занимает 7 — сигнала нет; заморозка выборку сбрасывает."""
    be = SimBE(); core = S.Core(be, ncpu=NCPU, mem=56)
    be.cpu_rate = {"low": 2, "ok": 7}
    core.add(job("low", "low", "prod", 8, 4, "none", 10 ** 6, 0, mr=10 ** 7))
    core.add(job("ok", "ok", "prod", 8, 4, "none", 10 ** 6, 0, mr=10 ** 7))
    t_alert = None
    for _ in range(int(7200 / S.TICK)):
        core.tick(); be.advance(core, S.TICK)
        if t_alert is None and any("low" in a and "занято" in a for a in be.alerts):
            t_alert = be.t
    return t_alert, [a for a in be.alerts if "занято" in a]


def idle_case():
    """п.8(в): очередь пуста, тикеты ждут — сигнал после IDLE_S, один раз; пока тикетов нет — тишина."""
    be = SimBE(); core = S.Core(be, ncpu=NCPU, mem=56)
    t_alert = None
    for _ in range(int(3600 / S.TICK)):
        if be.t == 600:
            be.tickets = ["TK-099"]
        core.tick(); be.advance(core, S.TICK)
        if t_alert is None and any("очередь пуста" in a for a in be.alerts):
            t_alert = be.t
    quiet = SimBE(); qc = S.Core(quiet, ncpu=NCPU, mem=56)
    for _ in range(int(3600 / S.TICK)):
        qc.tick(); quiet.advance(qc, S.TICK)
    return t_alert, [a for a in be.alerts if "очередь пуста" in a], quiet.alerts


def honest_case():
    """v2 п.1 (замечание Судьи 4): 3×4 честных (едят по 4, busy 0,75) + честное на 8 → ждёт (спрос 20/16); на 4 → идёт (16/16 не лезет → ждёт тоже)."""
    be = SimBE(); core = S.Core(be, ncpu=NCPU, mem=56, pack="fact")
    for i in range(3):
        j = job("h%d" % i, "h%d" % i, "prod", 4, 2, "none", 3600, 0); core.add(j); be.cpu_rate[j["id"]] = 4
    for _ in range(40):
        core.tick(); be.advance(core, S.TICK)
    w = job("w8", "w8", "prod", 8, 2, "none", 3600, be.t); core.add(w); be.cpu_rate["w8"] = 8
    for _ in range(60):
        core.tick(); be.advance(core, S.TICK)
    return w["state"], any(r.get("unpinned") for r in core.running("prod"))


def fact_case(pack):
    """v2 п.1: 12 заданий, заявлено 4 ядра, едят по 1; в claim идут 4 (16/4), в fact — 11 (12-е: 11 едят + 4 заявленных > 0,9·16 — по условию Судьи)."""
    be = SimBE(); core = S.Core(be, ncpu=NCPU, mem=56, pack=pack)
    for i in range(12):
        j = job("f%02d" % i, "f%d" % i, "prod", 4, 2, "none", 3600, 0)
        core.add(j); be.cpu_rate[j["id"]] = 1
    for _ in range(180):                                      # 15 мин
        core.tick(); be.advance(core, S.TICK)
    return len(core.running("prod"))


def mem_case():
    """v2 п.1: 20 заданий × 1 ядро, заявлено 4 ГБ, факт 1,2 ГБ, MemAvailable 27 — недобор считается только для молодых (< MEM_RAMP_S): за 15 мин идут все 16 ядер."""
    be = SimBE(); be.mem_av = 27.0; core = S.Core(be, ncpu=NCPU, mem=56, pack="fact")
    for i in range(20):
        j = job("m%02d" % i, "m%d" % i, "prod", 1, 4, "none", 3600, 0)
        core.add(j); be.cpu_rate[j["id"]] = 1; be.mem_used[j["id"]] = 1.2
    for _ in range(180):
        core.tick(); be.advance(core, S.TICK)
    return len(core.running("prod"))


def seq_case():
    """v2 п.2: два seq на одном диске — второй ждёт; seq на другом диске и не-seq на том же — идут."""
    be = SimBE(); core = S.Core(be, ncpu=NCPU, mem=56)
    for i, (d, io) in enumerate([("hdd1", "seq"), ("hdd1", "seq"), ("hdd2", "seq"), ("hdd1", "")]):
        j = job("s%d" % i, "s%d" % i, "prod", 1, 2, d, 3600, 0); j["io"] = io; core.add(j)
    core.tick()
    return sorted(j["id"] for j in core.running("prod"))


def iso_end_case():
    """09.10: iso_end ставит полный список ядер и читает effective назад; залипшее — 3 попытки и строка в alerts."""
    be, calls, al = S.SystemdBackend.__new__(S.SystemdBackend), [], []
    full, stuck = set(range(S.NCPU)), {"system.slice": 0}
    be.alert = al.append
    be.slice_cpus = lambda u: full if (u != "system.slice" or stuck["system.slice"] >= 2) else set(range(4))
    old = S.sh
    def fake(*a, **k):
        calls.append(a)
        if a[-1].startswith("AllowedCPUs") and a[-2] == "system.slice":
            stuck["system.slice"] += 1
        class R: stdout = ""
        return R
    S.sh = fake
    try:
        be.iso_end()
        n_ok, a_ok = len(calls), list(al)
        stuck["system.slice"] = -99
        be.slice_cpus = lambda u: set(range(4))
        calls.clear()
        be.iso_end()
    finally:
        S.sh = old
    return n_ok, a_ok, len(calls), len(al)


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
    small_bytes = dict(good, wall_s=634, cpu_s=10144, own_cpu_s=10140, disk_b=16.15e6, own_disk_b=11.26e6, ios=597, own_ios=201)      # окно 08.10: 30 % байт, 0,08 с диска
    bad_ios = dict(good, disk_b=1.001e9, ios=9000)       # байт +0,1 %, операций +11 %: поиски HDD
    bad_thaw = dict(good, forced_thaw=True)
    bad_swap = dict(good, swap_pages=120)
    peak_ok = S.peak_counts(300, 20.0, 0.0) and not S.peak_counts(25, 0.01, 0.0) and not S.peak_counts(300, 5.0, 8.0)
    BG = 0.75
    bg_only = dict(good, wall_s=60, cpu_s=960, own_cpu_s=958, ios=300 + int(BG * 60), own_ios=300, disk_b=1e9 + 180000)     # тёплая: фон без чужих
    bg_plus = dict(bg_only, ios=bg_only["ios"] + 60)                                              # фон + чужие 4К (60 × 21,8 мс / 60 с ≈ 2,2 % времени диска)
    chk = [S.judge_window(good)[0], S.judge_window(small_bytes, bg_ops_s=0.354)[0], not S.judge_window(bad_cpu)[0], not S.judge_window(bad_disk)[0],
           not S.judge_window(bad_unit)[0],
           not S.judge_window(bad_ios)[0], not S.judge_window(bad_thaw)[0], not S.judge_window(bad_swap)[0], peak_ok, S.judge_window(dict(good, swap_pages=0))[0], S.judge_window(dict(good, swap_pages=5, swap_slice=0))[0], not S.judge_window(dict(good, swap_pages=0, swap_slice=4096))[0],
           S.judge_window(bg_only, bg_ops_s=BG)[0], not S.judge_window(bg_plus, bg_ops_s=BG)[0],
           not S.judge_window(bg_only, bg_ops_s=0.0)[0],
           not S.mem_risk(36.0, 0.0, 2.54), len(S.mem_risk(36.0, 7.0, 2.54)) == 1, len(S.mem_risk(1.0, 0.0, 2.54)) == 1, not S.mem_risk(1.0, 0.0, 0.0),
           any("кто читал диск" in w and "session-1.scope 900 оп" in w for w in S.judge_window(dict(bad_ios, culprits=[dict(cgroup="/user.slice/session-1.scope", ops=900, mb=3.6)]))[1])]
    print("проверка волны (годна/ЦП/диск/чужой юнит/чужие операции/страховочная разморозка/своп в окне — нет/нулевой своп — годна/пик памяти только с настоящей волны/фон без чужих — годна/фон+чужие — нет/без вычета фона — нет/назван виновник):", chk)
    kb = SimBE(); kc = S.Core(kb, ncpu=NCPU, mem=56)
    kc.add(job("long", "long", "prod", 2, 2, "none", 10 * H, 0, mr=1800)); kc.add(job("w", "wave", "measure", NCPU, 1, "none", 600, 0, mr=300))
    for _ in range(600):
        kc.tick(); kb.advance(kc, S.TICK)
    killed_ok = set(kb.killed) == {"long", "w"}
    print("max_runtime: убиты", sorted(kb.killed), "ок" if killed_ok else "ПРОВАЛ")
    ok = all(chk) and killed_ok and all(w <= 120 for w in waits.values()) and util >= 0.8 and meas_overlap == 0
    w3, _ = wide_behind_narrow(3)
    w5, al = wide_behind_narrow(5)
    print(f"широкая 4 ядра за 100 узкими: prio 3 стартовала через {w3:.0f} с после подачи; prio 5 (FIFO) — {w5:.0f} с, сигнал ожидания: {len(al)}")
    ts, fr, tt, st, ff = preempt_case()
    print(f"вытеснение: головная стартовала на {ts} с (порог {S.PREEMPT_S}), заморожено {sorted(fr)}, оттаяло на {tt} с, итог {st}")
    t5, fz5, _, _, _ = preempt_case(5)
    print(f"prio 5 (равна R1): голые юниты не заморожены: {not (fz5 - {'n0'})}")
    ok = ok and not (fz5 - {"n0"}) and w3 <= 600 + 2 * S.TICK and len(al) >= 1
    ok = ok and ts is not None and ts <= S.PREEMPT_S + 3 * S.TICK and fr == {"n0", "retry"} and tt and st == "done" and not ff
    tu, au = underuse_case()
    ti, ai, aq = idle_case()
    print(f"недогруз: сигнал на {tu} с (окно {S.UNDER_S}), только на «low»: {len(au) == 1}; пустая очередь при ждущих тикетах: сигнал на {ti} с, штук {len(ai)}, без тикетов тишина: {not aq}")
    ok = ok and tu is not None and S.UNDER_S <= tu <= S.UNDER_S + 2 * S.SAMPLE_S and len(au) == 1
    ok = ok and ti is not None and 600 + S.IDLE_S <= ti <= 600 + S.IDLE_S + 2 * S.TICK and len(ai) == 1 and not aq
    nc, nf, sq, hs = fact_case("claim"), fact_case("fact"), seq_case(), honest_case()
    mc = mem_case()
    print(f"v2 память: заявка 4 ГБ, факт 1,2, MemAvailable 27 — идут {mc} из 16 ядер")
    ok = ok and mc == 16
    print(f"v2 пакование: заявка 4 ядра, факт 1 — claim идут {nc}, fact идут {nf}; seq-диск: идут {sq}; 3×4 честных + честное 8: {hs[0]}, сверх заявки {hs[1]}")
    ok = ok and nc == 4 and nf == 11 and sq == ["s0", "s2", "s3"] and hs == ("queued", False)
    ie = iso_end_case()
    print(f"iso_end: залипший слайс — вызовов {ie[0]} без тревоги {not ie[1]}; не вернулся совсем — вызовов {ie[2]}, тревог {ie[3]}")
    ok = ok and ie == (4, [], 9, 3)
    print("ГЕЙТ:", "ок" if ok else "ПРОВАЛ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
