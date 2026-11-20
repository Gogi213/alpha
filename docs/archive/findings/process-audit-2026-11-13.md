# Аудит процессов: оверспенд и незакрытое (G-022), 13.11.2026 ~01:00 GMT+4

Приказ владельца: «так пока идет рисеч выяясни где у нас был оверспенд и кривые процессы которые мы не закрыли».
Только чтение: ничего не остановлено, не удалено, не слито. Пометки: **[и]** измерено, **[в]** вывод, **[д]** догадка.

## Как мерили

- Токены — `bb thread log <id> --json` → событие `thread/tokenUsage/updated`, поле `total` (накопительное по треду; максимум).
  «Всего» включает повторные чтения кэша (cacheRead ≈ 98 % всего), поэтому реальную работу показывает «выход» (output). Для
  `thr_bjufupu9yg` и `thr_5bc3a4v4az` bb не записал токены (0) — их расход неизвестен. Тред `thr_yidkdm8xur` — тоже 0 (упал за 1,5 мин).
- Время — первое/последнее событие треда (GMT+4). Коммиты — `git log`. Серверы — ssh read-only (`systemctl`, `ps`, `df`, `journalctl`), без `find`/`du`.
- Режим «fast» (быстрый Opus) bb в логах не показывает — **не измерено**.
- Контекст — `bb thread context`; порог владельца 350 тыс. (приказ 24).

## Итого (измерено)

| Группа тредов | Тредов | Выход, токены | Всего, млн | Комментарий |
|---|---|---|---|---|
| Python-коллекторы (TK-527/531/532, integrate) | 9 | 1 022 457 | 155,9 | заменены Rust по В-149 (12.11 12:55) |
| Rust-коллектор (исп v1/v2, пров v1/v2) | 5 | 959 897 + (2 без учёта) | 293,2 | нижняя граница: пров v2 не учтён |
| Подготовка бирж (OKX; Bitget/HTX/BingX) | 2 | 448 566 | 74,6 | в `master` не слито, не запушено |
| Прочее (миграция, уборка, слияние, удаление датасетов, координатор) | 6 | 246 494 | 44,3 | |
| **Все 22 треда** | 22 | **≈ 2,68 млн** | **≈ 568** | **[и]** |

Время работы в окне 11.11–13.11: 66 коммитов Python-ветки (19 — 11.11, 47 — 12.11), 85 коммитов Rust-ветки `g022-turbo` за ~7 ч (12.11 13:19 → 20:05).

## Таблица 1. Оверспенд

