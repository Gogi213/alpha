---
id: page-8091-kb
type: runbook
status: running
title: "База знаний http://5.231.25.224:8091/ (юнит kb-page)"
date: 2026-11-20
links: [srv, srv-pages, TK-629, tools/kb_deploy.sh, kb/README.md]
supersedes: []
---
Статический сайт из карточек `kb/` (поиск, фильтры тип / статус / задача, обратные ссылки, граф): каталог `/data/pages/kb`, `python3 -m http.server 8091` как `kb-page` на [[srv]] ([[srv-pages]]).

- Обновление: `bash tools/kb_deploy.sh` ([[tools/kb_deploy.sh]]) — `kb check`, сборка `python tools/kb.py site`, заливка, юнит создаётся при первом запуске.
- Тесты: `python tools/tests/test_kb.py`; правила карточек — [[kb/README.md]]; создана в [[TK-629]].
