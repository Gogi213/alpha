//! Вывод сетки: результат формы на сутках символа (`FormDayResult`), шапки
//! `rounds.csv`/`forms.csv` (`ROUNDS_HEADER`/`FORMS_HEADER`, В-78/R6) и
//! писатель `Outputs` (строка `forms.csv` — `forms_row`, сумма `net_bps` —
//! `sum_net_bps`, сигналы по часам — `signals_by_hour`). Вынесено из
//! `bounce_grid` при разрезке B3 (ревью 23.09), поведение не менялось.

use std::io::Write;
use std::path::{Path, PathBuf};

use crate::commands::lob::backtest::exit_reason_label;
use crate::lob::backtest::{roundtrip_net_bps, BounceRun, BounceSignal};
use crate::lob::strategy::TradePlan;

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
    // T-31: `signals.csv` — след каждого сигнала, только с `--busy-skip off`.
    signals: Option<csv::Writer<std::fs::File>>,
    // TK-012: колонки П-08 в `signals.csv` — только с `--p08-cols`.
    with_p08: bool,
    // TK-025: колонки R1 в `signals.csv` (после П-08) — только с `--r1-cols`.
    with_r1: bool,
    // TK-115 Г-112: колонка `tape_press_lots` в `rounds.csv` — только с `--tape-log`.
    with_tape: bool,
}

/// Колонки `signals.csv` (T-31, `--busy-skip off`): по строке на сигнал набора, который дошёл до драйвера.
/// `signal_index` — номер в порядке по `t0` (как в `rounds.csv`); `price_tick` — тик стены (цена касания /
/// подхода в тиках, как `price_tick` кэша касаний T-28: склейка признаков по (символ, `t0`, тик) однозначна и
/// при двух подходах с одним `arm_ms`); `entry_px` — цена входа плана; `idle_ns` — когда форма снова свободна (`-` — шаг без движка); `residual` —
/// `flat` (остаток закрыт страховкой) / `ended` (запись кончилась в процессе — обрыв суток) / пусто;
/// `exit_ns` — у исполненного круга, как в `rounds.csv`.
const SIGNALS_HEADER: [&str; 11] = [
    "symbol",
    "day_utc",
    "form",
    "signal_index",
    "t0_ns",
    "price_tick",
    "entry_px",
    "step",
    "idle_ns",
    "residual",
    "exit_ns",
];

/// Колонки П-08 (TK-012, `--p08-cols`) в конце `signals.csv` — те же имена, что в кэше подходов
/// (`lob::levels::ArmP08` на кадре взвода сигнала); без флага их нет.
const SIGNALS_P08_HEADER: [&str; 6] = [
    "traded_lots_at_arm",
    "size_max_at_arm",
    "size_monotonic_at_arm",
    "eat_60s_lots",
    "size_max_60s_lots",
    "depth_behind50_lots_at_arm",
];

