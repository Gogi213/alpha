#!/usr/bin/env python3
"""alsched.py (TK-071, В-189): планировщик сервера счёта вместо замка-флока benchrun.

Заявка: класс prod (производство: ядра/память/диск по заявке, пакуется на свободные ядра) или measure (замер: эксклюзивное
окно — всё производство на freeze, замер получает все ядра, по выходу размораживается). Всё запускается через него.

  alsched.py submit --name N --max-runtime 2h [--cls prod|measure] [--cores 4] [--mem 8] [--disk hdd1|hdd2|none] [--cwd D] [--recompute --why ТЕКСТ] [--repeat N] -- команда…
  alsched.py wave|stand --max-runtime 30m команда…   # = замер: подать, дождаться окна, показать вывод, вернуть код (обёртка вместо benchrun)
  alsched.py ps | cancel <id> | reprio <id> <prio> | daemon
Состояние: $SCHED_DIR (/data/sched): jobs/<id>.json (создаёт CLI, дальше пишет только демон), cancel/<id>, logs/<id>.log, rc/<id>.
"""
import argparse, json, math, os, re, subprocess, sys, time

DIR = os.environ.get("SCHED_DIR", "/data/sched")
NCPU, MEM_GB, TICK = 16, 56, int(os.environ.get("SCHED_TICK", "5"))
DEAD_S = int(os.environ.get("SCHED_DEAD_S", str(max(60, 10 * TICK))))
WAIT_ALERT_S = 600
UNDER_S = 1800     # п.8(б): окно недогруза заявки
UNDER_FRAC = 0.5   # «заметно меньше» объявленного — меньше половины заявленных ядер
IDLE_S = 600       # п.8(в): очередь пуста при ждущих тикетах дольше этого
SAMPLE_S = 120
BARE_PRIO = 5      # голые юниты (R1) приравнены к prio 5: вытесняются только заявками строже (prio < 5)
PREEMPT_S = int(os.environ.get("SCHED_PREEMPT_S", "600"))   # резерв не стартовал за это время — вытеснение заморозкой
DISK_SLOTS = int(os.environ.get("SCHED_DISK_SLOTS", "16"))   # заданий на диск; 16 = калибровка R1 06.10 (tk071-calib: P=16 на одном HDD, 51 ед/мин, 18 МБ/с, iowait 2 %, ЦП 92 % — упор в ЦП, не в диск); 0 = без лимита
FREEZE_PAT = (os.environ["SCHED_PAT"].split(",") if os.environ.get("SCHED_PAT")   # SCHED_PAT — только для smoke
              else ["tk0*", "t4*", "t5*", "run-*", "tk048-*"])   # как benchrun2: всё, кроме alpha-*
