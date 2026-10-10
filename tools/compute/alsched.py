#!/usr/bin/env python3
"""alsched.py (TK-071, В-189): планировщик сервера счёта вместо замка-флока benchrun.

Заявка: класс prod (производство: ядра/память/диск по заявке, пакуется на свободные ядра) или measure (замер: эксклюзивное
окно — всё производство на freeze, замер получает все ядра, по выходу размораживается). Всё запускается через него.

  alsched.py submit --name N --max-runtime 2h [--cls prod|measure] [--cores 4] [--mem 8] [--disk hdd1|hdd2|none] [--cwd D] [--recompute --why ТЕКСТ] [--repeat N] [--vypiska ФАЙЛ_ШАГОВ] [--adhoc ПРИЧИНА] -- команда…
  alsched.py wave|stand --max-runtime 30m команда…   # = замер: подать, дождаться окна, показать вывод, вернуть код (обёртка вместо benchrun)
                                                    #   stand по умолчанию --iso 4 (без заморозки производства); --exclusive — заморозка всего; --iso K — другое K; wave — всегда заморозка
  alsched.py ps | cancel <id> [причина] | reprio <id> <prio> | daemon
Состояние: $SCHED_DIR (/data/sched): jobs/<id>.json (создаёт CLI, дальше пишет только демон), cancel/<id>, logs/<id>.log, rc/<id>.
"""
import argparse, json, math, os, re, subprocess, sys, time

from sched_cfg import (DIR, RUNS_DIR, NCPU, MEM_GB, TICK, DEAD_S, PREEMPT_S, DISK_SLOTS, FREEZE_PAT, LEGACY_PAT, PACK, FACT_BUSY, FACT_SETTLE_S, PROD_CAP_ISO, HOT_BUSY, PSI_CPU, MEM_RAMP_S, FACT_MEM_GAP_GB)   # noqa: F401 — вся конфигурация SCHED_* в sched_cfg.py (С-57)
WAIT_ALERT_S = 600
UNDER_S = 1800     # п.8(б): окно недогруза заявки
UNDER_FRAC = 0.5   # «заметно меньше» объявленного — меньше половины заявленных ядер
IDLE_S = 600       # п.8(в): очередь пуста при ждущих тикетах дольше этого
SAMPLE_S = 120
BARE_PRIO = 5      # голые юниты (R1) приравнены к prio 5: вытесняются только заявками строже (prio < 5)


def mem_peak_gb():
    try:
        return float(open(f"{DIR}/wave_mem_peak_gb").read())
    except (OSError, ValueError):
        return 0.0


def prod_mem_budget_gb():
    """Память производства = MemTotal − измеренный пик волны (замороженное производство остаётся в памяти, пока идёт волна); пик не измерен → MEM_GB."""
    p = mem_peak_gb()
    if p <= 0:
        return MEM_GB
    total = int(open("/proc/meminfo").readline().split()[1]) // 2**20
    return max(0, int(total - math.ceil(p)))


def mem_risk(avail_gb, swap_used_gb, peak_gb):
    """Перед окном замера: что может увести его в своп. Своп в окне = окно недействительно (judge_window), а занятый своп хост вернёт в память при первом касании страницы; нехватка available против измеренного пика волны — второй путь. Только предупреждение: старт не блокируем (занятый своп сам не освободится)."""
    r = []
    if swap_used_gb > 0.05:
        r.append(f"своп занят {swap_used_gb:.1f} ГБ — страницы вернутся при касании, своп в окне сделает замер недействительным")
    if peak_gb > 0 and avail_gb < peak_gb:
        r.append(f"MemAvailable {avail_gb:.1f} ГБ < пика волны {peak_gb:.1f} ГБ")
    return r


def peak_counts(wall_s, gb, prev_gb):
    """Пик памяти волны идёт в бюджет производства только с настоящей волны (окно ≥ MIN_WALL_S), иначе проба sleep 20 занизит пик и раздует бюджет."""
    return wall_s >= MIN_WALL_S and gb > prev_gb


