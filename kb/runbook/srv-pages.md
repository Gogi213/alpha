---
id: srv-pages
type: runbook
status: running
title: "Страницы на srv: каталог /data/pages/<имя>, юнит <имя>-page, порт"
date: 2026-11-20
links: [srv, TK-629, tools/p42/ml_page_update.sh]
supersedes: []
---

Каждая страница — статический каталог `/data/pages/<имя>`, раздаётся `python3 -m http.server <порт> --directory <каталог>` как transient-юнит `systemd-run` с `Restart=always` (образец — `ml-page-8090`).

| порт | юнит | каталог | что |
|---|---|---|---|
| 8088 | — | — | здоровье коллекторов |
| 8090 | `ml-page-8090` | `/data/pages/ml` | П-42, обновление — [[tools/p42/ml_page_update.sh]] |
| 8091 | `kb-page` | `/data/pages/kb` | база знаний (TK-629) |

## База знаний (`kb-page`, 8091)

Обновление: `bash tools/kb_deploy.sh` — собирает сайт (`python tools/kb.py site`), заливает в `/data/pages/kb`, при первом запуске создаёт юнит. Проверка: `curl -s -o /dev/null -w "%{http_code}" http://5.231.25.224:8091/`. Остановить только свой юнит: `systemctl stop kb-page` ([[srv-no-pkill]] не нарушается). Машина — [[srv]]; задача — [[TK-629]].
