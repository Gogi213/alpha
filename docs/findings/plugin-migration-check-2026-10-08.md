# Сверка переноса alpha → плагин 1.8.2 (TK-088, 08.10.2026)

Метод: только чтение. Старое — тег `alpha-dispatcher-pre-plugin` (`.claude/dispatcher/*.py`, `.claude/hooks/*.py`,
`settings.json.pre-plugin`), `~/.claude/hooks/alpha_one_build.py`, блокнот CEO. Новое — кэш плагина 1.8.2
(`hooks/hooks.json`, `.claude/hooks`, `.claude/dispatcher`), User-окружение Windows, `supervise.env.json`, Планировщик,
живые процессы. Сервер и Steam Deck не трогались.

## Итог в трёх строках

1. **Дыр, которые бьют сейчас, — три:** `ci_watch` и `tools/pulse` не под присмотром (после перезагрузки не встанут);
   секреты/настройки команды лежат в User-окружении всей машины (течёт в тесты и чужие проекты); хук владельца
   `~/.claude/hooks/alpha_one_build.py` не в репозитории (нет копии).
2. Правила сторожа и стражей перенесены **полностью по значениям** (списки путей, хосты, исключение `alsched` — все
   совпали с тегом). Дыры в способе доставки (env), не в правилах.
3. Ручные шаги CEO после перезагрузки: диспетчер/сторож — сами (задание Планировщика); `ci_watch`, `pulse collect`,
   `pulse web`, Monitor на `ceo-wake.log`, мосты-флаги — вручную.

## 1. Что поднимается само после перезагрузки ПК

| Что | Было (тег) | Сейчас (1.8.2) | Статус |
|---|---|---|---|
| диспетчер `dispatch.py` | WMI вручную, CEO; `alpha-supervise` | Планировщик `rpv-supervise-5376d7eb`, каждые 5 мин с 01:47, `supervise.py` → `start.spawn`; env — снимок `supervise.env.json` | **есть** |
| сторож `watch.py` | то же | то же (`BEATS` = dispatch + watch) | **есть** |
| `ci_watch.py` (автовлив PR) | не было | работает (pid 22160), поднят вручную WMI; в `supervise.BEATS` его нет, сердцебиения у него нет | **нет** (Д-1) |
| `tools/pulse/collect.py`, `pulse/web/server.py`, `mcp_server.py` | WMI вручную (блокнот CEO стр. 296) | так же вручную, не под присмотром | **нет** (Д-1) |
| Monitor `tail -F ceo-wake.log`, мосты `tk079-ci.flag`, авто-влив | сессия CEO | умирают с сессией CEO | **не нужно после TK-079/077** (проверить) |
| старое задание `alpha-supervise` | Планировщик | `Disabled`, осталось | **не нужно** — удалить (Д-6) |
| шина (сервер 89.163.242.211:8788) | — | systemd на сервере, мимо ПК | есть |

Нюансы Планировщика: задание `DisallowStartIfOnBatteries=True`, `StopIfGoingOnBatteries=True`, `StartWhenAvailable=False`
— на ноутбуке от батареи диспетчер и сторож не поднимутся (на стационарном ПК — безвредно; проверить тип ПК).
Снимок `supervise.env.json` делается при `--install`: правка User-окружения без переустановки не доходит до
перезапущенного диспетчера. Сейчас в снимке есть и `RPV_DISPATCH_MAX_RUNS_PER_TICKET_HOUR=20`, и
`ALPHA_DISPATCH_MAX_RUNS_PER_TICKET_HOUR=12`: `project.env` берёт RPV-имя первым — работает 20, но 12 вводит в заблуждение.

## 2. Правила сторожа alpha

