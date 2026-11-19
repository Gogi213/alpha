---
id: storage-box
type: machine
status: running
title: "Storage Box u677479@u677479.your-storagebox.de:23 — холодное хранилище сырья"
date: 2026-11-20
links: [srv, srv-offload, AGENTS.md, docs/findings/srv-offload-2026-11-18.md]
supersedes: []
---
## Что это

`u677479@u677479.your-storagebox.de`, порт 23, вход по ключу. Урезанный набор команд: `ls`, `du`, `df`, `rsync`, `sftp`.

## Что лежит

- `alpha/collect/<биржа>/<день>/` — сырьё коллекторов G-022 (выгрузка `collector-day-upload.timer`, sha256 каждого файла) — [[srv-offload]].
- `/home/offload-srv-2026-11-18/<каталог>` — холодные каталоги `/data/<имя>`, снятые с [[srv]] 18.11; манифест — [[docs/findings/srv-offload-2026-11-18.md]].
- Датасеты на srv и боксе удалять по слову владельца ([[В-131]], [[В-132]]); без слова — не удалять (`preferences.md` п. 3).
