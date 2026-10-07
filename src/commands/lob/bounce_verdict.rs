//! Вердикт по сетке форм отскока (B5, В-58 → В-62) — читает артефакты
//! `lob bounce-grid` (`rounds.csv`, `forms.csv`) и считает по контракту
//! BUSINESS-TASK §6–7 с поправками аудита 18.09 (K2, K3, K4):
//!
//! - **полная сетка** В-62 — декартово произведение множителей стопа, тейка и
//!   `DEADLINE_SECS` (имя формы `<стоп>-<тейк>-<H>`, В-65), и одинаковое число касаний
//!   (`n_signals + n_skipped`: у формы, которую не для всех касаний построить,
//!   сигналов меньше, касания те же) у всех форм на каждой паре (символ, сутки) — иначе отказ,
//!   не «вердикт по части»;
//! - по форме — все наблюдения сигналов (`FillObservation`: круг с `net_bps`
//!   или промах с нулём), **нижняя граница интервала `net_fill`** — тот же
//!   `costs::net_fill_interval` (wild cluster bootstrap-t, кластер — сутки,
//!   Decision 9), что у `lob backtest`; кластер суток общий для всех символов
//!   (режим дня один на рынок — консервативно);
//! - `DSR` лучшей формы по **суточному** ряду (T-26: наблюдение — сутки сетки,
//!   `r_d` — сумма `net` её кругов за сутки, сутки без кругов — 0), испытания —
//!   суточные Шарпы форм сетки, `V = max(V_изм, 1/T)`; печатается при `N` = форм
//!   сетки и при `N` журнала `runs.csv` (контекст перебора). **Отчёт, не ворота**
//!   (Судья `11a3d1e`: правильное `N` задаёт протокол, решение — по его правилу);
//! - **PBO и CPCV** по матрице «форма × сутки» (ячейка — `net_fill` формы за
//!   сутки: сумма `net` кругов на число сигналов), теми же оценщиками и
//!   параметрами, что у `lob shortlist` (`REPORT_PBO_PARTITIONS`,
//!   `REPORT_CPCV_PARAMS`, правило отбора «лучший по среднему на IS-сутках»);
//! - **порог данных**: у лучшей формы ≥ `MIN_FILL_DAYS_PER_MONTH` суток с кругами
//!   в каждом месяце сетки и `G ≥ G_MIN` кластеров с кругами — иначе итог «мало
//!   данных», а не вердикт; при пороге — «красный» (нижняя граница ≤ 0),
//!   «зелёный без ёмкости» (нижняя граница > 0, точка < `GREEN_NET_BPS`), «зелёный».
//!
//! Деления на разведочную/подтверждающую выборки нет (решение владельца
//! 2026-09-17, В-58) — это записано в шапке артефакта: вердикт означает «на
//! всех записанных сутках», а не «подтверждено на отложенных».
//!
//! Лучшая форма выбирается по **точке** `net_fill` (правило отбора то же, что
//! у CPCV: среднее), вердикт — по её интервалу; так отбор и проверка разведены.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write as _;
use std::path::{Path, PathBuf};

use anyhow::Context as _;
use clap::Args;

use crate::lob::costs::{net_fill_interval, FillObservation, NetFillInterval, GREEN_NET_BPS};
use crate::lob::final_metrics::{
    self, CpcvParams, DSR_TARGET, REPORT_CPCV_PARAMS, REPORT_PBO_PARTITIONS,
};
use crate::lob::runs::{self, BOUNCE_TRIAL_PREFIX};
use crate::stats::{BOOTSTRAP_REPLICATIONS, GATE_ALPHA, G_MIN};

use super::backtest::{EntryForm, EntryTtl, StopForm, TakeForm, SINGLE_ENTRY_LABEL};
use super::shortlist::{select_best_mean_net, CPCV_SELECTION_RULE};

/// Порог данных вердикта: суток с кругами у лучшей формы в **каждом** месяце
/// сетки (месяц — `YYYY-MM` суток). Аудит Судьи 27.09 п. 11
/// (`docs/research/reviews/rules-audit-2026-09-27.md`: «пол — не число
/// сделок», навык 07 §4) и условие «а» проверки плана T-26 (Судья `11a3d1e`,
/// `docs/research/reviews/t26-dsr-plan-2026-09-27.md`); заменил прежний
/// `n ≥ CONFIRM_MIN_N` кругов.
pub const MIN_FILL_DAYS_PER_MONTH: usize = 10;

/// Дедлайны сетки В-58, секунды (В-62: они же — окна `σ_H` в `touches`).
pub const DEADLINE_SECS: [u64; 4] = [60, 600, 3600, 7200];
/// Все разрешённые дедлайны: В-58 плюс 30 мин, 4 ч (S8 плана по сторонам,
/// предрегистрация 20.09: у лонгов 60 с / 10 мин на тех же сигналах −$103 /
/// −$65, час +$120, два часа +$111 — ось «удержание» нужно дотянуть). Сетка
/// задаёт свой набор `--deadline-secs` из этого списка; чужое число — отказ.
pub const DEADLINE_SECS_ALLOWED: [u64; 8] = [60, 600, 1800, 3600, 7200, 14400, 21600, 28800];
/// Причины выхода в порядке колонок артефакта.
pub const EXIT_REASONS: [&str; 9] = [
    "stop",
    "take",
    "trail",
    "deadline",
    "early",
    "horizon",
    "eaten",
    "eaten_by_trades",
    "wall_gone",
];

/// Имя колонки артефакта вердикта для причины выхода: у `deadline` она
/// исторически `timeout` (`n_timeout`/`share_timeout`), у остальных — само
/// имя причины. Одна функция на обе оси колонок (`n_*` и `share_*`), чтобы
/// новая причина не попала в чужую колонку молчаливым `_ =>` (умолчание
/// прежнего кода отдавало `n_horizon` всякому неизвестному имени).
fn exit_column(reason: &str) -> &'static str {
    match reason {
        "deadline" => "timeout",
        "eaten" => "eaten",
        "eaten_by_trades" => "eaten_by_trades",
        "wall_gone" => "wall_gone",
        // Прежние имена причин — как в самих формах (stop/take/trail/early/horizon).
        "stop" => "stop",
        "take" => "take",
        "trail" => "trail",
        "early" => "early",
        _ => "horizon",
    }
}

