#!/usr/bin/env python3
"""chron — летопись выводов и протоколов (TK-646): одна SQLite на srv (`/data/chron/chron.sqlite`) — источник правды.

Записи (вердикты, предрегистрации, поправки, замеры), разделы протоколов и типизированные ссылки между ними,
задачами, решениями, коммитами, файлами и карточками kb. Записи неизменяемы (триггеры БД), раздел протокола
«до счёта» неизменяем; исправление — новая строка со ссылкой на старую.

Работает отовсюду: на srv — прямо с БД; с ПК и из worktree — тот же файл исполняется на srv по ssh
(ключ `-i`, `UserKnownHostsFile` — кириллица в HOME), локально делается только чтение файлов и проверка git.
Один файл, только стандартная библиотека Python 3.8+. Описание — `docs/research/CHRONICLE.md`.
"""
import argparse
import base64
import datetime
import hashlib
import html
import http.server
import json
import os
import re
import socketserver
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

SRV_DB = "/data/chron/chron.sqlite"
SRV_HOST = "root@5.231.25.224"
TZ = datetime.timezone(datetime.timedelta(hours=4))
VERDICTS = ("работает", "не работает", "не ясно", "описательно")
METRIC_COLS = ("side", "cell", "uni", "win", "trades", "usd", "mean_usd", "sharpe", "verdict", "same_data", "from_text", "src", "src_commit",
               "src_ptr", "extra")
KINDS = ("verdict", "prereg", "amend", "freeze", "result", "step", "measure", "experiment", "correction", "note")
SCHEMA_VERSION = 4
HERE = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()

SCHEMA = """
CREATE TABLE entry(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,                       -- когда сделано: ГГГГ-ММ-ДД[THH:MM]+04:00 (GMT+4); NULL — в источнике нет даты
  task TEXT, project TEXT,       -- TK-NNN; П-NN / G-NNN
  kind TEXT NOT NULL,
  hypothesis TEXT,
  verdict TEXT CHECK (verdict IS NULL OR verdict IN ('работает','не работает','не ясно','описательно')),
  usd REAL, trades INTEGER, sharpe REAL,            -- ключевые числа: $ на ставку 500 $, сделок, Шарп
  same_data INTEGER CHECK (same_data IS NULL OR same_data IN (0,1)),   -- «найдено на этих данных»
  title TEXT NOT NULL, body TEXT NOT NULL,
  fields TEXT,                   -- JSON: ячейки таблицы источника и прочее разобранное
  parsed TEXT,                   -- JSON (перенос): откуда взято каждое поле и почему поле пусто
  corrects INTEGER REFERENCES entry(id),
  src TEXT, h TEXT NOT NULL,     -- источник переноса (путь:строка); sha256 тела
  author TEXT, created TEXT NOT NULL);
CREATE INDEX entry_task ON entry(task);
CREATE INDEX entry_project ON entry(project);
CREATE INDEX entry_ts ON entry(ts);
CREATE TRIGGER entry_immutable BEFORE UPDATE ON entry BEGIN SELECT RAISE(ABORT, 'chron: запись неизменяема — добавь исправление (--corrects)'); END;
CREATE TRIGGER entry_nodelete BEFORE DELETE ON entry BEGIN SELECT RAISE(ABORT, 'chron: запись неизменяема — удалять нельзя'); END;

CREATE TABLE protocol(id TEXT PRIMARY KEY, short TEXT NOT NULL, title TEXT NOT NULL, path TEXT, created TEXT NOT NULL);
CREATE TABLE psec(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  protocol TEXT NOT NULL REFERENCES protocol(id),
  ord REAL NOT NULL, level INTEGER NOT NULL, num TEXT, heading TEXT NOT NULL,
  body TEXT NOT NULL,            -- текст раздела дословно, со строкой заголовка
  kind TEXT,                     -- rules | gates | alpha | verdict_rule | data | hypothesis | amendment | other (по словам заголовка)
  alpha TEXT,                    -- первое «α = …» в разделе
  prereg INTEGER NOT NULL DEFAULT 0, prereg_at TEXT,   -- помечен «до счёта» и когда записан
  locked INTEGER NOT NULL DEFAULT 0,
  amends INTEGER REFERENCES psec(id),
  src TEXT, h TEXT NOT NULL, author TEXT, created TEXT NOT NULL);
CREATE INDEX psec_proto ON psec(protocol, ord);
CREATE TRIGGER psec_locked BEFORE UPDATE ON psec WHEN OLD.locked = 1 BEGIN SELECT RAISE(ABORT, 'chron: раздел записан до счёта и неизменяем — добавь поправку (--amends)'); END;
CREATE TRIGGER psec_nodelete BEFORE DELETE ON psec BEGIN SELECT RAISE(ABORT, 'chron: раздел протокола удалять нельзя'); END;

CREATE TABLE link(src TEXT NOT NULL, dst TEXT NOT NULL, rel TEXT NOT NULL, origin TEXT, created TEXT NOT NULL, PRIMARY KEY (src, dst, rel));
CREATE INDEX link_dst ON link(dst);
CREATE TRIGGER link_nodelete BEFORE DELETE ON link BEGIN SELECT RAISE(ABORT, 'chron: ссылки только добавляются'); END;
CREATE TRIGGER link_noupdate BEFORE UPDATE ON link BEGIN SELECT RAISE(ABORT, 'chron: ссылки только добавляются'); END;

-- снимки перенесённых md: файл восстанавливается байт в байт из chunk (тело записи/раздела + хвост, или текст рамки)
CREATE TABLE doc(ver INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, imported TEXT NOT NULL);
CREATE TABLE chunk(ver INTEGER NOT NULL REFERENCES doc(ver), ord INTEGER NOT NULL, node TEXT, text TEXT, suffix TEXT NOT NULL DEFAULT '', PRIMARY KEY (ver, ord));

-- узлы kb (карточки md в git) — связанный индекс, заменяется целиком командой sync-kb
CREATE TABLE kb_node(id TEXT PRIMARY KEY, type TEXT, status TEXT, title TEXT, date TEXT, path TEXT);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""
PSEC_CUR_V3 = """CREATE VIEW psec_cur AS SELECT p.id, p.protocol, p.ord, p.level, p.num, p.heading, p.body, p.kind, p.alpha,
  CASE WHEN f.id IS NULL THEN p.prereg ELSE f.prereg END AS prereg, CASE WHEN f.id IS NULL THEN p.prereg_at ELSE f.prereg_at END AS prereg_at,
  p.locked, p.amends, p.src, p.h, p.author, p.created, f.id AS flag_id, f.reason AS flag_reason
  FROM psec p LEFT JOIN psec_flag f ON f.id = (SELECT max(id) FROM psec_flag WHERE psec = p.id);
"""
# user_version → операторы; новая БД = SCHEMA + все шаги по порядку
MIGRATE = {3: """
-- исправление флага «до счёта» перенесённого раздела (строка psec неизменяема): действует последняя строка (TK-646 v3)
CREATE TABLE psec_flag(
  id INTEGER PRIMARY KEY AUTOINCREMENT, psec INTEGER NOT NULL REFERENCES psec(id),
  prereg INTEGER NOT NULL CHECK (prereg IN (0,1)), prereg_at TEXT, reason TEXT NOT NULL, author TEXT, created TEXT NOT NULL);
CREATE INDEX psec_flag_psec ON psec_flag(psec, id);
CREATE TRIGGER psec_flag_noupdate BEFORE UPDATE ON psec_flag BEGIN SELECT RAISE(ABORT, 'chron: исправления флага только добавляются'); END;
CREATE TRIGGER psec_flag_nodelete BEFORE DELETE ON psec_flag BEGIN SELECT RAISE(ABORT, 'chron: исправления флага только добавляются'); END;
""" + PSEC_CUR_V3,
           4: """
-- числа записи: запись × сторона × клетка × вселенная × окно (TK-647); строки только добавляются
CREATE TABLE metric(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry INTEGER NOT NULL REFERENCES entry(id),
  side TEXT CHECK (side IS NULL OR side IN ('long','short','both')),
  cell TEXT, uni TEXT, win TEXT,                     -- клетка / вариант; вселенная; окно
  trades INTEGER, usd REAL, mean_usd REAL, sharpe REAL,   -- сделок; Σ $ на ставку 500 $; $ / сделку; Шарп годовой по дневному
  verdict TEXT CHECK (verdict IS NULL OR verdict IN ('работает','не работает','не ясно','описательно')),
  same_data INTEGER CHECK (same_data IS NULL OR same_data IN (0,1)),   -- «найдено на этих данных»
  from_text INTEGER NOT NULL DEFAULT 0 CHECK (from_text IN (0,1)),     -- 1 = «из текста» (таблица отчёта md / запись), не файл результатов
  src TEXT NOT NULL, src_commit TEXT, src_ptr TEXT,  -- файл в git; коммит, где он таким записан; место (путь JSON / строка)
  extra TEXT,                                        -- JSON: прочие числа строки (p, lo90 / hi90, ворота, сырой $ при иной ставке)
  h TEXT NOT NULL UNIQUE, author TEXT, created TEXT NOT NULL);
CREATE INDEX metric_entry ON metric(entry);
CREATE TRIGGER metric_noupdate BEFORE UPDATE ON metric BEGIN SELECT RAISE(ABORT, 'chron: числа только добавляются — исправление новой записью (--corrects)'); END;
CREATE TRIGGER metric_nodelete BEFORE DELETE ON metric BEGIN SELECT RAISE(ABORT, 'chron: числа только добавляются'); END;

-- поправка номера раздела (строка psec неизменяема): действует последняя (TK-647)
CREATE TABLE psec_num(
  id INTEGER PRIMARY KEY AUTOINCREMENT, psec INTEGER NOT NULL REFERENCES psec(id), num TEXT NOT NULL, reason TEXT NOT NULL,
  author TEXT, created TEXT NOT NULL);
CREATE INDEX psec_num_psec ON psec_num(psec, id);
CREATE TRIGGER psec_num_noupdate BEFORE UPDATE ON psec_num BEGIN SELECT RAISE(ABORT, 'chron: поправки номера только добавляются'); END;
CREATE TRIGGER psec_num_nodelete BEFORE DELETE ON psec_num BEGIN SELECT RAISE(ABORT, 'chron: поправки номера только добавляются'); END;
DROP VIEW psec_cur;
CREATE VIEW psec_cur AS SELECT p.id, p.protocol, p.ord, p.level, coalesce(n.num, p.num) AS num, p.heading, p.body, p.kind, p.alpha,
  CASE WHEN f.id IS NULL THEN p.prereg ELSE f.prereg END AS prereg, CASE WHEN f.id IS NULL THEN p.prereg_at ELSE f.prereg_at END AS prereg_at,
  p.locked, p.amends, p.src, p.h, p.author, p.created, f.id AS flag_id, f.reason AS flag_reason, n.id AS num_id
  FROM psec p LEFT JOIN psec_flag f ON f.id = (SELECT max(id) FROM psec_flag WHERE psec = p.id)
  LEFT JOIN psec_num n ON n.id = (SELECT max(id) FROM psec_num WHERE psec = p.id);
