#!/usr/bin/env python3
"""kb — база знаний проекта (TK-629): карточки `kb/<тип>/<id>.md` в git → sqlite/FTS5, проверка ссылок, статический сайт.

Один файл, только стандартная библиотека Python 3.8+ (Windows / Linux / macOS). Настройки — `kb/kb.toml`; путей проекта в коде нет.
Команды: new · build · find · show · list · check · site · serve · precommit · install-hook. Описание — `kb/README.md`, перенос — `kb/PORTING.md`.
"""
import argparse
import datetime
import html
import http.server
import json
import os
import re
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from collections import namedtuple
from pathlib import Path
from urllib.parse import quote

DEFAULT_TYPES = ["decision", "task", "finding", "protocol", "runbook", "rule", "machine"]
DEFAULT_STATUSES = ["open", "running", "done", "dropped", "superseded"]
REQUIRED = ("id", "type", "status", "title", "date")
LIST_FIELDS = ("links", "supersedes")
DB_NAME = ".kb.sqlite"

Issue = namedtuple("Issue", "path level code msg")
Card = namedtuple("Card", "id type status title date links supersedes body path extra")
Edge = namedtuple("Edge", "src dst kind")


class KbError(Exception):
    pass


# ───────────────────────── разбор: нормализация, мини-TOML, front matter ─────────────────────────

def nfc(s):
    return unicodedata.normalize("NFC", s)


def fold(s):
    """Свёртка для поиска: регистр, NFC, ё = е (диакритику не трогаем: й ≠ и)."""
    return nfc(s).lower().replace("ё", "е")


def split_items(s):
    """Делит `a, "b, c", d` по запятым вне кавычек."""
    items, buf, q, esc = [], [], "", False
    for ch in s:
        if esc:
            buf.append(ch)
            esc = False
        elif q:
            buf.append(ch)
            if ch == "\\" and q == '"':
                esc = True
            elif ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def parse_value(v):
    """Скаляр (строка) или встроенный список `[a, b]`; общий для kb.toml и front matter."""
    v = v.strip()
    if v.startswith("["):
        if not v.endswith("]"):
            raise ValueError("список не закрыт ]")
        return [unquote(x) for x in split_items(v[1:-1])]
    return unquote(v)


def strip_toml_comment(line):
    q = ""
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
        elif ch == "#":
            return line[:i]
    return line


def parse_toml(text):
    """Подмножество TOML: `[секция]`, `ключ = "строка"`, `ключ = ["a", "b"]`, комментарии `#`."""
    out, cur = {}, None
    cur = out
    for n, raw in enumerate(text.lstrip("﻿").splitlines(), 1):
        line = strip_toml_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            cur = out.setdefault(line.strip("[] \t"), {})
            continue
        k, sep, v = line.partition("=")
        if not sep:
            raise KbError("kb.toml:%d: ожидалось «ключ = значение»" % n)
        try:
            cur[k.strip()] = parse_value(v)
        except ValueError as e:
            raise KbError("kb.toml:%d: %s" % (n, e))
    return out


_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_ITEM_RE = re.compile(r"^\s+-\s*(.*)$")