/// Размер полной сетки по её же именам (В-62/В-65/В-74/F6): формы стопа ×
/// формы тейка × дедлайны × формы входа × режимы срока жизни входа × формы
/// выхода, встреченные в именах (все из `DEADLINE_SECS_ALLOWED`). Сетка задана
/// именами владельца, не константой, и её
/// полнота проверяется как декартово произведение осей, встреченных в
/// именах форм: набор `{before-1to1-60, at-1to1-60}` без `before-1to1-600` —
/// не сетка. Оси названы только у форм F6/F5: у трёхпольных имён вход —
/// `single@fr`, срока жизни нет (`touch`), выхода нет (`none`) — прежде числа
/// и имена не менялись.
pub fn grid_size_from_labels<'a>(
    labels: impl IntoIterator<Item = &'a str>,
) -> anyhow::Result<usize> {
    let mut entries: BTreeSet<String> = BTreeSet::new();
    let mut stops: BTreeSet<String> = BTreeSet::new();
    let mut takes: BTreeSet<String> = BTreeSet::new();
    let mut deadlines: BTreeSet<u64> = BTreeSet::new();
    let mut ttls: BTreeSet<String> = BTreeSet::new();
    let mut exits: BTreeSet<String> = BTreeSet::new();
    let mut earlies: BTreeSet<String> = BTreeSet::new();
    for label in labels {
        let parts = parse_form_fields(label)?;
        entries.insert(parts.entry);
        stops.insert(parts.stop);
        takes.insert(parts.take);
        deadlines.insert(parts.deadline_secs);
        ttls.insert(parts.ttl.unwrap_or_else(|| TTL_NONE.to_string()));
        exits.insert(parts.exit.unwrap_or_else(|| EXIT_NONE.to_string()));
        earlies.insert(parts.early.unwrap_or_else(|| EARLY_NONE.to_string()));
    }
    Ok(entries.len()
        * stops.len()
        * takes.len()
        * deadlines.len()
        * ttls.len()
        * exits.len()
        * earlies.len())
}

/// Метка оси «срока жизни входа нет» (прежний режим `touch`, F5, В-74).
const TTL_NONE: &str = "touch";
/// Метка оси «выход прежний» (нет ключа `--exit-form`, F7/F8).
const EXIT_NONE: &str = "none";
/// Метка оси «выхода по прилипанию нет» (нет ключа `--early-exit-secs`, В-85).
const EARLY_NONE: &str = "off";

/// Имя формы: `<стоп>-<тейк>-<H>` — имена `StopForm::label`/`TakeForm::label`
/// (`before`, `pct1`, `s1` …; `1to1`, `t2`) и дедлайн в секундах из `DEADLINE_SECS`.
/// Прежняя (трёхпольная) запись: вход у неё неявный — `single@fr`.
pub fn form_label(stop: &str, take: &str, deadline_secs: u64) -> String {
    format!("{stop}-{take}-{deadline_secs}")
}

/// Имя формы с входом (F6, В-73): `<вход>-<стоп>-<тейк>-<H>`; у прежнего
/// входа (`single@fr`) имя остаётся трёхпольным — на этом стоит гейт «те же
/// круги» (прежние `rounds.csv`/`forms.csv` байт в байт). Суффиксы F5/F7
/// (`-ttl<значение>`, `-<выход>`) дописывает вызывающий: вход стоит первым, а
/// `parse_form_fields` читает хвостовые поля по префиксам.
pub fn form_label_with_entry(entry: &str, stop: &str, take: &str, deadline_secs: u64) -> String {
    if entry == SINGLE_ENTRY_LABEL {
        form_label(stop, take, deadline_secs)
    } else {
        format!("{entry}-{stop}-{take}-{deadline_secs}")
    }
}

/// Разобранные поля имени формы (F6): вход, стоп, тейк, дедлайн и
/// необязательные срок жизни входа (F5) и выход (F7). Трёхпольные имена
/// читаются как `single@fr-…` — обратная совместимость вердикта.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormFields {
    pub entry: String,
    pub stop: String,
    pub take: String,
    pub deadline_secs: u64,
    /// Значение `-ttl<…>`: `touch` | `wall` | секунды (F5, В-74); `None` —
    /// поля в имени нет.
    pub ttl: Option<String>,
    /// Значение поля выхода (F7/F8, например `eat50`); `None` — поля нет.
    pub exit: Option<String>,
    /// Секунды выхода по «прилипанию» (`-early<x>`, В-85); `None` — поля нет.
    pub early: Option<String>,
}

#[derive(Debug, Args)]
pub struct BounceVerdictArgs {
    /// Каталог `lob bounce-grid` с `rounds.csv` и `forms.csv`.
    #[arg(long)]
    pub grid_dir: PathBuf,
    /// Журнал испытаний (`docs/plan/runs.csv`).
    #[arg(long)]
    pub runs_csv: PathBuf,
    /// Артефакт вердикта (CSV с шапкой `#`).
    #[arg(long)]
    pub out: PathBuf,
    /// Дописать строки испытаний `bounce_form:<форма>` в журнал перед вердиктом.
    #[arg(long, default_value_t = false)]
    pub log_trials: bool,
}

/// Итог по одной форме.
#[derive(Debug, Clone, PartialEq)]
pub struct FormVerdict {
    pub form: String,
    /// Имена форм стопа и тейка (В-65).
    pub stop: String,
    pub take: String,
    pub deadline_secs: u64,
    /// Сигналов (касаний) всего по всем символам и суткам.
    pub n_signals: u64,
    /// Кругов (исполненных сделок).
    pub n_fills: u64,
    /// Суток, в которых у формы был хотя бы один круг.
    pub days_with_fills: usize,
    /// Единица кластера интервала (В-60): `day` при ≥ `G_MIN` сутках в сетке,
    /// иначе `hour` — вердикт возможен по одним суткам.
    pub cluster_unit: &'static str,
    /// Кластеров (суток или часов) с хотя бы одним кругом.
    pub clusters_with_fills: usize,
    /// Точка и нижняя граница интервала `net_fill` (bps на сигнал).
    pub interval: Option<NetFillInterval>,
    /// Среднее `net` по кругам (bps на круг) и Шарп ряда кругов.
    pub net_per_fill_bps: Option<f64>,
    pub sharpe: Option<f64>,
    pub exits: [u64; EXIT_REASONS.len()],
    /// Ряд `net` кругов.
    pub returns: Vec<f64>,
    /// Суточный ряд — вход DSR (T-26): на каждые сутки сетки сумма `net`
    /// кругов формы по всем символам, сутки без кругов — `0`.
    pub daily: Vec<f64>,
    /// Шарп суточного ряда (среднее / σ), `None` — ряд без дисперсии.
    pub daily_sharpe: Option<f64>,
    /// Суток с кругами по месяцам сетки (`YYYY-MM`, все месяцы сетки — и
    /// те, где кругов нет).
    pub month_fill_days: Vec<(String, usize)>,
}

