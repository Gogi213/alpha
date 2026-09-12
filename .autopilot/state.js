window.STATE =
{
  "slug": "lob-density-ed3",
  "dir": "2026-09-11-lob-density-ed3",
  "title": "Бот по плотностям стакана. Фаза 1 — вердикт по профилям",
  "mode": "semi",
  "depth": "deep",
  "polish": null,
  "tier": "T3",
  "briefFile": "2026-09-11-brief.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "~/.claude/skills/autopilot",
  "startedAt": "2026-09-11T00:20:00+04:00",
  "updatedAt": "2026-09-13T05:05:00+04:00",
  "finishedAt": null,
  "note": "Вторая редакция плана за день. Владелец поправил три вещи: архитектура сразу под бота; система быстрая с первого дня; тестовые прогоны не дольше 5 минут. Плюс разведка на трёх инструментах пула: данные не совпали с ожиданием по определению «крупного» уровня — H3 переопределён как пол. Прогон 2026-09-08 закрыт, его состояние в archive/.",
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-09-11T00:20:00+04:00",
      "finishedAt": "2026-09-11T00:32:00+04:00",
      "note": "каталог прогона, бриф дословно"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-09-11T00:32:00+04:00",
      "finishedAt": "2026-09-11T02:00:00+04:00",
      "note": "82 требования и 3 добавления; R78–R82 — дополнения владельца 2026-09-11"
    },
    {
      "id": "briefing",
      "status": "done",
      "startedAt": "2026-09-11T00:45:00+04:00",
      "finishedAt": "2026-09-11T01:55:00+04:00",
      "note": "три ответа владельца: продукт — бот; 5 минут — тесты; форма — autopilot. Две развилки: CLUSDT, G_min"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-09-11T01:00:00+04:00",
      "finishedAt": "2026-09-11T02:03:00+04:00",
      "note": "вторая редакция: 49 историй, шесть швов; 17 находок G2 внесены; повторный G2 запущен"
    },
    {
      "id": "plan",
      "status": "done",
      "startedAt": "2026-09-11T01:10:00+04:00",
      "finishedAt": "2026-09-11T02:05:00+04:00",
      "note": "15 тасков в 7 волн; G3 в обе стороны"
    },
    {
      "id": "build",
      "status": "done",
      "startedAt": "2026-09-11T02:20:00+04:00",
      "note": "Волна 9: T22 e333f31, боевой пилот 30 мин 16f91ce — красный про рынок; T23 (сутки на уровне части) ждёт; дальше — решение владельца",
      "finishedAt": "2026-09-12T03:30:00+04:00"
    },
    {
      "id": "review",
      "status": "done",
      "startedAt": "2026-09-11T03:33:00+04:00",
      "note": "три оси на каждом из 18 тасков (R-A/R-B/R-C, преемники после перезапуска на 17/18); 11 ремонтов по BLOCKING, все закрыты; 60 concerns → триаж §1a: таск 17 (fix now) + отчёт",
      "finishedAt": "2026-09-12T03:30:00+04:00"
    },
    {
      "id": "final",
      "status": "active",
      "startedAt": "2026-09-12T03:30:00+04:00",
      "note": "сдан 2026-09-12; волны 8–9 после сдачи: T20–T23. Волна 10 (владелец 2026-09-12): T24 супероптимизация коллектора → T25 олвейс-он коллектор → запуск и анализ по накопленному"
    }
  ],
  "requirements": {
    "total": 88,
    "done": 73,
    "inTicket": 10,
    "deferred": 4,
    "dropped": 1,
    "placeholder": 0,
    "open": 0,
    "note": "done узкое: механизм есть, тесты зелёные, служит редакции 3 без изменений. Ни одного артефакта задачи на диске нет."
  },
  "tickets": [
    {
      "id": "01",
      "title": "Вычистить отменённое и разрезать commands/lob.rs",
      "requirements": [
        "R18",
        "R19",
        "R32",
        "R37",
        "R43",
        "R49",
        "R71",
        "R73"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "src/",
        "docs/plan/"
      ],
      "status": "done",
      "startedAt": "2026-09-11T02:20:00+04:00",
      "finishedAt": "2026-09-11T04:20:00+04:00",
      "tests": "448 passed, 0 failed, 5 ignored",
      "commit": "3cc2e62",
      "retries": 0,
      "repairs": 1,
      "handoffs": 0,
      "review": {
        "R-A": "1 blocking: pick.rs 2666 строк → закрыто ремонтом",
        "R-B": "нет; docstring H10 → закрыто",
        "R-C": "3 blocking: pick.rs → закрыто; D-H3 против §9 → D02/В-29; G0 продление → источник ревизия 17б",
        "repair": "ложная тревога по watch.rs::day_eligible — снятие has_gap_over_6h из годности есть критерий таска 01, сделано первым проходом, не ремонтом"
      }
    },
    {
      "id": "02",
      "title": "H3 в двух режимах; размер как ось",
      "requirements": [
        "R14",
        "R40"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/lob/levels.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T04:10:00+04:00",
      "finishedAt": "2026-09-11T06:20:00+04:00",
      "tests": "454 passed, 0 failed, 5 ignored (HEAD+02 изолированно)",
      "commit": "d6b742d",
      "review": {
        "R-A": "нет; R14/R40 partial по дизайну (колонка h3_lots — T08, замер — T09)",
        "R-B": "нет; 2 структурных → concerns",
        "R-C": "clean"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "03",
      "title": "net_fill: формула и совместный интервал",
      "requirements": [
        "R02",
        "R05",
        "R06",
        "R07",
        "R08",
        "R50"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/lob/costs.rs",
        "src/stats/mod.rs",
        "src/lob/cells.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T04:10:00+04:00",
      "finishedAt": "2026-09-11T06:50:00+04:00",
      "tests": "462 passed, 0 failed, 5 ignored (d6b742d+03 изолированно)",
      "commit": "dc5bb5f",
      "review": {
        "R-A": "нет; R02/R05/R06/R08/R50 partial по плану — проводка колонки T06/T10, вызывающий T11, поправка T13",
        "R-B": "нет; докстрока costs.rs:32 и размер файла → concerns",
        "R-C": "clean"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "04",
      "title": "lob session: сессия по пулу — и это Feed бота",
      "requirements": [
        "R12",
        "R37",
        "R38",
        "R41",
        "R43",
        "R75i",
        "R78",
        "R79",
        "R80"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/commands/record.rs",
        "src/commands/lob/session.rs",
        "src/feed/"
      ],
      "status": "done",
      "startedAt": "2026-09-11T04:10:00+04:00",
      "finishedAt": "2026-09-11T08:45:00+04:00",
      "tests": "472 passed, 0 failed, 5 ignored (5c61bf2+04 изолированно)",
      "commit": "8e33149",
      "review": {
        "R-A": "1 blocking: D## под tokio::mpsc вместо crossbeam → D04; R38/R79/R80 partial по разделению с T09/T15",
        "R-B": "нет; grep-тест горячего пути для feed/, дубль trade_hit_from_record, размеры файлов → concerns",
        "R-C": "clean; артефакты прогона сверены с диском"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 2
    },
    {
      "id": "05",
      "title": "lob power: планка до сбора (G-POWER-A)",
      "requirements": [
        "R47",
        "R59",
        "R68",
        "A01"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/lob/final_metrics.rs",
        "src/commands/lob/power.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T08:05:00+04:00",
      "finishedAt": "2026-09-11T09:50:00+04:00",
      "tests": "480 passed, 0 failed, 5 ignored (8e33149+05 изолированно)",
      "commit": "3147a32",
      "review": {
        "R-A": "нет; R47/R68/A01 partial по дизайну (T09/T13)",
        "R-B": "нет; 4-й читатель instruments.csv → concerns",
        "R-C": "clean; прогон воспроизведён независимо"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "06",
      "title": "Сетка семи осей, час суток как ось",
      "requirements": [
        "R01",
        "R16",
        "R17",
        "R20",
        "R23",
        "R24",
        "R41",
        "R46",
        "R61"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/lob/shortlist.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T05:32:00+04:00",
      "finishedAt": "2026-09-11T07:40:00+04:00",
      "tests": "469 passed, 0 failed, 5 ignored (dc5bb5f+06 изолированно)",
      "commit": "5c61bf2",
      "review": {
        "R-A": "1 blocking: D03 отсутствовал → записан; после поворота R-C — дифф соответствует спеке, R23 done",
        "R-B": "нет; shortlist.rs 1673 строки → concerns",
        "R-C": "2 blocking: полный крест против В-18 → откат; repeat_bucket сдвиг → починен. Снято"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0
    },
    {
      "id": "08",
      "title": "lob pick начисто: пул заморожен, пол H3",
      "requirements": [
        "R14",
        "R21",
        "R22",
        "R23",
        "R24",
        "R25",
        "R26",
        "R27",
        "R28",
        "R29",
        "R30",
        "R31",
        "R33",
        "R34",
        "R35",
        "R36",
        "R69"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/commands/lob/pick.rs",
        "docs/plan/candidates.csv",
        "instruments.csv"
      ],
      "status": "done",
      "startedAt": "2026-09-11T08:50:00+04:00",
      "finishedAt": "2026-09-11T11:20:00+04:00",
      "tests": "500 passed, 0 failed, 5 ignored (3147a32+08 изолированно; флаки conn.rs)",
      "commit": "126dd4d",
      "review": {
        "R-A": "1 blocking: instruments.csv = все кандидаты → только пул + единый ридер; R69/R14 partial (боевое окно и k — T09)",
        "R-B": "нет; пять читателей instruments.csv → сведены ремонтом; pool/table > 690 строк → concerns",
        "R-C": "clean; k как обязательный флаг — верно"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0
    },
    {
      "id": "07",
      "title": "lob watch на сессиях",
      "requirements": [
        "R39",
        "R42",
        "R49",
        "R77i"
      ],
      "blockedBy": [
        "01",
        "04"
      ],
      "wave": 3,
      "zone": [
        "src/lob/watch.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T08:50:00+04:00",
      "finishedAt": "2026-09-11T11:10:00+04:00",
      "tests": "488 passed, 0 failed, 5 ignored (3147a32+07 изолированно)",
      "commit": "4073927",
      "review": {
        "R-A": "1 blocking: охрана --confirmatory на старом флаге → проводка require_session_ready_flag; R49 done",
        "R-B": "нет; дубль trade_hit_from_record, n_c2/g_c2, 1801 строк → concerns",
        "R-C": "clean"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0
    },
    {
      "id": "09",
      "title": "Пилот: отладка ≤5 мин, затем два часа; G-DEBUG, G0, G-POWER-B",
      "requirements": [
        "R40",
        "R51",
        "R68",
        "R78",
        "R81",
        "A01"
      ],
      "blockedBy": [
        "01",
        "02",
        "08"
      ],
      "wave": 3,
      "zone": [
        "src/commands/lob/pilot.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T11:30:00+04:00",
      "finishedAt": "2026-09-11T22:10:00+04:00",
      "tests": "542 passed, 0 failed, 5 ignored (1b8b682+09а изолированно)",
      "commit": "7ef26dd (часть а), 16f91ce (в: боевой пилот 30 мин)",
      "review": {
        "R-A": "нет; R78/R81 done, R40/A01 partial → (б)",
        "R-B": "нет; реплей ×3–4, 1087 строк → concerns",
        "R-C": "1 blocking: G0 по сырому m без проскальзывания → net ≥ 0; снято"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0,
      "note": "(в) боевой пилот 30 мин 2026-09-11: G0 RED edge (net −9.0 bps), G-POWER-B RED (2.26 vs 3.06), k не определим (G1 eaten < 5 % на медианном при всех k)"
    },
    {
      "id": "15",
      "title": "Горячий путь: on_event под Bot<MD>, ордер до триггера, G-LAT, dry-run",
      "requirements": [
        "R09",
        "R74i",
        "R78",
        "R79",
        "R80",
        "A04"
      ],
      "blockedBy": [
        "01",
        "04"
      ],
      "wave": 3,
      "zone": [
        "src/lob/strategy.rs",
        "src/bybit/trade_ws.rs",
        "src/commands/lob/react.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T09:15:00+04:00",
      "finishedAt": "2026-09-11T12:55:00+04:00",
      "tests": "530 passed, 0 failed, 5 ignored (126dd4d+15 изолированно)",
      "commit": "1b8b682 + ремонт 92ae94a",
      "review": {
        "R-A": "нет; R74i/R78/R79 partial — живые числа G-LAT требуют ключей (долг владельца)",
        "R-B": "1 blocking: аллокация в сборке ордера на событие → буферы + rebuild только при смене цены; остаток sign.rs → concerns",
        "R-C": "та же находка → снята; секреты не текут"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0
    },
    {
      "id": "10",
      "title": "lob profiles: таблица профилей",
      "requirements": [
        "R01",
        "R15",
        "R55",
        "R57",
        "R58",
        "R59",
        "R65",
        "R66"
      ],
      "blockedBy": [
        "03",
        "06",
        "15"
      ],
      "wave": 4,
      "zone": [
        "src/commands/lob/profiles.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T13:05:00+04:00",
      "finishedAt": "2026-09-11T15:40:00+04:00",
      "tests": "548 passed, 0 failed, 5 ignored (7ef26dd+10 изолированно)",
      "commit": "f8ef502",
      "review": {
        "R-A": "1 blocking: fill=1.0 из заглушки как факт → FillModel/not_measured/fill_model=none; снято",
        "R-B": "нет; 1066 строк, шапка → concerns",
        "R-C": "1 blocking: raw с .abs() против §11.5 → знаковый; снято"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0
    },
    {
      "id": "11",
      "title": "lob backtest: отчёт на N профилей, та же on_event",
      "requirements": [
        "R03",
        "R09",
        "R10",
        "R11",
        "R59",
        "R80"
      ],
      "blockedBy": [
        "03",
        "15"
      ],
      "wave": 4,
      "zone": [
        "src/lob/backtest.rs",
        "src/commands/lob/backtest.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T13:05:00+04:00",
      "finishedAt": "2026-09-11T16:45:00+04:00",
      "tests": "552 passed, 0 failed, 5 ignored (f8ef502+11 изолированно)",
      "commit": "44aef30",
      "review": {
        "R-A": "1 blocking: читатель --profiles-csv на старой схеме ProfileRow → формат таска 10; R11 done",
        "R-B": "та же находка; снята",
        "R-C": "1 blocking: артефакт без шапки/debug → шапка + --debug; order_qty докстрока → обязательный флаг; снято"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0
    },
    {
      "id": "12",
      "title": "Сбор сессиями, шорт-лист, подтверждение",
      "requirements": [
        "R44",
        "R45",
        "R46",
        "R47",
        "R48",
        "R56",
        "R59",
        "R67",
        "R77i"
      ],
      "blockedBy": [
        "07",
        "10"
      ],
      "wave": 5,
      "zone": [
        "docs/findings/",
        "src/commands/lob/shortlist.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T15:50:00+04:00",
      "finishedAt": "2026-09-11T18:05:00+04:00",
      "tests": "558 passed, 0 failed, 5 ignored (44aef30+12 изолированно)",
      "commit": "d905f19",
      "review": {
        "R-A": "нет; R44/R47/R67/R77i partial — данные и G/hour_tests → T13, календарь владельца",
        "R-B": "нет; чтение по позиции → починено по дозапросу; ScratchRoot → concerns",
        "R-C": "1 blocking: шапка shortlist.md G>=12 против G_MIN=7 → из констант; снято"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 0
    },
    {
      "id": "13",
      "title": "Вердикт в шапке шорт-листа, DSR подключён",
      "requirements": [
        "R47",
        "R49",
        "R50",
        "R51",
        "R52",
        "R53",
        "R54",
        "R59",
        "R68",
        "A03"
      ],
      "blockedBy": [
        "11",
        "12"
      ],
      "wave": 6,
      "zone": [
        "src/commands/lob/shortlist.rs",
        "src/lob/final_metrics.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T18:10:00+04:00",
      "finishedAt": "2026-09-11T19:35:00+04:00",
      "tests": "561 passed, 0 failed, 5 ignored (d905f19+13 изолированно)",
      "commit": "9d7dd50",
      "review": {
        "R-A": "нет; Confirmed недостижим без FillModel → таск 16",
        "R-B": "нет; ветка Ok часового теста не под тестом → concerns",
        "R-C": "clean"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "16",
      "title": "FillModel поверх бэктеста: путь к Confirmed",
      "requirements": [
        "R06",
        "R07",
        "R08",
        "R50",
        "R67"
      ],
      "blockedBy": [
        "10",
        "11",
        "12",
        "13"
      ],
      "wave": 6,
      "zone": [
        "src/commands/lob/backtest.rs",
        "src/commands/lob/profiles.rs",
        "src/commands/lob/shortlist.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-11T19:40:00+04:00",
      "finishedAt": "2026-09-11T21:30:00+04:00",
      "tests": "565 passed, 0 failed, 5 ignored (9d7dd50+16)",
      "commit": "1fc8fc3",
      "review": {
        "R-A": "нет; PBO/CPCV — отдельный таск",
        "R-B": "нет; сквозной тест Confirmed, дубль флагов → concerns",
        "R-C": "нет; блок; ключ-заглушка не в артефактах"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "добавлен по BLOCKERS таска 13"
    },
    {
      "id": "17",
      "title": "Долг ремесла: триаж concerns",
      "requirements": [
        "R71",
        "R73",
        "R79",
        "R80"
      ],
      "blockedBy": [
        "16"
      ],
      "wave": 7,
      "zone": [
        "src/**"
      ],
      "status": "done",
      "startedAt": "2026-09-11T23:20:00+04:00",
      "finishedAt": "2026-09-12T02:25:00+04:00",
      "tests": "558 passed, 0 failed, 5 ignored (cdd9a06+17 изолированно)",
      "commit": "b49d6b1",
      "review": {
        "R-B": "нет (преемник); ready.flag формат, disjoint_contrast pub без вызывающего, критерий «те же артефакты» проверен статически → concerns",
        "R-C": "нет (преемник); чисто"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 2,
      "note": "Phase 8 §1a: fix-now из concerns — сдвоенный код (3+ таска), Args, снос C1/C2, sign_into без аллокаций, мелочи с тестами"
    },
    {
      "id": "18",
      "title": "Динамический порог H3: сетка k в пилоте",
      "requirements": [
        "R14",
        "R40",
        "D05"
      ],
      "blockedBy": [
        "09",
        "17"
      ],
      "wave": 7,
      "zone": [
        "src/commands/lob/pilot.rs",
        "src/commands/lob/levels.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-12T02:30:00+04:00",
      "finishedAt": "2026-09-12T03:25:00+04:00",
      "tests": "571 passed, 0 failed, 5 ignored (b49d6b1+18 изолированно)",
      "commit": "50969d8",
      "review": {
        "R-B": "нет; feed_frames дублирует feed_frames_multi, pilot.rs 2287 строк → concerns",
        "R-C": "нет; ceil→floor в interfaces поправлено"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "В-30/D05 — методология вместо числа k"
    },
    {
      "id": "19",
      "title": "lob session пишет файлы, которые читают остальные команды",
      "requirements": [
        "R12",
        "R38",
        "R59"
      ],
      "blockedBy": [
        "04",
        "18"
      ],
      "wave": 7,
      "zone": [
        "src/commands/lob/session.rs",
        "src/commands/lob/pilot.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-12T04:20:00+04:00",
      "finishedAt": "2026-09-12T05:20:00+04:00",
      "tests": "581 passed, 0 failed, 5 ignored (50969d8+19 изолированно)",
      "commit": "a61f988",
      "review": {
        "R-B": "нет; мягкий пропуск старого формата в profiles/watch без счётчика → concerns"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 1,
      "note": "из слепой приёмки G4: имя бинлога сессии не совпадает с тем, что читают команды"
    },
    {
      "id": "20",
      "title": "Запись: доказать, что работает и экономна",
      "requirements": [
        "R38",
        "R43",
        "R79",
        "R80"
      ],
      "blockedBy": [
        "19"
      ],
      "wave": 8,
      "zone": [
        "src/commands/lob/session.rs",
        "src/feed/live.rs",
        "src/bybit/conn.rs",
        "src/bybit/ws.rs",
        "docs/findings/"
      ],
      "status": "done",
      "startedAt": "2026-09-12T12:10:00+04:00",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "владелец 2026-09-12: длинные прогоны только после доказанной экономности записи",
      "finishedAt": "2026-09-12T15:30:00+04:00",
      "tests": "586 passed, 0 failed, 5 ignored (56e7007+20 изолированно)",
      "commit": "9c16430",
      "review": {
        "R-B": "нет; TaggedSink без юнит-теста → concerns",
        "R-C": "нет; RSS единицы и байт/сутки против отменённого режима → поправлено, D07"
      }
    },
    {
      "id": "21",
      "title": "Окно «сейчас» — предрегистрированный интервал",
      "requirements": [
        "R57"
      ],
      "blockedBy": [
        "12",
        "13"
      ],
      "wave": 8,
      "zone": [
        "src/commands/lob/profiles.rs",
        "src/commands/lob/shortlist.rs",
        "src/lob/shortlist.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-12T12:10:00+04:00",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "из слепой приёмки R57; владелец делегировал определение",
      "finishedAt": "2026-09-12T13:40:00+04:00",
      "tests": "586 passed, 0 failed, 5 ignored (069fbfe+21 изолированно)",
      "commit": "56e7007",
      "review": {
        "R-B": "нет; второй парсер файла предрегистрации, декоративный range-check в shortlist → concerns",
        "R-C": "нет; D06 записан"
      }
    },
    {
      "id": "22",
      "title": "Запись для пилота и несколько сессий в сутки",
      "requirements": [
        "R38",
        "R40",
        "R81"
      ],
      "blockedBy": [
        "19",
        "20"
      ],
      "wave": 9,
      "zone": [
        "src/commands/lob/session.rs",
        "src/commands/lob/mod.rs",
        "src/commands/record.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-12T17:20:00+04:00",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "владелец 2026-09-12: «давай» на пилот; найдено, что пилот записать нечем и сессии в сутки затирают друг друга",
      "finishedAt": "2026-09-12T18:40:00+04:00",
      "tests": "593 passed, 0 failed, 5 ignored (4c919f5+22 изолированно)",
      "commit": "e333f31",
      "review": {
        "R-B": "нет; verify.rs сортировка — попутно починенный скрытый баг, без прямого теста → concerns",
        "R-C": "нет для однодневного пилота; day_utc из каталога — таск 23; D08"
      }
    },
    {
      "id": "23",
      "title": "Сутки на уровне части, не каталога",
      "requirements": [
        "R38",
        "R44",
        "R57"
      ],
      "blockedBy": [
        "22"
      ],
      "wave": 9,
      "zone": [
        "src/commands/lob/mod.rs",
        "src/commands/lob/profiles.rs",
        "src/commands/lob/watch.rs",
        "src/commands/lob/shortlist.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-12T21:00:00+04:00",
      "finishedAt": "2026-09-12T22:10:00+04:00",
      "tests": "607 passed, 0 failed, 5 ignored",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "из ревью R-C таска 22: day_utc из session.json каталога, а не части — ломает кластер суток при многодневном --root. Сделано: session_parts_for/group_parts_by_day/session_days_in_dir в mod.rs; profiles/watch реплеят по суткам, час старта от части; shortlist::group_by_day ставит каталог под каждыми его сутками (зона расширена)"
    },
    {
      "id": "24",
      "title": "Коллектор: супероптимизация горячего пути записи",
      "requirements": [
        "R83",
        "R79",
        "R80"
      ],
      "blockedBy": [
        "23"
      ],
      "wave": 10,
      "zone": [
        "src/bybit/ws.rs",
        "src/bybit/conn.rs",
        "src/feed/live.rs",
        "src/commands/lob/session.rs",
        "src/binlog/mod.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-12T22:40:00+04:00",
      "finishedAt": "2026-09-13T01:40:00+04:00",
      "tests": "615 passed, 0 failed, 5 ignored",
      "commit": "ac5ca69",
      "review": {
        "R-A/R-B": "manifest R83/R79/R80 done; 4 находки manifest, 4 spec — не блокирующие (таймер сброса 3600 с, RSS без артефакта, claim_part продублирован, класс ошибки NotJson)",
        "R-C": "13 находок craft, не блокирующие: фолбэк topic для некомпактного JSON, классификация ошибок, write_frame молчит, гистограмма приватна, третья копия SplitMix64; безопасность олвейс-он передана в T25"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 1,
      "note": "исполнитель 1 упал по API (403 oauth_org_not_allowed на claude-sonnet-5) посреди правки ws.rs/session.rs без handoff-файла; дерево компилируется; преемник на opus читает git diff как handoff. Владелец 2026-09-12: «начать с оптимизации коллектора. супер оптимизировать». Найдено чтением: serde_json::Value на сообщение, zstd-кадр на сообщение, Vec всех задержек (рост RSS), вторая книга в session.rs"
    },
    {
      "id": "25",
      "title": "Олвейс-он коллектор: крутится сутками, экономно",
      "requirements": [
        "R84",
        "R85",
        "R39"
      ],
      "blockedBy": [
        "24"
      ],
      "wave": 10,
      "zone": [
        "src/commands/lob/session.rs",
        "src/commands/record.rs",
        "src/feed/live.rs",
        "src/commands/lob/mod.rs",
        "src/main.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-13T01:45:00+04:00",
      "finishedAt": "2026-09-13T04:10:00+04:00",
      "tests": "627 passed, 0 failed, 5 ignored",
      "commit": "T25",
      "review": {
        "R-A/R-B": "1 blocking (выдуманный артефакт verify-SOLUSDT.status в findings) — закрыт; 3 manifest + 5 spec не блокирующих → concerns",
        "R-C": "2 blocking (полкадра на диске при ошибке сброса; ошибка session.json/ротации роняет цикл без финализации) + порядок финализации + ротация вперёд — закрыты дозапросом; 6 не блокирующих → concerns"
      },
      "retries": 0,
      "repairs": 1,
      "handoffs": 1,
      "note": "владелец 2026-09-12: «повесить олвейсон коллектор, но супер экономный» — снимает R37/R38; ротация по суткам UTC, периодический session.json, Ctrl+C, анализ по живому каталогу"
    },
    {
      "id": "26",
      "title": "Маркер сверки из lob verify — анализ олвейс-он суток без пилота",
      "requirements": [
        "R85",
        "R44"
      ],
      "blockedBy": [
        "25"
      ],
      "wave": 10,
      "zone": [
        "src/commands/lob/verify.rs",
        "src/commands/lob/pilot.rs"
      ],
      "status": "done",
      "startedAt": "2026-09-13T04:30:00+04:00",
      "finishedAt": "2026-09-13T05:05:00+04:00",
      "tests": "630 passed, 0 failed, 5 ignored",
      "commit": "T26",
      "review": {
        "R-A/R-B/R-C": "три оси одним ревьюером (дифф 4 файла): блокирующих нет; verify_file вне зоны — аддитивно, зафиксировано в interfaces; ручная сумма восьми полей VerifySummary, устаревшие докстроки watch.rs/session.rs → concerns"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "из ревью T25: verify-<SYMBOL>.status пишет только lob pilot; profiles/watch без маркера сутки не читают"
    },
    {
      "id": "14",
      "title": "Чистка репозитория и память проекта",
      "requirements": [
        "R65",
        "R70",
        "R71",
        "R73"
      ],
      "blockedBy": [
        "13"
      ],
      "wave": 7,
      "zone": [
        "data/",
        ".autopilot/",
        "."
      ],
      "status": "done",
      "startedAt": "2026-09-11T22:50:00+04:00",
      "finishedAt": "2026-09-12T00:20:00+04:00",
      "tests": "571 passed, 0 failed, 5 ignored",
      "commit": "cdd9a06",
      "review": {
        "R-B": "1 blocking: память утверждала --h3-mode у pilot — снято; числа README сверены"
      },
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    }
  ],
  "gates": [
    {
      "id": "РВ-0",
      "status": "passed",
      "note": "пять независимых ревью — docs/plan/REVIEW-2026-09-11.md"
    },
    {
      "id": "РВ-Р",
      "status": "passed",
      "note": "разведка: 3 инструмента пула × 5 мин — docs/plan/RECON-2026-09-11.md. Сверка 6/6 ok; levels=0 при прогреве 60 мин; p99 даёт 1 уровень/мин и 0 eaten"
    },
    {
      "id": "G2",
      "status": "passed",
      "note": "дважды: 17 + 5 находок, все внесены. D01 снят по уточнению владельца: ≤5 мин — фаза отладки, окно repeat_count — скользящий час по §3"
    },
    {
      "id": "G-DEBUG",
      "status": "passed",
      "note": "ПРОЙДЕН: живой прогон ≤5 мин, 8 инструментов, session→verify→levels→markout→profiles→backtest 8/8 без дефекта — T09б 2af1996, data/pilot-debug/20260911T084259Z. Фаза отладки закрыта; часовые окна и двухчасовой пилот разрешены (нужен k)"
    },
    {
      "id": "G3",
      "status": "passed",
      "note": "15 тасков; трассировка в обе стороны"
    },
    {
      "id": "GC",
      "status": "passed",
      "note": "на пуле из 8 (T20 9c16430): clippy 0, fmt 0, аллокации ноль (тесты), CPU 3.6–4.1 % < 5 %, RSS плоский после прогрева, gaps 0, clock.csv есть; разбор p99 462–851 мкс > 200 мкс калибровочных — единственная строка над суббюджетом. Пилот 30 мин: NTP offset 78.8 мс > 5 мс, parse p99 399.5 мкс, RSS 4.9→18.4 МиБ не плоский — техдолг"
    },
    {
      "id": "G-LAT",
      "status": "pending",
      "note": "замер 5 мин SOLUSDT (92ae94a): разбор p99 68.7 мкс, книга 222.5 мкс, весь путь по 63 срабатываниям median 111.8 мкс / p99 1.77 мс (< 5 мс), но triggers=63 < 1000 — НЕ ОБЪЯВЛЕН; нужен прогон ~1.5 ч (владелец). RTT хоста — lob probe ставит реальные ордера, не запускался"
    },
    {
      "id": "G-POWER-A",
      "status": "passed",
      "note": "lob power на пуле из десяти: N=179 (номинал), SR0=2.73, требуемый Шарп 3.13 при DSR 0.95, n=100 — таск 05 3147a32; N по пригодным парам — T09"
    },
    {
      "id": "G-POWER-B",
      "status": "failed",
      "note": "замеренный Шарп 2.2551 (кросс-инструментный по m10s, 1 день) против требуемого 3.0570 (lob power, n=147) — разрыв 0.80; разведка ждала порядок величины"
    },
    {
      "id": "G0",
      "status": "failed",
      "note": "боевой пилот 30 мин (16f91ce): sparse — ок (26–390 уровней/мин при k=1), edge — RED: net после издержек −9.0 bps < 0. Красный про рынок, не про мощность"
    },
    {
      "id": "G1",
      "status": "failed",
      "note": "eaten 4.0–8.2 % по инструментам при k=1; на медианном инструменте < 5 % при всех k сетки {2,5,10,20,50} → k: не определим (В-30). pulled 85–92 %"
    },
    {
      "id": "G2-в",
      "status": "pending",
      "note": "механизм заморозки — T12 d905f19; сам шорт-лист — после сбора по календарю"
    },
    {
      "id": "G3-в",
      "status": "pending",
      "note": "механизм: DSR по фактическому N двигает порог, три исхода, красный по мощности отдельно — T13 9d7dd50; числа — после FillModel (T16) и боевых сессий"
    },
    {
      "id": "G4-в",
      "status": "pending",
      "note": "механизм: обе кривые PnL (median и p95 RTT), G4 по обеим — T11 44aef30; числа — после боевых сессий"
    },
    {
      "id": "РВ-М",
      "status": "pending",
      "note": "методическое перед вердиктом"
    }
  ],
  "reviewers": {
    "R-A": {
      "axes": [
        "manifest",
        "spec"
      ],
      "handle": "потерян при перезапуске 2026-09-12; для T17/T18 не заводился — оси Манифест закрывает слепая приёмка",
      "lifetime": "до границы волны"
    },
    "R-B": {
      "axes": [
        "craft"
      ],
      "handle": "agent ae4b8b8f7e6f6c2ca (преемник, 2026-09-12; прежний a6fce5029a2c0557f потерян при перезапуске)",
      "lifetime": "весь прогон"
    },
    "R-C": {
      "axes": [
        "data",
        "prereg"
      ],
      "handle": "agent a59385754fa669d45 (преемник, 2026-09-12; прежний a16f59270e1d9f178 потерян при перезапуске — против правила «освежать нельзя», вынужденно)",
      "lifetime": "весь прогон, освежать нельзя"
    }
  },
  "coverage": {
    "findings": 22,
    "acted": 22,
    "note": "G2 дважды: 17 находок на первую редакцию спеки, 5 на вторую (окно repeat_count → D01; семантика теста 3; две кривые PnL; число 30 суток; родитель A01 → R81). Все внесены"
  },
  "concerns": [
    {
      "ticket": "26",
      "file": "src/commands/lob/verify.rs:106, src/bybit/verify.rs:779, src/commands/lob/watch.rs:23, src/commands/lob/session.rs:2300",
      "what": "сумма восьми полей VerifySummary вручную вне типа (новое поле выпадет молча — нужен merge/AddAssign у типа); verify_file(..)? возвращает Err до записи маркера — недекодируемая часть оставляет прежний ok на месте (fail-closed держится отсутствием файла); run_verify остался только в тестах — две обходки одних файлов; докстроки watch.rs/session.rs про «маркер пишет pilot» устарели",
      "kind": "structural"
    },
    {
      "ticket": "25",
      "file": "src/commands/lob/session.rs:~770 (flush_symbol → open_next_part)",
      "what": "при переоткрытии части после потерянной границы синтетический снапшот получает exch_ts_ns = локальный now_ns (смешение доменов часов, NTP offset ~140 мс); метка биржи должна быть последним виденным cts/T инструмента",
      "kind": "clock-domain"
    },
    {
      "ticket": "25",
      "file": "CLAUDE.md:108, src/commands/lob/verify.rs",
      "what": "таблица артефактов приписывает lob verify файлы verify.csv/verify-<SYMBOL>.status — verify.csv пишет сайдкар при записи, маркер — только lob pilot; для анализа олвейс-он данных profiles/watch маркера не получат → T26",
      "kind": "doc-vs-code"
    },
    {
      "ticket": "25",
      "file": "src/commands/lob/session.rs (push_book_snapshot, часовые расписания, вторая Book)",
      "what": "push_book_snapshot — вторая копия record::Recorder::on_snapshot; три независимых часовых расписания (поток clock.csv, сэмплер, on_tick) с гонкой idx на финальном замере — record.rs держит один таймер; вторая Book на инструмент вернулась ради снапшота ротации (apply дважды на дельту) — цена включена в 1.71 % CPU, но снятое таском 24 вернулось",
      "kind": "reinvention"
    },
    {
      "ticket": "25",
      "file": "src/commands/lob/session.rs:918, src/feed/live.rs:420",
      "what": "Disconnected ложится в gaps.csv как sequence_gap (как record.rs) — reconnects по kind не посчитать, тест закрепляет подмену; нужен свой GapKind",
      "kind": "diagnostics"
    },
    {
      "ticket": "25",
      "file": "src/commands/lob/session.rs (rotate_symbol_day при !synced), docs/findings",
      "what": "новая часть суток остаётся 0 байт до снапшота биржи — живой читатель падает на Reader::open в этом окне, не «до последнего кадра»; окно не названо в findings",
      "kind": "doc-gap"
    },
    {
      "ticket": "25",
      "file": "src/feed/live.rs:595",
      "what": "тест без тика блокирует next_event навсегда — CI виснет, не краснеет; нужно ограниченное ожидание",
      "kind": "test-hang"
    },
    {
      "ticket": "25",
      "file": "src/bybit/ws.rs (ParseError::BadShape), session.json.samples",
      "what": "BadShape вместо MissingField/BadNumber по classify() — обосновано (Category::Data их не разделяет), но не записано как отклонение от тикета; «потолок 120 + 24/сутки» — линейный рост, не потолок",
      "kind": "wording"
    },
    {
      "ticket": "24",
      "file": "src/bybit/ws.rs:140",
      "what": "быстрый путь topic_starts_with без фолбэка: валидный, но некомпактный JSON книги (пробел после двоеточия) тихо становится Event::Other, не Book/ParseFailed — прежний DOM-разбор это читал. Передано в T25 критерием (фолбэк через serde либо ParseFailed, тест на некомпактный JSON)",
      "kind": "silent-narrowing"
    },
    {
      "ticket": "24",
      "file": "src/bybit/ws.rs:370",
      "what": "любая ошибка формы при валидном JSON (data не объект, u строкой) — NotJson вместо MissingField/BadNumber; gaps.csv получит ложный диагноз. Передано в T25: serde_json::Error::classify() Data → MissingField/BadNumber, Syntax/Eof → NotJson, с тестом",
      "kind": "diagnostics"
    },
    {
      "ticket": "24",
      "file": "src/commands/lob/session.rs:734",
      "what": "ошибка write_frame проглатывается, квант потери вырос с 1–5 записей до ~1000, следа нет. Передано в T25: frames_failed в session.json / строка gaps.csv",
      "kind": "silent-narrowing"
    },
    {
      "ticket": "24",
      "file": "src/bybit/ws.rs:157",
      "what": "LEVELS_INITIAL_CAPACITY = 8 сослана на RECON, где такого счёта нет; честная ссылка — 230 681 записей / 58 495 кадров ≈ 3.9 на сообщение (collector-2026-09-12.md) или «предположение фикстуры»",
      "kind": "invented-citation"
    },
    {
      "ticket": "24",
      "file": "src/commands/lob/session.rs:775, src/commands/lob/session.rs:1266, tests/collector_bench.rs:137",
      "what": "LatencyHistogram приватен в session.rs (react.rs считает те же p99 по Vec — получит вторую копию); третья копия SplitMix64 при crate::stats::SplitMix64; свой percentile в бенче при probe::percentile_ns. Вынести гистограмму в stats/ или bybit/probe, тесты/бенч — на общие примитивы",
      "kind": "reinvention"
    },
    {
      "ticket": "24",
      "file": "src/commands/lob/session.rs:1210",
      "what": "тест гистограммы — один перцентиль на одной октаве; нужны случайные значения на ≥ 3 октавах, эталон probe::percentile_ns, p ∈ {50,90,99}, 0 ≤ точный − гистограмма ≤ RESOLUTION_PCT × точный",
      "kind": "test-weak"
    },
    {
      "ticket": "24",
      "file": "src/commands/lob/session.rs:749",
      "what": "таймер сброса кадра = HOURLY_REFRESH_SECS (3600 с) — обоснован как период clock.csv, не как окно потери при крахе; в сессии ≤ 6 ч не срабатывает ни разу, тихий инструмент теряет до 1000 записей. Передано в T25 явным критерием (окно потери названо и измерено)",
      "kind": "data"
    },
    {
      "ticket": "24",
      "file": "docs/findings/collector-2026-09-12.md:143",
      "what": "вердикт «RSS плоский» стоит на 30-с сэмплах stderr, которых нет в артефакте на диске (session.json: старт→конец 4.9→16.1 МиБ, старт взят до открытия сокетов). Передано в T25: периодический session.json несёт ряд RSS-сэмплов",
      "kind": "evidence"
    },
    {
      "ticket": "24",
      "file": "src/commands/lob/session.rs:348",
      "what": "claim_symbol_binlog повторяет цикл record::claim_part ради BufWriter — interfaces.md («через record::claim_part») и doc record.rs:480 теперь неточны; обобщить claim_part по W: Write или привести doc к факту",
      "kind": "reinvention"
    },
    {
      "ticket": "24",
      "file": "src/bybit/ws.rs:559",
      "what": "класс ошибки для кривой формы data/b сменился MissingField → NotJson (телеметрия ParseFailed теряет имя поля); фикстура «not json» заменена без причины",
      "kind": "behaviour"
    },
    {
      "ticket": "24",
      "file": "docs/findings/collector-2026-09-12.md:123",
      "what": "остаток 1–2 аллокации на книжное сообщение (Vec в book::Update, владеющая пересылка ConnEvent) — красная строка GC «ноль на событие» без D##; ноль — фиксированная ёмкость 50+50 в book::Update, вне зоны 24; решить D## или таском после запуска",
      "kind": "gc"
    },
    {
      "ticket": "24",
      "file": "src/commands/lob/session.rs:1135",
      "what": "тест батчинга — round-trip binlog::Reader по Record плюс живой lob verify, а не session_binlog_for + FileReplayer со сравнением книги, как требовал критерий",
      "kind": "test-narrowing"
    },
    {
      "ticket": "24",
      "file": "tests/collector_bench.rs",
      "what": "генератор фикстур (SplitMix64 + сборщики сообщений) продублирован в бенче и в тестах session.rs; тест LatencyHistogram стоит на приватных внутренностях",
      "kind": "structural"
    },
    {
      "ticket": "23",
      "file": "src/commands/lob/profiles.rs",
      "what": "срезы середины (mids) и трекер общие на части одних суток (наследие таска 22 «части одним потоком»): уровень, умерший в конце части -p1, ищет markout в срезах части -p2 часами позже — горизонты ≤ 60 с найдут либо ничего, либо чужой срез. На однодневном пилоте не проявляется; до сбора с двумя сессиями в сутки — решить: трекер/mids на часть или разрыв по метке времени",
      "kind": "data"
    },
    {
      "ticket": "01",
      "file": "src/commands/lob/pick.rs",
      "what": "2665 строк при потолке 900 — отбор пула, его тесты и сетевая оболочка в одном файле; разрез отложен в таск 08, который владеет pick.rs",
      "kind": "structural"
    },
    {
      "ticket": "01",
      "file": "—",
      "what": "исполнитель сделал 281 вызов инструментов за 70 минут без handoff при потолке ~50 — нарезка таска 01 слишком крупная: вычистка и разрез стоило резать на два таска",
      "kind": "plan-cut"
    },
    {
      "ticket": "02",
      "file": "src/commands/lob/levels.rs:56-113",
      "what": "resolve_h3_mode/h3_lots_for_symbol общие для levels/markout/pilot/watch, лежат в файле одной подкоманды; по конвенции таска 01 общий код — в mod.rs; перенести при следующем касании (таск 04 держит mod.rs)",
      "kind": "structural"
    },
    {
      "ticket": "02",
      "file": "src/commands/lob/{markout,pilot,watch}.rs",
      "what": "поля h3_mode/h3_lots с одинаковыми doc-комментариями продублированы в трёх Args — просится общая H3Args с #[command(flatten)]",
      "kind": "structural"
    },
    {
      "ticket": "02",
      "file": "src/commands/lob/levels.rs",
      "what": "--h3-lots вместе с --h3-mode floor молча игнорируется, а не отвергается (R-C, вне осей)",
      "kind": "craft"
    },
    {
      "ticket": "03",
      "file": "src/lob/costs.rs:32",
      "what": "докстрока модуля «не выделяет память» стала неверна — net_fill_interval аллоцирует; модуль вне горячего пути, но комментарий вводит в заблуждение",
      "kind": "craft"
    },
    {
      "ticket": "03",
      "file": ".autopilot/…/tickets/03-net-fill.md",
      "what": "ссылка таска «истории 19–22» — R06/R07/R08 по манифесту относятся к историям 23–25; дефект нарезки, не исполнителя",
      "kind": "plan-cut"
    },
    {
      "ticket": "02",
      "file": "manifest.md R40",
      "what": "таск 02 включил R40 без пометки «частично: только флаг режима»; содержание R40 (уровни/мин, число сессий) — T09; строку таска стоило писать явно",
      "kind": "plan-cut"
    },
    {
      "ticket": "06",
      "file": ".autopilot/…/tickets/06-profile-grid.md",
      "what": "критерий «строится крест семи осей» противоречил SETTLED В-18 (полный крест отвергнут); исполнитель построил 8129 ячеек, оркестратор записал D03 в пользу креста и откатил после R-C — ремонт по правильному условию",
      "kind": "plan-cut"
    },
    {
      "ticket": "03",
      "file": "src/lob/costs.rs",
      "what": "935 строк, из них ~400 тесты — растёт к потолку, кандидат на разбор при следующем касании (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "06",
      "file": "src/lob/shortlist.rs",
      "what": "1673 строки после диффа при мягком потолке 900 — выделить блок «тест на час» в отдельный файл (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "06",
      "file": "src/stats/mod.rs:49",
      "what": "докстрока GATE_ALPHA привязана к отменённой поправке C1/C2; переиспользована тестом на час законно, формулировку поправить (R-C, R-A)",
      "kind": "craft"
    },
    {
      "ticket": "04",
      "file": "src/commands/lob/session.rs",
      "what": "--minutes не валидируется в 5..15 рантаймом (только докстрока) — R38 partial; проверка диапазона — таску 09, который гоняет сессии",
      "kind": "craft"
    },
    {
      "ticket": "04",
      "file": "src/commands/lob/session.rs",
      "what": "session.json/stdout без явного маркера debug для прогона ≤5 мин (R-C); downstream ловит короткое окно сам",
      "kind": "craft"
    },
    {
      "ticket": "04",
      "file": "docs/plan/PLAN.md 3.1",
      "what": "первый замер parse_p99 = 251.8 мкс > калибровочного суббюджета 200 мкс; суббюджет пересматривается по замеру — решение при G-LAT (таск 15)",
      "kind": "data"
    },
    {
      "ticket": "04",
      "file": "src/bybit/verify.rs FileReplayer",
      "what": "аллоцирует через mem::take на кадр — вне зоны 04; alloc-тест ReplayFeed бьёт только write_market_event",
      "kind": "structural"
    },
    {
      "ticket": "04",
      "file": "src/feed/{live,replay}.rs",
      "what": "нет grep-теста горячего пути по образцу levels.rs (Instant/SystemTime/f64/HashMap) — нарушений нет по чтению, не по прогону (R-B); добавить в таске 15",
      "kind": "craft"
    },
    {
      "ticket": "04",
      "file": "src/feed/replay.rs:6-13",
      "what": "реконструкция сделки из бит Record.ev дублирует приватную commands::lob::trade_hit_from_record (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "08",
      "file": ".autopilot/…/tickets/08-pick-clean.md",
      "what": "окно 3600 с противоречит фазе отладки (≤5 мин до G-DEBUG); нарезано: код + отладочный прогон ≤5 мин в T08, боевое окно 3600 с и заморозка пула — шаг T09 после G-DEBUG",
      "kind": "plan-cut"
    },
    {
      "ticket": "05",
      "file": "src/commands/lob/{power,session,levels}.rs, pick/table.rs, record.rs",
      "what": "четвёртый независимый читатель instruments.csv; таск 08 добавит coverage_bps и заденет все — общий ридер (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "05",
      "file": "src/commands/lob/power.rs:126",
      "what": "докстринг теста ссылается на устаревшее имя теста shortlist (R-B)",
      "kind": "craft"
    },
    {
      "ticket": "07",
      "file": "src/commands/lob/watch.rs:213",
      "what": "trade_hit_from_record — копия приватной commands::lob::mod::trade_hit_from_record; is_trade_ev живёт в трёх файлах — сделать pub(crate) и переиспользовать (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "07",
      "file": "src/commands/lob/watch.rs WatchSummary",
      "what": "поля n_c2/g_c2 больше не значат C2 — переименовать при сносе C1/C2 в таске 14 (R-B, R-C)",
      "kind": "craft"
    },
    {
      "ticket": "07",
      "file": "src/lob/watch.rs",
      "what": "1801 строка — растёт из волны в волну; снос C1/C2 в таске 14 (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "08",
      "file": "src/commands/lob/session.rs load_pool, power.rs pool_size",
      "what": "не терпят строку `# debug` в instruments.csv (падают на новом файле) и не фильтруют пул до десяти; вместе с levels/record/table — пять читателей одного файла. Общий ридер + фильтр пула — первый шаг таска 09 (R-B, R-C)",
      "kind": "craft"
    },
    {
      "ticket": "08",
      "file": "src/commands/lob/pick/{pool,table}.rs",
      "what": "761/720 строк против названного потолка 690 из таска 01 (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "04",
      "file": "src/bybit/conn.rs:1320 connect_then_close_without_forwarding_a_message_does_not_reset_backoff",
      "what": "флаки под нагрузкой: 1 падение на прогоне 3147a32+07 при параллельных сборках, 3/3 зелёных при повторе — тест зависит от таймингов; починить в таске 14",
      "kind": "flaky-test"
    },
    {
      "ticket": "09",
      "file": ".autopilot/…/tickets/09-pilot-runs.md",
      "what": "G-DEBUG требует цепочку до profiles→backtest (T10/T11), а T10/T11 были blockedBy 09 — цикл; перерезано: T09(а) сейчас — --debug по существующей цепочке; T10/T11 без зависимости от 09; T09(б) — расширение цепочки, G-DEBUG, боевые окна после T11 и k владельца",
      "kind": "plan-cut"
    },
    {
      "ticket": "15",
      "file": "src/bybit/sign.rs Credentials::sign",
      "what": "реальный подписант аллоцирует внутри sign_into (делегирует sign()); ноль аллокаций доказан для фейкового подписанта; буферный HMAC в sign.rs — таск 14 (R-B, R-C)",
      "kind": "structural"
    },
    {
      "ticket": "15",
      "file": "src/lob/strategy.rs best_prices",
      "what": "6-строчная копия приватного хелпера backtest.rs (зона T11) — обоснована в докстроке; свести при таске 11 (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "15",
      "file": "src/commands/lob/react.rs",
      "what": "триггер react — смена лучшего тика, не density-сигнал levels; осознанное упрощение замера, документировано (R-A, R-B)",
      "kind": "craft"
    },
    {
      "ticket": "09",
      "file": "src/commands/lob/pilot.rs run_pilot_debug",
      "what": "нет регресс-теста «--debug не пишет runs.csv» — свойство держится конструкцией (log_pilot_run только в battle); добавить в 09(б) (R-A)",
      "kind": "craft"
    },
    {
      "ticket": "09",
      "file": "src/commands/lob/pilot.rs process_instrument",
      "what": "реплей инструмента 3–4 раза (floor/percentile/markout/агрегация), потому что run_markout не возвращает сырые наблюдения — не горячий путь; свести в 09(б) (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "09",
      "file": "src/commands/lob/pilot.rs",
      "what": "1087 строк — потолок на файл не назначен; структурно (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "10",
      "file": "src/lob/shortlist.rs ProfileRow/write_profiles_csv",
      "what": "старая схема колонок не совпадает с таском 10 — рискует остаться мёртвым кодом; снести в таске 14 (R-A)",
      "kind": "structural"
    },
    {
      "ticket": "11",
      "file": "src/commands/lob/backtest.rs --debug",
      "what": "метка debug по флагу вызывающего, не по длине окна (как levels); читать session.json.duration_s — на будущее; отдельного теста на шапку/debug нет (R-C)",
      "kind": "craft"
    },
    {
      "ticket": "11",
      "file": "src/lob/backtest.rs 1309 строк, src/commands/lob/backtest.rs 579",
      "what": "крупные файлы, потолок не назначен (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "11",
      "file": "src/commands/lob/backtest.rs --order-qty-e9",
      "what": "не читается из instruments.csv/order_size_22a автоматически — обязательный флаг; проводка — таск 13/09б (R-C)",
      "kind": "craft"
    },
    {
      "ticket": "12",
      "file": "src/commands/lob/shortlist.rs --freeze-commit",
      "what": "строка-метка, не проверяется как git-хеш и порядок в git log не проверяется рантаймом — аудируемо человеком; проверка через git cat-file -e — решение владельца (R-A)",
      "kind": "craft"
    },
    {
      "ticket": "12",
      "file": "src/commands/lob/shortlist.rs:463-485 read_profile_table",
      "what": "читает таблицу профилей по позиционным индексам, не по имени колонки — сдвиг колонок в profiles.rs собьёт net_fill тихо; свести к паттерну backtest.rs::read_table (по имени) — таск 13 (R-B)",
      "kind": "craft"
    },
    {
      "ticket": "12",
      "file": "src/commands/lob/shortlist.rs ScratchRoot",
      "what": "копия/хардлинк каталогов сессий во времянку на каждый прогон, до трёх — на многодневных данных дорого; пересмотреть (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "13",
      "file": ".autopilot/…/tickets/13-report-and-dsr.md",
      "what": "критерий «вклад истории контрастом C2 против C1» — из отменённой модели, в манифесте/спеке требования нет; снят при запуске",
      "kind": "plan-cut"
    },
    {
      "ticket": "13",
      "file": "src/commands/lob/profiles.rs:957-982",
      "what": "ветка hour_dependence_test → Ok → log_hour_test не под тестом (фикстуры короче 7 суток) (R-B)",
      "kind": "craft"
    },
    {
      "ticket": "13",
      "file": "src/commands/lob/profiles.rs ProfileAgg::days vs lob/watch.rs WatchSample::g",
      "what": "два счётчика «сколько суток дали наблюдение» — не дубль логики, но семантика одна; держать в согласии (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "13",
      "file": ".autopilot/…/tickets/13-report-and-dsr.md",
      "what": "FillModel поверх бэктеста не вместился — вырезан в таск 16",
      "kind": "plan-cut"
    },
    {
      "ticket": "16",
      "file": "src/commands/lob/shortlist.rs шапка pbo/cpcv",
      "what": "PBO/CPCV печатают None — нужна матрица «испытания × периоды» (покруговые net по общим календарным периодам на все id сетки), которую profiles/backtest не строят; отдельный таск 17 после боевых данных (R-A)",
      "kind": "plan-cut"
    },
    {
      "ticket": "16",
      "file": "src/commands/lob/{profiles,shortlist}.rs",
      "what": "нет сквозного теста profiles→shortlist через BacktestFillModel до Confirmed — только fill на синтетике + decide_profile отдельно (R-A)",
      "kind": "craft"
    },
    {
      "ticket": "09",
      "file": "src/commands/lob/pilot.rs process_instrument",
      "what": "до 5 реплеев на инструмент — run_verify/run_levels/run_markout возвращают только сводки; свести — таск 14 или отдельный (09б)",
      "kind": "structural"
    },
    {
      "ticket": "16",
      "file": "src/commands/lob/{profiles,shortlist}.rs Args",
      "what": "три флага RTT/лота продублированы в двух Args — второй случай паттерна H3Args, свести в общую структуру #[command(flatten)] (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "16",
      "file": "src/commands/lob/shortlist.rs jackknife",
      "what": "leave-one-day-out гоняет весь ScratchRoot-конвейер на каждые сутки — стоимость не оценена (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "15",
      "file": "src/commands/lob/react.rs стадия «книга»",
      "what": "отрицательные длительности на всех строках живого прогона — метка после разбора (parsed_ts_ns, SystemClock) и метки MonotonicClock в разных доменах; весь путь и горизонты непригодны; ремонт",
      "kind": "defect"
    },
    {
      "ticket": "15",
      "file": "src/commands/lob/react.rs триггер",
      "what": "117 срабатываний за 5 мин на SOLUSDT при требовании ≥1000 — план исходил из неверной оценки частоты смены лучшего тика; G-LAT за ≤5 мин не объявляем; решение: считать этапы разбор/книга на каждом событии, триггер/send — на срабатываниях, порог 1000 — по событиям пути",
      "kind": "plan-cut"
    },
    {
      "ticket": "15",
      "file": "src/commands/lob/probe.rs",
      "what": "lob probe меряет RTT реальными post-only ордерами create+cancel (Decision 12, H9) на живом аккаунте — не dry-run; оркестратор и исполнители его не запускают; запуск — владелец сам",
      "kind": "owner-action"
    },
    {
      "ticket": "09",
      "file": "src/commands/lob/pilot.rs read_clock_bybit_rtts",
      "what": "дублирует публичный bybit::clock::read_rows вместо фильтрации его результата (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "17",
      "file": "src/lob/cells.rs disjoint_contrast",
      "what": "стал pub без вызывающего ради dead_code — уместнее #[allow(dead_code)] с комментарием (R-B)",
      "kind": "craft"
    },
    {
      "ticket": "17",
      "file": "src/lob/watch.rs ReadyFlag",
      "what": "ключ n_c2→n в текстовом формате ready.flag — смена формата артефакта; единственный читатель синхронизирован (R-B)",
      "kind": "craft"
    },
    {
      "ticket": "17",
      "file": "src/commands/lob/markout.rs --median-lifetime-ms",
      "what": "обязательный флаг, значение не используется после сноса C1/C2 — снять при следующем касании --help (R-C)",
      "kind": "craft"
    },
    {
      "ticket": "17",
      "file": "tests",
      "what": "сквозной тест profiles→shortlist→Confirmed через BacktestFillModel не написан — нужна фикстура ≥100 исполнений на ≥7 суток",
      "kind": "test-gap"
    },
    {
      "ticket": "18",
      "file": "src/commands/lob/mod.rs feed_frames / feed_frames_multi",
      "what": "feed_frames дублирует блок сбора LevelObs вместо делегирования feed_frames_multi с одноэлементными срезами (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "18",
      "file": "src/commands/lob/pilot.rs",
      "what": "2287 строк, пять забот в одном файле — Divergent Change, разрез при следующем касании (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "19",
      "file": "src/commands/lob/{profiles,watch}.rs",
      "what": "старый недатированный бинлог при сканировании многих сессий пропускается молча без счётчика — печатать «сессий пропущено: старый формат» (R-B)",
      "kind": "craft"
    },
    {
      "ticket": "G4",
      "file": "src/commands/lob/session.rs parse_p99_ns",
      "what": "живой lob session по 8 инструментам: разбор p99 = 1136.6 мкс (221 406 кадров) против суббюджета 200 мкс; на одном инструменте lob react даёт 68.7 мкс — профилирование разбора под пулом; общий бюджет 5 мс не нарушен",
      "kind": "data"
    },
    {
      "ticket": "G4",
      "file": "R57 окно «сейчас»",
      "what": "profiles/shortlist читали все подкаталоги root без окна — закрыто таском 21 (D06/В-31)",
      "kind": "closed"
    },
    {
      "ticket": "21",
      "file": "src/lob/shortlist.rs load_or_write_window",
      "what": "второй парсер формата exploratory:/confirmatory: рядом с commands/lob/shortlist.rs::load_or_write_boundary — свести к одному разбору (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "21",
      "file": "src/commands/lob/shortlist.rs write_full_coverage_preregistration",
      "what": "выбор сессий в shortlist идёт старым списком дней (ScratchRoot), range-check окна там декоративный — два механизма «день в окне» (R-B)",
      "kind": "structural"
    },
    {
      "ticket": "20",
      "file": "src/feed/live.rs TaggedSink",
      "what": "нет юнит-теста с фейковым ConnSink на тегирование/пересылку — покрыто только живым прогоном (R-B)",
      "kind": "test-gap"
    },
    {
      "ticket": "20",
      "file": "src/bybit/ws.rs разбор",
      "what": "parse p99 462–851 мкс под пулом из 8 против калибровочных 200 мкс (один инструмент — 69 мкс); queue p99 690–1090 мкс; общий путь < 5 мс; профилирование JSON-разбора под нагрузкой — следующий заход",
      "kind": "data"
    },
    {
      "ticket": "22",
      "file": "src/bybit/verify.rs run_verify sort",
      "what": "лексикографическая сортировка ставила -p2 перед голым файлом на многочастных днях lob record — попутно починено, прямого теста на run_verify нет (R-B)",
      "kind": "craft"
    },
    {
      "ticket": "09в",
      "file": "src/commands/lob/pilot.rs боевой путь",
      "what": "instruments.csv пришлось копировать в --root руками — боевой путь без copy_pool_instruments_csv отладочного",
      "kind": "craft"
    },
    {
      "ticket": "09в",
      "file": "bybit/clock.rs, session.rs",
      "what": "NTP offset 78.8 мс > 5 мс (GC часы); RSS 4.9→18.4 МиБ за 30 мин не плоский; parse p99 400 мкс — три GC-нарушения на пилоте",
      "kind": "data"
    }
  ],
  "debt": [
    {
      "what": "живой гейт G-LAT (lob react, dry-run без ордеров) требует BYBIT_API_KEY/BYBIT_API_SECRET в окружении — тёплый аутентифицированный WS trade",
      "default": "не запускать; G-LAT не объявлен",
      "owner": "владелец",
      "ticket": "09"
    },
    {
      "what": "k для пола H3 — владелец делегировал методологию (В-30/D05): k выбирает пилот по сетке {2,5,10,20,50} и гейтам G0/G1",
      "default": "реализация — таск 18; до него отладка с k = 1.0",
      "owner": "таск 18",
      "ticket": "18"
    },
    {
      "what": "crossbeam vs tokio::mpsc для канала ввод-вывод → решения (D04)",
      "default": "tokio::mpsc + blocking_recv, замер задержки канала в G-LAT",
      "owner": "владелец",
      "ticket": "15"
    },
    {
      "what": "CLUSDT: baseCoin CL, признака в API нет; CL — тикер нефти WTI",
      "default": "исключить до подтверждения",
      "owner": "владелец",
      "ticket": "08"
    },
    {
      "what": "G_min: задача 7, код 12",
      "default": "7, с печатью df-цены и джекнайфа",
      "owner": "владелец",
      "ticket": "01"
    },
    {
      "what": "по итогам пилота: принять красный / гнать 2 ч (даст percentile) / пересмотреть правило 70/20 «проедают» или пул",
      "default": "—",
      "owner": "владелец",
      "ticket": "—"
    }
  ],
  "additions": [
    {
      "id": "A01",
      "what": "планка после дефляции считается до сбора; красный — сбор не начинается",
      "parent": "R47 + R68"
    },
    {
      "id": "A03",
      "what": "джекнайф-по-суткам рядом с вердиктом",
      "parent": "R68 (в брифе не просилось)"
    },
    {
      "id": "A04",
      "what": "бюджет реакции по этапам, не только весь путь",
      "parent": "R79"
    }
  ],
  "recon": {
    "at": "2026-09-10T21:30:00Z",
    "instruments": [
      "SOLUSDT",
      "NEARUSDT",
      "ZECUSDT"
    ],
    "minutes": 5,
    "verify_ok": "6/6",
    "levels_with_default_warmup": 0,
    "levels_per_min_floor1": {
      "SOLUSDT": 106,
      "NEARUSDT": 136,
      "ZECUSDT": 2925
    },
    "eaten_share": {
      "SOLUSDT": 0.023,
      "NEARUSDT": 0.071,
      "ZECUSDT": 0.015
    },
    "levels_at_p99_per_5min": {
      "SOLUSDT": 5,
      "NEARUSDT": 6,
      "ZECUSDT": 145
    },
    "eaten_at_p99": 0,
    "m_10s_bps_at_p99": {
      "SOLUSDT": -0.6,
      "NEARUSDT": -2.65,
      "ZECUSDT": 1.32
    },
    "sharpe_m_10s": {
      "SOLUSDT": -0.4,
      "NEARUSDT": -0.32,
      "ZECUSDT": 0.24
    },
    "required_sharpe": 2.7
  },
  "blind": {
    "at": "2026-09-12T04:00:00+04:00",
    "artifacts": "data/acceptance-20260911T155044Z/",
    "drift": [
      {
        "req": "R12/R38 запись сессиями",
        "manifest": "done",
        "blind": "частично",
        "what": "lob session пишет <SYMBOL>.binlog, команды анализа ждут <SYMBOL>-<дата>.binlog — сценарий запись→анализ руками обрывается; мост только в pilot",
        "fix": "таск 19"
      },
      {
        "req": "R79 «быстро сразу»",
        "manifest": "in-ticket",
        "blind": "частично",
        "what": "живой lob session по 8 инструментам: разбор p99 = 1136.6 мкс по 221 406 кадрам против суббюджета 200 мкс (в 6 раз); lob react на одном инструменте давал 68.7 мкс",
        "fix": "отчёт: суббюджет пересматривается по замеру (PLAN 3.1), общий 5 мс не нарушен; профилирование разбора на пуле — следующий заход"
      },
      {
        "req": "R47 PBO/CPCV",
        "manifest": "done (DSR)",
        "blind": "частично",
        "what": "PBO/CPCV — функции есть и протестированы, в lob shortlist захардкожены None (нет матрицы испытания×периоды)",
        "fix": "отчёт + отдельный таск после боевых данных"
      },
      {
        "req": "R57 окно «сейчас»",
        "manifest": "in-ticket",
        "blind": "нет",
        "what": "profiles/shortlist читают все подкаталоги root без окна фиксированной длины; длина окна нигде не печатается (repeat_window — другая ось)",
        "fix": "отчёт: открытый вопрос владельцу — длина окна в сутках/сессиях назначается до данных"
      },
      {
        "req": "R45 заморозка коммитом",
        "manifest": "done",
        "blind": "частично",
        "what": "--freeze-commit не проверяется по git log — защита через обязательность файла заморозки",
        "fix": "отчёт (concerns T12)"
      }
    ],
    "not_in_brief": [
      "внутреннее деление RedInsufficientPower/RedNoEdge — детализация красного (A03/R68), в текст вердикта схлопывается"
    ],
    "live_run": "один, 5 мин, lob session: records=1587882 gaps=0, 8 инструментов, без ключей"
  },
  "tests": {
    "passed": 630,
    "failed": 0,
    "ignored": 5,
    "at": "2026-09-13T05:05:00+04:00"
  }
}