class Core:
    """Решения без побочных эффектов: be — бэкенд (systemd или макет)."""

    def __init__(self, be, ncpu=NCPU, mem=MEM_GB, disk_slots=DISK_SLOTS, pack=None):
        self.be, self.ncpu, self.mem, self.slots = be, ncpu, mem, disk_slots
        self.pack = pack or PACK
        self.fact_t = -1e9
        self.fam_cores, self.fam_mem = {}, {}             # допуск по факту: ядра (EWMA) и пик памяти заданий семьи (имя без последнего «-…»)
        self.hot_t = None
        self.jobs = {}
        self.iso = set()                                  # ядра изолированного замера: производству недоступны
        self.frozen = False
        self.last = None
        self.snaps = {}
        self.preempt = be.load_preempt()                  # {id головной: [[вид, имя/id, ядер], ...]} — заморожено ради неё

    def add(self, j):
        self.jobs.setdefault(j["id"], j)

    def running(self, cls):
        return [j for j in self.jobs.values() if j["state"] == "running" and j["cls"] == cls]

    def tick(self):
        now = self.be.now()
        dt = 0 if self.last is None else now - self.last
        self.last = now
        self._budgets(now, dt)
        self._reap(now)
        if self._window(now):          # окно замера занято/открыто — упаковка производства в этот цикл не идёт
            return
        self._thaw_after_window()
        legacy = self._pack(now)
        self._alerts(now)
        self.log_util(now, legacy)

    def _budgets(self, now, dt):
        be = self.be
        for hid, vs in self.preempt.items():
            for kind, ident, _ in vs:
                if kind == "job" and ident in self.jobs:
                    self.jobs[ident]["frozen_for"] = hid
        for j in self.running("prod"):                    # бюджет max_runtime — время БЕЗ заморозки
            if not self.frozen and not j.get("frozen_for"):
                j["active_s"] = j.get("active_s", 0) + dt
            if j["active_s"] > j["max_runtime"]:
                j["kill_why"] = "снят демоном: бюджет max-runtime (активное время) вышел"
                be.kill(j)
        self.iso = {c for j in self.running("measure") for c in j.get("iso_cpus", [])}   # ядра изолированного замера (п.3)
        for j in self.running("measure"):                 # замер не замораживается: бюджет = стена
            if now - j["t_start"] > j["max_runtime"]:
                j["kill_why"] = "снят демоном: бюджет max-runtime замера (стена) вышел"
                be.kill(j)

    def _reap(self, now):
        be = self.be
        for j in self.jobs.values():
            if j["state"] == "queued" and be.cancelled(j):
                j.update(state="done", rc=-15, t_end=now, reason="снят из очереди: " + (be.cancel_why(j) if hasattr(be, "cancel_why") else "cancel"))
            elif j["state"] == "running":
                rc = be.done(j)
                if rc is not None:
                    j.update(state="done", rc=rc, t_end=now, cpus=[])
                    if rc != 0 and hasattr(be, "fail_reason"):      # TK-117 К2.1: падение не тихое — причина в json и в alerts.log
                        try:
                            j["reason"] = be.fail_reason(j, rc)
                        except Exception as e:
                            j["reason"] = f"rc {rc}, причина не прочитана: {e}"
                        if j.get("kill_why"):
                            j["reason"] = j["kill_why"] + "; " + j["reason"]
                        j["failed"] = True
                        be.alert(f"ЗАДАНИЕ УПАЛО {j['id']} {j['name']} {ticket_of(j['name'])} rc={rc}: {j['reason']}".replace("  ", " "))
                    if j.get("iso_cpus") and hasattr(be, "iso_end"):
                        be.iso_end()
                        self.iso = set()
                    if j["cls"] == "measure" and j["id"] in self.snaps:
                        j["valid"] = be.win_end(j, self.snaps.pop(j["id"]))
                    elif j["cls"] == "measure" and not j.get("valid"):
                        j["valid"] = dict(ok=False, why=["нет снимка начала окна (демон перезапущен или окно не открыто) — стена недостоверна"])

    def _window(self, now):
        be = self.be
        ms = self.running("measure")
        if any(not j.get("iso") for j in ms):
            self.log_util(now, 0)                         # окно видно в util.log: measure=1 раз в минуту
            return True
        mq = sorted((j for j in self.jobs.values() if j["state"] == "queued" and j["cls"] == "measure"),
                    key=lambda j: j["t_submit"])
        if mq and mq[0].get("iso") and not ms and not self.frozen:
            j = mq[0]                                     # п.3: замер на K физ. ядрах, производство идёт на остальных, без заморозки
            cpus = be.iso_cpus(j["iso"])
            av, su = be.mem_state()
            risk = mem_risk(av, su, 0.0)
            if risk:
                be.alert(f"память перед изолированным окном {j['id']} {j['name']}: " + "; ".join(risk))
            be.iso_begin(cpus)
            j.update(state="running", t_start=now, cpus=cpus, iso_cpus=cpus)
            self.iso = set(cpus)
            self.snaps[j["id"]] = be.win_begin(j)
            be.start(j)
        elif mq and not mq[0].get("iso") and not ms:
            if not self.frozen:
                be.freeze_all()
                self.frozen = True
            j = mq[0]
            j.update(state="running", t_start=now, cpus=list(range(self.ncpu)))
            self.snaps[j["id"]] = be.win_begin(j)
            av, su = be.mem_state()
            risk = mem_risk(av, su, mem_peak_gb())
            if risk:
                be.alert(f"память перед окном {j['id']} {j['name']}: " + "; ".join(risk))
            be.start(j)
            self.log_util(now, 0)
            return True
        return False

    def _thaw_after_window(self):
        be = self.be
        if self.frozen:
            be.thaw_all()
            self.frozen = False
            for j in self.running("prod"):
                be.extend(j, j["max_runtime"] - j.get("active_s", 0))

    def _pack(self, now):
        be = self.be
        self.thaw_victims()
        self.cap_iso()
        used = {c for j in self.running("prod") if not j.get("frozen_for") for c in j["cpus"]} | self.iso
        mem = sum(j["mem"] for j in self.running("prod"))
        legacy = math.ceil(be.legacy_busy())
        for j in self.jobs.values():                      # приоритет можно менять на ходу: prio/<id> (alsched.py reprio)
            pj = f"{DIR}/prio/{j['id']}"
            if j["state"] == "queued" and os.path.exists(pj):
                try:
                    j["prio"] = int(open(pj).read().strip())
                except ValueError as e:
                    warn_once(f"prio-{j['id']}", f"prio/{j['id']}: не число ({e}) — приоритет не изменён")
        fact = self.pack == "fact"
        ncp = self.ncpu - len(self.iso)                   # П1: загрузка и ёмкость — по ядрам, доступным производству
        busy = (be.busy_fact(self.iso) if self.iso else be.busy_fact()) if fact and hasattr(be, "busy_fact") else 1.0
        self.learn(now)
        self.guard_load(now, busy, fact)
        capped = sum(len(r["cpus"]) for r in self.running("prod") if not r.get("frozen_for")) if self.iso else 0
        reserve = head = None                             # EASY-backfill: первой заблокированной по приоритету заявке держим место
        for j in sorted((j for j in self.jobs.values() if j["state"] == "queued"),
                        key=lambda j: (j.get("prio", 5), j["t_submit"])):
            free = [c for c in range(self.ncpu) if c not in used]
            room = len(free) - legacy
            disk_full = bool(self.slots and j["disk"] != "none"
                             and sum(1 for r in self.running("prod") if r["disk"] == j["disk"]) >= self.slots)
            if j.get("io") == "seq" and j["disk"] != "none" and any(
                    r["disk"] == j["disk"] and r.get("io") == "seq" for r in self.running("prod")):
                disk_full = True                          # п.2: HDD-тяжёлое последовательное чтение — не больше одного на диск
            over = False
            if fact:                                      # п.1: по факту — память: MemAvailable покрывает заявку и недобранное идущими
                mem_ok = self.mem_fits(j)
                over = (room < j["cores"] and (now - self.fact_t >= FACT_SETTLE_S or self.need_cores(j) < j["cores"] or j["cores"] == 1) and mem_ok
                        and busy * ncp + self.ramp(now) + self.need_cores(j) <= FACT_BUSY * ncp)
                fits = (room >= j["cores"] or over) and mem_ok and not disk_full
            else:
                fits = room >= j["cores"] and mem + j["mem"] <= self.mem and not disk_full
            if fits and self.iso and capped + j["cores"] > PROD_CAP_ISO:
                fits = False                              # окно открыто: заявленных ядер prod сверх потолка не пускаем
            if fits and reserve is not None and j["max_runtime"] > reserve["shadow"] and not over \
                    and (j["cores"] > reserve["cores"] or j["mem"] > reserve["mem"]):   # 10.10: влезшая по ФАКТУ (over) резервом по заявкам не держится (gate10: 25 мин host 0,6 при очереди 28)
                fits = False                              # заняла бы место первой заявки и не успела бы до её старта
            if not fits:
                if reserve is None and head is None and not disk_full:
                    head = j
                    reserve = self.reservation(j, room, mem, now)
                continue
            if reserve is not None and j["max_runtime"] > reserve["shadow"]:
                reserve["cores"] -= j["cores"]
                reserve["mem"] -= j["mem"]
            if over and room < j["cores"]:               # сверх заявленных: не пинить (ядро пина не переносится, факт-загрузка ядер неизвестна)
                self.fact_t = now
                load = {c: sum(1 for r in self.running("prod") if c in r["cpus"]) for c in range(self.ncpu)}
                cpus = sorted(sorted(range(self.ncpu), key=lambda c: (load[c], c))[: j["cores"]])   # только учёт
                j["unpinned"] = True
            else:
                cpus = free[: j["cores"]]
            j.update(state="running", t_start=now, cpus=cpus)
            used |= set(j["cpus"])
            mem += j["mem"]
            capped += len(j["cpus"]) if self.iso else 0
            be.start(j)
        if head and head["state"] == "queued" and head["id"] not in self.preempt and mem + head["mem"] <= self.mem:
            worse = any(r.get("prio", 5) > head.get("prio", 5) and not r.get("frozen_for") for r in self.running("prod"))
            if worse or now - head["t_submit"] > PREEMPT_S:    # 10.10 (Судья): prio строго лучше идущего — вытесняем в тот же цикл, без таймера
                self.preempt_for(head, len(range(self.ncpu)) - len(used) - legacy, now, bare=now - head["t_submit"] > PREEMPT_S)
        return legacy

    def _alerts(self, now):
        self.alert_waiting(now)
        self.alert_underuse(now)
        self.alert_idle(now)

    @staticmethod
    def family(j):
        return re.sub(r"-[^-]+$", "", j["name"])

    def learn(self, now):
        """Допуск по факту (Судья 09.10, п.3): нужда семьи = занятые ядра (EWMA по выборкам alert_underuse) и пик памяти заданий старше MEM_RAMP_S;
        заявка остаётся потолком. Семья без факта — по заявке."""
        for j in self.running("prod"):
            sm = j.get("cpu_samples") or []
            if j.get("frozen_for") or self.frozen or now - j["t_start"] < MEM_RAMP_S or len(sm) < 2 or sm[-1][0] - sm[0][0] < SAMPLE_S:
                continue
            f, rate = self.family(j), (sm[-1][1] - sm[0][1]) / (sm[-1][0] - sm[0][0])
            self.fam_cores[f] = rate if f not in self.fam_cores else self.fam_cores[f] + 0.3 * (rate - self.fam_cores[f])
            if hasattr(self.be, "job_mem"):
                self.fam_mem[f] = max(self.fam_mem.get(f, 0.0), self.be.job_mem(j))

    def cold_rate(self, f, now):
        """Семья без EWMA (10.10, Судья gate10): допуск по собственному факту запущенных — средняя занятость ядер с начала, возраст ≥ 2·FACT_SETTLE_S (Судья: 2–3 отсчёта); обновляется раз в FACT_SETTLE_S."""
        rs = []
        for r in self.running("prod"):
            age = now - r["t_start"]
            if self.family(r) != f or r.get("frozen_for") or self.frozen or age < 2 * FACT_SETTLE_S or not hasattr(self.be, "job_cpu"):
                continue
            if now - r.get("cold_t", -1e9) >= FACT_SETTLE_S:
                r["cold_t"] = now
                r["cold_r"] = self.be.job_cpu(r) / max(1.0, age)   # CPUUsage юнита накопительный с его старта
            if r.get("cold_r") is not None:
                rs.append(r["cold_r"])
        return sum(rs) / len(rs) if rs else None

    def need_cores(self, j):
        f = self.family(j)
        c = self.fam_cores.get(f)
        if c is None:
            c = self.cold_rate(f, self.be.now())
        return j["cores"] if c is None else min(j["cores"], max(1, math.ceil(c)))

    def need_mem(self, j):
        m = self.fam_mem.get(self.family(j))
        return j["mem"] if m is None else min(j["mem"], m)

    def guard_load(self, now, busy, fact):
        """Защита: загрузка ≥ HOT_BUSY дольше FACT_SETTLE_S — замораживается новейшее задание сверх заявок (unpinned); оттаивает, когда загрузка < FACT_BUSY."""
        hot = [j for j in self.running("prod") if j.get("frozen_for") == "hot"]
        if not fact:
            return
        psi = self.be.psi_cpu() if hasattr(self.be, "psi_cpu") else 100.0
        if busy >= HOT_BUSY and psi >= PSI_CPU:
            self.hot_t = self.hot_t if self.hot_t is not None else now
            if now - self.hot_t >= FACT_SETTLE_S:
                vs = [j for j in self.running("prod") if j.get("unpinned") and not j.get("frozen_for")]
                if vs:
                    v = max(vs, key=lambda j: j["t_start"])
                    v["frozen_for"] = "hot"
                    self.be.freeze_unit(self.be.unit(v))
                    self.hot_t = now
                    self.be.alert(f"перегруз {busy:.2f}, PSI cpu {psi:.0f} %: заморожено новейшее сверх заявок {v['id']} {v['name']}")
        else:
            self.hot_t = None
            v = min(hot, key=lambda j: j["t_start"]) if hot else None
            if v and busy * (self.ncpu - len(self.iso)) + self.need_cores(v) <= FACT_BUSY * (self.ncpu - len(self.iso)):   # оттаивать, только если влезет без нового перегруза (иначе заморозка-оттайка по кругу)
                v.pop("frozen_for")
                self.be.thaw_unit(self.be.unit(v))
                self.be.extend(v, v["max_runtime"] - v.get("active_s", 0))

    def cap_iso(self):
        """Потолок prod в изолированном окне: заявленных ядер незамороженных ≤ PROD_CAP_ISO, лишнее — заморозка новейших; оттаивают по концу окна."""
        be = self.be
        if not self.iso:
            for j in self.running("prod"):
                if j.get("frozen_for") == "cap":
                    j.pop("frozen_for")
                    be.thaw_unit(be.unit(j))
                    be.extend(j, j["max_runtime"] - j.get("active_s", 0))
            return
        live = sorted((j for j in self.running("prod") if not j.get("frozen_for")), key=lambda j: j["t_start"])
        tot = sum(len(j["cpus"]) for j in live)
        while live and tot > PROD_CAP_ISO:
            v = live.pop()
            tot -= len(v["cpus"])
            v["frozen_for"] = "cap"
            be.freeze_unit(be.unit(v))
            be.alert(f"потолок prod в окне ({PROD_CAP_ISO} ядер): заморожено {v['id']} {v['name']}")

    def ramp(self, now):
        """Недобор EWMA по свежим заданиям: EWMA с τ = 60 с видит лишь долю 1 − e^(−a/60) нагрузки задания возраста a;
        недостающее ≤ cores·e^(−a/60) (заявка — нижняя оценка, берём её). Старше 180 с — ноль."""
        return sum(min(r["cores"], r.get("cold_r") or r["cores"]) * math.exp(-(now - r["t_start"]) / 60) for r in self.running("prod")   # со своим фактом (cold_r) — он, не заявка
                   if not r.get("frozen_for") and now - r["t_start"] < 180)

    def mem_fits(self, j):
        """Память не эластична: MemAvailable (в нём и tmpfs) покрывает заявку j и недобранное идущими до их заявки (max(заявка, факт))."""
        av, _ = self.be.mem_state()
        now = self.be.now()
        gap = sum(max(0.0, r["mem"] - (self.be.job_mem(r) if hasattr(self.be, "job_mem") else 0.0)) for r in self.running("prod")
                  if now - r["t_start"] < MEM_RAMP_S)   # старше MEM_RAMP_S — факт уже в MemAvailable (23:08: по заявке 41 ГБ против факта 17,5 стоял весь счёт)
        return av >= self.need_mem(j) + gap + FACT_MEM_GAP_GB

    def preempt_for(self, head, room, now, bare=True):
        """Вытеснение заморозкой (Slurm PreemptMode=SUSPEND): резерв не стартует за PREEMPT_S — замораживаем идущие заявки
        с большим prio (новейшие первыми), затем голые юниты (по возрастанию ядер) ровно на нужные ядра; оттаивают по концу головной."""
        need = head["cores"] - room
        if need <= 0:
            return
        vs, got = [], 0
        for j in sorted((j for j in self.running("prod") if j.get("prio", 5) > head.get("prio", 5) and not j.get("frozen_for")),
                        key=lambda j: (-j.get("prio", 5), -j["t_start"])):
            if got >= need:
                break
            vs.append(["job", j["id"], len(j["cpus"])])
            got += len(j["cpus"])
        if got < need and bare and head.get("prio", 5) < BARE_PRIO:   # голые юниты — только после PREEMPT_S
            for u, c in sorted(self.be.legacy_units(), key=lambda x: x[1]):
                if got >= need:
                    break
                vs.append(["unit", u, c])
                got += c
        if got < need:
            return
        for kind, ident, _ in vs:
            if kind == "job":
                self.jobs[ident]["frozen_for"] = head["id"]
                self.be.freeze_unit(self.be.unit(self.jobs[ident]))
            else:
                self.be.freeze_unit(ident)
        self.preempt[head["id"]] = vs
        self.be.save_preempt(self.preempt)
        self.be.alert(f"вытеснение: {head['id']} {head['name']} ждала {int((now - head['t_submit']) / 60)} мин — заморожено "
                      + ", ".join(f"{k}:{i}({c})" for k, i, c in vs))

    def thaw_victims(self):
        for hid, vs in list(self.preempt.items()):
            h = self.jobs.get(hid)
            if h is not None and h["state"] != "done":
                continue
            for kind, ident, _ in vs:
                if kind == "job" and ident in self.jobs:
                    j = self.jobs[ident]
                    j.pop("frozen_for", None)
                    self.be.thaw_unit(self.be.unit(j))
                    self.be.extend(j, j["max_runtime"] - j.get("active_s", 0))
                else:
                    self.be.thaw_unit(ident)
            del self.preempt[hid]
            self.be.save_preempt(self.preempt)
            self.be.alert(f"вытеснение снято: головная {hid} закончилась, заморожённое возвращено")

    def reservation(self, h, room, mem, now):
        """Когда первая заблокированная заявка h сможет стартовать (по остаткам max_runtime идущих) и сколько ядер/памяти
        останется сверх неё к этому времени. Не найдётся (чужие юниты держат ядра) — резерва нет."""
        cores, gb, shadow = room, self.mem - mem, 0
        for t, c, m in sorted((j["max_runtime"] - j.get("active_s", 0), 0 if j.get("frozen_for") else len(j["cpus"]), j["mem"]) for j in self.running("prod")):
            if cores >= h["cores"] and gb >= h["mem"]:
                break
            cores, gb, shadow = cores + c, gb + m, t
        if cores < h["cores"] or gb < h["mem"]:
            return None
        return dict(shadow=shadow, cores=cores - h["cores"], mem=gb - h["mem"])

    def alert_waiting(self, now):
        """п.8(а): годная заявка (влезает в машину) ждёт старта > WAIT_ALERT_S — строка в alerts.log, один раз."""
        for j in self.jobs.values():
            if j["state"] == "queued" and j["cls"] == "prod" and not j.get("alerted") and now - j["t_submit"] > WAIT_ALERT_S \
                    and j["cores"] <= self.ncpu and j["mem"] <= self.mem:
                j["alerted"] = True
                self.be.alert(f"заявка {j['id']} {j['name']} ({j['cores']} ядер, prio {j.get('prio', 5)}) ждёт старта {int((now - j['t_submit']) / 60)} мин")

    def alert_underuse(self, now):
        """п.8(б): заявка объявила N ядер, а за UNDER_S реально занимала меньше UNDER_FRAC·N — строка в alerts.log, один раз.
        Заморозка (окно замера, вытеснение) сбрасывает выборку: недогруз считается только по активному времени."""
        for j in self.running("prod"):
            if self.frozen or j.get("frozen_for"):
                j["cpu_samples"] = []
                continue
            sm = j.setdefault("cpu_samples", [])
            if sm and now - sm[-1][0] < SAMPLE_S:
                continue
            sm.append((now, self.be.job_cpu(j)))
            while len(sm) > 2 and now - sm[1][0] >= UNDER_S:
                sm.pop(0)
            t0, c0 = sm[0]
            if not j.get("under_alerted") and now - t0 >= UNDER_S:
                used = (sm[-1][1] - c0) / (now - t0)
                if used < UNDER_FRAC * j["cores"]:
                    j["under_alerted"] = True
                    self.be.alert(f"заявка {j['id']} {j['name']}: объявлено {j['cores']} ядер, за {int((now - t0) / 60)} мин занято {used:.2f} — сократить заявку")

    def log_util(self, now, legacy):
        """Гейт описания «загрузка ядер ≥ 80 % при производстве»: раз в минуту строка util.log — доля занятых ядер хоста
        (/proc/stat), заявки prod (идут/ждут), голые юниты (ядер), окно замера. Нет be.host_busy (макет) — молчим."""
        if not hasattr(self.be, "host_busy") or now - getattr(self, "_util_t", 0) < 60:
            return
        self._util_t = now
        run = [j for j in self.running("prod") if not j.get("frozen_for")]
        q = sum(1 for j in self.jobs.values() if j["state"] == "queued" and j["cls"] == "prod")
        meas = int(bool(self.running("measure")) or self.frozen)
        self.be.util_log(f"host={self.be.host_busy():.3f} prod_run={len(run)} prod_cores={sum(j['cores'] for j in run)} "
                         f"queued={q} legacy={legacy} measure={meas}")

    def alert_idle(self, now):
        """п.8(в): нет ни идущих, ни ждущих заявок, а тикеты ждут вычислений (be.waiting_tickets()) дольше IDLE_S — строка, один раз за простой."""
        if any(j["state"] in ("queued", "running") for j in self.jobs.values()):
            self.idle_since = None
            self.idle_alerted = False
            return
        if getattr(self, "idle_since", None) is None:
            self.idle_since = now
        waiting = self.be.waiting_tickets()
        if waiting and now - self.idle_since > IDLE_S and not getattr(self, "idle_alerted", False):
            self.idle_alerted = True
            self.be.alert(f"очередь пуста {int((now - self.idle_since) / 60)} мин, тикеты ждут вычислений: {', '.join(waiting)}")
        elif not waiting:
            self.idle_since = now

    def free_cores(self):
        return self.ncpu - sum(len(j["cpus"]) for j in self.running("prod") if not j.get("frozen_for")) - math.ceil(self.be.legacy_busy())