"""}
FTS_SQL = "CREATE VIRTUAL TABLE fts USING fts5(node UNINDEXED, title, body, tokenize='trigram')"


class ChronError(Exception):
    pass


def now():
    return datetime.datetime.now(TZ).strftime("%Y-%m-%dT%H:%M+04:00")


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fold(s):
    return unicodedata.normalize("NFC", s or "").lower().replace("ё", "е")


# ───────────────────────── узлы и ссылки ─────────────────────────

PSEC_REF_RE = re.compile(r"^[PП]-?([0-9A-Za-zА-Яа-я]+)\s*§\s*([\d.]*\d)$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def node_kind(n):
    """Тип канонического узла: entry | psec | commit | file | other (протокол, карточка kb, задача)."""
    if re.match(r"^L-\d+$", n):
        return "entry"
    if re.match(r"^PS-\d+$", n):
        return "psec"
    if n.startswith("commit:"):
        return "commit"
    if n.startswith("file:"):
        return "file"
    return "other"


def canon_ref(r):
    """Ввод пользователя → канонический узел или ('psec-ref', протокол, номер) для разрешения в БД."""
    r = unicodedata.normalize("NFC", r.strip())
    m = PSEC_REF_RE.match(r)
    if m:
        return ("psec-ref", "P-" + m.group(1), m.group(2))
    if re.match(r"^[LЛ]-\d+$", r):
        return "L-" + r[2:]
    if re.match(r"^PS-\d+$", r) or r.startswith(("commit:", "file:")):
        return r
    if SHA_RE.match(r.lower()) and re.search(r"\d", r):
        return "commit:" + r.lower()
    if "/" in r or re.search(r"\.(md|json|py|rs|sh|csv|txt|toml|html|js)(#.*)?$", r):
        return "file:" + r.lstrip("./")
    if r.startswith("П-"):
        r = "P-" + r[2:]
    return r


def rel_for(dst, kb_types=None):
    k = node_kind(dst)
    if k in ("entry", "psec", "commit"):
        return k
    if k == "file":
        p = dst[5:]
        if p.startswith("docs/research/freeze/"):
            return "freeze"
        if p.startswith(("docs/findings/", "docs/archive/findings/")):
            return "report"
        return "file"
    if kb_types and dst in kb_types:
        return {"decision": "decision", "task": "task", "protocol": "protocol"}.get(kb_types[dst], "kb")
    if re.match(r"^(TK-\d+|G-\d{3})$", dst):
        return "task"
    return "protocol" if dst.startswith("P-") else "kb"


# ───────────────────────── хранилище (исполняется там, где лежит БД) ─────────────────────────

def connect(path, ro=False):
    if ro and not Path(path).exists():
        connect(path).close()  # первая встреча — создать схему
    if ro:
        conn = sqlite3.connect("file:%s?mode=ro" % quote(str(path)), uri=True, timeout=30)
        if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:  # старая схема — перевести (один раз)
            conn.close()
            connect(path).close()
            conn = sqlite3.connect("file:%s?mode=ro" % quote(str(path)), uri=True, timeout=30)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=60, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=60000")
        if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
                for stmt in split_sql(SCHEMA) + [x for v in sorted(MIGRATE) for x in split_sql(MIGRATE[v])]:
                    conn.execute(stmt)
                conn.execute(FTS_SQL)
                conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
            conn.execute("COMMIT")
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        if v < SCHEMA_VERSION:
            conn.execute("BEGIN IMMEDIATE")
            v = conn.execute("PRAGMA user_version").fetchone()[0]
            for ver in range(v + 1, SCHEMA_VERSION + 1):
                for stmt in split_sql(MIGRATE[ver]):
                    conn.execute(stmt)
            conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
            conn.execute("COMMIT")
        conn.execute("PRAGMA synchronous=FULL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def split_sql(script):
    """Делит схему на операторы (триггеры содержат «;» внутри BEGIN…END)."""
    out, buf = [], []
    for line in script.splitlines():
        line = re.sub(r"(^|\s)-- .*$", "", line)
        if not line.strip():
            continue
        buf.append(line)
        joined = "\n".join(buf).strip()
        if joined.endswith(";") and (not re.match(r"(?is)^CREATE\s+TRIGGER", joined) or re.search(r"(?i)\bEND;$", joined)):
            out.append(joined)
            buf = []
    return out


# время записи для порядка «новые первыми»: дата без времени, записанная в тот же день, — время создания (иначе запись 20.11
# без часа оказалась бы ниже записи 20.11 19:38, сделанной раньше неё)
ENTRY_TS = "(CASE WHEN length(e.ts) = 10 AND substr(e.created, 1, 10) = e.ts THEN e.created ELSE coalesce(e.ts, '') END)"


class Store:
    def __init__(self, conn):
        self.c = conn

    # ── разрешение узлов
    def exists(self, n):
        k = node_kind(n)
        if k == "entry":
            return self.c.execute("SELECT 1 FROM entry WHERE id=?", (int(n[2:]),)).fetchone() is not None
        if k == "psec":
            return self.c.execute("SELECT 1 FROM psec WHERE id=?", (int(n[3:]),)).fetchone() is not None
        if k in ("commit", "file"):
            return None  # проверяется по git на стороне клиента
        if self.c.execute("SELECT 1 FROM protocol WHERE id=?", (n,)).fetchone():
            return True
        if self.c.execute("SELECT 1 FROM kb_node WHERE id=?", (n,)).fetchone():
            return True
        if re.match(r"^(TK-\d+|G-\d{3})$", n):
            return True  # номер задачи / цели — узел по формату (реестра задач вне bb нет); страница узла собирает все упоминания
        return False

    def resolve_psec(self, proto, num):
        """«P-42 §13.28» → PS-id: раздел главного файла протокола с этим номером (первая запись, не поправка)."""
        rows = self.c.execute("SELECT p.id FROM psec_cur p JOIN protocol r ON r.id = p.protocol WHERE (r.short=? OR r.id=?) AND p.num=? "
                              "ORDER BY (r.id <> (SELECT value FROM meta WHERE key='main:' || ?)), p.amends IS NOT NULL, p.id LIMIT 1",
                              (proto, proto, num, proto)).fetchone()
        return "PS-%d" % rows[0] if rows else None

    def canon(self, ref):
        c = canon_ref(ref) if isinstance(ref, str) else tuple(ref)
        if isinstance(c, tuple):
            return self.resolve_psec(c[1], c[2])
        return c

    def kb_types(self):
        return {r[0]: r[1] for r in self.c.execute("SELECT id, type FROM kb_node")}

    def _links(self, src, dsts, origin, kbt):
        for d in dsts:
            if d and d != src:
                self.c.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?)", (src, d, rel_for(d, kbt), origin, now()))

    def _fts(self, node, title, body):
        self.c.execute("INSERT INTO fts(node, title, body) VALUES (?,?,?)", (node, fold(title), fold(body)))

    # ── запись
    def add_entry(self, e, links, author=None, metrics=None):
        """e: поля записи; links: ссылки (строки ввода); metrics: строки чисел (TK-647). Битая ссылка — отказ, ничего не пишется."""
        if e.get("verdict") and e["verdict"] not in VERDICTS:
            raise ChronError("вердикт «%s» — только %s" % (e["verdict"], " / ".join(VERDICTS)))
        if e.get("kind") not in KINDS:
            raise ChronError("вид «%s» — только %s" % (e.get("kind"), ", ".join(KINDS)))
        if not (e.get("title") or "").strip():
            raise ChronError("нужен заголовок (--title)")
        self.c.execute("BEGIN IMMEDIATE")
        try:
            dsts, bad = [], []
            for r in links:
                d = self.canon(r)
                if not d or self.exists(d) is False:
                    bad.append(r)
                else:
                    dsts.append(d)
            if e.get("corrects"):
                d = self.canon(e["corrects"])
                if not d or node_kind(d) != "entry" or not self.exists(d):
                    bad.append(e["corrects"])
                else:
                    e["corrects"] = int(d[2:])
            if bad:
                raise ChronError("ссылки не найдены: %s" % ", ".join(bad))
            body = e.get("body") or ""
            cur = self.c.execute(
                "INSERT INTO entry(ts, task, project, kind, hypothesis, verdict, usd, trades, sharpe, same_data, title, body, fields, corrects, src, h, author, created)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.get("ts") or now(), e.get("task"), e.get("project"), e["kind"], e.get("hypothesis"), e.get("verdict"),
                 e.get("usd"), e.get("trades"), e.get("sharpe"), e.get("same_data"), e["title"].strip(), body,
                 json.dumps(e["fields"], ensure_ascii=False) if e.get("fields") else None, e.get("corrects"), e.get("src"),
                 sha(body), author, now()))
            nid = "L-%d" % cur.lastrowid
            if e.get("corrects"):
                dsts.append("L-%d" % e["corrects"])
            self._links(nid, dsts, "add", self.kb_types())
            self._fts(nid, e["title"] + " " + (e.get("hypothesis") or ""), body)
            if metrics:
                self._metric_rows(cur.lastrowid, metrics, author)
            self.c.execute("COMMIT")
            return nid
        except BaseException:
            self.c.execute("ROLLBACK")
            raise

    def new_protocol(self, pid, title, short=None, path=None):
        self.c.execute("INSERT INTO protocol VALUES (?,?,?,?,?)", (pid, short or protocol_short(pid), title, path, now()))
        return pid

    def add_psec(self, s, links, author=None):
        self.c.execute("BEGIN IMMEDIATE")
        try:
            pr = self.c.execute("SELECT id FROM protocol WHERE id=? OR short=? ORDER BY id<>? LIMIT 1", (s["protocol"],) * 3).fetchone()
            if not pr:
                if not s.get("create_title"):
                    raise ChronError("протокол %s не найден (новый — `chron.py protocol new`)" % s["protocol"])
                self.new_protocol(s["protocol"], s["create_title"])
                pr = (s["protocol"],)
            amends = None
            if s.get("amends"):
                d = self.canon(s["amends"])
                if not d or node_kind(d) != "psec" or not self.exists(d):
                    raise ChronError("раздел для поправки не найден: %s" % s["amends"])
                amends = int(d[3:])
            if s.get("after"):
                d = self.canon(s["after"])
                if not d or node_kind(d) != "psec":
                    raise ChronError("раздел не найден: %s" % s["after"])
                o = self.c.execute("SELECT ord FROM psec WHERE id=?", (int(d[3:]),)).fetchone()[0]
                nxt = self.c.execute("SELECT min(ord) FROM psec WHERE protocol=? AND ord>?", (pr[0], o)).fetchone()[0]
                ord_ = (o + nxt) / 2 if nxt is not None else o + 1
            elif amends:
                o = self.c.execute("SELECT ord FROM psec WHERE id=?", (amends,)).fetchone()[0]
                nxt = self.c.execute("SELECT min(ord) FROM psec WHERE protocol=? AND ord>?", (pr[0], o)).fetchone()[0]
                last = self.c.execute("SELECT max(ord) FROM psec WHERE amends=?", (amends,)).fetchone()[0]
                base = max(o, last or o)
                ord_ = (base + nxt) / 2 if nxt is not None and nxt > base else base + 1
            else:
                ord_ = (self.c.execute("SELECT max(ord) FROM psec WHERE protocol=?", (pr[0],)).fetchone()[0] or 0) + 1
            dsts, bad = [], []
            for r in links:
                d = self.canon(r)
                (bad if not d or self.exists(d) is False else dsts).append(d or r)
            if bad:
                raise ChronError("ссылки не найдены: %s" % ", ".join(bad))
            body = s["body"]
            head = s.get("heading") or first_line(body).lstrip("#").strip()
            meta = section_meta(head, body)
            cur = self.c.execute(
                "INSERT INTO psec(protocol, ord, level, num, heading, body, kind, alpha, prereg, prereg_at, locked, amends, src, h, author, created)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pr[0], ord_, s.get("level") or meta["level"] or 2, s.get("num") or meta["num"], head, body, meta["kind"], meta["alpha"],
                 1 if s.get("prereg") else 0, now() if s.get("prereg") else None, 1 if s.get("prereg") else 0,
                 amends, None, sha(body), author, now()))
            nid = "PS-%d" % cur.lastrowid
            if amends:
                dsts.append("PS-%d" % amends)
            self._links(nid, dsts, "add", self.kb_types())
            self._fts(nid, head, body)
            self.c.execute("COMMIT")
            return nid
        except BaseException:
            self.c.execute("ROLLBACK")
            raise

    def edit_psec(self, node, body):
        d = self.canon(node)
        if not d or node_kind(d) != "psec":
            raise ChronError("не раздел: %s" % node)
        self.c.execute("BEGIN IMMEDIATE")
        try:
            self.c.execute("UPDATE psec SET body=?, h=? WHERE id=?", (body, sha(body), int(d[3:])))
            self.c.execute("DELETE FROM fts WHERE node=?", (d,))
            self._fts(d, self.c.execute("SELECT heading FROM psec WHERE id=?", (int(d[3:]),)).fetchone()[0], body)
            self.c.execute("COMMIT")
        except BaseException:
            self.c.execute("ROLLBACK")
            raise
        return d

    def lock_psec(self, node):
        d = self.canon(node)
        if not d or node_kind(d) != "psec":
            raise ChronError("не раздел: %s" % node)
        self.c.execute("UPDATE psec SET prereg=1, prereg_at=coalesce(prereg_at, ?), locked=1 WHERE id=? AND locked=0", (now(), int(d[3:])))
        return d

    def flag_psec(self, node, prereg, prereg_at, reason, author=None):
        """Исправить флаг «до счёта» раздела: новая строка psec_flag (строка psec не меняется); читается последняя."""
        d = self.canon(node)
        if not d or node_kind(d) != "psec" or not self.exists(d):
            raise ChronError("не раздел: %s" % node)
        if not (reason or "").strip():
            raise ChronError("нужна причина исправления")
        cur = self.c.execute("INSERT INTO psec_flag(psec, prereg, prereg_at, reason, author, created) VALUES (?,?,?,?,?,?)",
                             (int(d[3:]), 1 if prereg else 0, prereg_at or None, reason, author, now()))
        return "%s флаг #%d" % (d, cur.lastrowid)

    def num_psec(self, node, num, reason, author=None):
        """Поправить номер раздела (строка psec неизменяема): новая строка psec_num; читается последняя (вид psec_cur)."""
        d = self.canon(node)
        if not d or node_kind(d) != "psec" or not self.exists(d):
            raise ChronError("не раздел: %s" % node)
        if not (num or "").strip() or not (reason or "").strip():
            raise ChronError("нужны номер и причина")
        cur = self.c.execute("INSERT INTO psec_num(psec, num, reason, author, created) VALUES (?,?,?,?,?)",
                             (int(d[3:]), num.strip(), reason, author, now()))
        return "%s номер «%s» #%d" % (d, num.strip(), cur.lastrowid)

    # ── числа записи (TK-647): строки metric только добавляются; повтор (тот же h) не пишется
    def _metric_rows(self, eid, rows, author):
        n = 0
        for r in rows:
            if r.get("verdict") and r["verdict"] not in VERDICTS:
                raise ChronError("вердикт «%s» — только %s" % (r["verdict"], " / ".join(VERDICTS)))
            if not r.get("src"):
                raise ChronError("у числа нет источника (src)")
            if r.get("side") not in (None, "long", "short", "both"):
                raise ChronError("сторона «%s» — только long / short / both" % r["side"])
            h = r.get("h") or sha(json.dumps([eid] + [r.get(k) for k in METRIC_COLS], ensure_ascii=False, default=str))
            cur = self.c.execute(
                "INSERT OR IGNORE INTO metric(entry, side, cell, uni, win, trades, usd, mean_usd, sharpe, verdict, same_data, from_text, src,"
                " src_commit, src_ptr, extra, h, author, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, r.get("side"), r.get("cell"), r.get("uni"), r.get("win"), r.get("trades"), r.get("usd"), r.get("mean_usd"),
                 r.get("sharpe"), r.get("verdict"), r.get("same_data"), 1 if r.get("from_text") else 0, r["src"], r.get("src_commit"),
                 r.get("src_ptr"), json.dumps(r["extra"], ensure_ascii=False) if r.get("extra") else None, h, author, now()))
            n += cur.rowcount
        return n

    def add_metrics(self, rows, author=None):
        """rows: [{entry: id, side, cell, uni, win, trades, usd, mean_usd, sharpe, verdict, same_data, from_text, src, src_commit, src_ptr, extra, h}]
        → {new, same}: одной транзакцией; запись должна существовать."""
        self.c.execute("BEGIN IMMEDIATE")
        try:
            ids = {r[0] for r in self.c.execute("SELECT id FROM entry")}
            bad = sorted({r["entry"] for r in rows if r["entry"] not in ids})
            if bad:
                raise ChronError("нет записей: %s" % ", ".join("L-%d" % b for b in bad))
            new = 0
            by = {}
            for r in rows:
                by.setdefault(r["entry"], []).append(r)
            for eid, rs in by.items():
                new += self._metric_rows(eid, rs, author)
            self.c.execute("COMMIT")
            return {"new": new, "same": len(rows) - new}
        except BaseException:
            self.c.execute("ROLLBACK")
            raise

    def sync_kb(self, nodes):
        self.c.execute("BEGIN IMMEDIATE")
        self.c.execute("DELETE FROM kb_node")
        self.c.executemany("INSERT INTO kb_node VALUES (?,?,?,?,?,?)", [tuple(n) for n in nodes])
        self.c.execute("INSERT OR REPLACE INTO meta VALUES ('kb_synced', ?)", (now(),))
        self.c.execute("COMMIT")
        return len(nodes)

    # ── перенос md (идемпотентно: совпавшие по тексту строки переиспользуются, новые добавляются, изменённые разделы — поправками)
    def import_docs(self, docs, kb_nodes=None):
        if kb_nodes is not None:
            self.sync_kb(kb_nodes)
        kbt = self.kb_types()
        stats = {}
        self.c.execute("BEGIN IMMEDIATE")
        try:
            for d in docs:
                st = stats.setdefault(d["path"], {"rows": 0, "new": 0, "reused": 0, "amended": 0, "frames": 0, "links": 0, "unresolved": 0})
                prev = self.c.execute("SELECT sha256 FROM doc WHERE path=? ORDER BY ver DESC LIMIT 1", (d["path"],)).fetchone()
                if prev and prev[0] == d["sha256"]:
                    st["unchanged"] = 1
                    st["rows"] = sum(1 for ch in d["chunks"] if ch["kind"] != "frame")
                    continue
                ver = self.c.execute("INSERT INTO doc(path, sha256, bytes, imported) VALUES (?,?,?,?)",
                                     (d["path"], d["sha256"], d["bytes"], now())).lastrowid
                if d.get("protocol"):
                    p = d["protocol"]
                    if not self.c.execute("SELECT 1 FROM protocol WHERE id=?", (p["id"],)).fetchone():
                        self.c.execute("INSERT INTO protocol VALUES (?,?,?,?,?)", (p["id"], p["short"], p["title"], d["path"], now()))
                    if p.get("main"):
                        self.c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("main:" + p["short"], p["id"]))
                pending = []
                used = set()
                if d.get("protocol"):
                    self._plan_sections(d)
                for i, ch in enumerate(d["chunks"]):
                    if ch["kind"] == "frame":
                        self.c.execute("INSERT INTO chunk VALUES (?,?,?,?,?)", (ver, i, None, ch["text"], ""))
                        st["frames"] += 1
                        continue
                    st["rows"] += 1
                    node = self._import_chunk(d, ch, i, used, st)
                    self.c.execute("INSERT INTO chunk VALUES (?,?,?,?,?)", (ver, i, node, None, ch.get("suffix", "")))
                    pending.append((node, ch.get("links") or []))
                for node, refs in pending:
                    for r in refs:
                        dst = self.canon(r)
                        if dst and dst != node and self.exists(dst) is not False:
                            if self.c.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?)", (node, dst, rel_for(dst, kbt), "import", now())).rowcount:
                                st["links"] += 1
                        else:
                            st["unresolved"] += 1
                            k = r[0] if isinstance(r, list) else re.sub(r"[-:].*$", "", r)
                            st.setdefault("unresolved_by", {})
                            st["unresolved_by"][k] = st["unresolved_by"].get(k, 0) + 1
            self.c.execute("COMMIT")
        except BaseException:
            self.c.execute("ROLLBACK")
            raise
        for d in docs:
            stats[d["path"]]["back_sha256"] = sha(self.reconstruct(d["path"]))
        return stats

    def _plan_sections(self, d):
        """До вставки: какие разделы файла уже есть в БД (тот же текст), место (ord) новых — между соседями по файлу,
        и разделы прошлой версии, которых в файле больше нет (кандидаты в «изменённые»)."""
        pid, used, secs = d["protocol"]["id"], set(), [ch for ch in d["chunks"] if ch["kind"] == "psec"]
        for ch in secs:
            excl = ",".join(str(u) for u in used) or "0"
            row = self.c.execute("SELECT id, ord FROM psec WHERE protocol=? AND h=? AND id NOT IN (%s) ORDER BY id LIMIT 1" % excl,
                                 (pid, sha(ch["body"]))).fetchone()
            ch["_ord"] = row[1] if row else None
            if row:
                used.add(row[0])
        for i, ch in enumerate(secs):
            if ch["_ord"] is not None:
                continue
            j = i
            while j < len(secs) and secs[j]["_ord"] is None:
                j += 1
            p = secs[i - 1]["_ord"] if i else None
            n = secs[j]["_ord"] if j < len(secs) else None
            for k, c in enumerate(secs[i:j], 1):
                c["_ord"] = (float(d["chunks"].index(c)) if p is None and n is None else p + k if n is None
                             else n - (j - i + 1 - k) if p is None else p + (n - p) * k / (j - i + 1))
        excl = ",".join(str(u) for u in used) or "0"
        d["_gone"] = [dict(r) for r in self.c.execute(
            "SELECT id, num, heading FROM psec_cur WHERE protocol=? AND src LIKE ? AND id NOT IN (%s) AND id NOT IN (SELECT amends FROM psec WHERE amends IS NOT NULL)"
            % excl, (pid, d["path"] + ":%"))]

    def _import_chunk(self, d, ch, i, used, st):
        h = sha(ch["body"])
        if ch["kind"] == "entry":
            excl = ",".join(str(u) for u in used) or "0"
            row = self.c.execute("SELECT id FROM entry WHERE h=? AND src LIKE ? AND id NOT IN (%s) ORDER BY id LIMIT 1" % excl,
                                 (h, d["path"] + ":%")).fetchone()
            if row:
                used.add(row[0])
                st["reused"] += 1
                return "L-%d" % row[0]
            e = ch["entry"]
            cur = self.c.execute(
                "INSERT INTO entry(ts, task, project, kind, hypothesis, verdict, usd, trades, sharpe, same_data, title, body, fields, parsed, corrects, src, h, author, created)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.get("ts"), e.get("task"), e.get("project"), e["kind"], e.get("hypothesis"), e.get("verdict"), e.get("usd"), e.get("trades"),
                 e.get("sharpe"), e.get("same_data"), e["title"], ch["body"], json.dumps(e["fields"], ensure_ascii=False) if e.get("fields") else None,
                 json.dumps(e["parsed"], ensure_ascii=False) if e.get("parsed") else None, None, "%s:%d" % (d["path"], ch["line"]), h, "import", now()))
            used.add(cur.lastrowid)
            st["new"] += 1
            nid = "L-%d" % cur.lastrowid
            self._fts(nid, e["title"], ch["body"])
            return nid
        # раздел протокола
        pid = d["protocol"]["id"]
        s = ch["sec"]
        excl = ",".join(str(u) for u in used) or "0"
        row = self.c.execute("SELECT id FROM psec WHERE protocol=? AND h=? AND id NOT IN (%s) ORDER BY id LIMIT 1" % excl, (pid, h)).fetchone()
        if row:
            used.add(row[0])
            st["reused"] += 1
            return "PS-%d" % row[0]
        # изменённый раздел = раздел прошлой версии файла с тем же номером, которого в файле больше нет (заголовок — для разрешения
        # дублей номера); без номера или без такого раздела — новый. Совпадение одного заголовка («1. Вопрос…») поправкой не считается.
        gone = [g for g in d.get("_gone", []) if s["num"] and g["num"] == s["num"] and g["id"] not in used]
        if len(gone) > 1:
            gone = [g for g in gone if g["heading"] == s["heading"]]
        amends, ord_ = (gone[0]["id"] if len(gone) == 1 else None), ch.get("_ord", float(i))
        if amends:
            used.add(amends)
            st["amended"] += 1
        else:
            st["new"] += 1
        cur = self.c.execute(
            "INSERT INTO psec(protocol, ord, level, num, heading, body, kind, alpha, prereg, prereg_at, locked, amends, src, h, author, created)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, ord_, s["level"], s["num"], s["heading"], ch["body"], s["kind"], s["alpha"], s["prereg"], s["prereg_at"], 1,
             amends, "%s:%d" % (d["path"], ch["line"]), h, "import", now()))
        used.add(cur.lastrowid)
        nid = "PS-%d" % cur.lastrowid
        if amends:
            self.c.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?)", (nid, "PS-%d" % amends, "psec", "import", now()))
        self._fts(nid, s["heading"], ch["body"])
        return nid

    def reconstruct(self, path, ver=None):
        if ver is None:
            r = self.c.execute("SELECT ver, sha256 FROM doc WHERE path=? ORDER BY ver DESC LIMIT 1", (path,)).fetchone()
            if not r:
                raise ChronError("нет снимка %s" % path)
            ver = r[0]
        out = []
        for ch in self.c.execute("SELECT node, text, suffix FROM chunk WHERE ver=? ORDER BY ord", (ver,)):
            if ch["node"] is None:
                out.append(ch["text"])
            else:
                out.append(self.node_body(ch["node"]) + ch["suffix"])
        return "".join(out)

    def node_body(self, n):
        k = node_kind(n)
        if k == "entry":
            return self.c.execute("SELECT body FROM entry WHERE id=?", (int(n[2:]),)).fetchone()[0]
        return self.c.execute("SELECT body FROM psec WHERE id=?", (int(n[3:]),)).fetchone()[0]

    # ── чтение
    def query(self, f):
        """Записи по фильтрам: q, project, task, verdict ('—' = не размечено), kind, since, until, limit, offset."""
        where, args = [], []
        if f.get("q"):
            ids = self.search(f["q"], kinds=("L",))
            if not ids:
                return {"rows": [], "total": 0}
            where.append("e.id IN (%s)" % ",".join(str(int(i[2:])) for i in ids))
        for k in ("project", "task", "kind"):
            if f.get(k):
                where.append("e.%s = ?" % k)
                args.append(f[k])
        if f.get("verdict"):
            if f["verdict"] == "—":
                where.append("e.verdict IS NULL")
            else:
                where.append("e.verdict = ?")
                args.append(f["verdict"])
        if f.get("since"):
            where.append("e.ts >= ?")
            args.append(f["since"])
        if f.get("until"):
            where.append("substr(e.ts, 1, 10) <= ?")
            args.append(f["until"])
        if f.get("nums"):  # есть числа: «файл» — из файлов результатов, «текст» — только из текста, «нет»
            where.append({"есть": "EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id)",
                          "файл": "EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id AND m.from_text = 0)",
                          "текст": "EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id) AND NOT EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id AND m.from_text = 0)",
                          "нет": "NOT EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id)"}.get(f["nums"], "1"))
        sql = " FROM entry e" + (" WHERE " + " AND ".join(where) if where else "")
        total = self.c.execute("SELECT count(*)" + sql, args).fetchone()[0]
        rows = self.c.execute("SELECT e.id, e.ts, e.task, e.project, e.kind, e.verdict, e.title, e.usd, e.trades, e.sharpe, e.same_data, e.corrects,"
                              " (SELECT max(c.id) FROM entry c WHERE c.corrects = e.id) AS corrected_by,"
                              " (SELECT count(*) FROM metric m WHERE m.entry = e.id) AS n_metrics" + sql +
                              " ORDER BY " + ENTRY_TS + " DESC, e.id DESC LIMIT ? OFFSET ?",
                              args + [int(f.get("limit") or 50), int(f.get("offset") or 0)]).fetchall()
        return {"rows": [dict(r) for r in rows], "total": total}

    def search(self, q, kinds=("L", "PS"), limit=500):
        toks = [t for t in fold(q).split() if t]
        if not toks:
            return []
        long_ = [t for t in toks if len(t) >= 3]
        short = [t for t in toks if len(t) < 3]
        if long_:
            match = " AND ".join('"%s"' % t.replace('"', '""') for t in long_)
            rows = self.c.execute("SELECT node, title, body FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 0, 4.0, 1.0) LIMIT ?",
                                  (match, limit * 4)).fetchall()
        else:
            rows = self.c.execute("SELECT node, title, body FROM fts").fetchall()
        out = []
        for r in rows:
            if not r[0].startswith(tuple(k + "-" for k in kinds)):
                continue
            if all(t in r[1] or t in r[2] for t in short):
                out.append(r[0])
            if len(out) >= limit:
                break
        return out

    def links_of(self, n):
        out = [dict(r) for r in self.c.execute("SELECT dst AS node, rel, origin FROM link WHERE src=? ORDER BY rel, dst", (n,))]
        inn = [dict(r) for r in self.c.execute("SELECT src AS node, rel, origin FROM link WHERE dst=? ORDER BY src", (n,))]
        for r in out + inn:
            r["label"] = self.label(r["node"])
        return {"out": out, "in": inn}

    def label(self, n):
        k = node_kind(n)
        if k == "entry":
            r = self.c.execute("SELECT title FROM entry WHERE id=?", (int(n[2:]),)).fetchone()
        elif k == "psec":
            r = self.c.execute("SELECT r.short || ' §' || coalesce(p.num, '') || ' ' || p.heading FROM psec_cur p JOIN protocol r ON r.id=p.protocol WHERE p.id=?",
                               (int(n[3:]),)).fetchone()
        elif k in ("commit", "file"):
            return n.split(":", 1)[1]
        else:
            r = self.c.execute("SELECT title FROM protocol WHERE id=? UNION ALL SELECT title FROM kb_node WHERE id=?", (n, n)).fetchone()
        return r[0][:200] if r else ""

    def get(self, ref, metrics_limit=200):
        n = self.canon(ref)
        if not n:
            raise ChronError("не найдено: %s" % ref)
        k = node_kind(n)
        res = {"node": n, "kind": k}
        if k == "entry":
            r = self.c.execute("SELECT * FROM entry WHERE id=?", (int(n[2:]),)).fetchone()
            if not r:
                raise ChronError("нет записи %s" % n)
            res["entry"] = dict(r)
            res["corrected_by"] = [x[0] for x in self.c.execute("SELECT id FROM entry WHERE corrects=? ORDER BY id", (r["id"],))]
            res["metrics_total"] = self.c.execute("SELECT count(*) FROM metric WHERE entry=?", (r["id"],)).fetchone()[0]
            res["metrics"] = [dict(x) for x in self.c.execute("SELECT * FROM metric WHERE entry=? ORDER BY id LIMIT ?", (r["id"], metrics_limit))]
        elif k == "psec":
            r = self.c.execute("SELECT p.*, r.short FROM psec_cur p JOIN protocol r ON r.id=p.protocol WHERE p.id=?", (int(n[3:]),)).fetchone()
            if not r:
                raise ChronError("нет раздела %s" % n)
            res["psec"] = dict(r)
            res["amended_by"] = [x[0] for x in self.c.execute("SELECT id FROM psec WHERE amends=? ORDER BY id", (r["id"],))]
            res["flags"] = [dict(x) for x in self.c.execute("SELECT * FROM psec_flag WHERE psec=? ORDER BY id", (r["id"],))]
            res["nums"] = [dict(x) for x in self.c.execute("SELECT * FROM psec_num WHERE psec=? ORDER BY id", (r["id"],))]
        else:
            p = self.c.execute("SELECT * FROM protocol WHERE id=?", (n,)).fetchone()
            if p:
                res["protocol"] = dict(p)
                res["sections"] = [dict(x) for x in self.c.execute(
                    "SELECT id, ord, level, num, heading, kind, prereg, prereg_at, locked, amends, length(body) AS bytes FROM psec_cur WHERE protocol=? ORDER BY ord, id", (n,))]
            kbn = self.c.execute("SELECT * FROM kb_node WHERE id=?", (n,)).fetchone()
            if kbn:
                res["kb"] = dict(kbn)
            res["protocols"] = [dict(x) for x in self.c.execute("SELECT id, title FROM protocol WHERE short=? ORDER BY id", (n,))]
            res["entries"] = [dict(x) for x in self.c.execute(
                "SELECT id, ts, task, project, kind, verdict, title FROM entry WHERE task=? OR project=? ORDER BY coalesce(ts,'') DESC, id DESC",
                (n, n.replace("P-", "П-", 1) if n.startswith("P-") else n))]
        res["links"] = self.links_of(n)
        return res

    def protocol_text(self, pid):
        p = self.c.execute("SELECT * FROM protocol WHERE id=? OR short=? ORDER BY id<>(SELECT value FROM meta WHERE key='main:'||?) LIMIT 1", (pid, pid, pid)).fetchone()
        if not p:
            raise ChronError("нет протокола %s" % pid)
        pre = ""
        ver = self.c.execute("SELECT ver FROM doc WHERE path=? ORDER BY ver DESC LIMIT 1", (p["path"],)).fetchone() if p["path"] else None
        if ver:
            first = self.c.execute("SELECT node, text FROM chunk WHERE ver=? ORDER BY ord LIMIT 1", (ver[0],)).fetchone()
            if first and first["node"] is None:
                pre = first["text"]
        secs = self.c.execute("SELECT id, body, amends FROM psec WHERE protocol=? ORDER BY ord, id", (p["id"],)).fetchall()
        return {"id": p["id"], "title": p["title"], "text": pre + "".join(s["body"] if s["body"].endswith("\n") else s["body"] + "\n" for s in secs)}

    def protocols(self):
        return [dict(r) for r in self.c.execute(
            "SELECT r.id, r.short, r.title, r.path, count(p.id) AS sections, sum(p.prereg) AS prereg, max(p.created) AS last"
            " FROM protocol r LEFT JOIN psec_cur p ON p.protocol=r.id GROUP BY r.id ORDER BY r.short, r.id")]

    def facets(self):
        return {"projects": [r[0] for r in self.c.execute("SELECT project FROM entry WHERE project IS NOT NULL GROUP BY project ORDER BY count(*) DESC")],
                "kinds": [r[0] for r in self.c.execute("SELECT kind FROM entry GROUP BY kind ORDER BY count(*) DESC")],
                "verdicts": list(VERDICTS)}

    def links_all(self):
        return [list(r) for r in self.c.execute("SELECT src, dst, rel FROM link")]

    def stats(self):
        q = lambda s: self.c.execute(s).fetchone()[0]
        return {"entries": q("SELECT count(*) FROM entry"), "entries_imported": q("SELECT count(*) FROM entry WHERE src IS NOT NULL"),
                "protocols": q("SELECT count(*) FROM protocol"), "sections": q("SELECT count(*) FROM psec"),
                "sections_prereg": q("SELECT count(*) FROM psec_cur WHERE prereg=1"), "flag_fixes": q("SELECT count(*) FROM psec_flag"), "num_fixes": q("SELECT count(*) FROM psec_num"),
                "metrics": q("SELECT count(*) FROM metric"), "entries_with_metrics": q("SELECT count(DISTINCT entry) FROM metric"), "links": q("SELECT count(*) FROM link"),
                "kb_nodes": q("SELECT count(*) FROM kb_node"), "docs": q("SELECT count(DISTINCT path) FROM doc"),
                "db_bytes": q("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")}

    def fields_report(self):
        """Перенесённые записи: сколько полей заполнено и почему пусто (причина из parsed) — с номерами записей."""
        filled = {f: self.c.execute("SELECT count(*) FROM entry WHERE src IS NOT NULL AND %s IS NOT NULL" % f).fetchone()[0]
                  for f in ("verdict", "usd", "trades", "sharpe", "same_data", "project", "task")}
        why = {}
        for i, p in self.c.execute("SELECT id, parsed FROM entry WHERE parsed IS NOT NULL ORDER BY id"):
            for f, w in json.loads(p).items():
                if w.startswith("—"):
                    r = re.sub(r"\s*\(.*$|\s+\d+$|:\s.*$", "", w[3:].strip())
                    why.setdefault(f, {}).setdefault(r, []).append(i)
        return {"total": self.c.execute("SELECT count(*) FROM entry WHERE src IS NOT NULL").fetchone()[0], "filled": filled, "why": why}

    METRIC_SORT = {"usd": "m.usd", "sharpe": "m.sharpe", "trades": "m.trades", "mean": "m.mean_usd", "date": ENTRY_TS, "entry": "m.entry"}

    def metrics_query(self, f):
        """Числа по фильтрам: entry, project, side, uni, win, cell (подстрока), verdict ('—' = пусто), same_data, text (0/1),
        min_trades, src (подстрока); sort: usd | sharpe | trades | mean | date | entry, «-» в начале — по убыванию; limit, offset."""
        where, args = [], []
        if f.get("entry"):
            where.append("m.entry = ?")
            args.append(int(str(f["entry"]).lstrip("LЛ-")))
        for k, col in (("project", "e.project"), ("side", "m.side"), ("uni", "m.uni"), ("win", "m.win")):
            if f.get(k):
                if f[k] == "—":
                    where.append("%s IS NULL" % col)
                else:
                    where.append("%s = ?" % col)
                    args.append(f[k].replace("P-", "П-", 1) if k == "project" else f[k])
        if f.get("verdict"):
            where.append("m.verdict IS NULL" if f["verdict"] == "—" else "m.verdict = ?")
            args += [] if f["verdict"] == "—" else [f["verdict"]]
        for k, col in (("same_data", "m.same_data"), ("text", "m.from_text")):
            if f.get(k) not in (None, ""):
                where.append("%s = ?" % col if str(f[k]) in ("0", "1") else "%s IS NULL" % col)
                args += [int(f[k])] if str(f[k]) in ("0", "1") else []
        for k, col in (("cell", "m.cell"), ("src", "m.src")):
            if f.get(k):
                where.append("instr(lower(%s), ?) > 0" % col)
                args.append(f[k].lower())
        if f.get("min_trades") not in (None, ""):
            where.append("m.trades >= ?")
            args.append(int(f["min_trades"]))
        if f.get("has_usd"):
            where.append("m.usd IS NOT NULL")
        sql = " FROM metric m JOIN entry e ON e.id = m.entry" + (" WHERE " + " AND ".join(where) if where else "")
        total = self.c.execute("SELECT count(*)" + sql, args).fetchone()[0]
        srt = f.get("sort") or "-date"
        col = self.METRIC_SORT.get(srt.lstrip("-"))
        if not col:
            raise ChronError("сортировка «%s» — только %s" % (srt, ", ".join(self.METRIC_SORT)))
        order = "%s IS NULL, %s %s, m.id" % (col, col, "DESC" if srt.startswith("-") else "ASC")
        rows = self.c.execute("SELECT m.*, e.project, e.task, e.ts, e.title, e.kind" + sql + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                              args + [int(f.get("limit") or 100), int(f.get("offset") or 0)]).fetchall()
        return {"rows": [dict(r) for r in rows], "total": total}

    def metrics_facets(self):
        q = lambda col: [r[0] for r in self.c.execute("SELECT %s FROM metric WHERE %s IS NOT NULL GROUP BY %s ORDER BY count(*) DESC LIMIT 60" % (col, col, col))]
        return {"uni": q("uni"), "win": q("win"),
                "projects": [r[0] for r in self.c.execute("SELECT e.project FROM metric m JOIN entry e ON e.id=m.entry WHERE e.project IS NOT NULL"
                                                          " GROUP BY e.project ORDER BY count(*) DESC")]}

    def metrics_coverage(self):
        """По проектам: записей, с числами (из файла / только из текста), строк, и сколько строк с каждым полем."""
        out = []
        for r in self.c.execute(
                "SELECT coalesce(e.project, '—') AS project, count(*) AS entries,"
                " sum(EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id)) AS with_metrics,"
                " sum(EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id AND m.from_text = 0)) AS from_files,"
                " sum(EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id) AND NOT EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id AND m.from_text = 0)) AS text_only,"
                " sum(e.kind IN ('verdict', 'result', 'experiment')) AS outcome,"
                " sum(e.kind IN ('verdict', 'result', 'experiment') AND EXISTS (SELECT 1 FROM metric m WHERE m.entry = e.id)) AS outcome_with"
                " FROM entry e GROUP BY 1 ORDER BY entries DESC"):
            d = dict(r)
            d.update(dict(self.c.execute(
                "SELECT count(*) AS rows, count(m.side) AS side, count(m.uni) AS uni, count(m.win) AS win, count(m.usd) AS usd,"
                " count(m.mean_usd) AS mean_usd, count(m.sharpe) AS sharpe, count(m.verdict) AS verdict, count(m.same_data) AS same_data,"
                " sum(m.from_text) AS from_text FROM metric m JOIN entry e ON e.id = m.entry WHERE coalesce(e.project, '—') = ?", (d["project"],)).fetchone()))
            out.append(d)
        return out

    def entries_for_metrics(self):
        """Записи для metrics-import: поля, текст, файлы из ссылок."""
        out = []
        for r in self.c.execute("SELECT id, project, task, kind, title, body, usd, trades, sharpe, same_data, verdict, src, parsed FROM entry ORDER BY id"):
            d = dict(r)
            d["links"] = [x[0][5:] for x in self.c.execute("SELECT dst FROM link WHERE src=? AND dst LIKE 'file:%'", ("L-%d" % r["id"],))]
            out.append(d)
        return out

    def metrics_sources(self):
        return [list(r) for r in self.c.execute("SELECT DISTINCT src, src_commit FROM metric")]

    def check_db(self):
        """Внутренняя целостность: битые ссылки (кроме commit/file — их проверяет клиент по git), триггеры, FTS, снимки md."""
        issues = []
        if self.c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            issues.append("integrity_check: не ok")
        trig = {r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for t in ("entry_immutable", "entry_nodelete", "psec_locked", "psec_nodelete", "link_nodelete", "link_noupdate",
                  "psec_flag_noupdate", "psec_flag_nodelete", "metric_noupdate", "metric_nodelete", "psec_num_noupdate", "psec_num_nodelete"):
            if t not in trig:
                issues.append("нет триггера %s" % t)
        for src, dst, rel in self.c.execute("SELECT src, dst, rel FROM link").fetchall():
            for n in (src, dst):
                if self.exists(n) is False:
                    issues.append("битая ссылка: %s → %s (%s): нет %s" % (src, dst, rel, n))
        n_nodes = self.c.execute("SELECT (SELECT count(*) FROM entry) + (SELECT count(*) FROM psec)").fetchone()[0]
        n_fts = self.c.execute("SELECT count(*) FROM fts").fetchone()[0]
        if n_nodes != n_fts:
            issues.append("FTS: строк %d, узлов %d" % (n_fts, n_nodes))
        for r in self.c.execute("SELECT id, entry FROM metric WHERE entry NOT IN (SELECT id FROM entry)"):
            issues.append("число #%d — нет записи L-%d" % (r[0], r[1]))
        n_m = self.c.execute("SELECT count(*) FROM metric WHERE from_text = 0 AND src NOT LIKE 'docs/%'").fetchone()[0]
        if n_m:
            issues.append("чисел из файла вне docs/: %d" % n_m)
        for r in self.c.execute("SELECT id FROM entry WHERE corrects IS NOT NULL AND corrects NOT IN (SELECT id FROM entry)"):
            issues.append("L-%d исправляет несуществующую запись" % r[0])
        for r in self.c.execute("SELECT path, max(ver) AS ver, sha256 FROM doc GROUP BY path"):
            got = sha(self.reconstruct(r["path"], r["ver"]))
            if got != self.c.execute("SELECT sha256 FROM doc WHERE ver=?", (r["ver"],)).fetchone()[0]:
                issues.append("снимок %s не восстанавливается байт в байт" % r["path"])
        return issues


# ───────────────────────── разбор md (клиент) ─────────────────────────

ENTRY_START_RE = re.compile(r"^(?:## |\[?\d{2}\.\d{2}(?:\s+\d{1,2}:\d{2})?\s+·)")
TABLE_SEP_RE = re.compile(r"^\|[\s:|-]*-[\s:|-]*\|?\s*$")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
PRE_RE = re.compile(r"(?i)\bдо\s+(?:первого\s+|любого\s+)?(?:сч[её]та|чтения)")
# метка «после счёта» в заголовке подраздела снимает наследование «до счёта» от раздела (TK-646 v3)
AFTER_RE = re.compile(r"^(?:§\s*)?\S+\s+результат\b|после\s+сч[её]та|\(после\s+pass\)")


def first_line(s):
    return s.split("\n", 1)[0]


def split_cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells, buf, code = [], [], False
    for ch in s:
        if ch == "`":
            code = not code
        if ch == "|" and not code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def chunk_lines(text, mode):
    """mode 'journal' | 'registry' → [{kind: entry|frame, text, line, header}]; склейка text по порядку = исходный файл.

    Запись — строка таблицы (кроме шапки) или, в журнале, блок с «## …» / «ДД.ММ[ ЧЧ:ММ] · …» до следующего начала.
    Пустые строки — хвост предыдущего куска; текст после таблицы — рамка."""
    lines = text.splitlines(keepends=True)
    chunks, cur, header = [], None, None

    def start(kind, i, hdr=None):
        nonlocal cur
        cur = {"kind": kind, "lines": [], "line": i + 1, "header": hdr}
        chunks.append(cur)

    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_sep = bool(TABLE_SEP_RE.match(ln.rstrip("\n")))
        is_row = ln.startswith("|") and not is_sep
        if is_row and TABLE_SEP_RE.match(nxt.rstrip("\n")):
            header = split_cells(ln)
            start("frame", i)
        elif is_row:
            start("entry", i, header or [])
        elif mode == "journal" and ENTRY_START_RE.match(ln):
            header = None
            start("entry", i)
        elif cur is None:
            start("frame", i)
        elif ln.strip() == "" or is_sep:
            pass
        elif cur["kind"] == "entry" and cur["header"] is not None:
            header = None
            start("frame", i)
        elif cur["kind"] == "frame":
            header = None
        cur["lines"].append(ln)
    for ch in chunks:
        ch["text"] = "".join(ch.pop("lines"))
    return chunks


def split_suffix(t):
    body = t.rstrip("\n")
    return body, t[len(body):]


def parse_ts(s, default_year=2026):
    """Первая дата в строке → ГГГГ-ММ-ДД[THH:MM]+04:00. «Z»/UTC после времени → +4 ч."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        rest = s[m.end():]
    else:
        m = re.search(r"(?<![\d.])(\d{1,2})(?:[–-]\d{1,2})?\.(\d{2})(?:\.(\d{4}))?(?![\d])", s)
        if not m:
            return None
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else default_year
        rest = s[m.end():]
    try:
        dt = datetime.datetime(y, mo, d)
    except ValueError:
        return None
    t = re.match(r"\s*~?(\d{1,2}):(\d{2})(Z| UTC)?", rest)
    if not t:
        return dt.strftime("%Y-%m-%d")
    dt = dt.replace(hour=int(t.group(1)), minute=int(t.group(2)))
    if t.group(3):
        dt += datetime.timedelta(hours=4)
    return dt.strftime("%Y-%m-%dT%H:%M+04:00")