| № | Где | Сколько | Почему это перерасход | Что закрывает | Док-во |
|---|---|---|---|---|---|
| О-1 | Python-коллекторы Binance+Bybit (TK-527/531/532, `g022-integrate`) переписаны на Rust через ~16 ч | 9 тредов, выход 1,02 млн токенов, 155,9 млн всего, 66 коммитов, +26 958 строк Python в 138 файлах; **0 строк в `master`** (`tools/collector/` в master: 0 файлов; в `g022-integrate`: 46) **[и]** | Полная Python-работа (ядро `wscore/`, 2 адаптера, лестницы, приёмки Судьи) заменена приказом В-149 «переделываем в раст» (12.11 12:55), на Rust перенесено поведение, а не код **[в]**. Переиспользуется только знание (отчёты по лимитам/маршруту) **[в]** | архив ветки `g022-integrate` тегом (см. Н-1); отчёты — в docs; в будущем: до крупной разработки спросить язык (В-148→В-149 пришли после старта) | `git rev-list --count master..g022-integrate` = 66; `.claude/decisions.md:831-839` |
| О-2 | Binance «лестница» (TK-531): 4 треда на один объект | `thr_yidkdm8xur` v1 — упал за 1,5 мин (error); `thr_upjqwagznd` v2 исп 0,5 ч; `thr_3748xfhbvs` пров — ВЕРНУТЬ; `thr_hn92g23fjy` v3 исп **6,2 ч**, 23,4 млн всего, выход 126 тыс. **[и]** | v3 6,2 ч закончились итогом «устойчивого выигрыша нет» (коммит `a0bbf202`) — 6 ч теневого замера на нулевой результат **[в]**. Причина возврата проверки (излом на пике, порог 300) — требования, не уточнённые до старта **[д]** | отчёт принят как отрицательный результат; правило: пробные замеры — фоновым юнитом (В-147), а не тредом на 6 ч | `git log a0bbf202`; `bb thread log` thr_hn92g23fjy |
| О-3 | Bybit TK-532: 2 исп. + 2 пров., обе проверки ВЕРНУТЬ, приёмки не было | `thr_jw4tqpx26g` 3,7 ч, **463 тыс.** контекста, 43,5 млн; `thr_tdywiip4wk` 3,3 ч, **397 тыс.**, 37,6 млн; пров. `thr_n2ufrnbd7n`, `thr_p87tqmpwed` — по 3,5–3,8 млн. 23 коммита TK-532 **[и]** | 2 круга «возврат → доработка», последний возврат (`abbe7dd9`: стакан через kz не сверен) остался без третьего круга; потом — переписывание на Rust **[и]** | закрыть как «не принято, заменено Rust», архив веток `tk532-*` | `git log abbe7dd9`, `2bb70977`; план §4 строка 2 «не принят — остановлен» |
| О-4 | Rust-коллектор: исполнитель v1 | `thr_i42yzre75q`: 6,2 ч, **24 вмешательства** (inputs), 1 156 действий, **255,3 млн всего, выход 787 тыс. (максимум среди всех тредов)**, контекст 308 тыс. **[и]** | Один тред — 29 % всех токенов выхода (787 тыс. из 2,68 млн); 85 коммитов, из них 36 — «отчёт» и 15 правок одного файла отчёта (302 строки → 344 строки, 67 КБ) **[и]**. Отчёт растёт правками вместо одной редакции **[в]** | правило брифа: отчёт — одной редакцией в конце, не коммит на каждый замер | `git log master..origin/g022-turbo --format=%s \| grep -c отчёт` = 36 из 92 |
| О-5 | Проверка Rust v1 нашла то, что можно было предусмотреть: «ядро портировано как в Python — с тем же бюджетом подключений без приоритета» | v1 пров 0,2 ч, вердикт RETURN, 7 пунктов; v2 исп 1,4 ч, 33,7 млн всего, выход 140 тыс. **[и]** | Приказ 23 «переносить как было» + перенос дефекта (553 отброшено против 97 подключений за 13 мин, 9 из 25 шардов без соединения 305 с) → круг возврата **[и]**; сам дефект Python-версии не фиксировался отдельно **[в]** | пункт 1 возврата закрыт коммитом `a82b5a51` | `g022-turbo-review-2026-11-12.md` (ветка `bb/g-022-rust-collector-v1-thr_pgx62pdrqp`), `git log a82b5a51` |
| О-6 | Проверка Rust v2: 2 параллельных проверяющих на один объект, оба остановлены | `thr_bjufupu9yg` (с 12.11 20:06, 4,1 ч, 2 хода, остановлен владельцем 13.11 00:14); `thr_5bc3a4v4az` (13.11 00:08–00:12, заархивирован, среда `env_3j75nd5hbv` недоступна) — токены не записаны **[и]** | Проверка v2 не завершена; вердикта нет; последнее сообщение: «waiting on check unit and gate» — ждёт гейт, который в моменте не нужен без задачи (приказ 24) **[и]** | вернуть `thr_bjufupu9yg` к жизни только по задаче владельца; `thr_5bc3a4v4az` — удалить/оставить архивным | `bb thread output thr_bjufupu9yg`; `bb thread show thr_5bc3a4v4az` |
| О-7 | Подготовка бирж: Bitget/HTX/BingX в одном треде | `thr_g6w5izsjsn`: 1,3 ч, **контекст 602 тыс.** (1,7× порога), 58,9 млн всего, выход 312 тыс.; OKX `thr_mtp79ffj9f`: 295 тыс., 15,7 млн **[и]** | Контекст втрое выше нормы при 3 биржах в одном треде; адаптеров нет — подготовка ушла вперёд ядра **[в]**. Две ветки не запушены (Н-3) | клир/разбивка по биржам; слияние docs в `master` | `bb thread context thr_g6w5izsjsn` = 602 032 |
| О-8 | Треды с контекстом выше 350 тыс. (приказ 24) | `thr_jw4tqpx26g` 463 тыс., `thr_g6w5izsjsn` 602 тыс., `thr_tdywiip4wk` 397 тыс., `thr_ki49k6g54p` 365 тыс. (4 из 22); `thr_bzj47264ji` 334 тыс., `thr_mtp79ffj9f` 295 тыс., `thr_i42yzre75q` 308 тыс. рядом с порогом **[и]** | Приказ 24 и сторож `context_guard.py` введены 13.11 00:21 — после этих тредов, задним числом не применимы **[в]** | тред закрыт/idle — докладывать не нужно; впредь сторож | `bb thread context`; коммит `2bdc96c3` |
| О-9 | Ожидание «удаления датасетов» тредом | `thr_3mwjykp458`: 4,2 ч, 29 ходов при 4 вводах (25 автопродолжений), 3,0 млн всего **[и]** | Тред ждал долгую операцию (серверное удаление 315→23 ГБ) вместо фонового юнита — то, что В-147 запретил позже (12.11 12:20) **[в]** | В-147 теперь действует | план §4 строка 0; `bb thread log` |
| О-10 | Уборка, не связанная с G-022, в разгар G-022 | `thr_idcqtsjp6v` lint-budget: 1,2 ч, 13,3 млн, выход 98 тыс. (969 → 832 срабатываний в `src/statarb`); `thr_p2ik57yyt2` compute-sprawl: 0,5 ч, 5,3 млн (430 скриптов в `archive/`) **[и]** | Не нужны для цели В-152 («хороший коллектор»); был ли приказ владельца — **не установлено** (приказ 24 «треды только под задачу владельца» появился позже) **[д]** | — | коммиты `18a9100b`, `ee5d5797` |
| О-11 | Переезд rpv → bb (тред миграции) | `thr_2es4s7puki`: 9 ходов, 10,9 млн всего, выход 52 тыс.; `thr_2htg7t89tw` — пробный codex-тред упал за 1 мин (`error`) **[и]** | По слову владельца «кодекса нет и не будет» — Codex не использовать; пробный тред напрасный. Сам тред миграции указал главную утечку: rpv сам запускал сессии ролей без владельца (его слова в отчёте треда, не проверено мной) **[д]** | Codex исключён из pstack (`d150bb85`) | `bb thread output thr_2es4s7puki`; `bb thread show thr_2htg7t89tw` |
| О-12 | Процессы srv, жгущие CPU/RAM без задачи | `/root/p40-ready.py` (счётчик готовности П-40, работает 2,16 дня, 0,4 % CPU); `p40-reap-timer` — каждые 20 мин **падает** (exit 2, `ls: cannot access 'alpha/stakan/bybit-linear-ob200'`), стартует `ssh`+`rsync` к Storage Box впустую; `until ! pgrep -f t522.py` — цикл ожидания 2,4 дня; `tail -F /data/bbws-entry/watch.log` 16 ч **[и]** | П-40 на паузе (AGENTS.md «Сейчас»); таймер не выполняет работу и бьёт по боксу; чистка не требует владельца **[в]** | остановить после слова координатора (Н-6) | `systemctl list-timers`; `journalctl -u p40-reap-timer.service -n 6` |
| О-13 | tmpfs в RAM: `/dev/shm` 11 ГБ, `/tmp` 4,3 ГБ (≈ 15 ГБ «shared» из 62 ГБ) | `free -g`: shared 15 ГБ; в `/dev/shm` — `c1_*.cfg`, `b_py`, `b_rs`, `auxv*`; `/tmp` — 2 102 объекта **[и]** | Остатки разовых проб П-40/L1 — занимают оперативку, мешают сборкам и коллекторам; `soak-*` сейчас ест 103 % + 77 % CPU (нормально) **[в]** | чистка по списку Н-6 | `df -h /dev/shm /tmp` |
| О-14 | Fast mode | **не измерено** | bb не даёт модель/режим в логах тредов; AGENTS.md/preferences режим не называют | — | — |