/// Клетки `SIGNALS_P08_HEADER` одного подхода.
pub(super) fn p08_cells(p: &crate::lob::levels::ArmP08) -> [String; 6] {
    [
        p.traded_lots.to_string(),
        p.size_max.to_string(),
        u8::from(p.size_monotonic).to_string(),
        p.eat_60s_lots.to_string(),
        p.size_max_60s_lots.to_string(),
        p.depth_behind50_lots.to_string(),
    ]
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
    pub(super) fn create(
        out_dir: &Path,
        header: &str,
        with_carry: bool,
        with_signals: bool,
        with_p08: bool,
        with_r1: bool,
        with_tape: bool,
    ) -> anyhow::Result<Self> {
        std::fs::create_dir_all(out_dir)?;
        let rounds_path = out_dir.join("rounds.csv");
        let forms_path = out_dir.join("forms.csv");
        let mut rf = std::fs::File::create(&rounds_path)?;
        writeln!(rf, "{header}")?;
        let mut ff = std::fs::File::create(&forms_path)?;
        writeln!(ff, "{header}")?;
        let mut rounds = csv::WriterBuilder::new().has_headers(false).from_writer(rf);
        if with_tape {
            let mut names: Vec<&str> = ROUNDS_HEADER.to_vec();
            names.push("tape_press_lots");
            names.push("cxl_lots");
            rounds.write_record(names)?;
        } else {
            rounds.write_record(ROUNDS_HEADER)?;
        }
        let mut forms = csv::WriterBuilder::new().has_headers(false).from_writer(ff);
        // R6: без `--carry-root` шапка — прежние 33 колонки (гейт «те же
        // байты»); с флагом — все 35, включая `n_carried`/`carry_unverified`.
        let forms_header = if with_carry {
            &FORMS_HEADER[..]
        } else {
            &FORMS_HEADER[..FORMS_HEADER.len() - FORMS_HEADER_CARRY_LEN]
        };
        forms.write_record(forms_header)?;
        let signals = if with_signals {
            let mut sf = std::fs::File::create(out_dir.join("signals.csv"))?;
            writeln!(sf, "{header}")?;
            let mut w = csv::WriterBuilder::new().has_headers(false).from_writer(sf);
            let mut names: Vec<&str> = SIGNALS_HEADER.to_vec();
            if with_p08 {
                names.extend(SIGNALS_P08_HEADER);
            }
            if with_r1 {
                names.extend(crate::lob::r1::ArmR1::names());
            }
            w.write_record(names)?;
            Some(w)
        } else {
            None
        };
        Ok(Self {
            rounds,
            forms,
            rounds_path,
            forms_path,
            with_carry,
            signals,
            with_p08,
            with_r1,
            with_tape,
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
        // TK-012: записи подхода суток (`--signal approach`) — источник колонок П-08.
        approaches: Option<&[crate::lob::levels::ApproachRecord]>,
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
            let mut row = vec![
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
            ];
            if self.with_tape {
                row.push(format!("{:.6}", fill.tape_press));
                row.push(format!("{:.6}", fill.cxl_press));
            }
            self.rounds.write_record(row)?;
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
        if let Some(w) = self.signals.as_mut() {
            // тот же порядок, что у драйвера: устойчивая сортировка копии по `t0`
            let mut order: Vec<&BounceSignal> = signals.iter().collect();
            order.sort_by_key(|s| s.t0_ns);
            let mut exit_of: Vec<Option<i64>> = vec![None; order.len()];
            for (i, &sig) in run.fill_signal.iter().enumerate() {
                if let Some(e) = exit_of.get_mut(sig) {
                    *e = Some(run.fill_exit_ns[i]);
                }
            }
            // TK-012: подход сигнала — по (`arm_ms`, тик стены), тот же ключ склейки, что у
            // `price_tick` строки; ищется только с `--p08-cols` / `--r1-cols`.
            let flag = if self.with_p08 {
                "--p08-cols"
            } else {
                "--r1-cols"
            };
            let p08_index: Vec<(i64, i64, usize)> =
                match (self.with_p08 || self.with_r1, approaches) {
                    (true, Some(ap)) => {
                        let mut v: Vec<(i64, i64, usize)> = ap
                            .iter()
                            .enumerate()
                            .map(|(i, a)| (a.arm_ms, a.price_tick, i))
                            .collect();
                        v.sort_unstable();
                        v
                    }
                    (true, None) => anyhow::bail!("{symbol} {day}: {flag} без записей подхода"),
                    _ => Vec::new(),
                };
            for t in &run.trace {
                let sig = order.get(t.signal);
                let tick = sig.and_then(|s| match s.plan {
                    // тот же перевод, что у стратегии (`strategy.rs`, `level_tick`)
                    TradePlan::Bounce {
                        level_px, tick_px, ..
                    } if tick_px > 0.0 => Some((level_px / tick_px).round() as i64),
                    _ => None,
                });
                let mut row: Vec<String> = Vec::with_capacity(SIGNALS_HEADER.len() + 6);
                row.extend([
                    symbol.to_string(),
                    day.to_string(),
                    form.label.to_string(),
                    t.signal.to_string(),
                    sig.map_or(0, |s| s.t0_ns).to_string(),
                    tick.map_or_else(String::new, |v| v.to_string()),
                    sig.map_or_else(String::new, |s| match s.plan {
                        TradePlan::Bounce { entry_px, .. } => format!("{entry_px:.10}"),
                        TradePlan::SpreadHold => String::new(),
                    }),
                    t.step.label().to_string(),
                    if t.idle_ns == i64::MIN {
                        "-".to_string()
                    } else {
                        t.idle_ns.to_string()
                    },
                    match t.residual {
                        None => "",
                        Some(false) => "flat",
                        Some(true) => "ended",
                    }
                    .to_string(),
                    exit_of
                        .get(t.signal)
                        .copied()
                        .flatten()
                        .map_or_else(String::new, |e| e.to_string()),
                ]);
                if self.with_p08 || self.with_r1 {
                    let (Some(s), Some(tick), Some(ap)) = (sig, tick, approaches) else {
                        anyhow::bail!(
                            "{symbol} {day} {}: {flag} — у сигнала {} нет тика стены",
                            form.label,
                            t.signal
                        );
                    };
                    let arm_ms = s.t0_ns.div_euclid(1_000_000);
                    let at = p08_index
                        .binary_search_by(|&(a, k, _)| (a, k).cmp(&(arm_ms, tick)))
                        .ok()
                        .and_then(|j| p08_index.get(j))
                        .map(|&(_, _, i)| &ap[i]);
                    if self.with_p08 {
                        let Some(p) = at.and_then(|a| a.p08) else {
                            anyhow::bail!(
                                "{symbol} {day} {}: --p08-cols — нет признаков подхода arm_ms={arm_ms} тик {tick}",
                                form.label
                            );
                        };
                        row.extend(p08_cells(&p));
                    }
                    if self.with_r1 {
                        let Some(r) = at.and_then(|a| a.r1.as_deref()) else {
                            anyhow::bail!(
                                "{symbol} {day} {}: --r1-cols — нет признаков R1 подхода arm_ms={arm_ms} тик {tick}",
                                form.label
                            );
                        };
                        row.extend(crate::commands::lob::touches::r1_cells(Some(r)));
                    }
                }
                w.write_record(&row)?;
            }
            w.flush()?;
        }
        self.rounds.flush()?;
        self.forms.flush()?;
        Ok(run.fills.len() as u64)
    }
}