def entry_kind(title):
    u = title.upper()
    for kw, k in (("ПРЕДРЕГИСТРАЦ", "prereg"), ("ПЕРЕЗАМОРОЗК", "freeze"), ("ЗАМОРОЗК", "freeze"), ("ЗАМОРОЖЕНО", "freeze"),
                  ("ПОПРАВК", "amend"), ("ПРАВК", "amend"), ("ВЕРДИКТ", "verdict"), ("РЕЗУЛЬТАТ", "result"), ("ПРОЧИТАНО", "result"),
                  ("ЧТЕНИЕ", "result"), ("ИТОГ", "result"), ("СЧЁТ ГОТОВ", "result"), ("ОПИСАТЕЛЬНО", "result")):
        if kw in u:
            return k
    return "step"


def find_project(s):
    m = re.search(r"(?<![\w-])[ПP]-(\d{2}|TK\d+|G\d+)(?![\d])", s)
    if m:
        return "П-" + m.group(1)
    m = re.search(r"(?<![\w-])G-(\d{3})(?!\d)", s)
    return "G-" + m.group(1) if m else None


def find_task(s):
    m = re.search(r"(?<![\w-])TK-(\d+)", s)
    return "TK-" + m.group(1) if m else None


def journal_entry(ch, ctx_head=None):
    """Запись журнала / строка реестра → поля. Задача и проект — из заголовка (строки), иначе из заголовка раздела; тело только для проекта."""
    body = ch["body"]
    if ch.get("header") is not None:
        cells = split_cells(body.split("\n")[0])
        hdr = ch["header"]
        fields = {hdr[j] if j < len(hdr) and hdr[j] else "col%d" % (j + 1): v for j, v in enumerate(cells)}
        vals = list(fields.values())
        title = " · ".join(v for v in vals[:4] if v)[:240]
        ts = parse_ts(vals[0]) if vals else None
        if ts is None:
            for v in vals[1:3]:
                ts = parse_ts(v)
                if ts:
                    break
    else:
        fields = None
        title = re.sub(r"^#+\s*", "", first_line(body)).strip()[:240]
        ts = parse_ts(first_line(body)[:40])
    hyp = None
    if fields:
        for k in ("гипотеза", "вопрос", "что мерили", "эксперимент"):
            if fields.get(k):
                hyp = fields[k]
                break
    ctx = ctx_head or ""
    proj = find_project(title) or find_project(ctx) or find_project(body)
    kind = "experiment" if ch.get("mode") == "registry" else entry_kind(title)
    task = find_task(title) or find_task(ctx)
    if not task:
        tks = set(re.findall(r"(?<![\w-])TK-\d+", body))
        task = tks.pop() if len(tks) == 1 else None  # из тела — только если задача там одна
    return {"ts": ts, "task": task, "project": proj, "kind": kind, "hypothesis": hyp,
            "title": title or "(без заголовка)", "fields": fields, **parse_marks(kind, title, body, fields, proj)}