| Правило | Тег (`watch.py`) | 1.8.2 (`watch.py`) | Статус |
|---|---|---|---|
| интервал, повторы 2 ч / 24 ч, сводка 1 ч, ложная тревога ssh (повтор, серия 3 цикла → сводка) | `ALPHA_WATCH_*` | `P.env("WATCH_*")` — те же имена, те же умолчания | есть |
| нормализация сигнатуры ALERT (без времени) | есть | есть | есть |
| ALERT-idle-deck при HOLD = ожидаемо | есть | есть (`deck-idle-expected`) | есть |
| deck-off: Steam Deck не трогать | флаг `deck-off`, умолч. хост `deck@192.168.1.49` | флаг `deck-off` **и** нет `RPV_DECK_HOST` → `deck_off()` истина; умолчания хоста нет | есть. В окружении `DECK_HOST` не задан — сторож на деку не ходит |
| простой сервера счёта (load < 4, ожидание замка > 20 мин → будить владельца держателя) | `SERVER_ALIAS="calc"` | то же; `RPV_WATCHED_ALIASES=calc`, `RPV_CALC_HOST=root@89.163.242.211` | есть |
| мёртвые ожидания (2 подряд), стопоры, сироты, без плана 30 мин | есть | есть | есть |
| сторож при обрыве связи снимает все `wait_for` | дефект (5 тикетов 07.10 21:40–00:50) | не исправлен, `SSH_FAIL_STRIKES` добавлен — проверить, хватает ли | **иначе**, в аудит TK-078/080 |
| осиротевшие `RPV_DECK_KEY`, `RPV_DECK_KNOWN_HOSTS` | — | заданы без `DECK_HOST` — мёртвые | не нужно (Д-3) |

## 3. Стражи

Хуки подключены `hooks/hooks.json` плагина (Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit → `delete_guard`,
`ceo_signal_guard`; SessionStart/UserPromptSubmit/SessionEnd/Notification/Skill → `role_context`/`role_memory`;
**новый** Stop → `stop_result`). Проектный `.claude/settings.json` хуков не содержит. Плюс хук владельца в
`~/.claude/settings.json` (`alpha_one_build.py`).

| Страж | Список / исключение | Было | Сейчас | Статус |
|---|---|---|---|---|
| `delete_guard` — свои корни удалённых машин | `~/alpha/`, `$home/alpha/`, `${home}/alpha/`, `/home/deck/alpha/`, `/opt/alpha-compute/` | в коде | `RPV_GUARD_REMOTE_ROOTS` (User) — совпадает 1:1 | есть (через env) |
| корни хоста | 89.163.242.211: `/home/deck/alpha/,/root/tk0,/data/tk0,/data/registry/,/tmp/,/opt/alpha-board/`; 13.140.29.171: `/opt/alpha-archive/stage/,/opt/alpha-archive-tk021/dup-reimport/` | в коде | `RPV_GUARD_HOST_ROOTS` — 1:1 | есть (через env) |
| стадия `/dev/shm/alpha-stage`, запрет коллектора 139.99.91.22 | в коде | `RPV_GUARD_STAGE`, `RPV_GUARD_FORBIDDEN_HOSTS` — 1:1 | есть (через env) |
| запись в автопамять проекта | зашит путь `c--visual-projects-alpha` | выводится из `CLAUDE_PROJECT_DIR` | есть |
| `heavy_guard` (бывш. `bench_guard`) — тяжёлое на 89.163.242.211 | в коде | `RPV_GUARD_HEAVY_HOST` (+`_HINT`) — 1:1; без переменной проверка выключена | есть (через env) |
| исключение `alsched submit/ps/cancel/wave/stand/reprio/thaw` | не было (дыра TK-077) | `RPV_GUARD_HEAVY_ALLOW` — регэксп есть, в User и в снимке | **есть** (ограничение: исключение пропускает любую команду после `--`; так задумано — очередь `alsched` её же ставит под CPUQuota) |
| `ceo_signal_guard` | `ceo-inbox.md`/`ceo-wake.log` только через шину | отличия — только текст подсказок (`result blocked|ask-owner` вместо `comment --next ceo`) | есть |
| `role_context`/`role_memory` | роль из `ALPHA_ROLE`, метки сессий | `RPV_ROLE`, запасное `ALPHA_ROLE`; метка сессии `/ceo` — новое; нет `.claude/roles` → молчат | есть, шире |
| хук владельца `alpha_one_build.py` (запрет cargo на ПК + fail-closed) | искал `delete_guard` в проекте | ищет в `installPath` плагина по `installed_plugins.json` (Д-4: дыра закрыта) | есть. Но: файл вне репозитория, копии нет; страж удаления теперь вызывается **дважды** (плагин + хук владельца) — безвредно, лишние ~сотни мс на вызов |
| fail-closed плагина при отсутствии Python | — | зашит в `hooks.json` | есть |

