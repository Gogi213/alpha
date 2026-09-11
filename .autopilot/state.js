window.STATE =
{
  "slug": "lob-density-ed3",
  "dir": "2026-09-11-lob-density-ed3--wip",
  "title": "Бот по плотностям стакана. Фаза 1 — вердикт по профилям",
  "mode": "semi",
  "depth": "deep",
  "polish": null,
  "tier": "T3",
  "briefFile": "2026-09-11-brief.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "~/.claude/skills/autopilot",
  "startedAt": "2026-09-11T00:20:00+04:00",
  "updatedAt": "2026-09-11T11:30:00+04:00",
  "finishedAt": null,
  "note": "Вторая редакция плана за день. Владелец поправил три вещи: архитектура сразу под бота; система быстрая с первого дня; тестовые прогоны не дольше 5 минут. Плюс разведка на трёх инструментах пула: данные не совпали с ожиданием по определению «крупного» уровня — H3 переопределён как пол. Прогон 2026-09-08 закрыт, его состояние в archive/.",

  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-09-11T00:20:00+04:00", "finishedAt": "2026-09-11T00:32:00+04:00", "note": "каталог прогона, бриф дословно" },
    { "id": "manifest",  "status": "done", "startedAt": "2026-09-11T00:32:00+04:00", "finishedAt": "2026-09-11T02:00:00+04:00", "note": "82 требования и 3 добавления; R78–R82 — дополнения владельца 2026-09-11" },
    { "id": "briefing",  "status": "done", "startedAt": "2026-09-11T00:45:00+04:00", "finishedAt": "2026-09-11T01:55:00+04:00", "note": "три ответа владельца: продукт — бот; 5 минут — тесты; форма — autopilot. Две развилки: CLUSDT, G_min" },
    { "id": "spec",      "status": "done", "startedAt": "2026-09-11T01:00:00+04:00", "finishedAt": "2026-09-11T02:03:00+04:00", "note": "вторая редакция: 49 историй, шесть швов; 17 находок G2 внесены; повторный G2 запущен" },
    { "id": "plan",      "status": "done", "startedAt": "2026-09-11T01:10:00+04:00", "finishedAt": "2026-09-11T02:05:00+04:00", "note": "15 тасков в 7 волн; G3 в обе стороны" },
    { "id": "build",     "status": "active", "startedAt": "2026-09-11T02:20:00+04:00", "note": "T08 закоммичен 126dd4d; в полёте T15 и T09(а: --debug по session→verify→levels→markout); T09(б: profiles→backtest, G-DEBUG, боевые прогоны) — после T10/T11 и k от владельца. 8 из 15 готово" },
    { "id": "review",    "status": "active", "startedAt": "2026-09-11T03:33:00+04:00", "note": "три постоянных ревьюера (R-A/R-B/R-C) с волны 2; 4 таска прошли; 1 ремонт (T06), 12 concerns накоплено" },
    { "id": "final",     "status": "pending" }
  ],

  "requirements": {
    "total": 85, "done": 38, "inTicket": 43, "deferred": 4, "dropped": 0, "placeholder": 0, "open": 0,
    "note": "done узкое: механизм есть, тесты зелёные, служит редакции 3 без изменений. Ни одного артефакта задачи на диске нет."
  },

  "tickets": [
    { "id": "01", "title": "Вычистить отменённое и разрезать commands/lob.rs", "requirements": ["R18","R19","R32","R37","R43","R49","R71","R73"], "blockedBy": [], "wave": 1, "zone": ["src/","docs/plan/"], "status": "done", "startedAt": "2026-09-11T02:20:00+04:00", "finishedAt": "2026-09-11T04:20:00+04:00", "tests": "448 passed, 0 failed, 5 ignored", "commit": "3cc2e62", "retries": 0, "repairs": 1, "handoffs": 0, "review": { "R-A": "1 blocking: pick.rs 2666 строк → закрыто ремонтом", "R-B": "нет; docstring H10 → закрыто", "R-C": "3 blocking: pick.rs → закрыто; D-H3 против §9 → D02/В-29; G0 продление → источник ревизия 17б", "repair": "ложная тревога по watch.rs::day_eligible — снятие has_gap_over_6h из годности есть критерий таска 01, сделано первым проходом, не ремонтом" } },
    { "id": "02", "title": "H3 в двух режимах; размер как ось", "requirements": ["R14","R40"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/levels.rs"], "status": "done", "startedAt": "2026-09-11T04:10:00+04:00", "finishedAt": "2026-09-11T06:20:00+04:00", "tests": "454 passed, 0 failed, 5 ignored (HEAD+02 изолированно)", "commit": "d6b742d", "review": { "R-A": "нет; R14/R40 partial по дизайну (колонка h3_lots — T08, замер — T09)", "R-B": "нет; 2 структурных → concerns", "R-C": "clean" }, "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "03", "title": "net_fill: формула и совместный интервал", "requirements": ["R02","R05","R06","R07","R08","R50"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/costs.rs","src/stats/mod.rs","src/lob/cells.rs"], "status": "done", "startedAt": "2026-09-11T04:10:00+04:00", "finishedAt": "2026-09-11T06:50:00+04:00", "tests": "462 passed, 0 failed, 5 ignored (d6b742d+03 изолированно)", "commit": "dc5bb5f", "review": { "R-A": "нет; R02/R05/R06/R08/R50 partial по плану — проводка колонки T06/T10, вызывающий T11, поправка T13", "R-B": "нет; докстрока costs.rs:32 и размер файла → concerns", "R-C": "clean" }, "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "04", "title": "lob session: сессия по пулу — и это Feed бота", "requirements": ["R12","R37","R38","R41","R43","R75i","R78","R79","R80"], "blockedBy": ["01"], "wave": 2, "zone": ["src/commands/record.rs","src/commands/lob/session.rs","src/feed/"], "status": "done", "startedAt": "2026-09-11T04:10:00+04:00", "finishedAt": "2026-09-11T08:45:00+04:00", "tests": "472 passed, 0 failed, 5 ignored (5c61bf2+04 изолированно)", "commit": "8e33149", "review": { "R-A": "1 blocking: D## под tokio::mpsc вместо crossbeam → D04; R38/R79/R80 partial по разделению с T09/T15", "R-B": "нет; grep-тест горячего пути для feed/, дубль trade_hit_from_record, размеры файлов → concerns", "R-C": "clean; артефакты прогона сверены с диском" }, "retries": 0, "repairs": 0, "handoffs": 2 },
    { "id": "05", "title": "lob power: планка до сбора (G-POWER-A)", "requirements": ["R47","R59","R68","A01"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/final_metrics.rs","src/commands/lob/power.rs"], "status": "done", "startedAt": "2026-09-11T08:05:00+04:00", "finishedAt": "2026-09-11T09:50:00+04:00", "tests": "480 passed, 0 failed, 5 ignored (8e33149+05 изолированно)", "commit": "3147a32", "review": { "R-A": "нет; R47/R68/A01 partial по дизайну (T09/T13)", "R-B": "нет; 4-й читатель instruments.csv → concerns", "R-C": "clean; прогон воспроизведён независимо" }, "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "06", "title": "Сетка семи осей, час суток как ось", "requirements": ["R01","R16","R17","R20","R23","R24","R41","R46","R61"], "blockedBy": ["01"], "wave": 2, "zone": ["src/lob/shortlist.rs"], "status": "done", "startedAt": "2026-09-11T05:32:00+04:00", "finishedAt": "2026-09-11T07:40:00+04:00", "tests": "469 passed, 0 failed, 5 ignored (dc5bb5f+06 изолированно)", "commit": "5c61bf2", "review": { "R-A": "1 blocking: D03 отсутствовал → записан; после поворота R-C — дифф соответствует спеке, R23 done", "R-B": "нет; shortlist.rs 1673 строки → concerns", "R-C": "2 blocking: полный крест против В-18 → откат; repeat_bucket сдвиг → починен. Снято" }, "retries": 0, "repairs": 1, "handoffs": 0 },
    { "id": "08", "title": "lob pick начисто: пул заморожен, пол H3", "requirements": ["R14","R21","R22","R23","R24","R25","R26","R27","R28","R29","R30","R31","R33","R34","R35","R36","R69"], "blockedBy": ["01"], "wave": 2, "zone": ["src/commands/lob/pick.rs","docs/plan/candidates.csv","instruments.csv"], "status": "done", "startedAt": "2026-09-11T08:50:00+04:00", "finishedAt": "2026-09-11T11:20:00+04:00", "tests": "500 passed, 0 failed, 5 ignored (3147a32+08 изолированно; флаки conn.rs)", "commit": "126dd4d", "review": { "R-A": "1 blocking: instruments.csv = все кандидаты → только пул + единый ридер; R69/R14 partial (боевое окно и k — T09)", "R-B": "нет; пять читателей instruments.csv → сведены ремонтом; pool/table > 690 строк → concerns", "R-C": "clean; k как обязательный флаг — верно" }, "retries": 0, "repairs": 1, "handoffs": 0 },
    { "id": "07", "title": "lob watch на сессиях", "requirements": ["R39","R42","R49","R77i"], "blockedBy": ["01","04"], "wave": 3, "zone": ["src/lob/watch.rs"], "status": "done", "startedAt": "2026-09-11T08:50:00+04:00", "finishedAt": "2026-09-11T11:10:00+04:00", "tests": "488 passed, 0 failed, 5 ignored (3147a32+07 изолированно)", "commit": "4073927", "review": { "R-A": "1 blocking: охрана --confirmatory на старом флаге → проводка require_session_ready_flag; R49 done", "R-B": "нет; дубль trade_hit_from_record, n_c2/g_c2, 1801 строк → concerns", "R-C": "clean" }, "retries": 0, "repairs": 1, "handoffs": 0 },
    { "id": "09", "title": "Пилот: отладка ≤5 мин, затем два часа; G-DEBUG, G0, G-POWER-B", "requirements": ["R40","R51","R68","R78","R81","A01"], "blockedBy": ["01","02","08"], "wave": 3, "zone": ["src/commands/lob/pilot.rs"], "status": "in-progress", "startedAt": "2026-09-11T11:30:00+04:00", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "15", "title": "Горячий путь: on_event под Bot<MD>, ордер до триггера, G-LAT, dry-run", "requirements": ["R09","R74i","R78","R79","R80","A04"], "blockedBy": ["01","04"], "wave": 3, "zone": ["src/lob/strategy.rs","src/bybit/trade_ws.rs","src/commands/lob/react.rs"], "status": "in-progress", "startedAt": "2026-09-11T09:15:00+04:00", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "10", "title": "lob profiles: таблица профилей", "requirements": ["R01","R15","R55","R57","R58","R59","R65","R66"], "blockedBy": ["03","06","15"], "wave": 4, "zone": ["src/commands/lob/profiles.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "11", "title": "lob backtest: отчёт на N профилей, та же on_event", "requirements": ["R03","R09","R10","R11","R59","R80"], "blockedBy": ["03","15"], "wave": 4, "zone": ["src/lob/backtest.rs","src/commands/lob/backtest.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "12", "title": "Сбор сессиями, шорт-лист, подтверждение", "requirements": ["R44","R45","R46","R47","R48","R56","R59","R67","R77i"], "blockedBy": ["07","10"], "wave": 5, "zone": ["docs/findings/","src/commands/lob/shortlist.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "13", "title": "Вердикт в шапке шорт-листа, DSR подключён", "requirements": ["R47","R49","R50","R51","R52","R53","R54","R59","R68","A03"], "blockedBy": ["11","12"], "wave": 6, "zone": ["src/commands/lob/shortlist.rs","src/lob/final_metrics.rs"], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 },
    { "id": "14", "title": "Чистка репозитория и память проекта", "requirements": ["R65","R70","R71","R73"], "blockedBy": ["13"], "wave": 7, "zone": ["data/",".autopilot/","."], "status": "pending", "retries": 0, "repairs": 0, "handoffs": 0 }
  ],

  "gates": [
    { "id": "РВ-0",      "status": "passed",  "note": "пять независимых ревью — docs/plan/REVIEW-2026-09-11.md" },
    { "id": "РВ-Р",      "status": "passed",  "note": "разведка: 3 инструмента пула × 5 мин — docs/plan/RECON-2026-09-11.md. Сверка 6/6 ok; levels=0 при прогреве 60 мин; p99 даёт 1 уровень/мин и 0 eaten" },
    { "id": "G2",        "status": "passed",  "note": "дважды: 17 + 5 находок, все внесены. D01 снят по уточнению владельца: ≤5 мин — фаза отладки, окно repeat_count — скользящий час по §3" },
    { "id": "G-DEBUG",   "status": "pending", "note": "сквозной отладочный прогон ≤5 мин без дефекта — граница между отладкой и двухчасовым пилотом §11" },
    { "id": "G3",        "status": "passed",  "note": "15 тасков; трассировка в обе стороны" },
    { "id": "GC",        "status": "passing", "note": "таск 01: clippy 0, fmt 0, 448 тестов; аллокации и реакционный путь — с таска 04/15" },
    { "id": "G-LAT",     "status": "pending", "note": "реакционный путь p99 < 5 мс на живом потоке — таск 15" },
    { "id": "G-POWER-A", "status": "passed", "note": "lob power на пуле из десяти: N=179 (номинал), SR0=2.73, требуемый Шарп 3.13 при DSR 0.95, n=100 — таск 05 3147a32; N по пригодным парам — T09" },
    { "id": "G-POWER-B", "status": "pending", "note": "замеренный Шарп против требуемого — таск 09. Разведка: −0.40 / −0.32 / +0.24 против ≈2.7" },
    { "id": "G0",        "status": "pending", "note": "таск 09" },
    { "id": "G1",        "status": "pending", "note": "eaten и pulled ≥ 5%. Разведка: eaten 2.3% / 7.1% / 1.5%" },
    { "id": "G2-в",      "status": "pending", "note": "шорт-лист заморожен — таск 12" },
    { "id": "G3-в",      "status": "pending", "note": "net_fill с поправкой DSR — таск 13" },
    { "id": "G4-в",      "status": "pending", "note": "PnL на медианном и p95 RTT — таск 11" },
    { "id": "РВ-М",      "status": "pending", "note": "методическое перед вердиктом" }
  ],

  "reviewers": {
    "R-A": { "axes": ["manifest","spec"], "handle": "agent acde3c11b529e4135, spawned 2026-09-11T04:12 волна 2", "lifetime": "до границы волны" },
    "R-B": { "axes": ["craft"],           "handle": "agent a6fce5029a2c0557f, spawned 2026-09-11T04:12 волна 2", "lifetime": "весь прогон" },
    "R-C": { "axes": ["data","prereg"],   "handle": "agent a16f59270e1d9f178, spawned 2026-09-11T04:13 волна 2", "lifetime": "весь прогон, освежать нельзя" }
  },

  "coverage": { "findings": 22, "acted": 22, "note": "G2 дважды: 17 находок на первую редакцию спеки, 5 на вторую (окно repeat_count → D01; семантика теста 3; две кривые PnL; число 30 суток; родитель A01 → R81). Все внесены" },

  "concerns": [
    { "ticket": "01", "file": "src/commands/lob/pick.rs", "what": "2665 строк при потолке 900 — отбор пула, его тесты и сетевая оболочка в одном файле; разрез отложен в таск 08, который владеет pick.rs", "kind": "structural" },
    { "ticket": "01", "file": "—", "what": "исполнитель сделал 281 вызов инструментов за 70 минут без handoff при потолке ~50 — нарезка таска 01 слишком крупная: вычистка и разрез стоило резать на два таска", "kind": "plan-cut" },
    { "ticket": "02", "file": "src/commands/lob/levels.rs:56-113", "what": "resolve_h3_mode/h3_lots_for_symbol общие для levels/markout/pilot/watch, лежат в файле одной подкоманды; по конвенции таска 01 общий код — в mod.rs; перенести при следующем касании (таск 04 держит mod.rs)", "kind": "structural" },
    { "ticket": "02", "file": "src/commands/lob/{markout,pilot,watch}.rs", "what": "поля h3_mode/h3_lots с одинаковыми doc-комментариями продублированы в трёх Args — просится общая H3Args с #[command(flatten)]", "kind": "structural" },
    { "ticket": "02", "file": "src/commands/lob/levels.rs", "what": "--h3-lots вместе с --h3-mode floor молча игнорируется, а не отвергается (R-C, вне осей)", "kind": "craft" },
    { "ticket": "03", "file": "src/lob/costs.rs:32", "what": "докстрока модуля «не выделяет память» стала неверна — net_fill_interval аллоцирует; модуль вне горячего пути, но комментарий вводит в заблуждение", "kind": "craft" },
    { "ticket": "03", "file": ".autopilot/…/tickets/03-net-fill.md", "what": "ссылка таска «истории 19–22» — R06/R07/R08 по манифесту относятся к историям 23–25; дефект нарезки, не исполнителя", "kind": "plan-cut" },
    { "ticket": "02", "file": "manifest.md R40", "what": "таск 02 включил R40 без пометки «частично: только флаг режима»; содержание R40 (уровни/мин, число сессий) — T09; строку таска стоило писать явно", "kind": "plan-cut" },
    { "ticket": "06", "file": ".autopilot/…/tickets/06-profile-grid.md", "what": "критерий «строится крест семи осей» противоречил SETTLED В-18 (полный крест отвергнут); исполнитель построил 8129 ячеек, оркестратор записал D03 в пользу креста и откатил после R-C — ремонт по правильному условию", "kind": "plan-cut" },
    { "ticket": "03", "file": "src/lob/costs.rs", "what": "935 строк, из них ~400 тесты — растёт к потолку, кандидат на разбор при следующем касании (R-B)", "kind": "structural" },
    { "ticket": "06", "file": "src/lob/shortlist.rs", "what": "1673 строки после диффа при мягком потолке 900 — выделить блок «тест на час» в отдельный файл (R-B)", "kind": "structural" },
    { "ticket": "06", "file": "src/stats/mod.rs:49", "what": "докстрока GATE_ALPHA привязана к отменённой поправке C1/C2; переиспользована тестом на час законно, формулировку поправить (R-C, R-A)", "kind": "craft" },
    { "ticket": "04", "file": "src/commands/lob/session.rs", "what": "--minutes не валидируется в 5..15 рантаймом (только докстрока) — R38 partial; проверка диапазона — таску 09, который гоняет сессии", "kind": "craft" },
    { "ticket": "04", "file": "src/commands/lob/session.rs", "what": "session.json/stdout без явного маркера debug для прогона ≤5 мин (R-C); downstream ловит короткое окно сам", "kind": "craft" },
    { "ticket": "04", "file": "docs/plan/PLAN.md 3.1", "what": "первый замер parse_p99 = 251.8 мкс > калибровочного суббюджета 200 мкс; суббюджет пересматривается по замеру — решение при G-LAT (таск 15)", "kind": "data" },
    { "ticket": "04", "file": "src/bybit/verify.rs FileReplayer", "what": "аллоцирует через mem::take на кадр — вне зоны 04; alloc-тест ReplayFeed бьёт только write_market_event", "kind": "structural" },
    { "ticket": "04", "file": "src/feed/{live,replay}.rs", "what": "нет grep-теста горячего пути по образцу levels.rs (Instant/SystemTime/f64/HashMap) — нарушений нет по чтению, не по прогону (R-B); добавить в таске 15", "kind": "craft" },
    { "ticket": "04", "file": "src/feed/replay.rs:6-13", "what": "реконструкция сделки из бит Record.ev дублирует приватную commands::lob::trade_hit_from_record (R-B)", "kind": "structural" },
    { "ticket": "08", "file": ".autopilot/…/tickets/08-pick-clean.md", "what": "окно 3600 с противоречит фазе отладки (≤5 мин до G-DEBUG); нарезано: код + отладочный прогон ≤5 мин в T08, боевое окно 3600 с и заморозка пула — шаг T09 после G-DEBUG", "kind": "plan-cut" },
    { "ticket": "05", "file": "src/commands/lob/{power,session,levels}.rs, pick/table.rs, record.rs", "what": "четвёртый независимый читатель instruments.csv; таск 08 добавит coverage_bps и заденет все — общий ридер (R-B)", "kind": "structural" },
    { "ticket": "05", "file": "src/commands/lob/power.rs:126", "what": "докстринг теста ссылается на устаревшее имя теста shortlist (R-B)", "kind": "craft" },
    { "ticket": "07", "file": "src/commands/lob/watch.rs:213", "what": "trade_hit_from_record — копия приватной commands::lob::mod::trade_hit_from_record; is_trade_ev живёт в трёх файлах — сделать pub(crate) и переиспользовать (R-B)", "kind": "structural" },
    { "ticket": "07", "file": "src/commands/lob/watch.rs WatchSummary", "what": "поля n_c2/g_c2 больше не значат C2 — переименовать при сносе C1/C2 в таске 14 (R-B, R-C)", "kind": "craft" },
    { "ticket": "07", "file": "src/lob/watch.rs", "what": "1801 строка — растёт из волны в волну; снос C1/C2 в таске 14 (R-B)", "kind": "structural" },
    { "ticket": "08", "file": "src/commands/lob/session.rs load_pool, power.rs pool_size", "what": "не терпят строку `# debug` в instruments.csv (падают на новом файле) и не фильтруют пул до десяти; вместе с levels/record/table — пять читателей одного файла. Общий ридер + фильтр пула — первый шаг таска 09 (R-B, R-C)", "kind": "craft" },
    { "ticket": "08", "file": "src/commands/lob/pick/{pool,table}.rs", "what": "761/720 строк против названного потолка 690 из таска 01 (R-B)", "kind": "structural" },
    { "ticket": "04", "file": "src/bybit/conn.rs:1320 connect_then_close_without_forwarding_a_message_does_not_reset_backoff", "what": "флаки под нагрузкой: 1 падение на прогоне 3147a32+07 при параллельных сборках, 3/3 зелёных при повторе — тест зависит от таймингов; починить в таске 14", "kind": "flaky-test" },
    { "ticket": "09", "file": ".autopilot/…/tickets/09-pilot-runs.md", "what": "G-DEBUG требует цепочку до profiles→backtest (T10/T11), а T10/T11 были blockedBy 09 — цикл; перерезано: T09(а) сейчас — --debug по существующей цепочке; T10/T11 без зависимости от 09; T09(б) — расширение цепочки, G-DEBUG, боевые окна после T11 и k владельца", "kind": "plan-cut" }
  ],

  "debt": [
    { "what": "k для пола H3 (h3_lots = k × медиана размера сделки): числа нет ни в задаче, ни в спеке, ни в плане", "default": "нет умолчания; отладка идёт с k = 1.0 как заглушкой", "owner": "владелец", "ticket": "09" },
    { "what": "crossbeam vs tokio::mpsc для канала ввод-вывод → решения (D04)", "default": "tokio::mpsc + blocking_recv, замер задержки канала в G-LAT", "owner": "владелец", "ticket": "15" },
    { "what": "CLUSDT: baseCoin CL, признака в API нет; CL — тикер нефти WTI", "default": "исключить до подтверждения", "owner": "владелец", "ticket": "08" },
    { "what": "G_min: задача 7, код 12", "default": "7, с печатью df-цены и джекнайфа", "owner": "владелец", "ticket": "01" }
  ],

  "additions": [
    { "id": "A01", "what": "планка после дефляции считается до сбора; красный — сбор не начинается", "parent": "R47 + R68" },
    { "id": "A03", "what": "джекнайф-по-суткам рядом с вердиктом", "parent": "R68 (в брифе не просилось)" },
    { "id": "A04", "what": "бюджет реакции по этапам, не только весь путь", "parent": "R79" }
  ],

  "recon": {
    "at": "2026-09-10T21:30:00Z", "instruments": ["SOLUSDT","NEARUSDT","ZECUSDT"], "minutes": 5,
    "verify_ok": "6/6", "levels_with_default_warmup": 0,
    "levels_per_min_floor1": { "SOLUSDT": 106, "NEARUSDT": 136, "ZECUSDT": 2925 },
    "eaten_share": { "SOLUSDT": 0.023, "NEARUSDT": 0.071, "ZECUSDT": 0.015 },
    "levels_at_p99_per_5min": { "SOLUSDT": 5, "NEARUSDT": 6, "ZECUSDT": 145 }, "eaten_at_p99": 0,
    "m_10s_bps_at_p99": { "SOLUSDT": -0.60, "NEARUSDT": -2.65, "ZECUSDT": 1.32 },
    "sharpe_m_10s": { "SOLUSDT": -0.40, "NEARUSDT": -0.32, "ZECUSDT": 0.24 }, "required_sharpe": 2.7
  },

  "blind": null,
  "tests": { "passed": 500, "failed": 0, "ignored": 5, "at": "2026-09-11T11:20:00+04:00" }
}
