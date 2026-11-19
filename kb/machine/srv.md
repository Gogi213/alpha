---
id: srv
type: machine
status: running
title: "srv 5.231.25.224 — счёт, закачка, коллекторы G-022, сборки, страницы"
date: 2026-11-20
links: [srv-pages, srv-no-pkill, docs/COMMANDS.md]
supersedes: []
---

## Что это

Debian 13, 8 vCPU, 62 ГБ ОЗУ, NVMe 394 ГБ. Счёт, закачка, коллекторы G-022 (`/data/bnws`, `/data/bbws`, метрики `/data/health/<биржа>.json`), сборки.

## Вход

ssh с ПК владельца — всегда с `-i` и `-o UserKnownHostsFile=…` (кириллица в HOME): `ssh -i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts root@5.231.25.224`.

## Порты страниц

8088 — здоровье коллекторов, 8090 — страница П-42, 8091 — эта база (`kb-page`). Как они поднимаются — [[srv-pages]].

## Грабли

- Никаких `pkill` / `killall` по имени — [[srv-no-pkill]].
- `find` / `du` по большим каталогам — только внутри `systemd-run`: оборванная ssh-сессия оставляет их жить.
- Склад тиков `/data/p42-tk599/tick-store` доказан — не сверять повторно.

Команды и ранбуки — [[docs/COMMANDS.md]].