## Таблица 2. Незакрытое

«Владелец?» — только деньги, необратимое, удаление сырых данных (приказ 1, 3). Остальное решает координатор.

| № | Что | Где | Состояние | Предлагаемое закрытие | Владелец? | Док-во |
|---|---|---|---|---|---|---|
| Н-1 | Python-ветки G-022 без тега `archive/` | `g022-integrate` @ `3f8e1ce6` (66 коммитов, не запушена, worktree `thr_ki49k6g54p-1`); `tk527-binance-ws`, `tk532-bybit-ws`, `tk531-binance-ladder`, `tk532-bybit-accept` + `bb/tk-531-*`, `bb/tk-532-*` | Правило AGENTS.md «Законченная ветка — тег `archive/<имя>` и удаление»: тег есть только `archive/g022-master-merge`. `git branch --no-merged master` = 38 локальных, 33 удалённых **[и]** | тег `archive/g022-python-*` и удаление веток/worktree (3,8 ГБ worktree-ов на ПК, 19 штук) **[и]** | нет | `git branch --no-merged master \| wc -l`; `du` каталога worktrees (ПК) = 3,8 ГБ |
| Н-2 | `g022-turbo` локальная отстаёт от origin | локально `9780557e` (worktree `thr_i42yzre75q-1`), origin `a32ef34f` — 0 впереди, **7 позади**; в worktree 3 грязных файла (`tk525-*-run.txt`, `.claude/dispatcher/`) **[и]** | Fresh-ветки проверяющего/исполнителя v2 (`bb/g-022-rust-collector-v2-*` ×3 на `a32ef34f`) дублируют одно состояние **[и]** | `git pull --ff-only` в worktree, удалить 2 из 3 дубликатов v2 | нет | `git rev-list --left-right --count g022-turbo...origin/g022-turbo` = `0 7` |
| Н-3 | Не запушенные ветки подготовки бирж | `okx-ws-prep` @ `a37997dd` (119 файлов, +8 216), `g022-prep-3` @ `28bf91f6` (214 файлов, +2 177), `g022-integrate` — **единственная копия** на этом ПК **[и]** | Handoff называет «готово, в master не слито (только docs)», но копии нет нигде, кроме диска ПК | `git push origin <ветка>` для каждой (docs, секретов нет — проверить грепом) | нет | `git rev-parse --verify origin/okx-ws-prep` → нет; handoff строка 11 |
| Н-4 | Rust-коллектор не слит в master | `g022-turbo` / `origin/g022-turbo` (92 коммита, 255 файлов, +58 682) | Проверка v1 — RETURN (7 пунктов, закрыты коммитами `a82b5a51…a32ef34f`); проверка v2 остановлена без вердикта (О-6); слияние ждёт | по задаче владельца: пров v2 → слияние | нет (после его слова «проверяй») | handoff стр. 7; `bb thread show thr_bjufupu9yg` |
| Н-5 | Обещанные «возвраты» без повторной проверки | `thr_3748xfhbvs` (TK-531: излом, порог 300, пинг), `thr_n2ufrnbd7n`/`thr_p87tqmpwed` (TK-532: стакан через kz, пара линий com/kz по IPv4) | Доработки есть, третьей проверки нет — ни приёмки, ни закрытия; код заменён Rust | пометить «закрыто заменой» в плане §4; в `docs/plan/G-022-collectors-plan-2026-11-12.md` | нет | `git log abbe7dd9`; план §4 |
| Н-6 | srv: упавшие transient-юниты | `systemctl --failed`: `bnws-recheck-i`, `bnws-recheck-i2`, `bnws-smoke`, `bnws-smoke128`, `bnws-smoke64`, `g022-zstdcheck2`, `p40-d1v4smoke`, `p40-gape2e`, `p40-reap-timer`, `p40-v2pace`, `p40-v3pace`, `p40-v3pace8`, `p40charts-hour`, `p40ref-book2`, `py-check-binance` (**15 юнитов**) **[и]** | Висят в списке failed; не едят ресурсы, но ломают сводку `systemctl --failed` и скрывают реальный сбой | `systemctl reset-failed` после слова координатора; запущенные: `soak-binance`, `soak-bybit` (19 106 с, бинарь `alpha-9f79c4d8`), `health-page-8088`, `health-soak-page` — **не трогать** | нет | `systemctl --failed --no-legend` |
| Н-7 | srv: таймеры | `alpha-day-upload.timer` (каждый час :17, TK-530, ветка `tk530-day-upload` не слита; `day_upload.py:197` `shutil.rmtree` **локальных суток после сверки при свободном < порога**; сейчас свободно 334 ГБ → no-op); `p40-reap-timer.timer` (падает, О-12) **[и]** | В-136 «выгрузка на Storage Box — последним»; таймер включён заранее (`enabled`) | решение координатора: отключить `alpha-day-upload.timer` до слияния TK-530 или оставить; reaper: остановить (П-40 на паузе) | **да — только для `alpha-day-upload`**, если включение означает возможное удаление сырых суток (приказ 3) | `systemctl list-timers`; `/root/day-upload/day_upload.py:185-197` |
| Н-8 | srv: данные коллекторов Python | `/data/bnws` (2026-11-11: 440 файлов, 2026-11-12), `/data/bbws`, `/data/bbws-*`, `/data/bnws-*`, `/data/*-probe`, `/data/py-check`, `/data/g022-stoptest-*`, `/data/turbo-probe/alpha-turbo-1…16` **[и]** | Python-коллекторы остановлены и `disabled` (В-152); данные сохранены по handoff; диск 45 ГБ из 394 ГБ — не критично | удаление — **только по слову владельца** (приказ 3, сырые данные) | **да** (сырьё) | `ls /data`; `df -h /` = 12 % |
| Н-9 | VPS София: чужие/старые сервисы | `rpv-pulse.service` (rpv снят 12.11!), `screenshot.service` (TK-017), `tg-reports`, `massedit`, `watcher`, `watcher-staging` (Solta DSP), `docker`; failed: `ifup@eth0`, `networking.service`; диск 67 % (свободно 23 ГБ — на пороге «сторожа места» < 23 ГБ режет сборки) **[и]** | `rpv-pulse` — остаток снятого rpv; Solta DSP-сервисы к alpha отношения не имеют (не установлено кем стоят) | остановить `rpv-pulse` координатором; про остальные — решение владельца кому они принадлежат | **да** для Solta/docker (чужое, не наше) | `ssh 13.140.29.171 systemctl list-units`; `df -h /` |
| Н-10 | Master-checkout: неотслеживаемое | `git status --short` = 107 записей: `tools/compute/*` 71, `docs/research/*` 26 (reviews tk387…tk509), `docs/findings/` 7, `Python/` (3 подпапки: `_cache`, `bin`, `pythoncore-3.15-64`), `tk426-windows.csv` (432 КБ, 28.10), `docs/plan/p40-d1-plan-2026-11-10.md` **[и]** | Не закоммичено с 28.10–11.11; часть — отчёты Судьи rpv (потеряются при очистке) | коммит отчётов reviews/findings; `Python/`, `tk426-windows.csv`, `tools/compute` — решение координатора (код — в `archive/` или удалить) | нет (но удаление `Python/` — установка Python вне репо?) | `git status --short \| wc -l` = 107 |
| Н-11 | Старые ветки/worktree-ы вне G-022 | `tk493-xdl9` (197 коммитов вне master, worktree `.claude/worktrees/TK-493` с 6 грязными файлами), `tk508-onion`, `tk500-p40core` (182), `tk486-speed` (34), `tk530-day-upload` (WIP), `tk515…tk526` — **22 worktree-а**, **43 локальные ветки**, 139 `archive/*` **[и]** | Закрытые тикеты rpv без тега; `archive/tk479-statarb` есть, остальные — нет. Поскольку П-40 на паузе, 3 stash «tk359-seconds leftover», «audit/design-fixes» лежат с сентября-октября | массовый тег+удаление после ревью списка (Судья TK-508 «слой 3» уже составил список — `59ea4260`) | нет | `git worktree list \| wc -l` = 22; `git stash list` |
| Н-12 | Слияние в master без проверки | `ee5d5797` (compute-sprawl: 430 скриптов) и `18a9100b` (lint-budget: правки `src/statarb`) вошли в `master` через `17f3f6d3`, `04ea09c1`; проверка `50b4568b` (`master-merge-2026-11-12.md`) относится только к `6cfa8dbf` и сама пишет «гейт (test/clippy/fmt) здесь не гонялся … ПРИНЯТО действует при зелёном гейте» **[и]** | Отдельных вердиктов нет; зелёный гейт на srv в журнале не найден **[в]** | `bash tools/vps-check.sh <дерево> all` на `master`; записать результат | нет | `git grep ee5d5797 50b4568b -- docs/research/reviews` пуст; `git show 50b4568b:docs/research/reviews/master-merge-2026-11-12.md` |
| Н-13 | План G-022 устарел | `docs/plan/G-022-collectors-plan-2026-11-12.md` (состояние 12.11 03:45): §4/§7 — шаги TK-527…TK-533 на Python; §8 «открытые вопросы» — Huobi/BingX уже решены В-146; критерий «ноль событий за 3 мин» не пересмотрен | В `docs/archive/` не перенесён, handoff ссылается на §2 как действующий (приказ 16) **[и]** | пометить §4–§8 «заменено В-149/В-152, см. handoff» или вынести §2 в отдельный файл, остальное — в `docs/archive/` | нет | `G-022-collectors-plan-2026-11-12.md:42-54,92-113` |
| Н-14 | Открытый вопрос Binance/София | `docs/findings/binance-route-problem-2026-11-13.md`; handoff п. 1 | «вопрос владельцу про Софию открыт», владелец делает ресёрч (деньги/направление) | ждать слова владельца | **да** (направление) | handoff стр. 10, 17 |
| Н-15 | Нарушенные правила на практике | (а) ~~AGENTS.md: «cargo запрещён хуком» — хука нет~~ — **неверно** (поправка координатора): хук глобальный, `~/.claude/settings.json:34` → `~/.claude/hooks/alpha_one_build.py` (запрет cargo build/test/clippy/… на ПК); в проектном `.claude/settings.json` его и не должно быть; файл `.claude/hooks/heavy_ssh_guard.py` лежит, но не подключён **[и]**; (б) пункт `З-2` проверки 12.11 — по той же причине снят; (в) дубли номеров В-146…В-150 между `.claude/decisions.md` и `docs/plan/SETTLED.md` (З-4) — «грепом по номеру» даёт два ответа **[и, по отчёту проверки]**; (г) Python-коллекторы остановлены (`bnws-prod`, `bbws-prod` — `disabled`), одновременный запуск Python+Rust **не наблюдается** **[и]** | Для (а), (б), (в) — исправления нет | правка AGENTS.md (убрать «хуком» или подключить hook); переименовать нумерацию | нет | `.claude/settings.json:34-67`; `50b4568b…master-merge…md` З-2, З-4 |
| Н-16 | Thread-реестр bb | В `bb thread list --parent-thread thr_ty6ae9r46y`: 19 тредов `idle` + `thr_yidkdm8xur` в `error`; `thr_5bc3a4v4az` в архиве; в корневом списке ещё `thr_2es4s7puki`, `thr_2htg7t89tw` (error), не дочерние **[и]** | Закрытых/архивированных нет, кроме одного; idle не расходуют ресурсов, но создают шум и держат 3,8 ГБ worktree | архивировать после Н-1/Н-3 (после пуша и тега) | нет | `bb thread list --parent-thread thr_ty6ae9r46y` |