# ── разметка полей перенесённой записи: вердикт, $ на ставку 500 $, сделок, Шарп, «найдено на этих данных».
# Поле заполняется, только если текст говорит однозначно; иначе пусто, а в parsed — причина (п. 9: не изобретать).

NUM_RX = r"[−+-]?\d{1,3}(?:[  ]\d{3})+(?:,\d+)?|[−+-]?\d+(?:[,.]\d+)?"
V_LIST_RX = re.compile(r"«?работает»?\s*/\s*«?не работает»?\s*/\s*«?не ясно»?")
V_RULES = (  # (вердикт, шаблон по fold-тексту); найденное вырезается до следующих правил — отрицания раньше утверждений
    ("описательно", r"описательн\w*|не вердикт|вердикта нет"),  # во всём тексте — только в начале фразы (verdict_classes)
    ("не работает", r"не работает|(?<!не )опроверг\w*|не подтвердил\w*|не держит\w*|не годит\w*"),
    ("не ясно", r"не ясно|неясн\w*|мало данных|в шуме"),
    ("работает", r"(?<!не )\bработает\b|(?<!не )\bподтверждено\b(?!\s*0)|(?<!не )\bподтвердил\w*|\bзелен\w*|(?<!не )\bгодится\b"),
)
# без однозначного перевода в четыре значения: «не подтверждено» (не ясно или не работает), светофор реестра «красный»
# (= нижняя граница ≤ 0), отметка клеток «…» в Летописи, счёт клеток «опровергнуты 38, прошли 10»
V_SKIP_RX = re.compile(r"не подтвержд\w*|\bкрасн\w*|«[^»]{0,40}»\s+в летописи|(?:опровергнут\w*|прошл\w*|не определен\w*)\s+\d+"
                       r"|«[^»]{0,30}»\s+(?:недостиж|невозмож)\w*")
SAME_DATA_RX = re.compile(r"найден\w* (?:отбором )?на (?:этих|тех же) данных|на тех же данных|по тем же данным|найден\w* постфактум|"
                          r"выбран\w* на тех же|найдено на авг")
NO_VERDICT_KINDS = ("prereg", "amend", "freeze")


def ru_num(s):
    return float(s.replace("−", "-").replace(" ", "").replace(" ", "").replace(",", "."))


def verdict_classes(text, whole=False):
    """whole — весь текст записи, а не выделенный вывод: «описательно» считается, только если с него начинается фраза."""
    t = V_SKIP_RX.sub(" ", V_LIST_RX.sub(" ", fold(text)))
    found = {}
    for cls, pat in V_RULES:
        if whole and cls == "описательно":
            pat = r"(?:^|[.;:(]\s*)описательн\w*|не вердикт|вердикта нет"
        m = re.search(pat, t)
        if m:
            found[cls] = m.group(0)
        t = re.sub(pat, " ", t)
    return found


MONEY_RX = re.compile(r"(?<![\w$/.,])(%s)\s?\$(?!\s?/)(?!\s*(?:на сделку|в сутки|за сутки|тыс|млн|млрд))"
                      r"|([−+-]?)\$\s?(%s)(?![\d,.]*\s*(?:/|тыс|млн|млрд|на сделку|в сутки|k\b))" % (NUM_RX, NUM_RX))
NOT_SUM_RX = re.compile(r"(?:ставк\w*|номинал\w*|позици\w*|счёт|сч[её]т|лот\w*|Δ\w*|не)\s*[(=:]?\s*$")


def money_values(text):
    """Суммы в $ — не в сутки и не на сделку, не интервалы […], не тыс. / млн, не ставка / номинал / Δ."""
    t = re.sub(r"\[[^\]]*\]", " ", text)
    out = set()
    for m in MONEY_RX.finditer(t):
        if NOT_SUM_RX.search(t[max(0, m.start() - 12):m.start()]):
            continue
        out.add(ru_num(m.group(1)) if m.group(1) else ru_num((m.group(2) or "") + m.group(3)))
    return out


def clause_pairs(text):
    """Фразы, где рядом названы и число сделок, и сумма $: [(сделок, $, Шарп | None, фраза)] — числа одного объекта."""
    out = []
    for cl in re.split(r";|\.\s|\n|·", re.sub(r"\[[^\]]*\]", "", text)):  # интервал […; …] фразу не рвёт
        if re.search(r"(?i)плюсов|минусов|прибыльн|убыточн", cl):  # часть сделок по исходу — не итог объекта
            continue
        tc, mv = trade_counts(cl), money_values(cl)
        if len(tc) == 1 and len(mv) == 1:
            sv = sharpe_values(cl)
            out.append((next(iter(tc)), next(iter(mv)), next(iter(sv)) if len(sv) == 1 else None, cl.strip()))
    return out


def trade_counts(text):
    return {int(ru_num(m.group(1))) for m in
            re.finditer(r"(?<!из )(?<![\d,.])(\d{1,3}(?:[  ]\d{3})+|\d+)\s+сдел(?:ок|ки|ка)\b(?!\s+в сутки)", text)}


def sharpe_values(text):
    return {ru_num(m.group(1)) for m in re.finditer(r"(?i)(?:sharpe|шарп\w*)(?:\s+годовой)?\s*[=:]?\s*(%s)(?![\d-])" % NUM_RX, text)}


def parse_marks(kind, title, body, fields, project):
    """Поля записи из текста: verdict, usd, trades, sharpe, same_data и parsed = {поле: откуда взято | «—: почему пусто»}."""
    why, out = {}, {}
    if kind in NO_VERDICT_KINDS:
        why["verdict"] = "—: вид записи без вердикта (%s)" % kind
    elif fields and "что мерили" in fields:
        why["verdict"] = "—: замер (строка M реестра), не вердикт"
    elif "не вердикт" in fold(title) or "ОПИСАТЕЛЬНО" in title or re.search(r"(?:\(|;\s)описательн", title):  # не «D 20 — описательно»
        out["verdict"], why["verdict"] = "описательно", "заголовок: " + title[:120]
    else:
        src = body
        if fields:
            cell = (fields.get("вердикт") or "").strip("* ")
            src = cell if cell not in ("—", "") else (fields.get("результат") or "")
        bold = " · ".join(re.findall(r"\*\*(.+?)\*\*", src, re.S))
        vc = verdict_classes(bold) if bold else {}
        if len(vc) != 1:  # выделенное жирным — главный вывод; если там вердикт не один или его нет — весь текст
            vc = verdict_classes(src, whole=True)
        if len(vc) == 1:
            (out["verdict"], q), = vc.items()
            why["verdict"] = "«%s»" % q
        else:
            why["verdict"] = ("—: несколько вердиктов (стороны / клетки): " + ", ".join("%s «%s»" % kv for kv in vc.items())
                              if vc else "—: вердикт словами не назван")
    # Числа — только одного объекта: сделок и $ в одной фразе главного вывода (жирное; у заморозки — весь текст), фраза одна.
    # $ на ставку 500 $: движок П-42 (номинал 500 $, trade.rs NOTIONAL_U) или реестр «P&L $1000» × 0,5; иные ставки не пересчитываются.
    text = body if not fields else " | ".join(v for k, v in fields.items() if k not in ("n", "P&L $1000"))
    bold = " · ".join(re.findall(r"\*\*(.+?)\*\*", text, re.S))
    pairs = {(t, u, s): cl for t, u, s, cl in clause_pairs(bold)}
    where = "жирный вывод"
    if not pairs and kind == "freeze":
        pairs, where = {(t, u, s): cl for t, u, s, cl in clause_pairs(text)}, "заморожено"
    pnl = (fields or {}).get("P&L $1000")
    n_cell = (fields or {}).get("n")
    if pnl is not None and re.fullmatch(r"\W*(?:[−+-]?\$\s?%s|%s\s?\$)\W*(?:\([^()]*\))?\s*" % (NUM_RX, NUM_RX), pnl):
        out["usd"], why["usd"] = round(next(iter(money_values(pnl))) / 2, 2), "P&L $1000 × 0,5: " + pnl[:120]
        if n_cell is not None and re.fullmatch(r"\s*\d+\s*", n_cell):
            out["trades"], why["trades"] = int(n_cell), "ячейка n"
        else:
            why["trades"] = "—: ячейка n — не одно число (стороны / наборы)"
    elif len(pairs) == 1 and (project == "П-42" or pnl is not None):
        (t, u, s), cl = next(iter(pairs.items()))
        out["trades"], out["usd"] = t, (round(u / 2, 2) if pnl is not None else u)
        why["trades"] = why["usd"] = "%s: %s" % (where, cl[:160])
        if s is not None:
            out["sharpe"], why["sharpe"] = s, why["usd"]
    else:
        r = ("—: ставка не названа (не движок П-42)" if pairs and project != "П-42" else
             "—: в главном выводе сделок и $ одного объекта %d" % len(pairs) if pairs else
             "—: в главном выводе нет фразы «N сделок … $» (числа по сторонам / клеткам / срезам или их нет)")
        why["usd"] = why["trades"] = r
    if "sharpe" not in why:
        why["sharpe"] = "—: Шарп не назван рядом с числами главного вывода"
    m = SAME_DATA_RX.search(fold(body))  # по словам текста; «нет» из молчания не выводится
    if m and re.match(r"\s*:\s*нет\b", fold(body)[m.end():]):
        out["same_data"], why["same_data"] = 0, "«%s: нет»" % m.group(0)
    elif m:
        out["same_data"], why["same_data"] = 1, "«%s»" % m.group(0)
    else:
        why["same_data"] = "—: в тексте не сказано"
    out["parsed"] = why
    return out


