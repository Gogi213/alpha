---
id: srv-offload
type: runbook
status: running
title: "srv: сырьё коллекторов уходит на Storage Box само (TK-608)"
date: 2026-11-20
links: [srv, storage-box, G-022, В-155, docs/COMMANDS.md, tools/srv-offload-delete.py, tools/srv-offload-dirs.py]
supersedes: []
---
Цепочка (TK-608, [[В-155]]; подробности и грабли — [[docs/COMMANDS.md]], раздел «srv — диск»): `collector-day-upload.timer` (каждый час, :17) → [[storage-box]] `alpha/collect/<биржа>/<день>/` с sha256 каждого файла → **`collector-offload-delete.timer` (каждый час, :47)** удаляет на [[srv]] закрытые сутки, сверенные на боксе. Скрипт `/opt/day-upload/offload-delete.py --keep-hours 37 --sample 20 --go` (в репозитории — [[tools/srv-offload-delete.py]]).

- **На srv остаётся:** текущие сутки (≈ 17 ГБ к концу дня) и сутки с данными моложе 37 ч (живой счёт `arb-live` читает окно 36 ч назад). В среднем текущие + 1–2 закрытых.
- **Сутки удаляются только при всех условиях:** `.ok` в `/data/upload-state/<биржа>/` равен локальным файлам (размер + mtime); на боксе совпали имена, размеры, число файлов и сумма байт; у случайных 20 файлов sha256 пересчитан заново и равен `sha256sum` на боксе; нет файла моложе 15 мин. Коллекторы не трогаются.
- **Журнал:** `journalctl -u collector-offload-delete` (строки `DEL` / `SKIP`). План без удаления — `systemd-run --wait --pipe -p Nice=19 -p IOSchedulingClass=idle python3 /opt/day-upload/offload-delete.py --keep-hours 37`. Выключить — `systemctl disable --now collector-offload-delete.timer`.
- **Вернуть сутки:** `rsync -a box:alpha/collect/<биржа>/<день>/ /data/collect/<биржа>/data/<день>/` (для `bybit` корень — `/data/turbo-probe/soak-bybit/data`).
- **Холодные каталоги `/data/<имя>`:** [[tools/srv-offload-dirs.py]] — один `rsync -aH` на набор, сверка (число файлов и ссылок, байты, повторный `rsync -n`, sha256 выборки), `--delete` только после сверки; запуск только через `systemd-run`.