## Что сделано хорошо (не трогать)

- На srv работают только Rust-юниты `soak-binance`, `soak-bybit` и страницы здоровья (:8088/:8089); Python-коллекторы `disabled` — приказ В-152 исполнен **[и]**.
- Диск srv 12 % занято; сервера не перегружены (load 0,98) **[и]**.
- Права на сырьё не нарушены: ни одного удаления данных в аудите не найдено; `rmtree` в `day_upload.py` срабатывает только при нехватке места после сверки **[в]**.

## Отклонения от брифа

- «per-thread token usage» — измерено через `thread/tokenUsage/updated`; для 3 тредов данных нет (отмечено). Долларов не считал (приказ 4).
- Fast mode — не измерим, помечено.
- Исполнители/проверяющие в `docs/research/reviews` на ветках не пересчитаны построчно; число «кругов» взято по числу тредов и коммитов возврата.
- Не открывал `docs/plan/SETTLED.md` по номерам; дубли В-146…В-150 взяты из проверки `50b4568b`.
- Время аудита: ~25 мин из 45.

## Закрытие 13.11

Исполнено по приказу владельца: «убери все что не относится к аудиту и сделай пункты из аудита». Время — 13.11 ~01:10–02:00 GMT+4. Вне задачи (не делалось): Н-4 слияние, Н-5, Н-8 удаление данных, Н-12, Н-13, Н-14, Н-15, Н-16 (архивация тредов bb — координатор).

