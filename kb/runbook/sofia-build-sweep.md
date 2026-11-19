---
id: sofia-build-sweep
type: runbook
status: running
title: "София: диск, sweep.sh и таймер очистки сборок (TK-075)"
date: 2026-11-20
links: [sofia, tools/vps-sweep.sh, docs/COMMANDS.md]
supersedes: []
---
Источник — [[docs/COMMANDS.md]], раздел «VPS София — диск (TK-075)»; машина — [[sofia]].

Сборка идёт через `/opt/alpha-compute/sweep.sh run <target> <cmd>` (в репозитории — [[tools/vps-sweep.sh]]): под `.build.lock` чистит осиротевшие `target-*`; отказывает (rc 75 + тревога `диск.софия`), если область + 3 ГБ > 12 или свободно − 3 < 20 ГБ; общий постоянный `shared-target` (TK-169) живёт между сборками и сбрасывается при свободном < 10 ГБ (`SWEEP_SHARED_FLOOR_GB`); одноразовые `target-*` удаляет после сборки.

Таймер каждые 15 минут — транзиентный, **после перезагрузки VPS поднять**:

```bash
systemd-run --quiet --unit=alpha-sweep15 --on-calendar="*:0/15" bash -c "exec 9>/opt/alpha-compute/.build.lock; exec /opt/alpha-compute/sweep.sh"
```

Список бинарников, которые не чистятся, — `/opt/alpha-compute/bin-keep.txt`.