impl FormVerdict {
    pub fn exits_of(&self, reason: &str) -> u64 {
        EXIT_REASONS
            .iter()
            .position(|r| *r == reason)
            .map(|i| self.exits[i])
            .unwrap_or(0)
    }

    pub fn share_of(&self, reason: &str) -> Option<f64> {
        if self.n_fills == 0 {
            return None;
        }
        Some(self.exits_of(reason) as f64 / crate::stats::count_f64_u64(self.n_fills))
    }
}

/// Итог §7 для лучшей формы.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// Порог данных (`MIN_FILL_DAYS_PER_MONTH` в каждом месяце, `G ≥ G_MIN`)
    /// не пройден — вердикта нет.
    NotEnoughData,
    /// Нижняя граница интервала ≤ 0.
    Red,
    /// Нижняя граница > 0, но точка < `GREEN_NET_BPS`.
    GreenNoCapacity,
    /// Нижняя граница > 0, точка ≥ `GREEN_NET_BPS`.
    Green,
}

impl Verdict {
    pub fn label(self) -> &'static str {
        match self {
            Self::NotEnoughData => "мало данных",
            Self::Red => "красный",
            Self::GreenNoCapacity => "зелёный без ёмкости",
            Self::Green => "зелёный",
        }
    }
}

#[derive(Debug, Clone)]
pub struct BounceVerdictSummary {
    pub forms: usize,
    pub symbols: usize,
    pub days: usize,
    pub trials: usize,
    pub journal_trials: usize,
    pub best_form: String,
    pub best_n_fills: u64,
    pub best_days: usize,
    pub best_point_bps: Option<f64>,
    pub best_lower_bps: Option<f64>,
    /// Суток с кругами у лучшей формы по месяцам сетки.
    pub best_month_fill_days: Vec<(String, usize)>,
    /// DSR суточного ряда лучшей формы при `N` = форм сетки и при `N` журнала
    /// (отчёт, не ворота — T-26); `V` — `v_used`.
    pub dsr: Option<f64>,
    pub dsr_at_journal_trials: Option<f64>,
    /// Требуемый дневной Шарп для `DSR_TARGET` при тех же `N` и `V`.
    pub required_sharpe: Option<f64>,
    pub required_sharpe_at_journal_trials: Option<f64>,
    /// Измеренная дисперсия суточных Шарпов форм (`None` — меньше двух форм).
    pub v_measured: Option<f64>,
    /// `max(V_изм, 1/T)`, `T` — суток сетки (условие 1 Судьи `11a3d1e`).
    pub v_used: f64,
    pub pbo: Option<f64>,
    pub cpcv: Option<f64>,
    pub verdict: Verdict,
    pub out: PathBuf,
}

/// Разбор имени формы `<стоп>-<тейк>-<H>` (В-65) — прежний вид без входа:
/// формы — через `StopForm::parse`/`TakeForm::parse` (они же проверяют
/// каноничность), дедлайн — из `DEADLINE_SECS`. Имена F5/F6 с суффиксом
/// (`-ttl<…>`) и с полем входа читаются `parse_form_fields`; здесь остаётся
/// тройка вердикта — стоп, тейк, дедлайн — какой её читали вызывающие до F6.
pub fn parse_form(label: &str) -> anyhow::Result<(String, String, u64)> {
    let parts = parse_form_fields(label)?;
    Ok((parts.stop, parts.take, parts.deadline_secs))
}

/// Разбор имени формы целиком (F6, В-73 + F5, В-74): `<вход>-<стоп>-<тейк>-<H>`
/// с необязательными хвостовыми полями `-ttl<…>` (срок жизни входа; `wall`,
/// `touch` или секунды) и `-<выход>` (F7/F8), в любом порядке. Прежние
/// трёхпольные имена (`<стоп>-<тейк>-<H>`) читаются как `single@fr-…` —
/// обратная совместимость вердикта; вход проверяется `EntryForm::parse` (он же
/// канонизирует имя), стоп/тейк — своими `parse`, дедлайн — из
/// `DEADLINE_SECS_ALLOWED`.
pub fn parse_form_fields(label: &str) -> anyhow::Result<FormFields> {
    let parts: Vec<&str> = label.split('-').collect();
    anyhow::ensure!(
        parts.len() >= 3 && parts.len() <= 7,
        "{label}: имя формы — <вход>-<стоп>-<тейк>-<дедлайн с>[-ttl<режим>][-<выход>] (`pct2-1to1-3600`, `ladder3x2..10-pct2-1to1-3600-ttl60`)"
    );
    // Вход: четвёртый сегмент — вход только тогда, когда разбирается как
    // `EntryForm` (трёхпольное имя — `single@fr`).
    let (entry, rest) = if parts.len() >= 4 && EntryForm::parse(parts[0]).is_ok() {
        (parts[0].to_string(), &parts[1..])
    } else {
        (SINGLE_ENTRY_LABEL.to_string(), &parts[..])
    };
    anyhow::ensure!(
        rest.len() >= 3,
        "{label}: имя формы — <вход>-<стоп>-<тейк>-<дедлайн с>[-ttl<режим>][-<выход>]"
    );
    let stop = StopForm::parse(rest[0]).map_err(|e| anyhow::anyhow!("{label}: {e}"))?;
    let take = TakeForm::parse(rest[1]).map_err(|e| anyhow::anyhow!("{label}: {e}"))?;
    let deadline_secs: u64 = rest[2]
        .parse()
        .map_err(|_| anyhow::anyhow!("{label}: дедлайн {} не число секунд", rest[2]))?;
    anyhow::ensure!(
        DEADLINE_SECS_ALLOWED.contains(&deadline_secs),
        "{label}: дедлайн {deadline_secs} с вне разрешённых {DEADLINE_SECS_ALLOWED:?}"
    );
    // Хвостовые поля: `ttl<…>` — срок жизни входа (F5, В-74), остальное —
    // поле выхода (F7/F8). Каждое не больше одного раза.
    let mut ttl: Option<String> = None;
    let mut exit: Option<String> = None;
    let mut early: Option<String> = None;
    for tail in &rest[3..] {
        if let Some(v) = tail.strip_prefix("early") {
            anyhow::ensure!(
                early.is_none() && !v.is_empty(),
                "{label}: поле выхода по прилипанию — одно (`-early<секунды>`)"
            );
            let x: i64 = v
                .parse()
                .map_err(|_| anyhow::anyhow!("{label}: -early{v}: не целые секунды"))?;
            crate::commands::lob::backtest::early_exit_ns_from_secs(Some(x))
                .map_err(|e| anyhow::anyhow!("{label}: {e}"))?;
            early = Some(v.to_string());
        } else if let Some(v) = tail.strip_prefix("ttl") {
            anyhow::ensure!(
                ttl.is_none() && !v.is_empty(),
                "{label}: поле срока жизни входа — одно (`-ttl<touch|wall|секунды>`)"
            );
            parse_form_ttl(label, v)?;
            ttl = Some(v.to_string());
        } else {
            anyhow::ensure!(
                exit.is_none() && !tail.is_empty(),
                "{label}: поле выхода — одно (F7/F8)"
            );
            parse_form_exit(label, tail)?;
            exit = Some((*tail).to_string());
        }
    }
    Ok(FormFields {
        entry,
        stop: stop.label(),
        take: take.label(),
        deadline_secs,
        ttl,
        exit,
        early,
    })
}