def section_chunks(text):
    """Протокол → [preamble frame?] + разделы по заголовкам вне блоков кода; склейка = исходный файл."""
    lines = text.splitlines(keepends=True)
    chunks, cur, fence = [], None, None
    for i, ln in enumerate(lines):
        fm = FENCE_RE.match(ln)
        if fm:
            fence = None if fence == fm.group(1) else (fence or fm.group(1))
        hm = HEAD_RE.match(ln.rstrip("\n")) if fence is None and not fm else None
        if hm:
            cur = {"kind": "psec", "lines": [], "line": i + 1, "level": len(hm.group(1)), "heading": hm.group(2)}
            chunks.append(cur)
        elif cur is None:
            cur = {"kind": "frame", "lines": [], "line": i + 1}
            chunks.append(cur)
        cur["lines"].append(ln)
    for ch in chunks:
        ch["text"] = "".join(ch.pop("lines"))
    return chunks


SEC_KINDS = (("правило вердикта", "verdict_rule"), ("вердикт", "verdict_rule"), ("ворот", "gates"), ("гейт", "gates"),
             ("бюджет испытаний", "alpha"), ("шум", "alpha"), ("множественност", "alpha"), ("α", "alpha"), ("альф", "alpha"),
             ("правил", "rules"), ("определени", "rules"), ("оси", "rules"), ("метрик", "rules"), ("данн", "data"), ("вселенн", "data"),
             ("гипотез", "hypothesis"), ("вопрос", "hypothesis"), ("отступлени", "amendment"), ("поправк", "amendment"), ("правк", "amendment"))


def section_meta(heading, body):
    m = re.match(r"^(?:§\s*)?(\d+(?:\.\d+)*[a-zа-я]?)[.\s)]", heading + " ")  # «11a.» — тоже номер
    hl = fold(heading)
    kind = next((k for kw, k in SEC_KINDS if kw in hl), "other")
    am = re.search(r"α\s*(?:=|≤|<)\s*([0-9][0-9,.]*(?:\s*·\s*10\S*)?)", body)
    pre = bool(PRE_RE.search(heading) or PRE_RE.search(body[:600]))
    at = None
    if pre:
        mm = re.search(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", heading) or re.search(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", body[:600])
        if mm:
            try:
                d = datetime.datetime(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
                at = d.strftime("%Y-%m-%d") + ("T%02d:%s+04:00" % (int(mm.group(4)), mm.group(5)) if mm.group(4) else "")
            except ValueError:
                at = None
    hm = HEAD_RE.match(first_line(body))
    return {"num": m.group(1) if m else None, "kind": kind, "alpha": am.group(1).strip() if am else None,
            "prereg": 1 if pre else 0, "prereg_at": at, "level": len(hm.group(1)) if hm else None}


def protocol_short(stem):
    m = re.match(r"^P-([0-9]{2}|TK\d+|G\d+)", stem)
    return "P-" + m.group(1) if m else stem


REF_PATTERNS = (
    re.compile(r"(?<![\w-])(TK-\d+)"),
    re.compile(r"(?<![\w-])(G-\d{3})(?!\d)"),
    re.compile(r"(?<![\w-])([ВS]-\d+)(?!\d)"),
)
PATH_RE = re.compile(r"(?<![\w/.-])((?:docs|tools|kb|src|study|\.claude)/[\w./@{}-]*\w)")
SHA_IN_TEXT_RE = re.compile(r"`([0-9a-f]{7,40})`|(?:коммит[а-я]*|commit|ветк[а-я]* \S+ на|на)\s+`?([0-9a-f]{7,40})`?")
SEC_REF_RE = re.compile(r"(?:([ПP])-(\d{2}|TK\d+)\s*(?:v\d+\s*)?)?§\s*(\d+(?:\.\d+)*\d|\d+)")


def extract_refs(text, ctx_proto, kb, entry_date):
    """Упоминания в тексте → кандидаты ссылок (проверяются потом: kb — по датам решений, git — по объектам, § — в БД)."""
    refs, shas, paths = [], set(), set()
    for rx in REF_PATTERNS:
        for m in rx.finditer(text):
            refs.append(m.group(1))
    for m in re.finditer(r"(?<![\w-])[ПP]-(\d{2})(?!\d)", text):
        refs.append("P-" + m.group(1))
    for m in SEC_REF_RE.finditer(text):
        proto = ("P-" + m.group(2)) if m.group(2) else ctx_proto
        if proto:
            refs.append(("psec-ref", proto, m.group(3)))
    for m in SHA_IN_TEXT_RE.finditer(text):
        s = m.group(1) or m.group(2)
        if s and re.search(r"[a-f]", s) and re.search(r"\d", s):
            shas.add(s)
    for m in PATH_RE.finditer(text):
        p = re.sub(r"[.,:;)]+$", "", m.group(1))
        if "{" not in p and "*" not in p:
            paths.add(p)
    out = []
    for r in refs:
        if isinstance(r, tuple):
            out.append(r)
            continue
        if r.startswith(("В-", "S-")):
            out.append(decision_ref(r, kb, entry_date))
        else:
            out.append(r)
    return [r for r in out if r], shas, paths


def decision_ref(r, kb, entry_date):
    """«В-N» в тексте: карточка В-N, если решение принято не позже записи; иначе S-N (старый реестр SETTLED) при том же условии."""
    num = r.split("-", 1)[1]
    for cand in ([r] if r.startswith("S-") else [r, "S-" + num]):
        c = kb.get(cand)
        if c and (not entry_date or not c[4] or c[4] <= entry_date[:10]):
            return cand
    return None


# ───────────────────────── git и kb (клиент) ─────────────────────────

def repo_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, cwd=str(HERE))
        if out.returncode == 0:
            return Path(out.stdout.strip())
    except OSError:
        pass
    return None


class Git:
    def __init__(self, root):
        self.root = root
        self._files = None

    def _run(self, args, inp=None):
        return subprocess.run(["git"] + args, cwd=str(self.root), capture_output=True, input=inp, encoding="utf-8")

    def commits(self, shas):
        """→ {sha: True/False}: объект-коммит есть в репозитории."""
        shas = sorted(set(shas))
        if not shas:
            return {}
        r = self._run(["cat-file", "--batch-check=%(objecttype)"], "\n".join(s + "^{commit}" for s in shas) + "\n")
        res = r.stdout.splitlines()
        return {s: (i < len(res) and res[i].strip() == "commit") for i, s in enumerate(shas)}

    def files(self):
        if self._files is None:
            fs = set()
            for rev in ("HEAD", "master"):
                r = self._run(["ls-tree", "-r", "--name-only", "-z", rev])
                if r.returncode == 0:
                    fs.update(x for x in r.stdout.split("\0") if x)
            self._files = fs
            self._dirs = {str(Path(f).parent.as_posix()) for f in fs}
            more = set()
            for d in self._dirs:
                p = d
                while "/" in p:
                    p = p.rsplit("/", 1)[0]
                    more.add(p)
            self._dirs |= more
        return self._files

    def file_ok(self, p):
        p = p.split("#", 1)[0].rstrip("/")
        p = re.sub(r":\d+(-\d+)?$", "", p)
        return p in self.files() or p in self._dirs or (self.root / p).exists()


def load_kb_nodes(root):
    """Карточки kb через tools/kb.py → [(id, type, status, title, date, path)]."""
    sys.path.insert(0, str(root / "tools"))
    try:
        import kb as kbmod
    finally:
        sys.path.pop(0)
    k = kbmod.Kb(root / "kb")
    cards, _ = kbmod.load_cards(k)
    return [(c.id, c.type, c.status, c.title, c.date, c.path) for c in cards]


DEFAULT_SOURCES = ("docs/research/JOURNAL.md", "docs/plan/EXPERIMENTS.md")


ARCHIVE_DIR = "docs/archive/chronicle-2026-11-20"
STUB_MARK = "<!-- chron-stub -->"


def archive_map(root, paths):
    """Файл-заглушка (после переноса) → читать снимок из docs/archive/chronicle-2026-11-20/<имя>."""
    out = {}
    for rel in paths:
        f = root / rel
        if f.exists() and STUB_MARK in f.read_text(encoding="utf-8")[:2000]:
            out[rel] = "%s/%s" % (ARCHIVE_DIR, Path(rel).name)
    return out


def protocol_files(root):
    return sorted(p.relative_to(root).as_posix() for p in (root / "docs" / "research").glob("P-*.md"))


def build_docs(root, paths, kb_nodes, git, archive_map=None):
    """Файлы → документы для import_docs: разбор, ссылки (проверенные по git и kb; § — разрешит БД). archive_map: путь в БД → откуда читать."""
    kb = {n[0]: n for n in kb_nodes}
    docs, all_shas = [], set()
    pending = []
    for rel in paths:
        src = (archive_map or {}).get(rel, rel)
        raw = (root / src).read_bytes().replace(b"\r\n", b"\n")  # канон — блоб git (LF); CRLF — след checkout с autocrlf
        text = raw.decode("utf-8")
        d = {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "chunks": []}
        is_proto = Path(rel).name.startswith("P-")
        if is_proto:
            stem = Path(rel).stem
            short = protocol_short(stem)
            title = next((HEAD_RE.match(l).group(2) for l in text.splitlines() if HEAD_RE.match(l)), stem)
            d["protocol"] = {"id": stem, "short": short, "title": title, "main": False}
            raws = section_chunks(text)
            ctx = short
        else:
            mode = "journal" if rel.endswith("JOURNAL.md") else "registry"
            raws = chunk_lines(text, mode)
            for ch in raws:
                ch["mode"] = mode
            ctx = None
        ctx_head = None
        nums = []  # стек (уровень, номер) — номер подраздела «7.» внутри §13.28 → «13.28 п. 7»
        for ch in raws:
            if ch["kind"] == "frame":
                d["chunks"].append({"kind": "frame", "text": ch["text"]})
                heads = [l for l in ch["text"].splitlines() if l.startswith("#")]
                if heads:
                    ctx_head = heads[-1]
                continue
            if is_proto:
                body = ch["text"]
                suffix = ""
                sec = dict(section_meta(ch["heading"], body), heading=ch["heading"], level=ch["level"])
                while nums and nums[-1][0] >= ch["level"]:
                    nums.pop()
                parent = next((x[1] for x in reversed(nums) if x[1]), None)
                if sec["num"] and parent and "." in parent and not sec["num"].startswith(parent.split(" ")[0] + ".") and "." not in sec["num"]:
                    sec["num"] = "%s п. %s" % (parent.split(" ")[0], sec["num"])
                pre_parent = nums[-1] if nums and nums[-1][2] else None
                if pre_parent and not sec["prereg"] and not AFTER_RE.search(fold(ch["heading"])):
                    sec["prereg"], sec["prereg_at"] = 1, pre_parent[3]  # подраздел раздела «до счёта» — тоже до счёта, если не помечен «после счёта»
                elif pre_parent and sec["prereg"] and not sec["prereg_at"]:
                    sec["prereg_at"] = pre_parent[3]  # своя метка «до счёта» без даты — дата раздела (§13.28 п. 8, TK-647)
                nums.append((ch["level"], sec["num"], sec["prereg"], sec["prereg_at"]))
                out = {"kind": "psec", "body": body, "suffix": suffix, "line": ch["line"], "sec": sec}
                date = sec["prereg_at"]
            else:
                body, suffix = split_suffix(ch["text"])
                ch["body"] = body
                e = journal_entry(ch, ctx_head if ch["mode"] == "registry" else None)
                out = {"kind": "entry", "body": body, "suffix": suffix, "line": ch["line"], "entry": e}
                date = e["ts"]
                ctx_e = e["project"].replace("П-", "P-") if e["project"] else None
            refs, shas, fpaths = extract_refs(body, ctx if is_proto else ctx_e, kb, date)
            all_shas |= shas
            pending.append((out, refs, shas, fpaths))
            d["chunks"].append(out)
        docs.append(d)
    ok = git.commits(all_shas) if git else {}
    for out, refs, shas, fpaths in pending:
        links = list(refs)
        links += ["commit:" + s for s in sorted(shas) if ok.get(s)]
        if git:
            links += ["file:" + p for p in sorted(fpaths) if git.file_ok(p)]
        out["links"] = [list(r) if isinstance(r, tuple) else r for r in links]
        out["unverified"] = len(shas) - sum(1 for s in shas if ok.get(s)) + (len(fpaths) - sum(1 for p in fpaths if git and git.file_ok(p)))
    # главный файл протокола — самый короткий путь среди файлов с тем же P-NN
    by_short = {}
    for d in docs:
        if d.get("protocol"):
            by_short.setdefault(d["protocol"]["short"], []).append(d)
    for short, ds in by_short.items():
        ds.sort(key=lambda d: (len(d["path"]), d["path"]))
        ds[0]["protocol"]["main"] = True
    return docs


# ───────────────────────── вызов хранилища: локально или на srv по ssh ─────────────────────────

def local_db():
    p = os.environ.get("CHRON_DB")
    if p:
        return p
    return SRV_DB if Path(SRV_DB).parent.is_dir() else None  # на srv каталог /data/chron есть всегда (chron_deploy.sh)


def ssh_cmd():
    home = Path.home()
    key = os.environ.get("CHRON_SSH_KEY") or (home / ".ssh" / "id_rsa").as_posix()
    kh = os.environ.get("CHRON_KNOWN_HOSTS") or (home / ".ssh" / "known_hosts").as_posix()
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "UserKnownHostsFile=" + kh, "-i", key,
            os.environ.get("CHRON_HOST", SRV_HOST)]


def call(op, *args, **kw):
    """Метод Store: в этом процессе (БД рядом) или этим же файлом на srv по ssh (stdin = вызов + исходник)."""
    db = local_db()
    if db:
        conn = connect(db, ro=op in RO_OPS)
        try:
            return getattr(Store(conn), op)(*args, **kw)
        finally:
            conn.close()
    payload = base64.b64encode(json.dumps({"op": op, "args": args, "kw": kw}, ensure_ascii=False).encode("utf-8")).decode()
    src = Path(__file__).read_text(encoding="utf-8")
    prog = "RPC = %r\n" % payload + src
    r = subprocess.run(ssh_cmd() + ["CHRON_RPC=1 python3 -"], input=prog.encode("utf-8"), capture_output=True)
    out = r.stdout.decode("utf-8", "replace")
    m = re.search(r"^CHRON-RESULT (.*)$", out, re.M)
    if not m:
        raise ChronError("srv: нет ответа (код %d): %s" % (r.returncode, (r.stderr.decode("utf-8", "replace") or out)[-800:]))
    res = json.loads(m.group(1))
    if "error" in res:
        raise ChronError(res["error"])
    return res["ok"]


RO_OPS = {"query", "search", "links_of", "get", "protocol_text", "protocols", "facets", "links_all", "stats", "fields_report", "check_db", "reconstruct",
          "exists", "label", "metrics_query", "metrics_facets", "metrics_coverage", "metrics_sources", "entries_for_metrics"}


def rpc_main():
    req = json.loads(base64.b64decode(globals()["RPC"]).decode("utf-8"))
    os.environ.setdefault("CHRON_DB", SRV_DB)
    try:
        if req["op"] not in RO_OPS | {"add_entry", "add_psec", "edit_psec", "lock_psec", "flag_psec", "num_psec", "add_metrics", "sync_kb", "import_docs",
                                      "new_protocol"}:
            raise ChronError("операция %s недоступна" % req["op"])
        res = {"ok": call(req["op"], *req["args"], **req["kw"])}
    except (ChronError, sqlite3.Error) as e:
        res = {"error": str(e)}
    sys.stdout.write("CHRON-RESULT " + json.dumps(res, ensure_ascii=False, default=str) + "\n")
    return 0


# ───────────────────────── команды ─────────────────────────

def author():
    return os.environ.get("CHRON_AUTHOR") or os.environ.get("BB_THREAD_ID") or os.environ.get("USER") or os.environ.get("USERNAME")


def read_body(a):
    if getattr(a, "body_file", None):
        return sys.stdin.read() if a.body_file == "-" else Path(a.body_file).read_text(encoding="utf-8")
    return getattr(a, "body", None) or ""


def verify_git_links(links):
    """commit:/file: из ввода — проверить по git до записи (битая → отказ)."""
    root = repo_root()
    need = [canon_ref(l) for l in links]
    gitl = [n for n in need if isinstance(n, str) and node_kind(n) in ("commit", "file")]
    if not gitl:
        return
    if not root:
        raise ChronError("ссылки на коммит/файл проверяются по git — запусти из репозитория")
    g = Git(root)
    ok = g.commits([n[7:] for n in gitl if n.startswith("commit:")])
    bad = [n for n in gitl if (n.startswith("commit:") and not ok.get(n[7:])) or (n.startswith("file:") and not g.file_ok(n[5:]))]
    if bad:
        raise ChronError("нет в git: %s" % ", ".join(bad))


def metrics_module():
    sys.path.insert(0, str(HERE))
    try:
        import chron_metrics
        return chron_metrics
    finally:
        sys.path.pop(0)


