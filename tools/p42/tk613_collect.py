#!/usr/bin/env python3
"""TK-613 / TK-631: сборщик данных вкладки «Прогресс» страницы srv:8090 — что идёт СЕЙЧАС.

Раз в минуту (таймер tk613-progress.timer) читает файлы и юниты srv — только чтение, ничего не трогает — и пишет один JSON
/data/pages/ml/data/tasks.json (его читает страница). Источники:
  now.json   /data/pages/ml/data/now.json — дочерние треды координатора bb (ПК владельца, tools/now_push.py раз в минуту): TK, тема, роль, статус;
             строка на задачу (исполнитель → проверяющий), русское имя — по TK из tk_names.json (нет — тема треда словами)
  юниты srv  systemd tk6*.service (кроме самого сборщика): имя, с какого времени, код возврата
  srv        нагрузка, место на /; сборки — очередь rbuild (/opt/rbuild, процессы run-srv.sh)
  София      нагрузка и место на / по ssh (ключ только на команду чтения, см. sofia_probe)
Использование: tk613_collect.py [выходной.json]. Корень данных — $TK613_DATA (по умолчанию /data). Ничего не падает от отсутствующих файлов.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.environ.get("TK613_DATA", "/data")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TK613_OUT", f"{ROOT}/pages/ml/data/tasks.json")
NOW = os.environ.get("TK613_NOW", f"{ROOT}/pages/ml/data/now.json")
NAMES = os.environ.get("TK613_NAMES", f"{ROOT}/p42-tk613/tk_names.json")
SOFIA = os.environ.get("TK613_SOFIA", "root@13.140.29.171")
SOFIA_KEY = os.environ.get("TK613_SOFIA_KEY", "/root/.ssh/id_tk613_sofia")
SOFIA_GUARD_GB = 23   # сторож места в Софии режет сборки при свободном ниже
SELF_UNITS = {"tk613-progress"}   # сам сборщик (oneshot раз в минуту) — не задача
UNIT_DONE_HOURS = 6               # завершённый юнит показывается, пока с конца не прошло столько
ROLES = {"исп": "исполнитель", "пров": "проверяющий", "чит": "читатель"}


def P(*a):
    return os.path.join(ROOT, *a)


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def jload(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def dec(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


# ── юниты srv: tk6*.service ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_show(text):
    """Вывод «systemctl show» по нескольким юнитам (блоки через пустую строку) → [{ключ: значение}]."""
    out = []
    for blk in re.split(r"\n\s*\n", text.strip()):
        d = dict(ln.split("=", 1) for ln in blk.splitlines() if "=" in ln)
        if d.get("Id"):
            out.append(d)
    return out


def unit_ts(v):
    """«@1795126468» (systemctl show --timestamp=unix) → epoch; пусто/0 → None."""
    m = re.fullmatch(r"@(\d+)", (v or "").strip())
    return float(m[1]) if m and int(m[1]) > 0 else None


def unit_rows(blocks, now):
    """Блоки systemctl show → строки для страницы: run (active/activating), fail (failed), done (inactive, недавно завершён)."""
    out = []
    for d in blocks:
        name = d["Id"].removesuffix(".service")
        if name in SELF_UNITS or not d["Id"].endswith(".service"):
            continue
        act, since, end = d.get("ActiveState"), unit_ts(d.get("ActiveEnterTimestamp")), unit_ts(d.get("ActiveExitTimestamp"))
        rc = int(d["ExecMainStatus"]) if (d.get("ExecMainStatus") or "").lstrip("-").isdigit() else None
        if act in ("active", "activating", "reloading"):
            st = "run"
        elif act == "failed":
            st = "fail"
        elif act == "inactive" and end and now - end <= UNIT_DONE_HOURS * 3600:
            st = "fail" if (rc not in (None, 0) or d.get("Result") not in (None, "", "success")) else "done"
        else:
            continue
        out.append({"name": name, "state": st, "since_ts": since, "end_ts": None if st == "run" else end, "rc": None if st == "run" else rc})
    out.sort(key=lambda u: ({"run": 0, "fail": 1, "done": 2}[u["state"]], -(u["since_ts"] or 0)))
    return out


def list_units(now):
    """None — не удалось прочитать systemd; иначе [строки юнитов tk6*]."""
    try:
        r = subprocess.run(["systemctl", "list-units", "--all", "--type=service", "--plain", "--no-legend", "--no-pager", "tk6*"],
                           capture_output=True, text=True, timeout=8)
        names = [ln.split()[0] for ln in r.stdout.splitlines() if ln.split()]
        names = [x for x in names if x.removesuffix(".service") not in SELF_UNITS]
        if not names:
            return []
        s = subprocess.run(["systemctl", "show", "--timestamp=unix", "--no-pager", "-p", "Id,ActiveState,Result,ExecMainStatus,ActiveEnterTimestamp,ActiveExitTimestamp"] + names,
                           capture_output=True, text=True, timeout=8)
        return unit_rows(parse_show(s.stdout), now)
    except (OSError, subprocess.SubprocessError):
        return None


# ── что идёт сейчас: треды bb ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def group_tasks(threads, names):
    """Треды (строки now.json) → по одной строке на задачу TK: цепочка ролей по порядку старта («исполнитель → проверяющий»), состояние — «идёт», если идёт любой тред
    задачи, иначе состояние последнего по старту; конец — когда закрылся последний тред (только для готово/ошибка). Тред без номера — своя строка."""
    groups = {}
    for t in threads:
        if isinstance(t, dict) and t.get("state") in ("run", "fail", "done"):
            groups.setdefault(t.get("tk") or t.get("id"), []).append(t)
    out = []
    for key, g in groups.items():
        g.sort(key=lambda t: (t.get("started_ts") or 0, t.get("updated_ts") or 0))
        last = g[-1]
        st = "run" if any(t["state"] == "run" for t in g) else last["state"]
        chain = []
        for t in g:
            lab = ROLES.get(t.get("role"), t.get("role"))
            lab = lab and lab + (f" v{t['version']}" if (t.get("version") or 1) > 1 else "")
            if lab and (not chain or chain[-1] != lab):
                chain.append(lab)
        tk, topic = last.get("tk"), last.get("topic")
        starts = [t["started_ts"] for t in g if t.get("started_ts")]
        out.append({"id": key, "tk": tk, "name": names.get(tk) or (topic.replace("-", " ") if topic else last.get("title") or key), "named": bool(names.get(tk)),
                    "state": st, "chain": chain, "threads": len(g), "started_ts": min(starts) if starts else None,
                    "end_ts": None if st == "run" else last.get("updated_ts")})
    out.sort(key=lambda r: ({"run": 0, "fail": 1, "done": 2}[r["state"]], -(r["started_ts"] or 0)))
    return out


def now_block(now, path=None, names_path=None):
    """now.json (его кладёт ПК) → {ok, pushed_ts, tasks[{id, tk, name, named, state, chain, threads, started_ts, end_ts}]}; name — русское по TK."""
    d = jload(path or NOW)
    if not isinstance(d, dict) or not isinstance(d.get("threads"), list):
        return {"ok": False, "err": "файл now.json не найден или не читается", "pushed_ts": None, "tasks": []}
    names = jload(names_path or NAMES)
    return {"ok": True, "pushed_ts": d.get("generated_ts"), "tasks": group_tasks(d["threads"], names if isinstance(names, dict) else {})}


# ── сборки, нагрузка, диск ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def rbuild_state():
    """(идёт ли сборка, [(архив запроса, mtime архива)]) по процессам: очередь rbuild — run-srv.sh <архив> <команда>, сборка — cargo/rustc из /opt/rbuild."""
    reqs, building = [], False
    try:
        pids = [x for x in os.listdir("/proc") if x.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if len(cmd) >= 3 and cmd[1].endswith(b"/run-srv.sh"):
            arc = cmd[2].decode("utf-8", "replace")
            reqs.append((os.path.basename(arc), mtime(arc)))
        elif cmd and os.path.basename(cmd[0].decode("utf-8", "replace")) in ("cargo", "rustc") and any(b"/opt/rbuild/" in c for c in cmd):
            building = True
    return building, reqs


def builds(now):
    rb = rbuild_state()
    if rb is None:
        return {"ok": False, "text": "очередь сборок: не удалось прочитать процессы"}
    building, reqs = rb
    waiting = max(0, len(reqs) - (1 if building else 0))
    oldest = max((now - m for _, m in reqs if m), default=None)
    text = (f"сборка идёт, в очереди ждут {waiting}" if building else f"сборок нет, запросов в очереди {len(reqs)}") + (f" (старейший запрос {int(oldest // 60)} мин)" if oldest else "")
    return {"ok": True, "building": building, "waiting": waiting, "text": text}


def sofia_probe():
    """Нагрузка и место на / в Софии: ssh ключом, который на Софии разрешён только на одну команду чтения (forced command в authorized_keys)."""
    try:
        r = subprocess.run(["ssh", "-i", SOFIA_KEY, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5",
                            "-o", "UserKnownHostsFile=/root/.ssh/known_hosts", SOFIA, "true"], capture_output=True, text=True, timeout=12, stdin=subprocess.DEVNULL)
        out = r.stdout.strip().splitlines()
        if r.returncode != 0 or len(out) < 3:
            return {"ok": False, "err": (r.stderr.strip().splitlines() or ["пустой ответ"])[-1][:100]}
        la = [float(x) for x in out[0].split()[:3]]
        df = out[2].split()
        total, avail = int(df[1]), int(df[3])
        return {"ok": True, "load": la, "cpus": int(out[1]), "disk": {"free_gb": round(avail / 1e9, 1), "total_gb": round(total / 1e9, 1),
                                                                      "used_pct": round(100 * (total - avail) / total, 1), "guard_gb": SOFIA_GUARD_GB}}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"[:100]}


def host(now, sofia=None):
    try:
        l1, l5, l15 = os.getloadavg()
    except (OSError, AttributeError):
        l1 = l5 = l15 = None
    try:
        du = shutil.disk_usage("/")
        disk = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1), "used_pct": round(100 * du.used / du.total, 1)}
    except OSError:
        disk = None
    return {"load": [round(x, 2) for x in (l1, l5, l15)] if l1 is not None else None, "cpus": os.cpu_count(), "disk": disk, "sofia": sofia, "builds": builds(now)}


def collect(now=None, units=None, sofia=None):
    now = now or time.time()
    units = list_units(now) if units is None else units
    sofia = sofia_probe() if sofia is None else sofia
    return {"generated_ts": now, "host": host(now, sofia), "units_ok": units is not None, "units": units or [], "now": now_block(now)}


def main():
    data = collect()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(OUT, f"задач {len(data['now']['tasks'])}, юниты {len(data['units'])}")


if __name__ == "__main__":
    main()
