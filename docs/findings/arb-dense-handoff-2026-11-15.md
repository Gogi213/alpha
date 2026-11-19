# Плотный макет «Арбитраж»: передача дел, 15.11.2026 ~18:40 GMT+4

Ветка `bb/g-022-arb-dense-v1-thr_pgaww747vc`, родитель `thr_ty6ae9r46y`. Отчёт — `docs/findings/arb-dense-visual-2026-11-15.md` (токены Spreader, решения, «Столбцы»). Дочерние треды — английский, итог — `bb thread tell thr_ty6ae9r46y`.

## Готово (всё закоммичено, не выложено)
- `tools/collector_health/page-dense.html` — плотный стиль (Geist / Geist Mono, строка 28 px), «Столбцы» (флажок на столбец, localStorage `alpha.arb.cols.hidden`, «по умолчанию»), у столбцов свои целые ширины, карточка сжимается слева.
- Макет и PNG — `Downloads/alpha-arb-dense-mockup*.{html,png}`; копии PNG — `docs/findings/arb-dense-*-2026-11-15.png`.
- Тесты: `perf/layout_test_shadcn.js` (77 действий) и `perf/layout_test_cols.js` (50 переключений + перезагрузка, сброс, окно) на 1180 / 1280 / 1440: сдвиг 0, сбоев 0, ошибок 0.

## Как пересобрать
- Скрипты и данные — `C:\Users\Георгий\AppData\Local\Temp\dense\` (`work\build.sh` — сборка макета из `page-dense.html`; `data\` — снимок arb.json / chart.jsonl / v3.json / turnover.json; `fonts\` — Geist, fontsource 5.3.0, SIL OFL; `tools\shot.js`, `popshot.js` — PNG). Если каталога нет — данные снимка взять из `Temp\shadcn\data\`, шрифты из npm `@fontsource-variable/geist`, `…/geist-mono`.
- Тесты: `node tools/collector_health/perf/layout_test_{shadcn,cols}.js file:///…/alpha-arb-dense-mockup.html <ширина>` (Playwright + Edge, путь в начале файла).

## Открыто
- Выкладка не заказана. Если выбран плотный вариант — шрифты Geist (4 файла) вместо Inter: список имён в `serve.py` и шаг 4 `arb-shadcn-deploy-handoff-2026-11-15.md`.
- Минимум карточки таблицы 480 px (ниже — столбцы растягиваются) — решение исполнителя, владельцу не показывалось.
