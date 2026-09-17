//! Аргументы `lob session` и разрешение режима прогона: границы
//! `--minutes`/`--pilot-minutes`, `SessionArgs`, `SessionPlan`. Отдельно от
//! цикла записи — это разбор флагов до сети и диска, у него свои тесты на
//! диапазоны и на «ровно один флаг».

use std::path::PathBuf;

use clap::Args;

use crate::bybit::rest::BYBIT_MAINNET_URL;

/// Граница `--minutes` — брифу владельца дословно: «данные набираются
/// короткими сессиями — от пяти до пятнадцати минут по всему пулу
/// одновременно» (история 7, `R38`, `docs/plan/BUSINESS-TASK.md`). Не
/// умолчание и не изобретённое число этого файла — предел приходит из
/// текста задачи, не из кода.
pub const MIN_MINUTES: u64 = 5;
pub const MAX_MINUTES: u64 = 15;

/// Границы `--pilot-minutes` (таск 22): пилот §11 плана — режим вне лимита
/// сессии сбора, поэтому отдельный флаг, а не число вне диапазона
/// `--minutes`. Нижняя граница — просто «дольше, чем сессия сбора»
/// (`MAX_MINUTES + 1`, не второе изобретённое число); верхняя — гейт G0
/// плана («у медианного инструмента < 200 уровней — пилот продлевается до
/// 6 часов один раз», ревизия 17б, `docs/plan/SETTLED.md` В-29). Сама длина
/// пилота — решение владельца по факту (2026-09-12: 30 минут, не два часа
/// первой редакции §11) и в код не зашита, только эти границы.
pub const MIN_PILOT_MINUTES: u32 = MAX_MINUTES as u32 + 1;
pub const MAX_PILOT_MINUTES: u32 = 360;

/// Аргументы `lob session`. Ни у `minutes`/`pilot_minutes`, ни у путей нет
/// правдоподобного умолчания (правило 1 `interfaces.md`: параметр без
/// умолчания лучше изобретённого) — пул и место записи владелец называет
/// каждый раз.
#[derive(Debug, Args)]
pub struct SessionArgs {
    /// `instruments.csv` последнего `lob pick` — колонки `symbol,tick_size,
    /// min_order_qty,qty_step,min_notional_value` (`CLAUDE.md`, «грабли»).
    /// Взаимоисключающий с `--all-instruments`; ровно один обязателен.
    #[arg(long, conflicts_with = "all_instruments")]
    pub pool_instruments: Option<PathBuf>,
    /// Писать **все** linear USDT-перпетуалы биржи, без отбора (таск 28,
    /// R86: «писать не 10 монет а например 500 также эффективно и
    /// экономно»). Список — `instruments-info` тем же путём, что у `lob
    /// pick` (`bybit::rest::fetch_all_linear_instruments`), фильтр — только
    /// «linear USDT-перпетуал в торгах»: исключения §2 (пул анализа) здесь
    /// **не применяются** — это запись, а не отбор. `tick_e9`/`step_e9` —
    /// из того же ответа, не из CSV.
    #[arg(long, conflicts_with = "pool_instruments")]
    pub all_instruments: bool,
    /// Корень сессии: по файлу `<SYMBOL>-<день UTC старта>.binlog` на
    /// инструмент (то же имя, что читают `verify`/`levels`/`markout` —
    /// `commands::record::day_file_path`), `gaps.csv`, `clock.csv`, запись
    /// о сессии.
    #[arg(long)]
    pub root: PathBuf,
    /// Длина сессии сбора. Диапазон `MIN_MINUTES..=MAX_MINUTES` — решение
    /// владельца (история 7), не умолчание этого файла; `resolve_duration`
    /// отклоняет значения вне него до всякой сети. Взаимоисключающий с
    /// `--pilot-minutes`/`--always-on` (`clap conflicts_with`); ровно один
    /// из трёх обязателен — эту половину условия clap не выражает, её
    /// проверяет `resolve_duration`.
    #[arg(long, conflicts_with_all = ["pilot_minutes", "always_on"])]
    pub minutes: Option<u64>,
    /// Длина пилота §11 плана, минуты — режим вне лимита сессии сбора
    /// (`MIN_PILOT_MINUTES..=MAX_PILOT_MINUTES`, doc констант). Пилот
    /// печатает `pilot: <n> мин` в stderr и несёт `session.json.pilot =
    /// true`, `pilot_minutes = <n>` — всё остальное (пул, `gaps.csv`,
    /// `clock.csv`, CPU/RSS, p99 разбора/очереди) как у обычной сессии.
    #[arg(long, conflicts_with_all = ["minutes", "always_on"])]
    pub pilot_minutes: Option<u32>,
    /// Олвейс-он коллектор (таск 25, В-34): без дедлайна, до Ctrl+C;
    /// новые сутки UTC — новая часть файла на инструмент; `session.json` и
    /// `clock.csv` переписываются раз в час (`record::HOURLY_REFRESH_SECS`)
    /// и на остановке. `session.json.always_on = true`. Сам по себе ничего
    /// не ограничивает по времени — правило владельца «не дольше 5 минут до
    /// вердикта экономии» соблюдает оператор (`timeout`/Ctrl+C).
    #[arg(long, conflicts_with_all = ["minutes", "pilot_minutes"])]
    pub always_on: bool,
    /// REST-хост Bybit v5 для одного замера `serverTime` в `clock.csv`.
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// NTP-эталон для того же замера. Публичный пул `pool.ntp.org` — то же
    /// эксплуатационное решение хоста, что уже подразумевает `ASSUMPTION
    /// H12` («часы дисциплинируются NTP»), без выбора конкретного адреса.
    #[arg(long, default_value = "pool.ntp.org:123")]
    pub ntp_addr: String,
}