Где env действительно виден стражу: хук исполняется в процессе Claude. Сессии диспетчера получают env из процесса
диспетчера (`set …` в `cmd`, снимок `supervise.env.json`), интерактивные сессии CEO/владельца — только из User-окружения на
момент запуска Claude Code. Сессия, запущенная до записи User-переменных, **не видит** списков → стражи молча пустые
(`REMOTE_ROOTS=()`, `HEAVY_HOST=""`) = отказ для всех удалённых удалений и отключённый замок замеров. Данная сессия (запуск
диспетчера) видит 7 `RPV_GUARD_*`.

## 4. Что читает User-окружение и кому вредит

User-переменные (Windows): `RPV_BUS_URL`, `RPV_BUS_TOKEN_FILE`, `RPV_GUARD_*` (7), `RPV_STOP_STRICT`,
`RPV_DISPATCH_*` (5), `RPV_CALC_HOST`, `RPV_VPS_HOST`, `RPV_WATCHED_ALIASES`, `RPV_PROGRESS_DIR` (путь на сервере, `/data/progress`),
`RPV_DECK_KEY`, `RPV_DECK_KNOWN_HOSTS`, `ALPHA_DISPATCH_{MODEL,MAX_RUNS_PER_TICKET_HOUR,DAILY_COST_USD,HOUR_COST_USD,RUN_CAP_USD}`.

| Переменная | Кто читает | Вред |
|---|---|---|
| `RPV_BUS_URL` + `RPV_BUS_TOKEN_FILE` | `bus_link`, `tickets.py`, `dispatch` | любой процесс на ПК (pytest плагина и alpha, `tickets.py` в чужом проекте) пишет в **живую** шину alpha; `test_bus_link.py` URL подменяет, `test_signals.py`/`test_doctor.py` — частично, остальные наследуют (причина 07.10) |
| `RPV_STOP_STRICT=1` | `stop_result` (Stop-хук) | во всех проектах и сессиях, где стоит плагин; в проекте без `active_runs` хук возвращает молча, но роль alpha-подобных запусков получит строгий отказ без `result` |
| `RPV_GUARD_*` | `delete_guard`, `heavy_guard` | в других проектах плагина молчат (нет `.claude/roles`), но в любом проекте с командой ролей ограничивают удаление этими путями alpha |
| `RPV_DISPATCH_*`, `RPV_WATCHED_ALIASES`, `RPV_*_HOST` | диспетчер/сторож | чужой диспетчер на этом ПК унаследует лимиты и параллельность alpha |
| `ALPHA_DISPATCH_{DAILY,HOUR,RUN_CAP}_COST_USD` | **никто** (в 1.8.2 нет чтения) | мёртвые; лимитов денег нет (В-173) — убрать |
| `ALPHA_DISPATCH_MAX_RUNS_PER_TICKET_HOUR=12` | перекрыта `RPV_…=20` | противоречие, убрать |
| `RPV_DECK_KEY`, `RPV_DECK_KNOWN_HOSTS` | сторож, только при `DECK_HOST` | мёртвые (дека выведена) |

Правильное место для проектных значений — не User-окружение, а **`env` в `<проект>/.claude/settings.json`** (действует на
сессии Claude в alpha, не течёт в чужие проекты и в голые терминалы) плюс `supervise.env.json` (диспетчер/сторож). Тесты
плагина всё равно должны сами чистить `RPV_*`/`ALPHA_*` (autouse-фикстура или `setUp`), иначе при запуске из сессии alpha
они снова упрутся в живую шину.

## 5. Ручные шаги CEO, которые раньше делал скрипт

| Шаг | Раньше | Сейчас | Статус |
|---|---|---|---|
| поднять диспетчер/сторож после перезагрузки | вручную WMI с env | Планировщик, 5 мин | есть |
| поднять `ci_watch` | — | вручную WMI (`--repo=Gogi213/role-play-vibing`, блокнот CEO стр. 198) | **нет** (Д-1) |
| поднять `pulse collect.py` / `web/server.py` | вручную WMI | вручную | **нет** (Д-1) |
| `plugin-final.flag` / `tk077-window.flag` / `rpv-1.8.2-installed.flag` / `tk076-*.flag` | мосты CEO на время переноса | 20 untracked `*.flag` в `.claude/dispatcher/` | не нужно после TK-079; прибрать (Д-6) |
| Monitor на `ceo-wake.log` | сессия CEO | то же; шина → `tickets.py inbox` | иначе |
| обновление `supervise.env.json` после правки env | не было | `supervise.py --install` вручную | **нет** (Д-5) |
| правка `~/.claude/settings.json`/`alpha_one_build.py` | — | вручную, вне репозитория | **нет** (Д-4) |
| хост-ключи/ssh-алиасы `calc`/`vps` | ALPHA_* в коде | `RPV_CALC_HOST`/`RPV_VPS_HOST` в User + снимок | есть |

