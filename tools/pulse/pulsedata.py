"""Общее для plan.py, ask.py, view2.py: пути, время GMT+4, атомарная запись json (tmp + replace, повтор на Windows)."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISP = ROOT / ".claude" / "dispatcher"
TICKETS = ROOT / ".claude" / "tickets"
PULSE_DIR = ROOT / ".claude" / "pulse"
PLANS_DIR = PULSE_DIR / "plans"
QUESTIONS_DIR = PULSE_DIR / "questions"
TZ = timezone(timedelta(hours=4))  # GMT+4

VALID_ON = ("pc", "vps", "calc", "col", "you")  # метки машин + «вы»
STEP_STATES = ("todo", "run", "review", "repair", "wait", "bad", "done")  # run делается · review проверяется · repair чинится


def now_dt() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_dt()).isoformat(timespec="seconds")


def parse(s) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def hhmm(s) -> str | None:
    d = parse(s)
    return d.astimezone(TZ).strftime("%H:%M") if d else None


def tk_id(s: str) -> str:
    """«tk044» / «TK-44» → «TK-044»; остальное (orphans-vps, …) — как есть."""
    m = re.fullmatch(r"(?i)tk[-_]?(\d+)", (s or "").strip())
    return f"TK-{int(m.group(1)):03d}" if m else (s or "").strip()


def is_ticket(pid: str) -> bool:
    return bool(re.fullmatch(r"TK-\d+", pid)) and (TICKETS / f"{pid}.md").exists()


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, obj, retries: int = 20) -> None:
    """tmp + os.replace; на Windows replace падает с PermissionError, пока файл кто-то держит открытым, — повторы."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        for i in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == retries - 1:
                    raise
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