| Пункт | Статус | Доказательство |
|---|---|---|
| Н-3 | **сделано** | `git push origin okx-ws-prep g022-prep-3` → `[new branch]` обе; перед пушем grep диффа на `api_key/secret/token/пароль/приват.ключ/32+ символов` — совпадений нет, кроме образца SBE-кадра (hex, не секрет). Ветки сохранены (`okx-ws-prep` @ `a37997dd`, `g022-prep-3` @ `28bf91f6`). |
| Н-2 | **сделано** | `g022-turbo` local = origin = `a32ef34f` (`git merge --ff-only`). 3 грязных файла: `.claude/dispatcher/guard.log` + 2 файла `tk525-*-run.txt` (разница только в окончаниях строк) → копия в `data/audit-2026-11-13-saved/g022-turbo-worktree/` (+`dirty-tracked.diff`), `.claude/dispatcher/` удалён. Остаток: 2 tracked-файла `tk525-*-run.txt` в worktree показываются как `M` (смешанные CRLF/LF в блобе + `eol=lf` — git сам не снимает; содержимое = HEAD с точностью до EOL). Дубли `bb/g-022-rust-collector-v2-thr_{5bc3a4v4az,bjufupu9yg,bzj47264ji}` @ `a32ef34f` — `merge-base --is-ancestor … origin/g022-turbo` = да → удалены без тега, 2 их worktree убраны. `bb/g-022-rust-collector-v1-thr_pgx62pdrqp` @ `b3635a1d` — **не** содержится (там отчёт проверки v1) → тег `archive/bb-g-022-rust-collector-v1-thr_pgx62pdrqp`, ветка удалена локально и на origin. |
| Н-1 + Н-11 | **сделано** | Все локальные и удалённые ветки вне `master`/`g022-turbo`/`okx-ws-prep`/`g022-prep-3`, не слитые в `master`: 35 локальных + 32 удалённых (31 только на origin + `bb/g-022-rust-collector-v1-…` с обеих сторон). Теги: 55 новых на ветки + 3 на stash; ещё 8 веток уже имели `archive/*`, чей коммит содержит кончик ветки (6 из этих 8 тегов не было на origin — запушены). После: `git branch -r --no-merged master` = `g022-prep-3`, `g022-turbo`, `okx-ws-prep` + `bb/none-audit-merges-v1-thr_cs2q5hznx5` (создана другим тредом уже во время работы — не трогал). `git ls-remote --tags origin 'refs/tags/archive/*'` = 161 (было 97; +58 новых +6 существовавших локально). 16 worktree-ов убраны (`git worktree remove`, без `--force`; `git worktree list` = 7). `tk493-xdl9`: 6 неотслеженных файлов (4 × `tools/compute/statarb/xdl9/census/*.py`, `conv/c2_git_ok`, `f8_git_ok`) → коммит `7c2e86fa` «WIP: сохранено при архивации (аудит 13.11)»; секретов в них нет (grep). Игнорируемые данные worktree (`data/xdl9` 1,5 ГБ, `data/p40ref`, `data/pairs_run.log`) **не удалены** — перенесены в `data/audit-2026-11-13-saved/TK-493-data/`. Слитые в `master` ветки `bb/*` (5) и их worktree-ы не трогал (вне приказа). Освобождено на ПК: ≈ 3,5 ГБ (`du` убранных worktree-ов = 3525 МБ; `df C:` свободно 85 → 89 ГБ). Stash: 3 → 0, каждый — тег (таблица ниже). Секретов в ранее не запушенных коммитах нет: `git log --branches --not master --remotes=origin -G<regex>` пуст; blob > 20 МБ нет (59 МБ всего). |
| Н-6 | **сделано** | `systemctl --failed` было 15 юнитов → `reset-failed` → 0. `p40-ready.service` (transient, с 10.11 16:49 UTC) остановлен: `inactive`, процесса `p40-ready.py` нет. Живые не тронуты: `soak-binance soak-bybit health-page-8088 health-soak-page` = `active ×4` после операций. `tail -F /data/bbws-entry/watch.log` (сессия ssh `session-10424.scope`, не юнит) — **не трогал**, не в приказе. |
| Н-7 | **сделано** | `alpha-day-upload.timer`: `systemctl stop` + `disable` → `Removed … timers.target.wants/alpha-day-upload.timer`; `is-enabled` = `disabled`, `is-active` = `inactive`; сервис `alpha-day-upload.service` = `inactive`. `p40-reap-timer.timer` (transient) остановлен вместе с `.service` → `not-found`; `disable` не применим (`Unit … does not exist`). |
| О-12 | **сделано** | `p40-ready.py` остановлен, `p40-reap-timer` остановлен, юнит `p40-reap-timer.service` убран из failed (`reset-failed`). Цикл `until ! pgrep -f t522.py` в списке процессов не найден (уже завершён). |
| О-13 | **сделано** | `df -h /dev/shm /tmp` до: `/dev/shm 11G` (24 %), `/tmp 4,3G` (14 %); после: `/dev/shm 0` (0 %), `/tmp 54M` (1 %). Удалено 2 251 верхнеуровневых объекта (15 495 МБ): ни один не открыт процессом (скан `/proc/*/fd,cwd,exe,maps,cmdline`; `lsof` на srv нет), нет файлов моложе 2026-11-11 21:30 UTC, не в списке «оставить» (`systemd-private-*`, `soak*`, `health*`, `g022*`, `turbo*`, `pgo-master-prof`, `pgo-try-prof` и др.). Крупное: `/dev/shm/p40o` 4,2 ГБ, `/dev/shm/p40d1hl` 3,9 ГБ, `/tmp/pgo_l1` 3,9 ГБ (выходные CSV прогонов PGO-гейта L1 09.11), `/dev/shm/p40` 1,1 ГБ, `/dev/shm/p40g` 0,5 ГБ (копии скачанных датасетов — В-131/В-132), `g_py/g_rs/b_py/b_rs` ~1 ГБ, `p40by`. Оставлено: 21 по имени, 30 моложе порога. Страховка: 2 241 объект < 100 МБ упакован в `/root/audit-shm-tmp-2026-11-13.tar.zst` (232 МБ, на диске srv, `/data` не трогал); список — `/root/audit-shmclean/cand.txt`. `free -g`: shared 15 → 0. |
| Н-9 | **сделано** | София: `rpv-pulse.service` `active/enabled` → `stop` + `disable` → `Removed …multi-user.target.wants/rpv-pulse.service`; `is-active` = `inactive`, `is-enabled` = `disabled`. Solta/docker/watcher/massedit/tg-reports/screenshot не тронуты (В-154). |
| Н-10 | **сделано** | см. коммит master ниже. `Python/` (166 МБ) — переносной интерпретатор `pythoncore-3.15-64` + `pip.exe`; хуки (`~/.claude/settings.json:34`, `.claude/settings.json:41,51,62`) вызывают `python` из PATH, а не `Python/` → добавлено `/Python/` в `.gitignore`, ничего не удалено. `tk426-windows.csv` (432 КБ) → `data/tk426-windows.csv`. `tools/compute`: 71 неотслеженный скрипт → 61 перенесён в `tools/compute/archive/`, 7 побайтово совпали с уже отслеживаемыми копиями в `archive/` (дубли убраны), 3 отличались (в `archive/` более поздняя версия) → старые версии в `data/audit-2026-11-13-saved/tools-compute-older/` (`tk265-table.py`, `tk319-gate.sh`, `tk319-spot-roots.py`). Ссылок на эти скрипты в коммитимых `docs/**` нет (0 замен). Документы `docs/research/reviews/*` (25), `P-33-statarb-v4.md`, `docs/findings/*`, `p40-d1-plan` — как есть. |

