"""TK-629: проверки tools/kb.py на подставных каталогах (запуск: python tools/tests/test_kb.py). Пишет только во временную папку."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kb  # noqa: E402

CARD = "---\nid: {id}\ntype: {type}\nstatus: {status}\ntitle: {title}\ndate: 2026-11-20\nlinks: [{links}]\nsupersedes: [{sup}]\n---\n{body}\n"


class Repo:
    """Временный репозиторий: <tmp>/kb/kb.toml, карточки, файл docs/a.md для ссылок по пути."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "kb").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "a.md").write_text("файл", encoding="utf-8")
        (self.root / "kb" / "kb.toml").write_text('title = "Т"\n[check]\norphans = "error"\n', encoding="utf-8")

    def card(self, cid, type_="task", status="open", title="Заголовок", links="", sup="", body="текст", name=None, dirname=None):
        d = self.root / "kb" / (dirname or type_)
        d.mkdir(exist_ok=True)
        (d / ((name or cid) + ".md")).write_text(
            CARD.format(id=cid, type=type_, status=status, title=title, links=links, sup=sup, body=body), encoding="utf-8")

    def kb(self):
        return kb.Kb(self.root / "kb")

    def run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = kb.main(["--kb", str(self.root / "kb")] + list(argv))
        return rc, buf.getvalue()

    def issues(self):
        k = self.kb()
        cards, iss = kb.load_cards(k)
        return {(i.code, i.level) for i in kb.check_cards(k, cards, iss)}


class Parsing(unittest.TestCase):
    def test_front_matter_forms(self):
        meta, body = kb.parse_card_text(
            '---\nid: В-161\ntitle: "Передача: при 260 тыс."\nlinks: [TK-1, "a, b", \'c\']\nsupersedes:\n  - X-1\n  - X-2\n# комментарий\nextra: 5\n---\n\nтело\n')
        self.assertEqual(meta["id"], "В-161")
        self.assertEqual(meta["title"], "Передача: при 260 тыс.")
        self.assertEqual(meta["links"], ["TK-1", "a, b", "c"])
        self.assertEqual(meta["supersedes"], ["X-1", "X-2"])
        self.assertEqual(meta["extra"], "5")
        self.assertEqual(body, "тело")

    def test_front_matter_crlf_and_bom(self):
        meta, body = kb.parse_card_text("﻿---\r\nid: a\r\n---\r\nb\r\n")
        self.assertEqual((meta["id"], body), ("a", "b"))

    def test_front_matter_errors(self):
        for bad in ("без front matter", "---\nid: a\n", "---\nid a\n---\n", "---\nid: a\nid: b\n---\n", "---\nlinks: [a\n---\n"):
            with self.assertRaises(ValueError, msg=bad):
                kb.parse_card_text(bad)

    def test_toml_subset(self):
        cfg = kb.parse_toml('# c\ntitle = "a # b"  # хвост\ntypes = ["x", "y"]\n[check]\norphans = "warn"\n')
        self.assertEqual(cfg, {"title": "a # b", "types": ["x", "y"], "check": {"orphans": "warn"}})
        with self.assertRaises(kb.KbError):
            kb.parse_toml("мусор")

    def test_inline_links_skip_code(self):
        body = "см. [[A-1]] и [[docs/a.md]], не `[[B-2]]`\n```\n[[C-3]]\n```\nещё [[A-1]]"
        self.assertEqual(kb.inline_links(body), ["A-1", "docs/a.md"])

    def test_is_path(self):
        for x in ("docs/a.md", "AGENTS.md", "tools/kb.py", "a.json"):
            self.assertTrue(kb.is_path(x), x)
        for x in ("TK-625", "В-161", "P.42", "v1.2", "srv"):
            self.assertFalse(kb.is_path(x), x)

    def test_fold_yo_and_case(self):
        self.assertEqual(kb.fold("ЁЖ Ёлка"), "еж елка")