def parse_metrics(args):
    """--metric … → строки чисел; источник проверяется по git (файл есть, коммит есть; без коммита — последний коммит файла)."""
    if not args:
        return []
    cm = metrics_module()
    rows = []
    for x in args:
        try:
            rows.append(cm.parse_metric_arg(x))
        except (ValueError, KeyError) as e:
            raise ChronError(str(e) if str(e) else "--metric: неверное значение в «%s»" % x)
    root = repo_root()
    if not root:
        raise ChronError("--metric проверяется по git — запусти из репозитория")
    g, repo = Git(root), cm.Repo(root)
    for r in rows:
        path = re.sub(r":\d+(-\d+)?$", "", r["src"])
        if not g.file_ok(path):
            raise ChronError("--metric: файла нет в git: %s" % r["src"])
        if r["src_commit"] and not g.commits([r["src_commit"]]).get(r["src_commit"]):
            raise ChronError("--metric: коммита нет: %s" % r["src_commit"])
        r["src_commit"] = r["src_commit"] or repo.commit(path)
        if not path.endswith(cm.DATA_EXT) and not r["from_text"]:
            raise ChronError("--metric: %s — не файл результатов (JSON / CSV); число из текста — с text=1" % r["src"])
    return rows


def cmd_add(a):
    links = list(a.link or [])
    verify_git_links(links)
    metrics = parse_metrics(a.metric)
    e = {"ts": a.ts, "task": a.task, "project": a.project.replace("P-", "П-", 1) if a.project else None, "kind": a.kind,
         "hypothesis": a.hyp, "verdict": a.verdict, "usd": a.usd, "trades": a.trades, "sharpe": a.sharpe,
         "same_data": 1 if a.same_data else (0 if a.not_same_data else None), "title": a.title, "body": read_body(a),
         "corrects": a.corrects}
    print(call("add_entry", e, [canon_ref(l) if not isinstance(canon_ref(l), tuple) else list(canon_ref(l)) for l in links], author(), metrics))
    return 0


SIDE_RU = {"long": "лонг", "short": "шорт", "both": "обе"}


def fmt_num(v, nd=2):
    return "—" if v is None else ("%.*f" % (nd, v)).replace("-", "−")


def fmt_metric_row(m, with_entry=True):
    return "%s%-5s %-22s %-6s %-9s %6s %9s %8s %6s %-11s %s%s%s%s" % (
        ("L-%-5s " % m["entry"]) if with_entry else "", SIDE_RU.get(m["side"], "—"), (m["cell"] or "—")[:22], m["uni"] or "—", (m["win"] or "—")[:9],
        m["trades"] if m["trades"] is not None else "—", fmt_num(m["usd"]), fmt_num(m["mean_usd"], 3), fmt_num(m["sharpe"]), m["verdict"] or "—",
        "найдено на этих данных " if m["same_data"] == 1 else "", "«из текста» " if m["from_text"] else "", m["src"],
        ("@%s" % m["src_commit"] if m["src_commit"] else "") + ("#%s" % m["src_ptr"] if m["src_ptr"] else ""))


def metric_filters(a):
    f = {k: getattr(a, k, None) for k in ("entry", "project", "side", "uni", "win", "cell", "verdict", "same_data", "text", "min_trades", "src", "sort")}
    f["limit"] = a.n
    return f


def cmd_metrics(a):
    if a.coverage:
        cov = call("metrics_coverage")
        print("%-9s %6s %9s %8s %9s %8s %7s | строк: сторона вселенная окно $ $/сд Шарп вердикт найдено «из текста»" % (
            "проект", "записей", "с числами", "из файла", "из текста", "с итогом", "с числ."))
        for c in cov:
            print("%-9s %6d %9d %8d %9d %8d %7d | %5d: %d %d %d %d %d %d %d %d %d" % (
                c["project"], c["entries"], c["with_metrics"], c["from_files"], c["text_only"], c["outcome"], c["outcome_with"], c["rows"],
                c["side"], c["uni"], c["win"], c["usd"], c["mean_usd"], c["sharpe"], c["verdict"], c["same_data"], c["from_text"] or 0))
        t = {k: sum(c[k] or 0 for c in cov) for k in ("entries", "with_metrics", "from_files", "text_only", "outcome", "outcome_with", "rows")}
        print("всего: записей %(entries)d, с числами %(with_metrics)d (из файла %(from_files)d, только из текста %(text_only)d); "
              "с итогом (verdict / result / experiment) %(outcome)d, из них с числами %(outcome_with)d; строк %(rows)d" % t)
        return 0
    res = call("metrics_query", metric_filters(a))
    for m in res["rows"]:
        print(fmt_metric_row(m))
    print("— %d из %d" % (len(res["rows"]), res["total"]), file=sys.stderr)
    return 0


def entry_text_metrics(ents, root):
    """Числа, разобранные из текста записи при переносе (entry.usd / trades / sharpe, TK-646) → строка «из текста», источник — строка снимка."""
    out = []
    for e in ents:
        if e["usd"] is None and e["trades"] is None:
            continue
        m = re.match(r"^(.*):(\d+)$", e["src"] or "")
        if not m:
            continue
        f = root / m.group(1)
        path = "%s/%s" % (ARCHIVE_DIR, Path(m.group(1)).name) if f.exists() and STUB_MARK in f.read_text(encoding="utf-8")[:2000] else m.group(1)
        why = json.loads(e["parsed"] or "{}").get("usd", "")
        r = {"entry": e["id"], "side": None, "cell": "итог записи", "uni": None, "win": None, "trades": e["trades"], "usd": e["usd"],
             "mean_usd": (e["usd"] / e["trades"]) if e["usd"] is not None and e["trades"] else None, "sharpe": e["sharpe"], "verdict": e["verdict"],
             "same_data": e["same_data"], "from_text": 1, "src": path, "src_commit": None, "src_ptr": "строка %s" % m.group(2),
             "extra": {"parsed": why[:200]} if why else None}
        r["h"] = sha(json.dumps([e["id"]] + [r.get(k) for k in METRIC_COLS], ensure_ascii=False, default=str))
        out.append(r)
    return out


def cmd_metrics_import(a):
    """Числа записей из файлов результатов в git (chron_metrics) → таблица metric на srv; повтор не дублирует."""
    root = repo_root()
    if not root:
        raise ChronError("metrics-import — из репозитория")
    cm = metrics_module()
    ents = call("entries_for_metrics")
    rows, _ = cm.build(root, ents)
    rows += entry_text_metrics(ents, root)
    if a.entry:
        keep = {int(x.lstrip("LЛ-")) for x in a.entry}
        rows = [r for r in rows if r["entry"] in keep]
    by = {}
    for r in rows:
        by.setdefault(r["entry"], [0, 0])[r["from_text"]] += 1
    print("строк %d (из файлов %d, «из текста» %d), записей %d, файлов %d" % (
        len(rows), sum(v[0] for v in by.values()), sum(v[1] for v in by.values()), len(by), len({r["src"] for r in rows})))
    if a.dry_run:
        for eid in sorted(by):
            print("  L-%-5d из файлов %5d  из текста %4d" % (eid, by[eid][0], by[eid][1]))
        return 0
    res = {"new": 0, "same": 0}
    for i in range(0, len(rows), 4000):  # частями: один ssh-вызов не раздувается
        x = call("add_metrics", rows[i:i + 4000], author())
        res["new"] += x["new"]
        res["same"] += x["same"]
    print("записано новых %d, уже были %d" % (res["new"], res["same"]))
    return 0


def fmt_entry_row(r):
    v = r.get("verdict") or "—"
    corr = " → исправлена L-%s" % r["corrected_by"] if r.get("corrected_by") else ""
    return "L-%-5s %-17s %-7s %-7s %-11s %s%s" % (r["id"], (r.get("ts") or "")[:16], r.get("project") or "", r.get("task") or "", v, r["title"][:110], corr)


def filters(a):
    return {"q": getattr(a, "query", None), "project": a.project.replace("P-", "П-", 1) if a.project else None, "task": a.task,
            "verdict": a.verdict, "kind": a.kind, "since": a.since, "until": a.until, "limit": a.n}


def cmd_find(a):
    res = call("query", filters(a))
    for r in res["rows"]:
        print(fmt_entry_row(r))
    if a.sections:
        for n in call("search", a.query, ["PS"], a.n):
            print("%-8s %s" % (n, call("label", n)))
    print("— %d из %d" % (len(res["rows"]), res["total"]), file=sys.stderr)
    return 0


cmd_list = cmd_find


def cmd_show(a):
    r = call("get", canon_ref(a.id) if not isinstance(canon_ref(a.id), tuple) else list(canon_ref(a.id)))
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    n = r["node"]
    if "entry" in r:
        e = r["entry"]
        print("%s · %s · %s · %s · вид %s · вердикт %s" % (n, e["ts"] or "без даты", e["project"] or "—", e["task"] or "—", e["kind"], e["verdict"] or "—"))
        nums = [("$", e["usd"]), ("сделок", e["trades"]), ("Шарп", e["sharpe"])]
        if any(v is not None for _, v in nums) or e["same_data"] is not None:
            print("  " + "  ".join("%s %s" % (k, v) for k, v in nums if v is not None) + ("  найдено на этих данных" if e["same_data"] == 1 else ""))
        if e["hypothesis"]:
            print("гипотеза: " + e["hypothesis"])
        if e["corrects"]:
            print("исправляет L-%d" % e["corrects"])
        if r["corrected_by"]:
            print("исправлена: " + ", ".join("L-%d" % x for x in r["corrected_by"]))
        print("источник: %s · автор %s · записано %s" % (e["src"] or "chron add", e["author"] or "—", e["created"]))
        print()
        print(e["body"] or e["title"])
        if r.get("metrics"):
            print("\nчисла (%d из %d; все — `chron.py metrics --entry %s`):" % (len(r["metrics"]), r["metrics_total"], n))
            for m in r["metrics"]:
                print("  " + fmt_metric_row(m, with_entry=False))
    elif "psec" in r:
        s = r["psec"]
        print("%s · %s §%s · %s%s%s" % (n, s["short"], s["num"] or "—", s["kind"], " · до счёта %s" % (s["prereg_at"] or "") if s["prereg"] else "",
                                     " · неизменяем" if s["locked"] else " · черновик"))
        if s["amends"]:
            print("поправка к PS-%d" % s["amends"])
        for f in r.get("nums", []):
            print("номер исправлен %s: «%s» — %s" % (f["created"], f["num"], f["reason"]))
        for f in r.get("flags", []):
            print("флаг исправлен %s: %s%s — %s" % (f["created"], "до счёта" if f["prereg"] else "не до счёта",
                                                  " %s" % f["prereg_at"] if f["prereg_at"] else "", f["reason"]))
        if r["amended_by"]:
            print("поправки: " + ", ".join("PS-%d" % x for x in r["amended_by"]))
        print()
        print(s["body"])
    else:
        if r.get("protocol"):
            p = r["protocol"]
            print("%s — %s (%d разделов)" % (p["id"], p["title"], len(r["sections"])))
            for s in r["sections"]:
                print("  PS-%-5d %s%s %s%s" % (s["id"], "  " * max(0, s["level"] - 2), s["num"] or "", s["heading"][:100], " [до счёта]" if s["prereg"] else ""))
        if r.get("kb"):
            k = r["kb"]
            print("kb: %s · %s · %s · %s" % (k["id"], k["type"], k["status"], k["title"]))
        for p in r.get("protocols") or []:
            print("протокол: %s — %s" % (p["id"], p["title"]))
        for e in r.get("entries") or []:
            print(fmt_entry_row(e))
    print_links(r["links"])
    return 0


def print_links(l):
    if l["out"]:
        print("\nссылки:")
        for x in l["out"]:
            print("  → %-28s %-9s %s" % (x["node"], x["rel"], x["label"][:100]))
    if l["in"]:
        print("\nобратные ссылки:")
        for x in l["in"]:
            print("  ← %-28s %-9s %s" % (x["node"], x["rel"], x["label"][:100]))


def cmd_links(a):
    ref = canon_ref(a.id)
    n = call("get", list(ref) if isinstance(ref, tuple) else ref)
    print(n["node"])
    print_links(n["links"])
    return 0


def cmd_psec(a):
    if a.psec_cmd == "add":
        verify_git_links(a.link or [])
        s = {"protocol": a.protocol, "num": a.num, "heading": a.heading, "body": read_body(a), "prereg": a.prereg,
             "amends": a.amends, "after": a.after, "level": a.level, "create_title": a.new_title}
        if not s["body"].strip():
            raise ChronError("пустой раздел")
        if s["heading"] and not s["body"].lstrip().startswith("#"):
            s["body"] = "%s %s\n\n%s" % ("#" * (a.level or 3), s["heading"], s["body"])
        if not s["body"].endswith("\n"):
            s["body"] += "\n"
        print(call("add_psec", s, [canon_ref(l) if not isinstance(canon_ref(l), tuple) else list(canon_ref(l)) for l in (a.link or [])], author()))
    elif a.psec_cmd == "edit":
        print(call("edit_psec", a.id, read_body(a)))
    elif a.psec_cmd == "lock":
        print(call("lock_psec", a.id))
    elif a.psec_cmd == "flag":
        print(call("flag_psec", a.id, a.prereg == "1", a.at, a.reason, author()))
    elif a.psec_cmd == "num":
        print(call("num_psec", a.id, a.num, a.reason, author()))
    return 0


def cmd_protocol(a):
    if a.protocol_cmd == "new":
        print(call("new_protocol", a.id, a.title))
    elif a.protocol_cmd == "show":
        sys.stdout.write(call("protocol_text", a.id)["text"])
    else:
        for p in call("protocols"):
            print("%-48s %-8s %4d разделов, до счёта %3d  %s" % (p["id"], p["short"], p["sections"], p["prereg"] or 0, p["title"][:80]))
    return 0


def sources(root, a):
    if a.paths:
        return list(a.paths)
    return list(DEFAULT_SOURCES) + protocol_files(root)


def cmd_import(a):
    root = repo_root()
    if not root:
        raise ChronError("import-md — из репозитория")
    paths = sources(root, a)
    kb_nodes = load_kb_nodes(root)
    amap = archive_map(root, paths)
    amap.update(dict(x.split("=", 1) for x in (a.read_from or [])))
    docs = build_docs(root, paths, kb_nodes, Git(root), amap)
    expect = {d["path"]: sum(1 for c in d["chunks"] if c["kind"] != "frame") for d in docs}
    if a.dry_run:
        for d in docs:
            print("%-60s строк %4d  рамок %3d" % (d["path"], expect[d["path"]], sum(1 for c in d["chunks"] if c["kind"] == "frame")))
        return 0
    st = call("import_docs", docs, [list(n) for n in kb_nodes])
    bad = 0
    tot = {"rows": 0, "new": 0, "reused": 0, "amended": 0, "links": 0, "unresolved": 0}
    for d in docs:
        s = st[d["path"]]
        for k in tot:
            tot[k] += s.get(k, 0)
        same = s["back_sha256"] == d["sha256"]
        if not same or s["rows"] != expect[d["path"]]:
            bad += 1
        if a.verbose or not same:
            print("%-60s в файле %4d → строк %4d (новых %d, совпало %d, поправок %d) ссылок %d  байт в байт: %s"
                  % (d["path"], expect[d["path"]], s["rows"], s["new"], s["reused"], s["amended"], s["links"], "да" if same else "НЕТ"))
    unres = {}
    for d in docs:
        for k, v in st[d["path"]].get("unresolved_by", {}).items():
            unres[k] = unres.get(k, 0) + v
    if unres:
        print("неразрешённые упоминания по видам: " + ", ".join("%s %d" % kv for kv in sorted(unres.items(), key=lambda kv: -kv[1])))
    unver = sum(c.get("unverified", 0) for d in docs for c in d["chunks"] if c["kind"] != "frame")
    print("перенос: файлов %d, строк %d (новых %d, совпало %d, поправок %d), ссылок %d, неразрешённых упоминаний %d, коммитов/файлов не найдено в git %d;"
          " восстановлено байт в байт %d из %d"
          % (len(docs), tot["rows"], tot["new"], tot["reused"], tot["amended"], tot["links"], tot["unresolved"], unver, len(docs) - bad, len(docs)))
    return 1 if bad else 0


def cmd_export(a):
    sys.stdout.write(call("reconstruct", a.path))
    return 0


def cmd_sync_kb(a):
    root = repo_root()
    print("kb: узлов %d" % call("sync_kb", [list(n) for n in load_kb_nodes(root)]))
    return 0


def cmd_check(a):
    """Целостность: БД (битые ссылки на записи/разделы/протоколы/карточки, триггеры, FTS, снимки) + коммиты и файлы по git. Код 1 при ошибках."""
    issues = list(call("check_db"))
    root = repo_root()
    links = call("links_all")
    gitl = [(s, d) for s, d, _ in links if node_kind(d) in ("commit", "file")]
    if root:
        g = Git(root)
        ok = g.commits([d[7:] for _, d in gitl if d.startswith("commit:")])
        for s, d in gitl:
            if (d.startswith("commit:") and not ok.get(d[7:])) or (d.startswith("file:") and not g.file_ok(d[5:])):
                issues.append("битая ссылка: %s → %s (нет в git)" % (s, d))
        msrc = call("metrics_sources")
        okc = g.commits([c for _, c in msrc if c])
        for src, c in msrc:
            if not g.file_ok(re.sub(r":\d+(-\d+)?$", "", src)):
                issues.append("число: файла нет в git: %s" % src)
            if c and not okc.get(c):
                issues.append("число: коммита нет: %s (%s)" % (c, src))
    else:
        print("chron: не в репозитории — ссылки на коммиты и файлы (%d) не проверены" % len(gitl), file=sys.stderr)
    for i in issues:
        print(i)
    print("chron check: ссылок %d, ошибок %d" % (len(links), len(issues)))
    return 1 if issues else 0


