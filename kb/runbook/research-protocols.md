---
id: research-protocols
type: runbook
status: running
title: "Протоколы исследований P-NN: где лежат, как связаны с журналом и реестром"
date: 2026-11-20
links: [P-42, decisions-registry, docs/research/JOURNAL.md, docs/plan/EXPERIMENTS.md, .claude/skills/alpha-research/SKILL.md, TK-632]
supersedes: []
---
Порядок (скилл [[.claude/skills/alpha-research/SKILL.md]], обязателен перед любым торговым выводом): вопрос → протокол до счёта, утверждает владелец → счёт строго по протоколу → отчёт с шумом и числом испытаний → вердикт по записанному правилу → журнал.

- Протоколы — `docs/research/P-<NN>-*.md`; одна карточка `kb/protocol/P-NN.md` на номер (статус и состояние — со слов файла, [[docs/research/JOURNAL.md]] и реестра [[docs/plan/EXPERIMENTS.md]]; если отчёта в них нет — `open`).
- Проверки Судьи — `docs/research/reviews/P-NN-*`; отчёты — `docs/findings/`.
- Действующий протокол — [[P-42]]; его задачи — карточки `TK-6xx`.
- Список: `python tools/kb.py list --type protocol`.