# Фон чужих операций чтения диска хоста (оп/с, sda+sdb): пачечный (Пуассон по одному 5-мин окну 0,0885 оп/с занижал —
# тёплая волна 56 с ложно недействительна), поэтому верхняя граница эмпирическая: максимум по 12 холостым окнам 60 с
# при работающем демоне (замер tk071-bg2 06.10, /data/sched-test4/bg2.log: 103 оп / 741 с = 0,139 в среднем, максимум 22 оп / 62,07 с).
# Пересчитывать при calibrate.sh.
BG_OPS_S = 0.354

# Время одного случайного чтения 4 КиБ с HDD, с (замер tk071-seek 06.10, /data/sched-test/seek.log, 1500 чтений O_DIRECT:
# среднее sdb 21,80 мс, sda 20,98 мс). Чужие операции важны временем диска, которое они крадут, а не долей от числа операций.
SEEK_S = 0.0218
# Фон приходит пачками до 22 оп (≈ 0,5 с диска); на окне < 60 с одна пачка — уже > 1 %, хотя на волнах (≥ 100 с) это < 0,5 %.
# Поэтому знаменатель критерия времени диска — не меньше MIN_WALL_S (smoke-окна по 8–10 с судятся как 60-секундные).
MIN_WALL_S = 60.0
# Потолок HDD сервера счёта, байт/с (замер CEO 05.10 05:12: ≈ 66 МБ/с и подряд, и вразнобой): чужие БАЙТЫ чтения судятся временем диска,
# а не долей от всех байт окна — стенд с чтением 16 МБ за 634 с (5 МБ чужих = 30 %, а диска украдено 0,08 с) был ложно недействителен (08.10).
DISK_BPS = 66e6