def cmd_stats(a):
    for k, v in call("stats").items():
        print("%-18s %s" % (k, v))
    return 0


def id_ranges(ids):
    out, s = [], None
    for i, x in enumerate(ids):
        if s is None:
            s = x
        if i + 1 == len(ids) or ids[i + 1] != x + 1:
            out.append("L-%d" % s if s == x else "L-%d…%d" % (s, x))
            s = None
    return ", ".join(out)


def cmd_fields(a):
    r = call("fields_report")
    print("перенесённых записей %d; заполнено: %s" % (r["total"], ", ".join("%s %d" % kv for kv in r["filled"].items())))
    for f, rs in r["why"].items():
        for reason, ids in sorted(rs.items(), key=lambda kv: -len(kv[1])):
            print("  %-9s пусто %3d — %s%s" % (f, len(ids), reason, (": " + id_ranges(ids)) if a.ids else ""))
    return 0


def cmd_backup(a):
    """Снимок БД (online backup API) + integrity_check + JSONL-выгрузка; с --box — rsync в Storage Box. Только на srv."""
    db = local_db()
    if not db:
        raise ChronError("backup — на srv (рядом с БД)")
    out = Path(a.dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    dst = out / ("chron-%s.sqlite" % stamp)
    src = connect(db, ro=True)
    tmp = sqlite3.connect(str(dst) + ".tmp")
    src.backup(tmp)
    tmp.close()
    src.close()
    chk = sqlite3.connect(str(dst) + ".tmp")
    ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
    n = chk.execute("SELECT (SELECT count(*) FROM entry), (SELECT count(*) FROM psec), (SELECT count(*) FROM link), (SELECT count(*) FROM metric)").fetchone()
    jl = out / ("chron-%s.jsonl" % stamp)
    with open(str(jl) + ".tmp", "w", encoding="utf-8") as f:
        chk.row_factory = sqlite3.Row
        for t in ("protocol", "entry", "psec", "psec_flag", "psec_num", "metric", "link", "doc", "chunk", "kb_node"):
            for r in chk.execute("SELECT * FROM %s" % t):
                f.write(json.dumps({"t": t, **dict(r)}, ensure_ascii=False) + "\n")
    chk.close()
    if ok != "ok":
        raise ChronError("снимок не прошёл integrity_check: %s" % ok)
    os.replace(str(dst) + ".tmp", str(dst))
    os.replace(str(jl) + ".tmp", str(jl))
    snaps = sorted(out.glob("chron-*.sqlite"))
    for old in snaps[:-a.keep]:
        old.unlink()
        j = old.with_suffix(".jsonl")
        if j.exists():
            j.unlink()
    print("backup: %s (записей %d, разделов %d, ссылок %d, чисел %d)" % (dst, n[0], n[1], n[2], n[3]))
    if a.box:
        r = subprocess.run(["rsync", "-a", "--delete", "-e", "ssh -F /root/.ssh/config", str(out) + "/", a.box], capture_output=True, text=True)
        if r.returncode:
            raise ChronError("rsync в Storage Box: %s" % r.stderr[-400:])
        print("box: %s" % a.box)
    return 0


# ───────────────────────── сайт (:8091, /chron/…) ─────────────────────────

def kb_module():
    sys.path.insert(0, str(HERE))
    try:
        import kb as kbmod
        return kbmod
    except ImportError:
        return None
    finally:
        sys.path.pop(0)


EXTRA_CSS = """
.ctl input[type=date]{flex:0 1 150px}
.vd{font-weight:600}.v-работает{color:var(--ok)}.v-не.работает{color:var(--bad)}
.erow{display:grid;grid-template-columns:72px 120px 1fr;gap:2px 12px;padding:8px 0;border-bottom:1px solid var(--line);align-items:baseline}
.erow .d{color:var(--mut);font-size:13px}.erow .t{min-width:0;overflow-wrap:anywhere}.erow .b{display:flex;gap:6px;flex-wrap:wrap;margin-top:3px}
.fields{border-collapse:collapse;font-size:14px;margin:.6em 0}.fields th{width:180px;white-space:normal}
.sec{border-top:1px solid var(--line);padding-top:4px;margin-top:10px}.sec .meta{margin:2px 0 4px}
.amend{border-left:3px solid #ecd3a8;padding-left:10px}
.pager{display:flex;gap:14px;margin:12px 0}
table.m{font-size:13px}table.m td,table.m th{padding:4px 7px;white-space:nowrap}table.m td.c{white-space:normal;min-width:140px;max-width:340px;overflow-wrap:anywhere}
table.m td.n{text-align:right;font-variant-numeric:tabular-nums}table.m th a{color:inherit}.neg{color:var(--bad)}.pos{color:var(--ok)}
.ft{color:var(--mut);font-size:12px}.ctl input.sm{flex:0 1 110px}
article.body{overflow-wrap:anywhere}
@media(max-width:640px){.erow{grid-template-columns:1fr auto}.erow .t{grid-column:1/-1}.fields th{width:110px}}
"""


class Site:
    def __init__(self, db, static):
        self.db, self.static = db, static
        self.kb = kb_module()

    def store(self):
        return Store(connect(self.db, ro=True))

    def page(self, title, body, cur=""):
        css = (self.kb.CSS if self.kb else "") + EXTRA_CSS
        nav = "".join('<a href="%s"%s>%s</a>' % (h, ' class="cur"' if k == cur else "", l) for k, h, l in (
            ("cards", "/index.html", "Карточки"), ("graph", "/graph.html", "Граф"), ("chron", "/chron/", "Летопись"), ("nums", "/chron/m/", "Числа"),
            ("proto", "/chron/p/", "Протоколы")))
        return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<link rel="icon" href="data:,"><title>%s</title><style>%s</style></head><body><header class="top"><b>alpha</b><nav>%s</nav></header>'
                '<main>%s</main></body></html>' % (html.escape(title), css, nav, body))

    def href(self, n, st=None):
        k = node_kind(n)
        if k == "entry":
            return "/chron/e/%s" % n[2:]
        if k == "psec":
            return "/chron/s/%s" % n[3:]
        if k == "commit":
            return "https://github.com/slayfactory/alpha/commit/%s" % n[7:]
        if k == "file":
            return "https://github.com/slayfactory/alpha/blob/master/%s" % quote(n[5:].split("#")[0])
        return "/chron/n/%s" % quote(n)

    def md(self, text, st):
        if not self.kb:
            return "<pre>%s</pre>" % html.escape(text)

        def link_for(t):
            c = canon_ref(t)
            n = st.canon(list(c)) if isinstance(c, tuple) else c
            return '<a href="%s">%s</a>' % (self.href(n or t), html.escape(t))
        try:
            return self.kb.md_to_html(text, link_for)
        except Exception:
            return "<pre>%s</pre>" % html.escape(text)

    def links_html(self, links):
        def ul(rows, arrow):
            if not rows:
                return "<p>—</p>"
            return '<ul class="links">%s</ul>' % "".join(
                '<li><a href="%s">%s</a><span>%s</span><span class="k">%s</span></li>' % (self.href(x["node"]), html.escape(x["node"]), html.escape(x["label"][:160]), html.escape(x["rel"]))
                for x in rows)
        return "<h2>Ссылки</h2>%s<h2>Обратные ссылки</h2>%s" % (ul(links["out"], "→"), ul(links["in"], "←"))

    def vbadge(self, v):
        return '<span class="badge vd v-%s">%s</span>' % (html.escape((v or "").replace(" ", ".")), html.escape(v or "не размечено"))

    def erow(self, r):
        corr = ' <span class="badge">исправлена L-%s</span>' % r["corrected_by"] if r.get("corrected_by") else ""
        if r.get("n_metrics"):
            corr += ' <a class="badge" href="/chron/m/?entry=%d">чисел %d</a>' % (r["id"], r["n_metrics"])
        return ('<div class="erow"><a class="id" href="/chron/e/%d">L-%d</a><span class="d">%s</span><span class="t">%s'
                '<span class="b">%s%s%s%s%s</span></span></div>') % (
            r["id"], r["id"], html.escape((r.get("ts") or "")[:16].replace("T", " ")), html.escape(r["title"]),
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(r["project"].replace("П-", "P-", 1)), html.escape(r["project"]))) if r.get("project") else "",
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(r["task"]), html.escape(r["task"]))) if r.get("task") else "",
            '<span class="badge">%s</span>' % html.escape(r["kind"]), self.vbadge(r.get("verdict")), corr)

    def index(self, qs):
        st = self.store()
        f = {k: (qs.get(k) or [""])[0].strip() for k in ("q", "project", "verdict", "kind", "since", "until", "task", "nums")}
        page_n = max(1, int((qs.get("page") or ["1"])[0] or 1))
        f.update(limit=100, offset=(page_n - 1) * 100)
        res = st.query(f)
        fc = st.facets()

        def sel(name, vals, first):
            return '<select name="%s" id="%s"><option value="">%s</option>%s</select>' % (name, name, first, "".join(
                '<option%s>%s</option>' % (" selected" if f[name] == v else "", html.escape(v)) for v in vals))
        ctl = ('<form class="ctl" method="get" action="/chron/"><input id="q" name="q" type="search" placeholder="поиск по тексту" value="%s">'
               '%s%s%s%s<input type="date" name="since" id="since" value="%s" title="с"><input type="date" name="until" id="until" value="%s" title="по">'
               '<input type="hidden" name="task" value="%s"><button>найти</button></form>') % (
            html.escape(f["q"]), sel("project", fc["projects"], "все проекты"), sel("verdict", fc["verdicts"] + ["—"], "любой вердикт"),
            sel("kind", fc["kinds"], "любой вид"), sel("nums", ["есть", "файл", "текст", "нет"], "числа: любые"),
            html.escape(f["since"]), html.escape(f["until"]), html.escape(f["task"]))
        rows = "".join(self.erow(r) for r in res["rows"]) or "<p>Ничего не найдено.</p>"
        q = {k: v for k, v in f.items() if v and k not in ("limit", "offset")}
        pager = ""
        if res["total"] > 100:
            pages = (res["total"] + 99) // 100
            pager = '<div class="pager">%s<span>стр. %d из %d</span>%s</div>' % (
                ('<a href="?%s">← назад</a>' % urlencode(dict(q, page=page_n - 1))) if page_n > 1 else "", page_n, pages,
                ('<a href="?%s">дальше →</a>' % urlencode(dict(q, page=page_n + 1))) if page_n < pages else "")
        body = '<h1>Летопись</h1>%s<p id="cnt">%d записей</p><div id="list">%s</div>%s' % (ctl, res["total"], rows, pager)
        return self.page("Летопись", body, "chron")

    def entry(self, n):
        st = self.store()
        r = st.get("L-%d" % n)
        e = r["entry"]
        meta = '<p class="meta">%s<span class="badge">%s</span>%s%s<span>%s</span></p>' % (
            self.vbadge(e["verdict"]), html.escape(e["kind"]),
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(e["project"].replace("П-", "P-", 1)), html.escape(e["project"]))) if e["project"] else "",
            ('<a class="badge" href="/chron/n/%s">%s</a>' % (quote(e["task"]), html.escape(e["task"]))) if e["task"] else "",
            html.escape((e["ts"] or "без даты").replace("T", " ")))
        nums = [("$ (ставка 500 $)", e["usd"]), ("сделок", e["trades"]), ("Шарп", e["sharpe"]),
                ("найдено на этих данных", {1: "да", 0: "нет"}.get(e["same_data"]))]
        tbl = "".join("<tr><th>%s</th><td>%s</td></tr>" % (k, html.escape(str(v))) for k, v in nums if v is not None)
        if e["hypothesis"]:
            tbl = "<tr><th>гипотеза</th><td>%s</td></tr>" % html.escape(e["hypothesis"]) + tbl
        notes = ""
        if e.get("parsed"):
            names = {"verdict": "вердикт", "usd": "$", "trades": "сделок", "sharpe": "Шарп", "same_data": "найдено на этих данных"}
            notes += '<details class="meta"><summary>откуда поля (разбор текста при переносе)</summary><table class="fields">%s</table></details>' % "".join(
                "<tr><th>%s</th><td>%s</td></tr>" % (names.get(k, k), html.escape(v)) for k, v in json.loads(e["parsed"]).items())
        if e["corrects"]:
            notes += '<p>Исправляет <a href="/chron/e/%d">L-%d</a>.</p>' % (e["corrects"], e["corrects"])
        if r["corrected_by"]:
            notes += "<p><b>Исправлена:</b> %s</p>" % ", ".join('<a href="/chron/e/%d">L-%d</a>' % (x, x) for x in r["corrected_by"])
        if e["fields"]:
            fl = json.loads(e["fields"])
            content = '<table class="fields">%s</table>' % "".join(
                "<tr><th>%s</th><td>%s</td></tr>" % (html.escape(k), self.kb.md_inline(v, lambda t: html.escape(t)) if self.kb else html.escape(v)) for k, v in fl.items())
        else:
            content = '<article class="body">%s</article>' % self.md(e["body"], st)
        src = '<p class="meta">%s · автор %s · записано %s</p>' % (html.escape(e["src"] or "chron add"), html.escape(e["author"] or "—"), html.escape(e["created"]))
        mt = ""
        if r["metrics_total"]:
            mt = '<h2 id="nums">Числа (%d%s)</h2>%s' % (r["metrics_total"], ", показаны первые %d" % len(r["metrics"]) if r["metrics_total"] > len(r["metrics"]) else "",
                                                     self.metrics_table(r["metrics"], with_entry=False))
            mt += '<p><a href="/chron/m/?entry=%d">все числа записи — фильтры и сортировка</a></p>' % n
        body = '%s<h1>L-%d <span style="color:var(--mut);font-weight:400">· %s</span></h1>%s%s%s%s%s%s' % (
            meta, n, html.escape(e["title"]), ('<table class="fields">%s</table>' % tbl) if tbl else "", notes, mt, content, src, self.links_html(r["links"]))
        return self.page("L-%d — %s" % (n, e["title"][:80]), body, "chron")

    def src_href(self, m):
        path = re.sub(r":\d+(-\d+)?$", "", m["src"])
        ref = m["src_commit"] or "master"
        line = re.match(r"^строка (\d+)", m["src_ptr"] or "")
        return "https://github.com/slayfactory/alpha/blob/%s/%s%s" % (quote(ref), quote(path), "#L%s" % line.group(1) if line else "")

    def metrics_table(self, rows, with_entry=True, sort_link=None):
        def th(key, label):
            if not sort_link or not key:
                return "<th>%s</th>" % label
            return '<th><a href="%s">%s</a></th>' % (html.escape(sort_link(key)), label)

        def money(v, nd=2):
            if v is None:
                return "—"
            return '<span class="%s">%s</span>' % ("neg" if v < 0 else "pos" if v > 0 else "", fmt_num(v, nd))
        head = "".join([th("entry", "запись")] if with_entry else []) + "".join(th(k, l) for k, l in (
            (None, "сторона"), (None, "клетка"), (None, "вселенная"), (None, "окно"), ("trades", "сделок"), ("usd", "Σ $ (500 $)"),
            ("mean", "$ / сделку"), ("sharpe", "Шарп"), (None, "вердикт"), (None, "найдено на этих данных"), (None, "источник")))
        body = []
        for m in rows:
            ex = json.loads(m["extra"]) if m.get("extra") else {}
            note = []
            if ex.get("usd_raw") is not None:
                note.append("$ в файле %s (ставка не названа)" % fmt_num(ex["usd_raw"]))
            if ex.get("pass") is not None:
                note.append("ворота: %s" % ("да" if ex["pass"] else "нет"))
            if ex.get("verdict_text"):
                note.append(ex["verdict_text"][:80])
            body.append("<tr>%s<td>%s</td><td class=\"c\">%s%s</td><td>%s</td><td>%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td>"
                        "<td class=\"n\">%s</td><td>%s</td><td>%s</td><td><a href=\"%s\">%s</a>%s</td></tr>" % (
                ('<td><a href="/chron/e/%d">L-%d</a></td>' % (m["entry"], m["entry"])) if with_entry else "",
                SIDE_RU.get(m["side"], "—"), html.escape(m["cell"] or "—"), ('<div class="ft">%s</div>' % html.escape("; ".join(note))) if note else "",
                html.escape(m["uni"] or "—"), html.escape(m["win"] or "—"), m["trades"] if m["trades"] is not None else "—", money(m["usd"]),
                money(m["mean_usd"], 3), fmt_num(m["sharpe"]), html.escape(m["verdict"] or "—"), {1: "да", 0: "нет"}.get(m["same_data"], "—"),
                html.escape(self.src_href(m)), html.escape(Path(re.sub(r":\d+$", "", m["src"])).name),
                ' <span class="ft">%s%s</span>' % (html.escape((m["src_ptr"] or "")[:60]), " · «из текста»" if m["from_text"] else "")))
        return '<div class="tw"><table class="m"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (head, "".join(body))

    def metrics_page(self, qs):
        st = self.store()
        keys = ("entry", "project", "side", "uni", "win", "cell", "verdict", "same_data", "text", "min_trades", "src", "sort")
        f = {k: (qs.get(k) or [""])[0].strip() for k in keys}
        f["sort"] = f["sort"] or "-date"
        page_n = max(1, int((qs.get("page") or ["1"])[0] or 1))
        q = {k: v for k, v in f.items() if v}
        res = st.metrics_query(dict(f, limit=200, offset=(page_n - 1) * 200))
        fc = st.metrics_facets()

        def sel(name, vals, first, labels=None):
            return '<select name="%s" id="m-%s"><option value="">%s</option>%s</select>' % (name, name, first, "".join(
                '<option value="%s"%s>%s</option>' % (html.escape(v), " selected" if f[name] == v else "", html.escape((labels or {}).get(v, v))) for v in vals))

        def sort_link(key):
            cur = f["sort"]
            nxt = ("-" + key) if cur != "-" + key else key
            return "?" + urlencode(dict(q, sort=nxt))
        ctl = ('<form class="ctl" method="get" action="/chron/m/">'
               '<input name="cell" id="m-cell" type="search" placeholder="клетка (подстрока)" value="%s">%s%s%s%s%s%s%s'
               '<input class="sm" name="min_trades" id="m-min" type="number" min="0" placeholder="сделок ≥" value="%s">'
               '<input class="sm" name="entry" id="m-entry" placeholder="L-N" value="%s">'
               '%s<button>показать</button></form>') % (
            html.escape(f["cell"]), sel("project", fc["projects"], "все проекты"), sel("side", ["long", "short", "both", "—"], "любая сторона", SIDE_RU),
            sel("uni", fc["uni"], "любая вселенная"), sel("win", fc["win"], "любое окно"), sel("verdict", list(VERDICTS) + ["—"], "любой вердикт"),
            sel("same_data", ["1", "0", "—"], "найдено на этих данных: всё", {"1": "найдено на этих данных", "0": "не на этих данных", "—": "не сказано"}),
            sel("text", ["0", "1"], "источник: любой", {"0": "из файлов результатов", "1": "«из текста»"}),
            html.escape(f["min_trades"]), html.escape(f["entry"]),
            sel("sort", ["-date", "-usd", "usd", "-sharpe", "sharpe", "-trades", "-mean", "mean", "entry"], "порядок",
                {"-date": "новые записи", "-usd": "Σ $ ↓", "usd": "Σ $ ↑", "-sharpe": "Шарп ↓", "sharpe": "Шарп ↑", "-trades": "сделок ↓",
                 "-mean": "$ / сделку ↓", "mean": "$ / сделку ↑", "entry": "по записи"}))
        pager = ""
        if res["total"] > 200:
            pages = (res["total"] + 199) // 200
            pager = '<div class="pager">%s<span>стр. %d из %d</span>%s</div>' % (
                ('<a href="?%s">← назад</a>' % urlencode(dict(q, page=page_n - 1))) if page_n > 1 else "", page_n, pages,
                ('<a href="?%s">дальше →</a>' % urlencode(dict(q, page=page_n + 1))) if page_n < pages else "")
        note = ('<p class="meta">Σ $ — на ставку 500 $ (движок П-42); у других проектов ставка в файле не названа — $ пуст, сумма файла в примечании. '
                'Шарп — годовой по дневному ряду. «из текста» — число из таблицы отчёта md, не из файла результатов.</p>')
        body = '<h1>Числа</h1>%s%s<p id="cnt">%d строк</p>%s%s' % (ctl, note, res["total"],
                                                                   self.metrics_table(res["rows"], True, sort_link) if res["rows"] else "<p>Ничего не найдено.</p>", pager)
        return self.page("Числа", body, "nums")

    def section(self, n):
        st = self.store()
        r = st.get("PS-%d" % n)
        s = r["psec"]
        meta = '<p class="meta"><a class="badge" href="/chron/p/%s">%s</a><span class="badge">%s</span>%s<span class="badge">%s</span>%s</p>' % (
            quote(s["protocol"]), html.escape(s["short"]), html.escape(s["kind"] or ""),
            ('<span class="badge st-done">до счёта %s</span>' % html.escape(s["prereg_at"] or "")) if s["prereg"] else "",
            "неизменяем" if s["locked"] else "черновик", (' <span>α %s</span>' % html.escape(s["alpha"])) if s["alpha"] else "")
        notes = ""
        if s["amends"]:
            notes += '<p>Поправка к <a href="/chron/s/%d">PS-%d</a>.</p>' % (s["amends"], s["amends"])
        if r["amended_by"]:
            notes += "<p><b>Поправки:</b> %s</p>" % ", ".join('<a href="/chron/s/%d">PS-%d</a>' % (x, x) for x in r["amended_by"])
        body = '%s<h1>PS-%d <span style="color:var(--mut);font-weight:400">· %s §%s</span></h1>%s<article class="body">%s</article><p class="meta">%s · записано %s</p>%s' % (
            meta, n, html.escape(s["short"]), html.escape(s["num"] or "—"), notes, self.md(s["body"], st),
            html.escape(s["src"] or "chron psec add"), html.escape(s["created"]), self.links_html(r["links"]))
        return self.page("PS-%d — %s" % (n, s["heading"][:80]), body, "proto")

    def protocols(self):
        st = self.store()
        rows = "".join('<div class="erow"><a class="id" href="/chron/p/%s">%s</a><span class="d">%d разд. · до счёта %d</span><span class="t">%s</span></div>' % (
            quote(p["id"]), html.escape(p["short"]), p["sections"], p["prereg"] or 0, html.escape(p["title"])) for p in st.protocols())
        return self.page("Протоколы", "<h1>Протоколы</h1><div id=\"list\">%s</div>" % rows, "proto")

    def protocol(self, pid):
        st = self.store()
        r = st.get(pid)
        if not r.get("protocol"):
            return None
        p = r["protocol"]
        parts = []
        for s in r["sections"]:
            full = st.get("PS-%d" % s["id"])["psec"]
            parts.append('<section class="sec%s" id="s%d"><p class="meta"><a href="/chron/s/%d">PS-%d</a>%s%s</p>%s</section>' % (
                " amend" if s["amends"] else "", s["id"], s["id"], s["id"],
                ' <span class="badge st-done">до счёта %s</span>' % html.escape(s["prereg_at"] or "") if s["prereg"] else "",
                (' <span class="badge">поправка к PS-%d</span>' % s["amends"]) if s["amends"] else "", self.md(full["body"], st)))
        toc = "".join('<li style="margin-left:%dpx"><a href="#s%d">%s</a></li>' % (max(0, s["level"] - 2) * 16, s["id"], html.escape(s["heading"][:140]))
                      for s in r["sections"] if s["level"] <= 3)
        body = '<h1>%s</h1><p class="meta"><span>%s</span><span>%d разделов</span><a href="/chron/n/%s">связи</a></p><details><summary>Оглавление</summary><ul>%s</ul></details>%s' % (
            html.escape(p["title"]), html.escape(p["id"]), len(r["sections"]), quote(p["short"]), toc, "".join(parts))
        return self.page(p["short"] + " — " + p["title"][:80], body, "proto")

    def node(self, n):
        st = self.store()
        r = st.get(n)
        parts = []
        if r.get("kb"):
            k = r["kb"]
            parts.append('<p class="meta"><a class="badge" href="/c/%s.html">карточка kb</a><span class="badge">%s</span><span class="badge st-%s">%s</span></p><h1>%s <span style="color:var(--mut);font-weight:400">· %s</span></h1>' % (
                quote(k["id"]), html.escape(k["type"]), html.escape(k["status"] or ""), html.escape(k["status"] or ""), html.escape(k["id"]), html.escape(k["title"])))
        else:
            parts.append("<h1>%s</h1>" % html.escape(n))
        for p in r.get("protocols") or []:
            parts.append('<p>Протокол: <a href="/chron/p/%s">%s</a> — %s</p>' % (quote(p["id"]), html.escape(p["id"]), html.escape(p["title"])))
        if r.get("entries"):
            parts.append("<h2>Записи летописи (%d)</h2><div>%s</div>" % (len(r["entries"]), "".join(self.erow(e) for e in r["entries"])))
        parts.append(self.links_html(r["links"]))
        return self.page(n, "".join(parts), "chron")


