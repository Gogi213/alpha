//! Прогон-вердикт базовой сетки форм отскока (B5, В-58).
//!
//! Сводка `lob backtest --touches` печатает **средние** по форме, а поправка
//! на число испытаний дефлирует Шарп **ряда** покруговых `net` — по средним его
//! не посчитать. Поэтому прогон формы оставляет покруговой дамп
//! (`--trades-out`, один файл на символ), а эта подкоманда собирает дампы в
//! один вердикт: по форме — круги, `net_fill`, Шарп ряда и доли причин выхода,
//! по лучшей форме — `DSR` при `N` испытаний **этой** процедуры отбора.
//!
//! `N` считается по журналу `runs.csv` (`lob/runs.rs`) и только по строкам
//! этой процедуры — с префиксом `bounce_form`: в журнале лежат испытания других
//! процедур (пилоты плотности, профили касаний), и складывать их с формами
//! отскока значило бы дефлировать Шарп выбором, которого не было. Полное число
//! испытаний журнала печатается рядом как контекст: если прогонов было больше,
//! чем строк, вердикт обязан быть строже, и это видно.
//!
//! Форма названа каталогом `<стоп>-<дедлайн>-<ранний>` (`behind-60-off`,
//! `before-7200-3`) значениями предрегистрированной сетки В-58: другое имя —
//! отказ, а не параметр (сетка зафиксирована до данных).

use std::path::{Path, PathBuf};

use anyhow::Context as _;

use crate::lob::final_metrics::{self, DSR_TARGET};
use crate::lob::runs::{self, BOUNCE_TRIAL_PREFIX};

/// Формы стопа предрегистрированной сетки (B1/В-58 п. 1) — те же имена, что
/// значения `--stop-mode`.
pub const STOP_MODES: [&str; 3] = ["before", "at", "behind"];

/// Дедлайны сетки, секунды (B1/В-58 п. 4).
pub const DEADLINE_SECS: [u64; 4] = [60, 600, 3600, 7200];

/// Досрочный выход сетки, секунды: `off` — выключен (четвёртый вариант оси,
/// B1/В-58 п. 5).
pub const EARLY_EXIT_LABELS: [&str; 4] = ["off", "1", "2", "3"];

/// Причины выхода, как их пишет `--trades-out` (`ExitReason` словами).
pub const EXIT_REASONS: [&str; 6] = ["stop", "take", "trail", "deadline", "early", "horizon"];

/// Аргументы `lob bounce-verdict`.
#[derive(Debug, clap::Args)]
pub struct BounceVerdictArgs {
    /// Каталог прогонов: `<каталог>/<форма>/<СИМВОЛ>.csv` — покруговые дампы
    /// `lob backtest --touches --trades-out`.
    #[arg(long)]
    pub trades_dir: PathBuf,
    /// Журнал испытаний: по строке-испытанию на форму (`--log-trials`), чтобы
    /// `N` поправки было настоящим числом испытанных вариантов.
    #[arg(long)]
    pub runs_csv: PathBuf,
    /// Куда писать таблицу вердикта.
    #[arg(long)]
    pub out: PathBuf,
    /// Дописать в журнал строку на каждую форму (`confirmatory`, префикс
    /// `bounce_form`). Без флага журнал только читается: повторный прогон не
    /// имеет права удваивать `N` молча — это учёт, а не побочный эффект.
    #[arg(long, default_value_t = false)]
    pub log_trials: bool,
}

/// Итог одной формы: покруговой ряд, его сводка и доли причин выхода.
#[derive(Debug, Clone, PartialEq)]
pub struct FormVerdict {
    /// Имя каталога формы (`behind-60-off`).
    pub form: String,
    /// Форма стопа (`before`/`at`/`behind`).
    pub stop: String,
    /// Дедлайн плана, секунды.
    pub deadline_secs: u64,
    /// Досрочный выход, секунды (`None` — выключен).
    pub early_exit_secs: Option<u64>,
    /// Число кругов (наблюдений ряда).
    pub n_fills: usize,
    /// Средний чистый результат круга, bps.
    pub net_fill_bps: Option<f64>,
    /// Шарп ряда покруговых `net` (на наблюдение).
    pub sharpe: Option<f64>,
    /// Число выходов по каждой причине, параллельно `EXIT_REASONS`.
    pub exits: [u64; EXIT_REASONS.len()],
    /// Сам ряд — вход `dsr_from_returns`.
    pub returns: Vec<f64>,
}