def judge_window(d, ncpu=NCPU, tol=0.01, bg_ops_s=None):
    """d: cpu_s (занято на хосте за окно), own_cpu_s (юнит замера), wall_s, foreign_units (посторонние активные юниты в окне),
    disk_b (прочитано с дисков хоста), own_disk_b (читал юнит замера). Помеха = чужое ЦП / (стена × ядра) и чужое чтение
    диска / всё чтение окна и время диска чужих операций чтения сверх фона × SEEK_S / стена (только если замер сам читает диск); замороженные юниты не в счёт; допуск tol = 1 % (гейт TK-071). → (годна, причины)."""
    why = []
    if d["foreign_units"]:
        why.append("посторонние юниты в окне: " + ",".join(sorted(d["foreign_units"])[:5]))
    cap = d["wall_s"] * ncpu
    if cap > 0 and (d["cpu_s"] - d["own_cpu_s"] - d.get("daemon_cpu_s", 0.0)) / cap > tol:
        why.append(f"чужое ЦП {(d['cpu_s'] - d['own_cpu_s'] - d.get('daemon_cpu_s', 0.0)) / cap * 100:.1f} % ядер окна")
    foreign_t = (d["disk_b"] - d["own_disk_b"]) / DISK_BPS / max(d["wall_s"], MIN_WALL_S)      # доля времени окна, занятая чужими байтами
    sens = d.get("disk_sens", True)                                    # изолированный замер без --disk (не читает диск): чужое чтение ему не помеха
    if sens and d["own_disk_b"] > 0 and d["disk_b"] > 0 and foreign_t > tol:
        why.append(f"чужое чтение диска {(d['disk_b'] - d['own_disk_b']) / d['disk_b'] * 100:.1f} % байт = {foreign_t * 100:.1f} % времени окна")
    ios, own_ios = d.get("ios", 0), d.get("own_ios", 0)
    bg = (BG_OPS_S if bg_ops_s is None else bg_ops_s) * d["wall_s"]
    extra = ios - own_ios - bg                                         # чужие операции сверх фона
    if sens and own_ios > 0 and d["wall_s"] > 0 and extra * SEEK_S / max(d["wall_s"], MIN_WALL_S) > tol:     # мелкие чтения HDD: байт мало, поисков много
        why.append(f"чужие чтения диска: {extra:.0f} оп сверх фона × {SEEK_S * 1000:.1f} мс = {extra * SEEK_S / max(d['wall_s'], MIN_WALL_S) * 100:.1f} % времени окна")
    if d.get("culprits") and any("диска" in w for w in why):
        why.append("кто читал диск (оп, МБ): " + "; ".join(f"{c['cgroup']} {c['ops']} оп {c['mb']} МБ" for c in d["culprits"]))
    if d.get("swap_slice") is not None:      # изолированный замер без --disk: годность — своп слайса замера; своп хоста справочно (судья 03:38, 09.10)
        if d["swap_slice"] > 0:
            why.append(f"своп слайса замера: {d['swap_slice']} байт (memory.swap.peak) — память замера вытеснена, замер недействителен")
    elif d.get("swap_pages", 0) > 0:
        why.append(f"своп в окне: {d['swap_pages']} страниц (pswpin+pswpout) — память хоста вытесняется, замер недействителен")
    if d.get("forced_thaw"):
        why.append("страховочная разморозка в окне (аренда истекла или демон не вернул окно)")
    return (not why, why)


def warn_for(job, free, queued_prod):
    if job["cls"] == "prod" and job["cores"] == 1 and free >= 8 and not queued_prod:
        return (f"ПРЕДУПРЕЖДЕНИЕ: заявка на 1 ядро, свободно {free} из {NCPU}. Подбери параллельность "
                f"(tools/compute/calibrate.sh, В-178) и подай --cores N или несколько заданий.")
    return None


# ---------- systemd-бэкенд ----------
def ticket_of(name):
    """TK-117: номер тикета из имени заявки (tk115-wa-… → TK-115); нет — пусто. Та же логика, что в alsched-jobowners.sh."""
    m = re.match(r"tk0*(\d+)", name or "")
    return f"TK-{int(m.group(1)):03d}" if m else ""


def classify_fail(rc, journal):
    """TK-117 К2.1: причина rc≠0 по журналу юнита (юнит с --collect исчезает, Result в systemctl не достать, но journald помнит
    «Failed with result '…'»). oom-kill / timeout / signal / exit-code; rc 124 — снят демоном по бюджету, -15 — отмена, 125 — не запустился."""
    m = re.search(r"Failed with result '([a-z-]+)'", journal or "")
    if m:
        return {"oom-kill": "oom-kill (память юнита выше MemoryMax)", "timeout": "timeout (RuntimeMaxSec)",
                "signal": "signal", "exit-code": f"exit-code {rc}"}.get(m.group(1), m.group(1))
    if "oom-kill" in (journal or ""):
        return "oom-kill"
    return {124: "снят демоном по max-runtime", -15: "отменён", 125: "systemd-run не запустил юнит"}.get(rc, f"rc {rc}, причина в журнале не найдена")


def alert_line(text):
    """Строка в alerts.log; сам журнал не пишется — stderr (юнит демона → journald), демон из-за этого не падает."""
    try:
        with open(f"{DIR}/alerts.log", "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + text + "\n")
    except OSError as e:
        print(f"alerts.log не пишется ({e}): {text}", file=sys.stderr)


_WARNED = set()


def warn_once(key, text):
    """TK-133 С-43: сбой записи/чтения состояния не глотается — одна строка в alerts.log на ключ за жизнь процесса."""
    if key not in _WARNED:
        _WARNED.add(key)
        alert_line("СБОЙ СОСТОЯНИЯ: " + text)


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, stdin=subprocess.DEVNULL)


