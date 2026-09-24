//! Вывод сетки: результат формы на сутках символа (`FormDayResult`), шапки
//! `rounds.csv`/`forms.csv` (`ROUNDS_HEADER`/`FORMS_HEADER`, В-78/R6) и
//! писатель `Outputs` (строка `forms.csv` — `forms_row`, сумма `net_bps` —
//! `sum_net_bps`, сигналы по часам — `signals_by_hour`). Вынесено из
//! `bounce_grid` при разрезке B3 (ревью 23.09), поведение не менялось.

use std::io::Write;
use std::path::{Path, PathBuf};

use crate::commands::lob::backtest::exit_reason_label;
use crate::lob::backtest::{roundtrip_net_bps, BounceRun, BounceSignal};

use super::forms::GridForm;

/// Результат одной формы на одних сутках одного символа.
pub(super) struct FormDayResult {
    pub(super) form: usize,
    pub(super) run: BounceRun,
    /// Касаний, для которых форму не построить (нет σ / фронтрана / второй
    /// плотности, `--frontrun-only`) — сигнала у них нет.
    pub(super) skipped: u64,
}

/// Колонки `rounds.csv`. F4 (план 2026-09-20, В-78) добавила четыре в конец:
/// `fill_frac` (доля исполненного от заказанного), `entry_vwap` (средняя цена
/// исполненного входа — по ней и стоп/тейк), `legs_filled`, `legs_rejected`
/// (ног, которых биржа не поставила, В-72). Прежние колонки остались на своих
/// местах и с теми же значениями — на этом стоит гейт «те же круги».
const ROUNDS_HEADER: [&str; 16] = [
    "symbol",
    "day_utc",
    "form",
    "signal_index",
    "t0_ns",
    "dir",
    "entry_px",
    "exit_px",
    "qty",
    "net_bps",
    "reason",
    "exit_ns",
    "fill_frac",
    "entry_vwap",
    "legs_filled",
    "legs_rejected",
];

// R6 (ревью 23.09): `n_carried`/`carry_unverified` — колонки переноса через
// полночь (`--carry-root`, e5c8847). Без флага гейт «те же байты» ловит их
// как расхождение — держим их последними `FORMS_HEADER_CARRY_LEN` полями и
// без флага пишем срез `FORMS_HEADER[..FORMS_HEADER.len() -
// FORMS_HEADER_CARRY_LEN]` (33 колонки, прежняя шапка).
pub(super) const FORMS_HEADER_CARRY_LEN: usize = 2;
pub(super) const FORMS_HEADER: [&str; 35] = [
    "symbol",
    "day_utc",
    "form",
    "n_signals",
    "n_submitted",
    "n_fills",
    "n_busy",
    "entry_rejected",
    "entry_crossed",
    "sum_net_bps",
    "n_stop",
    "n_take",
    "n_trail",
    "n_deadline",
    "n_early",
    "n_eaten",
    "n_eaten_by_trades",
    "n_wall_gone",
    "n_partial",
    "n_horizon",
    "incomplete",
    "n_skipped",
    "n_residual_flattened",
    "n_fill_by_cross",
    "n_rejected_postonly",
    "signals_by_hour",
    // F5 (В-74): снятия неисполненного входа по причинам — «стена снята»,
    // «цена ушла из полосы», потолок. Добавлены в конец: прежние колонки
    // остались на местах, на этом стоит гейт «те же круги».
    "n_entry_cancelled_ttl",
    "n_entry_cancelled_wall_dead",
    "n_entry_cancelled_price_left",
    // F8b (В5/Р2): срабатывания потолка ожидания подтверждения отмены
    // (`CANCEL_WAIT_NS`) — на входе и на лимитке выхода. В конце: прежние
    // колонки не сдвинуты (гейт «те же круги»).
    "n_entry_cancelled_cancel_timeout",
    "n_exit_cancel_timeout",
    // F8: средняя доля исполненного входа (F4, В-78).
    "mean_fill_frac",
    // F8c (К1): исполнения заявок-сирот после потолка отмены (ноль — норма).
    "n_orphan_fills",
    // Перенос круга через полночь (`--carry-root`): в конце, прежние колонки
    // не сдвинуты (гейт «те же круги» без флага, `same_fields` по именам).
    "n_carried",
    "carry_unverified",
];

pub(super) struct Outputs {
    rounds: csv::Writer<std::fs::File>,
    forms: csv::Writer<std::fs::File>,
    pub(super) rounds_path: PathBuf,
    pub(super) forms_path: PathBuf,
    // R6: колонки переноса через полночь в `forms.csv` — только с `--carry-root`.
    with_carry: bool,
}