## Дыры и что с ними делать

| № | Дыра | Одно предложение | Тикет |
|---|---|---|---|
| Д-1 | `ci_watch` и `pulse` (collect/web) не под присмотром, после перезагрузки не встанут | добавить в `supervise.BEATS` `ci_watch` (сердцебиение-файл + `RPV_CI_REPO` в снимок env), а `tools/pulse/*` — вторым заданием Планировщика alpha | TK-077 (плагин: ci_watch), alpha-задача для pulse — CEO |
| Д-2 | env команды в User-окружении течёт в тесты и чужие проекты | перенести `RPV_GUARD_*`, `RPV_BUS_*`, `RPV_STOP_STRICT`, `RPV_DISPATCH_*` в `env` проектного `settings.json` и `supervise.env.json`, из User убрать; сессии CEO запускать после этого | TK-077 |
| Д-2б | тесты плагина читают живую шину | autouse-чистка `RPV_*`/`ALPHA_*` в `setUp`/conftest `test_signals.py`, `test_doctor.py` и остальных, где не подменяется `RPV_BUS_URL` | TK-079 (или 085–087 по маршруту) |
| Д-3 | мёртвые и противоречащие переменные (`ALPHA_*_COST_USD`, `ALPHA_…MAX_RUNS…=12`, `RPV_DECK_KEY`, `RPV_DECK_KNOWN_HOSTS`) | удалить вместе с переносом в Д-2 | TK-077 |
| Д-4 | хук владельца `alpha_one_build.py` и его запись в `~/.claude/settings.json` вне репозитория; страж удаления вызывается дважды | положить копию в `tools/` alpha (с описанием подключения) и убрать из хука владельца вызов `delete_guard` — он уже в `hooks.json` плагина, оставить только запрет cargo | TK-077 |
| Д-5 | снимок `supervise.env.json` устаревает после правки env | сделать в `supervise.py` перечитывание проектного `settings.json` вместо снимка либо документировать «env → `--install`» в ранбук `docs/COMMANDS.md` | TK-079 (плагин) / CEO (ранбук) |
| Д-6 | мусор после переноса: задание `alpha-supervise` (Disabled), 20 `*.flag`, `.claude/settings.json.pre-plugin`, `ssh-calls.log` в рабочем дереве | удалить задание Планировщика, флаги в `.gitignore` или в архив, одним коммитом после финального аудита | TK-077 |
| Д-7 | батарейные флаги задания `rpv-supervise-*` (`DisallowStartIfOnBatteries`) и `StartWhenAvailable=False` | проверить, что ПК стационарный; иначе создавать задание с отключёнными флагами и запуском «как можно скорее после пропуска» | TK-077 |
| Д-8 | сторож снимает `wait_for` при обрыве связи ПК (дефект 07.10 21:40–00:50, 5 тикетов) — исправление не подтверждено (появился `SSH_FAIL_STRIKES`, но связи ПК→интернет это не касается) | воспроизвести тестом «обрыв связи ПК» и подтвердить, что `wait_for` не снимаются | аудит TK-078/080 |
| Д-9 | сессия, запущенная до записи User-переменных, не видит списков стража (молчаливо пустой страж) | если Д-2 принят — список лежит в `settings.json` проекта, и проблема исчезает; иначе добавить в `role_context` предупреждение «`RPV_GUARD_*` пусто, но на машине замеров есть юнит» | TK-077 |

Не проверялось (вне ограничения «только чтение, без сервера»): состояние сервера шины и `benchrun`/`alsched` на
89.163.242.211; содержимое `~/.claude/shell-snapshots`; поведение Планировщика после реальной перезагрузки (проверено по
свойствам задания и `LastTaskResult=0`).