class SystemdBackend:
    def __init__(self):
        self.frozen_list = os.path.join(DIR, "frozen.json")
        self._cpu = {}
        self._t = time.time()
        self._legacy = 0.0
        self._legacy_detail = {}
        self._last_own = {}
        self._peak = {}
        self._memc = {}

    def now(self):
        return time.time()

    def load_preempt(self):
        try:
            return json.load(open(f"{DIR}/preempt.json"))
        except (OSError, ValueError):
            return {}

    def save_preempt(self, d):
        json.dump(d, open(f"{DIR}/preempt.json.tmp", "w"))
        os.replace(f"{DIR}/preempt.json.tmp", f"{DIR}/preempt.json")

    def freeze_unit(self, u):
        sh("systemctl", "freeze", u)

    def thaw_unit(self, u):
        sh("systemctl", "thaw", u)

    def legacy_units(self):
        """Голые юниты (не из очереди), идущие и не замороженные, с ядрами по квоте/факту — кандидаты на вытеснение."""
        return [(u, math.ceil(v)) for u, v in self._legacy_detail.items() if v >= 1]

    def alert(self, text):
        alert_line(text)

    def host_busy(self):
        """Доля занятых ядер хоста с прошлого вызова (/proc/stat: 1 − (idle+iowait)/сумма)."""
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        tot, idle = sum(v), v[3] + v[4]
        t0, i0 = getattr(self, "_hb", (tot, idle))
        self._hb = (tot, idle)
        return 1 - (idle - i0) / (tot - t0) if tot > t0 else 0.0

    @staticmethod
    def stat_cpus(skip=()):
        """Сумма /proc/stat по ядрам без skip (по-ядерные строки cpuN); пусто skip — общая строка cpu."""
        if not skip:
            return [int(x) for x in open("/proc/stat").readline().split()[1:]]
        tot = None
        for l in open("/proc/stat"):
            k = l.split()
            if k[0].startswith("cpu") and k[0] != "cpu" and int(k[0][3:]) not in skip:
                v = [int(x) for x in k[1:]]
                tot = v if tot is None else [a + b for a, b in zip(tot, v)]
        return tot

    def busy_fact(self, skip=()):
        """Загрузка ЦП хоста для пакования по факту: EWMA с постоянной 60 с (отдельное состояние от host_busy); skip — ядра замера."""
        v = self.stat_cpus(skip)
        tot, idle, t = sum(v), v[3] + v[4], time.time()
        st = getattr(self, "_fb", None)
        self._fb = (tot, idle, t, st[3] if st else 0.0)
        if st is None or tot <= st[0]:
            return self._fb[3]
        inst = 1 - (idle - st[1]) / (tot - st[0])
        a = 1 - math.exp(-(t - st[2]) / 60)
        self._fb = (tot, idle, t, st[3] + a * (inst - st[3]))
        return self._fb[3]

    def job_mem(self, j):
        """Факт памяти юнита, ГБ (MemoryCurrent; кэш 30 с)."""
        c = self._memc.get(j["id"])
        if c and time.time() - c[0] < 30:
            return c[1]
        r = sh("systemctl", "show", "-p", "MemoryCurrent", "--value", self.unit(j)).stdout.strip()
        g = int(r) / 2**30 if r.isdigit() else 0.0
        self._memc[j["id"]] = (time.time(), g)
        return g

    def util_log(self, text):
        with open(f"{DIR}/util.log", "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + text + "\n")

    @staticmethod
    def psi_cpu():
        """PSI cpu some avg10, % (доля времени, когда готовая к работе задача ждёт ядра)."""
        try:
            m = re.search(r"some avg10=([0-9.]+)", open("/proc/pressure/cpu").read())
            return float(m.group(1)) if m else 0.0
        except OSError:
            return 0.0

    def cancelled(self, j):
        return os.path.exists(f"{DIR}/cancel/{j['id']}")

    def cancel_why(self, j):
        """текст из cancel/<id> (alsched.py cancel <id> [причина]); пусто — «cancel без причины»."""
        try:
            return open(f"{DIR}/cancel/{j['id']}", encoding="utf-8").read().strip() or "cancel без причины"
        except OSError as e:
            return f"cancel (причина не прочитана: {e})"

    def unit(self, j):
        return ("alpha-sm-" if j["cls"] == "measure" else "tk0s-") + f"{j['name']}-{j['id']}"

    def slice(self, j):
        return f"alsm{j['id']}.slice"     # замер и вложенные systemd-run волны — одна cgroup: учёт ЦП/диска/памяти волны целиком

    def start(self, j):
        os.makedirs(f"{DIR}/logs", exist_ok=True)
        os.makedirs(f"{DIR}/rc", exist_ok=True)
        cpus = ",".join(map(str, range(NCPU) if j.get("unpinned") else j["cpus"]))
        iso = bool(j.get("iso_cpus"))
        os.makedirs(f"{DIR}/own", exist_ok=True)     # итог ЦП/диска юнита снимает сам юнит перед выходом (после выхода cgroup исчезает)
        fin = (f"cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); [ -n \"$ALSCHED_SLICE\" ] && cg=/sys/fs/cgroup/$ALSCHED_SLICE; {{ cat $cg/io.stat; grep usage_usec $cg/cpu.stat; grep '^oom_kill ' $cg/memory.events; echo swap_peak $(cat $cg/memory.swap.peak 2>/dev/null || cat $cg/memory.swap.current 2>/dev/null); }} "
               f"> {DIR}/own/{j['id']} 2>/dev/null; ")
        run_dir = f"{RUNS_DIR}/{j['id']}"     # КТ-9: единый адрес выхода прогона; задание берёт его из $ALSCHED_RUN_DIR (старые пути остаются как есть)
        try:
            os.makedirs(run_dir, exist_ok=True)
        except OSError as e:
            print(f"{run_dir} не создан ({e}): задание идёт без каталога выхода", file=sys.stderr)
        pre = f"export ALSCHED_RUN_DIR={run_dir}\n"
        if j["cls"] == "measure":
            pre += f"export ALSCHED_SLICE={self.slice(j)} PATH={DIR}/shim:$PATH\n"
        inner = f"{pre}{j['cmd']}\nrc=$?; {fin}echo $rc > {DIR}/rc/{j['id']}; exit $rc"
        if j["cls"] == "measure":
            self.arm_failsafe(j["max_runtime"] + 2 * TICK)
        mem_p = ["-p", f"MemoryMin={j['mem']}G"] if iso else []        # П4: замеряемого защищаем, соседей не душим
        sl = ["-p", f"Slice={self.slice(j)}"] if j["cls"] == "measure" else []
        r = sh("systemd-run", f"--unit={self.unit(j)}", "--collect", *sl, f"--working-directory={j['cwd']}",
               "-p", f"RuntimeMaxSec={int(j['max_runtime'])}", "-p", "IOAccounting=yes", "-p", "CPUAccounting=yes",
               "-p", f"AllowedCPUs={cpus}", "-p", f"MemoryMax={j['mem']}G", "-p", "MemorySwapMax=0", "-p", f"CPUQuota={j['cores'] * 100 if j.get('unpinned') else len(j['cpus']) * 100}%",
               "-p", f"CPUWeight={10 if j.get('prio', 5) > BARE_PRIO else 100}",      # 09.10: prio ниже боевого (синтетика, prio>5) — вес ЦП ≤1/10, боевой не страдает при конкуренции
               "-p", f"StandardOutput=append:{DIR}/logs/{j['id']}.log", "-p", "StandardError=inherit",
               *mem_p, "bash", "-c", inner)
        if r.returncode:
            open(f"{DIR}/rc/{j['id']}", "w").write("125\n")
        elif iso:       # вложенные юниты волны живут в слайсе замера: ядра и своп — на слайс
            sh("systemctl", "set-property", "--runtime", self.slice(j), f"AllowedCPUs={cpus}", "MemorySwapMax=0")

    def fail_reason(self, j, rc):
        r = sh("journalctl", "-u", self.unit(j) + ".service", "-n", "30", "--no-pager", "-o", "cat")
        return classify_fail(rc, r.stdout)

    def kill(self, j):
        sh("systemctl", "thaw", self.unit(j))      # замороженный юнит не останавливается («Cannot stop frozen unit», SIGKILL через 18 с)
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

    @staticmethod
    def cpu_cores(cpus):
        """ЦП-секунды (без idle/iowait) по перечисленным ядрам — /proc/stat cpuN."""
        t = 0
        for l in open("/proc/stat"):
            k = l.split()
            if k[0].startswith("cpu") and k[0] != "cpu" and int(k[0][3:]) in cpus:
                v = [int(x) for x in k[1:]]
                t += sum(v) - v[3] - v[4]
        return t / os.sysconf("SC_CLK_TCK")

    @staticmethod
    def oom_kills():
        return sum(int(l.split()[1]) for l in open("/proc/vmstat") if l.startswith("oom_kill "))

    def cpu_host(self):
        f = open("/proc/stat").readline().split()[1:]
        return (sum(map(int, f)) - int(f[3]) - int(f[4])) / os.sysconf("SC_CLK_TCK")

    def disk_host(self):
        tot = n = 0
        for l in open("/proc/diskstats"):
            p = l.split()
            if len(p) > 5 and (p[2].startswith("sd") or p[2].startswith("nvme")) and not p[2][-1].isdigit():
                tot += int(p[5]) * 512
                n += int(p[3]) + int(p[4])   # завершённые + слитые: как rios у cgroup (счёт bio до слияния)
        return tot, n

    @staticmethod
    def mem_state():
        """(MemAvailable, занятый своп), ГБ — из /proc/meminfo."""
        m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        return m["MemAvailable"] / 2**20, (m["SwapTotal"] - m["SwapFree"]) / 2**20

    @staticmethod
    def swap_pages():
        n = 0
        for l in open("/proc/vmstat"):
            if l.startswith(("pswpin ", "pswpout ")):
                n += int(l.split()[1])
        return n

    def unit_cpu_io(self, j):
        u = self.unit(j)
        c = sh("systemctl", "show", "-p", "CPUUsageNSec", "--value", u).stdout.strip()
        cg = sh("systemctl", "show", "-p", "ControlGroup", "--value", u).stdout.strip()
        if j["cls"] == "measure" and cg not in ("", "/"):
            cg = "/" + self.slice(j)
            try:
                m = re.search(r"usage_usec (\d+)", open(f"/sys/fs/cgroup{cg}/cpu.stat").read())
                c = str(int(m.group(1)) * 1000) if m else c
            except OSError as e:
                warn_once("cgroup-cpu-stat", f"cpu.stat слайса {cg} не прочитан ({e}) — ЦП замера по systemctl")
        rb = rn = 0
        try:
            if cg in ("", "/"):
                raise OSError
            for l in open(f"/sys/fs/cgroup{cg}/io.stat"):
                rb += sum(int(x.split("=")[1]) for x in l.split() if x.startswith("rbytes="))
                rn += sum(int(x.split("=")[1]) for x in l.split() if x.startswith("rios="))
        except OSError as e:
            warn_once("cgroup-io-stat", f"io.stat {cg} не прочитан ({e}) — диск юнита 0")
        return (int(c) / 1e9 if c.isdigit() else 0.0), rb, rn

    def job_cpu(self, j):
        return self.unit_cpu_io(j)[0]

    def waiting_tickets(self):
        """Тикеты, ждущие вычислений: строки файла DIR/waiting_tickets (пишет CEO/диспетчер); файла нет — никто."""
        try:
            return [l.strip() for l in open(f"{DIR}/waiting_tickets") if l.strip()]
        except OSError:
            return []

    def cg_io(self):
        """io.stat (rbytes, rios) всех cgroup от корня (иерархические значения)."""
        out = {}
        for dp, _dn, fn in os.walk("/sys/fs/cgroup"):
            if "io.stat" not in fn:
                continue
            rb = rn = 0
            try:
                for l in open(dp + "/io.stat"):
                    for x in l.split():
                        if x.startswith("rbytes="):
                            rb += int(x[7:])
                        elif x.startswith("rios="):
                            rn += int(x[5:])
            except OSError:
                continue
            out[dp[len("/sys/fs/cgroup"):] or "/"] = (rb, rn)
        return out

    def culprits(self, j, c0, c1, host_rn, host_rb, t0=None):
        """Топ чужих читателей окна: cgroup (юнит/сессия ssh), остаток = ядро, своп. io.stat иерархичен (родитель = сам + потомки) →
        считаем «сам» = родитель минус прямые дети; чтение исчезнувшей cgroup оседает в «сам» родителя."""
        me = self.unit(j)

        def own_part(c):
            kids = {}
            for k, (rb, rn) in c.items():
                par = k.rsplit("/", 1)[0] or "/"
                if k != "/":
                    a = kids.setdefault(par, [0, 0])
                    a[0] += rb
                    a[1] += rn
            return {k: (rb - kids.get(k, (0, 0))[0], rn - kids.get(k, (0, 0))[1]) for k, (rb, rn) in c.items()}

        e0, e1 = own_part(c0), own_part(c1)
        me_alive = any(me in k for k in c1)
        rows, sn, sb = [], 0, 0
        for k, (rb, rn) in e1.items():
            b0, n0 = e0.get(k, (0, 0))
            dn, db = rn - n0, rb - b0
            if k.startswith("/system.slice/alpha-sm-") or me in k or self.slice(j) in k:
                continue
            if k == "/system.slice" and not me_alive:
                dn, db = dn - self._last_own.get(j["id"], (0.0, 0, 0))[2], db - self._last_own.get(j["id"], (0.0, 0, 0))[1]
            if dn <= 0:
                continue
            rows.append((dn, db, k + (" (сессии ssh/юниты, закрытые в окне)" if k in ("/user.slice", "/system.slice") else "")))
            sn += dn
            sb += db
        rows.sort(reverse=True)
        top = [dict(cgroup=k, ops=dn, mb=round(db / 1e6, 1)) for dn, db, k in rows[:3]]
        if host_rn - sn > 0:
            top.append(dict(cgroup="(остаток: ядро/своп)", ops=host_rn - sn, mb=round((host_rb - sb) / 1e6, 1)))
        if t0 is not None:
            r = sh("journalctl", "-u", "ssh", "-u", "sshd", "--no-pager", "-o", "cat", "--since", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)))
            ips = [l.split(" from ")[1].split()[0] for l in r.stdout.splitlines() if "Accepted " in l and " from " in l]
            if ips:
                top.append(dict(cgroup=f"(ssh-входов за окно: {len(ips)}; с {', '.join(sorted(set(ips))[:3])})", ops=0, mb=0.0))
        return top

    def foreign_units(self, j):
        own = {self.unit(j) + ".service"}
        return [u for u in self.units(FREEZE_PAT) if u not in own and not u.startswith("alpha-")
                and sh("systemctl", "show", "-p", "Slice", "--value", u).stdout.strip() != self.slice(j)     # вложенные юниты самой волны
                and sh("systemctl", "show", "-p", "FreezerState", "--value", u).stdout.strip() != "frozen"]   # замороженный не мешает

    def daemon_cpu(self):
        t = os.times()          # сам демон и его systemctl/sh: не помеха замеру
        return t[0] + t[1] + t[2] + t[3]

    def win_begin(self, j):
        b, n = self.disk_host()
        iso = j.get("iso_cpus")
        return dict(t=time.time(), cpu=self.cpu_cores(iso) if iso else self.cpu_host(), disk=b, ios=n, units=set() if iso else set(self.foreign_units(j)), dcpu=self.daemon_cpu(), cg=self.cg_io(), swap=self.swap_pages(), oom=self.oom_kills())

    def win_end(self, j, s0):
        t1 = time.time()
        own_cpu, own_rb, own_rn = self._last_own.get(j["id"], (0.0, 0, 0))
        hb, hn = self.disk_host()
        try:
            forced = os.path.getmtime(f"{DIR}/forced-thaw") >= s0["t"]
        except OSError:
            forced = False
        iso = j.get("iso_cpus")     # изолированное окно: чужое ЦП — на ядрах замера, посторонние юниты на других ядрах не помеха
        d = dict(wall_s=t1 - s0["t"], cpu_s=(self.cpu_cores(iso) if iso else self.cpu_host()) - s0["cpu"], own_cpu_s=own_cpu,
                 daemon_cpu_s=self.daemon_cpu() - s0["dcpu"], disk_b=hb - s0["disk"], own_disk_b=own_rb, ios=hn - s0["ios"], own_ios=own_rn, forced_thaw=forced, swap_pages=self.swap_pages() - s0["swap"],
                 foreign_units=set() if iso else s0["units"] | set(self.foreign_units(j)))
        d["culprits"] = self.culprits(j, s0["cg"], self.cg_io(), d["ios"] - d["own_ios"], d["disk_b"] - d["own_disk_b"], s0["t"])
        d["disk_sens"] = not iso or j.get("disk", "none") != "none"
        if not d["disk_sens"]:
            try:
                d["swap_slice"] = sum(int(l.split()[1]) for l in open(f"{DIR}/own/{j['id']}") if l.startswith("swap_peak "))
            except (OSError, ValueError, IndexError):
                d["swap_slice"] = 0
        ok, why = judge_window(d, ncpu=len(iso) if iso else NCPU)
        if iso and ok:       # П4: OOM-убийств за окно нет
            d["oom_host"] = self.oom_kills() - s0.get("oom", 0)      # справочно: чужой memcg-OOM на других ядрах замеру не помеха
            try:
                oom = sum(int(l.split()[1]) for l in open(f"{DIR}/own/{j['id']}") if l.startswith("oom_kill "))   # убийства внутри слайса замера
            except (OSError, ValueError, IndexError):
                oom = 0
            if oom:
                ok, why = False, [f"oom_kill в окне: {oom}"]
        if ok and d["wall_s"] >= 600:     # метка для wait_for Судьи: действительная настоящая волна (≥ 10 мин) под демоном
            open(f"{DIR}/valid_real_wave", "a").write(j["id"] + "\n")
        os.makedirs(f"{DIR}/validity", exist_ok=True)
        json.dump(dict(ok=ok, why=why, **{k: (sorted(v) if isinstance(v, set) else v) for k, v in d.items()}),
                  open(f"{DIR}/validity/{j['id']}.json", "w"), ensure_ascii=False)
        return dict(ok=ok, why=why)

    def read_final(self, j):
        try:
            txt = open(f"{DIR}/own/{j['id']}").read()
        except OSError:
            return None
        rb = rn = 0
        for x in txt.split():
            k, _, v = x.partition("=")
            if k == "rbytes":
                rb += int(v)
            elif k == "rios":
                rn += int(v)
        m = re.search(r"usage_usec (\d+)", txt)
        return (int(m.group(1)) / 1e6 if m else 0.0), rb, rn

    def sample_wave_mem(self, j):
        """Анонимная память + shmem юнита замера (кэш файлов вытесняем — в бюджет производства не входит); максимум по тактам → DIR/wave_mem_peak_gb."""
        cg = sh("systemctl", "show", "-p", "ControlGroup", "--value", self.unit(j)).stdout.strip()
        if cg in ("", "/"):
            return
        if j["cls"] == "measure":
            cg = "/" + self.slice(j)
        st = dict(l.split() for l in open(f"/sys/fs/cgroup{cg}/memory.stat"))
        gb = (int(st["anon"]) + int(st.get("shmem", 0))) / 2**30
        self._peak[j["id"]] = max(gb, self._peak.get(j["id"], 0.0))
        if peak_counts(self.now() - j["t_start"], self._peak[j["id"]], mem_peak_gb()):     # пробы короче MIN_WALL_S пик не пишут
            open(f"{DIR}/wave_mem_peak_gb", "w").write(f"{self._peak[j['id']]:.2f}")

    def done(self, j):
        p = f"{DIR}/rc/{j['id']}"
        if j["cls"] == "measure":      # юнит с --collect исчезает по выходу — снять ЦП/диск юнита, пока он жив
            try:
                self.sample_wave_mem(j)
            except Exception as e:
                warn_once("wave-mem", f"sample_wave_mem {j['id']}: {e!r}")
            try:
                new = self.unit_cpu_io(j)
                old = self._last_own.get(j["id"], (0.0, 0, 0))
                self._last_own[j["id"]] = tuple(max(a, b) for a, b in zip(new, old))
                fin = self.read_final(j)
                if fin:
                    self._last_own[j["id"]] = fin
            except Exception as e:
                warn_once("unit-cpu-io", f"снятие ЦП/диска юнита {j['id']}: {e!r}")
        if os.path.exists(p):
            if j["cls"] == "measure" and self.now() - j["t_start"] >= MIN_WALL_S:
                open(f"{DIR}/first_real_wave", "a").write(j["id"] + "\n")     #метка для wait_for: настоящая волна под демоном кончилась
            return int(open(p).read().strip() or 0)
        if sh("systemctl", "is-active", self.unit(j)).stdout.strip() not in ("active", "activating"):
            return 143
        return None

    def units(self, pats):
        out = sh("systemctl", "list-units", "--type=service", "--state=active", "--no-legend", "--plain", *pats).stdout
        return [l.split()[0] for l in out.splitlines() if l.strip()]

    def iso_cpus(self, k):
        """K физических ядер с обоими SMT-соседями (М4): с конца нумерации; топология — thread_siblings_list."""
        sib, seen = [], set()
        for c in range(NCPU - 1, -1, -1):
            if c in seen:
                continue
            g = set()
            for part in open(f"/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list").read().strip().split(","):
                lo, _, hi = part.partition("-")
                g |= set(range(int(lo), int(hi or lo) + 1))
            seen |= g
            sib.append(g)
        return sorted(c for g in sib[:k] for c in g)

    TOP_SLICES = ("system.slice", "user.slice", "init.scope")      # П3: ядра замера уходят со ВСЕХ верхних слайсов

    def slice_cpus(self, u):
        """Ядра верхнего слайса по факту (cpuset.cpus.effective) — множество; нет файла → пусто."""
        try:
            t = open(f"/sys/fs/cgroup/{u}/cpuset.cpus.effective").read().strip()
        except OSError:
            return set()
        r = set()
        for part in t.split(","):
            lo, _, hi = part.partition("-")
            if lo:
                r |= set(range(int(lo), int(hi or lo) + 1))
        return r

    def iso_begin(self, cpus):
        rest = ",".join(str(c) for c in range(NCPU) if c not in cpus)
        json.dump(cpus, open(f"{DIR}/iso.json", "w"))
        for u in self.TOP_SLICES:
            sh("systemctl", "set-property", "--runtime", u, f"AllowedCPUs={rest}")

    def iso_end(self):
        """09.10: пустой AllowedCPUs= не вернул effective (system/user/init остались на 0-3,8-11, prod на 8 из 16 ядер, гейт (а) недействителен) —
        ставим явный полный список и читаем назад cpuset.cpus.effective; расхождение — строка в alerts.log и повтор."""
        full = ",".join(str(c) for c in range(NCPU))
        want = set(range(NCPU))
        for u in self.TOP_SLICES:
            for _ in range(3):
                sh("systemctl", "set-property", "--runtime", u, f"AllowedCPUs={full}")
                if self.slice_cpus(u) == want:
                    break
            else:
                self.alert(f"iso_end: {u} cpuset.cpus.effective={sorted(self.slice_cpus(u))} вместо 0-{NCPU - 1} после 3 попыток")
        try:
            if os.path.exists(f"{DIR}/iso.json"):
                os.remove(f"{DIR}/iso.json")
        except OSError as e:
            warn_once("iso-json", f"iso.json не удалён ({e}) — следующий демон прочтёт старую изоляцию")

    def freeze_all(self):
        us = [u for u in self.units(FREEZE_PAT) if not u.startswith("alpha-")
              and sh("systemctl", "show", "-p", "FreezerState", "--value", u).stdout.strip() != "frozen"]   # чужую заморозку (benchrun) не трогаем и не размораживаем
        for u in us:
            sh("systemctl", "freeze", u)
        json.dump(us, open(self.frozen_list, "w"))

    def thaw_all(self):
        sh("systemctl", "stop", "alpha-sm-failsafe.timer")
        if os.path.exists(f"{DIR}/iso.json"):               # страховка: ядра замера вернуть производству
            self.iso_end()
        if os.path.exists(self.frozen_list):
            for u in json.load(open(self.frozen_list)):
                sh("systemctl", "thaw", u)
            os.remove(self.frozen_list)

    def legacy_busy(self):
        """Ядер, занятых чужими (не из очереди) счётными юнитами: прирост CPUUsageNSec за интервал; кэш на ≥ TICK."""
        t = time.time()
        if t - self._t < TICK and self._legacy:
            return self._legacy
        tot, dt = 0, max(t - self._t, 1e-3)
        self._legacy_detail = {}
        for u in self.units(LEGACY_PAT):
            if u.startswith("tk0s-"):
                continue
            if sh("systemctl", "show", "-p", "FreezerState", "--value", u).stdout.strip() == "frozen":
                continue                  # замороженный юнит ядер не держит (вытеснен или в окне замера)
            v = sh("systemctl", "show", "-p", "CPUUsageNSec", "-p", "CPUQuotaPerSecUSec", u).stdout
            kv = dict(l.split("=", 1) for l in v.splitlines() if "=" in l)
            cur = kv.get("CPUUsageNSec", "")
            used = 0.0
            if cur.isdigit():
                used = (int(cur) - self._cpu.get(u, int(cur))) / 1e9 / dt if u in self._cpu else 0.0
                self._cpu[u] = int(cur)
            q = kv.get("CPUQuotaPerSecUSec", "infinity")
            quota = float(q[:-1]) if q.endswith("s") and q[:-1].replace(".", "").isdigit() else 0.0   # «12s» = 1200 %
            held = used if PACK == "fact" else max(used, quota)     # fact: по факту, не по квоте (квота 12 при факте 1 не держит 11 пустых)
            self._legacy_detail[u] = held
            tot += held                   # claim: голый юнит держит ядра по квоте, без квоты — по факту
        self._legacy, self._t = tot, t
        return self._legacy