def make_handler(site):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=site.static, **kw)

        def log_message(self, *args):
            pass

        def send_html(self, text, code=200):
            b = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            p = unquote(u.path)
            if not p.startswith("/chron"):
                return super().do_GET()
            try:
                if p in ("/chron", "/chron/"):
                    return self.send_html(site.index(parse_qs(u.query)))
                m = re.match(r"^/chron/e/(\d+)/?$", p)
                if m:
                    return self.send_html(site.entry(int(m.group(1))))
                m = re.match(r"^/chron/s/(\d+)/?$", p)
                if m:
                    return self.send_html(site.section(int(m.group(1))))
                if p in ("/chron/p", "/chron/p/"):
                    return self.send_html(site.protocols())
                if p in ("/chron/m", "/chron/m/"):
                    return self.send_html(site.metrics_page(parse_qs(u.query)))
                m = re.match(r"^/chron/p/(.+?)/?$", p)
                if m:
                    out = site.protocol(m.group(1))
                    if out:
                        return self.send_html(out)
                m = re.match(r"^/chron/n/(.+?)/?$", p)
                if m:
                    return self.send_html(site.node(m.group(1)))
            except ChronError as e:
                return self.send_html(site.page("не найдено", "<h1>Не найдено</h1><p>%s</p>" % html.escape(str(e))), 404)
            except Exception as e:  # страница не должна ронять соединение
                return self.send_html(site.page("ошибка", "<h1>Ошибка</h1><p>%s</p>" % html.escape(repr(e))), 500)
            return self.send_html(site.page("не найдено", "<h1>Не найдено</h1>"), 404)

    return H


def cmd_serve(a):
    db = local_db()
    if not db:
        raise ChronError("serve — рядом с БД (srv) или с CHRON_DB")
    site = Site(db, a.static)
    socketserver.TCPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer((a.host, a.port), make_handler(site)) as srv:
        print("chron: http://%s:%d/chron/ (static %s)" % (a.host, a.port, a.static), flush=True)
        srv.serve_forever()
    return 0


# ───────────────────────── main ─────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(prog="chron", description="летопись выводов и протоколов (SQLite на srv)")
    sp = p.add_subparsers(dest="cmd", required=True)

    def flt(s, q=False):
        if q:
            s.add_argument("query", nargs="?" if q == "opt" else None, default="")
        s.add_argument("--project")
        s.add_argument("--task")
        s.add_argument("--verdict", help="работает | не работает | не ясно | описательно | — (не размечено)")
        s.add_argument("--kind")
        s.add_argument("--since", help="ГГГГ-ММ-ДД")
        s.add_argument("--until", help="ГГГГ-ММ-ДД")
        s.add_argument("-n", type=int, default=50)

    s = sp.add_parser("add", help="новая запись (неизменяемая)")
    s.add_argument("--title", required=True)
    s.add_argument("--kind", default="verdict", choices=KINDS)
    s.add_argument("--task")
    s.add_argument("--project", help="П-NN / G-NNN")
    s.add_argument("--hyp", help="гипотеза")
    s.add_argument("--verdict", choices=VERDICTS)
    s.add_argument("--usd", type=float, help="$ на ставку 500 $")
    s.add_argument("--trades", type=int)
    s.add_argument("--sharpe", type=float)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--same-data", action="store_true", help="найдено на этих данных")
    g.add_argument("--not-same-data", action="store_true")
    s.add_argument("--body")
    s.add_argument("--body-file", help="файл с текстом; «-» — stdin")
    s.add_argument("--link", action="append", help="L-N, PS-N, «P-42 §13.28», TK-NNN, В-NNN, карточка kb, SHA коммита, путь файла (повторяемо)")
    s.add_argument("--corrects", help="L-N: эта запись исправляет ту")
    s.add_argument("--ts", help="когда сделано (по умолчанию — сейчас, GMT+4)")
    s.add_argument("--metric", action="append", help="число записи (повторяемо): «side=long; cell=L_n24; uni=h775; win=01.07–13.11; trades=181; usd=294.31;"
                   " sharpe=5.11; verdict=не ясно; same_data=1; src=файл[@коммит][#место]»; text=1 — «из текста» (src = файл:строка)")
    s.set_defaults(fn=cmd_add)
    s = sp.add_parser("find", help="поиск по тексту + фильтры")
    flt(s, q=True)
    s.add_argument("--sections", action="store_true", help="искать и в разделах протоколов")
    s.set_defaults(fn=cmd_find)
    s = sp.add_parser("list", help="список записей по фильтрам")
    flt(s, q="opt")
    s.set_defaults(fn=cmd_list, sections=False)
    s = sp.add_parser("show", help="запись / раздел / протокол / узел + ссылки")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)
    s = sp.add_parser("links", help="ссылки узла в обе стороны")
    s.add_argument("id")
    s.set_defaults(fn=cmd_links)
    s = sp.add_parser("psec", help="разделы протоколов")
    ps = s.add_subparsers(dest="psec_cmd", required=True)
    x = ps.add_parser("add", help="новый раздел (с --prereg — сразу неизменяемый, «до счёта»)")
    x.add_argument("protocol", help="P-42 или id протокола")
    x.add_argument("--num")
    x.add_argument("--heading")
    x.add_argument("--level", type=int)
    x.add_argument("--body")
    x.add_argument("--body-file")
    x.add_argument("--prereg", action="store_true", help="записано ДО счёта: раздел неизменяем")
    x.add_argument("--amends", help="PS-N / «P-42 §13.28»: поправка к разделу")
    x.add_argument("--after", help="PS-N: поставить после раздела")
    x.add_argument("--link", action="append")
    x.add_argument("--new-title", help="создать протокол с этим заголовком, если его нет")
    x = ps.add_parser("edit", help="заменить текст черновика (не «до счёта»)")
    x.add_argument("id")
    x.add_argument("--body")
    x.add_argument("--body-file")
    x = ps.add_parser("lock", help="пометить «до счёта» — дальше только поправки")
    x.add_argument("id")
    x = ps.add_parser("num", help="исправить номер раздела (строка только добавляется; действует последняя)")
    x.add_argument("id")
    x.add_argument("--num", required=True)
    x.add_argument("--reason", required=True)
    x = ps.add_parser("flag", help="исправить флаг «до счёта» (строка только добавляется; действует последняя)")
    x.add_argument("id")
    x.add_argument("--prereg", choices=("0", "1"), required=True)
    x.add_argument("--at", help="когда записан до счёта (ГГГГ-ММ-ДД[THH:MM+04:00])")
    x.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_psec)
    s = sp.add_parser("protocol", help="протоколы: list | new | show")
    ps = s.add_subparsers(dest="protocol_cmd", required=True)
    ps.add_parser("list")
    x = ps.add_parser("new")
    x.add_argument("id")
    x.add_argument("title")
    x = ps.add_parser("show", help="протокол целиком (Markdown)")
    x.add_argument("id")
    s.set_defaults(fn=cmd_protocol)
    s = sp.add_parser("import-md", help="перенос md в БД (JOURNAL, EXPERIMENTS, P-*.md); повторный — только новое")
    s.add_argument("paths", nargs="*")
    s.add_argument("--read-from", action="append", help="путь=откуда_читать (снимок в docs/archive)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_import)
    s = sp.add_parser("export-md", help="восстановить перенесённый md из БД (байт в байт)")
    s.add_argument("path")
    s.set_defaults(fn=cmd_export)
    sp.add_parser("sync-kb", help="обновить узлы kb (карточки) в БД").set_defaults(fn=cmd_sync_kb)
    sp.add_parser("check", help="целостность и битые ссылки; код 1 при ошибках").set_defaults(fn=cmd_check)
    sp.add_parser("stats").set_defaults(fn=cmd_stats)
    s = sp.add_parser("metrics", help="числа записей (сторона × клетка × вселенная × окно): фильтры, сортировка, --coverage")
    s.add_argument("--entry", help="L-N")
    s.add_argument("--project")
    s.add_argument("--side", choices=("long", "short", "both", "—"))
    s.add_argument("--uni")
    s.add_argument("--win")
    s.add_argument("--cell", help="подстрока клетки")
    s.add_argument("--verdict", help="работает | не работает | не ясно | описательно | —")
    s.add_argument("--same-data", dest="same_data", choices=("0", "1", "—"))
    s.add_argument("--text", choices=("0", "1"), help="1 — только «из текста», 0 — только из файлов")
    s.add_argument("--min-trades", dest="min_trades", type=int)
    s.add_argument("--src", help="подстрока пути источника")
    s.add_argument("--sort", default="-date", help="usd | sharpe | trades | mean | date | entry; «-» — по убыванию")
    s.add_argument("-n", type=int, default=100)
    s.add_argument("--coverage", action="store_true", help="покрытие по проектам")
    s.set_defaults(fn=cmd_metrics)
    s = sp.add_parser("metrics-import", help="числа записей из файлов результатов в git → БД (повтор не дублирует)")
    s.add_argument("--entry", action="append", help="только эти L-N")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_metrics_import)
    s = sp.add_parser("fields", help="перенесённые записи: заполненные поля и почему пусто")
    s.add_argument("--ids", action="store_true", help="номера записей по каждой причине")
    s.set_defaults(fn=cmd_fields)
    s = sp.add_parser("backup", help="снимок БД (srv)")
    s.add_argument("--dir", default="/data/chron/backup")
    s.add_argument("--keep", type=int, default=30)
    s.add_argument("--box", help="rsync-цель, напр. box:chron/")
    s.set_defaults(fn=cmd_backup)
    s = sp.add_parser("serve", help="сайт: статика kb + /chron/ (srv, порт 8091)")
    s.add_argument("--port", type=int, default=8091)
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--static", default="/data/pages/kb")
    s.set_defaults(fn=cmd_serve)
    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        return a.fn(a)
    except (ChronError, sqlite3.Error) as e:
        print("chron: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(rpc_main() if os.environ.get("CHRON_RPC") and "RPC" in globals() else main())
