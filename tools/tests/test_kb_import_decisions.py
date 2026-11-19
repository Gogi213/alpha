"""TK-632: проверки tools/kb_import_decisions.py на подставных реестрах (запуск: python tools/tests/test_kb_import_decisions.py). Пишет только во временную папку."""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kb  # noqa: E402
import kb_import_decisions as imp  # noqa: E402

DECISIONS = """# Решения владельца

## В-1 · 2026-10-14 19:08
> доделывай
> и ещё строка
Суть: Довести переход. Второе предложение.
Заменяет: —

## В-2 · 2026-10-15 00:42
> ну вроде мне понравилось делай
Суть: Макет v4 принят (см. SETTLED В-29 и П-42, TK-100).
Заменяет: —

## В-3 · 2026-10-15 04:15
> норм делай
Суть: Макет v6 принят.
Заменяет: В-2

## В-4 · 2026-10-16 10:00
> часть
Вопрос CEO: что именно?
Суть: Порог 220 тыс. поменяли.
Заменяет: порог из В-1 (в части места сборки)
Повторено владельцем · 2026-10-16 19:00: «2 - да»

## В-5 · 2026-10-17 (время не зафиксировано)
> без времени
Суть: Запись без времени.
Заменяет: —
"""

SETTLED = """# Settled objections

## Из валидации
В-1 (блокирующее) | Возражение про рантайм. | Ответ плана: шаг 0.7.

В-2 | Второе возражение | Ответ.

## Из ревью
**В-29. Предрегистрация в два этапа (2026-09-11).** Текст решения со ссылкой на В-1 и П-42.

**В-30. Порог (2026-09-12).** Владелец: «не знаю». Решение.
*Дополнено 2026-09-17: хвост той же записи.*

**В-30, правка по данным в тот же день.** Поправка.
"""

INDEX = """# Решения владельца — оглавление

- **В-1** — Возражение про рантайм.
- **В-29** — Предрегистрация в два этапа (2026-09-11).
- **В-30** — Порог (2026-09-12).
"""


class Repo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "archive").mkdir(parents=True)
        (self.root / "docs" / "archive" / "decisions-2026-11-20.md").write_text(DECISIONS, encoding="utf-8")
        (self.root / "docs" / "archive" / "SETTLED-2026-11-20.md").write_text(SETTLED, encoding="utf-8")
        (self.root / "docs" / "archive" / "SETTLED-index-2026-11-20.md").write_text(INDEX, encoding="utf-8")
        kbd = self.root / "kb"
        (kbd / "protocol").mkdir(parents=True)
        (kbd / "task").mkdir()
        (kbd / "runbook").mkdir()
        (kbd / "kb.toml").write_text('title = "Т"\n[check]\norphans = "error"\n', encoding="utf-8")
        card = "---\nid: {id}\ntype: {t}\nstatus: open\ntitle: x\ndate: 2026-11-20\nlinks: [decisions-registry]\nsupersedes: []\n---\nтекст\n"
        (kbd / "protocol" / "P-42.md").write_text(card.format(id="P-42", t="protocol"), encoding="utf-8")
        (kbd / "task" / "TK-100.md").write_text(card.format(id="TK-100", t="task"), encoding="utf-8")
        for hub in ("decisions-registry", "settled-registry"):
            (kbd / "runbook" / (hub + ".md")).write_text(card.format(id=hub, t="runbook").replace("[decisions-registry]", "[P-42]"), encoding="utf-8")

    def run(self, *extra):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = imp.main(["--repo", str(self.root)] + list(extra))
        return rc, buf.getvalue()

    def read(self, cid):
        return (self.root / "kb" / "decision" / (cid + ".md")).read_text(encoding="utf-8")

    def meta(self, cid):
        return kb.parse_card_text(self.read(cid))[0]