# ---------- CLI ----------
def jpath(i):
    return f"{DIR}/jobs/{i}.json"


def load_all(known=()):
    os.makedirs(f"{DIR}/jobs", exist_ok=True)
    return [json.load(open(f"{DIR}/jobs/{f}")) for f in sorted(os.listdir(f"{DIR}/jobs")) if f.endswith(".json") and f[:-5] not in known]


def write_job(j):
    tmp = jpath(j["id"]) + ".tmp"
    json.dump(j, open(tmp, "w"), ensure_ascii=False)
    os.replace(tmp, jpath(j["id"]))


def parse_dur(s):
    m = {"s": 1, "m": 60, "h": 3600}
    return float(s[:-1]) * m[s[-1]] if s and s[-1] in m else float(s)


def submit(cls, name, cores, mem, disk, cwd, cmd, max_runtime, prio=5, io="", iso=0, vypiska=None):
    os.makedirs(f"{DIR}/jobs", exist_ok=True)
    jid = time.strftime("%m%d%H%M%S") + f"{os.getpid() % 1000:03d}"
    j = dict(id=jid, name=name, cls=cls, cores=cores if cls == "prod" else NCPU, mem=mem, disk=disk, cwd=cwd, cmd=cmd,
             prio=prio, io=io, max_runtime=max_runtime, active_s=0, state="queued", t_submit=time.time(), cpus=[])
    if iso and cls == "measure":
        j["iso"] = iso
    if vypiska is not None:
        j["vypiska"] = vypiska
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
    seen = {}
    mw = [j for j in load_all() if j["state"] == "running" and j["cls"] == "measure"]
    if mw:                       # перезапуск демона в окне замера: производство не размораживаем, окно недействительно
        core.frozen = any(not j.get("iso_cpus") for j in mw)    # изолированное окно производство не морозило
        for j in mw:
            j["valid"] = dict(ok=False, why=["демон перезапущен в окне замера"])
            core.add(j)
    else:
        be.thaw_all()
    while True:
        open(f"{DIR}/heartbeat", "w").write(str(time.time()))
        for j in load_all(core.jobs):                     # читаем только новые заявки: на волне кэш файлов вытеснен, каждый json — поиск HDD
            core.add(j)
        core.mem = prod_mem_budget_gb()
        core.tick()
        for j in core.jobs.values():
            if seen.get(j["id"]) != (j["state"], j.get("rc")):
                seen[j["id"]] = (j["state"], j.get("rc"))
                write_job(j)
        time.sleep(TICK * 3 if core.running("measure") else TICK)   # в окне замера демон тише: чужие чтения демона — помеха волне


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    if a[0] in ("wave", "stand"):
        g = argparse.Namespace(recompute=False, why="", repeat=0, cls="measure", cwd=os.getcwd())
        g.exclusive, g.iso = False, 4
        while a[1:2] and a[1] in ("--recompute", "--why", "--repeat", "--exclusive", "--iso"):     # TK-081: повтор замера — только явно
            if a[1] in ("--recompute", "--exclusive"):
                g.__dict__[a[1][2:]] = True; a = a[:1] + a[2:]
            else:
                g.__dict__[a[1][2:]] = a[2] if a[1] == "--why" else int(a[2]); a = a[:1] + a[3:]
        if len(a) < 4 or a[1] != "--max-runtime":
            print(f"alsched.py {a[0]} --max-runtime <срок: 90s|30m|2h> команда… (срок обязателен: окно замера = аренда с TTL)")
            return 2
        grc, gcmd = guard_check(g, a[3:])
        if grc:
            return grc
        if a[0] == "stand" and not g.exclusive and g.iso:     # судья 09.10: stand по умолчанию на K физ. ядрах без заморозки; заморозка всего — волна или явный --exclusive
            j = submit("measure", a[0], g.iso, 20, "none", os.getcwd(), gcmd, parse_dur(a[2]), prio=0, iso=g.iso)
        else:
            j = submit("measure", a[0], NCPU, 56, "none", os.getcwd(), gcmd, parse_dur(a[2]), prio=0)
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
                v = cur.get("valid")
                if not v:
                    print("ВОЛНА НЕДЕЙСТВИТЕЛЬНА: окно не проверено", file=sys.stderr)
                    return cur["rc"] or 3
                if not v["ok"]:
                    print("ВОЛНА НЕДЕЙСТВИТЕЛЬНА: " + "; ".join(v["why"]), file=sys.stderr)
                    return cur["rc"] or 3
                return cur["rc"]
            try:
                hb = os.path.getmtime(f"{DIR}/heartbeat")
            except OSError:
                hb = 0
            if time.time() - max(hb, j["t_submit"]) > DEAD_S:
                print(f"ВОЛНА НЕДЕЙСТВИТЕЛЬНА: демон планировщика молчит > {DEAD_S} с (окно {j['id']} не под контролем)", file=sys.stderr)
                return 4
            time.sleep(1)
    if a[0] == "ps":
        return ps() or 0
    if a[0] == "reprio":     # alsched.py reprio <id> <prio>: меньше = раньше; демон читает на ближайшем такте
        os.makedirs(f"{DIR}/prio", exist_ok=True)
        open(f"{DIR}/prio/{a[1]}", "w").write(a[2] + "\n")
        return 0
    if a[0] == "cancel":
        os.makedirs(f"{DIR}/cancel", exist_ok=True)
        open(f"{DIR}/cancel/{a[1]}", "w", encoding="utf-8").write(" ".join(a[2:]) + "\n" if a[2:] else "")
        return 0
    if a[0] == "thaw":     # страховочный таймер аренды и ручная разморозка; метка делает идущее окно недействительным
        os.makedirs(DIR, exist_ok=True)
        open(f"{DIR}/forced-thaw", "w").write(str(time.time()))
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
        p.add_argument("--io", default="", choices=["", "seq"], help="seq: HDD-тяжёлое последовательное чтение — не больше одного такого на диск")
        p.add_argument("--cwd", default=os.getcwd())
        p.add_argument("--iso", type=int, default=0, help="замер: K физических ядер (оба SMT-соседа) без остановки производства (п.3); 0 — окно на весь сервер")
        p.add_argument("--prio", type=int, default=5)
        p.add_argument("--recompute", action="store_true", help="пересчитать уже посчитанное (нужен --why)")
        p.add_argument("--why", default="", help="причина пересчёта; пишется в реестр")
        p.add_argument("--repeat", type=int, default=0, help="замер: всего N прогонов с тем же отпечатком (против шума)")
        p.add_argument("--adhoc", default="", help="КТ-1: обход проверки манифеста для prod — причина (пишется в alerts.log)")
        p.add_argument("--vypiska", default="", help="TK-118, В-213: файл шагов прохода, по которому снята выписка из Летописи (guard.py plan --steps)")
        o = p.parse_args(a[1:sep])
        if o.cls == "prod":
            mrc = manifest_check(a[sep + 1:], o.adhoc, o.name)      # КТ-1: бинарник вне манифеста / команда не через обёртку
            if mrc:
                return mrc
        rc, cmd = guard_check(o, a[sep + 1:])       # TK-081, В-196: реестр спрашивается до постановки в очередь
        if rc:
            return rc
        vyp = vypiska_warn(o, parse_dur(o.max_runtime))
        j = submit(o.cls, o.name, o.cores, o.mem, o.disk, o.cwd, cmd, parse_dur(o.max_runtime), o.prio, o.io, o.iso, vypiska=vyp)
        print(j["id"])
        return 0
    print(__doc__)
    return 2