def parse_card_text(text):
    """→ (meta: dict, body: str). Front matter — между строками `---`; значения: скаляр, `[a, b]` или блок `- a`."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        raise ValueError("нет front matter: первая строка должна быть ---")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        raise ValueError("front matter не закрыт строкой ---")
    meta, block = {}, None
    for n, line in enumerate(lines[1:end], 2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ITEM_RE.match(line)
        if m and block is not None:
            meta[block].append(unquote(m.group(1)))
            continue
        m = _KEY_RE.match(line)
        if not m:
            raise ValueError("строка %d: ожидалось «ключ: значение»" % n)
        key, val = m.group(1), m.group(2)
        if key in meta:
            raise ValueError("строка %d: ключ %s повторён" % (n, key))
        if val.strip() == "":
            meta[key], block = [], key
        else:
            meta[key], block = parse_value(val), None
    return meta, "\n".join(lines[end + 1:]).strip("\n")


def strip_code(body):
    """Тело без блоков кода и `встроенного кода` — ссылки `[[…]]` внутри кода ссылками не считаются."""
    out, fence = [], False
    for line in body.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if not fence:
            out.append(re.sub(r"`[^`\n]*`", "", line))
    return "\n".join(out)


_INLINE_RE = re.compile(r"\[\[([^\[\]\n|]+?)\]\]")


def inline_links(body):
    seen, out = set(), []
    for t in _INLINE_RE.findall(strip_code(body)):
        t = nfc(t.strip())
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


_FILE_EXT_RE = re.compile(r"\.(md|json|py|rs|sh|toml|txt|csv|yml|yaml|html|js|ts|sql|service|timer|cfg|ini|lock)$", re.I)


def is_path(target):
    """Ссылка на файл репозитория: есть «/» или расширение известного типа (`AGENTS.md`); иначе — id карточки."""
    return "/" in target or bool(_FILE_EXT_RE.search(target))


# ───────────────────────── корень kb, конфиг, загрузка карточек ─────────────────────────

class Kb:
    def __init__(self, kb_dir):
        self.dir = Path(kb_dir).resolve()
        self.repo = self.dir.parent
        cfg_path = self.dir / "kb.toml"
        self.cfg = parse_toml(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        self.types = list(self.cfg.get("types") or DEFAULT_TYPES)
        self.statuses = list(self.cfg.get("statuses") or DEFAULT_STATUSES)
        self.title = self.cfg.get("title") or self.repo.name
        self.orphans = (self.cfg.get("check") or {}).get("orphans", "error")
        self.rel = self.dir.name

    @property
    def db_path(self):
        return self.dir / DB_NAME

    def card_files(self):
        out = []
        for d in sorted(self.dir.iterdir()):
            if d.is_dir() and not d.name.startswith((".", "_")):
                out += sorted(d.glob("*.md"))
        return out


def find_kb_dir(explicit=None):
    cand = explicit or os.environ.get("KB_DIR")
    if cand:
        return Path(cand)
    here = Path.cwd().resolve()
    for p in [here] + list(here.parents):
        if (p / "kb" / "kb.toml").exists():
            return p / "kb"
        if p.name == "kb" and (p / "kb.toml").exists():
            return p
    fallback = Path(__file__).resolve().parent.parent / "kb"
    if fallback.exists():
        return fallback
    raise KbError("не найден kb/kb.toml (запусти из репозитория или укажи --kb)")


def load_cards(kb):
    """→ (cards, issues): разобранные карточки и ошибки разбора/полей. Дубли id — первая берётся, остальные в issues."""
    cards, issues, by_id = [], [], {}
    for d in sorted(kb.dir.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")) and d.name not in kb.types:
            for f in sorted(d.glob("*.md")):
                issues.append(Issue(rel_path(kb, f), "error", "unknown-dir", "каталог «%s» не из списка типов %s" % (d.name, kb.types)))
    for f in kb.card_files():
        rel = rel_path(kb, f)
        if f.parent.name not in kb.types:
            continue
        try:
            meta, body = parse_card_text(f.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            issues.append(Issue(rel, "error", "front-matter", str(e)))
            continue
        card = make_card(kb, f, rel, meta, body, issues)
        if card is None:
            continue
        if card.id in by_id:
            issues.append(Issue(rel, "error", "duplicate-id", "id %s уже есть в %s" % (card.id, by_id[card.id].path)))
            continue
        by_id[card.id] = card
        cards.append(card)
    return cards, issues


def rel_path(kb, f):
    return f.relative_to(kb.repo).as_posix()


def make_card(kb, f, rel, meta, body, issues):
    def bad(code, msg):
        issues.append(Issue(rel, "error", code, msg))

    for k in REQUIRED:
        if k not in meta or meta[k] in ("", []):
            bad("front-matter", "нет обязательного поля «%s»" % k)
    if any(k not in meta or meta[k] in ("", []) for k in ("id", "type")):
        return None
    for k in REQUIRED:
        if isinstance(meta.get(k), list):
            bad("front-matter", "поле «%s» — не список" % k)
            return None
    cid, ctype = nfc(meta["id"]), meta["type"]
    if not re.match(r"^[\w.\-]+$", cid):
        bad("front-matter", "id «%s»: только буквы, цифры, «.», «-», «_»" % cid)
    if ctype not in kb.types:
        bad("front-matter", "тип «%s» не из списка %s" % (ctype, kb.types))
    status = meta.get("status", "")
    if status and status not in kb.statuses:
        bad("front-matter", "статус «%s» не из списка %s" % (status, kb.statuses))
    date = meta.get("date", "")
    if date:
        try:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                raise ValueError
            datetime.date.fromisoformat(date)
        except ValueError:
            bad("front-matter", "дата «%s» — нужен формат ГГГГ-ММ-ДД" % date)
    if nfc(f.stem) != cid or f.parent.name != ctype:
        bad("path-mismatch", "файл должен лежать в %s/%s/%s.md" % (kb.rel, ctype, cid))
    lists = {}
    for k in LIST_FIELDS:
        v = meta.get(k, [])
        lists[k] = [nfc(x.strip()) for x in (v if isinstance(v, list) else [v]) if x.strip()]
    extra = {k: v for k, v in meta.items() if k not in REQUIRED + LIST_FIELDS}
    return Card(cid, ctype, status, meta.get("title", ""), date, lists["links"], lists["supersedes"], body, rel, extra)


def edges_of(cards):
    seen, out = set(), []
    for c in cards:
        for kind, targets in (("links", c.links), ("supersedes", c.supersedes), ("inline", inline_links(c.body))):
            for t in targets:
                e = Edge(c.id, t, kind)
                if e not in seen:
                    seen.add(e)
                    out.append(e)
    return out


def resolves(kb, target, ids):
    if is_path(target):
        return (kb.repo / target.split("#")[0]).exists()
    return target in ids


# ───────────────────────── check ─────────────────────────

def check_cards(kb, cards, issues):
    """Добавляет к issues: битые ссылки, сироты, заменённые без замены; предупреждение — заменена, но статус не superseded."""
    issues = list(issues)
    ids = {c.id: c for c in cards}
    edges = edges_of(cards)
    for e in edges:
        if not resolves(kb, e.dst, ids):
            what = "файл" if is_path(e.dst) else "карточка"
            issues.append(Issue(ids[e.src].path, "error", "broken-link", "%s не найдена: %s (%s)" % (what, e.dst, e.kind)))
    replaced_by = {}
    for c in cards:
        for t in c.supersedes:
            replaced_by.setdefault(t, []).append(c.id)
    for c in cards:
        if c.status == "superseded" and c.id not in replaced_by:
            issues.append(Issue(c.path, "error", "no-replacement", "статус superseded, но ни одна карточка не указывает supersedes: [%s]" % c.id))
        if c.id in replaced_by and c.status != "superseded":
            issues.append(Issue(c.path, "warning", "stale-status", "заменена карточкой %s, но статус %s, не superseded" % (", ".join(replaced_by[c.id]), c.status)))
    linked = set()
    for e in edges:
        if e.dst in ids and e.src != e.dst:
            linked.add(e.src)
            linked.add(e.dst)
    level = "error" if kb.orphans == "error" else "warning"
    for c in cards:
        if c.id not in linked:
            issues.append(Issue(c.path, level, "orphan", "нет ни исходящих, ни входящих ссылок на другие карточки"))
    return issues


def format_issues(issues):
    return ["%s: %s [%s] %s" % (i.path, "ошибка" if i.level == "error" else "предупреждение", i.code, i.msg)
            for i in sorted(issues, key=lambda i: (i.path, i.code, i.msg))]


# ───────────────────────── sqlite / FTS5 ─────────────────────────

SCHEMA = """
CREATE TABLE cards(id TEXT PRIMARY KEY, type TEXT, status TEXT, title TEXT, date TEXT, path TEXT, body TEXT, extra TEXT);
CREATE TABLE links(src TEXT, dst TEXT, kind TEXT, is_path INTEGER, ok INTEGER);
CREATE INDEX links_dst ON links(dst);
CREATE INDEX links_src ON links(src);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def build_db(kb, cards):
    tmp = kb.db_path.with_name(DB_NAME + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(str(tmp))
    conn.executescript(SCHEMA)
    try:
        conn.execute("CREATE VIRTUAL TABLE fts USING fts5(id, title, body, tokenize='unicode61 remove_diacritics 0')")
        fts = 1
    except sqlite3.OperationalError:
        fts = 0
    ids = {c.id for c in cards}
    for c in cards:
        cur = conn.execute("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?)",
                           (c.id, c.type, c.status, c.title, c.date, c.path, c.body, json.dumps(c.extra, ensure_ascii=False)))
        if fts:
            conn.execute("INSERT INTO fts(rowid, id, title, body) VALUES (?,?,?,?)", (cur.lastrowid, fold(c.id), fold(c.title), fold(c.body)))
    for e in edges_of(cards):
        conn.execute("INSERT INTO links VALUES (?,?,?,?,?)", (e.src, e.dst, e.kind, int(is_path(e.dst)), int(resolves(kb, e.dst, ids))))
    n_files = len(kb.card_files())
    conn.executemany("INSERT INTO meta VALUES (?,?)", [("fts", str(fts)), ("files", str(n_files))])
    conn.commit()
    conn.close()
    os.replace(str(tmp), str(kb.db_path))
    return len(cards)


def ensure_db(kb):
    """Пересобирает `.kb.sqlite`, если его нет или карточки новее/другого числа."""
    files = kb.card_files()
    stale = not kb.db_path.exists()
    if not stale:
        newest = max([f.stat().st_mtime for f in files] + [(kb.dir / "kb.toml").stat().st_mtime if (kb.dir / "kb.toml").exists() else 0])
        conn = sqlite3.connect(str(kb.db_path))
        try:
            n = conn.execute("SELECT value FROM meta WHERE key='files'").fetchone()
        finally:
            conn.close()
        stale = newest > kb.db_path.stat().st_mtime or n is None or int(n[0]) != len(files)
    if stale:
        cards, _ = load_cards(kb)
        build_db(kb, cards)
    conn = sqlite3.connect(str(kb.db_path))
    conn.row_factory = sqlite3.Row
    return conn


def search(conn, q, limit=20):
    """→ список строк cards; id-совпадение первым, дальше bm25 (id×8, заголовок×4, текст×1) или счёт вхождений без FTS5."""
    toks = fold(q).split()
    if not toks:
        return []
    exact = conn.execute("SELECT * FROM cards WHERE lower(id)=?", (fold(q.strip()),)).fetchall()
    has_fts = conn.execute("SELECT value FROM meta WHERE key='fts'").fetchone()[0] == "1"
    if has_fts:
        match = " ".join('"%s"*' % t.replace('"', '""') for t in toks)
        rows = conn.execute("SELECT c.* FROM fts JOIN cards c ON c.rowid = fts.rowid WHERE fts MATCH ? ORDER BY bm25(fts, 8.0, 4.0, 1.0) LIMIT ?",
                            (match, limit)).fetchall()
    else:
        rows = []
        for r in conn.execute("SELECT * FROM cards"):
            hay = fold(r["id"] + " " + r["title"] + " " + r["body"])
            if all(t in hay for t in toks):
                rows.append((sum(hay.count(t) for t in toks), r))
        rows = [r for _, r in sorted(rows, key=lambda x: -x[0])][:limit]
    seen, out = set(), []
    for r in list(exact) + list(rows):
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out[:limit]


def snippet(body, q, width=110):
    flat = re.sub(r"\s+", " ", body)
    folded = fold(flat)
    for t in fold(q).split():
        i = folded.find(t)
        if i >= 0 and len(folded) == len(flat):
            a = max(0, i - 40)
            return ("…" if a else "") + flat[a:a + width] + ("…" if a + width < len(flat) else "")
    return flat[:width] + ("…" if len(flat) > width else "")


# ───────────────────────── команды ─────────────────────────

def cmd_new(kb, a):
    if a.type not in kb.types:
        raise KbError("тип «%s» не из списка %s" % (a.type, kb.types))
    cid = nfc(a.id)
    if not re.match(r"^[\w.\-]+$", cid):
        raise KbError("id «%s»: только буквы, цифры, «.», «-», «_»" % cid)
    cards, _ = load_cards(kb)
    for c in cards:
        if c.id == cid:
            raise KbError("id %s уже есть: %s" % (cid, c.path))
    path = kb.dir / a.type / (cid + ".md")
    tpl_dir = kb.dir / "_templates"
    tpl = next((p for p in (tpl_dir / (a.type + ".md"), tpl_dir / "default.md") if p.exists()), None)
    head = "---\nid: %s\ntype: %s\nstatus: open\ntitle: %s\ndate: %s\nlinks: []\nsupersedes: []\n---\n\n" % (
        cid, a.type, json.dumps(a.title, ensure_ascii=False), datetime.date.today().isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + (tpl.read_text(encoding="utf-8") if tpl else ""), encoding="utf-8", newline="\n")
    print(rel_path(kb, path))
    return 0


def cmd_build(kb, a):
    cards, issues = load_cards(kb)
    n = build_db(kb, cards)
    conn = sqlite3.connect(str(kb.db_path))
    links = conn.execute("SELECT count(*) FROM links").fetchone()[0]
    conn.close()
    print("kb: %d карточек, %d ссылок → %s%s" % (n, links, rel_path(kb, kb.db_path), ("; проблем при разборе: %d (kb check)" % len(issues)) if issues else ""))
    return 0


def cmd_find(kb, a):
    conn = ensure_db(kb)
    rows = search(conn, a.query, a.n)
    conn.close()
    for r in rows:
        print("%-12s %-9s %-10s %s" % (r["id"], r["type"], r["status"], r["title"]))
        print("    " + snippet(r["body"], a.query))
    if not rows:
        print("ничего не найдено")
    return 0


def link_rows(conn, cid):
    out = conn.execute("SELECT dst, kind, is_path, ok FROM links WHERE src=? ORDER BY kind, dst", (cid,)).fetchall()
    inn = conn.execute("SELECT src, kind FROM links WHERE dst=? AND src<>? ORDER BY kind, src", (cid, cid)).fetchall()
    return out, inn


def cmd_show(kb, a):
    conn = ensure_db(kb)
    try:
        return show_card(conn, a)
    finally:
        conn.close()


def show_card(conn, a):
    r = conn.execute("SELECT * FROM cards WHERE id=?", (nfc(a.id),)).fetchone()
    if not r:
        raise KbError("карточки %s нет" % a.id)
    print("%s  [%s · %s]  %s\n%s\n%s\n" % (r["id"], r["type"], r["status"], r["date"], r["title"], r["path"]))
    print(r["body"].strip("\n") + "\n")
    out, inn = link_rows(conn, r["id"])

    def describe(i):
        c = conn.execute("SELECT type, status, title FROM cards WHERE id=?", (i,)).fetchone()
        return "%s · %s  %s" % (c["type"], c["status"], c["title"]) if c else "нет карточки"

    print("Ссылки из карточки:")
    for dst, kind, path, ok in out:
        print("  → %-14s (%s) %s" % (dst, kind, "файл" + ("" if ok else " — НЕ НАЙДЕН") if path else describe(dst)))
    if not out:
        print("  —")
    print("Ссылки на карточку:")
    for src, kind in inn:
        print("  ← %-14s (%s) %s" % (src, kind, describe(src)))
    if not inn:
        print("  —")
    return 0


def cmd_list(kb, a):
    conn = ensure_db(kb)
    try:
        return list_cards(conn, a)
    finally:
        conn.close()


def list_cards(conn, a):
    sql, args = "SELECT * FROM cards WHERE 1=1", []
    if a.type:
        sql += " AND type=?"
        args.append(a.type)
    if a.status:
        sql += " AND status=?"
        args.append(a.status)
    if a.link:
        t = nfc(a.link)
        sql += " AND (id IN (SELECT src FROM links WHERE dst=?) OR id IN (SELECT dst FROM links WHERE src=?))"
        args += [t, t]
    for r in conn.execute(sql + " ORDER BY date DESC, id", args):
        print("%-12s %-9s %-10s %s  %s" % (r["id"], r["type"], r["status"], r["date"], r["title"]))
    return 0


def cmd_check(kb, a):
    cards, issues = load_cards(kb)
    issues = check_cards(kb, cards, issues)
    for line in format_issues(issues):
        print(line)
    errors = sum(1 for i in issues if i.level == "error")
    warns = len(issues) - errors
    n_edges = len(edges_of(cards))
    print("kb check: %d карточек, %d ссылок, ошибок %d, предупреждений %d" % (len(cards), n_edges, errors, warns))
    return 1 if errors else 0


def git_lines(repo, *args):
    r = subprocess.run(["git", "-C", str(repo)] + list(args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return [x.decode("utf-8", "replace") for x in r.stdout.split(b"\0") if x] if r.returncode == 0 else []


def cmd_precommit(kb, a):
    """Для git-хука и хука Claude: `kb check` только если в коммит попал kb/ (или, с --changed, есть любые правки kb/)."""
    if a.changed:
        names = [x[3:] for x in git_lines(kb.repo, "status", "--porcelain", "-z", "--", kb.rel)]
    else:
        names = git_lines(kb.repo, "diff", "--cached", "--name-only", "-z", "--", kb.rel)
    if not names:
        return 0
    return cmd_check(kb, a)


def cmd_install_hook(kb, a):
    hooks = git_lines(kb.repo, "rev-parse", "--git-path", "hooks")
    if not hooks:
        raise KbError("не git-репозиторий")
    hook = (kb.repo / hooks[0].strip()).resolve()
    hook.mkdir(parents=True, exist_ok=True)
    f = hook / "pre-commit"
    me = os.path.relpath(str(Path(__file__).resolve()), str(kb.repo)).replace(os.sep, "/")
    block = ('# kb-hook: проверка карточек, только если в коммит попал kb/\n'
             'PY=$(command -v python3 || command -v python) && "$PY" "$(git rev-parse --show-toplevel)/%s" precommit || '
             '{ [ -z "$PY" ] || exit 1; }\n' % me)
    cur = f.read_text(encoding="utf-8") if f.exists() else "#!/bin/sh\n"
    if "# kb-hook" in cur:
        print("хук уже установлен: %s" % f)
        return 0
    f.write_text(cur.rstrip("\n") + "\n" + block, encoding="utf-8", newline="\n")
    try:
        f.chmod(f.stat().st_mode | 0o111)
    except OSError:
        pass
    print("хук установлен: %s" % f)
    return 0


def cmd_site(kb, a):
    n = build_site(kb, Path(a.outdir))
    print("kb: сайт %d карточек → %s" % (n, a.outdir))
    return 0


def cmd_serve(kb, a):
    out = Path(tempfile.mkdtemp(prefix="kb-site-"))
    build_site(kb, out)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(out), **kw)

        def log_message(self, *args):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((a.host, a.port), H) as srv:
        print("kb: http://%s:%d/  (Ctrl+C — стоп)" % (a.host, a.port))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


# ───────────────────────── сайт: Markdown → HTML ─────────────────────────

def md_inline(text, link_for):
    """Встроенная разметка: `код`, **жирный**, *курсив*, [текст](url), [[id]], голые http(s)-ссылки. `link_for(target)` → HTML ссылки."""
    keep = []

    def stash(s):
        keep.append(s)
        return "\x00%d\x00" % (len(keep) - 1)

    text = re.sub(r"`([^`\n]+)`", lambda m: stash("<code>%s</code>" % html.escape(m.group(1))), text)
    text = re.sub(r"\[\[([^\[\]\n|]+?)\]\]", lambda m: stash(link_for(nfc(m.group(1).strip()))), text)

    def md_link(m):
        url = m.group(2).strip()
        if re.match(r"^\s*(javascript|data|vbscript):", url, re.I):
            return html.escape(m.group(0))
        return stash('<a href="%s">%s</a>' % (html.escape(url, quote=True), md_inline(m.group(1), link_for)))

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", md_link, text)
    text = re.sub(r"(?<![\"'=(])\bhttps?://[^\s<>)\]]+", lambda m: stash('<a href="%s">%s</a>' % (html.escape(m.group(0), quote=True), html.escape(m.group(0)))), text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: keep[int(m.group(1))], text)


_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def md_to_html(body, link_for):
    """Блоки: заголовки (# → h2), абзацы, списки (вложенность по отступу), таблицы, цитаты, код, ---."""
    lines = body.replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    inl = lambda s: md_inline(s, link_for)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^(\s*)(```|~~~)\s*([\w+-]*)\s*$", line)
        if m:
            fence, buf = m.group(2), []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(buf)))
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            lvl = max(2, len(m.group(1)))   # h1 — заголовок страницы; `#` и `##` в теле — h2
            out.append("<h%d>%s</h%d>" % (lvl, inl(m.group(2)), lvl))
            i += 1
            continue
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]) and "|" in lines[i + 1]:
            head = split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(split_row(lines[i]))
                i += 1
            t = "<div class=\"tw\"><table><thead><tr>%s</tr></thead><tbody>" % "".join("<th>%s</th>" % inl(c) for c in head)
            for r in rows:
                r = (r + [""] * len(head))[:len(head)]
                t += "<tr>%s</tr>" % "".join("<td>%s</td>" % inl(c) for c in r)
            out.append(t + "</tbody></table></div>")
            continue
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % md_to_html("\n".join(buf), link_for))
            continue
        if _LIST_RE.match(line):
            i, h = _md_list(lines, i, inl)
            out.append(h)
            continue
        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\s|\s*(```|~~~)|\s*>)", lines[i]) and not _LIST_RE.match(lines[i]) \
                and not ("|" in lines[i] and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1])):
            buf.append(lines[i].strip())
            i += 1
        if not buf:
            buf, i = [lines[i].strip()], i + 1
        out.append("<p>%s</p>" % inl(" ".join(buf)))
    return "\n".join(out)


def _md_list(lines, i, inl):
    base = len(_LIST_RE.match(lines[i]).group(1).expandtabs(4))
    ordered = bool(re.match(r"\d", _LIST_RE.match(lines[i]).group(2)))
    tag = "ol" if ordered else "ul"
    items, n = [], len(lines)
    while i < n:
        m = _LIST_RE.match(lines[i])
        if not m:
            if lines[i].strip() and lines[i].startswith("  ") and items:
                items[-1][0] += " " + lines[i].strip()
                i += 1
                continue
            break
        indent = len(m.group(1).expandtabs(4))
        if indent < base:
            break
        if indent > base and items:
            i, sub = _md_list(lines, i, inl)
            items[-1][1] += sub
            continue
        items.append([m.group(3), ""])
        i += 1
    return i, "<%s>%s</%s>" % (tag, "".join("<li>%s%s</li>" % (inl(t), sub) for t, sub in items), tag)


# ───────────────────────── сайт: страницы ─────────────────────────

CSS = r"""
:root{--bg:#fff;--fg:#1d2433;--mut:#6b7385;--line:#e3e6ee;--acc:#1f5fbf;--soft:#f4f6fb;--ok:#1d7a4a;--warn:#a15c00;--bad:#b3261e}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
header.top{display:flex;align-items:center;gap:20px;padding:10px 20px;border-bottom:1px solid var(--line);background:var(--soft);flex-wrap:wrap}
header.top b{font-size:16px}header.top nav{display:flex;gap:16px}header.top nav a.cur{font-weight:600;color:var(--fg)}
main{max-width:980px;margin:0 auto;padding:18px 20px 60px}
h1{font-size:24px;line-height:1.25;margin:.3em 0 .5em}h2{font-size:19px;margin:1.6em 0 .5em}h3{font-size:16px;margin:1.3em 0 .4em}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;color:var(--mut);font-size:13px;margin:0}
.badge{display:inline-block;padding:0 8px;border:1px solid var(--line);border-radius:10px;font-size:12px;line-height:20px;background:#fff;color:var(--fg);white-space:nowrap}
.st-done{color:var(--ok);border-color:#bfe0cd}.st-running{color:var(--acc);border-color:#bcd0f0}.st-open{color:var(--warn);border-color:#ecd3a8}
.st-dropped,.st-superseded{color:var(--mut)}
code{background:var(--soft);padding:1px 5px;border-radius:4px;font:13px ui-monospace,Menlo,Consolas,monospace;word-break:break-word}
pre{background:var(--soft);padding:10px 12px;border-radius:6px;overflow:auto}pre code{padding:0;background:none}
blockquote{margin:.8em 0;padding:2px 14px;border-left:3px solid var(--line);color:var(--mut)}
.tw{overflow-x:auto;margin:.8em 0}table{border-collapse:collapse;font-size:14px}th,td{border:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}th{background:var(--soft)}
.broken{color:var(--bad);border-bottom:1px dashed var(--bad)}
ul.links{list-style:none;padding:0;margin:0}ul.links li{padding:6px 0;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
ul.links .k{color:var(--mut);font-size:12px}
.ctl{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 6px}
.ctl input,.ctl select{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--fg);min-width:0}
.ctl input{flex:1 1 280px}.ctl select{flex:0 1 170px}
#cnt{color:var(--mut);font-size:13px;margin:0 0 6px}
.row{display:grid;grid-template-columns:210px 1fr auto;gap:2px 12px;padding:9px 0;border-bottom:1px solid var(--line);align-items:baseline}
.row .id{font-weight:600}.row .t{grid-column:2}.row .d{color:var(--mut);font-size:13px;text-align:right}.row .b{display:flex;gap:6px;flex-wrap:wrap}
#graph{width:100%;height:72vh;border:1px solid var(--line);border-radius:6px;background:#fff}
#graph .e{stroke:#c3c9d8;stroke-width:1}#graph .n circle{stroke:#fff;stroke-width:1.5}#graph text{font-size:11px;fill:var(--fg);paint-order:stroke;stroke:#fff;stroke-width:3px}
#graph .dim{opacity:.12}#graph .foc circle{stroke:var(--acc);stroke-width:3}#graph .n{cursor:pointer}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:var(--mut);margin:6px 0}.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
@media(max-width:640px){main{padding:14px 14px 50px}header.top{padding:8px 14px;gap:12px}h1{font-size:21px}
.row{grid-template-columns:1fr auto}.row .t{grid-column:1/-1;order:2}.row .d{grid-column:1/-1;text-align:left;order:3}.ctl select{flex:1 1 40%}#graph{height:66vh}}
"""

TYPE_COLORS = {"decision": "#c2410c", "task": "#1f5fbf", "finding": "#0f766e", "protocol": "#7c3aed", "runbook": "#a16207", "rule": "#b91c1c", "machine": "#4b5563"}

APP_JS = r"""
(function(){
var D=window.KB,cards=D.cards,byId={};cards.forEach(function(c){byId[c.id]=c;});
var $=function(i){return document.getElementById(i);};
function fold(s){return s.toLowerCase().replace(/ё/g,'е');}
function opt(sel,vals,first){sel.innerHTML='<option value="">'+first+'</option>'+vals.map(function(v){return '<option>'+v.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</option>';}).join('');}
opt($('type'),D.types,'все типы');opt($('status'),D.statuses,'все статусы');
opt($('task'),cards.filter(function(c){return c.type==='task';}).map(function(c){return c.id;}),'любая задача');
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
function readHash(){var o={};location.hash.replace(/^#/,'').split('&').forEach(function(p){var k=p.split('=');if(k[0])o[k[0]]=decodeURIComponent(k.slice(1).join('=')||'');});return o;}
function writeHash(){var p=[];['q','type','status','task'].forEach(function(k){var v=$(k).value;if(v)p.push(k+'='+encodeURIComponent(v));});history.replaceState(null,'','#'+p.join('&'));}
function match(c,f){
 if(f.type&&c.type!==f.type)return -1;if(f.status&&c.status!==f.status)return -1;
 if(f.task&&!(c.id===f.task||c.out.indexOf(f.task)>=0||c.inn.indexOf(f.task)>=0))return -1;
 if(!f.toks.length)return 0;var s=0,id=fold(c.id),ti=fold(c.title);
 for(var i=0;i<f.toks.length;i++){var t=f.toks[i],h=0;if(id===t)h+=100;else if(id.indexOf(t)>=0)h+=20;if(ti.indexOf(t)>=0)h+=10;if(c.text.indexOf(t)>=0)h+=1;if(!h)return -1;s+=h;}
 return s;}
function render(){
 var f={q:$('q').value,type:$('type').value,status:$('status').value,task:$('task').value};f.toks=fold(f.q).split(/\s+/).filter(Boolean);
 var r=[];cards.forEach(function(c){var s=match(c,f);if(s>=0)r.push([s,c]);});
 r.sort(function(a,b){return b[0]-a[0]||(a[1].date<b[1].date?1:a[1].date>b[1].date?-1:0)||(a[1].id<b[1].id?-1:1);});
 $('cnt').textContent=r.length+' из '+cards.length;
 $('list').innerHTML=r.map(function(x){var c=x[1];return '<div class="row"><a class="id" href="'+c.href+'">'+esc(c.id)+'</a><span class="b"><span class="badge">'+esc(c.type)+'</span><span class="badge st-'+esc(c.status)+'">'+esc(c.status)+'</span></span><span class="d">'+esc(c.date)+'</span><span class="t">'+esc(c.title)+'</span></div>';}).join('')||'<p>Ничего не найдено.</p>';
 writeHash();}
function fromHash(){var h=readHash();$('q').value=h.q||'';$('type').value=h.type||'';$('status').value=h.status||'';$('task').value=h.task||'';render();}
['q','type','status','task'].forEach(function(k){$(k).addEventListener(k==='q'?'input':'change',render);});
window.addEventListener('hashchange',fromHash);
fromHash();
})();
"""

GRAPH_JS = r"""
(function(){
var D=window.KB,cards=D.cards,idx={},nodes=[],edges=[],seen={};
cards.forEach(function(c){idx[c.id]=nodes.length;nodes.push({c:c,x:0,y:0,vx:0,vy:0,deg:0});});
cards.forEach(function(c){c.out.forEach(function(t){if(idx[t]===undefined||t===c.id)return;var k=idx[c.id]<idx[t]?c.id+'\u0000'+t:t+'\u0000'+c.id;if(seen[k])return;seen[k]=1;edges.push([idx[c.id],idx[t]]);nodes[idx[c.id]].deg++;nodes[idx[t]].deg++;});});
var seed=7;function rnd(){seed=(seed+0x6D2B79F5)|0;var t=Math.imul(seed^(seed>>>15),1|seed);t=(t+Math.imul(t^(t>>>7),61|t))^t;return((t^(t>>>14))>>>0)/4294967296;}
var n=nodes.length,R=Math.max(120,n*12);
nodes.forEach(function(p,i){var a=2*Math.PI*i/Math.max(n,1);p.x=Math.cos(a)*R*(0.5+rnd()*0.5);p.y=Math.sin(a)*R*(0.5+rnd()*0.5);});
var K=70;
for(var it=0;it<400;it++){var T=1-it/400;
 for(var i=0;i<n;i++)for(var j=i+1;j<n;j++){var dx=nodes[i].x-nodes[j].x,dy=nodes[i].y-nodes[j].y,d2=dx*dx+dy*dy+0.01,d=Math.sqrt(d2),f=K*K/d2;dx*=f;dy*=f;nodes[i].vx+=dx;nodes[i].vy+=dy;nodes[j].vx-=dx;nodes[j].vy-=dy;}
 edges.forEach(function(e){var a=nodes[e[0]],b=nodes[e[1]],dx=a.x-b.x,dy=a.y-b.y,d=Math.sqrt(dx*dx+dy*dy)+0.01,f=(d-K)/K*0.6;dx*=f;dy*=f;a.vx-=dx;a.vy-=dy;b.vx+=dx;b.vy+=dy;});
 nodes.forEach(function(p){p.vx-=p.x*0.02;p.vy-=p.y*0.02;var l=Math.sqrt(p.vx*p.vx+p.vy*p.vy)+0.01,m=Math.min(l,30*T+1);p.x+=p.vx/l*m;p.y+=p.vy/l*m;p.vx=0;p.vy=0;});}
var x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;nodes.forEach(function(p){x0=Math.min(x0,p.x);y0=Math.min(y0,p.y);x1=Math.max(x1,p.x);y1=Math.max(y1,p.y);});
var pad=70;if(!n){x0=y0=0;x1=y1=100;}
var svg=document.getElementById('graph'),NS='http://www.w3.org/2000/svg';
svg.setAttribute('viewBox',(x0-pad)+' '+(y0-pad)+' '+(x1-x0+2*pad)+' '+(y1-y0+2*pad));
function el(t,a,p){var e=document.createElementNS(NS,t);for(var k in a)e.setAttribute(k,a[k]);p.appendChild(e);return e;}
var ge=el('g',{},svg),gn=el('g',{},svg),lines=[],gs=[];
edges.forEach(function(e){lines.push(el('line',{'class':'e',x1:nodes[e[0]].x,y1:nodes[e[0]].y,x2:nodes[e[1]].x,y2:nodes[e[1]].y},ge));});
nodes.forEach(function(p,i){var g=el('g',{'class':'n','data-id':p.c.id,transform:'translate('+p.x+','+p.y+')'},gn);
 el('circle',{r:5+Math.min(p.deg,10),fill:D.colors[p.c.type]||'#888'},g);var t=el('text',{x:10+Math.min(p.deg,10),y:4},g);t.textContent=p.c.id;
 var tt=el('title',{},g);tt.textContent=p.c.id+' — '+p.c.title;
 g.addEventListener('click',function(){location.href=p.c.href;});
 g.addEventListener('mouseenter',function(){focus(p.c.id);});g.addEventListener('mouseleave',function(){focus(location.hash.slice(1)?decodeURIComponent(location.hash.slice(1)):'');});gs.push(g);});
function focus(id){var keep={};if(id&&idx[id]!==undefined){keep[idx[id]]=1;edges.forEach(function(e){if(e[0]===idx[id])keep[e[1]]=1;if(e[1]===idx[id])keep[e[0]]=1;});}
 gs.forEach(function(g,i){g.setAttribute('class','n'+(id&&idx[id]!==undefined?(keep[i]?(i===idx[id]?' foc':''):' dim'):''));});
 lines.forEach(function(l,i){var e=edges[i];l.setAttribute('class','e'+(id&&idx[id]!==undefined&&e[0]!==idx[id]&&e[1]!==idx[id]?' dim':''));});}
focus(location.hash.slice(1)?decodeURIComponent(location.hash.slice(1)):'');
var lg=document.getElementById('legend');lg.innerHTML=D.types.map(function(t){return '<span><i style="background:'+D.colors[t]+'"></i>'+t+'</span>';}).join('');
})();
"""


def page(kb, title, body, root, cur, scripts=()):
    nav = "".join('<a href="%s%s"%s>%s</a>' % (root, href, ' class="cur"' if key == cur else "", label)
                  for key, href, label in (("cards", "index.html", "Карточки"), ("graph", "graph.html", "Граф")))
    sc = "".join('<script src="%s%s"></script>' % (root, s) for s in scripts)
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">'
            '<title>%s</title><link rel="stylesheet" href="%sstyle.css"></head><body>'
            '<header class="top"><b>%s</b><nav>%s</nav></header><main>%s</main>%s</body></html>'
            % (html.escape(title), root, html.escape(kb.title), nav, body, sc))


def card_href(cid):
    return "c/%s.html" % quote(cid)


def badges(c):
    return '<span class="badge">%s</span> <span class="badge st-%s">%s</span>' % (html.escape(c.type), html.escape(c.status), html.escape(c.status))


def build_site(kb, outdir):
    cards, _ = load_cards(kb)
    ids = {c.id: c for c in cards}
    edges = edges_of(cards)
    out_of, in_of = {c.id: [] for c in cards}, {c.id: [] for c in cards}
    for e in edges:
        if e.src in out_of:
            out_of[e.src].append(e)
        if e.dst in in_of and e.src != e.dst:
            in_of[e.dst].append(e)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "c").mkdir(exist_ok=True)
    for old in (outdir / "c").glob("*.html"):
        old.unlink()
    (outdir / "style.css").write_text(CSS, encoding="utf-8", newline="\n")
    (outdir / "app.js").write_text(APP_JS, encoding="utf-8", newline="\n")
    (outdir / "graph.js").write_text(GRAPH_JS, encoding="utf-8", newline="\n")
    data = {
        "types": kb.types, "statuses": kb.statuses,
        "colors": {t: TYPE_COLORS.get(t, "#6b7385") for t in kb.types},
        "cards": [{"id": c.id, "type": c.type, "status": c.status, "title": c.title, "date": c.date, "href": card_href(c.id),
                   "out": sorted({e.dst for e in out_of[c.id] if e.dst in ids}), "inn": sorted({e.src for e in in_of[c.id]}),
                   "text": fold(c.body)[:20000]} for c in cards],
    }
    (outdir / "data.js").write_text("window.KB=%s;\n" % json.dumps(data, ensure_ascii=True), encoding="utf-8", newline="\n")

    def link_for(t):
        """Ссылка `[[…]]` в теле карточки (страница лежит в c/, соседи — рядом)."""
        if is_path(t):
            return '<code>%s</code>' % html.escape(t) if resolves(kb, t, ids) else '<span class="broken">%s</span>' % html.escape(t)
        if t in ids:
            return '<a href="%s.html">%s</a>' % (quote(t), html.escape(t))
        return '<span class="broken">%s</span>' % html.escape(t)

    ctl = ('<div class="ctl"><input id="q" type="search" placeholder="поиск: id, заголовок, текст" autocomplete="off">'
           '<select id="type"></select><select id="status"></select><select id="task"></select></div>')
    index_body = "<h1>Карточки</h1>%s<p id=\"cnt\"></p><div id=\"list\"></div>" % ctl
    (outdir / "index.html").write_text(page(kb, kb.title, index_body, "", "cards", ("data.js", "app.js")), encoding="utf-8", newline="\n")
    graph_body = ('<h1>Граф ссылок</h1><div class="legend" id="legend"></div>'
                  '<svg id="graph" role="img" aria-label="граф ссылок между карточками"></svg>')
    (outdir / "graph.html").write_text(page(kb, kb.title + " · граф", graph_body, "", "graph", ("data.js", "graph.js")), encoding="utf-8", newline="\n")

    kind_out = {"links": "", "supersedes": "заменяет", "inline": "в тексте"}
    kind_in = {"links": "", "supersedes": "заменяет эту карточку", "inline": "в тексте"}

    def li(other, label):
        if is_path(other):
            ok = resolves(kb, other, ids)
            return '<li><code>%s</code><span class="k">файл%s %s</span></li>' % (html.escape(other), "" if ok else " — не найден", label)
        o = ids.get(other)
        if not o:
            return '<li><span class="broken">%s</span><span class="k">нет карточки</span></li>' % html.escape(other)
        return '<li><a href="%s">%s</a> %s<span>%s</span><span class="k">%s</span></li>' % (
            quote(other) + ".html", html.escape(other), badges(o), html.escape(o.title), label)

    def li_list(edge_list, pick, names):
        by = {}
        for e in edge_list:
            by.setdefault(pick(e), []).append(names[e.kind])
        return "".join(li(t, ", ".join(sorted({x for x in ks if x}))) for t, ks in sorted(by.items()))

    for c in cards:
        outs = li_list(out_of[c.id], lambda e: e.dst, kind_out)
        ins = li_list(in_of[c.id], lambda e: e.src, kind_in)
        replaced = [e.src for e in in_of[c.id] if e.kind == "supersedes"]
        meta = '<p class="meta">%s <span>%s</span><span><code>%s</code></span>%s</p>' % (
            badges(c), html.escape(c.date), html.escape(c.path),
            ('<span>заменена: %s</span>' % ", ".join('<a href="%s">%s</a>' % (quote(r) + ".html", html.escape(r)) for r in replaced)) if replaced else "")
        body = ("%s<h1>%s <span style=\"color:var(--mut);font-weight:400\">· %s</span></h1>"
                "<article class=\"body\">%s</article>"
                "<h2>Ссылки</h2>%s<h2>Обратные ссылки</h2>%s<p><a href=\"%sgraph.html#%s\">на графе</a></p>") % (
            meta, html.escape(c.id), html.escape(c.title),
            md_to_html(c.body, link_for),
            ('<ul class="links">%s</ul>' % outs) if outs else "<p>—</p>",
            ('<ul class="links">%s</ul>' % ins) if ins else "<p>—</p>",
            "../", quote(c.id))
        (outdir / "c" / (c.id + ".html")).write_text(page(kb, c.id + " — " + c.title, body, "../", "cards"), encoding="utf-8", newline="\n")
    return len(cards)


# ───────────────────────── main ─────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(prog="kb", description="база знаний проекта: карточки Markdown в git")
    p.add_argument("--kb", help="каталог kb (по умолчанию — ближайший kb/kb.toml вверх от текущего, или $KB_DIR)")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("new", help="создать карточку из шаблона")
    s.add_argument("type")
    s.add_argument("id")
    s.add_argument("title")
    s.set_defaults(fn=cmd_new)
    sp.add_parser("build", help="собрать kb/.kb.sqlite (карточки, ссылки, FTS5)").set_defaults(fn=cmd_build)
    s = sp.add_parser("find", help="полнотекстовый поиск")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=20)
    s.set_defaults(fn=cmd_find)
    s = sp.add_parser("show", help="карточка + ссылки из неё и на неё")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    s = sp.add_parser("list", help="список карточек")
    s.add_argument("--type")
    s.add_argument("--status")
    s.add_argument("--link", help="только карточки, связанные с этим id (в любую сторону)")
    s.set_defaults(fn=cmd_list)
    sp.add_parser("check", help="битые ссылки, сироты, дубли, front matter; код 1 при ошибках").set_defaults(fn=cmd_check)
    s = sp.add_parser("site", help="статический сайт в каталог")
    s.add_argument("outdir")
    s.set_defaults(fn=cmd_site)
    s = sp.add_parser("serve", help="локальный http-сервер с сайтом")
    s.add_argument("--port", type=int, default=8095)
    s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(fn=cmd_serve)
    s = sp.add_parser("precommit", help="check, только если в коммит попал kb/")
    s.add_argument("--changed", action="store_true", help="смотреть любые правки kb/ (не только staged)")
    s.set_defaults(fn=cmd_precommit)
    sp.add_parser("install-hook", help="поставить git pre-commit, вызывающий precommit").set_defaults(fn=cmd_install_hook)
    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        return a.fn(Kb(find_kb_dir(a.kb)), a)
    except KbError as e:
        print("kb: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
