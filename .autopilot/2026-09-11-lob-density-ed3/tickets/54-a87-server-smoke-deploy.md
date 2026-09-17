# 54 — Смоук сценариев A8 на сервере и деплой (A8.7)

**Требования:** директива владельца 2026-09-17 («коллектор при любых обстоятельствах»), подтикет A8.7
в `docs/plan/dev-plan-2026-09-17.md` §Дополнения; порядок — A8.1b ✔ … A8.6 ✔ → **A8.7** → B5.
**Blocked by:** —
**Зона:** сервер (второй корень `/opt/alpha/smoke`, бинарник `alpha-smoke-a87`), отчёт
`docs/findings/collector-resilience-2026-09-18.md`, `docs/plan/dev-plan-2026-09-17.md`, `CLAUDE.md`
(§Состояние — после деплоя).
**Волна:** 13
**Status:** done (смоук и деплой 2026-09-17 21:55:21Z; аудит 18.09)

## Что сделано (смоук)

Собран на сервере из `git archive HEAD` (`29e8327`, сборка 1 мин 11 с — это же и компиляция
unix-ветки SIGTERM на настоящем Linux), прогнан во **втором** корне своим бинарником; прод не тронут.
Проверено живьём: добавление монеты, снятие и замена одной перечиткой, **шов соседям** (снята монета,
делящая сокет: 4 строки `sequence_gap` «шов покрытия — пересборка сокета при снятии HYPEUSDT»),
**SIGTERM → `closed=true`** (ветка A8.2 на Linux), **отказ подписки** фейкового символа
(`subscribe_failed` + счётчик). Числа и команды — `docs/findings/collector-resilience-2026-09-18.md`.

Живьём **не** проверялись (и почему): заполнение диска (общий `sdb` с боевыми данными), обрыв сети
(`iptables` под root заблокировал бы SSH), шаг часов (`timedatectl` сдвинул бы часы живого коллектора)
— все три закреплены тестами с двойниками, тикеты 51/52/53.

## Что осталось (деплой — по слову владельца)

`touch /opt/alpha/root/stop` → дождаться `closed=true` → бэкап
`sudo -n cp /opt/alpha/alpha-collector /opt/alpha/alpha-collector-prev-20260918` →
`sudo -n cp /home/ubuntu/alpha-smoke-a87 /opt/alpha/alpha-collector` →
`sudo -n systemctl start alpha-collector` → проверить `active`, свежий `session.json`
(`connect_failed`/`gap_rows_failed`/`subscribe_failed`) и что файлы растут. После этого:
`CLAUDE.md` §Состояние (новый коммит бинарника), память, `alpha-verify` тем же бинарником —
это отдельный маленький шаг, потому что `alpha-verify` живёт в `/opt/alpha/alpha-verify`.
