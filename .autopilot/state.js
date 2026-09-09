window.STATE =
{
  "slug": "lob-density",
  "dir": "2026-09-08-lob-density--wip",
  "title": "Плотности в стакане: добавляет ли история уровня информацию к его исходу",
  "mode": "semi",
  "depth": "deep",
  "polish": null,
  "tier": "T3",
  "briefFile": "~/Downloads/lob-density-signal.md (вне репозитория; коды R01-R10 — docs/plan/REQUIREMENTS.md)",
  "memoryFile": "CLAUDE.md",
  "skillDir": "~/.claude/skills/autopilot",
  "startedAt": "2026-09-08T01:40:00+04:00",
  "updatedAt": "2026-09-09T23:04:53+04:00",
  "finishedAt": null,
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-09-08T01:40:00+04:00",
      "finishedAt": "2026-09-08T02:05:00+04:00",
      "note": "репозиторий обнулён, скелет компилируется, rustc 1.93.1"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-09-08T02:05:00+04:00",
      "finishedAt": "2026-09-08T02:10:00+04:00",
      "note": "бриф разобран на 10 требований"
    },
    {
      "id": "briefing",
      "status": "done",
      "startedAt": "2026-09-08T02:10:00+04:00",
      "finishedAt": "2026-09-08T02:20:00+04:00",
      "note": "12 вопросов в OPEN_QUESTIONS.md, ответы батчем в конце"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-09-08T02:12:00+04:00",
      "finishedAt": "2026-09-08T02:22:00+04:00",
      "note": "23 решения, гейты G0-G4 и GC, предрегистрация"
    },
    {
      "id": "plan",
      "status": "done",
      "startedAt": "2026-09-08T02:22:00+04:00",
      "note": "ревизия 8 закрыта, 7 проходов критики, 25 блокеров. Архитектура в docs/ARCHITECTURE.md",
      "finishedAt": "2026-09-08T03:54:10+04:00"
    },
    {
      "id": "build",
      "status": "active",
      "startedAt": "2026-09-08T03:54:10+04:00",
      "note": "рев 14-15: 7 шагов назад в pending, 0.9 в работе, В-11 открыт"
    },
    {
      "id": "review",
      "status": "pending"
    },
    {
      "id": "final",
      "status": "pending"
    }
  ],
  "requirements": {
    "total": 10,
    "done": 1,
    "inTicket": 10,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "0.0",
      "title": "Пин тулчейна: rust-toolchain.toml 1.93.1, rust-version 1.90",
      "requirements": [
        "R04"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "Cargo.toml"
      ],
      "status": "done",
      "startedAt": "2026-09-08T03:54:10+04:00",
      "finishedAt": "2026-09-08T03:54:10+04:00",
      "commit": "3b07fa9",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "0.1",
      "title": "Bybit WS + book: u=1, size=0, ресинк по u, local_ts на recv, impl MarketDepth",
      "requirements": [
        "R06",
        "R08"
      ],
      "blockedBy": [
        "0.0"
      ],
      "wave": 2,
      "zone": [
        "src/bybit/ws.rs",
        "src/book/"
      ],
      "status": "done",
      "startedAt": "2026-09-08T03:54:10+04:00",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "finishedAt": "2026-09-08T05:30:32+04:00",
      "tests": {
        "passed": 89,
        "failed": 0
      }
    },
    {
      "id": "0.2",
      "title": "Бинарный лог: сутки самодостаточны, кадры с u32 длины, надмножество Event",
      "requirements": [
        "R06",
        "R08"
      ],
      "blockedBy": [
        "0.1"
      ],
      "wave": 3,
      "zone": [
        "src/bybit/binlog.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-08T14:55:08+04:00",
      "tests": {
        "passed": 219,
        "failed": 0
      },
      "finishedAt": "2026-09-08T17:08:45+04:00"
    },
    {
      "id": "0.3",
      "title": "lob record: ротация по суткам UTC, gaps.csv",
      "requirements": [
        "R06"
      ],
      "blockedBy": [
        "0.2"
      ],
      "wave": 4,
      "zone": [
        "src/commands/lob.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 1,
      "handoffs": 0,
      "startedAt": "2026-09-08T23:06:58+04:00",
      "finishedAt": "2026-09-09T02:05:51+04:00",
      "tests": {
        "passed": 242,
        "failed": 0
      },
      "commit": "e278d7a",
      "concerns": [
        "ремонт вынесен в 0.7: REST в ветке tokio::select! паникует вложенным рантаймом"
      ],
      "note": "B-1 nested runtime, fix in 0.7"
    },
    {
      "id": "0.4",
      "title": "lob pick: отбор по времени-взвешенной глубине, два кандидата",
      "requirements": [
        "R07",
        "R09"
      ],
      "blockedBy": [
        "0.1"
      ],
      "wave": 3,
      "zone": [
        "src/commands/lob.rs"
      ],
      "status": "in-progress",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-08T14:55:09+04:00",
      "tests": {
        "passed": 219,
        "failed": 0
      },
      "concerns": [
        "SETTLED В-10, восьмой случай: done-condition требует закоммиченную таблицу кандидатов с окном замера и двух финалистов — таблицы нет нигде, коммита у таска тоже нет",
        "lob pick ни разу не запускался: instruments.csv во всех семи прогонах вписан руками и содержит BTCUSDT, который бриф и R07 запрещают прямым текстом",
        "нужен час непрерывного живого orderbook.50 по двадцати символам (Decision 18, H7) — команда уже существует, можно запускать"
      ]
    },
    {
      "id": "0.5",
      "title": "lob clock: смещение против NTP и serverTime, clock.csv раз в час",
      "requirements": [
        "R08"
      ],
      "blockedBy": [
        "0.1"
      ],
      "wave": 3,
      "zone": [
        "src/bybit/clock.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-08T14:55:09+04:00",
      "tests": {
        "passed": 219,
        "failed": 0
      },
      "finishedAt": "2026-09-08T17:08:46+04:00"
    },
    {
      "id": "0.6",
      "title": "lob verify: сверка с REST по u, инварианты, трейды внутри книги",
      "requirements": [
        "R06"
      ],
      "blockedBy": [
        "0.3"
      ],
      "wave": 5,
      "zone": [
        "src/bybit/verify.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T00:02:50+04:00",
      "finishedAt": "2026-09-09T05:08:46+04:00",
      "tests": {
        "passed": 299,
        "failed": 0
      },
      "commit": "36b44b5"
    },
    {
      "id": "0.7",
      "title": "REST вне событийного пути: отдельная задача, таймауты, регресс-тест",
      "requirements": [
        "R06",
        "R08"
      ],
      "blockedBy": [
        "0.3"
      ],
      "wave": 5,
      "zone": [
        "src/commands/record.rs",
        "src/bybit/rest.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 1,
      "handoffs": 0,
      "startedAt": "2026-09-09T01:44:49+04:00",
      "finishedAt": "2026-09-09T02:51:40+04:00",
      "tests": {
        "passed": 280,
        "failed": 0
      },
      "commit": "612369d",
      "concerns": [
        "ремонт Р1/Р2 принят: будильник sync_channel(1) + recv_timeout(1h), источник шагов за трейтом с тестовым спавном"
      ]
    },
    {
      "id": "0.8",
      "title": "verify.csv сайдкаром при записи, раз в 5 минут",
      "requirements": [
        "R06"
      ],
      "blockedBy": [
        "0.6",
        "0.7"
      ],
      "wave": 6,
      "zone": [
        "src/bybit/verify.rs",
        "src/commands/record.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 1,
      "handoffs": 0,
      "startedAt": "2026-09-09T02:07:39+04:00",
      "finishedAt": "2026-09-09T04:26:36+04:00",
      "tests": {
        "passed": 293,
        "failed": 0
      },
      "commit": "202915e",
      "concerns": [
        "живой прогон: verify.csv за два запуска по 5+ минут — одна шапка, ноль строк. Запись при этом исправна (240 КБ бинлога, gaps.csv пуст, ротация по UTC верна)",
        "Р1 (SETTLED В-8): порядок сверки перевёрнут против 0.6. Книга замораживается, снапшот приходит позже с другим u — Misaligned всегда. Плюс два независимых тикера: сайдкар берёт кадр предыдущего тика, пятиминутной давности",
        "Р2 (SETTLED В-9): сайдкар — tokio::spawn, get() внутри рантайма join-ит ОС-поток и блокирует воркер до 10 с. На VPS с 1 vCPU встаёт conn.rs и портит local_ts, на котором стоит 6.1",
        "гейт 4.1 читает долю расхождений теста 1: при обоих дефектах он зелёный не потому, что книга верна, а потому что сверка не состоялась"
      ]
    },
    {
      "id": "0.9",
      "title": "Недостающий CLI: clock, probe, levels, markout, pilot, watch",
      "requirements": [
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
        "R06",
        "R08"
      ],
      "blockedBy": [
        "0.5",
        "1.2",
        "2.1",
        "6.2"
      ],
      "wave": 5,
      "zone": [
        "src/commands/lob.rs",
        "src/main.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T21:13:52+04:00",
      "finishedAt": "2026-09-09T21:46:19+04:00",
      "tests": {
        "passed": 415,
        "failed": 0
      },
      "commit": "2a65722"
    },
    {
      "id": "1.1",
      "title": "lob levels: жизнь уровня + шесть признаков истории колонками",
      "requirements": [
        "R01",
        "R10"
      ],
      "blockedBy": [
        "0.2"
      ],
      "wave": 4,
      "zone": [
        "src/lob/levels.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-08T23:06:58+04:00",
      "finishedAt": "2026-09-08T23:39:48+04:00",
      "tests": {
        "passed": 224,
        "failed": 0
      },
      "commit": "abd01ad"
    },
    {
      "id": "1.2",
      "title": "Классификация eaten/pulled/mixed, фильтр BT и стороны агрессора",
      "requirements": [
        "R01"
      ],
      "blockedBy": [
        "1.1"
      ],
      "wave": 5,
      "zone": [
        "src/lob/levels.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T00:02:50+04:00",
      "finishedAt": "2026-09-09T00:41:40+04:00",
      "tests": {
        "passed": 244,
        "failed": 0
      },
      "commit": "82005b5"
    },
    {
      "id": "2.1",
      "title": "lob markout: формула с сигмой по стороне, база до исчезновения",
      "requirements": [
        "R02"
      ],
      "blockedBy": [
        "1.2"
      ],
      "wave": 6,
      "zone": [
        "src/lob/markout.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "concerns": [
        "греп границы модулей пофайловый (levels.rs:689), не по дереву: markout.rs обязан унести такой же тест, иначе гарантия «REST до ядра не дотянется» на него не распространяется"
      ],
      "startedAt": "2026-09-09T02:14:44+04:00",
      "finishedAt": "2026-09-09T02:19:00+04:00",
      "tests": {
        "passed": 269,
        "failed": 0
      },
      "commit": "c294d63"
    },
    {
      "id": "3.1",
      "title": "Пилот 2 часа по двум кандидатам, гейт G0 — до недели записи",
      "requirements": [
        "R03"
      ],
      "blockedBy": [
        "0.4",
        "0.5",
        "0.6",
        "0.7",
        "0.9",
        "2.1"
      ],
      "wave": 8,
      "zone": [
        "docs/findings/"
      ],
      "status": "in-progress",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "note": "В-11 открыт: 5-мин прогоны были отладкой, не пилотом; длительность/порог G0 решает план",
      "startedAt": "2026-09-09T03:04:25+04:00",
      "concerns": [
        "SETTLED В-11: таск был переименован в «пилотный прогон 5 минут», а PLAN.md требует два часа по двум кандидатам (H10), считается второй; G0 требует ≥200 зачтённых pulled-уровней",
        "пять минут не дают ни часа прогрева (H3), ни двухсот уровней. Развилка: гонять два часа как написано ЛИБО править план явно — новая длительность, новый порог G0, обоснование. Молча нельзя ни то, ни другое",
        "Decision 4 ставит G0 до недельной записи: сейчас это единственный незакрытый таск, а всё, что по плану идёт после него, было закрыто"
      ]
    },
    {
      "id": "4.1",
      "title": "Открытая запись + lob watch: флаг по размеру выборки, не по результату",
      "requirements": [
        "R06",
        "R05"
      ],
      "blockedBy": [
        "0.5",
        "0.6",
        "0.8",
        "0.9",
        "3.1"
      ],
      "wave": 9,
      "zone": [
        "src/lob/watch.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T02:20:22+04:00",
      "tests": {
        "passed": 277,
        "failed": 0
      },
      "commit": "a3d0a53",
      "concerns": [
        "SETTLED В-10: реализация есть (коммит и тесты на строке), измерение не проведено — done-condition называет число, а не код",
        "ready.flag с n/G/временем и progress.csv со строкой на сутки — ни одного файла нет; данных семь прогонов по 5 минут вместо недели"
      ],
      "note": "rev14: возврат в pending по правилу закрытия (коммит и тесты сохранены)"
    },
    {
      "id": "5.1",
      "title": "Разметка недели, гейт G1",
      "requirements": [
        "R01"
      ],
      "blockedBy": [
        "4.1",
        "1.2"
      ],
      "wave": 10,
      "zone": [
        "docs/findings/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T12:30:00+04:00",
      "tests": {
        "passed": 320,
        "failed": 0
      },
      "commit": "0b38f2d",
      "concerns": [
        "SETTLED В-10: реализация есть (коммит и тесты на строке), измерение не проведено — done-condition называет число, а не код",
        "распределение исходов не напечатано: docs/findings/ не существует, вердикт G1 не вынесен"
      ],
      "note": "rev14: возврат в pending по правилу закрытия (коммит и тесты сохранены)"
    },
    {
      "id": "5.2",
      "title": "Ячейки C1 и C2, разность с интервалом, гейт G2",
      "requirements": [
        "R02",
        "R10"
      ],
      "blockedBy": [
        "5.1",
        "2.1",
        "7.2"
      ],
      "wave": 11,
      "zone": [
        "src/lob/markout.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T12:39:54+04:00",
      "tests": {
        "passed": 336,
        "failed": 0
      },
      "commit": "2cc9b32",
      "concerns": [
        "SETTLED В-10: реализация есть (коммит и тесты на строке), измерение не проведено — done-condition называет число, а не код",
        "вердикт G2 не вынесен: нет подтверждающей выборки, нет интервала, нет контраста C2 против C1\\C2"
      ],
      "note": "rev14: возврат в pending по правилу закрытия (коммит и тесты сохранены)"
    },
    {
      "id": "5.3",
      "title": "Издержки и проскальзывание по Decision 15, гейт G3",
      "requirements": [
        "R03"
      ],
      "blockedBy": [
        "5.2"
      ],
      "wave": 12,
      "zone": [
        "docs/findings/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T14:47:45+04:00",
      "tests": {
        "passed": 348,
        "failed": 0
      },
      "commit": "39fb6a1",
      "concerns": [
        "SETTLED В-10: реализация есть (коммит и тесты на строке), измерение не проведено — done-condition называет число, а не код",
        "вердикт G3 не вынесен: издержки и проскальзывание не посчитаны ни на одном прогоне"
      ],
      "note": "rev14: возврат в pending по правилу закрытия (коммит и тесты сохранены)"
    },
    {
      "id": "6.1",
      "title": "lob export: npy через write_npy крейта, exch_ts < local_ts",
      "requirements": [
        "R04"
      ],
      "blockedBy": [
        "0.2",
        "4.1"
      ],
      "wave": 10,
      "zone": [
        "src/lob/export.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T05:10:17+04:00",
      "finishedAt": "2026-09-09T08:50:11+04:00",
      "tests": {
        "passed": 309,
        "failed": 0
      },
      "commit": "5ad9b1f"
    },
    {
      "id": "6.2",
      "title": "lob probe: RTT, HMAC-SHA256, ключи из окружения",
      "requirements": [
        "R04",
        "R08"
      ],
      "blockedBy": [
        "0.0"
      ],
      "wave": 2,
      "zone": [
        "src/bybit/probe.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-08T04:14:28+04:00",
      "finishedAt": "2026-09-08T05:30:33+04:00",
      "tests": {
        "passed": 89,
        "failed": 0
      },
      "concerns": [
        "подпись, ключи и статистика RTT в силе; транспорт замера заменяется в 6.4"
      ]
    },
    {
      "id": "6.3",
      "title": "hftbacktest с очередью и измеренным RTT, гейт G4",
      "requirements": [
        "R04"
      ],
      "blockedBy": [
        "5.3",
        "6.1",
        "6.4"
      ],
      "wave": 13,
      "zone": [
        "src/lob/backtest.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T17:42:29+04:00",
      "tests": {
        "passed": 384,
        "failed": 0
      },
      "commit": "6f71a00",
      "concerns": [
        "SETTLED В-10: реализация есть (коммит и тесты на строке), измерение не проведено — done-condition называет число, а не код",
        "вердикт G4 не вынесен: нет PnL-кривой, числа заполнений и колонки пропущенных сигналов"
      ],
      "note": "rev14: возврат в pending по правилу закрытия (коммит и тесты сохранены)"
    },
    {
      "id": "6.4",
      "title": "RTT по WS trade, REST-число справочной колонкой",
      "requirements": [
        "R04"
      ],
      "blockedBy": [
        "6.2"
      ],
      "wave": 12,
      "zone": [
        "src/bybit/trade_ws.rs",
        "src/bybit/probe.rs"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T17:10:57+04:00",
      "finishedAt": "2026-09-09T17:18:52+04:00",
      "tests": {
        "passed": 368,
        "failed": 0
      },
      "commit": "e34ba0b"
    },
    {
      "id": "7.1",
      "title": "runs.csv: журнал прогонов, ведётся с пилота",
      "requirements": [
        "R05"
      ],
      "blockedBy": [
        "3.1"
      ],
      "wave": 9,
      "zone": [
        "docs/plan/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T20:01:36+04:00",
      "tests": {
        "passed": 405,
        "failed": 0
      },
      "commit": "bd9775d",
      "concerns": [
        "SETTLED В-10: реализация есть (коммит и тесты на строке), измерение не проведено — done-condition называет число, а не код",
        "runs.csv не существует ни в одном каталоге, а вести его положено с 3.1"
      ],
      "note": "rev14: возврат в pending по правилу закрытия (коммит и тесты сохранены)"
    },
    {
      "id": "7.2",
      "title": "src/stats: wild cluster bootstrap-t, DSR, PBO, CPCV",
      "requirements": [
        "R05"
      ],
      "blockedBy": [
        "0.0"
      ],
      "wave": 2,
      "zone": [
        "src/stats/"
      ],
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-08T04:14:27+04:00",
      "finishedAt": "2026-09-08T05:30:33+04:00",
      "tests": {
        "passed": 89,
        "failed": 0
      },
      "note": "факт: только bootstrap-t; DSR/PBO/CPCV реализованы в 7.3 (lob/final_metrics.rs)"
    },
    {
      "id": "7.3",
      "title": "DSR, PBO, CPCV на итоговом прогоне",
      "requirements": [
        "R05"
      ],
      "blockedBy": [
        "6.3",
        "7.1",
        "7.2"
      ],
      "wave": 14,
      "zone": [
        "docs/findings/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0,
      "startedAt": "2026-09-09T19:47:13+04:00",
      "tests": {
        "passed": 399,
        "failed": 0
      },
      "commit": "cfb7d50",
      "concerns": [
        "SETTLED В-10: реализация есть (коммит и тесты на строке), измерение не проведено — done-condition называет число, а не код",
        "три числа отчёта (DSR, PBO, CPCV) не получены: итогового прогона не было"
      ],
      "note": "rev14: возврат в pending по правилу закрытия (коммит и тесты сохранены)"
    }
  ],
  "singlePass": null,
  "tests": null,
  "debt": {
    "placeholders": [],
    "assumptions": [
      "H1 ОТВЕЧЕНО: пишем сами, запись открытая, стоп по размеру выборки (Decision 21)",
      "H2 инструмент назначается в 0.4 правилом Decision 18: средняя треть по обороту",
      "H3 порог крупного уровня: 99-й перцентиль по времени-взвешенной выборке, прогрев 60 минут",
      "H4 комиссии 0.02 / 0.055, круг мейкер-тейкер 7.5 bps — одно число для G0 и G3",
      "H5 бюджет лога 12 байт на событие после zstd, около 50 МБ в сутки на символ",
      "H6 ОТВЕЧЕНО: минимальный лот, гейты в bps, зелёный при эдже 3 bps сверх издержек",
      "H7 отбор: предфильтр по обороту до 20, затем час живой глубины, ранг по ней",
      "H8 разрыв или провал verify выбрасывает сутки; потолка нет, флаг просто отодвигается",
      "H9 ОТВЕЧЕНО: живой счёт, post-only, минимальный размер, ключи из переменных окружения",
      "H10 пилот по двум кандидатам, G0 красный только если оба",
      "H11 ОТМЕНЕНО Decision 21: недобор больше не исход, запись идёт до флага",
      "H12 ОТВЕЧЕНО: VPS в регионе Bybit, часы по NTP, local_ts по возврату из recv"
    ],
    "emptyEnv": [
      "BYBIT_API_KEY",
      "BYBIT_API_SECRET"
    ]
  },
  "additions": [
    "0.7 — REST вынесен из событийного пути рекордера, таймауты на HTTP-клиентах",
    "6.4 — RTT меряется по WS trade, а не по REST: G4 обязан стоять на транспорте, которым система торгует",
    "0.8 — verify.csv сайдкаром, чтобы тест 1 существовал на неделе и при этом не входил в цикл записи",
    "ремонт 0.7 (ревизия 12): будильник авторитета и трейт для источника шагов — две done-condition, Р1 и Р2",
    "ремонт 0.8 (ревизия 13): выравнивание по u в порядке 0.6 и сайдкар в ОС-поток — две done-condition, Р1 и Р2"
  ],
  "coverage": null,
  "concerns": [
    "вердикты G0-G4 вынести нечем: шесть из десяти подкоманд не объявлены (В-12). Это причина, а не следствие отсутствия артефактов",
    "покрытие брифа считается из статусов тасков (st.py recount_requirements); коды R01-R10 восстановлены реконструкцией в docs/plan/REQUIREMENTS.md — исходной таблицы manifest не существует",
    "дефект В-9 не воспроизводится на многоядерной машине: тест обязан задавать worker_threads = 1 явно, иначе он не проверяет ничего",
    "требование «HFT без REST в коре»: ядро (src/lob, src/book, src/stats) чистое и это проверено грепом, нарушения только на краях — 0.7 и 6.4",
    "ARCHITECTURE.md строки 22 и 153 прямо коммитят исполнение в REST; текст правится вне этих тасков",
    "A6 «живой режим достаётся бесплатно» неточно: LiveBot параметризован Channel и шлёт LiveRequest::Order в iceoryx2-IPC, коннектора биржи в крейте нет — живой режим стоит отдельного процесса",
    "ПРОГРЕСС ПРОЕКТА вверху считает этапы конвейера, а не работу: пять из восьми этапов это планирование, по трудозатратам процентов пять. Честные числа — ПОКРЫТИЕ БРИФА и ТАСКИ, оба ноль",
    "первый содержательный ответ — гейт G0, два часа пилота после того как заработает код; торгуемость — не раньше 12 суток открытой записи",
    "Bybit не даёт order-level: единица анализа уровень-кластер, пер-ордерная атрибуция невозможна",
    "20 мс это каденция снапшота, не поток событий: add+cancel внутри окна не наблюдаем",
    "data/bybit не восстановим: часы, verify и пилот стоят до недельной записи",
    "7 суток дают 7 кластеров, поэтому wild cluster bootstrap-t, а не обычная робастная дисперсия",
    "tungstenite выделяет Vec на кадр безусловно: бюджет аллокаций разделён на транспорт и разбор",
    "переставление уровня остаётся эвристикой и в гейты не входит: агрегат не доказывает тождество заявки",
    "открытая запись требует правила остановки по размеру выборки: lob watch не имеет права считать markout",
    "бюджет лога 12 байт на событие после zstd: при 4.3 млн обновлений в сутки это 50 МБ против 260 МБ наивных"
  ],
  "reviewers": {
    "manifestSpec": null,
    "craft": null
  },
  "blind": null
}