impl FormVerdict {
    /// Число выходов по причине (`0`, если причины в этой форме не было).
    pub fn exits_of(&self, reason: &str) -> u64 {
        EXIT_REASONS
            .iter()
            .position(|r| *r == reason)
            .map(|i| self.exits[i])
            .unwrap_or(0)
    }

    /// Доля выходов по причине от кругов формы.
    pub fn share_of(&self, reason: &str) -> Option<f64> {
        if self.n_fills == 0 {
            return None;
        }
        Some(self.exits_of(reason) as f64 / crate::stats::count_f64(self.n_fills))
    }
}

/// Сводка прогона-вердикта (то, что печатает вызывающий).
#[derive(Debug, Clone)]
pub struct BounceVerdictSummary {
    /// Число форм сетки в каталоге.
    pub forms: usize,
    /// Испытаний этой процедуры в журнале (строки `bounce_form`).
    pub trials: usize,
    /// Всего испытаний в журнале (контекст: другие процедуры отбора).
    pub journal_trials: usize,
    /// Форма с лучшим `net_fill`.
    pub best_form: String,
    /// Её `net_fill`, bps.
    pub best_net_fill_bps: Option<f64>,
    /// `DSR` лучшей формы при `trials` испытаниях.
    pub dsr: Option<f64>,
    /// `DSR` той же формы при полном числе испытаний журнала (строже).
    pub dsr_at_journal_trials: Option<f64>,
    /// Требуемый Шарп на наблюдение для `DSR_TARGET` при `trials` и её `n`.
    pub required_sharpe: Option<f64>,
    /// Куда записан артефакт.
    pub out: PathBuf,
}

/// Разбирает имя формы по предрегистрированной сетке В-58.
pub fn parse_form(label: &str) -> anyhow::Result<(&'static str, u64, Option<u64>)> {
    let parts: Vec<&str> = label.split('-').collect();
    anyhow::ensure!(
        parts.len() == 3,
        "{label}: имя формы — <стоп>-<дедлайн>-<ранний> (`behind-60-off`), сетка В-58"
    );
    let stop = *STOP_MODES.iter().find(|s| **s == parts[0]).ok_or_else(|| {
        anyhow::anyhow!("{label}: стоп {} вне сетки В-58 {STOP_MODES:?}", parts[0])
    })?;
    let deadline_secs: u64 = parts[1]
        .parse()
        .map_err(|_| anyhow::anyhow!("{label}: дедлайн {} не число секунд", parts[1]))?;
    anyhow::ensure!(
        DEADLINE_SECS.contains(&deadline_secs),
        "{label}: дедлайн {deadline_secs} с вне сетки В-58 {DEADLINE_SECS:?}"
    );
    let early_exit_secs = if parts[2] == "off" {
        None
    } else {
        let secs: u64 = parts[2].parse().map_err(|_| {
            anyhow::anyhow!(
                "{label}: досрочный выход {} не число секунд и не `off`",
                parts[2]
            )
        })?;
        anyhow::ensure!(
            EARLY_EXIT_LABELS.contains(&parts[2]),
            "{label}: досрочный выход {secs} с вне сетки В-58 {EARLY_EXIT_LABELS:?}"
        );
        Some(secs)
    };
    Ok((stop, deadline_secs, early_exit_secs))
}

/// Число испытаний одной процедуры отбора: строки журнала, которые он
/// засчитывает (`RunKind::counts_as_trial`), с деталью `<prefix> <id>`.
/// Префикс, а не всё чтение: в одном журнале живут испытания разных процедур.
pub fn count_scoped_trials(rows: &[runs::RunRow], prefix: &str) -> usize {
    rows.iter()
        .filter(|r| r.kind.counts_as_trial())
        .filter(|r| {
            r.detail
                .strip_prefix(prefix)
                .is_some_and(|rest| rest.starts_with(' '))
        })
        .count()
}

