---
id: sofia
type: machine
status: running
title: "VPS София 13.140.29.171 — сборки и проверки по умолчанию"
date: 2026-11-20
links: [srv, sofia-build-sweep, storage-box, TK-628, AGENTS.md, tools/vps-check.sh]
supersedes: []
---
## Что это

4 ядра, 7,9 ГБ ОЗУ. Сборки и проверки по умолчанию (`vps-check.sh`, TK-614, приказ владельца 19.11 «нагрузим под 90 % софию»); счёт — нет (кроме В-101). Сторож места режет сборки при свободном < 23 ГБ; `vps-check.sh` отказывает с rc 76 при свободном < `VPS_MIN_GB`=28 ([[tools/vps-check.sh]]; на 20.11 TK-628 получил отказ при 26 ГБ и гонял гейт на [[srv]]).

## Команды

```bash
bash tools/vps-check.sh <рабочее дерево> all           # test + clippy -D warnings + fmt на Софии (CHECK_HOST=srv — на srv; build — srv)
bash tools/vps-check.sh <рабочее дерево> test <фильтр>
```

`build` идёт на srv, при занятом раннере srv и свободной Софии — на Софии. Диск и очистка — [[sofia-build-sweep]].

## Грабли

- Сервисы Solta и docker на этой машине принадлежат владельцу — не трогать ([[В-154]]).
- Binance и другие биржи через Софию не подключаем, коллекторы — на srv ([[В-153]]).
