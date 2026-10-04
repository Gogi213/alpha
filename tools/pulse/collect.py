#!/usr/bin/env python3
"""Сборщик экрана хода работ (без модели): раз в 5 с пишет `.claude/pulse/status.json` — всё для кадра.

Источники: тикеты и диспетчер (`.claude/tickets`, `.claude/dispatcher`), лента `ceo-wake.log`, цель `.claude/pulse-goal.txt`,
человеческие тексты (названия задач и шагов, «дальше», вопросы владельцу, что было, старые процессы без задачи) —
переводчик на Haiku `plainify.py` (поток в этом процессе, зовёт модель только при изменении входа, кэш
`.claude/pulse/plain-auto.json`); `.claude/pulse/plain.json` — ручное переопределение, главнее. По ОДНОМУ долгоживущему
ssh на машину (счёт, VPS, коллектор; параллельно, с переподключением): ЦП/ОЗУ/диск, процессы проекта, ход задач
`/data/progress/*.json` (пишет `tools/compute/progress.sh`).
Этот ПК — своими силами, Steam Deck не опрашивается.
Будильник (`Waker`): задание с `ticket` в `/data/progress` дошло до done >= total (или файл не обновлялся > 10 мин при
неактивном юните) — один раз `tickets.py comment <TK> --author ceo --next <owner>`; повторы — `.claude/pulse/woken.json`,
журнал — `.claude/pulse/wake.log`.
Связь «процесс → задача → машина» считается здесь (`make_view`): раздел `view` в status.json — готовый к показу вид
(задачи с «где», машины с «для какой задачи», вопросы, что было); `pulse.py` и страница только рисуют `view`.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # plainify.py — переводчик строк (Haiku)
import plainify  # noqa: E402  (переводчик строк, Haiku)
import view2  # noqa: E402  (раздел view2: процессы, шаги, вопросы — контракт VIEW2.md)
PULSE_DIR = ROOT / ".claude" / "pulse"
STATUS = PULSE_DIR / "status.json"
PIDFILE = PULSE_DIR / "collect.pid"
STOP = PULSE_DIR / "stop"
GOAL = ROOT / ".claude" / "pulse-goal.txt"
PLAIN = PULSE_DIR / "plain.json"
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
ROLE_DO = {"engineer": "инженер пишет код", "researcher": "исследователь работает", "judge": "судья проверяет",
           "ceo": "CEO работает"}
UNIT_DO = {"alpha-collector": "пишет стакан Bybit",
           "alpha-board": "табло для владельца", "bridge.py": "табло для владельца",  # веб-табло на счёте (tools/pulse/web)
           "alpha-bus": "шина событий команды",  # TK-045, В-180: постоянные юниты шины (bus.py, watcher.py --host calc)
           "alpha-bus-watcher": "шина: слежка за машиной"}
MACH_ORDER = ("calc", "vps", "collector", "pc", "deck")
# служебные процессы сборки cargo и закачки дерева (vps-check.sh): принадлежат задаче по держателю замка сборки
BUILD_NAMES = {"rustc", "cargo", "flock", "nice", "set", "tar", "rm", "scp", "sftp-server", "cc", "ld", "rustfmt",
               "clippy-driver", "cargo-clippy", "cargo-fmt"}
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


def tk_norm(s, prefix: bool = False):
    """«tk042-run» / «TK-042» → «TK-042» (prefix=True — только в начале строки), иначе None."""
    m = (re.match if prefix else re.search)(r"(?i)tk[-_]?(\d+)", s or "")
    return f"TK-{int(m.group(1)):03d}" if m else None


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


def read_text_shared(path, encoding: str = "utf-8", errors: str = "strict", retries: int = 5) -> str:
    """Прочитать файл целиком, не мешая его атомарной замене (os.replace) другим процессом. Обычный open() на Windows
    не даёт FILE_SHARE_DELETE — пока мы читаем, диспетчер получает PermissionError на `state.json.tmp → state.json`.
    Здесь файл открывается через CreateFileW с FILE_SHARE_READ|WRITE|DELETE, читается сразу целиком и закрывается;
    при ошибке — до `retries` повторов с паузой 20 мс (файла нет — сразу FileNotFoundError)."""
    if os.name != "nt":
        return Path(path).read_text(encoding=encoding, errors=errors)
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                              wintypes.DWORD, ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                           ctypes.c_void_p]
    k.ReadFile.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    invalid = ctypes.c_void_p(-1).value
    last = 0
    for _ in range(retries):
        # GENERIC_READ, SHARE_READ|WRITE|DELETE, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL
        h = k.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
        if h is None or h == invalid:
            last = ctypes.get_last_error()
            if last in (2, 3):
                raise FileNotFoundError(str(path))
            time.sleep(0.02)
            continue
        chunks, ok = [], True
        try:
            buf = ctypes.create_string_buffer(1 << 16)
            n = wintypes.DWORD(0)
            while True:
                if not k.ReadFile(h, buf, len(buf), ctypes.byref(n), None):
                    ok, last = False, ctypes.get_last_error()
                    break
                if n.value == 0:
                    break
                chunks.append(buf.raw[: n.value])
        finally:
            k.CloseHandle(h)
        if ok:
            text = b"".join(chunks).decode(encoding, errors)
            return text.replace("\r\n", "\n").replace("\r", "\n")  # как read_text (универсальные переводы строк)
        time.sleep(0.02)
    raise OSError(f"не удалось прочитать {path} (код {last})")


def read_json(path: Path):
    try:
        return json.loads(read_text_shared(path))
    except Exception:
        return None


_json_cache: dict = {}


def read_json_cached(path: Path, settle_s: float = 0.0):
    """Файл читается только после изменения (stat файл не открывает) и не раньше чем через `settle_s` после него.
    Зачем: на Windows os.replace падает, пока файл открыт кем угодно — даже с FILE_SHARE_DELETE (проверено), поэтому
    диспетчер, заменяя `state.json`, получал PermissionError, когда мы читали его раз в 5 с. Читаем раз на запись,
    через пару секунд после неё, — следующая замена будет не раньше следующего тика диспетчера."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _json_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    if hit and settle_s and time.time() - st.st_mtime < settle_s:
        return hit[1]  # только что записан (возможна вторая запись подряд) — читаем на следующем тике
    val = read_json(path)
    if val is None:
        return hit[1] if hit else None
    _json_cache[path] = (key, val)
    return val


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
            g = groups.setdefault(name, {"name": name, "cpu": 0.0, "etimes": 0, "count": 0, "args": args[:160]})
            g["count"] += 1
            g["etimes"] = max(g["etimes"], etimes)
            if warm:
                g["cpu"] += max(0, times - old[1].get(pid, 0)) / (up - old[0]) * 100
        self.samples.append((up, cur))
        procs = sorted(groups.values(), key=lambda g: (-g["cpu"], -g["etimes"]))
        snap["warm"] = warm
        snap["procs"] = [{"name": g["name"], "cpu": round(g["cpu"]) if warm else None,
                          "minutes": g["etimes"] // 60, "count": g["count"], "args": g["args"]} for g in procs]
        snap["units"] = [{"name": u[0], "state": u[1] if len(u) > 1 else "?"} for u in blk["U"]]
        snap["lock"] = None
        if self.host["lock"]:
            snap["lock"] = {"busy": False}
            if blk["L"]:
                et, _, args = blk["L"].strip().partition(" ")
                m = re.search(r"cargo\s+(test|clippy|build|fmt|check)", args)
                what = f"cargo {m.group(1)}" if m else clip(args.split(self.host["lock"], 1)[-1].strip(), 40)
                lab = re.search(r"ALPHA_TICKET=(\S+)", args)  # метку ставит tools/vps-check.sh: `env ALPHA_TICKET=… nice …`
                snap["lock"] = {"busy": True, "what": what, "minutes": int(et) // 60 if et.isdigit() else 0,
                                "label": lab.group(1) if lab else None, "ticket": tk_norm(lab.group(1)) if lab else None}
        with self.lock:
            self.state = "ok"
            self.block_at = time.time()
            self.snap = snap
            self.progress = blk["P"]

    def get(self):
        with self.lock:
            return self.state, dict(self.snap), dict(self.progress if self.state == "ok" else {})


# --- веб-табло: сводка на счётный сервер, ответы владельца обратно -------------------------------------------------------
BOARD_TARGET = HOSTS[0]["target"]  # сервер счёта: там юнит alpha-board (tools/pulse/web)
BOARD_CMD = "python3 -u /opt/alpha-board/bridge.py {off}"
BOARD_OFFSET = PULSE_DIR / "board-offset.txt"   # сколько байт /data/board/answers.jsonl уже обработано
BOARD_LOG = PULSE_DIR / "board-link.log"
ASK_PY = Path(__file__).resolve().parent / "ask.py"


class BoardLink(threading.Thread):
    """ОДНО долгоживущее ssh на счётный сервер для веб-табло: в stdin — view2 строкой JSON раз в тик (пишет поток-писатель,
    сборщик не ждёт сеть), из stdout — ответы владельца со страницы → `ask.py answer <id> <key>`, смещение — в файл."""

    def __init__(self):
        super().__init__(daemon=True)
        self.cv = threading.Condition()
        self.line = None  # последняя сводка (bytes), ещё не отправленная
        self.proc = None
        self.state = "connecting"
        self.sent_at = 0.0
        self.stopping = False

    def push(self, view2: dict, built_at: str):
        data = json.dumps({"view2": view2, "built_at": built_at}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with self.cv:
            self.line = data + b"\n"
            self.cv.notify()

    def info(self) -> dict:
        return {"link": self.state, "sent_age_s": round(time.time() - self.sent_at) if self.sent_at else None}

    def kill(self):
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def log(self, text: str):
        try:
            with open(BOARD_LOG, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now(TZ).isoformat(timespec='seconds')} {text}\n")
        except OSError:
            pass

    def run(self):
        while not self.stopping:
            try:
                self._session()
            except Exception as e:  # noqa: BLE001
                self.log(f"сессия: {type(e).__name__}: {e}")
            self.state = "down"
            self.kill()
            for _ in range(10):  # пауза перед переподключением
                if self.stopping:
                    return
                time.sleep(0.5)

    def _writer(self, p):
        last = None
        while not self.stopping and p.poll() is None:
            with self.cv:
                if self.line is None or self.line is last:
                    self.cv.wait(timeout=2)
                line = self.line
            if line is None or line is last:
                continue
            try:
                p.stdin.write(line)
                p.stdin.flush()
            except (OSError, ValueError):
                return
            last = line
            self.sent_at = time.time()
            self.state = "ok"

    def _session(self):
        try:
            off = int(read_text_shared(BOARD_OFFSET).strip())
        except (OSError, ValueError):
            off = 0
        self.proc = p = subprocess.Popen(
            [SSH_EXE, *SSH_OPTS, BOARD_TARGET, BOARD_CMD.format(off=off)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=0x00000200 | 0x08000000)  # NEW_PROCESS_GROUP | NO_WINDOW
        threading.Thread(target=self._writer, args=(p,), daemon=True).start()
        for raw in p.stdout:
            self._answer(raw)

    def _answer(self, raw: bytes):
        try:
            r = json.loads(raw.decode("utf-8", "replace"))
            qid, key, off = str(r["id"]), str(r["key"]), int(r["off"])
        except (ValueError, KeyError, TypeError):
            return
        if re.fullmatch(r"q-[\w.-]{1,78}", qid) and re.fullmatch(r"\w{1,20}", key):  # id/key пришли из сети — только безопасный вид
            try:
                res = subprocess.run([sys.executable, str(ASK_PY), "answer", qid, key], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=120, cwd=str(ROOT), creationflags=0x08000000,
                                     env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
                msg = (res.stdout.strip() or res.stderr.strip()).replace("\n", " | ")[:300]
                self.log(f"ответ {qid} {key}: код {res.returncode}: {msg}")
            except (OSError, subprocess.SubprocessError) as e:
                self.log(f"ответ {qid} {key}: не выполнен: {type(e).__name__}: {e}")
                return  # смещение не двигаем — повторим при следующем соединении
        else:
            self.log(f"ответ с недопустимым видом пропущен: {qid[:40]!r}")
        try:
            BOARD_OFFSET.write_text(str(off))
        except OSError:
            pass


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
                hit = (key, T.parse_text(read_text_shared(p), p))
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
            hit = (key, T._parse_log("## Лог\n" + read_text_shared(ap)))
            _arch_cache[ap.name] = hit
        ents += hit[1]
    except OSError:
        pass
    return sorted(ents, key=lambda e: e.ts)


def load_state() -> dict:
    s = read_json_cached(DISP / "state.json", settle_s=2.0)
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
        lines = read_text_shared(WAKE_LOG, errors="replace").splitlines()[-300:]
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


# --- человеческий вид: процесс → задача → машина ----------------------------------------------------------------------
def load_plain() -> dict:
    """`.claude/pulse/plain.json` — РУЧНОЕ переопределение (главнее авто-строк) в нормальной форме; нет файла/битый — пусто."""
    p = read_json_cached(PLAIN)
    p = p if isinstance(p, dict) else {}

    def dic(k):
        v = p.get(k)
        return v if isinstance(v, dict) else {}

    def strs(k):
        v = p.get(k)
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    raw_news = p.get("news") if isinstance(p.get("news"), list) else []
    news = [{"time": str(n.get("time") or "").strip(), "text": str(n["text"]).strip()}
            for n in raw_news if isinstance(n, dict) and n.get("text")]
    return {"headline": str(p.get("headline") or "").strip(), "tasks": dic("tasks"), "next": strs("next"),
            "questions": strs("questions"), "news": news, "legacy": dic("legacy"), "units": dic("units"),
            "machines": dic("machines")}


# --- человеческие строки от Haiku (plainify.py): то же, что в plain.json, но само, по изменению входа -----------------------
AUTO = None  # plainify.Translator; поднимает main(). None (сборщик не запущен здесь) — пустой авто-вид, прежнее поведение
NEWS_CAND = 12             # столько самых свежих записей лога (всех тикетов) переводится для ленты «что было»
NEWS_AGE_S = 48 * 3600
NEXT_MAX = 4
QUESTIONS_MAX = 3


def _role_key(author: str) -> str:
    return (author or "").split()[0].lower() if (author or "").split() else ""


def news_time(ts: datetime) -> str:
    """Пять знаков (колонка ленты): сегодня — ЧЧ:ММ, вчера — «вчера», раньше — ДД.ММ."""
    d = ts.astimezone(TZ)
    days = (datetime.now(TZ).date() - d.date()).days
    return d.strftime("%H:%M") if days <= 0 else "вчера" if days == 1 else d.strftime("%d.%m")


def auto_plain(tickets: dict, live: dict, machines: list, jobs_all: dict, skip_legacy=()) -> dict:
    """Авто-строки (та же форма, что у load_plain): названия задач, шаги и единицы хода, «дальше», вопросы владельцу,
    лента, старые процессы. Всё — из кэша переводчика; чего в кэше нет, то ставится в очередь и появится позже."""
    out = {"tasks": {}, "next": [], "questions": [], "news": [], "legacy": {}, "units": {}}
    if AUTO is None:
        return out
    now = time.time()
    job_tids = {tk_norm(k) or k for k in jobs_all}
    rel = {}  # тикеты, которые табло показывает (то же правило, что у строк в build)
    for tid, t in tickets.items():
        if tid in live or tid in job_tids or t.status in ("todo", "in_progress", "in_review", "needs_owner") or \
                (t.status == "waiting" and now - t_updated(t, now) < WAITING_MAX_AGE_S):
            rel[tid] = t
    order = sorted(rel, key=lambda i: (i not in live, -t_updated(rel[i], now)))

    def entries_of(t):
        return [e for e in all_entries(t) if _role_key(e.author) in ROLE_RU]  # служебные записи диспетчера — мимо

    def entry_out(t, e, prio):
        return AUTO.get("entry", f"{t.id} {e.ts_raw}", {"ticket": short_title(t.header.get("title", ""), 70),
                                                         "author": ROLE_RU[_role_key(e.author)], "text": e.text}, prio)

    # 1. названия задач
    for tid in order:
        t = rel[tid]
        r = AUTO.get("title", tid, {"title": t.header.get("title", ""), "start": clip(clean_text(t.description), 500)}, 0)
        if r and r.get("title"):
            out["tasks"][tid] = {"title": r["title"]}

    # 2. последняя запись каждого показанного тикета → вопрос владельцу, «дальше»
    nxt, ques = [], []
    last = {}
    for tid in order:
        es = entries_of(rel[tid])
        if es:
            last[tid] = (es[-1], entry_out(rel[tid], es[-1], 0))

    # 3. ход задач: шаг и единица по-людски, «дальше» из файла прогресса
    for key, jl in jobs_all.items():
        for j in jl:
            tid = tk_norm(key) or key or f"job:{j['job']}"
            tk = tickets.get(tid)
            r = AUTO.get("step", f"{tid}:{j['job']}", {"ticket": short_title(tk.header.get("title", ""), 60) if tk else "",
                                                       "step": j["step"], "next": j.get("next") or "", "unit": j["unit"]}, 0)
            if not r:
                continue
            if r.get("step") and j["step"] and not plainify.looks_plain(j["step"]):  # понятный шаг («готово») не трогаем
                out["tasks"].setdefault(tid, {}).setdefault("steps", {})[j["step"]] = r["step"]
            if r.get("unit") and j["unit"]:
                out["units"][j["unit"]] = r["unit"]
            if r.get("next"):
                nxt.append(r["next"])

    for tid in order:
        t = rel[tid]
        if tid not in last:
            continue
        e, r = last[tid]
        if r and r.get("next"):
            nxt.append(r["next"])
        if tid not in live and wait_text(t) in ("ждёт: CEO", "ждёт: владельца"):  # вопрос живёт, пока ждут CEO/владельца
            q = r.get("question") if r else None
            if q and _role_key(e.author) != "ceo" and "владел" not in e.text.lower():
                q = None  # вопрос Судье/CEO, который модель приняла за вопрос владельцу: в записи владелец не назван
            if q:
                ques.append(q)
            elif t.status == "needs_owner":
                ques.append("Ждёт вашего решения: " + short_title(t.header.get("title", ""), 60))
    seen = set()
    for lst, dst, lim in ((nxt, out["next"], NEXT_MAX), (ques, out["questions"], QUESTIONS_MAX)):
        for x in lst:
            if x.lower() not in seen and len(dst) < lim:
                seen.add(x.lower())
                dst.append(x)

    # 4. лента: самые свежие записи лога всех тикетов; служебные модель отсеивает (news = null)
    cands = []
    for tid, t in tickets.items():
        try:
            if now - t.path.stat().st_mtime > NEWS_AGE_S:
                continue
        except OSError:
            continue
        for e in entries_of(t):
            if now - e.ts.timestamp() <= NEWS_AGE_S:
                cands.append((e.ts, t, e))
    cands.sort(key=lambda c: c[0], reverse=True)
    items = []
    for ts, t, e in cands[:NEWS_CAND]:
        r = entry_out(t, e, 1)
        if r and r.get("news"):
            items.append((ts, r["news"], t.id))
    out["news"] = [{"time": news_time(ts), "text": tx, "ts": ts.astimezone(TZ).isoformat(timespec="seconds"), "ticket": tid}
                   for ts, tx, tid in items[:5]]  # ts/ticket — для ленты view2; v1 берёт только time/text

    # 5. процессы на машинах без задачи
    for m in machines:
        if m["id"] in ("pc", "deck"):
            continue
        for g in m.get("procs", []):
            name = g["name"]
            if tk_norm(name, prefix=True) or name in UNIT_DO or name in skip_legacy or is_build_proc(name) or g["minutes"] < 5:
                continue
            r = AUTO.get("proc", name, {"name": name, "args": g.get("args", "")}, 2)
            if r and r.get("what"):
                out["legacy"][name] = f"{r['what']} — без задачи, висит {fmt_min(g['minutes'])}"
    return out


def merge_plain(auto: dict, manual: dict) -> dict:
    """Ручное (plain.json) главнее авто: ключ есть — берётся он (списки — целиком, словари — по ключам)."""
    tasks = {}
    for tid in set(auto["tasks"]) | set(manual["tasks"]):
        a, m = auto["tasks"].get(tid) or {}, manual["tasks"].get(tid)
        m = m if isinstance(m, dict) else {}
        d = {}
        if m.get("title") or a.get("title"):
            d["title"] = m.get("title") or a.get("title")
        steps = dict(a.get("steps") or {})
        steps.update(m.get("steps") if isinstance(m.get("steps"), dict) else {})
        if steps:
            d["steps"] = steps
        tasks[tid] = d
    return {"headline": manual["headline"], "tasks": tasks, "next": manual["next"] or auto["next"],
            "questions": manual["questions"] or auto["questions"], "news": manual["news"] or auto["news"],
            "legacy": {**auto["legacy"], **manual["legacy"]}, "units": {**auto["units"], **manual["units"]},
            "machines": manual["machines"]}


def is_build_proc(name: str) -> bool:
    return name in BUILD_NAMES or name.startswith(("rustc", "cargo", "build-script"))


def make_view(plain: dict, tickets: dict, live: dict, machines: list, jobs: dict, disp_ok: bool, watch_ok: bool,
              events: list) -> dict:
    """Готовый к показу вид для `pulse.py` и страницы. Задача «идёт», если у неё есть: сессия роли (этот ПК), ход
    в /data/progress, процесс с префиксом tkNNN, сборка на VPS под её меткой. Остальные процессы — «⚠» (старые из
    plain.legacy или «без задачи»)."""
    mach = {m["id"]: m for m in machines}
    ptasks = plain["tasks"]
    legacy = plain["legacy"]

    def pt(tid):
        v = ptasks.get(tid)
        return v if isinstance(v, dict) else {}

    def title_of(tid):
        t = pt(tid).get("title")
        if t:
            return str(t)
        tk = tickets.get(tid)
        return (short_title(tk.header.get("title", ""), 60) if tk else "") or tid

    def short_of(tid):  # для строки машины: без хвостовой скобки «(минуты и часы)»
        t = title_of(tid)
        return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip() or t

    def step_of(tid, step):
        st = pt(tid).get("steps")
        return str((st or {}).get(step) or step) if isinstance(st, dict) else step

    def known(tid):
        return bool(tid) and tid in tickets

    def active(tid):
        t = tickets.get(tid)
        return tid in live or (t is not None and t.status in ACTIVE)

    entries: list = []  # {tid, mid, kind: job|proc|build|session|unit, what, eta, text, pct, detail, minutes}
    warns: dict = {}

    def warn(mid, text):
        if text not in warns.setdefault(mid, []):
            warns[mid].append(text)

    # 1. ход задач (/data/progress)
    for key, jl in jobs.items():
        for j in jl:
            tid = tk_norm(key) or key or f"job:{j['job']}"
            if j["done"] >= j["total"] and known(tid) and not active(tid):
                continue  # закончено, тикет уже закрыт
            unit = str(plain["units"].get(j["unit"]) or j["unit"]).strip()
            detail = f"{fmt_num(j['done'])} из {fmt_num(j['total'])} {unit}".strip()
            entries.append({"tid": tid, "mid": j["machine"], "kind": "job", "what": "", "eta": j["eta"],
                            "text": step_of(tid, j["step"]) or j["job"], "pct": j["pct"], "detail": detail,
                            "minutes": None})
    job_at = {(e["tid"], e["mid"]) for e in entries}

    # 2. процессы машин и замок сборки
    for m in machines:
        mid = m["id"]
        if mid in ("pc", "deck"):
            continue
        lock = m.get("lock") or {}
        for g in m.get("procs", []):
            name, mins = g["name"], g["minutes"]
            tid = tk_norm(name, prefix=True)
            if tid:
                if active(tid):
                    if (tid, mid) not in job_at:
                        entries.append({"tid": tid, "mid": mid, "kind": "proc", "what": "идёт счёт", "eta": None,
                                        "text": None, "pct": None, "detail": None, "minutes": mins})
                else:
                    warn(mid, f"без задачи: {name}")
            elif name in UNIT_DO:  # постоянная служба — раньше legacy: «без хозяина» ей не бывает
                entries.append({"tid": None, "mid": mid, "kind": "unit", "what": "", "eta": None, "text": UNIT_DO[name],
                                "pct": None, "detail": None, "minutes": mins})
            elif name in legacy:
                warn(mid, str(legacy[name]))
            elif is_build_proc(name):
                if not lock.get("busy") and mins >= 10:
                    warn(mid, f"без задачи: {name}")
            else:
                warn(mid, f"без задачи: {name}")
        if lock.get("busy"):
            tid = lock.get("ticket")
            if known(tid):
                entries.append({"tid": tid, "mid": mid, "kind": "build", "what": "сборка кода", "eta": None,
                                "text": None, "pct": None, "detail": None, "minutes": lock.get("minutes")})
            else:
                lab = f" ({lock['label']})" if lock.get("label") else ""
                warn(mid, f"без задачи: сборка кода{lab}")

    # 3. сессии ролей на этом ПК
    for tid, r in live.items():
        entries.append({"tid": tid, "mid": "pc", "kind": "session", "what": ROLE_DO.get(r["role"], r["role"]),
                        "eta": None, "text": None, "pct": None, "detail": None, "minutes": r["minutes"]})

    def place(e):
        return {"mid": e["mid"], "name": mach[e["mid"]]["name"] if e["mid"] in mach else e["mid"], "what": e["what"],
                "eta": e["eta"]}

    def mkey(p):
        return MACH_ORDER.index(p["mid"]) if p["mid"] in MACH_ORDER else 99

    # --- задачи (СЕЙЧАС ИДЁТ)
    by_tid: dict = {}
    for e in entries:
        if e["tid"]:
            by_tid.setdefault(e["tid"], []).append(e)
    now_rows = []
    for tid, es in by_tid.items():
        jobs_e = [e for e in es if e["kind"] == "job"]
        job_mids = {e["mid"] for e in jobs_e}
        others, seen = [], set()
        for e in es:
            if e["kind"] != "job" and e["mid"] not in job_mids and e["mid"] not in seen:
                seen.add(e["mid"])
                others.append(place(e))
        others.sort(key=mkey)
        mins = [e["minutes"] for e in es if e.get("minutes") is not None]
        since = max(mins) if mins else None
        tag = tid if tk_norm(tid) else ""
        if jobs_e:
            for i, e in enumerate(jobs_e):
                now_rows.append({"tag": tag, "text": e["text"], "detail": e["detail"], "pct": e["pct"], "since_min": since,
                                 "where": [place(e)] + (others if i == 0 else [])})
        else:
            now_rows.append({"tag": tag, "text": title_of(tid), "detail": None, "pct": None, "since_min": since,
                             "where": others})
    now_rows.sort(key=lambda r: (r["pct"] is None, r["tag"], r["text"]))

    # --- машины (для какой задачи)
    problems = []
    mv = []
    for m in machines:
        mid = m["id"]
        items = []
        for e in entries:
            if e["mid"] != mid:
                continue
            if e["kind"] == "job":
                txt = f"{e['text']} ({e['pct']} %)"
            elif e["kind"] == "unit":
                txt = e["text"]
            else:
                txt = f"{short_of(e['tid'])}: {e['what']}"
            if txt not in items:
                items.append(txt)
        note = None
        if mid == "pc":
            if not disp_ok:
                st, stext = "bad", "диспетчер стоит"
                problems.append("диспетчер стоит")
            elif not watch_ok:
                st, stext = "bad", "сторож стоит"
                problems.append("сторож стоит")
            else:
                st, stext = "ok", "в норме"
        elif mid == "deck":
            st, stext = "off", m["lines"][0] if m.get("lines") else "выведен"
        elif m["state"] == "down":
            st, stext = "down", "нет связи"
            problems.append(f"нет связи с машиной «{m['name']}»")
        elif m["state"] == "connecting":
            st, stext = "connecting", "подключаюсь…"
        else:
            units = m.get("units") or []
            if any(u["state"] == "failed" for u in units):
                st, stext = "bad", "сбой"
                problems.append(f"сбой службы на машине «{m['name']}»")
            elif items:
                st, stext = "busy", "занят"
            elif units and any(u["state"] != "active" for u in units):
                st, stext = "stopped", "стоит"
                note = str(plain["machines"].get(mid) or "выключен")
            else:
                st, stext = "idle", "свободна"
        mv.append({"id": mid, "name": m["name"], "state": st, "state_text": stext,
                   "cpu": m.get("cpu") if st in ("busy", "idle") else None,
                   "items": items, "warns": warns.get(mid, []), "note": note})

    # --- заголовок
    n_run = len(now_rows)
    if problems:
        head = {"text": "есть проблема: " + "; ".join(problems), "level": "bad"}
    else:
        head = {"text": plain["headline"] or ("всё идёт" if n_run else "сейчас ничего не идёт"),
                "level": "ok" if n_run else "idle"}

    news = [{"time": n["time"], "text": n["text"]} for n in plain["news"][:5]]
    news_src = "plain"
    if not news:
        news_src = "events"
        news = [{"time": e["time"], "text": e["text"], "who": e["who"]} for e in reversed(events[-5:])]

    return {"headline": head, "attention": len(plain["questions"]), "questions": plain["questions"], "now": now_rows,
            "next": plain["next"], "machines": mv, "news": news, "news_src": news_src}


# --- будильник: конец серверного задания будит исполнителя тикета -----------------------------------------------------------
TICKETS_PY = DISP / "tickets.py"
WOKEN = PULSE_DIR / "woken.json"    # «машина:job:updated» → что сделано; защита от повторов, переживает перезапуск сборщика
WOKE_LOG = PULSE_DIR / "wake.log"
STALL_S = 600                       # файл хода не обновлялся дольше — при неактивном юните это «остановилось, не дойдя»
WOKEN_KEEP_S = 7 * 86400
WAKE_TRIES = 3                      # неудачных вызовов tickets.py на один ключ, потом запись «ошибка» и отказ
WAKE_RETRY_S = 60
WAKE = None                         # Waker; поднимает main(); None (кадр строится не сборщиком) — не будим


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


class Waker:
    """Задание на машине (`/data/progress/<job>.json`, поле ticket) дошло до done >= total — или файл не обновлялся > 10 мин,
    а процессов тикета на машине нет — тогда ОДИН раз `tickets.py comment <TK> --author ceo --text … --next <owner>`: диспетчер
    серверных заданий не видит, а тикет в `waiting` без `next` не просыпается. Не будим: тикет не активен (done/stopped/
    backlog/blocked) или его нет; роль-владелец сейчас работает (отложено до конца сессии); в логе тикета уже есть запись
    роли позже хода задания (кто-то отреагировал). Повторов нет: ключ «машина:job:updated» в `woken.json`."""

    def __init__(self, tickets_py=None, woken=None, log=None):
        self.tickets_py = Path(tickets_py or TICKETS_PY)
        self.woken_path = Path(woken or WOKEN)
        self.log_path = Path(log or WOKE_LOG)
        self.lock = threading.Lock()
        self.busy: set = set()   # ключи, по которым tickets.py вызван и ещё не вернулся
        self.tries: dict = {}    # ключ → [неудач, не раньше чем (время)]
        self.noted: set = set()  # «отложено» пишем в лог один раз на ключ
        raw = read_json(self.woken_path)
        now = time.time()
        self.woken = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items()
                      if isinstance(v, dict) and now - float(v.get("ts") or 0) < WOKEN_KEEP_S}

    def log(self, text: str):
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now(TZ).isoformat(timespec='seconds')} {text}\n")
        except OSError:
            pass

    def _record(self, key: str, tid: str, kind: str, result: str):
        """Запомнить итог по ключу (под self.lock) и записать woken.json."""
        import ticket as T
        self.woken[key] = {"ts": time.time(), "at": datetime.now(TZ).isoformat(timespec="seconds"), "ticket": tid,
                           "kind": kind, "result": result}
        T.atomic_write_text(self.woken_path, json.dumps(self.woken, ensure_ascii=False, indent=1))
        self.log(f"{key} {tid} {kind}: {result}")

    @staticmethod
    def alive(procs: list, tid: str, job: str) -> bool:
        """На машине есть процесс тикета: имя группы (юнит) с префиксом tkNNN, либо tkNNN / имя задания в аргументах."""
        slug = tid.lower().replace("-", "")
        for g in procs:
            name = str(g.get("name") or "")
            text = (name + " " + str(g.get("args") or "")).lower()
            if tk_norm(name, prefix=True) == tid or slug in text or (len(job) >= 4 and job.lower() in text):
                return True
        return False

    def observe(self, jobs: list, tickets: dict, live: dict, now: float):
        """jobs — по заданию на каждую запись хода: {mid, mname, job, p (файл хода как есть), procs (процессы машины)}."""
        for j in jobs:
            p, job = j["p"], j["job"]
            try:
                done, total = float(p["done"]), float(p["total"])
            except (KeyError, TypeError, ValueError):
                continue
            tid = tk_norm(str(p.get("ticket") or ""))
            if not tid or total <= 0:
                continue
            upd_raw = str(p.get("updated") or "")
            try:
                upd = datetime.fromisoformat(upd_raw).timestamp()
            except ValueError:
                upd = None
            if done >= total:
                kind = "done"
            elif upd is not None and now - upd > STALL_S and not self.alive(j["procs"], tid, job):
                kind = "stalled"
            else:
                continue
            key = f"{j['mid']}:{job}:{upd_raw}"
            with self.lock:
                if key in self.woken or key in self.busy:
                    continue
                t = tickets.get(tid)
                if t is None:
                    self._record(key, tid, kind, "не будим: тикета нет")
                    continue
                if t.status not in ACTIVE:
                    self._record(key, tid, kind, f"не будим: тикет {t.status or '?'}")
                    continue
                role = t.owner
                if role not in ROLE_RU:
                    self._record(key, tid, kind, f"не будим: owner {role!r} не роль")
                    continue
                if tid in live:
                    if key not in self.noted:
                        self.noted.add(key)
                        self.log(f"{key} {tid} {kind}: отложено — сессия {live[tid].get('role')} работает")
                    continue
                if upd is not None and any(
                        _role_key(e.author) in ROLE_RU and e.ts.timestamp() > upd and not e.text.startswith("Сервер (")
                        for e in t.log):
                    self._record(key, tid, kind, "не будим: в логе уже есть запись после хода задания")
                    continue
                fail = self.tries.get(key)
                if fail and now < fail[1]:
                    continue
                self.busy.add(key)
            hhmm = datetime.fromtimestamp(upd or now, TZ).strftime("%H:%M")
            step = str(p.get("step") or job)
            at = f"{_num(done)}/{_num(total)}"
            if kind == "done":
                text = f"Сервер ({j['mname']}): задание «{step}» закончено ({at}) в {hhmm} GMT+4 — продолжай по тикету."
            else:
                text = f"Сервер ({j['mname']}): задание «{step}» остановилось на {at}, юнит не активен — разберись."
            threading.Thread(target=self._wake, args=(key, tid, kind, role, text), daemon=True).start()

    def _wake(self, key: str, tid: str, kind: str, role: str, text: str):
        ok, msg = False, ""
        try:
            res = subprocess.run(
                [sys.executable, str(self.tickets_py), "comment", tid, "--author", "ceo", "--text", text, "--next", role],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, cwd=str(ROOT),
                creationflags=0x08000000, env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
            ok = res.returncode == 0
            msg = (res.stdout.strip() or res.stderr.strip()).replace("\n", " | ")[:200]
        except (OSError, subprocess.SubprocessError) as e:
            msg = f"{type(e).__name__}: {e}"
        with self.lock:
            self.busy.discard(key)
            try:
                if ok:
                    self._record(key, tid, kind, f"разбужен: {role}")
                else:
                    n = (self.tries.get(key) or [0])[0] + 1
                    self.tries[key] = [n, time.time() + WAKE_RETRY_S]
                    self.log(f"{key} {tid} {kind}: tickets.py не вышло ({n}/{WAKE_TRIES}): {msg}")
                    if n >= WAKE_TRIES:
                        self._record(key, tid, kind, f"ошибка: {msg}")
            except Exception as e:  # noqa: BLE001  (запись итога не должна ронять поток)
                self.log(f"{key} {tid} {kind}: итог не записан: {type(e).__name__}: {e}")


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
    machines, jobs_by_ticket, wake_jobs = [], {}, []
    for f in feeds:
        f.watchdog()
        st, snap, prog = f.get()
        machines.append(machine_view(f.host, st, snap))
        for job, p in prog.items():
            wake_jobs.append({"mid": f.host["id"], "mname": f.host["name"], "job": job, "p": p,
                              "procs": snap.get("procs") or []})
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
                 "eta_min": None if eta is None else round(eta, 1), "text": txt, "next": p.get("next") or None,
                 "step_n": p.get("step_n")})  # номер шага плана (alpha-progress … step_n); нет — шаг «идёт»

    jobs_all = {k: list(v) for k, v in jobs_by_ticket.items()}  # для вида: ниже jobs_by_ticket разбирается

    if WAKE is not None:  # конец серверного задания будит исполнителя; сбой будильника кадр не роняет
        try:
            WAKE.observe(wake_jobs, tickets, live, now)
        except Exception as e:  # noqa: BLE001
            WAKE.log(f"observe: {type(e).__name__}: {e}")

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
    hb = read_json_cached(DISP / "watch-heartbeat.json", settle_s=2.0) or {}
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
        goal = next((ln.strip() for ln in read_text_shared(GOAL).splitlines() if ln.strip()), "")
    except OSError:
        goal = ""
    events = read_events(tickets)
    manual = load_plain()
    try:
        auto = auto_plain(tickets, live, machines, jobs_all, manual["legacy"])
    except Exception:  # переводчик не должен ронять кадр: прежнее поведение (заголовок тикета, первая фраза записи)
        auto = {"tasks": {}, "next": [], "questions": [], "news": [], "legacy": {}, "units": {}}
    plain = merge_plain(auto, manual)
    view = make_view(plain, tickets, live, machines, jobs_all, disp_ok, watch_ok, events)
    try:  # view2 не должен ронять кадр: v1 (view) живёт отдельно
        view2, view2_error = view2_build(plain, tickets, live, machines, jobs_all, events, view, disp_ok, watch_ok, now), None
    except Exception as e:  # noqa: BLE001
        view2, view2_error = None, f"{type(e).__name__}: {e}"
    return {"v": 2, "built_at": datetime.now(TZ).isoformat(timespec="seconds"), "built_ts": now, "pid": os.getpid(),
            "goal": goal, "dispatcher": {"ok": disp_ok, "tick_age_s": tick_age, "watch_ok": watch_ok},
            "plain": plain, "view": view, "view2": view2, "view2_error": view2_error,
            "tickets": rows, "roles_free": roles_free, "machines": machines, "events": events, "error": None}


def view2_build(plain, tickets, live, machines, jobs_all, events, view, disp_ok, watch_ok, now) -> dict:
    return view2.make(sys.modules[__name__], plain=plain, tickets=tickets, live=live, machines=machines, jobs_all=jobs_all,
                      events=events, view=view, disp_ok=disp_ok, watch_ok=watch_ok, now=now)


def t_updated(t, default: float) -> float:
    try:
        return datetime.fromisoformat(t.header.get("updated", "")).timestamp()
    except ValueError:
        return default


def read_pid(path: Path):
    try:
        return int(read_text_shared(path).strip())
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
    global AUTO, WAKE
    try:
        AUTO = plainify.Translator().start()
    except Exception:
        AUTO = None  # без переводчика — прежнее поведение
    WAKE = Waker()
    feeds = [Feed(h) for h in HOSTS]
    for f in feeds:
        f.start()
    board = BoardLink()
    board.start()
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
            if isinstance(st.get("view2"), dict):
                board.push(st["view2"], st["built_at"])
            st["board"] = board.info()
            T.atomic_write_text(STATUS, json.dumps(st, ensure_ascii=False, indent=1))
            PIDFILE.touch()
            if STOP.exists():
                break
            time.sleep(max(0.2, TICK_S - (time.time() - t0)))
    finally:
        for f in feeds:
            f.stopping = True
            f.kill()
        board.stopping = True
        board.kill()
        STOP.unlink(missing_ok=True)
        PIDFILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