/// Строка покругового дампа `--trades-out`: читается как есть, `net_bps`
/// строкой — чтобы «круг не посчитан» (`not_measured`) был отказом, а не
/// нулём.
#[derive(Debug, serde::Deserialize)]
struct TradeRow {
    #[allow(dead_code)]
    signal_index: u64,
    #[allow(dead_code)]
    dir: i8,
    #[allow(dead_code)]
    entry_px: f64,
    #[allow(dead_code)]
    exit_px: f64,
    #[allow(dead_code)]
    qty: f64,
    net_bps: String,
    reason: String,
}

/// Читает круги одной формы из всех `*.csv` её каталога (по одному на символ).
fn read_form_trades(dir: &Path) -> anyhow::Result<(Vec<f64>, [u64; EXIT_REASONS.len()])> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .with_context(|| format!("{}: каталог формы не читается", dir.display()))?
        .map(|e| e.map(|e| e.path()))
        .collect::<Result<Vec<_>, _>>()
        .with_context(|| format!("{}: запись каталога не читается", dir.display()))?
        .into_iter()
        .filter(|p| p.extension().is_some_and(|e| e == "csv"))
        .collect();
    files.sort();
    anyhow::ensure!(
        !files.is_empty(),
        "{}: нет ни одного покругового дампа (<СИМВОЛ>.csv)",
        dir.display()
    );
    let mut returns = Vec::new();
    let mut exits = [0u64; EXIT_REASONS.len()];
    for path in files {
        let mut r = csv::ReaderBuilder::new()
            .comment(Some(b'#'))
            .from_path(&path)
            .with_context(|| format!("{}: дамп не открылся", path.display()))?;
        for row in r.deserialize::<TradeRow>() {
            let row =
                row.with_context(|| format!("{}: строка дампа не разобралась", path.display()))?;
            let net: f64 = row.net_bps.parse().map_err(|_| {
                anyhow::anyhow!(
                    "{}: net_bps {} — круг не посчитан, а не ноль; в ряд Шарпа он не годится",
                    path.display(),
                    row.net_bps
                )
            })?;
            let idx = EXIT_REASONS
                .iter()
                .position(|r| *r == row.reason)
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "{}: причина выхода {} вне словаря {EXIT_REASONS:?}",
                        path.display(),
                        row.reason
                    )
                })?;
            exits[idx] += 1;
            returns.push(net);
        }
    }
    Ok((returns, exits))
}

