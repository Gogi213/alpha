#!/usr/bin/env python3
"""Правка .autopilot/state.js как JSON, а не как текста.

sync.py переписывает файл через json.dumps(indent=2), поэтому подстроки вида
'"id": "7.2", "status": "pending"' живут ровно до первого его вызова. Разбор
структуры от форматирования не зависит.

    python .autopilot/st.py ticket 7.2 in-progress
    python .autopilot/st.py ticket 0.1 done --tests 22/0 --commit abc1234
    python .autopilot/st.py stage build active
    python .autopilot/st.py note "что происходит"
"""

import argparse
import io
import json
import os
import subprocess
import sys

A = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(A, "state.js")


def now():
    return subprocess.run(["date", "-Iseconds"], capture_output=True, text=True).stdout.strip()


def load():
    raw = io.open(STATE, encoding="utf-8").read()
    head, _, body = raw.partition("=")
    return head + "=", json.loads(body.strip().rstrip(";"))


def save(head, st):
    st["updatedAt"] = now()
    tmp = STATE + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(head + "\n" + json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, STATE)


def find(items, key):
    for it in items:
        if it.get("id") == key:
            return it
    return None


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("ticket")
    t.add_argument("id")
    t.add_argument("status", choices=["pending", "in-progress", "review", "repair", "done", "failed"])
    t.add_argument("--tests", help="passed/failed, например 22/0")
    t.add_argument("--commit")

    s = sub.add_parser("stage")
    s.add_argument("id")
    s.add_argument("status", choices=["pending", "active", "done", "skipped", "failed"])
    s.add_argument("--note")

    n = sub.add_parser("note")
    n.add_argument("text")

    a = ap.parse_args()
    head, st = load()
    stamp = now()

    if a.cmd == "ticket":
        tk = find(st["tickets"], a.id)
        if tk is None:
            sys.exit("нет таска %s" % a.id)
        tk["status"] = a.status
        if a.status == "in-progress" and not tk.get("startedAt"):
            tk["startedAt"] = stamp
        if a.status in ("done", "failed"):
            tk.setdefault("startedAt", stamp)
            tk["finishedAt"] = stamp
        if a.tests:
            p, _, f = a.tests.partition("/")
            # шаблон читает {passed, failed}; строка отрисовалась бы как undefined
            tk["tests"] = {"passed": int(p), "failed": int(f or 0)}
        if a.commit:
            tk["commit"] = a.commit
        print("таск %s -> %s" % (a.id, a.status))

    elif a.cmd == "stage":
        sg = find(st["stages"], a.id)
        if sg is None:
            sys.exit("нет этапа %s" % a.id)
        sg["status"] = a.status
        if a.status == "active" and not sg.get("startedAt"):
            sg["startedAt"] = stamp
        if a.status in ("done", "skipped", "failed"):
            sg["finishedAt"] = stamp
        if a.note:
            sg["note"] = a.note
        print("этап %s -> %s" % (a.id, a.status))

    elif a.cmd == "note":
        sg = next((x for x in st["stages"] if x.get("status") == "active"), None)
        if sg is None:
            sys.exit("нет активного этапа")
        sg["note"] = a.text
        print("заметка на %s" % sg["id"])

    save(head, st)


if __name__ == "__main__":
    main()
