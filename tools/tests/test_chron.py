"""TK-646: проверки tools/chron.py (запуск: python tools/tests/test_chron.py). БД — во временной папке; репозиторий только читается."""
import io
import json
import multiprocessing as mp
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import chron  # noqa: E402


def entry(title="заголовок", **kw):
    e = {"kind": "verdict", "title": title, "body": kw.pop("body", "текст " + title)}
    e.update(kw)
    return e


def _writer(args):
    db, p, k = args
    st = chron.Store(chron.connect(db))
    for i in range(k):
        st.add_entry(entry("w%d-%d" % (p, i)), [])
    return k


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "c.sqlite")
        os.environ["CHRON_DB"] = self.db
        self.st = chron.Store(chron.connect(self.db))

    def tearDown(self):
        self.st.c.close()
        os.environ.pop("CHRON_DB", None)
        self.tmp.cleanup()

    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = chron.main(list(argv))
        return rc, buf.getvalue()


class TestStore(Base):
    def test_entry_immutable(self):
        n = self.st.add_entry(entry("а"), [])
        self.assertEqual(n, "L-1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("UPDATE entry SET title='б' WHERE id=1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("DELETE FROM entry WHERE id=1")

    def test_correction_and_links_both_ways(self):
        self.st.add_entry(entry("старое"), [])
        n = self.st.add_entry(entry("новое", corrects="L-1", verdict="не работает", usd=-12.5, trades=40, sharpe=-0.3, same_data=1), ["TK-646"])
        g = self.st.get("L-1")
        self.assertEqual(g["corrected_by"], [2])
        self.assertIn({"node": n, "rel": "entry", "origin": "add", "label": "новое"}, g["links"]["in"])
        self.assertEqual({x["node"] for x in self.st.get("L-2")["links"]["out"]}, {"L-1", "TK-646"})
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("DELETE FROM link")

    def test_bad_input_writes_nothing(self):
        with self.assertRaises(chron.ChronError):
            self.st.add_entry(entry("x"), ["L-99"])
        with self.assertRaises(chron.ChronError):
            self.st.add_entry(entry("x", verdict="хорошо"), [])
        with self.assertRaises(chron.ChronError):
            self.st.add_entry(entry("x", corrects="L-5"), [])
        self.assertEqual(self.st.c.execute("SELECT count(*) FROM entry").fetchone()[0], 0)

    def test_psec_draft_lock_amend(self):
        self.st.new_protocol("P-90-test", "Тест")
        a = self.st.add_psec({"protocol": "P-90", "body": "## 1. Вопрос\nчто\n"}, [])
        b = self.st.add_psec({"protocol": "P-90", "body": "## 2. Правило вердикта\nα = 0,05\n"}, [])
        self.st.edit_psec(a, "## 1. Вопрос\nчто именно\n")
        self.st.lock_psec(a)
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.edit_psec(a, "## 1. Вопрос\nдругое\n")
        am = self.st.add_psec({"protocol": "P-90", "body": "### 1.1 Поправка\nуточнение\n", "amends": "P-90 §1", "prereg": True}, [])
        g = self.st.get(a)
        self.assertEqual(g["amended_by"], [int(am[3:])])
        secs = [s["id"] for s in self.st.get("P-90-test")["sections"]]
        self.assertEqual(secs, [int(a[3:]), int(am[3:]), int(b[3:])])  # поправка — сразу за исправляемым разделом
        self.assertEqual(self.st.get(b)["psec"]["kind"], "verdict_rule")
        self.assertEqual(self.st.get(b)["psec"]["alpha"], "0,05")
        with self.assertRaises(sqlite3.DatabaseError):
            self.st.c.execute("DELETE FROM psec")

    def test_search_substring_and_yo(self):
        self.st.add_entry(entry("Перетитровка шорт-базы", body="заморожено ёмкость L_n24"), [])
        self.st.add_entry(entry("другое", body="ничего"), [])
        self.assertEqual(self.st.search("титровк"), ["L-1"])
        self.assertEqual(self.st.search("емкость"), ["L-1"])
        self.assertEqual(self.st.search("n24 L_"), ["L-1"])
        self.assertEqual(self.st.query({"q": "заморож", "verdict": "—"})["total"], 1)

    def test_parallel_writers_no_lost_rows(self):
        with mp.Pool(8) as pool:
            pool.map(_writer, [(self.db, p, 20) for p in range(8)])
        n, d = self.st.c.execute("SELECT count(*), count(DISTINCT title) FROM entry").fetchone()
        self.assertEqual((n, d), (160, 160))
        self.assertEqual(self.st.check_db(), [])

    def test_rpc_bootstrap(self):
        """Путь «с ПК на srv»: исходник + вызов на stdin `python -`, ответ строкой CHRON-RESULT (без ssh)."""
        self.st.add_entry(entry("по сети"), [])
        import base64
        payload = base64.b64encode(json.dumps({"op": "query", "args": [{}], "kw": {}}).encode()).decode()
        prog = "RPC = %r\n" % payload + (TOOLS / "chron.py").read_text(encoding="utf-8")
        r = subprocess.run([sys.executable, "-"], input=prog.encode("utf-8"), capture_output=True,
                           env=dict(os.environ, CHRON_RPC="1", CHRON_DB=self.db, PYTHONUTF8="1"))
        m = re.search(r"^CHRON-RESULT (.*)$", r.stdout.decode("utf-8"), re.M)
        self.assertTrue(m, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(m.group(1))["ok"]["rows"][0]["title"], "по сети")

    def test_cli_add_show_find_links_check(self):
        rc, out = self.cli("add", "--title", "вердикт П-90", "--project", "P-90", "--task", "TK-900", "--verdict", "не ясно",
                           "--usd", "12.5", "--trades", "33", "--sharpe", "0.4", "--same-data", "--body", "числа", "--link", "TK-901")
        self.assertEqual((rc, out.strip()), (0, "L-1"))
        rc, out = self.cli("show", "L-1")
        self.assertIn("найдено на этих данных", out)
        self.assertIn("TK-901", out)
        rc, out = self.cli("find", "вердикт", "--project", "П-90")
        self.assertIn("L-1", out)
        rc, out = self.cli("links", "TK-901")
        self.assertIn("← L-1", out)
        rc, out = self.cli("check")
        self.assertEqual(rc, 0, out)
        self.st.c.execute("INSERT INTO link VALUES ('L-1', 'commit:0000000deadbeef', 'commit', 't', 'now')")
        self.st.c.execute("INSERT INTO link VALUES ('L-1', 'L-77', 'entry', 't', 'now')")
        rc, out = self.cli("check")
        self.assertEqual(rc, 1)
        self.assertIn("commit:0000000deadbeef", out)
        self.assertIn("L-77", out)


JOURNAL = """# Журнал

текст
| дата | протокол | шаг |
|---|---|---|
| 25.09 ~19:00 | П-01 | протокол TK-001 |
| 26.09 ~01:31Z | П-01 | счёт |

## 2026-10-09 · П-12 охват (TK-063)
строка 1
- пункт

05.11 00:55 · TK-491 · П-34 — запись
18.11 · П-42 · ПРЕДРЕГИСТРАЦИЯ §13.22 подряд без пустой строки
продолжение

| 20.10 ~10:00 (GMT+4) | П-21 | без шапки |
"""

PROTO = """# П-90. Тест

вступление
## 1. Вопрос
```bash
# не заголовок
```
## 13.28 Раздел — записано 19.11.2026 ДО счёта
#### 7. Подпункт
текст
## Приложение А
"""


class TestParse(Base):
    def test_journal_chunks_lossless(self):
        ch = chron.chunk_lines(JOURNAL, "journal")
        self.assertEqual("".join(c["text"] for c in ch), JOURNAL)
        ents = [c for c in ch if c["kind"] == "entry"]
        self.assertEqual(len(ents), 6)
        for c in ents:
            c["body"], c["suffix"] = chron.split_suffix(c["text"])
        es = [chron.journal_entry(c) for c in ents]
        self.assertEqual(es[0]["ts"], "2026-09-25T19:00+04:00")
        self.assertEqual(es[1]["ts"], "2026-09-26T05:31+04:00")  # «Z» → GMT+4
        self.assertEqual((es[2]["task"], es[2]["project"]), ("TK-063", "П-12"))
        self.assertEqual(es[4]["kind"], "prereg")
        self.assertIn("продолжение", ents[4]["body"])
        self.assertEqual(es[5]["fields"]["col3"], "без шапки")

    def test_protocol_sections(self):
        ch = chron.section_chunks(PROTO)
        self.assertEqual("".join(c["text"] for c in ch), PROTO)
        heads = [c["heading"] for c in ch if c["kind"] == "psec"]
        self.assertEqual(len(heads), 5)  # «# не заголовок» внутри ``` не считается
        root = Path(self.tmp.name)
        (root / "docs" / "research").mkdir(parents=True)
        (root / "docs" / "research" / "P-90-test.md").write_text(PROTO, encoding="utf-8", newline="\n")
        docs = chron.build_docs(root, ["docs/research/P-90-test.md"], [], None)
        secs = [c["sec"] for c in docs[0]["chunks"] if c["kind"] == "psec"]
        self.assertEqual([s["num"] for s in secs], [None, "1", "13.28", "13.28 п. 7", None])
        self.assertEqual([s["prereg"] for s in secs], [0, 0, 1, 1, 0])
        self.assertEqual(secs[2]["prereg_at"], "2026-11-19")
        st = self.st.import_docs(docs, [])
        self.assertEqual(st["docs/research/P-90-test.md"]["rows"], 5)
        self.assertEqual(self.st.reconstruct("docs/research/P-90-test.md"), PROTO)
        # повторный перенос изменённого файла: новый раздел — новая строка, изменённый — поправка, старые не тронуты
        (root / "docs" / "research" / "P-90-test.md").write_text(PROTO.replace("текст\n", "текст 2\n") + "## 14. Новое\n", encoding="utf-8", newline="\n")
        docs = chron.build_docs(root, ["docs/research/P-90-test.md"], [], None)
        st = self.st.import_docs(docs, [])["docs/research/P-90-test.md"]
        self.assertEqual((st["new"], st["amended"], st["reused"]), (1, 1, 4))
        self.assertEqual(self.st.check_db(), [])


def independent_count(path):
    """Счёт записей в md, независимый от разбора chron: строки таблиц (без шапки), блоки журнала, заголовки протоколов вне ```."""
    lines = (ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    sep = re.compile(r"^\|[\s:|-]*-[\s:|-]*\|?\s*$")
    if Path(path).name.startswith("P-"):
        n, fence = 0, None
        for ln in lines:
            m = re.match(r"^\s{0,3}(```|~~~)", ln)
            if m:
                fence = None if fence == m.group(1) else (fence or m.group(1))
                continue
            if fence is None and re.match(r"^#{1,6}\s", ln):
                n += 1
        return n
    n = 0
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if ln.startswith("|") and not sep.match(ln) and not sep.match(nxt):
            n += 1
        elif path.endswith("JOURNAL.md") and re.match(r"^(## |\[?\d\d\.\d\d( \d{1,2}:\d\d)? ·)", ln):
            n += 1
    return n


class TestRealMigration(Base):
    def test_all_sources_lossless(self):
        paths = list(chron.DEFAULT_SOURCES) + chron.protocol_files(ROOT)
        amap = chron.archive_map(ROOT, paths)
        docs = chron.build_docs(ROOT, paths, chron.load_kb_nodes(ROOT), chron.Git(ROOT), amap)
        st = self.st.import_docs(docs, [list(n) for n in chron.load_kb_nodes(ROOT)])
        total = 0
        for d in docs:
            src = amap.get(d["path"], d["path"])
            self.assertEqual(st[d["path"]]["rows"], independent_count(src), d["path"])
            self.assertEqual(self.st.reconstruct(d["path"]).encode("utf-8"), (ROOT / src).read_bytes().replace(b"\r\n", b"\n"), d["path"])
            total += st[d["path"]]["rows"]
        self.assertEqual(total, self.st.c.execute("SELECT (SELECT count(*) FROM entry) + (SELECT count(*) FROM psec)").fetchone()[0])
        again = self.st.import_docs(docs, None)
        self.assertEqual(sum(s.get("new", 0) + s.get("amended", 0) for s in again.values()), 0)
        self.assertEqual(self.st.check_db(), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
