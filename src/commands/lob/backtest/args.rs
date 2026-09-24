//! Аргументы `lob backtest` (`BacktestArgs`) и итог команды
//! (`BacktestSummary`). Вынесено из `backtest` при разрезке B3 (ревью
//! 23.09), поведение не менялось.

use std::path::PathBuf;

use clap::Args;

use crate::lob::backtest::ExecLatency;

// ---------------------------------------------------------------------------
// `lob backtest` (шаг 6.3, история 32–34).
// ---------------------------------------------------------------------------

/// Аргументы `lob backtest`: вердикт бэктеста с моделью очереди на
/// произвольное число профилей.
#[derive(Debug, Args)]
pub struct BacktestArgs {
    /// Корень сессии: файл `<SYMBOL>-<день>.binlog` (см. `lob session`,
    /// резолвер `super::session_binlog_for`).
    #[arg(long)]
    pub session_root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// CSV сигналов: `profile_id,side,birth_ms` (`side`: `bid`|`ask` —
    /// Decision 14; `birth_ms` — момент срабатывания, тот же смысл, что
    /// колонка `birth_ms` артефакта `lob levels`). Несколько строк одного
    /// `profile_id` — один профиль. Не нужен с `--touches`.
    #[arg(long)]
    pub signals_csv: Option<PathBuf>,
    /// Задержка исполнения, нс: одно число на всё (В-37, `assumed`) или
    /// измеренная тройка `place=<нс>,cancel=<нс>,taker=<нс>` (В-68, `lob
    /// latency`). Без умолчания: изобретённое число запрещено (§9 плана).
    #[arg(long)]
    pub median_rtt_ns: ExecLatency,
    /// 95-й перцентиль той же задержки, та же форма.
    #[arg(long)]
    pub p95_rtt_ns: ExecLatency,
    /// Размер круга в 1e-9 лотов — минимальный лот площадки (Decision 22:
    /// `order_size_22a` из `instruments.csv`, не шаг книги). Без умолчания
    /// нарочно: шаг книги (`step_e9` бинлога) — другая величина, и молчаливо
    /// подставлять его вместо лота площадки было бы неверным умолчанием, а
    /// не измеренным (§9 плана). Взаимоисключающий с `--order-qty-from-pool`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
    /// Считать размер круга `order_size_22a` от полей пула `instruments.csv`
    /// сессии и цены последнего касания реплея (Decision 22а). **Одна цена на
    /// весь прогон** — прежняя семантика одиночного отладочного прогона; у
    /// сетки форм (`lob bounce-grid`, путь вердикта) лот — по цене каждого
    /// касания (R2, 24.09), поэтому при ходе цены за запись числа этих двух
    /// команд расходятся. Нужен там, где
    /// лота символа нет в журнале `candidates.csv`: у замороженного пула на 100
    /// монет (отбор 2026-09-15) таблица кандидатов не коммитилась, а шаг книги —
    /// другая величина. Только с `--touches`: цену даёт реплей касаний.
    #[arg(long, default_value_t = false)]
    pub order_qty_from_pool: bool,
    /// Таблица профилей (таск 10, `docs/findings/profiles-<дата>.csv`) для
    /// сравнения `net_fill`. Без флага сравнение печатает `none`. Строки, где
    /// `fill_model=none` (колонки `fill`/`net_fill` — литерал `not_measured`),
    /// сравниваются по `net_bps` — см. `TableComparison`.
    #[arg(long)]
    pub profiles_csv: Option<PathBuf>,
    /// Куда писать сводку (по умолчанию `docs/findings/backtest-<дата>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Куда писать кривые PnL по обеим RTT (по умолчанию
    /// `docs/findings/backtest-<дата>-pnl.csv`).
    #[arg(long)]
    pub pnl_out: Option<PathBuf>,
    /// Вход — отладочная запись (`data/session-debug/…`, короче боевого
    /// окна): метит оба артефакта строкой `# lob backtest: …` с суффиксом
    /// ` debug`, тем же приёмом, что `lob profiles --allow-unverified`.
    /// Решение вызывающего, не автоопределение — как там же.
    #[arg(long, default_value_t = false)]
    pub debug: bool,
    /// Вместо CSV сигналов — **касания живых уровней** (В-44) из реплея того же
    /// каталога: каждая строка `touches` становится сделкой-отскоком, план
    /// которой строится здесь (бид `P`: вход `P+1` тик, стоп `P−1`, тейк
    /// `P+3`; аск зеркально), а строки CSV — оси В-44. Порог `H3` — тот же
    /// резолвер, что у `lob touches`.
    #[arg(long, default_value_t = false)]
    pub touches: bool,
    /// Вход пост-онли (`GTX`) вместо обычного лимита (`GTC`): замер того,
    /// сколько входов пересекает спред в момент касания (T38).
    #[arg(long, default_value_t = false)]
    pub post_only: bool,
    /// Трейл-тейк: откат от лучшего исхода, bps (0 — выключен, работает
    /// фиксированный тейк 1:1). Решение владельца 2026-09-13.
    #[arg(long, default_value_t = 0.0)]
    pub trail_bps: f64,
    /// Прибыль от входа, после которой трейл включается, bps.
    #[arg(long, default_value_t = 0.0)]
    pub trail_activate_bps: f64,
    /// Вход лестницей: сколько лимитов ставить вместо одного (1 — как было).
    /// Решение владельца 2026-09-13.
    #[arg(long, default_value_t = 1)]
    pub grid_legs: u8,
    /// Шаг лестницы в тиках (0 — все ноги по одной цене).
    #[arg(long, default_value_t = 0)]
    pub grid_step_ticks: i64,
    /// Форма стопа базы (В-65): `before|at|behind|midfr|stack2|pct<x>|s<a>`.
    /// Обязательна при `--touches`. Вход берётся от первого фронтранера
    /// касания, если он был (`TouchRecord::frontrun_tick`), иначе `P+1` тик.
    #[arg(long)]
    pub stop_form: Option<String>,
    /// Форма тейка: `1to1` (от входа) или `t<b>` (`b × σ_H`). Обязательна при `--touches`.
    #[arg(long)]
    pub take_form: Option<String>,
    /// Пол σ-тейка в кругах комиссий (`costs::ROUNDTRIP_FEES_BPS`, В-62/В-63);
    /// нужен только форме `t<b>`.
    #[arg(long)]
    pub take_floor_fees: Option<f64>,
    /// Дедлайн сделки, секунды (B3, В-58 п. 4) — из предрегистрированной
    /// сетки {60, 600, 3600, 7200}: «S и S-D; S-D значит от секунд до, наверно,
    /// пары часов» (ответ владельца 2). Другое значение — отказ: сетка
    /// зафиксирована **до** данных, и «попробовать ещё одно» — это лишнее
    /// испытание, а не параметр (В-58: каждое дополнительно рассмотренное
    /// значение считается отдельным испытанием).
    #[arg(long, default_value_t = 60)]
    pub deadline_secs: i64,
    /// Досрочный выход «по прилипанию» (B4, В-58 п. 5): секунды `X` из
    /// предрегистрированного набора {1, 2, 3} — если через `X` после входа
    /// уровень **всё ещё лучшая цена** (касание не разрешилось ни в отскок, ни
    /// в пробой), выходим по рынку. Отсутствие флага — выход выключен, и это
    /// четвёртый вариант той же оси В-58 («выключен либо X ∈ {1, 2, 3}»).
    /// Другое значение — отказ: сетка зафиксирована **до** данных.
    #[arg(long)]
    pub early_exit_secs: Option<i64>,
    /// Покруговой дамп прогона `--touches` (B5, В-58): одна строка на закрытый
    /// круг профиля «все» — обе ноги, размер, чистая доходность в bps и причина
    /// выхода. Прокруговой ряд нужен прогону-вердикту (`lob bounce-verdict`):
    /// поправка на число испытаний дефлирует Шарп **ряда**, и по средним из
    /// сводки его не посчитать. Без флага файл не пишется — у прогонов B2/B4
    /// артефакт остаётся прежним.
    #[arg(long)]
    pub trades_out: Option<PathBuf>,
    /// Порог `H3` для `--touches` — те же флаги, что у `lob touches`/`levels`.
    #[command(flatten)]
    pub h3: crate::commands::lob::H3Args,
    /// Относительный порог `floor(k × median_trade_lots)` (В-30) для `--touches`.
    #[arg(long)]
    pub h3_k: Option<f64>,
    /// Прогрев разметки, мс (`--touches`; по умолчанию `DEFAULT_WARMUP_MS`).
    #[arg(long)]
    pub warmup_ms: Option<i64>,
    /// Окно `repeat_count`, мс (`--touches`; по умолчанию `DEFAULT_REPEAT_WINDOW_MS`).
    #[arg(long)]
    pub repeat_window_ms: Option<i64>,
    /// Снять требование маркера сверки `verify-<SYMBOL>.status == ok` (отладочные
    /// данные; К1 аудита 18.09).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
}

/// Итог `lob backtest` для печати диспетчером.
pub struct BacktestSummary {
    pub profiles: usize,
    pub pass: usize,
    pub red: usize,
    pub out: PathBuf,
    pub pnl_out: PathBuf,
}