/// Сигналы формы по часам UTC суток, `h0:h1:…:h23` (В-60): вердикт по одним
/// суткам кластеризует интервал `net_fill` по часам, и промахи (у них в
/// `rounds.csv` нет времени) раскладываются по часам из этого столбца.
fn signals_by_hour(signals: &[BounceSignal]) -> String {
    let mut by_hour = [0u64; 24];
    for s in signals {
        let secs = s.t0_ns.div_euclid(1_000_000_000).rem_euclid(86_400);
        by_hour[(secs / 3_600) as usize] += 1;
    }
    by_hour
        .iter()
        .map(u64::to_string)
        .collect::<Vec<_>>()
        .join(":")
}

/// Строка `forms.csv`: значения строго в порядке `FORMS_HEADER`. Вынесена из
/// `Outputs::write_form` ради теста соответствия «поле счётчика → колонка»
/// (F8b, Р4 аудита 21.09): позиционная запись ловит дубликат имени формы, но
/// перепутанные `n_eaten_by_trades`/`n_wall_gone` в ней не видны.
#[allow(clippy::too_many_arguments)]
pub(super) fn forms_row(
    symbol: &str,
    day: &str,
    form_label: &str,
    signals: &[BounceSignal],
    run: &BounceRun,
    skipped: u64,
    mean_fill_frac: f64,
    carry_boundary_ns: Option<i64>,
    carry_unverified: bool,
    // R6: столбцы переноса — только когда прогон вызван с `--carry-root`
    // (без флага `carry_boundary_ns`/`carry_unverified` всё равно приходят
    // `None`/`false`, но это про значение, а не про присутствие колонки —
    // гейт «те же байты» смотрит на шапку).
    with_carry: bool,
) -> Vec<String> {
    let mut row = vec![
        symbol.to_string(),
        day.to_string(),
        form_label.to_string(),
        signals.len().to_string(),
        run.submitted_signal.len().to_string(),
        run.fills.len().to_string(),
        run.busy_signal.len().to_string(),
        run.entry_rejected.to_string(),
        run.entry_crossed.to_string(),
        // Сумма `net_bps` по кругам формы — здесь же, из `run.fills`.
        format!("{:.6}", sum_net_bps(run)),
        run.exits.stop.to_string(),
        run.exits.take.to_string(),
        run.exits.trail.to_string(),
        run.exits.deadline.to_string(),
        run.exits.early.to_string(),
        run.exits.eaten.to_string(),
        run.exits.eaten_by_trades.to_string(),
        run.exits.wall_gone.to_string(),
        run.exits.partial.to_string(),
        run.exits.horizon.to_string(),
        run.incomplete.to_string(),
        skipped.to_string(),
        run.residual_flattened.to_string(),
        // Путь исполнения (3) (F3): крейт исполнил ногу обновлением
        // лучшей цены, а не сделкой, — счётчик по кругам формы.
        run.fills
            .iter()
            .filter(|f| f.fill_by_cross)
            .count()
            .to_string(),
        // Ног входа, которых биржа не поставила (пост-онли `Expired` или
        // `Rejected`, В-72) — по всем кругам формы за сутки.
        run.rejected_postonly.to_string(),
        signals_by_hour(signals),
        // Снятия неисполненного входа по причинам (F5, В-74).
        run.entry_cancelled_ttl.to_string(),
        run.entry_cancelled_wall_dead.to_string(),
        run.entry_cancelled_price_left.to_string(),
        // F8b (В5/Р2): срабатывания потолка ожидания подтверждения отмены.
        run.entry_cancelled_cancel_timeout.to_string(),
        run.exit_cancel_timeout.to_string(),
        // F8: средняя доля исполненного входа (F4, В-78).
        format!("{mean_fill_frac:.6}"),
        // F8c (К1): исполнения заявок-сирот.
        run.orphan_fills.to_string(),
    ];
    if with_carry {
        // Перенос круга через полночь (`--carry-root`): кругов, чей выход
        // случился уже на данных D+1 (`exit_ns ≥` граница полуночи), и флаг
        // «части довеска без сверки» — в конце, прежние колонки не сдвинуты.
        row.push(match carry_boundary_ns {
            Some(boundary) => run
                .fill_exit_ns
                .iter()
                .filter(|&&ns| ns >= boundary)
                .count()
                .to_string(),
            None => "0".to_string(),
        });
        row.push(carry_unverified.to_string());
    }
    row
}

/// Сумма `net_bps` кругов формы — та же арифметика `roundtrip_net_bps`, что у
/// кривой PnL; круг без измеренного `net` в сумму не входит (как и раньше,
/// когда сумма считалась в `write_form`).
///
/// Начало суммы — явный `0.0`, а не `Iterator::sum`: у `f64` он складывает от
/// `-0.0`, и пустая сумма печаталась бы `-0.000000` вместо прежнего
/// `0.000000` — гейт «те же байты» ловит именно это (поймано F8b).
pub(super) fn sum_net_bps(run: &BounceRun) -> f64 {
    run.fills
        .iter()
        .filter_map(roundtrip_net_bps)
        .fold(0.0_f64, |acc, v| acc + v)
}

