#!/usr/bin/env python3
"""Цикл работы команды: куда уходит настенное время между «гипотеза придумана» и «число получено».

Только чтение (В-187, 05.10): запуски ролей диспетчера, логи тикетов, журнал юнитов сервера счёта и VPS по ssh.
Только стандартная библиотека, Python 3.11.

    python tools/team/cycle-report.py --since "2026-10-05T00:10" [--until "2026-10-05T03:30"] \
        [--tickets TK-048,TK-049,TK-050,TK-051,TK-052] [--out docs/findings/team-cycle-2026-10-05.md] [--no-remote]

Время везде GMT+4 (время диспетчера); сервер счёта в CEST (+02:00) и VPS в UTC приводятся к нему.
Источник, который не прочитался, в отчёте помечен «нет данных», остальное считается дальше.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=4))
ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / ".claude" / "dispatcher" / "runs"
RUNS_LOG = ROOT / ".claude" / "dispatcher" / "runs.log"
DISP_ERR = ROOT / ".claude" / "dispatcher" / "dispatch.err.log"
TICKETS = ROOT / ".claude" / "tickets"
CALC = "root@89.163.242.211"
VPS = "root@13.140.29.171"

MIN = 60.0
EMPTY_RUN_S = 60          # запуск короче — «пустой»
ENTRY_SLACK_S = 20        # запись роли в лог считается «этого запуска», если ушла не позже конца + столько
VPS_LONG_S = 45           # сеанс ssh на VPS длиннее — сборка/проверка (cargo), короче — служебный опрос
CAT_NAMES = {
    "eng": "работа инженера (в запуске)",
    "ovh": "накладные запуска (старт CLI + опрос диспетчера)",
    "w_run": "сам замер (юнит считает)",
    "w_queue": "очередь сервера (юнит ждёт замок)",
    "w_lag": "лаг диспетчера после готовности замера",
    "w_unk": "ждал юнит без метрик (очередь и счёт не разделены)",
    "w_other": "ждал не юнит (сборка/файл/тикет) — причина не видна",
    "ceo": "ждал CEO",
    "blk": "блокировка диспетчера (до снятия CEO)",
    "lag": "простой диспетчера (лаг запуска, возобновление, повтор)",
}
WAIT_CATS = ("w_run", "w_queue", "w_unk", "w_lag", "w_other")


# --- интервалы -------------------------------------------------------------------------------------------------
def clip(iv, lo, hi):
    a, b = max(iv[0], lo), min(iv[1], hi)
    return (a, b) if b > a else None


def union(ivs):
    out = []
    for a, b in sorted(i for i in ivs if i and i[1] > i[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract(ivs, cut):
    cut = union(cut)
    out = []
    for a, b in union(ivs):
        cur = a
        for c, d in cut:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append((cur, c))
            cur = max(cur, d)
        if cur < b:
            out.append((cur, b))
    return out


def total(ivs):
    return sum((b - a).total_seconds() for a, b in union(ivs))


def mins(sec):
    return f"{sec / MIN:.1f}"


def hhmm(t):
    return t.astimezone(TZ).strftime("%H:%M")


def parse_when(text):
    dt = datetime.fromisoformat(text.strip())
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


# --- ssh -------------------------------------------------------------------------------------------------------
def ssh(host, remote_cmd, timeout=300):
    """Только чтение. Ключ и known_hosts — явно (кириллический HOME ломает умолчания ssh)."""
    home = Path.home() / ".ssh"
    key = os.environ.get("ALPHA_SSH_KEY") or (home / "id_rsa").as_posix()
    kh = os.environ.get("ALPHA_SSH_KNOWN_HOSTS") or (home / "known_hosts").as_posix()
    cmd = ["ssh", "-i", key, "-o", f"UserKnownHostsFile={kh}", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host,
           remote_cmd]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"ssh {host}: не ответил за {timeout} с"
    except OSError as e:
        return None, f"ssh {host}: {e}"
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode not in (0, 1):  # grep без совпадений даёт 1
        return None, f"ssh {host}: rc={p.returncode} {p.stderr.decode('utf-8', 'replace')[:200]}"
    return out, None


# --- запуски ролей ---------------------------------------------------------------------------------------------
@dataclass
class Run:
    tid: str
    role: str
    start: datetime
    dur: float | None          # duration_ms / 1000; None — JSON пуст (идёт или оборван)
    end: datetime | None = None  # время строки runs.log (диспетчер заметил конец)
    reason: str = "?"
    status: str = "?"
    is_error: bool = False
    session: str = "-"
    running: bool = False
    has_entry: bool = False

    @property
    def active_end(self):
        return self.start + timedelta(seconds=self.dur) if self.dur is not None else (self.end or self.start)


RUN_NAME_RE = re.compile(r"^(\d{8})-(\d{6})-(TK-\d+)-(\w+)\.json$")


def load_runs(tids, until):
    runs = defaultdict(list)
    for f in sorted(RUNS_DIR.glob("*.json")):
        m = RUN_NAME_RE.match(f.name)
        if not m or m.group(3) not in tids:
            continue
        start = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=TZ)
        dur, err = None, False
        try:
            txt = f.read_text(encoding="utf-8").strip()
            if txt:
                d = json.loads(txt)
                dur = float(d.get("duration_ms", 0)) / 1000.0
                err = bool(d.get("is_error"))
        except (OSError, ValueError):
            pass
        runs[m.group(3)].append(Run(m.group(3), m.group(4), start, dur, is_error=err))
    logs = defaultdict(list)
    dup_lines = 0
    try:
        for line in RUNS_LOG.read_text(encoding="utf-8").splitlines():
            p = line.split()
            if len(p) < 4:
                continue
            kv = dict(x.split("=", 1) for x in p[3:] if "=" in x)
            logs[(p[1], p[2])].append({"t": parse_when(p[0]), "reason": kv.get("reason", "?"),
                                       "status": kv.get("status", "?"), "session": kv.get("session", "-"),
                                       "cost": kv.get("cost_usd", ""), "used": False})
    except OSError:
        pass
    for tid, rs in runs.items():
        rs.sort(key=lambda r: r.start)
        for role in {r.role for r in rs}:
            rr = [r for r in rs if r.role == role]
            ll = sorted(logs[(tid, role)], key=lambda x: x["t"])
            for i, r in enumerate(rr):
                nxt = rr[i + 1].start + timedelta(seconds=5) if i + 1 < len(rr) else None
                lo = max(r.start, r.start + timedelta(seconds=(r.dur or 0) - 5))
                for l in ll:
                    if l["used"] or l["t"] < lo:
                        continue
                    if nxt and l["t"] > nxt:
                        break
                    l["used"] = True
                    r.end, r.reason, r.status, r.session = l["t"], l["reason"], l["status"], l["session"]
                    break
            # строки-дубли: та же сессия и стоимость, что у уже сопоставленной, не позже 2 мин
            for l in ll:
                if not l["used"] and any(o["used"] and o["session"] == l["session"] and o["cost"] == l["cost"]
                                         and abs((o["t"] - l["t"]).total_seconds()) < 120 for o in ll):
                    dup_lines += 1
    for rs in runs.values():
        for r in rs:
            if r.end is None:
                if r.dur is not None:
                    r.end = r.active_end
                else:
                    r.running = (until - r.start) < timedelta(minutes=25)
                    r.end = until if r.running else r.start
            r.end = max(r.end, r.active_end)
    return runs, dup_lines


# --- тикеты ----------------------------------------------------------------------------------------------------
ENTRY_RE = re.compile(r"^###\s+(\S+)\s+(\S+)\s*$", re.M)


def load_ticket(tid):
    entries, header = [], {}
    for path in (TICKETS / "archive" / f"{tid}-log.md", TICKETS / f"{tid}.md"):
        try:
            txt = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if path.parent.name != "archive":
            m = re.match(r"^---\r?\n(.*?)\r?\n---", txt, re.S)
            if m:
                for ln in m.group(1).splitlines():
                    if ":" in ln:
                        k, v = ln.split(":", 1)
                        header[k.strip()] = v.strip()
        ms = list(ENTRY_RE.finditer(txt))
        for i, m in enumerate(ms):
            try:
                t = parse_when(m.group(1))
            except ValueError:
                continue
            body = txt[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(txt)]
            entries.append((t, m.group(2).lower(), body))
    entries.sort(key=lambda e: e[0])
    return entries, header


# --- сервер счёта: юниты и метрики ----------------------------------------------------------------------------
@dataclass
class Unit:
    name: str
    tid: str
    cmd: str
    s: datetime
    e: datetime | None = None
    state: str = "ok"          # ok | failed | stopped | running
    flock: bool = False
    metrics: list = field(default_factory=list)
    compute: list = field(default_factory=list)   # интервалы счёта по metrics
    queue: list = field(default_factory=list)
    tail: list = field(default_factory=list)      # после последнего замера до конца юнита (diff, сверка)
    service: list = field(default_factory=list)   # юнит без замка и без метрик — служебный
    unknown: list = field(default_factory=list)   # юнит с замком, закончился, метрик нет — очередь и счёт не разделены
    split: str = "metrics"      # metrics | no-metrics-queue | unknown | service


JOURNAL_START = re.compile(r"Started (tk[\w.@-]+?)\.(service|timer) - (.*)")
JOURNAL_END = re.compile(r"(tk[\w.@-]+?)\.service: (Deactivated successfully|Failed with result.*)")
JOURNAL_STOPPED = re.compile(r"Stopped (tk[\w.@-]+?)\.service")


def load_units(text, until):
    ev = []
    for line in text.splitlines():
        m = re.match(r"^(\S+) \S+ systemd\[1\]: (.*)$", line)
        if not m:
            continue
        try:
            t = datetime.fromisoformat(m.group(1)).astimezone(TZ)
        except ValueError:
            continue
        msg = m.group(2)
        if (x := JOURNAL_START.match(msg)) and x.group(2) == "service":
            ev.append((t, "S", x.group(1), x.group(3)))
        elif (x := JOURNAL_END.match(msg)):
            ev.append((t, "F" if x.group(2).startswith("Failed") else "E", x.group(1), ""))
        elif (x := JOURNAL_STOPPED.match(msg)):
            ev.append((t, "X", x.group(1), ""))
    ev.sort(key=lambda e: e[0])
    open_, units = {}, []
    for t, k, name, cmd in ev:
        if k == "S":
            if name in open_:  # перезапуск без отдельной записи конца
                open_[name].e = t
            tm = re.match(r"tk(\d{3})", name)
            u = Unit(name, f"TK-{tm.group(1)}" if tm else "?", cmd, t)
            u.flock = "flock" in cmd
            open_[name] = u
            units.append(u)
        elif k in ("E", "F") and name in open_:
            u = open_.pop(name)
            u.e, u.state = t, ("failed" if k == "F" else "ok")
        elif k == "X":
            for u in reversed(units):
                if u.name == name and u.e and abs((u.e - t).total_seconds()) <= 2:
                    u.state = "stopped" if u.state == "ok" else u.state
                    break
    for u in open_.values():
        u.e, u.state = until, "running"
    return units


MET_WALL_RE = re.compile(r"(?:^|\s)(?:\w+=\d+\s+)?(\w*wall_s)\s+([0-9]*\.?[0-9]+)")


@dataclass
class Metric:
    path: str
    t: datetime
    wall: float | None
    dup: bool = False
    owner: str | None = None
    anchor: datetime | None = None   # конец интервала счёта, если mtime файла не совпал с концом юнита


def parse_metrics(text):
    mt, walls = {}, defaultdict(list)
    for line in text.splitlines():
        if line.startswith("M "):
            _, ts, path = line.split(" ", 2)
            mt[path.strip()] = datetime.fromtimestamp(float(ts), TZ)
        elif ":" in line:
            path, rest = line.split(":", 1)
            for m in MET_WALL_RE.finditer(rest):
                walls[path.strip()].append(float(m.group(2)))
    ms = [Metric(p, t, sum(walls[p]) if walls.get(p) else None) for p, t in mt.items()]
    ms.sort(key=lambda m: m.t)
    # копии (cp metrics.txt в другой каталог): тот же wall и время с точностью до 3 с — одна запись
    seen = {}
    for m in ms:
        if m.wall is None:
            continue
        key = (round(m.wall, 2), int(m.t.timestamp()) // 3)
        key2 = (round(m.wall, 2), int(m.t.timestamp()) // 3 - 1)
        if key in seen or key2 in seen:
            m.dup = True
        else:
            seen[key] = m
    return ms


def unit_tokens(u):
    toks = set()
    toks.add(u.name.split("-", 1)[1] if "-" in u.name else u.name)
    for w in u.cmd.split():
        w = w.rstrip(".")   # journalctl дописывает точку в конец командной строки
        if re.fullmatch(r"alpha-[\w-]+|[a-z]+\d+[a-z0-9]*", w):
            toks.add(w)
    toks.discard("gate")
    return {t.rstrip(".") for t in toks if len(t.rstrip(".")) >= 2}


def assign_metrics(units, metrics):
    for m in metrics:
        if m.dup:
            continue
        best = None
        for u in units:
            if not (u.s - timedelta(seconds=1) <= m.t <= u.e + timedelta(seconds=3)):
                continue
            if m.wall is not None and m.t - timedelta(seconds=m.wall) < u.s - timedelta(seconds=5):
                continue
            tok = any(t in m.path for t in unit_tokens(u))
            end_diff = abs((u.e - m.t).total_seconds())
            same_dir = m.path.split("/")[0] == u.tid.replace("-", "").lower()   # каталог тикета юнита
            if not (tok or same_dir or end_diff <= 3):
                continue   # чужой каталог (стенд другого тикета) и ни имени, ни конца юнита — не его метрика
            score = (0 if (tok or end_diff <= 3) else 1, end_diff)
            if best is None or score < best[0]:
                best = (score, u)
        if best:
            m.owner = best[1].name + "@" + best[1].s.isoformat()
            best[1].metrics.append(m)
    # метрика, оставшаяся без владельца по времени, но с именем бинарника из командной строки юнита без метрик
    for m in metrics:
        if m.dup or m.owner:
            continue
        for u in units:
            if not u.metrics and u.e and any(t in m.path for t in unit_tokens(u) if t.startswith("alpha-")) \
                    and m.t >= u.s:
                m.owner = u.name + "@" + u.s.isoformat() + "~"
                m.anchor = u.e
                u.metrics.append(m)
                break


def split_unit(u):
    ms = [m for m in u.metrics if m.wall]
    if ms:
        ivs = [((m.anchor or m.t) - timedelta(seconds=m.wall), m.anchor or m.t) for m in ms]
        ivs = [clip(i, u.s, u.e) for i in ivs]
        u.compute = union(ivs)
        if u.compute:
            u.queue = [(u.s, u.compute[0][0])] if u.compute[0][0] > u.s else []
            u.queue += [(u.compute[i][1], u.compute[i + 1][0]) for i in range(len(u.compute) - 1)]
            u.tail = [(u.compute[-1][1], u.e)] if u.e > u.compute[-1][1] else []
            return
    if u.flock and u.state in ("stopped", "failed") and not u.metrics:
        u.queue, u.split = [(u.s, u.e)], "no-metrics-queue"   # остановлен/упал, замера нет — стоял в очереди
    elif u.flock:
        u.unknown, u.split = [(u.s, u.e)], "unknown"          # закончился/идёт, метрик нет — не делим
    else:
        u.service, u.split = [(u.s, u.e)], "service"


# --- VPS -------------------------------------------------------------------------------------------------------
def load_vps_sessions(text):
    st, ses = {}, []
    for line in text.splitlines():
        m = re.match(r"^(\S+) \S+ systemd-logind\[\d+\]: (New session (\d+) of user|Removed session (\d+)\.)", line)
        if not m:
            continue
        t = datetime.fromisoformat(m.group(1)).astimezone(TZ)
        if m.group(3):
            st[m.group(3)] = t
        elif m.group(4) in st:
            ses.append((st.pop(m.group(4)), t, m.group(4)))
    builds = []
    for line in text.splitlines():
        m = re.match(r"^(\S+) \S+ systemd\[1\]: (Started|.*Deactivated)\s*(tk[\w-]*-build)\.service", line) or \
            re.match(r"^(\S+) \S+ systemd\[1\]: (tk[\w-]*-build)\.service: (Deactivated)", line)
        if m:
            builds.append((datetime.fromisoformat(m.group(1)).astimezone(TZ), line))
    return sorted(ses), builds


# --- временная линия тикета ------------------------------------------------------------------------------------
def classify_gap(g0, g1, reason, entries, units):
    """Паузу [g0, g1] между запусками — на причины. reason — причина СЛЕДУЮЩЕГО запуска (runs.log)."""
    if g1 <= g0:
        return []
    out = []
    ceo = [t for t, a, _ in entries if a == "ceo" and g0 - timedelta(seconds=5) <= t <= g1]
    blk = [t for t, a, body in entries if a == "dispatcher" and "заблок" in body.lower()
           and g0 - timedelta(seconds=120) <= t <= g1]
    if blk:
        b = max(g0, min(blk))
        c = max([t for t in ceo if t >= b] or [g1])   # запись CEO, после которой пошёл запуск — последняя в паузе
        out += [(g0, b, "lag"), (b, c, "blk"), (c, g1, "lag")]
    elif reason == "wait_for-met":
        comp = union([clip(i, g0, g1) for u in units for i in u.compute + u.tail])
        unk = subtract([clip(i, g0, g1) for u in units for i in u.unknown + u.service], comp)
        que = subtract([clip(i, g0, g1) for u in units for i in u.queue], comp + unk)
        covered = union(comp + unk + que)
        ends = [u.e for u in units if u.e and g0 <= u.e <= g1 + timedelta(seconds=90)]
        lag = []
        if ends:
            lag = subtract([(max(g0, max(ends)), g1)], covered)
        rest = subtract([(g0, g1)], covered + lag)
        out += [(a, b, "w_run") for a, b in comp] + [(a, b, "w_queue") for a, b in que] + \
               [(a, b, "w_unk") for a, b in unk] + \
               [(a, b, "w_lag") for a, b in lag] + [(a, b, "w_other") for a, b in rest]
    elif reason == "next" and ceo:
        c = max(ceo)   # будит запуск последняя запись CEO в паузе; прежние — промежуточные комментарии
        out += [(g0, max(g0, c), "ceo"), (max(g0, c), g1, "lag")]
    else:
        out += [(g0, g1, "lag")]
    return [(a, b, c) for a, b, c in out if b > a]


def build_timeline(tid, life0, life1, runs, entries, units, header):
    segs, gaps = [], []
    prev = life0
    for r in sorted(runs, key=lambda r: r.start):
        if r.end <= life0 or r.start >= life1:
            continue
        s = max(r.start, prev)
        if s > prev:
            reason = r.reason if r.reason != "?" else "todo"
            g = classify_gap(prev, min(s, life1), reason, entries, units)
            segs += g
            gaps.append((prev, min(s, life1), reason, g))
        a = clip((s, r.active_end), life0, life1)
        o = clip((max(s, r.active_end), r.end), life0, life1)
        if r.running:
            a = clip((s, life1), life0, life1)
        if a:
            segs.append((a[0], a[1], "eng"))
        if o:
            segs.append((o[0], o[1], "ovh"))
        prev = max(prev, r.end)
    if prev < life1:
        st = header.get("status", "")
        # хвост окна: статус тикета сейчас (по времени статус не хранится — берём на момент запуска отчёта)
        reason = {"waiting": "wait_for-met", "needs_owner": "next"}.get(st, "todo")
        g = classify_gap(prev, life1, reason, entries, units)
        if st == "needs_owner" or header.get("next") == "ceo":
            g = [(a, b, "ceo" if c == "lag" else c) for a, b, c in g]
        segs += g
        gaps.append((prev, life1, f"хвост окна, статус {st or '?'}", g))
    return segs, gaps


# --- главное ---------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True, help="начало окна, GMT+4, напр. 2026-10-05T00:10")
    ap.add_argument("--until", default=None, help="конец окна (по умолчанию — сейчас)")
    ap.add_argument("--tickets", default="TK-048,TK-049,TK-050,TK-051,TK-052")
    ap.add_argument("--out", default=None, help="куда писать (по умолчанию docs/findings/team-cycle-<дата>.md)")
    ap.add_argument("--no-remote", action="store_true", help="не ходить по ssh (только локальные источники)")
    ap.add_argument("--lookback-h", type=float, default=8.0, help="сколько часов до окна читать журналы юнитов")
    ap.add_argument("--ssh-timeout", type=int, default=300)
    ap.add_argument("--all-gaps", action="store_true", help="добавить приложение: все паузы всех тикетов")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    since = parse_when(args.since)
    until = parse_when(args.until) if args.until else datetime.now(TZ)
    wsec = (until - since).total_seconds()
    tids = [t.strip() for t in args.tickets.split(",") if t.strip()]
    out_path = Path(args.out) if args.out else ROOT / "docs" / "findings" / f"team-cycle-{until:%Y-%m-%d}.md"
    gaps_missing = []   # чего не удалось прочитать

    if args.no_remote:
        gaps_missing.append("сервер счёта и VPS не читались (флаг --no-remote)")

    # 1. запуски и тикеты
    runs, dup_lines = load_runs(set(tids), until)
    tick = {t: load_ticket(t) for t in tids}
    for t in tids:
        for r in runs.get(t, []):
            r.has_entry = any(a == r.role and r.start - timedelta(seconds=2) <= tt <= r.end + timedelta(seconds=ENTRY_SLACK_S)
                              for tt, a, _ in tick[t][0])

    # 2. сервер счёта
    units, metrics, calc_ok, clock_skew = [], [], False, None
    if not args.no_remote:
        pat = "tk0(" + "|".join(t[-2:] for t in tids if re.fullmatch(r"TK-0\d\d", t)) + ")"
        look = since - timedelta(hours=args.lookback_h)
        jtxt, err = ssh(CALC, f"journalctl --since '@{int(look.timestamp())}' --until '@{int(until.timestamp()) + 60}' "
                              f"-o short-iso --no-pager | grep -E '{pat}'", args.ssh_timeout)
        if jtxt is None:
            gaps_missing.append(f"журнал юнитов сервера счёта: {err}")
        else:
            units = [u for u in load_units(jtxt, until) if u.e > since]
            dirs = " ".join(sorted({t.lower().replace("-", "") for t in tids}))
            # metrics*.txt: у ряда замеров второй файл (metrics66.txt); только чтение (find + grep)
            t_loc0 = datetime.now(TZ)
            mtxt, err = ssh(CALC, f"echo \"T $(date +%s.%N)\"; cd /data && find {dirs} -maxdepth 3 -name 'metrics*.txt' -newermt '@{int(look.timestamp())}' "
                                  f"-printf 'M %T@ %p\\n' 2>/dev/null; find {dirs} -maxdepth 3 -name 'metrics*.txt' "
                                  f"-newermt '@{int(look.timestamp())}' -exec grep -H -E 'wall_s' {{}} + 2>/dev/null",
                            args.ssh_timeout)
            if mtxt is None:
                gaps_missing.append(f"metrics.txt сервера счёта: {err} — очередь и счёт юнитов не разделены")
            else:
                metrics = parse_metrics(mtxt)
                calc_ok = True
                mm = re.search(r"^T ([0-9.]+)", mtxt, re.M)
                if mm:   # расхождение часов: серверное время минус середина вызова на этой машине
                    mid = (t_loc0 + (datetime.now(TZ) - t_loc0) / 2).timestamp()
                    clock_skew = float(mm.group(1)) - mid
    if calc_ok:
        assign_metrics(units, metrics)
    for u in units:
        split_unit(u)

    # 3. VPS
    vps_ses, vps_builds, vps_ok = [], [], False
    if not args.no_remote:
        look = since - timedelta(minutes=30)
        vtxt, err = ssh(VPS, f"journalctl --since '@{int(look.timestamp())}' --until '@{int(until.timestamp()) + 60}' "
                             f"-o short-iso --no-pager | grep -E 'systemd-logind|-build.service'", args.ssh_timeout)
        if vtxt is None:
            gaps_missing.append(f"журнал VPS: {err}")
        else:
            vps_ses, vps_builds = load_vps_sessions(vtxt)
            vps_ok = True

    # 4. временные линии
    life, seg_by, gap_by = {}, {}, {}
    for t in tids:
        entries, header = tick[t]
        rs = [r for r in runs.get(t, []) if r.end > since and r.start < until]
        firsts = [e[0] for e in entries[:1]] + [r.start for r in runs.get(t, [])[:1]]
        birth = min(firsts) if firsts else since
        l0 = max(since, birth)
        l1 = until
        if header.get("status") == "done":
            try:
                l1 = min(until, parse_when(header["updated"]))
            except (KeyError, ValueError):
                pass
        life[t] = (l0, l1)
        us = [u for u in units if u.tid == t]
        seg_by[t], gap_by[t] = build_timeline(t, l0, l1, rs, entries, us, header)

    # --- расчёты ---
    def cat_sec(segs):
        d = defaultdict(float)
        for a, b, c in segs:
            d[c] += (b - a).total_seconds()
        return d

    per = {t: cat_sec(seg_by[t]) for t in tids}
    life_sec = {t: (life[t][1] - life[t][0]).total_seconds() for t in tids}
    all_cat = defaultdict(float)
    for t in tids:
        for c, v in per[t].items():
            all_cat[c] += v
    total_life = sum(life_sec.values())

    # VPS: сборки внутри запусков инженера
    active_all = union([clip((r.start, r.active_end if not r.running else until), since, until)
                        for rs in runs.values() for r in rs])
    long_ses = [(a, b, n) for a, b, n in vps_ses if (b - a).total_seconds() >= VPS_LONG_S and b > since and a < until]
    inter = []
    for a, b, _ in long_ses:
        c = clip((a, b), since, until)
        if c:
            inter += [clip(c, x, y) for x, y in active_all]
    build_in_run = total(inter)
    eng_total = all_cat.get("eng", 0.0)
    build_est = min(build_in_run, eng_total)

    # --- отчёт ---
    L = []
    w = L.append
    w(f"# Цикл работы команды — {since:%d.%m %H:%M}–{until:%d.%m %H:%M} GMT+4 (окно {mins(wsec)} мин)")
    w("")
    w(f"Сформировано `tools/team/cycle-report.py` в {datetime.now(TZ):%d.%m %H:%M} GMT+4 (только чтение). Тикеты: "
      f"{', '.join(tids)}. Время — GMT+4.")
    w("")
    if gaps_missing:
        w("**Нет данных:** " + "; ".join(gaps_missing) + ".")
        w("")

    # А
    w("## А. По тикетам")
    w("")
    w("Запуск — один запуск роли диспетчером. «Активные» — сумма `duration_ms`; «накладные» — от конца `duration_ms` до "
      "строки в `runs.log` (старт CLI, опрос диспетчера раз в 15 с). «Пустой» — короче 60 с или без новой записи роли "
      "в логе тикета за время запуска. Паузы — время между концом запуска и началом следующего; причина — по "
      "`reason` следующего запуска (`wait_for-met` — ждал сервер счёта; `next` + запись CEO в паузе — ждал CEO; запись "
      "dispatcher «заблокирована» до записи CEO — блокировка; остальное — простой диспетчера).")
    w("")
    w("| тикет | жизнь в окне, мин | запусков | активные мин | пустых (<60 с / без записи / всего) | накладные мин | "
      "ждал сервер, мин | ждал CEO, мин | блок диспетчера, мин | прочий простой, мин |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for t in tids:
        rs = [r for r in runs.get(t, []) if r.end > since and r.start < until]
        d = per[t]
        short = sum(1 for r in rs if r.dur is not None and r.dur < EMPTY_RUN_S)
        noent = sum(1 for r in rs if not r.has_entry and not r.running)
        empty = sum(1 for r in rs if not r.running and ((r.dur is not None and r.dur < EMPTY_RUN_S) or not r.has_entry))
        srv = sum(d.get(c, 0.0) for c in WAIT_CATS)
        w(f"| {t} | {mins(life_sec[t])} | {len(rs)} | {mins(d.get('eng', 0))} | {short} / {noent} / {empty} | "
          f"{mins(d.get('ovh', 0))} | {mins(srv)} | {mins(d.get('ceo', 0))} | {mins(d.get('blk', 0))} | "
          f"{mins(d.get('lag', 0))} |")
    n_runs = sum(1 for t in tids for r in runs.get(t, []) if r.end > since and r.start < until)
    w(f"| **итого** | {mins(total_life)} | {n_runs} | {mins(all_cat.get('eng', 0))} | | {mins(all_cat.get('ovh', 0))} | "
      f"{mins(sum(all_cat.get(c, 0) for c in WAIT_CATS))} | {mins(all_cat.get('ceo', 0))} | "
      f"{mins(all_cat.get('blk', 0))} | {mins(all_cat.get('lag', 0))} |")
    w("")
    w("Доли от жизни тикета в окне (строки суммируются до 100 %):")
    w("")
    w("| тикет | работа | накладные | сервер счёта | CEO | блок диспетчера | прочий простой |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for t in tids:
        d, ls = per[t], life_sec[t] or 1
        srv = sum(d.get(c, 0.0) for c in WAIT_CATS)
        w(f"| {t} | {d.get('eng', 0) / ls:.0%} | {d.get('ovh', 0) / ls:.0%} | {srv / ls:.0%} | {d.get('ceo', 0) / ls:.0%} | "
          f"{d.get('blk', 0) / ls:.0%} | {d.get('lag', 0) / ls:.0%} |")
    w("")
    w("Пауза «ждал сервер» по составу (все тикеты): " + "; ".join(
        f"{CAT_NAMES[c]} — {mins(all_cat.get(c, 0))} мин" for c in WAIT_CATS) + ".")
    w("")

    top = []
    for t in tids:
        for g0, g1, reason, parts in gap_by[t]:
            top.append(((g1 - g0).total_seconds(), t, g0, g1, reason, parts))
    top.sort(key=lambda x: -x[0])
    w("Самые долгие паузы между запусками:")
    w("")
    w("| тикет | с | по | мин | следующий запуск | из чего |")
    w("|---|---|---|---:|---|---|")
    for sec, t, g0, g1, reason, parts in top[:6]:
        agg = defaultdict(float)
        for a, b, c in parts:
            agg[c] += (b - a).total_seconds()
        comp = ", ".join(f"{CAT_NAMES[c].split(' (')[0]} {mins(v)}" for c, v in sorted(agg.items(), key=lambda kv: -kv[1]))
        w(f"| {t} | {hhmm(g0)} | {hhmm(g1)} | {mins(sec)} | {reason} | {comp} |")
    w("")

    # Б
    w("## Б. Сервер счёта (замок `/data/tk-bench.lock`)")
    w("")
    srv_missing = not calc_ok
    if srv_missing:
        w("Нет данных (журнал или metrics.txt не прочитаны).")
        w("")
    else:
        w("Очередь = жизнь юнита до начала счёта (счёт = `[время metrics.txt − wall_s, время metrics.txt]`; у цепочек "
          "из нескольких замеров — промежутки между замерами тоже очередь); «хвост» — после последнего замера до конца "
          "юнита (сверка diff). Юнит без метрик и с замком — считается стоявшим в очереди до остановки; без замка и без "
          "метрик (сборка стенда, цепочка) — служебный.")
        w("")
        w("| юнит | тикет | старт | конец | жизнь, мин | очередь, мин | счёт, мин | хвост, мин | итог |")
        w("|---|---|---|---|---:|---:|---:|---:|---|")
        rows = []
        for u in sorted(units, key=lambda x: x.s):
            lf = (u.e - u.s).total_seconds()
            q = total(u.queue)
            c = total(u.compute) + total(u.unknown)
            tl = total(u.tail)
            sv = total(u.service)
            note = {"metrics": "", "no-metrics-queue": "метрик нет", "unknown": "метрик нет, не разделено",
                    "service": "служебный"}[u.split]
            st = {"ok": "ок", "failed": "упал", "stopped": "остановлен", "running": "идёт"}[u.state]
            w(f"| {u.name} | {u.tid} | {hhmm(u.s)} | {hhmm(u.e)}{'…' if u.state == 'running' else ''} | {mins(lf)} | "
              f"{mins(q)} | {mins(c + sv)} | {mins(tl)} | {st}{', ' + note if note else ''} |")
            rows.append((u, q))
        w("")
        win = (since, until)
        m_iv = union([clip(i, *win) for u in units for i in u.compute])
        extra = [clip((m.t - timedelta(seconds=m.wall), m.t), *win) for m in metrics
                 if m.wall and not m.dup and not m.owner]
        meas = union(m_iv + extra)
        tails = subtract(union([clip(i, *win) for u in units for i in u.tail]), meas)
        service = subtract(union([clip(i, *win) for u in units for i in u.service]), meas + tails)
        busy = union(meas + tails)
        idle = subtract([win], busy)
        queue_sum = sum(total([clip(i, *win) for i in u.queue]) for u in units)
        ov = []
        for m in metrics:
            if m.dup or not m.wall or m.wall < 5:
                continue
            end = m.anchor or m.t
            c = clip((end - timedelta(seconds=m.wall), end), *win)
            if c:
                ov += [(c[0], 1), (c[1], -1)]
        overlap_sec, cnt, last, overlaps = 0.0, 0, None, []
        for tt, d in sorted(ov):
            if cnt >= 2 and last is not None:
                overlap_sec += (tt - last).total_seconds()
                overlaps.append((last, tt))
            cnt += d
            last = tt
        ev = []
        for u in units:
            for a, b in u.queue:
                c = clip((a, b), *win)
                if c:
                    ev += [(c[0], 1), (c[1], -1)]
        cur = peak = 0
        for _, d in sorted(ev):
            cur += d
            peak = max(peak, cur)
        w(f"Сервер за окно ({mins(wsec)} мин): занят замером — **{mins(total(meas))} мин ({total(meas) / wsec:.0%})**, "
          f"хвосты юнитов вне замера — {mins(total(tails))} мин (в «занят» входят), служебные юниты без замера "
          f"(сборка стенда, цепочка-ожидатель) — {mins(total(service))} мин (не входят), простаивал — "
          f"**{mins(total(idle))} мин ({total(idle) / wsec:.0%})**. Суммарно юниты простояли в очереди за замком **{mins(queue_sum)} мин** "
          f"(пик одновременно в очереди — {peak}); минут, когда считалось два замера и больше одновременно (замок "
          f"их не развёл): {mins(overlap_sec)}; метрик без `wall_s`: "
          f"{sum(1 for m in metrics if not m.dup and m.wall is None)}.")
        w("")
        if overlaps:
            w("Одновременные замеры: " + "; ".join(
                f"{hhmm(a)}–{hhmm(b)} ({mins((b - a).total_seconds())} мин)"
                for a, b in sorted(overlaps, key=lambda x: -(x[1] - x[0]).total_seconds())[:5]) + ".")
            w("")
        if idle:
            w("Самые долгие простои сервера: " + "; ".join(
                f"{hhmm(a)}–{hhmm(b)} ({mins((b - a).total_seconds())} мин)"
                for a, b in sorted(idle, key=lambda x: -(x[1] - x[0]).total_seconds())[:3]) + ".")
            w("")
        w("Топ-5 самых долгих ожиданий замка:")
        w("")
        w("| юнит | тикет | старт | ждал, мин |")
        w("|---|---|---|---:|")
        for u, q in sorted(rows, key=lambda x: -x[1])[:5]:
            w(f"| {u.name} | {u.tid} | {hhmm(u.s)} | {mins(q)} |")
        w("")

    # В
    w("## В. VPS сборок (13.140.29.171)")
    w("")
    if not vps_ok:
        w("Нет данных (журнал VPS не прочитан).")
        w("")
    else:
        w(f"Сборки `tools/vps-check.sh` ходят в systemd как обычные ssh-сеансы (юнитов сборки в журнале нет, кроме "
          f"{len(vps_builds) // 2 if vps_builds else 0} разовых `*-build.service`), поэтому длительность берётся по "
          f"сеансам `systemd-logind` длиннее {VPS_LONG_S} с (scp + `flock .build.lock` + cargo; очередь за замком "
          f"сборки внутри не разделена). В выборку попадают и ручные сборки CEO/Судьи.")
        w("")
        if long_ses:
            durs = [(b - a).total_seconds() for a, b, _ in long_ses]
            ev = []
            for a, b, _ in long_ses:
                ev += [(a, 1), (b, -1)]
            cur = pk = 0
            for _, d in sorted(ev):
                cur += d
                pk = max(pk, cur)
            w(f"- Сеансов ≥ {VPS_LONG_S} с: **{len(long_ses)}**; средняя {mins(sum(durs) / len(durs))} мин, "
              f"максимум {mins(max(durs))} мин, суммарно {mins(sum(durs))} мин; одновременно шло не больше {pk}.")
            w(f"- Из них внутри активных минут запусков инженеров: **{mins(build_in_run)} мин** — это верхняя оценка «ожидания "
              f"сборки» (блокирующий вызов держит запуск роли).")
            w("")
            w("| с | по | мин |")
            w("|---|---|---:|")
            for a, b, _ in sorted(long_ses, key=lambda x: -(x[1] - x[0]).total_seconds())[:5]:
                w(f"| {hhmm(a)} | {hhmm(b)} | {mins((b - a).total_seconds())} |")
            w("")
        else:
            w("Длинных сеансов за окно нет.")
            w("")

    # Г
    w("## Г. Итог: куда ушли инженеро-часы")
    w("")
    hrs = total_life / 3600
    work = all_cat.get("eng", 0.0) - build_est
    rows_g = [
        ("работа инженера (модель и локальные инструменты в запуске)", work),
        ("ожидание сборки на VPS (внутри запуска, верхняя оценка)", build_est),
        ("накладные запуска (старт CLI + опрос диспетчера)", all_cat.get("ovh", 0.0)),
        ("ожидание очереди сервера (юнит стоит за замком)", all_cat.get("w_queue", 0.0)),
        ("сам замер на сервере", all_cat.get("w_run", 0.0)),
        ("ждал юнит без метрик (очередь и счёт не разделены)", all_cat.get("w_unk", 0.0)),
        ("лаг диспетчера после готовности замера", all_cat.get("w_lag", 0.0)),
        ("ждал не юнит (сборка, файл, тикет) — причина не видна", all_cat.get("w_other", 0.0)),
        ("ожидание CEO", all_cat.get("ceo", 0.0)),
        ("блокировки диспетчера (до снятия CEO)", all_cat.get("blk", 0.0)),
        ("прочий простой диспетчера (лаг запуска, возобновление, повтор)", all_cat.get("lag", 0.0)),
    ]
    w("| куда ушло | инженеро-часы | доля |")
    w("|---|---:|---:|")
    for name, v in rows_g:
        w(f"| {name} | {v / 3600:.2f} | {v / (total_life or 1):.1%} |")
    w(f"| **всего** | **{hrs:.2f}** | 100 % |")
    w("")
    nt = len(tids)
    w(f"Инженеро-часы = сумма жизни тикетов в окне (с заведения тикета, не раньше начала окна); при «{nt} × окно» это "
      f"{nt * wsec / 3600:.2f} ч — TK-051/TK-052 заведены позже начала окна, их время до заведения не считаем.")
    w("")
    sync_pct = lambda v: f"{v / (total_life or 1):.0%}"
    w("**Итоговая строка:** из " + f"{hrs:.1f} инженеро-часов окна — работа инженера {sync_pct(work)}, ожидание сборки "
      f"{sync_pct(build_est)}, очередь сервера {sync_pct(all_cat.get('w_queue', 0))}, сам замер "
      f"{sync_pct(all_cat.get('w_run', 0))}, ждал CEO {sync_pct(all_cat.get('ceo', 0))}, блокировки и простой "
      f"диспетчера {sync_pct(all_cat.get('blk', 0) + all_cat.get('lag', 0) + all_cat.get('ovh', 0) + all_cat.get('w_lag', 0))}"
      f", ждал не юнит {sync_pct(all_cat.get('w_other', 0))}.")
    w("")

    # наблюдения — из чисел
    w("### Наблюдения")
    w("")
    obs = []
    pct = lambda v: f"{v / (total_life or 1):.0%}"
    srv_all = sum(all_cat.get(c, 0.0) for c in WAIT_CATS)
    pauses = sum(v for c, v in all_cat.items() if c not in ("eng", "ovh"))
    obs.append(f"Ожидание сервера счёта — {srv_all / 3600:.1f} ч ({pct(srv_all)} инженеро-часов, "
               f"{srv_all / max(pauses, 1):.0%} всех пауз), из них стояние юнитов за замком "
               f"{all_cat.get('w_queue', 0) / 3600:.1f} ч и сам счёт {all_cat.get('w_run', 0) / 3600:.1f} ч. Работа инженера — {pct(work)}, ожидание сборки на VPS внутри "
               f"запусков — до {pct(build_est)}.")
    if calc_ok:
        ratio = queue_sum / max(total(meas), 1.0)
        obs.append(f"Сервер не простаивает: замером занят {total(meas) / wsec:.0%} окна, простой {total(idle) / wsec:.0%}. "
                   f"Замеры идут по одному, очередь растёт: юниты суммарно простояли за замком {queue_sum / 3600:.1f} ч "
                   f"({ratio:.1f} мин ожидания на минуту счёта), пик — {peak} юнитов одновременно. При одном "
                   f"замке время ожидания растёт с числом замеров впереди; одновременных замеров (замок не развёл) — "
                   f"{mins(overlap_sec)} мин.")
        long_q = sorted(rows, key=lambda x: -x[1])[0]
        obs.append(f"Самое долгое ожидание замка — {long_q[0].name} ({long_q[0].tid}): {mins(long_q[1])} мин при счёте "
                   f"{mins(total(long_q[0].compute))} мин.")
    lag_w = all_cat.get("w_lag", 0.0)
    n_wait = sum(1 for t in tids for _g0, _g1, rsn, _p in gap_by[t] if rsn == "wait_for-met")
    timeouts = 0
    try:
        for ln in DISP_ERR.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\[dispatch\] (\S+) wait_for .*TimeoutExpired", ln)
            if m and since <= parse_when(m.group(1)) <= until:
                timeouts += 1
    except OSError:
        timeouts = -1
    runs_w = [r for t in tids for r in runs.get(t, []) if r.end > since and r.start < until and not r.running]
    ovh_avg = all_cat.get("ovh", 0.0) / max(len(runs_w), 1)
    obs.append(f"Диспетчер: от конца юнита до запуска инженера — {lag_w / 60:.1f} мин суммарно "
               f"({lag_w / max(n_wait, 1):.0f} с на ожидание, {n_wait} ожиданий); на каждый запуск накладных "
               f"{ovh_avg:.0f} с (старт CLI + опрос), всего {mins(all_cat.get('ovh', 0))} мин; ssh-проверка `wait_for` "
               f"срывалась по таймауту {timeouts if timeouts >= 0 else 'н/д'} раз (`dispatch.err.log`).")
    short_w = sum(1 for r in runs_w if r.dur is not None and r.dur < EMPTY_RUN_S and r.reason == "wait_for-met")
    empties = sum(1 for r in runs_w if (r.dur is not None and r.dur < EMPTY_RUN_S) or not r.has_entry)
    obs.append(f"Пустых запусков (короче минуты или без записи в лог) — {empties} из {len(runs_w)}; из них "
               f"{short_w} — пробуждение по готовности замера, где инженер за минуту лишь ставит следующий замер "
               f"(платит старт CLI и 15-секундный опрос за каждое); строк-дублей в `runs.log`: {dup_lines}.")
    obs.append(f"CEO ждали {mins(all_cat.get('ceo', 0))} мин ({pct(all_cat.get('ceo', 0))}), диспетчерских блокировок — "
               f"{mins(all_cat.get('blk', 0))} мин ({pct(all_cat.get('blk', 0))}); сборка на VPS внутри запусков — до "
               f"{mins(build_est)} мин из {mins(eng_total)} активных ({build_est / max(eng_total, 1):.0%}).")
    for i, o in enumerate(obs, 1):
        w(f"{i}. {o}")
    w("")
    w("### Ограничения метода")
    w("")
    w("- Причина паузы — по `reason` следующего запуска и записям в логе тикета; статус тикета по времени не хранится, "
      "поэтому «ждал сборку / файл / тикет» не отделяется от «ждал не юнит».")
    w("- Привязка `metrics.txt` к юниту — по времени записи файла, имени бинарника/юнита и каталогу тикета; метрики без "
      "юнита в журнале (процессы, пережившие юнит) идут в «занят замером», но не в очередь юнитов.")
    w("- Лаг после готовности считается от конца последнего юнита тикета в паузе до старта следующего запуска; если "
      "ждали не тот юнит, оценка занижена.")
    skew = "не измерено" if clock_skew is None else f"{clock_skew:+.1f} с"
    w(f"- Часы сервера счёта (CEST) и VPS (UTC) приведены к GMT+4; расхождение часов сервера счёта с этой машиной "
      f"в момент запуска отчёта: {skew} (на сервере `timedatectl` показывал «clock synchronized: no»), поправка не "
      f"вводилась.")
    w("- Сборки VPS — верхняя оценка по длинным ssh-сеансам, пересекающимся с активными минутами запусков; "
      "очередь за `.build.lock` внутри сеанса не видна.")
    w("")

    if args.all_gaps:
        w("### Приложение: все паузы между запусками")
        w("")
        w("| тикет | с | по | мин | следующий запуск | из чего |")
        w("|---|---|---|---:|---|---|")
        for t in tids:
            for g0, g1, reason, parts in gap_by[t]:
                agg = defaultdict(float)
                for a_, b_, c_ in parts:
                    agg[c_] += (b_ - a_).total_seconds()
                comp = ", ".join(f"{c_} {mins(v)}" for c_, v in sorted(agg.items(), key=lambda kv: -kv[1]))
                w(f"| {t} | {hhmm(g0)} | {hhmm(g1)} | {mins((g1 - g0).total_seconds())} | {reason} | {comp} |")
        w("")

    text = "\n".join(L)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[cycle-report] записано: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