def manifest_check(argv, adhoc, who=""):
    """КТ-1 (TK-145): проверка заявки prod по манифесту сборок; режим — SCHED_MANIFEST (0|warn|1), см. manifest.py.
    Режим 0 проверяется до загрузки manifest.py (откат флагом работает и без файла); файла нет в режиме warn/1 — тревога, заявка идёт."""
    if os.environ.get("SCHED_MANIFEST", "warn") == "0":
        return 0
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest.py")
    if not os.path.isfile(path):
        alert_line(f"manifest.py не найден ({path}): проверка манифеста пропущена")
        return 0
    spec = importlib.util.spec_from_file_location("manifest", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.check_submit(argv, adhoc, alert_line, who)


def guard_check(o, argv):
    """TK-081: реестр спрашивается до очереди. -> (rc, команда для очереди: с хвостом записи результата)."""
    sys.path.insert(0, os.environ.get("REG_TOOLS", "/data/registry"))
    import guard
    cwd = os.getcwd()
    try:
        os.chdir(o.cwd)
        rc, info = guard.check("measure" if o.cls == "measure" else "prod", argv, o.recompute, o.why, o.repeat)
        if rc:
            return rc, None
        pend = shell_quote(info["pending"])
        tail = (f"; __grc=$?; python3 {os.path.dirname(os.path.abspath(guard.__file__))}/guard.py done {o.cls} $__grc --pending {pend}"
                f" || echo 'guard: done не записан (см. done-errors.log)' >&2; (exit $__grc)")
        return 0, f"env GUARD_PENDING={pend} " + " ".join(map(shell_quote, info["argv"])) + tail
    finally:
        os.chdir(cwd)


VYPISKA_MIN_S = 15 * 60


def vypiska_warn(o, max_runtime_s):
    """TK-118, В-213: prod > 15 мин без отметки выписки («что есть / что считаем нового / стоимость») — предупреждение в alerts.log и роли (не отказ).
    -> запись для job json: {"file", "steps", "have", "new", "wrapper_only"} | {"file", "missing": true} | None (не требуется)."""
    if o.cls != "prod" or max_runtime_s <= VYPISKA_MIN_S:
        return None
    sys.path.insert(0, os.environ.get("REG_TOOLS", "/data/registry"))
    import guard
    mk = guard.vypiska_mark(o.vypiska or None)
    msgs = []
    if mk is None:
        msgs.append(f"ВНИМАНИЕ (В-213): заявка {o.name} prod > 15 мин подана без выписки из Летописи"
                    + (f" (--vypiska {o.vypiska}: нет отметки по этому файлу — это файл ШАГОВ или сохранённый вывод plan, снятый guard.py plan)" if o.vypiska else "")
                    + " — снять: python3 /data/registry/guard.py plan --steps <файл шагов> и подать с --vypiska <файл шагов>; считать то же самое нельзя")
    elif mk.get("wrapper_only"):
        msgs.append(f"ВНИМАНИЕ (В-213): заявка {o.name}: выписка {o.vypiska} по ОДНОЙ обёртке (bash скрипт.sh) — шаги внутри не защищены, "
                    "объявить шаги и обернуть их в guard step")
    for msg in msgs:
        print(msg, file=sys.stderr)
        alert_line(msg)
    if mk is None:
        return {"file": o.vypiska or None, "missing": True}
    return {"file": o.vypiska, "steps": mk.get("steps_file"), "have": mk.get("have"), "new": mk.get("new"), "wrapper_only": bool(mk.get("wrapper_only"))}


def shell_quote(s):
    import shlex
    return shlex.quote(s)


if __name__ == "__main__":
    sys.exit(main())