class Registry(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.addCleanup(self.r.tmp.cleanup)
        self.r.run()

    def test_counts(self):
        files = sorted(p.stem for p in (self.r.root / "kb" / "decision").glob("*.md"))
        self.assertEqual(files, ["S-1", "S-2", "S-29", "S-30", "В-1", "В-2", "В-3", "В-4", "В-5"])

    def test_quote_verbatim_and_sut(self):
        t = self.r.read("В-1")
        self.assertIn("> доделывай\n> и ещё строка", t)
        self.assertIn("## Суть\n\nДовести переход. Второе предложение.", t)
        self.assertEqual(self.r.meta("В-1")["title"], "В-1 — Довести переход")

    def test_pure_replaces_sets_supersedes_and_status(self):
        self.assertEqual(self.r.meta("В-3")["supersedes"], ["В-2"])
        self.assertEqual(self.r.meta("В-2")["status"], "superseded")
        self.assertEqual(self.r.meta("В-3")["status"], "done")

    def test_partial_replaces_is_only_a_note(self):
        m4, m1 = self.r.meta("В-4"), self.r.meta("В-1")
        self.assertEqual(m4["supersedes"], [])
        self.assertEqual(m1["status"], "done")
        self.assertIn("Частично заменена [[В-4]]", self.r.read("В-1"))
        self.assertIn("В-1", m4["links"])
        self.assertIn("Вопрос CEO: что именно?", self.r.read("В-4"))
        self.assertIn("Повторено владельцем · 2026-10-16 19:00: «2 - да»", self.r.read("В-4"))

    def test_links_to_existing_cards_only(self):
        links = self.r.meta("В-2")["links"]
        self.assertIn("S-29", links)      # «SETTLED В-29» → S-29
        self.assertIn("P-42", links)
        self.assertIn("TK-100", links)
        self.assertNotIn("В-29", links)
        self.assertIn("decisions-registry", links)

    def test_missing_time(self):
        self.assertIn("записано 2026-10-17 (время не зафиксировано)", self.r.read("В-5"))

    def test_settled_forms(self):
        m1 = self.r.read("S-1")
        self.assertIn("**Возражение (блокирующее).** Возражение про рантайм.", m1)
        self.assertIn("**Ответ плана.** Ответ плана: шаг 0.7.", m1)
        self.assertEqual(self.r.meta("S-1")["date"], "2026-09-11")   # нет даты → по следующей записи
        self.assertIn("взята по следующей записи", m1)
        s29 = self.r.read("S-29")
        self.assertEqual(self.r.meta("S-29")["date"], "2026-09-11")
        self.assertIn("S-1", self.r.meta("S-29")["links"])           # «В-1» в тексте SETTLED = S-1
        self.assertEqual(self.r.meta("S-29")["title"], "S-29 — Предрегистрация в два этапа")
        self.assertIn("Владелец: «не знаю»", self.r.read("S-30"))

    def test_settled_addendum_merged(self):
        t = self.r.read("S-30")
        self.assertIn("## Дополнение\n\n**В-30, правка по данным в тот же день.** Поправка.", t)
        self.assertIn("*Дополнено 2026-09-17: хвост той же записи.*", t)

    def test_check_green(self):
        k = kb.Kb(self.r.root / "kb")
        cards, issues = kb.load_cards(k)
        problems = [i for i in kb.check_cards(k, cards, issues) if i.level == "error"]
        self.assertEqual(problems, [])


class Idempotent(unittest.TestCase):
    def test_second_run_changes_nothing(self):
        r = Repo()
        self.addCleanup(r.tmp.cleanup)
        r.run()
        before = {p.name: p.read_bytes() for p in (r.root / "kb" / "decision").glob("*.md")}
        rc, out = r.run("--force")
        self.assertEqual(rc, 0)
        self.assertIn("new 0, changed 0, same 9", out)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (r.root / "kb" / "decision").glob("*.md")})

    def test_manual_card_kept(self):
        r = Repo()
        self.addCleanup(r.tmp.cleanup)
        (r.root / "kb" / "decision").mkdir()
        manual = "---\nid: В-1\ntype: decision\nstatus: done\ntitle: рукой\ndate: 2026-10-14\nlinks: [P-42]\nsupersedes: []\n---\nруками\n"
        (r.root / "kb" / "decision" / "В-1.md").write_text(manual, encoding="utf-8")
        rc, out = r.run("--force")
        self.assertIn("kept-manual 1", out)
        self.assertEqual(r.read("В-1"), manual)

    def test_existing_cards_need_force(self):
        r = Repo()
        self.addCleanup(r.tmp.cleanup)
        r.run()
        card = r.root / "kb" / "decision" / "В-3.md"
        card.write_text(card.read_text(encoding="utf-8") + "\nправка карточкой\n", encoding="utf-8")
        edited = card.read_bytes()
        with redirect_stderr(io.StringIO()):
            rc, out = r.run()
        self.assertEqual(rc, 2)
        self.assertEqual(card.read_bytes(), edited)

    def test_dry_run_writes_nothing(self):
        r = Repo()
        self.addCleanup(r.tmp.cleanup)
        rc, out = r.run("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(list((r.root / "kb" / "decision").glob("*.md")), [])

    def test_changed_source_updates_card(self):
        r = Repo()
        self.addCleanup(r.tmp.cleanup)
        r.run()
        p = r.root / "docs" / "archive" / "decisions-2026-11-20.md"
        p.write_text(p.read_text(encoding="utf-8").replace("Макет v6 принят.", "Макет v7 принят."), encoding="utf-8")
        rc, out = r.run("--force")
        self.assertIn("changed 1", out)
        self.assertIn("Макет v7 принят.", r.read("В-3"))


if __name__ == "__main__":
    unittest.main()
