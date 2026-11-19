---
id: page-8090-ml
type: runbook
status: running
title: "Страница П-42 http://5.231.25.224:8090/ (юнит ml-page-8090)"
date: 2026-11-20
links: [srv, srv-pages, P-42, TK-631, TK-624, tools/p42/ml_page_update.sh, tools/p42/ml_progress.json]
supersedes: []
---
Статическая страница `/data/pages/ml`, раздаётся `python3 -m http.server 8090` как `ml-page-8090` на [[srv]] ([[srv-pages]]). Протокол — [[P-42]].

- **Вкладки** (хеш): «Прогресс» `#live` (по умолчанию; что идёт сейчас — [[TK-631]]), «Ход работ» `#progress` (только этапы), «Эквити» `#equity/short|long|both` с блоком «Где мы» `#equity/short/where` ([[TK-624]]), «Цели TK-598», «Признаки», «Модель TK-592».
- **Обновление:** правим [[tools/p42/ml_progress.json]] (схема `tools/p42/ml_progress.schema.json`), затем `bash tools/p42/ml_page_update.sh` — заливает JSON и исходники на srv, проверяет по схеме, пересобирает `/data/pages/ml/index.html`. Подробности — `docs/COMMANDS.md`.
- **«Сейчас идёт»:** `now.json` кладёт с ПК раз в минуту `tools/now_push.py` ([[TK-631]]).