### Ветки и теги (Н-1, Н-11, Н-2)

| Где | Ветка | SHA | Тег | Итог |
|---|---|---|---|---|
| локально | `bb/g-022-binance-bybit-thr_ki49k6g54p` | `f6e00fc7` | `archive/bb-g-022-binance-bybit-thr_ki49k6g54p` | тег создан и запушен; ветка удалена |
| локально | `bb/g-022-rust-collector-v1-thr_pgx62pdrqp` | `b3635a1d` | `archive/bb-g-022-rust-collector-v1-thr_pgx62pdrqp` | тег создан и запушен; ветка удалена |
| локально | `bb/g-022-rust-collector-v2-thr_5bc3a4v4az` | `a32ef34f` | — | содержится в `origin/g022-turbo` (`merge-base --is-ancestor`), тег не нужен; ветка и worktree удалены |
| локально | `bb/g-022-rust-collector-v2-thr_bjufupu9yg` | `a32ef34f` | — | содержится в `origin/g022-turbo` (`merge-base --is-ancestor`), тег не нужен; ветка и worktree удалены |
| локально | `bb/g-022-rust-collector-v2-thr_bzj47264ji` | `a32ef34f` | — | содержится в `origin/g022-turbo` (`merge-base --is-ancestor`), тег не нужен; ветка и worktree удалены |
| локально | `bb/g-022-thr_i42yzre75q` | `f6e00fc7` | `archive/bb-g-022-thr_i42yzre75q` | тег создан и запушен; ветка удалена |
| локально | `bb/master-thr_ssija84pzt` | `50b4568b` | `archive/bb-master-thr_ssija84pzt` | тег создан и запушен; ветка удалена |
| локально | `bb/okx-thr_mtp79ffj9f` | `f6e00fc7` | `archive/bb-okx-thr_mtp79ffj9f` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-531-binance-2-thr_upjqwagznd` | `f67900f0` | `archive/bb-tk-531-binance-2-thr_upjqwagznd` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-531-binance-thr_yidkdm8xur` | `f67900f0` | `archive/bb-tk-531-binance-thr_yidkdm8xur` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-531-thr_3748xfhbvs` | `f90baeeb` | `archive/bb-tk-531-thr_3748xfhbvs` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-531-thr_hn92g23fjy` | `a0bbf202` | `archive/bb-tk-531-thr_hn92g23fjy` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-532-533-bybit-thr_jw4tqpx26g` | `3b579781` | `archive/bb-tk-532-533-bybit-thr_jw4tqpx26g` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-532-533-thr_n2ufrnbd7n` | `2bb70977` | `archive/bb-tk-532-533-thr_n2ufrnbd7n` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-532-bybit-thr_tdywiip4wk` | `f6e00fc7` | `archive/bb-tk-532-bybit-thr_tdywiip4wk` | тег создан и запушен; ветка удалена |
| локально | `bb/tk-532-v2-thr_p87tqmpwed` | `abbe7dd9` | `archive/bb-tk-532-v2-thr_p87tqmpwed` | тег создан и запушен; ветка удалена |
| локально | `g022-integrate` | `3f8e1ce6` | `archive/g022-integrate` | тег создан и запушен; ветка удалена |
| локально | `tk479-statarb` | `bd97d079` | `archive/tk479-statarb-bd97d079` | тег создан и запушен; ветка удалена |
| локально | `tk486-speed` | `79ad96ff` | `archive/tk486-speed` | тег создан и запушен; ветка удалена |
| локально | `tk493-xdl9` | `7c2e86fa` | `archive/tk493-xdl9` | тег создан и запушен; ветка удалена |
| локально | `tk500-p40core` | `95f7436c` | `archive/tk500-p40core` | тег создан и запушен; ветка удалена |
| локально | `tk508-onion` | `59ea4260` | `archive/tk508-onion` | тег создан и запушен; ветка удалена |
| локально | `tk515-l1` | `05978863` | `archive/tk515-l1` | тег создан и запушен; ветка удалена |
| локально | `tk516-pq2bin` | `14048c9a` | `archive/tk516-pq2bin` | тег создан и запушен; ветка удалена |
| локально | `tk518-orch-l1` | `7bec4c02` | `archive/tk518-orch-l1` | тег создан и запушен; ветка удалена |
| локально | `tk522-orch-l1all` | `a4e8c8cb` | `archive/tk522-orch-l1all` | тег создан и запушен; ветка удалена |
| локально | `tk522-pq2bin-carry` | `6c8ede68` | `archive/tk522-pq2bin-carry` | тег создан и запушен; ветка удалена |
| локально | `tk525-l1-tail` | `37a5a162` | `archive/tk525-l1-tail` | тег создан и запушен; ветка удалена |
| локально | `tk526-theta-t` | `f28c8c22` | `archive/tk526-theta-t` | тег создан и запушен; ветка удалена |
| локально | `tk527-binance-ws` | `f67900f0` | `archive/tk527-binance-ws` | тег создан и запушен; ветка удалена |
| локально | `tk528-datasets-removed` | `f9a2bf82` | `archive/tk528-datasets-removed` | тег создан и запушен; ветка удалена |
| локально | `tk530-day-upload` | `90e53c1f` | `archive/tk530-day-upload` | тег создан и запушен; ветка удалена |
| локально | `tk531-binance-ladder` | `93cbb48f` | `archive/tk531-binance-ladder` | тег создан и запушен; ветка удалена |
| локально | `tk532-bybit-accept` | `5e51f765` | `archive/tk532-bybit-accept` | тег создан и запушен; ветка удалена |
| локально | `tk532-bybit-ws` | `3b579781` | `archive/tk532-bybit-ws` | тег создан и запушен; ветка удалена |
| origin | `dependabot/cargo/clap-4.6.7` | `3d9e73e3` | `archive/dependabot-cargo-clap-4.6.7` | тег создан и запушен; ветка удалена |
| origin | `dependabot/cargo/serde_json-1.0.154` | `84fcd378` | `archive/dependabot-cargo-serde_json-1.0.154` | тег создан и запушен; ветка удалена |
| origin | `dependabot/cargo/sha2-0.11.1` | `c53461ba` | `archive/dependabot-cargo-sha2-0.11.1` | тег создан и запушен; ветка удалена |
| origin | `dependabot/cargo/tokio-1.53.3` | `8b2598c0` | `archive/dependabot-cargo-tokio-1.53.3` | тег создан и запушен; ветка удалена |
| origin | `dependabot/cargo/zstd-0.14.3` | `373dbd2c` | `archive/dependabot-cargo-zstd-0.14.3` | тег создан и запушен; ветка удалена |
| origin | `dependabot/github_actions/actions/cache-6` | `90f09e89` | `archive/dependabot-github_actions-actions-cache-6` | тег создан и запушен; ветка удалена |
| origin | `dependabot/github_actions/actions/checkout-7` | `be0f4862` | `archive/dependabot-github_actions-actions-checkout-7` | тег создан и запушен; ветка удалена |
| origin | `dependabot/github_actions/actions/upload-artifact-6` | `1f27c4f2` | `archive/dependabot-github_actions-actions-upload-artifact-6` | тег создан и запушен; ветка удалена |
| origin | `feat/hypothesis-backtest-storagebox-report-15933323708583137623` | `48482984` | `archive/feat-hypothesis-backtest-storagebox-report-15933323708583137623` | тег создан и запушен; ветка удалена |
| origin | `tk201-a2` | `5fc73624` | `archive/tk201-a2` | тег создан и запушен; ветка удалена |
| origin | `tk207-gate-r2` | `5fc73624` | `archive/tk207-gate-r2` | тег создан и запушен; ветка удалена |
| origin | `tk210-rebased` | `c7cfabc9` | `archive/tk210-rebased` | тег создан и запушен; ветка удалена |
| origin | `tk211` | `c8ee131e` | `archive/tk211` | тег создан и запушен; ветка удалена |
| origin | `tk222` | `93758ded` | `archive/tk222` | тег создан и запушен; ветка удалена |
| origin | `tk222-r` | `9dbb8de8` | `archive/tk222-r` | тег создан и запушен; ветка удалена |
| origin | `tk223-leftover` | `209ab5d0` | `archive/tk223-leftover` | тег создан и запушен; ветка удалена |
| origin | `tk223b` | `6630517a` | `archive/tk223b` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |
| origin | `tk223c` | `66d60668` | `archive/tk223c` | тег создан и запушен; ветка удалена |
| origin | `tk223stand` | `e8e10fe7` | `archive/tk223stand` | тег создан и запушен; ветка удалена |
| origin | `tk257` | `eb80b20b` | `archive/tk257` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |
| origin | `tk258-deploy` | `59b150f9` | `archive/tk258-deploy` | тег создан и запушен; ветка удалена |
| origin | `tk261` | `d1263105` | `archive/tk261` | тег создан и запушен; ветка удалена |
| origin | `tk265` | `213bff33` | `archive/tk265` | тег создан и запушен; ветка удалена |
| origin | `tk284` | `c7adb63d` | `archive/tk284` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |
| origin | `tk294` | `146fbe61` | `archive/tk294` | тег создан и запушен; ветка удалена |
| origin | `tk296` | `6a759298` | `archive/tk296` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |
| origin | `tk306` | `d567ee2f` | `archive/tk306` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |
| origin | `tk312-held-net` | `a4558fce` | `archive/tk312-held-net` | тег создан и запушен; ветка удалена |
| origin | `tk316` | `67543dc6` | `archive/tk316` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |
| origin | `tk341b` | `f2094a3a` | `archive/tk341b` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |
| origin | `tk363-bank-d20` | `c4dca598` | `archive/tk363-bank-d20` | тег уже был и содержит кончик ветки (тег запушен); удалённая ветка удалена |

| Stash | SHA | Тег |
|---|---|---|
| `stash@{0}` | `1f8d43b2` | `archive/stash-0-tk359-seconds-leftover` |
| `stash@{1}` | `1779d88e` | `archive/stash-1-audit-design-fixes-wip-0510` |
| `stash@{2}` | `91aae80d` | `archive/stash-2-tk057-dirty-src-maintree-0510` |
