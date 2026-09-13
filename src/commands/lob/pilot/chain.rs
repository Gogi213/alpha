//! Хвост `--debug`-цепочки G-DEBUG (09(б)): `lob profiles --allow-unverified`
//! по пулу, затем `lob backtest --debug` на сигналах каждого символа, и
//! сводный `pilot-debug-summary.csv`. Отдельно от `pilot.rs`: не сетевая
//! часть отладочного режима, тестируется на синтетике той же фикстурой.

use std::path::Path;

use crate::commands::lob::backtest::{run_backtest, BacktestArgs};
use crate::commands::lob::profiles::{run_profiles, ProfilesArgs};
use crate::commands::lob::{ExecutionArgs, H3Args, H3ModeArg};

use super::inputs::{compute_order_qty_e9, resolve_backtest_rtt_ns, write_signals_csv_from_levels};
use super::metrics::{InstrumentMetrics, StepFailure};

/// Путь `runs_out`, которым `run_profiles_and_backtest_chain` зовёт `lob
/// profiles` внутри `--debug`-цепочки: `allow_unverified: true` гарантирует,
/// что `run_profiles` его не тронет (`profiles.rs`: «отладочный режим файл
/// не трогает») — имя нарочно недвусмысленное, регресс-тест
/// `debug_chain_after_process_instrument_backtests_and_never_writes_runs_csv`
/// проверяет, что файл с этим именем не появляется (долг ревью 09(а)).
pub(super) const DEBUG_CHAIN_RUNS_SENTINEL: &str = "profiles-runs-should-never-exist.csv";

// ---------------------------------------------------------------------------
// Хвост `--debug`-цепочки (09(б)): `lob profiles --allow-unverified` по
// всему пулу, затем `lob backtest --debug` на сигналах каждого символа —
// сигналы из уже написанного `levels-floor-<SYMBOL>.csv` (`run_levels`),
// RTT из `probe-<SYMBOL>.csv`/`clock.csv` сессии или флагов, лот —
// `order_size_22a` от `instruments.csv` пула. Не сетевая: работает на уже
// готовых каталогах — тестируется на синтетике той же фикстурой, что
// `process_instrument`.
// ---------------------------------------------------------------------------