class Check(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.addCleanup(self.r.tmp.cleanup)

    def test_clean(self):
        self.r.card("TK-1", links="TK-2, docs/a.md")
        self.r.card("TK-2", body="назад [[TK-1]]")
        self.assertEqual(self.r.issues(), set())
        self.assertEqual(self.r.run("check")[0], 0)

    def test_broken_link_all_kinds(self):
        self.r.card("TK-1", links="TK-9, docs/нет.md", sup="TK-8", body="[[TK-7]]")
        self.r.card("TK-2", links="TK-1")
        out = self.r.run("check")
        self.assertEqual(out[0], 1)
        self.assertEqual(out[1].count("[broken-link]"), 4)

    def test_orphan_is_error_and_configurable(self):
        self.r.card("TK-1")
        self.r.card("TK-2", links="TK-9")
        self.assertIn(("orphan", "error"), self.r.issues())
        (self.r.root / "kb" / "kb.toml").write_text('[check]\norphans = "warn"\n', encoding="utf-8")
        self.assertIn(("orphan", "warning"), self.r.issues())
        self.assertEqual(self.r.run("check")[0], 1)  # битая ссылка TK-2 → TK-9 всё равно ошибка

    def test_path_link_does_not_unorphan(self):
        self.r.card("TK-1", links="docs/a.md")
        self.assertIn(("orphan", "error"), self.r.issues())

    def test_duplicate_id(self):
        self.r.card("TK-1", links="TK-2")
        self.r.card("TK-2", links="TK-1")
        self.r.card("TK-1", type_="finding", links="TK-2", name="TK-1")
        self.assertIn(("duplicate-id", "error"), self.r.issues())

    def test_superseded_needs_replacement(self):
        self.r.card("OLD", status="superseded", links="TK-1")
        self.r.card("TK-1", links="OLD")
        self.assertIn(("no-replacement", "error"), self.r.issues())
        self.r.card("NEW", sup="OLD")
        got = self.r.issues()
        self.assertNotIn(("no-replacement", "error"), got)

    def test_stale_status_warning(self):
        self.r.card("OLD", status="open", links="NEW")
        self.r.card("NEW", sup="OLD")
        self.assertIn(("stale-status", "warning"), self.r.issues())
        self.assertEqual(self.r.run("check")[0], 0)  # предупреждение не валит

    def test_bad_front_matter_fields(self):
        self.r.card("TK-1", status="готово", links="TK-2")
        self.r.card("TK-2", links="TK-1")
        (self.r.root / "kb" / "task" / "TK-3.md").write_text("---\nid: TK-3\ntype: task\n---\n", encoding="utf-8")
        (self.r.root / "kb" / "task" / "TK-4.md").write_text("нет шапки", encoding="utf-8")
        self.r.card("TK-5", links="TK-1")
        (self.r.root / "kb" / "task" / "TK-5.md").write_text(
            CARD.format(id="TK-5", type="task", status="open", title="t", links="TK-1", sup="", body="").replace("2026-11-20", "20.11.2026"), encoding="utf-8")
        k = self.r.kb()
        cards, iss = kb.load_cards(k)
        msgs = " | ".join(i.msg for i in iss)
        self.assertIn("статус «готово»", msgs)
        self.assertIn("нет обязательного поля", msgs)
        self.assertIn("нет front matter", msgs)
        self.assertIn("ГГГГ-ММ-ДД", msgs)

    def test_path_mismatch_and_unknown_dir(self):
        self.r.card("TK-1", links="TK-2", name="TK-77")
        self.r.card("TK-2", links="TK-1")
        self.r.card("Z-1", type_="task", dirname="чужой")
        got = self.r.issues()
        self.assertIn(("path-mismatch", "error"), got)
        self.assertIn(("unknown-dir", "error"), got)

    def test_cyrillic_id_nfd_file_name(self):
        import unicodedata
        self.r.card("В-161", type_="decision", links="TK-1", name=unicodedata.normalize("NFD", "В-161"))
        self.r.card("TK-1", links="В-161")
        self.assertEqual(self.r.issues(), set())


class Db(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.addCleanup(self.r.tmp.cleanup)
        self.r.card("TK-625", title="Признак успешных пробоев", links="P-42, docs/a.md", body="Шорт D 14: Ёлка и ёж. Ёж найден.")
        self.r.card("P-42", type_="protocol", title="Пробой уровня", links="TK-625", body="протокол, см. [[TK-625]]")
        self.r.card("В-161", type_="decision", title="Передача дел при 260 тыс.", links="P-42")

    def test_build_links_backlinks(self):
        rc, out = self.r.run("build")
        self.assertEqual(rc, 0)
        self.assertIn("3 карточек", out)
        rc, out = self.r.run("show", "P-42")
        self.assertIn("← TK-625", out)
        self.assertIn("← В-161", out)
        self.assertIn("→ TK-625", out)
        rc, out = self.r.run("show", "TK-625")
        self.assertIn("файл", out)

    def test_fts_cyrillic_yo_prefix_and_hyphen(self):
        self.r.run("build")
        self.assertIn("TK-625", self.r.run("find", "елка")[1])      # ё = е
        self.assertIn("TK-625", self.r.run("find", "ПРОБОЕВ")[1])   # регистр
        self.assertIn("TK-625", self.r.run("find", "пробо")[1])    # префикс
        self.assertIn("В-161", self.r.run("find", "В-161")[1])     # дефис, кириллическая В
        self.assertIn("В-161", self.r.run("find", "передача 260")[1])
        self.assertIn("ничего не найдено", self.r.run("find", "несуществует")[1])
        self.assertTrue(self.r.run("find", 'кавычка"')[0] == 0)    # кавычки в запросе не ломают MATCH

    def test_exact_id_first(self):
        self.r.run("build")
        first = self.r.run("find", "P-42")[1].splitlines()[0]
        self.assertTrue(first.startswith("P-42"))

    def test_list_filters(self):
        self.r.run("build")
        self.assertEqual(len(self.r.run("list")[1].splitlines()), 3)
        self.assertEqual(len(self.r.run("list", "--type", "decision")[1].splitlines()), 1)
        self.assertEqual(len(self.r.run("list", "--status", "done")[1].splitlines()), 0)
        link = self.r.run("list", "--link", "P-42")[1]
        self.assertIn("TK-625", link)
        self.assertIn("В-161", link)
        self.assertNotIn("P-42 ", link)

    def test_autorebuild_on_new_card(self):
        self.r.run("build")
        self.r.card("TK-9", links="P-42", body="новая-уникальная-строка")
        os.utime(self.r.root / "kb" / "task" / "TK-9.md", (4102444800, 4102444800))
        self.assertIn("TK-9", self.r.run("find", "новая-уникальная-строка")[1])

    def test_without_fts5(self):
        k = self.r.kb()
        cards, _ = kb.load_cards(k)
        kb.build_db(k, cards)
        import sqlite3
        conn = sqlite3.connect(str(k.db_path))
        conn.execute("UPDATE meta SET value='0' WHERE key='fts'")
        conn.commit()
        conn.close()
        conn = kb.ensure_db(k)
        try:
            self.assertEqual([r["id"] for r in kb.search(conn, "ёж", 5)], ["TK-625"])
        finally:
            conn.close()

    def test_new_card_template_and_duplicate(self):
        (self.r.root / "kb" / "_templates").mkdir()
        (self.r.root / "kb" / "_templates" / "task.md").write_text("## Дальше\n", encoding="utf-8")
        rc, out = self.r.run("new", "task", "TK-700", 'Заголовок: с "кавычками"')
        self.assertEqual(rc, 0)
        text = (self.r.root / "kb" / "task" / "TK-700.md").read_text(encoding="utf-8")
        meta, body = kb.parse_card_text(text)
        self.assertEqual(meta["title"], 'Заголовок: с "кавычками"')
        self.assertEqual(meta["status"], "open")
        self.assertEqual(body, "## Дальше")
        self.assertEqual(self.r.run("new", "task", "TK-700", "x")[0], 2)
        self.assertEqual(self.r.run("new", "nope", "TK-701", "x")[0], 2)


class Site(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.addCleanup(self.r.tmp.cleanup)
        body = ("## Итог\n\nСмотри [[В-161]], файл [[docs/a.md]], битая [[НЕТ-1]].\n\n- пункт **один**\n  - вложенный\n- пункт `два`\n\n"
                "| a | b |\n|---|---|\n| 1 | <x> |\n\n```\n[[В-161]] <b>\n```\n\n> цитата\n\n[js](javascript:alert(1)) https://example.com/x")
        self.r.card("TK-625", title="Признак <b>", links="В-161", body=body)
        self.r.card("В-161", type_="decision", links="TK-625")
        self.r.card("TK-2", status="superseded", links="TK-625")
        self.r.card("TK-3", sup="TK-2", links="TK-625")
        self.out = self.r.root / "site"
        self.assertEqual(self.r.run("site", str(self.out))[0], 0)

    def test_files(self):
        for f in ("index.html", "graph.html", "style.css", "app.js", "graph.js", "data.js", "c/TK-625.html", "c/В-161.html"):
            self.assertTrue((self.out / f).exists(), f)

    def test_favicon_inline(self):
        self.assertIn('rel="icon" href="data:,"', (self.out / "index.html").read_text(encoding="utf-8"))  # без запроса /favicon.ico → 404

    def test_no_external_resources(self):
        for f in self.out.rglob("*"):
            if f.suffix in (".html", ".css", ".js"):
                t = f.read_text(encoding="utf-8")
                self.assertNotIn("cdn", t.lower(), f.name)
                self.assertNotRegex(t, r'(src|href)="https?://(?!example\.com)', f.name)

    def test_card_page(self):
        t = (self.out / "c" / "TK-625.html").read_text(encoding="utf-8")
        self.assertIn('<a href="%D0%92-161.html">В-161</a>', t)
        self.assertIn('<code>docs/a.md</code>', t)
        self.assertIn('class="broken">НЕТ-1<', t)
        self.assertIn("<h2>Итог</h2>", t)
        self.assertIn("<li>вложенный</li>", t)
        self.assertIn("<strong>один</strong>", t)
        self.assertIn("<table>", t)
        self.assertIn("&lt;x&gt;", t)
        self.assertIn("[[В-161]] &lt;b&gt;", t)         # код не превращается в ссылку и экранируется
        self.assertNotIn('href="javascript', t)
        self.assertIn('href="https://example.com/x"', t)
        self.assertIn("Признак &lt;b&gt;", t)           # заголовок экранирован
        # обратные ссылки: на TK-625 ссылаются В-161, TK-2, TK-3
        self.assertEqual(t.split("Обратные ссылки")[0].count('href="%D0%92-161.html"'), 2)  # в тексте + в карточке-списке, но список — одной строкой
        self.assertEqual(t.split("<h2>Ссылки</h2>")[1].split("<h2>")[0].count("В-161"), 1)
        back = t.split("Обратные ссылки")[1]
        for cid in ("В-161", "TK-2", "TK-3"):
            self.assertIn(cid, back)

    def test_supersedes_shown(self):
        old = (self.out / "c" / "TK-2.html").read_text(encoding="utf-8")
        self.assertIn("заменена:", old)
        new = (self.out / "c" / "TK-3.html").read_text(encoding="utf-8")
        self.assertIn("заменяет", new)

    def test_data_json_and_graph_edges(self):
        raw = (self.out / "data.js").read_text(encoding="ascii")  # только ASCII: не зависит от кодировки сервера
        data = json.loads(raw[len("window.KB="):].rstrip().rstrip(";"))
        by = {c["id"]: c for c in data["cards"]}
        self.assertEqual(set(by), {"TK-625", "В-161", "TK-2", "TK-3"})
        self.assertIn("В-161", by["TK-625"]["out"])
        self.assertNotIn("НЕТ-1", by["TK-625"]["out"])
        self.assertEqual(set(by["TK-625"]["inn"]), {"В-161", "TK-2", "TK-3"})
        self.assertEqual(by["TK-625"]["href"], "c/TK-625.html")
        self.assertEqual(set(data["types"]), set(self.r.kb().types))

    def test_markdown_edges(self):
        link = lambda t: "<L:%s>" % t
        self.assertEqual(kb.md_inline("a_b_c *x* **y**", link), "a_b_c <em>x</em> <strong>y</strong>")
        self.assertEqual(kb.md_inline("<script>", link), "&lt;script&gt;")
        self.assertIn("<ol><li>раз</li><li>два</li></ol>", kb.md_to_html("1. раз\n2. два", link))
        self.assertEqual(kb.md_to_html("a\nb\n\nc", link), "<p>a b</p>\n<p>c</p>")
        self.assertIn("<hr>", kb.md_to_html("---", link))


class Cli(unittest.TestCase):
    def test_script_runs_as_process_and_hook(self):
        r = Repo()
        self.addCleanup(r.tmp.cleanup)
        r.card("TK-1", links="TK-2")
        r.card("TK-2", links="TK-1")
        script = str(Path(kb.__file__).resolve())
        env = dict(os.environ, PYTHONUTF8="1")
        p = subprocess.run([sys.executable, script, "--kb", str(r.root / "kb"), "check"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(p.returncode, 0, p.stdout)
        r.card("TK-3", links="НЕТ-9")
        p = subprocess.run([sys.executable, script, "--kb", str(r.root / "kb"), "check"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("broken-link", p.stdout.decode("utf-8"))

    def test_precommit_only_when_kb_staged(self):
        r = Repo()
        self.addCleanup(r.tmp.cleanup)
        g = lambda *a: subprocess.run(["git", "-C", str(r.root)] + list(a), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        try:
            g("init", "-q")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git недоступен")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        r.card("TK-3", links="НЕТ-9")                      # битая ссылка в kb/
        (r.root / "docs" / "b.md").write_text("x", encoding="utf-8")
        g("add", "docs/b.md")
        self.assertEqual(r.run("precommit")[0], 0)          # kb/ не в коммите → check не запускается
        g("add", "kb")
        self.assertEqual(r.run("precommit")[0], 1)          # kb/ в коммите → битая ссылка ловится
        g("reset", "-q")
        self.assertEqual(r.run("precommit")[0], 0)
        self.assertEqual(r.run("precommit", "--changed")[0], 1)  # любые правки kb/ (для хука Claude)
        rc, out = r.run("install-hook")
        self.assertEqual(rc, 0)
        hook = r.root / ".git" / "hooks" / "pre-commit"
        self.assertIn("# kb-hook", hook.read_text(encoding="utf-8"))
        self.assertIn("уже установлен", r.run("install-hook")[1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
