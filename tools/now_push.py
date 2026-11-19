#!/usr/bin/env python3
"""TK-631: что делается СЕЙЧАС — дочерние треды координатора bb → now.json → srv:/data/pages/ml/data/now.json (страница :8090, вкладка «Прогресс»).

Запуск раз в минуту скриптовой автоматикой bb (0 токенов). Только stdlib. Ничего не печатает при успехе (тихий тик); при сбое — строка в stderr, код 1.
  now_push.py [--coordinator thr_…] [--out файл] [--no-push]
Название треда — «роль · TK-NNN · тема · vN» (preferences п. 17). Статусы bb: active → run, error → fail, idle → done (только если закрыт не раньше DONE_HOURS назад).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

COORD = "thr_ty6ae9r46y"
DONE_HOURS = 6
HOST = "root@5.231.25.224"
REMOTE = "/data/pages/ml/data/now.json"
TITLE = re.compile(r"^\s*(?P<role>[^·]+?)\s*·\s*(?P<tk>TK-\d+)\s*·\s*(?P<topic>[^·]+?)\s*·\s*v(?P<v>\d+)\s*$")
STATE = {"active": "run", "error": "fail", "idle": "done"}


def parse_title(title):
    """«исп · TK-631 · progress-now · v1» → {role, tk, topic, version}; не по формату → None."""
    m = TITLE.match(title or "")
    return {"role": m["role"], "tk": m["tk"], "topic": m["topic"], "version": int(m["v"])} if m else None


def build(threads, now_ms, done_hours=DONE_HOURS):
    """Список тредов bb (bb thread list --json) → строки now.json: идут (run), с ошибкой (fail), закрытые за done_hours (done); новее — выше."""
    out = []
    for t in threads:
        st = STATE.get(t.get("status"))
        if not st or t.get("archivedAt") or t.get("deletedAt"):
            continue
        upd = t.get("updatedAt") or t.get("createdAt")
        if st == "done" and (not upd or now_ms - upd > done_hours * 3600e3):
            continue
        p = parse_title(t.get("title"))
        out.append({"id": t["id"], "state": st, "title": t.get("title") or "", "tk": p and p["tk"], "topic": p["topic"] if p else None,
                    "role": p and p["role"], "version": p and p["version"],
                    "started_ts": t["createdAt"] / 1000 if t.get("createdAt") else None, "updated_ts": upd / 1000 if upd else None})
    order = {"run": 0, "fail": 1, "done": 2}
    out.sort(key=lambda r: (order[r["state"]], -(r["updated_ts"] or 0)))
    return out


def bb_cmd():
    """На Windows BB_CLI указывает на файл без расширения (не запускается) — рядом лежит bb.cmd."""
    cli = os.environ.get("BB_CLI")
    if os.name == "nt":
        for c in ([cli + ".cmd"] if cli else []) + [shutil.which("bb.cmd"), cli]:
            if c and os.path.isfile(c) and c.lower().endswith((".cmd", ".exe", ".bat")):
                return c
    return cli or shutil.which("bb") or "bb"


def list_threads(coordinator):
    r = subprocess.run([bb_cmd(), "thread", "list", "--json", "--parent-thread", coordinator], capture_output=True, text=True, encoding="utf-8", timeout=40)
    d = json.loads(r.stdout)
    if not isinstance(d, list):
        raise RuntimeError(f"bb thread list: {str(d)[:200]}")
    return d


def push(path):
    """Один ssh: запись во временный файл и mv — страница не увидит половину файла. Ключ и known_hosts — явно (кириллица в HOME)."""
    ssh = os.path.join(os.path.expanduser("~"), ".ssh")
    cmd = [shutil.which("ssh") or "ssh", "-i", os.path.join(ssh, "id_rsa"), "-o", "UserKnownHostsFile=" + os.path.join(ssh, "known_hosts"),
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, f"cat > {REMOTE}.tmp && mv {REMOTE}.tmp {REMOTE}"]
    with open(path, "rb") as f:
        r = subprocess.run(cmd, stdin=f, capture_output=True, timeout=40)
    if r.returncode != 0:
        raise RuntimeError("ssh: " + (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["rc=%d" % r.returncode])[-1][:200])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--coordinator", default=COORD)
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP") or os.environ.get("TMPDIR") or ".", "alpha-now.json"))
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args(argv)
    now = time.time()
    try:
        data = {"generated_ts": now, "coordinator": a.coordinator, "done_hours": DONE_HOURS, "threads": build(list_threads(a.coordinator), now * 1000)}
        tmp = a.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, a.out)
        if not a.no_push:
            push(a.out)
    except Exception as e:  # noqa: BLE001 — одна строка причины в вывод автоматики, код 1
        print(f"now_push: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
