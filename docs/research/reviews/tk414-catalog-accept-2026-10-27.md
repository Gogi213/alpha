# TK-414 каталог 5 точек (итерация 1) — принято (Судья, 27.10 ~07:50)

Сдача: docs/findings/pump-dump-scenarios-catalog-5points-bybit-final-2026-10-27.md (58409fcf, правка текста по возврату 07:36).
Числа приняты прежними проверками: tk414-catalog-gate-ob-return (Gate стакан: 1089 / O1 0,15 / O3 0,09 = csv), tk414-catalog-bybit-final-return (Bybit ob200 = csv), tk414-oifund-bybit-return (OI/фандинг = csv). Нового счёта не было.

| пункт возврата 07:36 | да/нет | на чём проверено |
|---|---|---|
| §3 без устаревшего «не сделано» | да | diff 58409fcf: две строки убраны; осталось только вне заказа «нужно, не скачано» (ob200 spot Bybit, глубина >25 бп, ликвидации Bybit) — разрешено описанием тикета |
| §4.2 «полные тексты не читаются (решение Судьи)», числа литературы в выводах не используются | да | строки 75, 79 |
| §5 21/22 инстр. подписано | да | заголовок §5 |
| нет иных «не сделано» по заказу | да | grep по обоим файлам: одна фраза §4 «отдельные правила разворота (не зеркало) не заданы» — граница замера (заказ 06:47 — зеркало), не хвост |

Вердикт: принято. Хвост вне заказа (данные «нужно, не скачано») — строкой CEO.

## Ключевые числа

| число | файл:строка |
|---|---|
| Bybit: ложные старты 1,69 → 0,05–0,15 на инстр.-сутки (O1/O3/O4), пропуск 83–99 % ног | docs/findings/pump-dump-scenarios-catalog-5points-bybit-final-2026-10-27.md:37 |
| Bybit ob200 O1: 0,15 ложных на инстр.-сутки | docs/findings/pump-dump-scenarios-catalog-5points-bybit-final-2026-10-27.md:27 |
| Точки 4–5: R≈T≈P, разрыв ≤ 5 п.п. | docs/findings/pump-dump-scenarios-catalog-5points-bybit-final-2026-10-27.md:60 |
| Gate фьюч: свечи 1,73 ложных на инстр.-сутки | docs/findings/pump-dump-scenarios-catalog-5points-bybit-final-2026-10-27.md:86 |
| Gate фьюч O1: 0,15 | docs/findings/pump-dump-scenarios-catalog-5points-bybit-final-2026-10-27.md:88 |
| Gate: ног 445 памп / 437 дамп | docs/findings/pump-dump-scenarios-catalog-5points-bybit-final-2026-10-27.md:82 |