/// Значение `-ttl<…>` имени формы: `touch` | `wall` | секунды из сетки замера
/// В-74. Проверка та же, что у `--entry-ttl-secs` (`EntryTtl::parse`), иначе
/// имя формы описывало бы режим, которого сетка не знает.
fn parse_form_ttl(label: &str, value: &str) -> anyhow::Result<()> {
    let mode = match value {
        // У прежнего режима суффикса нет: `-ttltouch` — не имя, а другая
        // запись того же (как и `w1` у формы входа).
        "touch" => anyhow::bail!("{label}: прежний режим входа суффикса не несёт"),
        _ => value,
    };
    EntryTtl::parse(mode)
        .map(|_| ())
        .map_err(|e| anyhow::anyhow!("{label}: {e}"))
}

/// Поле выхода имени формы (F7/F8): `none` — как раньше, `eat<X>` — выход по
/// съеданию стены, `gone<W>` — по снятию. Проверяются имена, которые сетка
/// пишет сама; произвольный хвост (`s1-t1-60-5`) — не имя. Список форм выхода
/// ведут F7/F8 — здесь читается то, что названо в плане 2026-09-20 §3 F7.
fn parse_form_exit(label: &str, value: &str) -> anyhow::Result<()> {
    if value == "none" {
        return Ok(());
    }
    let (prefix, number) = if let Some(rest) = value.strip_prefix("eat") {
        ("eat", rest)
    } else if let Some(rest) = value.strip_prefix("gone") {
        ("gone", rest)
    } else {
        anyhow::bail!(
            "{label}: выход {value:?} не из форм (`none`, `eat<X>` — съели, `gone<W>` — сняли)"
        );
    };
    let pct: f64 = number
        .parse()
        .map_err(|_| anyhow::anyhow!("{label}: {prefix}{number}: порог не число"))?;
    anyhow::ensure!(
        pct.is_finite() && pct > 0.0 && pct <= 100.0,
        "{label}: {prefix}{number}: порог — конечное число в (0, 100] %"
    );
    Ok(())
}

/// Испытания **этой** процедуры в журнале — строки с префиксом `bounce_form`.
pub fn count_scoped_trials(rows: &[runs::RunRow], prefix: &str) -> usize {
    rows.iter()
        .filter(|r| r.kind.counts_as_trial() && r.detail.starts_with(prefix))
        .count()
}

/// Ключ строки `forms.csv`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct Cell {
    symbol: String,
    day: String,
    form: String,
}

#[derive(Debug, Clone, Default)]
struct CellStat {
    n_signals: u64,
    /// Касаний, для которых форма не построилась (нет σ, фронтрана, второй
    /// плотности; В-65): `n_signals + n_skipped` — общее число касаний пары,
    /// одно у всех форм.
    n_skipped: u64,
    n_fills: u64,
    sum_net_bps: f64,
    exits: [u64; EXIT_REASONS.len()],
    /// Сигналы по часам UTC (`signals_by_hour` в `forms.csv`, В-60); `None` —
    /// столбца нет (дамп до 18.09) — вердикт по часам тогда невозможен.
    signals_by_hour: Option<[u64; 24]>,
}

struct GridData {
    forms: Vec<String>,
    symbols: BTreeSet<String>,
    days: Vec<String>,
    cells: BTreeMap<Cell, CellStat>,
    /// `(net_bps, t0_ns)` кругов по (форма, сутки).
    fills: BTreeMap<(String, String), Vec<(f64, i64)>>,
}