/// Хвост цепочки G-DEBUG после `verify -> levels -> markout` (шаг 09(б)):
/// `lob profiles --allow-unverified` по всему пулу, затем `lob backtest
/// --debug` на сигналах каждого символа. Принимает уже готовый `pilot_root`
/// (`session/` с настоящими `<SYMBOL>-<день>.binlog` и копией
/// `instruments.csv` внутри — из `copy_pool_instruments_csv`) — не сетевая,
/// тестируется на синтетике. Возвращает напечатанные строки и первый
/// найденный дефект (тем же протоколом, что цикл `process_instrument` в
/// `run_pilot_debug`).
pub(super) fn run_profiles_and_backtest_chain(
    pilot_root: &Path,
    symbols: &[String],
    warmup_ms: i64,
    repeat_window_ms: i64,
    candidates_csv: &Path,
    explicit_rtt_ns: (Option<i64>, Option<i64>),
    now: &str,
) -> (Vec<String>, Option<String>) {
    let (explicit_median_rtt_ns, explicit_p95_rtt_ns) = explicit_rtt_ns;
    let mut lines = Vec::new();
    let mut first_defect: Option<String> = None;
    let session_dir = pilot_root.join("session");

    if let Err(e) = std::fs::copy(
        session_dir.join("instruments.csv"),
        pilot_root.join("instruments.csv"),
    ) {
        let msg = format!("instruments.csv для profiles: {e}");
        lines.push(format!("pilot debug: profiles — упал: {msg}"));
        return (lines, Some(format!("profiles: {msg}")));
    }

    let profiles_out = pilot_root.join("profiles-debug.csv");
    // Тройка RTT/лота площадки (таск 16, `resolve_fill_model`) здесь
    // намеренно не задаётся: она одна на весь пул, а лот (`order_size_22a`)
    // у каждого символа свой (см. `compute_order_qty_e9` ниже, для шага
    // `backtest`) — подставлять единый лот на пул значило бы изобретать
    // число для символов, для которых он не измерен. `profiles` остаётся на
    // `NoFillModel` (`fill_model=none`), а вердикт по исполнению даёт
    // отдельный шаг `backtest` ниже, per-symbol.
    let profiles_result = run_profiles(&ProfilesArgs {
        root: pilot_root.to_path_buf(),
        candidates_csv: candidates_csv.to_path_buf(),
        h3: H3Args {
            h3_mode: H3ModeArg::Floor,
            h3_lots: None,
        },
        warmup_ms,
        repeat_window_ms,
        allow_unverified: true,
        out: Some(profiles_out.clone()),
        now_utc: Some(now.to_string()),
        runs_out: pilot_root.join(DEBUG_CHAIN_RUNS_SENTINEL),
        execution: ExecutionArgs {
            median_rtt_ns: None,
            p95_rtt_ns: None,
            order_qty_e9: None,
        },
        // Окно «сейчас» (ticket 21, R57): не нужно здесь — `allow_unverified:
        // true` выше уже снимает требование окна (doc `profiles.rs`); поля
        // добавлены только чтобы этот литерал остался исчерпывающим после
        // добавления `--preregistration`/`--window-end` в `ProfilesArgs`.
        preregistration: None,
        window_end: None,
    });
    match &profiles_result {
        Ok(s) => lines.push(format!(
            "pilot debug: profiles rows={} out={}",
            s.rows,
            s.out.display()
        )),
        Err(e) => {
            lines.push(format!("pilot debug: profiles — упал: {e}"));
            first_defect.get_or_insert_with(|| format!("profiles: {e}"));
        }
    }
    let profiles_csv_for_comparison = profiles_result.is_ok().then(|| profiles_out.clone());

    for symbol in symbols {
        let levels_csv = session_dir.join(format!("levels-floor-{symbol}.csv"));
        let signals_csv = session_dir.join(format!("signals-{symbol}.csv"));
        let profile_id = format!("debug:{symbol}");
        let last_price_tick =
            match write_signals_csv_from_levels(&levels_csv, &signals_csv, &profile_id) {
                Ok(v) => v,
                Err(e) => {
                    lines.push(format!("pilot debug: {symbol} backtest — сигналы: {e}"));
                    first_defect.get_or_insert_with(|| format!("backtest:{symbol}: сигналы: {e}"));
                    continue;
                }
            };
        let Some(last_price_tick) = last_price_tick else {
            lines.push(format!(
                "pilot debug: {symbol} backtest — пропущен, нет сигналов (0 уровней floor)"
            ));
            continue;
        };
        let instruments_csv = session_dir.join("instruments.csv");
        let order_qty_e9 = match compute_order_qty_e9(&instruments_csv, symbol, last_price_tick) {
            Ok(v) => v,
            Err(e) => {
                lines.push(format!("pilot debug: {symbol} backtest — лот: {e}"));
                first_defect.get_or_insert_with(|| format!("backtest:{symbol}: лот: {e}"));
                continue;
            }
        };
        let (median_rtt_ns, p95_rtt_ns, rtt_source) = match resolve_backtest_rtt_ns(
            &session_dir,
            symbol,
            explicit_median_rtt_ns,
            explicit_p95_rtt_ns,
        ) {
            Ok(v) => v,
            Err(e) => {
                lines.push(format!("pilot debug: {symbol} backtest — RTT: {e}"));
                first_defect.get_or_insert_with(|| format!("backtest:{symbol}: RTT: {e}"));
                continue;
            }
        };
        let bt = run_backtest(&BacktestArgs {
            session_root: session_dir.clone(),
            symbol: symbol.clone(),
            signals_csv: Some(signals_csv),
            median_rtt_ns,
            p95_rtt_ns,
            order_qty_e9,
            profiles_csv: profiles_csv_for_comparison.clone(),
            out: Some(session_dir.join(format!("backtest-{symbol}.csv"))),
            pnl_out: Some(session_dir.join(format!("backtest-{symbol}-pnl.csv"))),
            debug: true,
            // Пилот гоняет старую сетку смертей: касания (В-44) — отдельная
            // ветка `--touches`, здесь она выключена.
            touches: false,
            post_only: false,
            h3: crate::commands::lob::H3Args {
                h3_mode: crate::commands::lob::H3ModeArg::Floor,
                h3_lots: None,
            },
            h3_k: None,
            warmup_ms: None,
            repeat_window_ms: None,
        });
        match bt {
            Ok(s) => lines.push(format!(
                "pilot debug: {symbol} backtest rtt_source={rtt_source} median_rtt_ns={median_rtt_ns} p95_rtt_ns={p95_rtt_ns} order_qty_e9={order_qty_e9} profiles={} pass={} red={}",
                s.profiles, s.pass, s.red
            )),
            Err(e) => {
                lines.push(format!("pilot debug: {symbol} backtest — упал: {e}"));
                first_defect.get_or_insert_with(|| format!("backtest:{symbol}: {e}"));
            }
        }
    }
    (lines, first_defect)
}

