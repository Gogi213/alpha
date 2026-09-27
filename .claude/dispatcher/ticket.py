"""Формат тикета диспетчера alpha: `.claude/tickets/<ID>.md`.

Шапка между строками `---` (простые строки `ключ: значение`, без внешнего YAML):
`id, title, owner` (researcher|engineer|judge), `status`
(backlog|todo|in_progress|waiting|in_review|done|blocked|needs_owner), `reviewer` (опц.),
`wait_for` (опц.: `file:<путь>` локально, `deck:<путь>` на Steam Deck через ssh, `mention`),
`updated`. `backlog` — задача перенесена (например из TASKS.md), но ещё не в работе: диспетчер её
не трогает (`dispatch.decide()`), в `todo` переводит `tickets.py start <ID>`.

Тело: свободное описание, затем заголовок `## Лог` — записи вида
`### <ISO-время> <автор>` + текст; упоминания `@researcher`/`@engineer`/`@judge`/`@ceo`.

Только stdlib. Роли и авторы записей — латинские ключи (researcher/engineer/judge/ceo/
dispatcher), не русские названия: так упоминания и авторство сравниваются без транслитерации.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HEADER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.S)
LOG_HEADING_RE = re.compile(r"^##\s*Лог\s*$", re.M)
ENTRY_RE = re.compile(r"^###\s+(\S+)\s+(.+?)\s*$", re.M)
MENTION_RE = re.compile(r"@(researcher|engineer|judge|ceo)\b", re.I)
ROLE_TOKENS = ("researcher", "engineer", "judge", "ceo")


def now_iso(now: datetime | None = None) -> str:
    dt = now or datetime.now().astimezone()
    return dt.isoformat(timespec="seconds")


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class LogEntry:
    ts: datetime
    ts_raw: str
    author: str
    text: str

    @property
    def mentions(self) -> set:
        return {m.lower() for m in MENTION_RE.findall(self.text)}


@dataclass
class Ticket:
    path: Path
    header: dict
    description: str
    log: list

    @property
    def id(self) -> str:
        return self.header.get("id") or (self.path.stem if self.path else "")

    @property
    def status(self) -> str:
        return self.header.get("status", "")

    @property
    def owner(self) -> str:
        return self.header.get("owner", "")

    @property
    def reviewer(self) -> str:
        return self.header.get("reviewer") or ""

    @property
    def executor(self) -> str:
        return self.header.get("executor") or ""

    @property
    def kind(self) -> str:
        return self.header.get("kind") or ""

    def logged_since(self, author: str, since: datetime) -> bool:
        author = author.lower()
        return any(e.author.lower() == author and e.ts > since for e in self.log)


def _match_header(text: str):
    m = HEADER_RE.match(text)
    if not m:
        raise ValueError("тикет без шапки `---` ... `---`")
    return m


def _parse_header(text: str):
    m = _match_header(text)
    header = {}
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        header[key.strip()] = value.strip()
    return header, m.end()


def _parse_log(rest: str):
    entries = []
    heading = LOG_HEADING_RE.search(rest)
    if not heading:
        return entries
    body = rest[heading.end():]
    matches = list(ENTRY_RE.finditer(body))
    for i, m in enumerate(matches):
        ts_raw, author = m.group(1), m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        entry_text = body[start:end].strip("\n")
        try:
            ts = parse_dt(ts_raw)
        except ValueError:
            continue  # не запись лога (например, случайный `### ...` в описании) — пропустить
        entries.append(LogEntry(ts=ts, ts_raw=ts_raw, author=author, text=entry_text))
    return entries


def parse_text(text: str, path: Path = None) -> Ticket:
    header, body_start = _parse_header(text)
    rest = text[body_start:]
    heading = LOG_HEADING_RE.search(rest)
    description = (rest[: heading.start()] if heading else rest).strip()
    log = _parse_log(rest)
    return Ticket(path=path, header=header, description=description, log=log)


def read_ticket(path) -> Ticket:
    path = Path(path)
    return parse_text(path.read_text(encoding="utf-8"), path)


def write_header_updates(path, updates: dict, now: datetime = None, stamp_updated: bool = True) -> None:
    """Точечно правит строки шапки (значения `updates`), тело файла не трогает."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    m = _match_header(text)
    lines = m.group(1).splitlines()
    updates = dict(updates)
    if stamp_updated and "updated" not in updates:
        updates["updated"] = now_iso(now)
    seen = set()
    new_lines = []
    for line in lines:
        key = line.split(":", 1)[0].strip() if ":" in line else None
        if key in updates:
            new_lines.append(f"{key}: {updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}: {value}")
    new_header = "---\n" + "\n".join(new_lines) + "\n---\n"
    # newline="\n" — иначе на Windows write_text переводит \n в CRLF (судья 27.09: `git commit`
    # предупреждал «CRLF will be replaced by LF», цель fc2faa3 «задачи всегда LF» не держалась)
    path.write_text(new_header + text[m.end():], encoding="utf-8", newline="\n")


def append_log(path, author: str, text: str, now: datetime = None) -> None:
    """Дописывает запись `### <время> <автор>` в конец файла (лог — последняя секция)."""
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    if not content.endswith("\n"):
        content += "\n"
    if not LOG_HEADING_RE.search(content):
        if not content.endswith("\n\n"):
            content += "\n"
        content += "## Лог\n"
    if not content.endswith("\n\n"):
        content += "\n"
    content += f"### {now_iso(now)} {author}\n{text.strip()}\n"
    path.write_text(content, encoding="utf-8", newline="\n")


def next_ticket_id(tickets_dir, prefix: str = "TK-") -> str:
    tickets_dir = Path(tickets_dir)
    best = 0
    if tickets_dir.exists():
        pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        for p in tickets_dir.glob(f"{prefix}*.md"):
            m = pat.match(p.stem)
            if m:
                best = max(best, int(m.group(1)))
    return f"{prefix}{best + 1:03d}"


def create_ticket(tickets_dir, owner: str, title: str, reviewer: str = None,
                   description: str = "", wait_for: str = "", now: datetime = None,
                   prefix: str = "TK-", status: str = "todo", executor: str = None,
                   kind: str = None) -> Path:
    tickets_dir = Path(tickets_dir)
    tickets_dir.mkdir(parents=True, exist_ok=True)
    tid = next_ticket_id(tickets_dir, prefix)
    lines = [f"id: {tid}", f"title: {title}", f"owner: {owner}", f"status: {status}"]
    if reviewer:
        lines.append(f"reviewer: {reviewer}")
    if executor:
        lines.append(f"executor: {executor}")
    if kind:
        lines.append(f"kind: {kind}")
    lines.append(f"wait_for: {wait_for}")
    lines.append(f"updated: {now_iso(now)}")
    text = "---\n" + "\n".join(lines) + "\n---\n\n"
    if description.strip():
        text += description.strip() + "\n\n"
    text += "## Лог\n"
    path = tickets_dir / f"{tid}.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def list_tickets(tickets_dir):
    tickets_dir = Path(tickets_dir)
    if not tickets_dir.exists():
        return []
    return sorted(tickets_dir.glob("*.md"))