fn read_grid(dir: &Path) -> anyhow::Result<GridData> {
    let forms_path = dir.join("forms.csv");
    let rounds_path = dir.join("rounds.csv");
    let forms_text = std::fs::read_to_string(&forms_path)
        .with_context(|| format!("{}: forms.csv не читается", forms_path.display()))?;
    // T-31 (условие Судьи 2): прогон `--busy-skip off` — все сигналы без правила «занято» движка, его
    // `forms.csv`/`rounds.csv` — не сетка для вердикта (счёт — только после `busy-replay.py`).
    anyhow::ensure!(
        !forms_text
            .lines()
            .take_while(|l| l.starts_with('#'))
            .any(|l| l.contains(" busy_skip=off")),
        "{}: прогон `--busy-skip off` — не сетка для вердикта (T-31: сначала busy-replay.py)",
        dir.display()
    );
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(forms_text.as_bytes());
    let header: Vec<String> = r.headers()?.iter().map(str::to_string).collect();
    let idx = |name: &str| -> anyhow::Result<usize> {
        header
            .iter()
            .position(|h| h == name)
            .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {name}", forms_path.display()))
    };
    let (i_sym, i_day, i_form, i_sig, i_skipped, i_fills, i_sum) = (
        idx("symbol")?,
        idx("day_utc")?,
        idx("form")?,
        idx("n_signals")?,
        idx("n_skipped")?,
        idx("n_fills")?,
        idx("sum_net_bps")?,
    );
    let i_hours = header.iter().position(|h| h == "signals_by_hour");
    // Колонки причин выхода читаются **по наличию**: артефакты до F7/F8 их не
    // несут (`n_eaten_by_trades`/`n_wall_gone`), а вердикт обязан читаться по
    // уже снятой сетке — отсутствующая колонка это ноль, не отказ.
    let exit_idx: Vec<Option<usize>> = EXIT_REASONS
        .iter()
        .map(|r| header.iter().position(|h| h == &format!("n_{r}")))
        .collect();
    let mut cells: BTreeMap<Cell, CellStat> = BTreeMap::new();
    let mut form_set: BTreeSet<String> = BTreeSet::new();
    let mut symbols = BTreeSet::new();
    let mut days_set = BTreeSet::new();
    for rec in r.records() {
        let rec = rec?;
        let cell = Cell {
            symbol: rec[i_sym].to_string(),
            day: rec[i_day].to_string(),
            form: rec[i_form].to_string(),
        };
        parse_form(&cell.form)?;
        let signals_by_hour = match i_hours {
            Some(i) => {
                let parts: Vec<&str> = rec[i].split(':').collect();
                anyhow::ensure!(
                    parts.len() == 24,
                    "{}: signals_by_hour у {} {} {} — {} чисел, нужно 24",
                    forms_path.display(),
                    cell.symbol,
                    cell.day,
                    cell.form,
                    parts.len()
                );
                let mut h = [0u64; 24];
                for (k, v) in parts.iter().enumerate() {
                    h[k] = v.parse()?;
                }
                Some(h)
            }
            None => None,
        };
        let mut st = CellStat {
            n_signals: rec[i_sig].parse()?,
            n_skipped: rec[i_skipped].parse()?,
            n_fills: rec[i_fills].parse()?,
            sum_net_bps: rec[i_sum].parse()?,
            exits: [0; EXIT_REASONS.len()],
            signals_by_hour,
        };
        if let Some(h) = st.signals_by_hour {
            anyhow::ensure!(
                h.iter().sum::<u64>() == st.n_signals,
                "{}: signals_by_hour у {} {} {} не сходится с числом сигналов n_signals",
                forms_path.display(),
                cell.symbol,
                cell.day,
                cell.form
            );
        }
        for (k, &i) in exit_idx.iter().enumerate() {
            if let Some(i) = i {
                st.exits[k] = rec[i].parse()?;
            }
        }
        form_set.insert(cell.form.clone());
        symbols.insert(cell.symbol.clone());
        days_set.insert(cell.day.clone());
        anyhow::ensure!(
            cells.insert(cell.clone(), st).is_none(),
            "{}: строка {} {} {} повторяется",
            forms_path.display(),
            cell.symbol,
            cell.day,
            cell.form
        );
    }
    let grid_size = grid_size_from_labels(form_set.iter().map(String::as_str))?;
    anyhow::ensure!(
        form_set.len() == grid_size,
        "{}: форм {}, полная сетка В-62 по встреченным осям — {}; частичный прогон вердиктом не считается",
        forms_path.display(),
        form_set.len(),
        grid_size
    );
    // Полнота: у каждой пары (символ, сутки) все формы и одно число касаний
    // (`n_signals + n_no_sigma`: касания общие, сигналов у длинного окна `σ`
    // меньше — В-62).
    let mut by_pair: BTreeMap<(String, String), Vec<(&String, u64)>> = BTreeMap::new();
    for (c, st) in &cells {
        by_pair
            .entry((c.symbol.clone(), c.day.clone()))
            .or_default()
            .push((&c.form, st.n_signals.saturating_add(st.n_skipped)));
    }
    for ((sym, day), v) in &by_pair {
        anyhow::ensure!(
            v.len() == grid_size,
            "{sym} {day}: форм {}, нужно {} — сетка неполная",
            v.len(),
            grid_size
        );
        let sigs: BTreeSet<u64> = v.iter().map(|(_, n)| *n).collect();
        anyhow::ensure!(
            sigs.len() == 1,
            "{sym} {day}: число сигналов вместе с пропущенными касаниями расходится между формами {sigs:?} — касания должны быть общими"
        );
    }

    let rounds_text = std::fs::read_to_string(&rounds_path)
        .with_context(|| format!("{}: rounds.csv не читается", rounds_path.display()))?;
    let mut r = csv::ReaderBuilder::new()
        .comment(Some(b'#'))
        .from_reader(rounds_text.as_bytes());
    let header: Vec<String> = r.headers()?.iter().map(str::to_string).collect();
    let idx = |name: &str| -> anyhow::Result<usize> {
        header
            .iter()
            .position(|h| h == name)
            .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {name}", rounds_path.display()))
    };
    let (i_sym, i_day, i_form, i_net, i_t0) = (
        idx("symbol")?,
        idx("day_utc")?,
        idx("form")?,
        idx("net_bps")?,
        idx("t0_ns")?,
    );
    let mut fills: BTreeMap<(String, String), Vec<(f64, i64)>> = BTreeMap::new();
    let mut fills_per_cell: BTreeMap<Cell, u64> = BTreeMap::new();
    for rec in r.records() {
        let rec = rec?;
        let net: f64 = rec[i_net].parse().map_err(|_| {
            anyhow::anyhow!(
                "{}: круг {} {} {} без измеренного net ({}) — вердикт по неизмеренным кругам не считается",
                rounds_path.display(),
                &rec[i_sym],
                &rec[i_day],
                &rec[i_form],
                &rec[i_net]
            )
        })?;
        anyhow::ensure!(net.is_finite(), "{}: net не число", rounds_path.display());
        let cell = Cell {
            symbol: rec[i_sym].to_string(),
            day: rec[i_day].to_string(),
            form: rec[i_form].to_string(),
        };
        anyhow::ensure!(
            cells.contains_key(&cell),
            "{}: круг {} {} {} без строки в forms.csv",
            rounds_path.display(),
            cell.symbol,
            cell.day,
            cell.form
        );
        let t0_ns: i64 = rec[i_t0]
            .parse()
            .map_err(|_| anyhow::anyhow!("{}: t0_ns не число", rounds_path.display()))?;
        *fills_per_cell.entry(cell.clone()).or_default() += 1;
        fills
            .entry((cell.form, cell.day))
            .or_default()
            .push((net, t0_ns));
    }
    for (c, st) in &cells {
        let got = fills_per_cell.get(c).copied().unwrap_or(0);
        anyhow::ensure!(
            got == st.n_fills,
            "{} {} {}: кругов в rounds.csv {got}, в forms.csv {} — артефакты рассогласованы",
            c.symbol,
            c.day,
            c.form,
            st.n_fills
        );
    }
    Ok(GridData {
        forms: form_set.into_iter().collect(),
        symbols,
        days: days_set.into_iter().collect(),
        cells,
        fills,
    })
}