LEGACY_PAT = FREEZE_PAT
PACK = os.environ.get("SCHED_PACK", "claim")   # claim (как было) | fact: пускать из очереди по факту ЦП/памяти, заявка --cores — нижняя оценка (TK-071 v2, п.1)
FACT_BUSY = float(os.environ.get("SCHED_FACT_BUSY", "0.90"))      # старт сверх заявленных ядер, пока загрузка ЦП (EWMA 60 с) ниже этого
FACT_SETTLE_S = int(os.environ.get("SCHED_FACT_SETTLE_S", "30"))  # между стартами по факту: новая задача набирает ЦП не сразу
MEM_RAMP_S = int(os.environ.get("SCHED_MEM_RAMP_S", "180"))      # возраст, до которого задание «добирает» заявленную память
FACT_MEM_GAP_GB = float(os.environ.get("SCHED_FACT_MEM_GAP_GB", "2"))   # запас MemAvailable сверх заявки и недобранного идущими


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
        self.jobs = {}
        self.frozen = False
        self.last = None
        self.snaps = {}
        self.preempt = be.load_preempt()                  # {id головной: [[вид, имя/id, ядер], ...]} — заморожено ради неё

    def add(self, j):
        self.jobs.setdefault(j["id"], j)

    def running(self, cls):
        return [j for j in self.jobs.values() if j["state"] == "running" and j["cls"] == cls]

    def tick(self):
        be, now = self.be, self.be.now()
        dt = 0 if self.last is None else now - self.last
        self.last = now
        for hid, vs in self.preempt.items():
            for kind, ident, _ in vs:
                if kind == "job" and ident in self.jobs:
                    self.jobs[ident]["frozen_for"] = hid
        for j in self.running("prod"):                    # бюджет max_runtime — время БЕЗ заморозки
            if not self.frozen and not j.get("frozen_for"):
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
            self.log_util(now, 0)                         # окно видно в util.log: measure=1 раз в минуту
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
            av, su = be.mem_state()
            risk = mem_risk(av, su, mem_peak_gb())
            if risk:
                be.alert(f"память перед окном {j['id']} {j['name']}: " + "; ".join(risk))
            be.start(j)
            self.log_util(now, 0)
            return
        if self.frozen:
            be.thaw_all()
            self.frozen = False
            for j in self.running("prod"):
                be.extend(j, j["max_runtime"] - j.get("active_s", 0))
        self.thaw_victims()
        used = {c for j in self.running("prod") if not j.get("frozen_for") for c in j["cpus"]}
        mem = sum(j["mem"] for j in self.running("prod"))
        legacy = math.ceil(be.legacy_busy())
        for j in self.jobs.values():                      # приоритет можно менять на ходу: prio/<id> (alsched.py reprio)
            pj = f"{DIR}/prio/{j['id']}"
            if j["state"] == "queued" and os.path.exists(pj):
                try:
                    j["prio"] = int(open(pj).read().strip())
                except ValueError:
                    pass
        fact = self.pack == "fact"
        busy = be.busy_fact() if fact and hasattr(be, "busy_fact") else 1.0
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
                over = (room < j["cores"] and now - self.fact_t >= FACT_SETTLE_S and mem_ok
                        and busy * self.ncpu + self.ramp(now) + j["cores"] <= FACT_BUSY * self.ncpu)
                fits = (room >= j["cores"] or over) and mem_ok and not disk_full
            else:
                fits = room >= j["cores"] and mem + j["mem"] <= self.mem and not disk_full
            if fits and reserve is not None and j["max_runtime"] > reserve["shadow"] \
                    and (j["cores"] > reserve["cores"] or j["mem"] > reserve["mem"]):
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
            be.start(j)
        if head and head["state"] == "queued" and now - head["t_submit"] > PREEMPT_S and head["id"] not in self.preempt                 and mem + head["mem"] <= self.mem:
            self.preempt_for(head, len(range(self.ncpu)) - len(used) - legacy, now)
        self.alert_waiting(now)
        self.alert_underuse(now)
        self.alert_idle(now)
        self.log_util(now, legacy)

    def ramp(self, now):
        """Недобор EWMA по свежим заданиям: EWMA с τ = 60 с видит лишь долю 1 − e^(−a/60) нагрузки задания возраста a;
        недостающее ≤ cores·e^(−a/60) (заявка — нижняя оценка, берём её). Старше 180 с — ноль."""
        return sum(r["cores"] * math.exp(-(now - r["t_start"]) / 60) for r in self.running("prod")
                   if not r.get("frozen_for") and now - r["t_start"] < 180)

    def mem_fits(self, j):
        """Память не эластична: MemAvailable (в нём и tmpfs) покрывает заявку j и недобранное идущими до их заявки (max(заявка, факт))."""
        av, _ = self.be.mem_state()
        now = self.be.now()
        gap = sum(max(0.0, r["mem"] - (self.be.job_mem(r) if hasattr(self.be, "job_mem") else 0.0)) for r in self.running("prod")
                  if now - r["t_start"] < MEM_RAMP_S)   # старше MEM_RAMP_S — факт уже в MemAvailable (23:08: по заявке 41 ГБ против факта 17,5 стоял весь счёт)
        return av >= j["mem"] + gap + FACT_MEM_GAP_GB

    def preempt_for(self, head, room, now):
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
        if got < need and head.get("prio", 5) < BARE_PRIO:
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
    if d["own_disk_b"] > 0 and d["disk_b"] > 0 and foreign_t > tol:
        why.append(f"чужое чтение диска {(d['disk_b'] - d['own_disk_b']) / d['disk_b'] * 100:.1f} % байт = {foreign_t * 100:.1f} % времени окна")
    ios, own_ios = d.get("ios", 0), d.get("own_ios", 0)
    bg = (BG_OPS_S if bg_ops_s is None else bg_ops_s) * d["wall_s"]
    extra = ios - own_ios - bg                                         # чужие операции сверх фона
    if own_ios > 0 and d["wall_s"] > 0 and extra * SEEK_S / max(d["wall_s"], MIN_WALL_S) > tol:     # мелкие чтения HDD: байт мало, поисков много
        why.append(f"чужие чтения диска: {extra:.0f} оп сверх фона × {SEEK_S * 1000:.1f} мс = {extra * SEEK_S / max(d['wall_s'], MIN_WALL_S) * 100:.1f} % времени окна")
    if d.get("culprits") and any("диска" in w for w in why):
        why.append("кто читал диск (оп, МБ): " + "; ".join(f"{c['cgroup']} {c['ops']} оп {c['mb']} МБ" for c in d["culprits"]))
    if d.get("swap_pages", 0) > 0:
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
        with open(f"{DIR}/alerts.log", "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + text + "\n")

    def host_busy(self):
        """Доля занятых ядер хоста с прошлого вызова (/proc/stat: 1 − (idle+iowait)/сумма)."""
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        tot, idle = sum(v), v[3] + v[4]
        t0, i0 = getattr(self, "_hb", (tot, idle))
        self._hb = (tot, idle)
        return 1 - (idle - i0) / (tot - t0) if tot > t0 else 0.0

    def busy_fact(self):
        """Загрузка ЦП хоста для пакования по факту: EWMA с постоянной 60 с (отдельное состояние от host_busy)."""
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
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

    def cancelled(self, j):
        return os.path.exists(f"{DIR}/cancel/{j['id']}")

    def unit(self, j):
        return ("alpha-sm-" if j["cls"] == "measure" else "tk0s-") + f"{j['name']}-{j['id']}"

    def slice(self, j):
        return f"alsm{j['id']}.slice"     # замер и вложенные systemd-run волны — одна cgroup: учёт ЦП/диска/памяти волны целиком

    def start(self, j):
        os.makedirs(f"{DIR}/logs", exist_ok=True)
        os.makedirs(f"{DIR}/rc", exist_ok=True)
        cpus = ",".join(map(str, range(NCPU) if j.get("unpinned") else j["cpus"]))
        os.makedirs(f"{DIR}/own", exist_ok=True)     # итог ЦП/диска юнита снимает сам юнит перед выходом (после выхода cgroup исчезает)
        fin = (f"cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); [ -n \"$ALSCHED_SLICE\" ] && cg=/sys/fs/cgroup/$ALSCHED_SLICE; {{ cat $cg/io.stat; grep usage_usec $cg/cpu.stat; }} "
               f"> {DIR}/own/{j['id']} 2>/dev/null; ")
        pre = f"export ALSCHED_SLICE={self.slice(j)} PATH={DIR}/shim:$PATH\n" if j["cls"] == "measure" else ""
        inner = f"{pre}{j['cmd']}\nrc=$?; {fin}echo $rc > {DIR}/rc/{j['id']}; exit $rc"
        if j["cls"] == "measure":
            self.arm_failsafe(j["max_runtime"] + 2 * TICK)
        sl = ["-p", f"Slice={self.slice(j)}"] if j["cls"] == "measure" else []
        r = sh("systemd-run", f"--unit={self.unit(j)}", "--collect", *sl, f"--working-directory={j['cwd']}",
               "-p", f"RuntimeMaxSec={int(j['max_runtime'])}", "-p", "IOAccounting=yes", "-p", "CPUAccounting=yes",
               "-p", f"AllowedCPUs={cpus}", "-p", f"MemoryMax={j['mem']}G", "-p", "MemorySwapMax=0", "-p", f"CPUQuota={j['cores'] * 100 if j.get('unpinned') else len(j['cpus']) * 100}%",
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
            except OSError:
                pass
        rb = rn = 0
        try:
            if cg in ("", "/"):
                raise OSError
            for l in open(f"/sys/fs/cgroup{cg}/io.stat"):
                rb += sum(int(x.split("=")[1]) for x in l.split() if x.startswith("rbytes="))
                rn += sum(int(x.split("=")[1]) for x in l.split() if x.startswith("rios="))
        except OSError:
            pass
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
        return dict(t=time.time(), cpu=self.cpu_host(), disk=b, ios=n, units=set(self.foreign_units(j)), dcpu=self.daemon_cpu(), cg=self.cg_io(), swap=self.swap_pages())

    def win_end(self, j, s0):
        t1 = time.time()
        own_cpu, own_rb, own_rn = self._last_own.get(j["id"], (0.0, 0, 0))
        hb, hn = self.disk_host()
        try:
            forced = os.path.getmtime(f"{DIR}/forced-thaw") >= s0["t"]
        except OSError:
            forced = False
        d = dict(wall_s=t1 - s0["t"], cpu_s=self.cpu_host() - s0["cpu"], own_cpu_s=own_cpu,
                 daemon_cpu_s=self.daemon_cpu() - s0["dcpu"], disk_b=hb - s0["disk"], own_disk_b=own_rb, ios=hn - s0["ios"], own_ios=own_rn, forced_thaw=forced, swap_pages=self.swap_pages() - s0["swap"],
                 foreign_units=s0["units"] | set(self.foreign_units(j)))
        d["culprits"] = self.culprits(j, s0["cg"], self.cg_io(), d["ios"] - d["own_ios"], d["disk_b"] - d["own_disk_b"], s0["t"])
        ok, why = judge_window(d)
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
            except Exception:
                pass
            try:
                new = self.unit_cpu_io(j)
                old = self._last_own.get(j["id"], (0.0, 0, 0))
                self._last_own[j["id"]] = tuple(max(a, b) for a, b in zip(new, old))
                fin = self.read_final(j)
                if fin:
                    self._last_own[j["id"]] = fin
            except Exception:
                pass
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


def submit(cls, name, cores, mem, disk, cwd, cmd, max_runtime, prio=5, io=""):
    os.makedirs(f"{DIR}/jobs", exist_ok=True)
    jid = time.strftime("%m%d%H%M%S") + f"{os.getpid() % 1000:03d}"
    j = dict(id=jid, name=name, cls=cls, cores=cores if cls == "prod" else NCPU, mem=mem, disk=disk, cwd=cwd, cmd=cmd,
             prio=prio, io=io, max_runtime=max_runtime, active_s=0, state="queued", t_submit=time.time(), cpus=[])
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
        core.frozen = True
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
        while a[1:2] and a[1] in ("--recompute", "--why", "--repeat"):     # TK-081: повтор замера — только явно
            if a[1] == "--recompute":
                g.recompute = True; a = a[:1] + a[2:]
            else:
                g.__dict__[a[1][2:]] = int(a[2]) if a[1] == "--repeat" else a[2]; a = a[:1] + a[3:]
        if len(a) < 4 or a[1] != "--max-runtime":
            print(f"alsched.py {a[0]} --max-runtime <срок: 90s|30m|2h> команда… (срок обязателен: окно замера = аренда с TTL)")
            return 2
        grc, gcmd = guard_check(g, a[3:])
        if grc:
            return grc
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
        open(f"{DIR}/cancel/{a[1]}", "w").close()
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
        p.add_argument("--prio", type=int, default=5)
        p.add_argument("--recompute", action="store_true", help="пересчитать уже посчитанное (нужен --why)")
        p.add_argument("--why", default="", help="причина пересчёта; пишется в реестр")
        p.add_argument("--repeat", type=int, default=0, help="замер: всего N прогонов с тем же отпечатком (против шума)")
        o = p.parse_args(a[1:sep])
        rc, cmd = guard_check(o, a[sep + 1:])       # TK-081, В-196: реестр спрашивается до постановки в очередь
        if rc:
            return rc
        j = submit(o.cls, o.name, o.cores, o.mem, o.disk, o.cwd, cmd, parse_dur(o.max_runtime), o.prio, o.io)
        print(j["id"])
        return 0
    print(__doc__)
    return 2


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


def shell_quote(s):
    import shlex
    return shlex.quote(s)


if __name__ == "__main__":
    sys.exit(main())
