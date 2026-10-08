# Плагин: доводка до финала — дыра → PR/коммит → тест (TK-090)

Обновляется по мере закрытия. «Как проверено в бою» заполняется после выкладки 1.8.4.

| Дыра | Причина → чем устранена | PR / коммит | Тест | Как проверено в бою |
|---|---|---|---|---|
| Д-1 ci_watch под присмотром | два списка служб (start.SERVICES и supervise.BEATS) → один `start.services()`, supervise берёт его же | плагин #46, #52 | test_supervise_and_start_share_one_service_list; test_start (окружение не течёт, 5fe1eef) | после выпуска: `--install` + убить ручной ci_watch; pulse collect/web — задание Планировщика alpha (открыто) |
| Д-2 env из User | открыто (перенос в `.claude/settings.json` env + supervise) | — | — | — |
| Д-3 мёртвые переменные | открыто (вместе с Д-2) | — | — | — |
| Д-4 хук владельца | хук лежал только вне репозитория и повторно вызывал delete_guard (плагин уже подключает его в hooks.json) → копия `tools/hooks/alpha_one_build.py` без вызова стража, развёрнута в `~/.claude/hooks`, старый — `alpha_one_build.py.pre-tk090` рядом | alpha (этот коммит) | ручной прогон: cargo test → deny; ssh cargo, cargo fmt, Write → пропуск; мусор на stdin → deny | следующий вызов Bash в alpha |
| Д-5 снимок env устаревает | снимок перекрывал settings.json → ключи settings в снимок не пишутся | плагин #46, #52 | test_snapshot_skips_settings_keys_so_deleted_key_disappears | после `--install` |
| Д-6 мусор переноса | задание `alpha-supervise` удалено из Планировщика; `*.flag`, `*.pre-*`, runtime служб — в `.claude/dispatcher/.gitignore`; `settings.json.pre-plugin` → `docs/archive/` | alpha (этот коммит) | `git status` без этих файлов | — |
| Д-7 батарея / нагон | `schtasks /Create` ставит значения по умолчанию, на ноутбуке (Win32_Battery есть) присмотр не стартует на батарее → `schtasks_settings()` после создания; живое задание `rpv-supervise-*` исправлено сейчас (Disallow…=False, Stop…=False, StartWhenAvailable=True) | плагин #53 | test_schtasks_settings_run_on_battery_and_catch_up; test_install_on_windows_applies_battery_settings_after_create | Get-ScheduledTask после правки |
| Д-8 обрыв связи ПК | открыто | — | — | — |
| Д-9 сессия без списков стража | следует из Д-2 | — | — | — |
| (а) круг accept влитого PR | у Судьи не было итога на влитом PR → accept влитого PR закрывает тикет | плагин #49 | test_final_accept_on_landed_pr_closes_without_round | финальная приёмка TK-090 |
| (б) CEO не может закрыть | done от CEO без роли разрешён и не будит Судью | плагин #49 | сквозной тест result | — |
