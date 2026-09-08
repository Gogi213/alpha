window.STATE =
{
  "slug": "lob-density",
  "dir": "2026-09-08-lob-density--wip",
  "title": "Плотности в стакане: добавляет ли история уровня информацию к его исходу",
  "mode": "semi",
  "depth": "deep",
  "polish": null,
  "tier": "T3",
  "briefFile": "lob-density-signal.md",
  "memoryFile": "CLAUDE.md",
  "skillDir": "/c/Users/Георгий/.claude/skills/autopilot",
  "startedAt": "2026-09-08T01:40:00+04:00",
  "updatedAt": "2026-09-08T04:08:12+04:00",
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
      "note": "0.1: разбор и книга готовы, 22 теста зелёные; остался сокет и local_ts"
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
    "done": 0,
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
      "status": "in-progress",
      "startedAt": "2026-09-08T03:54:10+04:00",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "0.4",
      "title": "lob pick: отбор по времени-взвешенной глубине, два кандидата",
      "requirements": [
        "R07"
      ],
      "blockedBy": [
        "0.1"
      ],
      "wave": 3,
      "zone": [
        "src/commands/lob.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "3.1",
      "title": "Пилот 2 часа по двум кандидатам, гейт G0 — до недели записи",
      "requirements": [
        "R03"
      ],
      "blockedBy": [
        "2.1",
        "0.4",
        "0.6",
        "0.5"
      ],
      "wave": 7,
      "zone": [
        "docs/findings/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
    },
    {
      "id": "4.1",
      "title": "Открытая запись + lob watch: флаг по размеру выборки, не по результату",
      "requirements": [
        "R06",
        "R05"
      ],
      "blockedBy": [
        "0.6",
        "0.5",
        "3.1"
      ],
      "wave": 8,
      "zone": [
        "src/lob/watch.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "wave": 8,
      "zone": [
        "docs/plan/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "wave": 9,
      "zone": [
        "docs/findings/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "wave": 9,
      "zone": [
        "src/lob/export.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "wave": 10,
      "zone": [
        "src/lob/markout.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "wave": 11,
      "zone": [
        "docs/findings/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
        "6.2"
      ],
      "wave": 12,
      "zone": [
        "src/lob/backtest.rs"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
      "wave": 13,
      "zone": [
        "docs/findings/"
      ],
      "status": "pending",
      "retries": 0,
      "repairs": 0,
      "handoffs": 0
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
  "additions": [],
  "coverage": null,
  "concerns": [
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