/// Режим прогона, разрешённый из трёх взаимоисключающих флагов.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionPlan {
    /// `--minutes` (сессия сбора) или `--pilot-minutes` (пилот §11):
    /// дедлайн через `minutes`.
    Timed {
        minutes: u64,
        pilot_minutes: Option<u32>,
    },
    /// `--always-on`: без дедлайна, до `None` от `Feed` (Ctrl+C).
    AlwaysOn,
}

/// Ровно один из `--minutes`/`--pilot-minutes`/`--always-on` — `clap
/// conflicts_with` запрещает любые два разом, но не делает ни один
/// обязательным сам по себе; эта функция закрывает вторую половину условия
/// и проверяет диапазон до сети и диска (тот же приём, что раньше был
/// инлайн-проверкой `--minutes` в начале `run_session`).
pub(super) fn resolve_duration(args: &SessionArgs) -> anyhow::Result<SessionPlan> {
    match (args.minutes, args.pilot_minutes, args.always_on) {
        (Some(minutes), None, false) => {
            if !(MIN_MINUTES..=MAX_MINUTES).contains(&minutes) {
                anyhow::bail!(
                    "--minutes обязан быть в {MIN_MINUTES}..={MAX_MINUTES} (история 7, R38: «от \
                     пяти до пятнадцати минут»): получено {minutes}"
                );
            }
            Ok(SessionPlan::Timed {
                minutes,
                pilot_minutes: None,
            })
        }
        (None, Some(pilot_minutes), false) => {
            if !(MIN_PILOT_MINUTES..=MAX_PILOT_MINUTES).contains(&pilot_minutes) {
                anyhow::bail!(
                    "--pilot-minutes обязан быть в {MIN_PILOT_MINUTES}..={MAX_PILOT_MINUTES} \
                     (PLAN.md §11, гейт G0: «продление до 6 часов один раз»): получено \
                     {pilot_minutes}"
                );
            }
            Ok(SessionPlan::Timed {
                minutes: u64::from(pilot_minutes),
                pilot_minutes: Some(pilot_minutes),
            })
        }
        (None, None, true) => Ok(SessionPlan::AlwaysOn),
        (None, None, false) => anyhow::bail!(
            "нужен ровно один флаг: --minutes <{MIN_MINUTES}..={MAX_MINUTES}> для сессии сбора, \
             --pilot-minutes <{MIN_PILOT_MINUTES}..={MAX_PILOT_MINUTES}> для пилота §11 или \
             --always-on для олвейс-он коллектора (В-34)"
        ),
        _ => anyhow::bail!(
            "флаги режима разошлись с разбором `clap`: --minutes/--pilot-minutes/--always-on \
             взаимоисключающие (V11 аудита 2026-09-17: здесь раньше стоял `unreachable!`, \
             то есть паника вместо ошибки, — инвариант обязан быть проверкой, а не обещанием)"
        ),
    }
}

/// Отладочная сессия — короче часа. Чистая функция от `duration_s`, а не
/// прямая проверка `args.minutes < 60` внутри `run_session`: так у неё есть
/// собственный тест на обе ветки (`< 3600` и `>= 3600`).
pub(super) fn is_debug_session(duration_s: u64) -> bool {
    const HOUR_S: u64 = 3600;
    duration_s < HOUR_S
}