fn day_index(days: &[String], day: &str) -> i64 {
    days.iter()
        .position(|d| d == day)
        .map(|i| i as i64)
        .unwrap_or(-1)
}

fn form_verdict(data: &GridData, form: &str) -> anyhow::Result<FormVerdict> {
    let (stop, take, deadline_secs) = parse_form(form)?;
    let mut n_signals: u64 = 0;
    let mut n_fills: u64 = 0;
    let mut exits = [0u64; EXIT_REASONS.len()];
    let mut observations: Vec<FillObservation> = Vec::new();
    let mut returns: Vec<f64> = Vec::new();
    let mut days_with_fills: BTreeSet<&str> = BTreeSet::new();
    let mut clusters_with_fills: BTreeSet<i64> = BTreeSet::new();
    // В-60 (владелец 2026-09-18: «одного дня минимум хватает»): кластер —
    // сутки, когда суток в сетке не меньше `G_MIN`, иначе час UTC: у одних
    // суток 24 кластера, и бутстрэп Уэбба (`G_MIN` — его порог) работает.
    let by_hour = data.days.len() < G_MIN;
    let cluster_unit = if by_hour { "hour" } else { "day" };
    let hour_of =
        |t0_ns: i64| -> i64 { t0_ns.div_euclid(1_000_000_000).rem_euclid(86_400) / 3_600 };
    // Сигналы по часам суммируются **по суткам через все символы**: круги в
    // `data.fills` лежат по (форма, сутки) без символа, и сравнивать их с
    // сигналами одной ячейки нельзя (до 19.09 так и делалось — ложный отказ
    // «кругов больше сигналов» на первом же прогоне с двумя символами и
    // фильтром касаний; синтетика проходила случайно).
    let mut sig_hours_by_day: BTreeMap<&str, [u64; 24]> = BTreeMap::new();
    for (c, st) in data.cells.iter().filter(|(c, _)| c.form == form) {
        n_signals = n_signals.saturating_add(st.n_signals);
        n_fills = n_fills.saturating_add(st.n_fills);
        for (k, e) in st.exits.iter().enumerate() {
            exits[k] = exits[k].saturating_add(*e);
        }
        let day = day_index(&data.days, &c.day);
        if by_hour {
            let Some(sig_by_hour) = st.signals_by_hour else {
                anyhow::bail!(
                    "{} {} {}: forms.csv без signals_by_hour — вердикт по часам (суток меньше {G_MIN}) \
                     невозможен; дамп сделан сеткой до 18.09, пересчитать `lob bounce-grid`",
                    c.symbol,
                    c.day,
                    c.form
                );
            };
            let acc = sig_hours_by_day.entry(c.day.as_str()).or_insert([0; 24]);
            for h in 0..24 {
                acc[h] = acc[h].saturating_add(sig_by_hour[h]);
            }
            if st.n_fills > 0 {
                days_with_fills.insert(c.day.as_str());
            }
            continue;
        }
        let cluster = day;
        let nets = data
            .fills
            .get(&(c.form.clone(), c.day.clone()))
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        // Круги этой ячейки — первые `st.n_fills` ещё не отданных кругов пары
        // (форма, сутки): порядок внутри суток вердикту не важен, интервал
        // считается по суммам суток.
        if st.n_fills > 0 {
            days_with_fills.insert(c.day.as_str());
        }
        for _ in 0..st.n_signals.saturating_sub(st.n_fills) {
            observations.push(FillObservation {
                day_cluster: cluster,
                net_bps: 0.0,
                filled: false,
            });
        }
        let _ = nets;
    }
    if by_hour {
        for (day_label, sig_by_hour) in &sig_hours_by_day {
            let day = day_index(&data.days, day_label);
            let day_fills = data
                .fills
                .get(&(form.to_string(), (*day_label).to_string()))
                .map(Vec::as_slice)
                .unwrap_or(&[]);
            let mut fills_by_hour = [0u64; 24];
            for &(_, t0) in day_fills {
                fills_by_hour[hour_of(t0) as usize] += 1;
            }
            for h in 0..24 {
                anyhow::ensure!(
                    fills_by_hour[h] <= sig_by_hour[h],
                    "{form} {day_label}: в часе {h} кругов {} больше сигналов {} (сумма по символам)",
                    fills_by_hour[h],
                    sig_by_hour[h]
                );
                let cluster = day * 24 + h as i64;
                for _ in 0..(sig_by_hour[h] - fills_by_hour[h]) {
                    observations.push(FillObservation {
                        day_cluster: cluster,
                        net_bps: 0.0,
                        filled: false,
                    });
                }
            }
        }
    }
    for ((f, day), nets) in data.fills.iter().filter(|((f, _), _)| f == form) {
        let d = day_index(&data.days, day);
        for &(net, t0) in nets {
            let cluster = if by_hour { d * 24 + hour_of(t0) } else { d };
            clusters_with_fills.insert(cluster);
            observations.push(FillObservation {
                day_cluster: cluster,
                net_bps: net,
                filled: true,
            });
            returns.push(net);
        }
        let _ = f;
    }
    let interval = if n_fills == 0 {
        None
    } else {
        net_fill_interval(&observations, GATE_ALPHA, BOOTSTRAP_REPLICATIONS, 0)
    };
    let net_per_fill_bps = if returns.is_empty() {
        None
    } else {
        Some(returns.iter().sum::<f64>() / crate::stats::count_f64(returns.len()))
    };
    let daily: Vec<f64> = data
        .days
        .iter()
        .map(|day| {
            data.fills
                .get(&(form.to_string(), day.clone()))
                .map_or(0.0, |nets| nets.iter().map(|(net, _)| net).sum())
        })
        .collect();
    let mut month_fill_days: BTreeMap<&str, usize> =
        data.days.iter().map(|d| (month_of(d), 0)).collect();
    for day in &days_with_fills {
        *month_fill_days.entry(month_of(day)).or_insert(0) += 1;
    }
    Ok(FormVerdict {
        form: form.to_string(),
        stop,
        take,
        deadline_secs,
        n_signals,
        n_fills,
        days_with_fills: days_with_fills.len(),
        cluster_unit,
        clusters_with_fills: clusters_with_fills.len(),
        interval,
        net_per_fill_bps,
        sharpe: final_metrics::sharpe_ratio(&returns),
        exits,
        returns,
        daily_sharpe: final_metrics::sharpe_ratio(&daily),
        daily,
        month_fill_days: month_fill_days
            .into_iter()
            .map(|(m, n)| (m.to_string(), n))
            .collect(),
    })
}

