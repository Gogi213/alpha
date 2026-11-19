---
id: page-8088-health
type: runbook
status: running
title: "Страница здоровья коллекторов http://5.231.25.224:8088/ (и :8089)"
date: 2026-11-20
links: [srv, srv-pages, G-022, tools/collector_health/serve.py, tools/collector_health/page.html]
supersedes: []
---
Показывает здоровье коллекторов [[G-022]] по биржам: столбики по 15 минут (макет v3, владелец: «кайф»), `/api/v3`, поминутные ряды `series-minutes.jsonl` в корне данных (передача 13.11 — [[docs/plan/G-022-handoff-2026-11-13.md]]).

- Юниты на [[srv]]: `health-page-8088` (порт 8088) и `health-soak-page` (порт 8089) — оба `python3 /data/turbo-probe/page/serve.py --health-dir /data/turbo-probe/health-soak --exchanges …/exchanges.json --thresholds …/thresholds.json --port <порт>` (`systemctl show`, 20.11).
- Код в репозитории — [[tools/collector_health/serve.py]], [[tools/collector_health/page.html]] (версии prod; слияние — [[TK-628]]).
- Метрики коллекторов — `/data/health/<биржа>.json` (AGENTS.md); выгрузка — `upload-<биржа>.json`.
- Все страницы srv — [[srv-pages]].
