---
id: decisions-registry
type: runbook
status: running
title: "Реестр решений владельца .claude/decisions.md → карточки В-N"
date: 2026-11-20
links: [settled-registry, .claude/decisions.md, tools/kb_import_decisions.py, .claude/preferences.md, TK-632]
supersedes: []
---
**Реестр v2** — `.claude/decisions.md`: решения владельца дословно, 161 запись (В-1…В-161, 14.10 → 20.11.2026). Запись: `## В-N · дата время`, строки `> слова владельца`, `Суть:`, `Заменяет:`. Ранний реестр — [[settled-registry]].

## Карточки

- Одна карточка на решение — `kb/decision/В-N.md` (id с кириллической «В»), собирает [[tools/kb_import_decisions.py]]: слова владельца дословно, «Суть», «Заменяет», ссылки на упомянутые решения, протоколы, задачи. Конвертер идемпотентен; карточку без поля `source: kb_import_decisions` (написана руками, например В-161) не перезаписывает.
- «Заменяет: В-M» — только номер → `supersedes: [В-M]`, у В-M статус `superseded`. «Заменяет» с оговоркой («в части …», текст без номера) → у старой карточки пометка «Частично заменена», статус не меняется.
- Нумерация: **В-N** — этот реестр, **S-N** — SETTLED (в `.claude/preferences.md`: «В-NNN» — decisions.md, «SETTLED В-NNN» — SETTLED.md). Номера двух реестров пересекаются, id — нет.
- Поиск: `python tools/kb.py find "<слова>"`, `python tools/kb.py list --type decision --status superseded`.
