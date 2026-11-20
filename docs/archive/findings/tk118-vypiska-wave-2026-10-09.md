# TK-118: первая боевая подача (tk115-delta-wave) — почему защита не сработала

Причина: подано `--vypiska /data/tk0115/delta/wave-vypiska.txt` — это СОХРАНЁННЫЙ ВЫВОД `guard.py plan`, а отметка ключуется по хешу ФАЙЛА ШАГОВ (`wave-steps.tsv`, отметка e5989a46… есть с 18:07:47). `vypiska_marked` читал файл как шаги → другого хеша нет → «подана без выписки» (alerts 18:08:12). Флаг в jobs json не писался.

Вторая дыра (выписка «1 шаг, ~0 с»): файл шагов объявил одну обёртку `bash tk115-delta-wave.sh`; сутки внутри (`tk115-delta-day.sh`) зовут `lob touches`/`bounce-grid` напрямую, не через `guard step` — пошаговой защиты нет. Правка скрипта — зона TK-115, здесь только предупреждение.

Исправлено (calc: guard.py, alsched.py, бэкапы `.bak-tk118b`; волна не тронута, демон не перезапускался):
- `guard.vypiska_mark(путь)`: принимает файл шагов ИЛИ сохранённый вывод plan (первая строка «ВЫПИСКА <шаги>: шагов N» → отметка по файлу шагов); `vypiska_marked` — обёртка.
- plan: одна обёртка `bash X.sh` → «ВНИМАНИЕ … ОДНОЙ обёртке», `wrapper_only` в отметке; цена неизвестна → «неизвестна (N шагов …)», не «~0 с».
- alsched submit: в job json поле `vypiska` ({file, steps, have, new, wrapper_only} | {file, missing}); предупреждение в alerts.log также для wrapper_only и с подсказкой, что принимается.
- Тест: test_vypiska_accepts_saved_plan_output_and_flags_wrapper, 28/28.

Проверка на копии этой подачи (calc, юнит tk118chk): `--vypiska` = вывод plan → отметка найдена, `wrapper_only: true`, предупреждение об обёртке; `/nonexistent` → `missing`. Подача wave-vypiska.txt теперь не давала бы «без выписки», но давала бы «одна обёртка».