impl Outputs {
    pub(super) fn create(out_dir: &Path, header: &str, with_carry: bool) -> anyhow::Result<Self> {
        std::fs::create_dir_all(out_dir)?;
        let rounds_path = out_dir.join("rounds.csv");
        let forms_path = out_dir.join("forms.csv");
        let mut rf = std::fs::File::create(&rounds_path)?;
        writeln!(rf, "{header}")?;
        let mut ff = std::fs::File::create(&forms_path)?;
        writeln!(ff, "{header}")?;
        let mut rounds = csv::WriterBuilder::new().has_headers(false).from_writer(rf);
        rounds.write_record(ROUNDS_HEADER)?;
        let mut forms = csv::WriterBuilder::new().has_headers(false).from_writer(ff);
        // R6: без `--carry-root` шапка — прежние 33 колонки (гейт «те же
        // байты»); с флагом — все 35, включая `n_carried`/`carry_unverified`.
        let forms_header = if with_carry {
            &FORMS_HEADER[..]
        } else {
            &FORMS_HEADER[..FORMS_HEADER.len() - FORMS_HEADER_CARRY_LEN]
        };
        forms.write_record(forms_header)?;
        Ok(Self {
            rounds,
            forms,
            rounds_path,
            forms_path,
            with_carry,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn write_form(
        &mut self,
        symbol: &str,
        day: &str,
        form: GridForm,
        signals: &[BounceSignal],
        run: &BounceRun,
        skipped: u64,
        // Перенос круга через полночь (`--carry-root`): граница полуночи D+1
        // для `n_carried` (`None` — довесок не применился к этим суткам) и
        // флаг «части довеска без сверки» (`forms_row`).
        carry_boundary_ns: Option<i64>,
        carry_unverified: bool,
    ) -> anyhow::Result<u64> {
        anyhow::ensure!(
            run.fill_reason.len() == run.fills.len()
                && run.fill_signal.len() == run.fills.len()
                && run.fill_exit_ns.len() == run.fills.len(),
            "{symbol} {day} {}: кругов {}, причин {}, сигналов {} — дамп не пишется",
            form.label,
            run.fills.len(),
            run.fill_reason.len(),
            run.fill_signal.len()
        );
        // `fill_signal` нумерует сигналы в порядке **по t0** (драйвер сортирует
        // копию), а `signals` идут в порядке трекера (по концу касания) — до
        // 2026-09-18 `t0_ns` брался по индексу из несортированного списка и у
        // части кругов был чужим (поймал вердикт по часам: кругов в часе
        // больше сигналов). Сортировка устойчивая, равные t0 взаимозаменяемы.
        let mut t0s: Vec<i64> = signals.iter().map(|s| s.t0_ns).collect();
        t0s.sort_unstable();
        for (i, fill) in run.fills.iter().enumerate() {
            let net = roundtrip_net_bps(fill);
            let sig = run.fill_signal[i];
            let t0 = t0s.get(sig).copied().unwrap_or(0);
            self.rounds.write_record([
                symbol.to_string(),
                day.to_string(),
                form.label.to_string(),
                sig.to_string(),
                t0.to_string(),
                fill.dir.to_string(),
                format!("{:.10}", fill.entry_px),
                format!("{:.10}", fill.exit_px),
                format!("{:.10}", fill.qty),
                net.map(|v| format!("{v:.6}"))
                    .unwrap_or_else(|| "not_measured".to_string()),
                exit_reason_label(run.fill_reason[i]).to_string(),
                run.fill_exit_ns[i].to_string(),
                // F4 (В-78): фактическое исполнение круга — доля и средняя
                // цена входа (по ней считает `roundtrip_net_bps`), число
                // исполнившихся ног и ног, которых биржа не поставила (В-72).
                format!("{:.6}", fill.fill_frac),
                format!("{:.10}", fill.entry_vwap),
                fill.legs_filled.to_string(),
                fill.legs_rejected.to_string(),
            ])?;
        }
        let mean_fill_frac = if run.fills.is_empty() {
            0.0
        } else {
            run.fills.iter().map(|f| f.fill_frac).sum::<f64>() / run.fills.len() as f64
        };
        self.forms.write_record(forms_row(
            symbol,
            day,
            form.label,
            signals,
            run,
            skipped,
            mean_fill_frac,
            carry_boundary_ns,
            carry_unverified,
            self.with_carry,
        ))?;
        // Инвариант вердикта по часам (В-60): кругов в часе не больше сигналов.
        debug_assert!({
            let mut fills_by_hour = [0u64; 24];
            for &sig in &run.fill_signal {
                let secs = t0s[sig].div_euclid(1_000_000_000).rem_euclid(86_400);
                fills_by_hour[(secs / 3_600) as usize] += 1;
            }
            let sig_by_hour: Vec<u64> = signals_by_hour(signals)
                .split(':')
                .map(|v| v.parse().unwrap_or(0))
                .collect();
            (0..24).all(|h| fills_by_hour[h] <= sig_by_hour[h])
        });
        self.rounds.flush()?;
        self.forms.flush()?;
        Ok(run.fills.len() as u64)
    }
}