/// Месяц суток `YYYY-MM-DD` — `YYYY-MM`; имя короче — само себе месяц (порог
/// по нему строже, не мягче).
fn month_of(day: &str) -> &str {
    day.get(..7).unwrap_or(day)
}

/// Матрица «форма × сутки»: ячейка — `net_fill` формы за сутки по всем
/// символам (сумма `net` кругов на число сигналов), как у `net_fill_interval`.
fn form_day_matrix(data: &GridData) -> Vec<Vec<f64>> {
    data.forms
        .iter()
        .map(|form| {
            data.days
                .iter()
                .map(|day| {
                    let mut sum = 0.0;
                    let mut sigs: u64 = 0;
                    for (_c, st) in data
                        .cells
                        .iter()
                        .filter(|(c, _)| &c.form == form && &c.day == day)
                    {
                        sum += st.sum_net_bps;
                        sigs = sigs.saturating_add(st.n_signals);
                    }
                    if sigs == 0 {
                        f64::NAN
                    } else {
                        sum / crate::stats::count_f64_u64(sigs)
                    }
                })
                .collect()
        })
        .collect()
}

/// Вердикт лучшей формы: порог данных — суток с кругами в каждом месяце сетки
/// и `G_MIN` кластеров (суток или часов, В-60), затем интервал. DSR сюда не
/// входит — отчёт (T-26, Судья `11a3d1e`, ответ «в»).
fn verdict_of(best: &FormVerdict) -> Verdict {
    let thin_month = best
        .month_fill_days
        .iter()
        .any(|(_, n)| *n < MIN_FILL_DAYS_PER_MONTH);
    if thin_month || best.clusters_with_fills < G_MIN {
        return Verdict::NotEnoughData;
    }
    let Some(iv) = best.interval.as_ref() else {
        return Verdict::NotEnoughData;
    };
    if iv.lower_bps <= 0.0 {
        Verdict::Red
    } else if iv.point_bps < GREEN_NET_BPS {
        Verdict::GreenNoCapacity
    } else {
        Verdict::Green
    }
}

