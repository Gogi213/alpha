"""Сторож CEO без модели (судья TK-002 п.2) — заменяет получасовой крон CEO. Покрывает то же самое:
диспетчер жив, траты против бюджета, тревоги Steam Deck (ALERT-*), простой Steam Deck при непустой
очереди, тикеты-сироты (in_progress/waiting без новой записи дольше порога), blocked/needs_owner.
Ошибка ssh/чтения — сама сигнал (не молчание, п.2в). Дедуп по (вид, ключ), повтор раз в
WATCH_DEDUP_REPEAT_HOURS, пока проблема не снята (п.2г). Пишет `ceo-wake.log`/`ceo-inbox.md` только
при находке; сердцебиение (`watch-heartbeat.json`) обновляется каждый цикл независимо от находок —
его возраст проверяет хук `role_memory.py` (кто сторожит сторожа, п.2а).

Запуск: python .claude/dispatcher/watch.py --once   (для крона/планировщика Windows)
        python .claude/dispatcher/watch.py           (цикл раз в WATCH_INTERVAL_S)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch as D  # noqa: E402 — переиспользуем пути/константы/append_ceo_inbox/_pid_alive
import ticket as T  # noqa: E402

DISPATCHER_DIR = Path(__file__).resolve().parent
WATCH_HEARTBEAT_FILE = DISPATCHER_DIR / "watch-heartbeat.json"
WATCH_STATE_FILE = DISPATCHER_DIR / "watch-state.json"

WATCH_INTERVAL_S = float(os.environ.get("ALPHA_WATCH_INTERVAL", "120"))
WATCH_DEDUP_REPEAT_HOURS = float(os.environ.get("ALPHA_WATCH_REPEAT_HOURS", "2"))
DISPATCH_STALE_MINUTES = float(os.environ.get("ALPHA_WATCH_DISPATCH_STALE_MIN", "5"))
ORPHAN_TICKET_HOURS = float(os.environ.get("ALPHA_WATCH_ORPHAN_HOURS", "2"))
DECK_QUEUE_STALE_MINUTES = float(os.environ.get("ALPHA_WATCH_DECK_QUEUE_STALE_MIN", "30"))


@dataclass
class Finding:
    kind: str
    key: str
    message: str


# --- сбор находок (чистые функции — без сети, кроме ssh-хелперов ниже) ------------------------

def check_dispatcher_alive(state: dict, now) -> list:
    """п.2б: диспетчер жив — по времени последнего тика (dispatch.tick() пишет state["last_tick"])."""
    last_tick = state.get("last_tick")
    if not last_tick:
        return [Finding("dispatcher-down", "last_tick", "в state.json нет last_tick — диспетчер ни разу не тикнул "
                                                          "с начала наблюдения или это не тот state.json")]
    age_min = (now - T.parse_dt(last_tick)).total_seconds() / 60
    if age_min > DISPATCH_STALE_MINUTES:
        return [Finding("dispatcher-down", "last_tick",
                         f"последний тик диспетчера {age_min:.0f} мин назад (> {DISPATCH_STALE_MINUTES:.0f})")]
    return []


def check_budgets(state: dict, now) -> list:
    """п.2б: траты против бюджета — вторая (независимая) проверка сверх собственных гейтов dispatch.py."""
    out = []
    if D._daily_budget_exceeded(state, now):
        spent = state.get("daily_cost", {}).get(D._today(now), 0.0)
        out.append(Finding("budget-watch", "daily", f"суточный расход ${spent:.2f} ≥ ${D.DAILY_COST_USD:.2f}"))
    if D._hour_budget_exceeded(state, now):
        cost = D._rolling_hour_cost(state, now)
        out.append(Finding("budget-watch", "hour", f"часовой расход ${cost:.2f} ≥ ${D.HOUR_COST_USD:.2f}"))
    for tid, budget in state.get("ticket_budget", {}).items():
        if D.ticket_budget_exceeded(state, tid):
            spent = D.ticket_cost_spent(state, tid)
            out.append(Finding("budget-watch", f"ticket:{tid}", f"{tid}: потрачено ${spent:.2f} из ${budget:.2f}"))
    return out


def check_blocked_and_needs_owner(now) -> list:
    """п.2б: blocked/needs_owner — сторож пересобирает список сам (не полагаясь только на то, что
    dispatch.py однажды уже написал в ceo-inbox — вдруг тот запуск и был тем, что легло)."""
    out = []
    for path in T.list_tickets(D.TICKETS_DIR):
        try:
            tkt = T.read_ticket(path)
        except Exception as e:
            out.append(Finding("ticket-unreadable", path.stem, f"{path.stem}: не читается — {type(e).__name__}: {e}"))
            continue
        if tkt.status in ("blocked", "needs_owner"):
            out.append(Finding(tkt.status, tkt.id, f"{tkt.id}: status={tkt.status}"))
    return out


def check_orphan_tickets(now) -> list:
    """п.2б: in_progress/waiting без новой записи дольше порога — сирота (TK-001 п.2, до правила
    (а') это значило «замерла навсегда»; правило (а') её теперь будит, но сторож всё равно следит на
    случай, если тикет застрял по другой причине — троттлинг/бюджет/сама роль не отвечает)."""
    out = []
    for path in T.list_tickets(D.TICKETS_DIR):
        try:
            tkt = T.read_ticket(path)
        except Exception:
            continue
        if tkt.status not in ("in_progress", "waiting"):
            continue
        last_ts = tkt.log[-1].ts if tkt.log else T.parse_dt(tkt.header.get("updated")) if tkt.header.get(
            "updated") else None
        if last_ts is None:
            continue
        age_h = (now - last_ts).total_seconds() / 3600
        if age_h > ORPHAN_TICKET_HOURS:
            out.append(Finding("orphan-ticket", tkt.id,
                                f"{tkt.id}: status={tkt.status} без новой записи {age_h:.1f} ч"))
    return out


def _ssh_run(cmd_suffix: str, timeout: float = 10.0):
    """Общий ssh-вызов на Steam Deck теми же умолчаниями, что и dispatch._deck_file_exists (кириллический
    HOME). Возвращает (ok, stdout) — ok=False на любой ошибке (сама по себе становится находкой, п.2в)."""
    host = os.environ.get("ALPHA_DECK_HOST", "deck@192.168.1.49")
    key = os.environ.get("ALPHA_DECK_KEY", r"C:/Users/Георгий/.ssh/id_rsa")
    known_hosts = os.environ.get("ALPHA_DECK_KNOWN_HOSTS", r"C:/Users/Георгий/.ssh/known_hosts")
    cmd = ["ssh", "-i", key, "-o", f"UserKnownHostsFile={known_hosts}", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=8", host, cmd_suffix]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return False, (r.stderr or "").strip()[:200]
        return True, (r.stdout or "").strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_steam_deck(ssh_run=_ssh_run) -> list:
    """п.2б/в: ALERT-* Steam Deck + простой при непустой очереди; ssh-хелпер подменяем в тестах."""
    out = []
    ok, alerts = ssh_run("for f in ~/alpha/queue/ALERT-*; do [ -f \"$f\" ] && "
                          "echo \"$(basename $f): $(head -c 200 $f)\"; done; true")
    if not ok:
        out.append(Finding("deck-ssh-error", "alerts", f"не удалось проверить тревоги Steam Deck: {alerts}"))
    elif alerts.strip():
        for line in alerts.strip().splitlines():
            name = line.split(":", 1)[0].strip()
            out.append(Finding("deck-alert", name, f"Steam Deck: {line[:200]}"))

    # простой при непустой очереди: HOLD снят, очередь непуста, но STATUS давно не обновлялся
    ok2, status_info = ssh_run(
        "if [ -f ~/alpha/queue/HOLD ]; then echo HOLD; else "
        "n=$(ls ~/alpha/queue/*.json 2>/dev/null | wc -l); "
        "age=$(( $(date +%s) - $(stat -c %Y ~/alpha/queue/STATUS 2>/dev/null || echo 0) )); "
        "echo \"$n $age\"; fi")
    if not ok2:
        out.append(Finding("deck-ssh-error", "queue", f"не удалось проверить очередь Steam Deck: {status_info}"))
    elif status_info.strip() and status_info.strip() != "HOLD":
        try:
            n_pending, age_s = (int(x) for x in status_info.split())
            if n_pending > 0 and age_s > DECK_QUEUE_STALE_MINUTES * 60:
                out.append(Finding("deck-idle", "queue",
                                    f"очередь Steam Deck не пуста ({n_pending}), STATUS не обновлялся "
                                    f"{age_s // 60:.0f} мин — похоже на простой"))
        except ValueError:
            pass  # неожиданный вывод — не валим находками на угад, но и не молчим полностью:
    return out


def collect_findings(state: dict, now, ssh_run=_ssh_run) -> list:
    findings = []
    findings += check_dispatcher_alive(state, now)
    findings += check_budgets(state, now)
    findings += check_blocked_and_needs_owner(now)
    findings += check_orphan_tickets(now)
    findings += check_steam_deck(ssh_run)
    return findings


# --- дедуп (вид, ключ) с повтором раз в WATCH_DEDUP_REPEAT_HOURS, пока не снято (п.2г) -----------

def load_watch_state() -> dict:
    try:
        return json.loads(WATCH_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_watch_state(ws: dict) -> None:
    WATCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATCH_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ws, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(WATCH_STATE_FILE)


def notify_findings(findings: list, ws: dict, now) -> list:
    """Возвращает находки, по которым реально написали (для тестов); дедуп — по (kind, key)."""
    notified = ws.setdefault("notified", {})
    current_keys = set()
    posted = []
    for f in findings:
        marker = f"{f.kind}:{f.key}"
        current_keys.add(marker)
        last = notified.get(marker)
        if last is None or (now - T.parse_dt(last)).total_seconds() >= WATCH_DEDUP_REPEAT_HOURS * 3600:
            D.append_ceo_inbox("*", f"watch-{f.kind}", f.message, now)
            notified[marker] = T.now_iso(now)
            posted.append(f)
    # снятые находки — забыть, чтобы будущее повторение не ждало старого 2-часового окна
    for marker in list(notified):
        if marker not in current_keys:
            notified.pop(marker, None)
    return posted


def write_heartbeat(now, findings_count: int) -> None:
    WATCH_HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATCH_HEARTBEAT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"ts": T.now_iso(now), "findings": findings_count}, ensure_ascii=False),
                    encoding="utf-8")
    tmp.replace(WATCH_HEARTBEAT_FILE)


def run_once(now=None, ssh_run=_ssh_run) -> list:
    now = now or datetime.now().astimezone()
    state = D.load_state()
    findings = collect_findings(state, now, ssh_run)
    ws = load_watch_state()
    posted = notify_findings(findings, ws, now)
    save_watch_state(ws)
    write_heartbeat(now, len(findings))
    return posted


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--once" in argv:
        posted = run_once()
        print(f"[watch] once: findings={len(posted)}")
        return 0
    print(f"[watch] loop every {WATCH_INTERVAL_S:.0f}s")
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[watch] cycle error: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(WATCH_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
