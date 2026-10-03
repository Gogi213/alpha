#!/usr/bin/env python3
"""Сборщик экрана хода работ (без модели): раз в 5 с пишет `.claude/pulse/status.json` — всё для кадра.

Источники: тикеты и диспетчер (`.claude/tickets`, `.claude/dispatcher`), лента `ceo-wake.log`, цель `.claude/pulse-goal.txt`,
по ОДНОМУ долгоживущему ssh на машину (счёт, VPS, коллектор; параллельно, с переподключением): ЦП/ОЗУ/диск, процессы проекта,
ход задач `/data/progress/*.json` (пишет `tools/compute/progress.sh`). Этот ПК — своими силами, Steam Deck не опрашивается.
Запуск: `pulse.py` / `mcp_server.py` поднимают сборщик сами (`ensure_collector`); вручную: `python tools/pulse/collect.py`.
Остановка: создать файл `.claude/pulse/stop`. Экран читает status.json — `pulse.py`, MCP-сервер, страница-артефакт.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISP = ROOT / ".claude" / "dispatcher"
sys.path.insert(0, str(DISP))  # ticket.py — разбор тикетов (stdlib)
PULSE_DIR = ROOT / ".claude" / "pulse"
STATUS = PULSE_DIR / "status.json"
PIDFILE = PULSE_DIR / "collect.pid"
STOP = PULSE_DIR / "stop"
GOAL = ROOT / ".claude" / "pulse-goal.txt"
TICKETS = ROOT / ".claude" / "tickets"
WAKE_LOG = DISP / "ceo-wake.log"
TZ = timezone(timedelta(hours=4))  # GMT+4
TICK_S = 5
STALE_S = 30
WAITING_MAX_AGE_S = 12 * 3600  # waiting-тикет старше — не показывать (сироты)

# --- машины: по одному ssh на каждую; ключ один (id_rsa) — проверено BatchMode=yes на всех трёх ------------------------
SSH_DIR = Path.home() / ".ssh"
SSH_EXE = r"C:\Windows\System32\OpenSSH\ssh.exe"
if not os.path.exists(SSH_EXE):
    SSH_EXE = shutil.which("ssh") or "ssh"
SSH_OPTS = ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3", "-o", f"UserKnownHostsFile={(SSH_DIR / 'known_hosts').as_posix()}",
            "-i", (SSH_DIR / "id_rsa").as_posix()]
HOSTS = [
    {"id": "calc", "name": "сервер счёта", "target": "root@89.163.242.211", "units": "", "lock": ""},
    {"id": "vps", "name": "VPS София", "target": "root@13.140.29.171", "units": "",
     "lock": "/opt/alpha-compute/.build.lock"},
    {"id": "collector", "name": "коллектор", "target": "ubuntu@139.99.91.22", "units": "alpha-collector", "lock": ""},
]
UNIT_RU = {"alpha-collector": "коллектор"}
STATE_RU = {"active": "работает", "activating": "запускается", "inactive": "остановлен", "failed": "остановлен (сбой)",
            "deactivating": "останавливается"}

# Удалённый цикл: раз в 5 с блок строк `S`(счётчики) `R`(процессы проекта) `U`(юниты) `L`(кто держит замок сборки)
# `P`(ход задач) и разделитель `---`. Сам сервер ничего не считает — дельты считает сборщик.
REMOTE = r'''
UNITS='@UNITS@'; LOCK='@LOCK@'
RE='tk0[0-9]+|alpha|lob |cargo|rustc|/opt/alpha|/data/|tools/compute|xargs'
d=1
while :; do
  echo "S $(cut -d' ' -f1 /proc/uptime) $(awk '/^cpu /{t=0;for(i=2;i<=9;i++)t+=$i;print t,$5+$6}' /proc/stat) $(awk '$3~/^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme[0-9]+n[0-9]+)$/{s+=$6+$10}END{print s+0}' /proc/diskstats) $(awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}END{print t,a}' /proc/meminfo)"
  ps -eo pid=,etimes=,times=,unit=,comm=,args= 2>/dev/null | awk -v re="$RE" '$5!~/^(awk|ps|find|sort|tr|cut|sleep|sed|basename|systemctl)$/ && $0~re {print "R " substr($0,1,200)}'
  for u in $UNITS; do echo "U $u $(systemctl is-active $u 2>/dev/null)"; done
  if [ -n "$LOCK" ]; then for p in $(find /proc/[0-9]*/fd -maxdepth 1 -lname "$LOCK" 2>/dev/null | cut -d/ -f3 | sort -un | head -1); do echo "L $(ps -o etimes=,args= -p $p | cut -c1-300)"; done; fi
  find /data/progress -maxdepth 1 -name '*.json' -mmin -30 2>/dev/null | sort | while IFS= read -r f; do printf 'P %s ' "$(basename "$f" .json)"; tr -d '\n\r' < "$f"; echo; done
  echo '---'
  sleep $d; d=5
done
'''

ROLE_RU = {"engineer": "Инженер", "researcher": "Исследователь", "judge": "Судья", "ceo": "CEO"}
WORK_ROLES = ("engineer", "judge", "researcher")
ACTIVE = ("todo", "in_progress", "waiting", "in_review", "needs_owner")


# --- мелочи -----------------------------------------------------------------------------------------------------------
def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def fmt_num(x) -> str:
    if isinstance(x, float) and not x.is_integer():
        return f"{x:,.1f}".replace(",", " ")
    return f"{int(x):,}".replace(",", " ")


def fmt_min(m) -> str:
    m = int(m)
    if m < 1:
        return "<1 мин"
    if m < 60:
        return f"{m} мин"
    if m < 48 * 60:
        return f"{m // 60} ч {m % 60:02d} мин"
    return f"{m // 1440} сут {m % 1440 // 60} ч"


def eta_text(m) -> str:
    if m < 1.5:
        return "ещё <1 мин"
    if m < 90:
        return f"ещё ~{round(m)} мин"
    if m < 600:
        return f"ещё ~{int(m // 60)} ч {int(m % 60):02d} мин"
    return f"ещё ~{round(m / 60)} ч"


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def clean_text(text: str) -> str:
    """Текст записи лога одной строкой: без markdown-таблиц, заголовков, оград кода и разметки."""
    keep = []
    for ln in text.splitlines():
        t = ln.strip()
        if not t or t.startswith(("|", "#", "```")) or t.count("|") >= 2 or re.fullmatch(r"[-=*_ |:]+", t):
            continue
        keep.append(re.sub(r"^([-*•]|\d+[.)])\s+", "", t))
    t = re.sub(r"[`*]+", "", " ".join(keep))
    return re.sub(r"\s+", " ", t).strip()


def first_phrase(text: str, limit: int, semi: bool = True) -> str:
    """Первая осмысленная фраза: до «. » / «;» (не внутри «т.е.») или `limit` символов."""
    t = clean_text(text)
    for m in re.finditer(r"\.(?=\s|$)" + (r"|;" if semi else ""), t):
        if m.group() == "." and re.search(r"\w\.\w$", t[max(0, m.start() - 3):m.start()]):
            continue
        t = t[: m.start()]
        break
    return clip(t.rstrip(" .;"), limit)


def short_title(title: str, n: int = 24) -> str:
    t = re.split(r":| — |,| \(", title or "", maxsplit=1)[0].strip()
    if len(t) > n:
        cut = t[: n - 1]
        sp = cut.rfind(" ")
        t = (cut[:sp] if sp >= 8 else cut).rstrip(" ,.-") + "…"
    return t


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# --- машины по ssh ----------------------------------------------------------------------------------------------------
def proc_name(args: str) -> str:
    toks = args.split()
    if not toks:
        return "?"
    exe = os.path.basename(toks[0])
    if re.fullmatch(r"(bash|sh|python[0-9.]*)", exe):
        for tk in toks[1:]:
            if not tk.startswith("-"):
                return os.path.basename(tk)
    return exe


class Feed(threading.Thread):
    """Одно долгоживущее ssh на машину: читает блоки, считает дельты (ЦП, диск, ЦП процессов), переподключается."""

    def __init__(self, host: dict):
        super().__init__(daemon=True)
        self.host = host
        self.lock = threading.Lock()
        self.proc = None
        self.state = "connecting"
        self.progress: dict = {}
        self.block_at = 0.0
        self.snap = {}
        self.prevS = None
        self.samples: deque = deque(maxlen=12)  # (uptime, {pid: times})
        self.stopping = False

    def run(self):
        script = REMOTE.replace("@UNITS@", self.host["units"]).replace("@LOCK@", self.host["lock"])
        while not self.stopping:
            try:
                self.proc = subprocess.Popen(
                    [SSH_EXE, *SSH_OPTS, self.host["target"], "bash -s"], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=0x00000200 | 0x08000000)  # NEW_PROCESS_GROUP | NO_WINDOW
                self.proc.stdin.write(script.encode("utf-8"))
                self.proc.stdin.flush()
                blk: dict = {"R": [], "U": [], "L": None, "P": {}}
                for raw in self.proc.stdout:
                    ln = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if ln == "---":
                        self._commit(blk)
                        blk = {"R": [], "U": [], "L": None, "P": {}}
                    elif ln.startswith("S "):
                        blk["S"] = ln[2:].split()
                    elif ln.startswith("R "):
                        blk["R"].append(ln[2:])
                    elif ln.startswith("U "):
                        blk["U"].append(ln[2:].split())
                    elif ln.startswith("L "):
                        blk["L"] = ln[2:]
                    elif ln.startswith("P "):
                        job, _, js = ln[2:].partition(" ")
                        try:
                            blk["P"][job] = json.loads(js)
                        except ValueError:
                            pass
            except Exception:
                pass
            with self.lock:
                self.state = "down"
                self.snap = {}
                self.prevS = None
                self.samples.clear()
            self.kill()
            for _ in range(10):  # пауза перед переподключением
                if self.stopping:
                    return
                time.sleep(0.5)

    def kill(self):
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def watchdog(self):
        """Тихий обрыв: блока нет > 25 с — рвём, поток переподключится."""
        if self.state == "ok" and time.time() - self.block_at > 25:
            self.kill()

    def _commit(self, blk: dict):
        S = blk.get("S")
        if not S or len(S) < 6:
            return
        try:
            up, tot, idle, sect, mt, ma = (float(x) for x in S[:6])
        except ValueError:
            return
        snap = {"cpu": None, "disk_mb_s": None, "mem": round(100 * (1 - ma / mt)) if mt else None}
        if self.prevS and up - self.prevS[0] >= 0.5:
            dt = up - self.prevS[0]
            dtot = tot - self.prevS[1]
            if dtot > 0:
                snap["cpu"] = round(100 * (1 - (idle - self.prevS[2]) / dtot))
            snap["disk_mb_s"] = round(max(0.0, (sect - self.prevS[3]) * 512 / dt / 1e6))
        self.prevS = (up, tot, idle, sect)
        # процессы: ЦП группы = Σ дельт `times` (целые секунды) за окно ≥ 4 с (до 30 с)
        rows = []
        for r in blk["R"]:
            f = r.split(None, 5)
            if len(f) < 6:
                continue
            try:
                rows.append((int(f[0]), int(f[1]), int(f[2]), f[3], f[5]))
            except ValueError:
                continue
        cur = {pid: times for pid, _, times, _, _ in rows}
        old = None
        for u0, m0 in self.samples:
            if up - u0 <= 30:
                old = (u0, m0)
                break
        warm = bool(old and up - old[0] >= 4)
        groups: dict = {}
        for pid, etimes, times, unit, args in rows:
            name = unit[:-8] if unit.endswith(".service") else proc_name(args)
            g = groups.setdefault(name, {"name": name, "cpu": 0.0, "etimes": 0, "count": 0})
            g["count"] += 1
            g["etimes"] = max(g["etimes"], etimes)
            if warm:
                g["cpu"] += max(0, times - old[1].get(pid, 0)) / (up - old[0]) * 100
        self.samples.append((up, cur))
        procs = sorted(groups.values(), key=lambda g: (-g["cpu"], -g["etimes"]))
        snap["warm"] = warm
        snap["procs"] = [{"name": g["name"], "cpu": round(g["cpu"]) if warm else None,
                          "minutes": g["etimes"] // 60, "count": g["count"]} for g in procs]
        snap["units"] = [{"name": u[0], "state": u[1] if len(u) > 1 else "?"} for u in blk["U"]]
        snap["lock"] = None
        if self.host["lock"]:
            snap["lock"] = {"busy": False}
            if blk["L"]:
                et, _, args = blk["L"].strip().partition(" ")
                m = re.search(r"cargo\s+(test|clippy|build|fmt|check)", args)
                what = f"cargo {m.group(1)}" if m else clip(args.split(self.host["lock"], 1)[-1].strip(), 40)
                snap["lock"] = {"busy": True, "what": what, "minutes": int(et) // 60 if et.isdigit() else 0}
        with self.lock:
            self.state = "ok"
            self.block_at = time.time()
            self.snap = snap
            self.progress = blk["P"]

    def get(self):
        with self.lock:
            return self.state, dict(self.snap), dict(self.progress if self.state == "ok" else {})


# --- этот ПК ----------------------------------------------------------------------------------------------------------
class Pc:
    class _Mem(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    def __init__(self):
        self.prev = self._times()

    @staticmethod
    def _times():
        idle, kern, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        return idle.value, kern.value + user.value  # ядро уже включает простой

    def sample(self):
        idle, total = self._times()
        di, dt = idle - self.prev[0], total - self.prev[1]
        self.prev = (idle, total)
        cpu = round(100 * (1 - di / dt)) if dt > 0 else None
        m = self._Mem()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return cpu, int(m.dwMemoryLoad)


# --- тикеты, диспетчер, лента -----------------------------------------------------------------------------------------
_tk_cache: dict = {}
_state_cache: dict = {}


def load_tickets() -> dict:
    import ticket as T
    out = {}
    for p in sorted(TICKETS.glob("TK-*.md")):
        try:
            st = p.stat()
            key = (st.st_mtime_ns, st.st_size)
            hit = _tk_cache.get(p.name)
            if not hit or hit[0] != key:
                hit = (key, T.read_ticket(p))
                _tk_cache[p.name] = hit
            out[hit[1].id or p.stem] = hit[1]
        except Exception:
            continue
    return out


_arch_cache: dict = {}


def all_entries(t) -> list:
    """Записи лога тикета + перенесённые в `archive/<ID>-log.md` (компакция лога уносит старые), по времени."""
    import ticket as T
    ents = list(t.log)
    ap = TICKETS / "archive" / f"{t.id}-log.md"
    try:
        st = ap.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = _arch_cache.get(ap.name)
        if not hit or hit[0] != key:
            hit = (key, T._parse_log("## Лог\n" + ap.read_text(encoding="utf-8")))
            _arch_cache[ap.name] = hit
        ents += hit[1]
    except OSError:
        pass
    return sorted(ents, key=lambda e: e.ts)


def load_state() -> dict:
    s = read_json(DISP / "state.json")
    if isinstance(s, dict):
        _state_cache["s"] = s
    return _state_cache.get("s", {})


def wait_text(t) -> str:
    nxt = t.next_role
    if nxt == "ceo":
        return "ждёт: CEO"
    if nxt:
        return f"в очереди: {ROLE_RU.get(nxt, nxt)}"
    st = t.status
    if st in ("todo", "in_progress"):
        return "в очереди"
    if st == "in_review":
        return f"ждёт: {ROLE_RU.get(t.reviewer or 'judge', 'Судья')}"
    if st == "needs_owner":
        return "ждёт: владельца"
    wf = (t.header.get("wait_for") or "").strip()
    low = wf.lower()
    if not wf or low.startswith("ceo"):
        return "ждёт: CEO"
    if low.startswith("ticket:"):
        return f"ждёт: {wf[7:].strip().split()[0]}"
    if low.startswith("mention"):
        return "ждёт: ответа"
    if low.startswith("deck:"):
        return "ждёт: Steam Deck"
    path = wf[5:].strip() if low.startswith("file:") else wf
    if path.startswith("/"):
        base = os.path.basename(path.split()[0])
        return f"ждёт: {'сервер' if path.startswith('/data') else 'файл'} → {base}"
    return "ждёт: " + clip(wf, 40)


def ticket_row_role(t, run) -> str:
    if run:
        return run.get("role") or t.owner
    if t.next_role and t.next_role != "ceo":
        return t.next_role
    if t.status == "in_review":
        return t.reviewer or "judge"
    return t.owner


def next_hint(t):
    for e in reversed(t.log[-3:]):
        m = re.search(r"[Дд]альше\s*[:—-]\s*(.+)", e.text, re.S)
        if m:
            return first_phrase(m.group(1), 120, semi=False)
    return None


class EtaWindows:
    """Скользящее окно (10 мин) по (время обновления, done): скорость = Δdone/Δt, ETA = остаток / скорость."""

    def __init__(self):
        self.w: dict = {}

    def eta(self, key, p) -> float | None:
        try:
            done, total = float(p["done"]), float(p["total"])
        except (KeyError, TypeError, ValueError):
            return None
        try:
            t = datetime.fromisoformat(str(p.get("updated"))).timestamp()
        except ValueError:
            t = time.time()
        dq = self.w.setdefault(key, deque(maxlen=300))
        if dq and done < dq[-1][1]:
            dq.clear()  # задачу перезапустили
        if not dq or t > dq[-1][0]:
            dq.append((t, done))
        while len(dq) > 2 and dq[-1][0] - dq[0][0] > 600:
            dq.popleft()
        if done >= total:
            return 0.0
        (t0, d0), (t1, d1) = dq[0], dq[-1]
        if t1 - t0 >= 20 and d1 > d0:
            return (total - done) / ((d1 - d0) / (t1 - t0)) / 60
        return None


def read_events(tickets: dict, limit: int = 6) -> list:
    try:
        lines = WAKE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
    except OSError:
        return []
    out = []
    prefix = {"done": "готово · ", "blocked": "стоп · ", "needs_owner": "нужен владелец · "}
    for ln in lines:
        p = ln.split(None, 2)
        if len(p) < 3:
            continue
        ts_raw, who, rest = p
        kind = rest.split()[0]
        if kind in ("watch-orphan-ticket", "watch-blocked"):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        text = None
        t = tickets.get(who)
        if t:  # запись лога пишется за секунды до строки wake-лога: берём последнюю с ts ≤ событие (+5 с на часы)
            ent = [e for e in all_entries(t) if e.ts <= ts + timedelta(seconds=5)]
            ph = first_phrase(ent[-1].text, 90) if ent else ""
            if ph:
                text = prefix.get(kind, "") + ph
        if text is None:
            text = clip(rest, 90)
        out.append({"ts": ts.astimezone(TZ).isoformat(timespec="seconds"), "time": ts.astimezone(TZ).strftime("%H:%M"),
                    "who": who if who != "*" else "—", "text": text, "_t": ts.timestamp()})
    ded = []
    for e in out:
        if ded and ded[-1]["who"] == e["who"] and abs(ded[-1]["_t"] - e["_t"]) < 60:
            ded[-1] = e
        else:
            ded.append(e)
    for e in ded:
        e.pop("_t", None)
    return ded[-limit:]


# --- сборка кадра -----------------------------------------------------------------------------------------------------
def machine_view(host: dict, state: str, snap: dict) -> dict:
    m = {"id": host["id"], "name": host["name"], "host": host["target"].split("@")[1], "state": state,
         "cpu": snap.get("cpu"), "mem": snap.get("mem"), "disk_mb_s": snap.get("disk_mb_s"),
         "procs": snap.get("procs", []), "units": snap.get("units", []), "lock": snap.get("lock"), "lines": []}
    lines = []
    for u in m["units"]:
        lines.append(f"{UNIT_RU.get(u['name'], u['name'])}: {STATE_RU.get(u['state'], u['state'])}")
    if m["lock"] is not None:
        lk = m["lock"]
        lines.append(f"сборка: {lk['what']} · {fmt_min(lk['minutes'])}" if lk.get("busy") else "сборка: свободна")
    for g in m["procs"]:
        cpu = "…" if g["cpu"] is None else f"{g['cpu']} %"
        n = f" ×{g['count']}" if g["count"] > 1 and not g["name"].startswith("tk0") else ""
        lines.append(f"{g['name']}{n} · {cpu} · {fmt_min(g['minutes'])}")
    m["lines"] = lines[:4]
    return m


def build(feeds, pc, etas) -> dict:
    now = time.time()
    tickets = load_tickets()
    state = load_state()

    # живые сессии ролей
    live = {}
    for tid, r in (state.get("active_runs") or {}).items():
        if pid_alive(r.get("pid")):
            try:
                started = datetime.fromisoformat(r["started"]).timestamp()
            except (KeyError, ValueError):
                started = now
            live[tid] = {"role": r.get("role", ""), "minutes": int((now - started) // 60)}

    # машины и ход задач
    machines, jobs_by_ticket = [], {}
    for f in feeds:
        f.watchdog()
        st, snap, prog = f.get()
        machines.append(machine_view(f.host, st, snap))
        for job, p in prog.items():
            try:
                done, total = float(p["done"]), float(p["total"])
            except (KeyError, TypeError, ValueError):
                continue
            eta = etas.eta((f.host["id"], job), p)
            prog = f"{fmt_num(done)}/{fmt_num(total)} {p.get('unit', '')}".strip()
            etxt = "готово" if done >= total else (eta_text(eta) if eta is not None else None)
            txt = f"{p.get('step', '')} {prog}".strip() + (f" · {etxt}" if etxt else "")
            jobs_by_ticket.setdefault(str(p.get("ticket", "")).upper(), []).append(
                {"machine": f.host["id"], "job": job, "step": p.get("step", ""), "done": done, "total": total,
                 "unit": p.get("unit", ""), "pct": round(100 * done / total) if total else 0, "progress": prog, "eta": etxt,
                 "eta_min": None if eta is None else round(eta, 1), "text": txt, "next": p.get("next") or None})

    # строки тикетов
    rows = []
    for tid, t in tickets.items():
        run = live.get(tid)
        jobs = jobs_by_ticket.pop(tid, [])
        age = now - t_updated(t, now)
        show = run or jobs or t.status in ("todo", "in_progress", "in_review", "needs_owner") or \
            (t.status == "waiting" and age < WAITING_MAX_AGE_S)
        if not show:
            continue
        role = ticket_row_role(t, run)
        hint = next((j["next"] for j in jobs if j["next"]), None) or next_hint(t)
        hint = re.sub(r"^[Дд]альше\s*:\s*", "", hint) if hint else hint
        rows.append({"id": tid, "title": short_title(t.header.get("title", "")), "role": ROLE_RU.get(role, role),
                     "live": bool(run), "live_min": run["minutes"] if run else None,
                     "status": f"работает {fmt_min(run['minutes'])}" if run else wait_text(t),
                     "jobs": jobs, "next": hint})
    for tid, jobs in jobs_by_ticket.items():  # прогресс есть, тикета нет среди найденных
        rows.append({"id": tid, "title": "", "role": "", "live": False, "live_min": None, "status": "", "jobs": jobs,
                     "next": next((j["next"] for j in jobs if j["next"]), None)})
    rows.sort(key=lambda r: r["id"])

    busy = {r.get("role") for r in live.values()}
    roles_free = [ROLE_RU[r] for r in WORK_ROLES if r not in busy]

    # этот ПК
    cpu, mem = pc.sample()
    tick_age = None
    try:
        tick_age = int(now - datetime.fromisoformat(state["last_tick"]).timestamp())
    except (KeyError, ValueError):
        pass
    disp_ok = pid_alive(read_pid(DISP / "dispatch.pid")) and tick_age is not None and tick_age <= 90
    hb = read_json(DISP / "watch-heartbeat.json") or {}
    try:
        hb_age = int(now - datetime.fromisoformat(hb["ts"]).timestamp())
    except (KeyError, ValueError):
        hb_age = None
    watch_ok = pid_alive(read_pid(DISP / "watch.pid")) and hb_age is not None and hb_age <= 360
    pc_lines = [f"диспетчер: {'работает' if disp_ok else 'НЕ РАБОТАЕТ'} · сторож: {'работает' if watch_ok else 'НЕ РАБОТАЕТ'}"]
    for tid, r in sorted(live.items()):
        pc_lines.append(f"{ROLE_RU.get(r['role'], r['role'])} · {tid} · {fmt_min(r['minutes'])}")
    machines.append({"id": "pc", "name": "этот ПК", "host": "localhost", "state": "ok", "cpu": cpu, "mem": mem,
                     "disk_mb_s": None, "procs": [], "units": [], "lock": None, "lines": pc_lines[:4]})
    deck_off = (DISP / "deck-off").exists()
    machines.append({"id": "deck", "name": "Steam Deck", "host": "192.168.1.49", "state": "off", "cpu": None, "mem": None,
                     "disk_mb_s": None, "procs": [], "units": [], "lock": None,
                     "lines": ["выведен" if deck_off else "не опрашивается"]})

    try:
        goal = next((ln.strip() for ln in GOAL.read_text(encoding="utf-8").splitlines() if ln.strip()), "")
    except OSError:
        goal = ""
    return {"v": 1, "built_at": datetime.now(TZ).isoformat(timespec="seconds"), "built_ts": now, "pid": os.getpid(),
            "goal": goal, "dispatcher": {"ok": disp_ok, "tick_age_s": tick_age, "watch_ok": watch_ok},
            "tickets": rows, "roles_free": roles_free, "machines": machines, "events": read_events(tickets),
            "error": None}


def t_updated(t, default: float) -> float:
    try:
        return datetime.fromisoformat(t.header.get("updated", "")).timestamp()
    except ValueError:
        return default


def read_pid(path: Path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


# --- запуск / общие помощники для pulse.py и mcp_server.py -----------------------------------------------------------
def read_status():
    return read_json(STATUS)


def status_fresh(st) -> bool:
    return bool(st) and time.time() - float(st.get("built_ts", 0)) < STALE_S


def ensure_collector() -> bool:
    """Поднять сборщик в фоне, если status.json нет/устарел > 30 с и свежий сборщик не стартует прямо сейчас."""
    if status_fresh(read_status()):
        return False
    pid = read_pid(PIDFILE)
    try:
        starting = time.time() - PIDFILE.stat().st_mtime < 40
    except OSError:
        starting = False
    if pid and pid_alive(pid) and starting:
        return False
    PULSE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([sys.executable, str(Path(__file__).resolve())], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=str(ROOT), close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    return True


def main() -> int:
    PULSE_DIR.mkdir(parents=True, exist_ok=True)
    other = read_pid(PIDFILE)
    if other and other != os.getpid() and pid_alive(other) and \
            (status_fresh(read_status()) or time.time() - PIDFILE.stat().st_mtime < 40):
        return 0  # уже работает
    PIDFILE.write_text(str(os.getpid()))
    STOP.unlink(missing_ok=True)
    import ticket as T
    feeds = [Feed(h) for h in HOSTS]
    for f in feeds:
        f.start()
    pc, etas, last = Pc(), EtaWindows(), None
    time.sleep(1.0)
    try:
        while True:
            t0 = time.time()
            try:
                st = last = build(feeds, pc, etas)
            except Exception as e:  # не умираем: прежний кадр + текст ошибки
                st = dict(last or {"v": 1, "tickets": [], "machines": [], "events": [], "roles_free": [], "goal": ""})
                st.update(built_at=datetime.now(TZ).isoformat(timespec="seconds"), built_ts=time.time(),
                          pid=os.getpid(), error=f"{type(e).__name__}: {e}")
            T.atomic_write_text(STATUS, json.dumps(st, ensure_ascii=False, indent=1))
            PIDFILE.touch()
            if STOP.exists():
                break
            time.sleep(max(0.2, TICK_S - (time.time() - t0)))
    finally:
        for f in feeds:
            f.stopping = True
            f.kill()
        STOP.unlink(missing_ok=True)
        PIDFILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