/// Собирает вердикт: читает формы, пишет испытания в журнал (по флагу),
/// считает `DSR` лучшей формы и записывает таблицу.
pub fn run_bounce_verdict(args: &BounceVerdictArgs) -> anyhow::Result<BounceVerdictSummary> {
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(&args.trades_dir)
        .with_context(|| {
            format!(
                "{}: каталог прогонов не читается",
                args.trades_dir.display()
            )
        })?
        .map(|e| e.map(|e| e.path()))
        .collect::<Result<Vec<_>, _>>()
        .with_context(|| format!("{}: запись каталога не читается", args.trades_dir.display()))?
        .into_iter()
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();
    anyhow::ensure!(
        dirs.len() >= 2,
        "{}: форм {}. Поправка на число испытаний считается по сетке, а не по одной форме",
        args.trades_dir.display(),
        dirs.len()
    );

    let mut forms: Vec<FormVerdict> = Vec::with_capacity(dirs.len());
    for dir in &dirs {
        let label = dir
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or_else(|| anyhow::anyhow!("{}: имя каталога формы не читается", dir.display()))?
            .to_string();
        let (stop, deadline_secs, early_exit_secs) = parse_form(&label)?;
        let (returns, exits) = read_form_trades(dir)?;
        let n_fills = returns.len();
        let net_fill_bps = if n_fills == 0 {
            None
        } else {
            Some(returns.iter().sum::<f64>() / crate::stats::count_f64(n_fills))
        };
        forms.push(FormVerdict {
            form: label,
            stop: stop.to_string(),
            deadline_secs,
            early_exit_secs,
            n_fills,
            net_fill_bps,
            sharpe: final_metrics::sharpe_ratio(&returns),
            exits,
            returns,
        });
    }
    // Шарп нужен каждой форме: срез пробных Шарпов — вход поправки, и форма
    // без Шарпа молча выпала бы из среза, занизив `N` (то же правило, что
    // `final_metrics::moments`: нулевая дисперсия — отказ, не ноль).
    let trial_sharpes: Vec<f64> = forms
        .iter()
        .map(|f| {
            f.sharpe.ok_or_else(|| {
                anyhow::anyhow!(
                    "{}: Шарп не считается ({} кругов, нужен ряд из ≥ 2 с ненулевой дисперсией)",
                    f.form,
                    f.n_fills
                )
            })
        })
        .collect::<anyhow::Result<Vec<f64>>>()?;

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
        "{}: испытаний формы отскока {trials}, а форм {forms} — журнал не знает про прогон; \
         дописать строки можно флагом --log-trials",
        args.runs_csv.display(),
        forms = forms.len()
    );

    let best = forms
        .iter()
        .max_by(|a, b| {
            a.net_fill_bps
                .unwrap_or(f64::NEG_INFINITY)
                .total_cmp(&b.net_fill_bps.unwrap_or(f64::NEG_INFINITY))
        })
        .expect("форм ≥ 2");
    let dsr = final_metrics::dsr_from_returns(&best.returns, &trial_sharpes);
    let dsr_at_journal_trials = best.sharpe.and_then(|sr| {
        final_metrics::dsr_for_trial_count(sr, best.n_fills, 0.0, 3.0, journal_trials)
    });
    let required_sharpe = final_metrics::required_sharpe_for_dsr(trials, best.n_fills, DSR_TARGET);

    let num = |v: Option<f64>| match v {
        Some(x) => format!("{x:.6}"),
        None => "—".to_string(),
    };
    if let Some(parent) = args.out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut file = std::fs::File::create(&args.out)
        .with_context(|| format!("{}: вердикт не записался", args.out.display()))?;
    use std::io::Write as _;
    writeln!(
        file,
        "# lob bounce-verdict: форм {}, испытаний {} (строки {BOUNCE_TRIAL_PREFIX} в {}), \
         испытаний в журнале {}, лучшая форма {} net_fill={} bps, DSR={} при {trials} испытаниях, \
         DSR={} при {journal_trials}, требуемый Шарп для DSR={DSR_TARGET} при {trials} и n={} равен {}",
        forms.len(),
        trials,
        args.runs_csv.display(),
        journal_trials,
        best.form,
        num(best.net_fill_bps),
        num(dsr),
        num(dsr_at_journal_trials),
        best.n_fills,
        num(required_sharpe)
    )?;
    let mut w = csv::Writer::from_writer(file);
    let mut header = vec![
        "form",
        "stop",
        "deadline_secs",
        "early_exit_secs",
        "n_fills",
        "net_fill_bps",
        "sharpe",
    ];
    for reason in EXIT_REASONS {
        header.push(match reason {
            "stop" => "n_stop",
            "take" => "n_take",
            "trail" => "n_trail",
            "deadline" => "n_timeout",
            "early" => "n_early",
            _ => "n_horizon",
        });
    }
    for reason in EXIT_REASONS {
        header.push(match reason {
            "stop" => "share_stop",
            "take" => "share_take",
            "trail" => "share_trail",
            "deadline" => "share_timeout",
            "early" => "share_early",
            _ => "share_horizon",
        });
    }
    w.write_record(&header)?;
    for f in &forms {
        let mut row = vec![
            f.form.clone(),
            f.stop.clone(),
            f.deadline_secs.to_string(),
            match f.early_exit_secs {
                Some(x) => x.to_string(),
                None => "off".to_string(),
            },
            f.n_fills.to_string(),
            num(f.net_fill_bps),
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
        trials,
        journal_trials,
        best_form: best.form.clone(),
        best_net_fill_bps: best.net_fill_bps,
        dsr,
        dsr_at_journal_trials,
        required_sharpe,
        out: args.out.clone(),
    })
}

#[cfg(test)]
mod tests;