pub fn run_bounce_verdict(args: &BounceVerdictArgs) -> anyhow::Result<BounceVerdictSummary> {
    let data = read_grid(&args.grid_dir)?;
    let mut forms: Vec<FormVerdict> = Vec::with_capacity(data.forms.len());
    for f in &data.forms {
        forms.push(form_verdict(&data, f)?);
    }
    // Суточный Шарп нужен каждой форме: срез пробных Шарпов — вход `V`, и
    // форма без Шарпа молча выпала бы из среза. Форма, у которой суточный ряд
    // без дисперсии (меньше двух суток или все сутки одинаковы — например, без
    // кругов), входит в срез с Шарпом 0: испытание, которое ничего не дало
    // (19.09: у формы с одним кругом на пул отказ ронял весь вердикт).
    let trial_sharpes: Vec<f64> = forms
        .iter()
        .map(|f| f.daily_sharpe.unwrap_or(0.0))
        .collect();
    let forms_without_sharpe = forms.iter().filter(|f| f.daily_sharpe.is_none()).count();
    if forms_without_sharpe > 0 {
        eprintln!(
            "bounce-verdict: форм без суточного Шарпа (ряд без дисперсии) — {forms_without_sharpe}, в срезе DSR как 0"
        );
    }
    // Условие 1 Судьи `11a3d1e`: формы одной сетки коррелированы (одни
    // сигналы), измеренная `V` занижена — берётся не меньше `1/T`, дисперсии
    // оценки Шарпа под нулём на `T` суток.
    let num_days = data.days.len();
    let v_floor = 1.0 / crate::stats::count_f64(num_days.max(1));
    let v_measured = final_metrics::trial_sharpe_variance(&trial_sharpes);
    let v_used = v_measured.map_or(v_floor, |v| v.max(v_floor));

    let labels: Vec<String> = forms.iter().map(|f| f.form.clone()).collect();
    if args.log_trials {
        runs::ensure_runs_csv(&args.runs_csv)
            .map_err(|e| anyhow::anyhow!("{}: {e}", args.runs_csv.display()))?;
        let ts = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
        runs::log_trials(&args.runs_csv, &ts, BOUNCE_TRIAL_PREFIX, &labels)
            .map_err(|e| anyhow::anyhow!("{}: {e}", args.runs_csv.display()))?;
    }
    let rows = runs::read_run_rows(&args.runs_csv)
        .map_err(|e| anyhow::anyhow!("{}: {e}", args.runs_csv.display()))?;
    let trials = count_scoped_trials(&rows, BOUNCE_TRIAL_PREFIX);
    let journal_trials = runs::count_trials(&rows);
    anyhow::ensure!(
        trials >= forms.len(),
        "{}: испытаний формы отскока {trials}, а форм {} — журнал не знает про прогон; \
         дописать строки можно флагом --log-trials",
        args.runs_csv.display(),
        forms.len()
    );

    let best = forms
        .iter()
        .max_by(|a, b| {
            let pa = a
                .interval
                .as_ref()
                .map(|i| i.point_bps)
                .unwrap_or(f64::NEG_INFINITY);
            let pb = b
                .interval
                .as_ref()
                .map(|i| i.point_bps)
                .unwrap_or(f64::NEG_INFINITY);
            pa.total_cmp(&pb)
        })
        .expect("форм ≥ 48");
    let best_moments = final_metrics::moments(&best.daily);
    let dsr_at = |n_trials: usize| {
        best_moments.as_ref().and_then(|m| {
            final_metrics::dsr_with_variance(
                m.mean / m.std,
                m.n,
                m.skew,
                m.kurtosis,
                n_trials,
                v_used,
            )
        })
    };
    let required_at = |n_trials: usize| {
        final_metrics::required_sharpe_for_dsr_with_variance(n_trials, v_used, num_days, DSR_TARGET)
    };
    let dsr = dsr_at(forms.len());
    let dsr_at_journal_trials = dsr_at(journal_trials);
    let required_sharpe = required_at(forms.len());
    let required_sharpe_at_journal_trials = required_at(journal_trials);

    let matrix = form_day_matrix(&data);
    let scored: Vec<Vec<f64>> = matrix
        .iter()
        .filter(|row| row.iter().all(|x| x.is_finite()))
        .cloned()
        .collect();
    let pbo = final_metrics::pbo(&scored, REPORT_PBO_PARTITIONS);
    let cpcv_params: CpcvParams = REPORT_CPCV_PARAMS;
    let cpcv = final_metrics::cpcv_selection_oos_sharpe(&scored, cpcv_params, select_best_mean_net);
    let verdict = verdict_of(best);

    let num = |v: Option<f64>| match v {
        Some(x) => format!("{x:.6}"),
        None => "—".to_string(),
    };
    let month_days_text = best
        .month_fill_days
        .iter()
        .map(|(m, n)| format!("{m}: {n}"))
        .collect::<Vec<_>>()
        .join(", ");
    if let Some(parent) = args.out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut file = std::fs::File::create(&args.out)
        .with_context(|| format!("{}: вердикт не записался", args.out.display()))?;
    writeln!(
        file,
        "# lob bounce-verdict (В-58, BUSINESS-TASK §6–7, без деления выборки — решение владельца 2026-09-17): \
         grid={} форм {} символов {} суток {}; испытаний {trials} (строки {BOUNCE_TRIAL_PREFIX} в {}), в журнале {journal_trials}",
        args.grid_dir.display(),
        forms.len(),
        data.symbols.len(),
        data.days.len(),
        args.runs_csv.display()
    )?;
    writeln!(
        file,
        "# лучшая форма (по точке net_fill): {} — кругов {} из {} сигналов, суток с кругами {} ({}), net_fill точка={} нижняя={} bps (alpha={GATE_ALPHA}, кластер={}, wild cluster bootstrap-t ×{BOOTSTRAP_REPLICATIONS}), DSR={} по суткам при {} формах сетки (при {journal_trials} испытаниях журнала — контекст перебора: {}), требуемый дневной Шарп для DSR={DSR_TARGET}: {} (при журнале: {}); дневной Шарп формы {}",
        best.form,
        best.n_fills,
        best.n_signals,
        best.days_with_fills,
        month_days_text,
        num(best.interval.as_ref().map(|i| i.point_bps)),
        num(best.interval.as_ref().map(|i| i.lower_bps)),
        // Аудит дизайна 22.09 §4 С2: при суток < G_MIN кластер интервала — час UTC, а шапка
        // писала «сутки» всегда; теперь — фактическая единица.
        if best.cluster_unit == "hour" {
            "час UTC (суток меньше G_MIN — часы одних суток не независимы)"
        } else {
            "сутки"
        },
        num(dsr),
        forms.len(),
        num(dsr_at_journal_trials),
        num(required_sharpe),
        num(required_sharpe_at_journal_trials),
        num(best.daily_sharpe)
    )?;
    // Метки T-26 (условие 4 Судьи `11a3d1e`): артефакт без `dsr_basis` —
    // прежний DSR по кругам, воротами не читать (`docs/COMMANDS.md`).
    writeln!(
        file,
        "# dsr_basis=days dsr_gate=off min_days={MIN_FILL_DAYS_PER_MONTH} T={num_days} n_forms={} n_journal={journal_trials} v={} V_изм={} 1/T={} V={}",
        forms.len(),
        if v_measured.is_some_and(|v| v >= v_floor) {
            "измерено"
        } else {
            "1/T"
        },
        num(v_measured),
        num(Some(v_floor)),
        num(Some(v_used))
    )?;
    writeln!(
        file,
        "# процедура отбора: PBO={} (partitions={REPORT_PBO_PARTITIONS}, матрица форма×сутки, ячейка=net_fill за сутки), CPCV OOS Sharpe={} (folds={}, purge={}, embargo={}, правило: {CPCV_SELECTION_RULE}); матрица {}×{} строк без NaN {}",
        num(pbo),
        num(cpcv),
        cpcv_params.folds,
        cpcv_params.purge,
        cpcv_params.embargo,
        matrix.len(),
        data.days.len(),
        scored.len()
    )?;
    writeln!(
        file,
        "# гейты §7 + В-60 + T-26: суток с кругами ≥ {MIN_FILL_DAYS_PER_MONTH} в каждом месяце сетки, кластеров с кругами ≥ {G_MIN} (кластер — сутки при ≥ {G_MIN} сутках в сетке, иначе час UTC), нижняя граница > 0, ёмкость ≥ {GREEN_NET_BPS} bps; DSR — отчёт, не ворота → ИТОГ: {}",
        verdict.label()
    )?;
    let mut w = csv::Writer::from_writer(file);
    let mut header: Vec<String> = [
        "form",
        "stop",
        "take",
        "deadline_secs",
        "n_signals",
        "n_fills",
        "days_with_fills",
        "cluster_unit",
        "clusters_with_fills",
        "net_fill_point_bps",
        "net_fill_lower_bps",
        "net_per_fill_bps",
        "sharpe",
    ]
    .into_iter()
    .map(String::from)
    .collect();
    for reason in EXIT_REASONS {
        header.push(format!("n_{}", exit_column(reason)));
    }
    for reason in EXIT_REASONS {
        header.push(format!("share_{}", exit_column(reason)));
    }
    w.write_record(&header)?;
    for f in &forms {
        let mut row = vec![
            f.form.clone(),
            f.stop.clone(),
            f.take.clone(),
            f.deadline_secs.to_string(),
            f.n_signals.to_string(),
            f.n_fills.to_string(),
            f.days_with_fills.to_string(),
            f.cluster_unit.to_string(),
            f.clusters_with_fills.to_string(),
            num(f.interval.as_ref().map(|i| i.point_bps)),
            num(f.interval.as_ref().map(|i| i.lower_bps)),
            num(f.net_per_fill_bps),
            num(f.sharpe),
        ];
        for reason in EXIT_REASONS {
            row.push(f.exits_of(reason).to_string());
        }
        for reason in EXIT_REASONS {
            row.push(num(f.share_of(reason)));
        }
        w.write_record(&row)?;
    }
    w.flush()?;

    Ok(BounceVerdictSummary {
        forms: forms.len(),
        symbols: data.symbols.len(),
        days: data.days.len(),
        trials,
        journal_trials,
        best_form: best.form.clone(),
        best_n_fills: best.n_fills,
        best_days: best.days_with_fills,
        best_month_fill_days: best.month_fill_days.clone(),
        best_point_bps: best.interval.as_ref().map(|i| i.point_bps),
        best_lower_bps: best.interval.as_ref().map(|i| i.lower_bps),
        dsr,
        dsr_at_journal_trials,
        required_sharpe,
        required_sharpe_at_journal_trials,
        v_measured,
        v_used,
        pbo,
        cpcv,
        verdict,
        out: args.out.clone(),
    })
}

#[cfg(test)]
mod tests;