/// Сводный артефакт `--debug`: шапка несёт `debug` первой строкой (тот же
/// приём, что `levels.rs::debug_warning` — терпимый к `#` `csv::Reader`, не
/// изобретённый второй формат). Это единственный артефакт, который пилот
/// пишет сам, поэтому только он несёт метку явно; `markout-<SYMBOL>.csv`
/// такой шапки не пишет (см. doc `process_instrument`, CONCERNS таска 09).
pub(super) fn write_debug_summary_csv(
    out: &Path,
    reports: &[(String, Result<InstrumentMetrics, StepFailure>)],
) -> anyhow::Result<()> {
    use std::io::Write as _;
    let mut file = std::fs::File::create(out)?;
    writeln!(
        file,
        "# debug: пилот ≤5 минут — результат не данные, ни для вердикта, ни для предрегистрации"
    )?;
    let mut w = csv::Writer::from_writer(file);
    w.write_record([
        "symbol",
        "step_failed",
        "verify_ok",
        "levels_floor",
        "levels_percentile",
        "rate_floor_per_min",
        "rate_percentile_per_min",
        "share_eaten",
        "share_pulled",
        "share_mixed",
        "m10s_n",
        "m10s_mean_bps",
        "m10s_lower_bps",
        "sharpe",
    ])?;
    for (symbol, outcome) in reports {
        match outcome {
            Ok(m) => {
                w.write_record([
                    symbol.clone(),
                    String::new(),
                    m.verify_ok.to_string(),
                    m.levels_floor.to_string(),
                    m.levels_percentile.to_string(),
                    format!("{:.6}", m.levels_per_min_floor),
                    format!("{:.6}", m.levels_per_min_percentile),
                    format!("{:.6}", m.share_eaten),
                    format!("{:.6}", m.share_pulled),
                    format!("{:.6}", m.share_mixed),
                    m.m10s_n.to_string(),
                    m.m10s_mean_bps
                        .map(|v| format!("{v:.6}"))
                        .unwrap_or_default(),
                    m.m10s_lower_bps
                        .map(|v| format!("{v:.6}"))
                        .unwrap_or_default(),
                    m.sharpe.map(|v| format!("{v:.6}")).unwrap_or_default(),
                ])?;
            }
            Err(f) => {
                w.write_record([
                    symbol.clone(),
                    format!("{}: {}", f.step, f.message),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                    String::new(),
                ])?;
            }
        }
    }
    w.flush()?;
    Ok(())
}
