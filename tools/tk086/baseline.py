"""TK-086 (4): база «до» — токены интерактивных сессий CEO за локальные сутки (GMT+4) из транскриптов Claude Code.
Сессия роли — первая реплика начинается с «Ты — <роль> команды»; остальное (владелец, CEO) — интерактивные.
Usage: baseline.py YYYY-MM-DD[THH:MM] [часов=24] [каталог транскриптов]; окно — с указанного момента (GMT+4). Токены — по уникальным message.id (стриминг дублирует строки)."""
import json, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=4))
ROLE = re.compile(r"^\s*Ты — (engineer|judge|researcher|ceo-triage|исследователь|инженер|судья)\b", re.I)


def first_user_text(lines):
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("type") == "user":
            c = r.get("message", {}).get("content")
            if isinstance(c, list):
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            return c or ""
    return ""


def main():
    day = sys.argv[1]
    start = datetime.fromisoformat(day if "T" in day else day + "T00:00").replace(tzinfo=TZ)
    end = start + timedelta(hours=float(sys.argv[2]) if len(sys.argv) > 2 else 24)
    root = Path(sys.argv[3] if len(sys.argv) > 3 else Path.home() / ".claude/projects/C--visual-projects-alpha")
    out = {"interactive": {}, "role": {}}
    sess = {"interactive": {}, "role": {}}
    for f in root.glob("*.jsonl"):
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        kind = "role" if ROLE.match(first_user_text(lines)) else "interactive"
        seen = set()
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            m = r.get("message") or {}
            u = m.get("usage")
            if r.get("type") != "assistant" or not u or m.get("id") in seen:
                continue
            ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).astimezone(TZ)
            if not start <= ts < end:
                continue
            seen.add(m.get("id"))
            s = sess[kind].setdefault(f.stem, {"turns": 0, "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0})
            s["turns"] += 1
            s["input"] += u.get("input_tokens", 0)
            s["cache_creation"] += u.get("cache_creation_input_tokens", 0)
            s["cache_read"] += u.get("cache_read_input_tokens", 0)
            s["output"] += u.get("output_tokens", 0)
    for kind, d in sess.items():
        tot = {k: sum(s[k] for s in d.values()) for k in ("turns", "input", "cache_creation", "cache_read", "output")}
        out[kind] = {"sessions": len(d), **tot, "top": sorted(((k, v["turns"], v["cache_read"]) for k, v in d.items()), key=lambda x: -x[2])[:5]}
    print(json.dumps({"day": day, **out}, ensure_ascii=False, indent=1))


main()
