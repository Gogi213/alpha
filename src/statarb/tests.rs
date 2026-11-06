use super::cli::{funding_min, parse_funding};
use super::kernel::*;
use super::panel::*;
use super::reference;
use crate::lob::costs::{leg_fee_bps, spot_leg_fee_bps};

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-9 * a.abs().max(b.abs()).max(1.0)
}

/// Детерминированный генератор: u ∈ (−1, 1), сумма трёх — грубая «гауссиана».
pub(super) struct Lcg(pub(super) u64);
impl Lcg {
    pub(super) fn u(&mut self) -> f64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((self.0 >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }
    pub(super) fn g(&mut self) -> f64 {
        (self.u() + self.u() + self.u()) / 1.7
    }
}

/// Ряд цен `exp(ln)` с пропусками (`NaN` в open и close), open = прежнее закрытие с шумом.
pub(super) fn with_gaps(
    ln: &[f64],
    rng: &mut Lcg,
    burst: Option<(usize, usize)>,
) -> (Vec<f64>, Vec<f64>) {
    let n = ln.len();
    let mut o = vec![f64::NAN; n];
    let mut c = vec![f64::NAN; n];
    for t in 0..n {
        let gap = rng.u() > 0.96 || burst.is_some_and(|(a, b)| (a..b).contains(&t));
        if !gap {
            c[t] = ln[t].exp();
            o[t] = (if t > 0 { ln[t - 1] } else { ln[t] } + 1e-4 * rng.g()).exp();
        }
    }
    (o, c)
}

pub(super) fn lns(c: &[f64]) -> Vec<f64> {
    c.iter().map(|v| v.ln()).collect()
}

pub(super) fn walk(n: usize, rng: &mut Lcg, step: f64, start: f64) -> Vec<f64> {
    let mut v = Vec::with_capacity(n);
    let mut x = start;
    for _ in 0..n {
        x += step * rng.g();
        v.push(x);
    }
    v
}

pub(super) fn ou_on(base: &[f64], beta: f64, rng: &mut Lcg, sig: f64) -> Vec<f64> {
    let mut o = 0.0;
    base.iter()
        .map(|x| {
            o = 0.97 * o + sig * rng.g();
            beta * x + o + 1.0
        })
        .collect()
}

pub(super) fn params(beta: BetaMode, yk: LegKind, xk: LegKind) -> Params {
    Params {
        window: 240,
        min_obs: 120,
        beta,
        entry_z: 2.0,
        exit_z: 0.2,
        stop_z: 4.0,
        max_hold: 500,
        refresh: 0,
        y_kind: yk,
        x_kind: xk,
        taker_in: true,
        taker_out: false,
        delay_x: 0,
        rearm: false,
        entry_from: None,
        rule: Rule::Z,
    }
}

pub(super) fn both(inp: &PairInput, p: &Params) -> (Vec<Trade>, Counters) {
    inp.check().unwrap();
    let mut a: Vec<Trade> = Vec::new();
    let mut b: Vec<Trade> = Vec::new();
    let ca = run_pair(inp, p, &mut a);
    let cb = reference::run_pair(inp, p, &mut b);
    assert_eq!(a.len(), b.len(), "число сделок");
    assert!(a.len() >= 5, "тест пуст: сделок {}", a.len());
    for (x, y) in a.iter().zip(&b) {
        // Целые — побайтно.
        assert_eq!(
            (x.dir, x.sig_in, x.fill_in, x.fill_x, x.sig_out, x.fill_out),
            (y.dir, y.sig_in, y.fill_in, y.fill_x, y.sig_out, y.fill_out)
        );
        // TK-493 w3: причина выхода и β на входе — те же в ядре и в референсе
        assert_eq!(x.reason, y.reason, "причина выхода");
        for (u, v) in [
            (x.gross_bps, y.gross_bps),
            (x.fee_bps, y.fee_bps),
            (x.funding_bps, y.funding_bps),
            (x.net_bps, y.net_bps),
            (x.beta, y.beta),
        ] {
            assert!(close(u, v), "{u} против {v}");
        }
    }
    assert!(close(ca.sum_net_bps, cb.sum_net_bps));
    let (mut ca0, mut cb0) = (ca, cb);
    ca0.sum_net_bps = 0.0;
    cb0.sum_net_bps = 0.0;
    assert_eq!(ca0, cb0, "счётчики");
    (a, ca)
}

pub(super) fn funding_events(
    first: i64,
    n: usize,
    every: i64,
    off: i64,
    sign: f64,
) -> Vec<(i64, f64)> {
    (0..n as i64)
        .filter(|i| i % every == off)
        .map(|i| (first + i, sign * 1e-4 * (1.0 + (i % 3) as f64)))
        .collect()
}

pub(super) const N: usize = 8000;

#[test]
fn pair_kernel_matches_reference_with_gaps() {
    let mut r = Lcg(1);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, Some((3000, 3040)));
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 777,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let p = params(BetaMode::Ols, LegKind::Perp, LegKind::Perp);
    let (_, c) = both(&inp, &p);
    assert!(c.gap_minutes > 0 && c.obs_minutes + c.gap_minutes == N as u64);
    // Частота точного пересчёта окна не меняет сделок.
    for refresh in [7, 1000] {
        let mut a: Vec<Trade> = Vec::new();
        let mut b: Vec<Trade> = Vec::new();
        run_pair(&inp, &p, &mut a);
        run_pair(&inp, &Params { refresh, ..p }, &mut b);
        assert_eq!(a.len(), b.len());
        assert!(a.iter().zip(&b).all(|(u, v)| u.fill_in == v.fill_in
            && u.fill_out == v.fill_out
            && close(u.net_bps, v.net_bps)));
    }
}

/// Приёмник z-скора ядра по минутам (хук `Sink::z`, им меряется дрейф на реальных данных).
struct ZSink(Vec<f64>);

impl Sink for ZSink {
    fn trade(&mut self, _: &Trade) {}
    fn z(&mut self, t: usize, z: f64) {
        self.0[t] = z;
    }
}

#[test]
fn z_hook_matches_reference_z_series() {
    let mut r = Lcg(5);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 0.8, &mut r, 2e-3);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, Some((3000, 3040)));
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 0,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    for refresh in [0, 7] {
        let p = Params {
            refresh,
            ..params(BetaMode::Ols, LegKind::Perp, LegKind::Perp)
        };
        let mut k = ZSink(vec![f64::NAN; N]);
        run_pair(&inp, &p, &mut k);
        let zr = reference::z_series(&inp, &p);
        let finite = zr.iter().filter(|q| q.1.is_finite()).count();
        assert!(finite > N / 2, "тест пуст: z конечных {finite}");
        for (t, (a, (_, b))) in k.0.iter().zip(&zr).enumerate() {
            assert_eq!(a.is_finite(), b.is_finite(), "минута {t}");
            if a.is_finite() {
                assert!((a - b).abs() <= 1e-7, "минута {t}: {a} против {b}");
            }
        }
    }
}

#[test]
fn basket_kernel_matches_reference() {
    let mut r = Lcg(2);
    let base = walk(N, &mut r, 1e-3, 3.0);
    let comps: Vec<(Vec<f64>, Vec<f64>)> = (0..3)
        .map(|i| {
            let ln: Vec<f64> = base
                .iter()
                .map(|b| b * (0.7 + 0.2 * i as f64) + i as f64 + 3e-4 * r.g())
                .collect();
            with_gaps(&ln, &mut r, None)
        })
        .collect();
    let w = [0.5, 0.3, 0.2];
    let lx: Vec<f64> = (0..N)
        .map(|t| {
            if comps.iter().all(|c| c.1[t].is_finite()) {
                comps.iter().zip(w).map(|(c, wi)| wi * c.1[t].ln()).sum()
            } else {
                f64::NAN
            }
        })
        .collect();
    let lx_fill: Vec<f64> = lx
        .iter()
        .map(|v| if v.is_finite() { *v } else { 0.0 })
        .collect();
    let y = ou_on(&lx_fill, 1.1, &mut r, 2e-3);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let ly = lns(&cy);
    let ox: Vec<&[f64]> = comps.iter().map(|c| c.0.as_slice()).collect();
    let inp = PairInput {
        first_min: 0,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &ox,
        wx: &w,
        fund_y: &[],
        fund_x: &[],
    };
    both(&inp, &params(BetaMode::Ols, LegKind::Perp, LegKind::Perp));
}

#[test]
fn cross_exchange_fixed_beta_matches_reference() {
    let mut r = Lcg(3);
    let x = walk(N, &mut r, 1e-3, 5.0);
    let y: Vec<f64> = ou_on(&x, 1.0, &mut r, 3e-4)
        .iter()
        .map(|v| v - 1.0)
        .collect();
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 5,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    both(
        &inp,
        &params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp),
    );
}

#[test]
fn funding_perp_spot_and_perp_perp_matches_reference() {
    let mut r = Lcg(4);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 1.0, &mut r, 5e-4);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let fy = funding_events(100, N, 480, 3, 1.0);
    let fx = funding_events(100, N, 480, 3, -1.0);
    let fxs: [&[(i64, f64)]; 1] = [&fx];
    let inp = PairInput {
        first_min: 100,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &fy,
        fund_x: &fxs,
    };
    // Перп против перпа: фандинг обеих ног.
    let (t_pp, _) = both(
        &inp,
        &params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp),
    );
    assert!(
        t_pp.iter().any(|t| t.funding_bps != 0.0),
        "фандинг не выплачен"
    );
    // Перп против спота: фандинг только у ноги-перпа Y.
    let inp2 = PairInput { fund_x: &[], ..inp };
    let (t_ps, _) = both(
        &inp2,
        &params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Spot),
    );
    assert!(t_ps.iter().any(|t| t.funding_bps != 0.0));
}

fn flat_x_params() -> Params {
    Params {
        window: 4,
        min_obs: 3,
        beta: BetaMode::Fixed(1.0),
        entry_z: 1.5,
        exit_z: 0.5,
        stop_z: f64::INFINITY,
        max_hold: 0,
        refresh: 0,
        y_kind: LegKind::Perp,
        x_kind: LegKind::Spot,
        taker_in: true,
        taker_out: true,
        delay_x: 0,
        rearm: false,
        entry_from: None,
        rule: Rule::Z,
    }
}

#[test]
fn single_trade_gross_fees_by_leg_closed_form() {
    // X плоская (100), Y: 100×6, 98, 99, 100…; окно 4. Сигнал входа t=6 (z=−√3), исполнение open[7]=c[6]=98;
    // выход по сигналу t=7 (z≈−0,3), исполнение open[8]=c[7]=99; дальше |z| ≤ 0,9 < 1,5 — входов нет.
    let mut cy = vec![100.0; 6];
    cy.extend([98.0, 99.0, 100.0, 100.0, 100.0, 100.0, 100.0]);
    let n = cy.len();
    let oy: Vec<f64> = (0..n).map(|t| cy[t.saturating_sub(1)]).collect();
    let cx = vec![100.0; n];
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 40,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&cx],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let p = Params {
        min_obs: 4,
        ..flat_x_params()
    };
    let mut tr: Vec<Trade> = Vec::new();
    let c = run_pair(&inp, &p, &mut tr);
    assert_eq!(tr.len(), 1);
    assert_eq!((c.trades, c.entries, c.open_at_end), (1, 1, 0));
    let t = tr[0];
    assert_eq!(
        (t.dir, t.sig_in, t.fill_in, t.sig_out, t.fill_out),
        (1, 46, 47, 47, 48)
    );
    assert!(close(t.gross_bps, 1e4 * (99.0 / 98.0 - 1.0)));
    let (fp, fs) = (leg_fee_bps(true), spot_leg_fee_bps(true));
    let fee = (1.0 + 99.0 / 98.0) * fp + 2.0 * fs;
    assert!(close(t.fee_bps, fee), "{} против {fee}", t.fee_bps);
    assert!(close(t.net_bps, t.gross_bps - fee));
}

#[test]
fn gap_minute_is_not_invented_and_blocks_fill() {
    // Сигнал входа на t=6, но open[7] отсутствует: вход не исполнен, счётчик отложенных растёт.
    let mut cy = vec![100.0; 6];
    cy.extend([98.0, f64::NAN, 100.0, 100.0, 100.0, 100.0]);
    let n = cy.len();
    let mut oy: Vec<f64> = (0..n).map(|t| cy[t.saturating_sub(1)]).collect();
    oy[7] = f64::NAN;
    let cx = vec![100.0; n];
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 0,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&cx],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let mut tr: Vec<Trade> = Vec::new();
    let c = run_pair(&inp, &flat_x_params(), &mut tr);
    assert_eq!(c.entry_deferred, 1);
    assert_eq!(c.gap_minutes, 1);
    assert!(tr.iter().all(|t| t.fill_in != 7));
}

#[test]
fn spot_and_perp_fees_after_rebate() {
    assert!(close(leg_fee_bps(false), 1.26) && close(leg_fee_bps(true), 3.15));
    assert!(close(spot_leg_fee_bps(false), 5.625) && close(spot_leg_fee_bps(true), 6.75));
}

#[test]
fn panel_roundtrip_and_csv_gap_rule() {
    let csv = format!(
        "t_open_ms,o,h,l,c,v,quote_v,n_trades\n{},1.5,2,1,1.75,3,4,5\n{},1.75,2,1,1.8,3,4,5\n{}, 2.5 ,3,1, 2.6,3,4,5\n",
        GRID0_MS + 10 * MINUTE_MS,
        GRID0_MS + 11 * MINUTE_MS,
        GRID0_MS + 14 * MINUTE_MS,
    );
    let s = convert_csv(csv.as_bytes()).unwrap().unwrap();
    assert_eq!((s.first_min, s.len(), s.last_min()), (10, 5, 14));
    assert!(s.close[2].is_nan() && s.close[3].is_nan() && s.open[2].is_nan());
    assert_eq!((s.open[4], s.close[4], s.close[1]), (2.5, 2.6, 1.8));
    let mut buf: Vec<u8> = Vec::new();
    write_series(&mut buf, &s).unwrap();
    let back = read_series(&mut buf.as_slice()).unwrap();
    let bits = |v: &[f64]| v.iter().map(|x| x.to_bits()).collect::<Vec<_>>();
    assert_eq!(back.first_min, s.first_min);
    assert_eq!(bits(&back.open), bits(&s.open));
    assert_eq!(bits(&back.close), bits(&s.close));
    // Только заголовок — нет ряда; время не на минуте и порядок не по возрастанию — отказ.
    assert!(convert_csv("t_open_ms,o,h,l,c\n".as_bytes())
        .unwrap()
        .is_none());
    assert!(convert_csv(format!("{},1,1,1,1\n", GRID0_MS + 7).as_bytes()).is_err());
    assert!(convert_csv(
        format!("{},1,1,1,1\n{},1,1,1,1\n", GRID0_MS + 60_000, GRID0_MS).as_bytes()
    )
    .is_err());
}

#[test]
fn overlap_of_spans() {
    let mk = |f: i64, n: usize| Series {
        first_min: f,
        open: vec![1.0; n],
        close: vec![1.0; n],
    };
    assert_eq!(mk(10, 10).overlap(&mk(15, 10)), Some((5, 0, 5)));
    assert_eq!(mk(10, 3).overlap(&mk(13, 3)), None);
}

/// Каталог панелей AAA, BBB (7 суток, коинтегрированная пара с пропусками) для тестов `statarb run`.
pub(super) fn run_panels_dir() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    let mut rng = Lcg(77);
    let n = 7 * 1440;
    let base = walk(n, &mut rng, 4e-4, 0.0);
    let ln_y = ou_on(&base, 1.0, &mut rng, 6e-4);
    let ln_x = ou_on(&base, 0.0, &mut rng, 4e-4);
    for (sym, ln) in [("AAA", &ln_y), ("BBB", &ln_x)] {
        let (open, close) = with_gaps(ln, &mut rng, None);
        let s = Series {
            first_min: 0,
            open,
            close,
        };
        let mut f = std::fs::File::create(dir.path().join(format!("{sym}.sap"))).unwrap();
        write_series(&mut f, &s).unwrap();
    }
    dir
}

/// `statarb run`: суточные суммы = сумма сделок из `--trades-out` по суткам закрытия; потоки результат
/// не меняют; сделки до окна отброшены; нет панели — посчитано отдельным счётчиком.
#[test]
fn run_daily_equals_trades_and_is_thread_independent() {
    use super::run::RunArgs;
    use clap::Parser;
    use std::collections::BTreeMap;

    #[derive(Parser)]
    struct W {
        #[command(flatten)]
        a: RunArgs,
    }

    let dir = run_panels_dir();
    let jobs = dir.path().join("jobs.csv");
    std::fs::write(
        &jobs,
        "y,x,from_day,to_day\nAAA,BBB,2,6\nAAA,ZZZ,2,6\nBBB,AAA,3,5\n",
    )
    .unwrap();
    let go = |threads: &str, tag: &str| {
        let daily = dir.path().join(format!("daily-{tag}.csv"));
        let trades = dir.path().join(format!("trades-{tag}.csv"));
        let a = W::parse_from([
            "run",
            "--kind",
            "pair",
            "--cell",
            "T",
            "--jobs",
            jobs.to_str().unwrap(),
            "--panels",
            dir.path().to_str().unwrap(),
            "--daily-out",
            daily.to_str().unwrap(),
            "--trades-out",
            trades.to_str().unwrap(),
            "--trade-jobs",
            "0,2",
            "--min-span",
            "100",
            "--threads",
            threads,
            "--window",
            "240",
            "--min-obs",
            "120",
            "--entry-z",
            "2.0",
            "--exit-z",
            "0.2",
            "--stop-z",
            "inf",
            "--max-hold",
            "720",
            "--refresh",
            "0",
        ])
        .a;
        super::run::run(&a).unwrap();
        (
            std::fs::read_to_string(&daily).unwrap(),
            std::fs::read_to_string(&trades).unwrap(),
        )
    };
    let (d1, t1) = go("1", "a");
    let (d4, t4) = go("4", "b");
    assert_eq!(d1, d4, "суточный csv зависит от числа потоков");
    assert_eq!(t1, t4);
    let mut by_day: BTreeMap<i64, (f64, u64)> = BTreeMap::new();
    let mut seen_jobs = std::collections::BTreeSet::new();
    for l in t1.lines().skip(1) {
        let f: Vec<&str> = l.split(',').collect();
        let job: i64 = f[1].parse().unwrap();
        seen_jobs.insert(job);
        let (from, to) = if job == 0 { (2, 6) } else { (3, 5) };
        let (fill_in, fill_out): (i64, i64) = (f[6].parse().unwrap(), f[8].parse().unwrap());
        assert!(
            fill_in >= from * 1440 && fill_out < to * 1440,
            "сделка вне окна: {l}"
        );
        let e = by_day.entry(fill_out / 1440).or_default();
        e.0 += f[12].parse::<f64>().unwrap();
        e.1 += 1;
    }
    assert!(!by_day.is_empty(), "сделок нет — тест ничего не проверяет");
    assert!(seen_jobs.is_subset(&[0, 2].into()));
    // посчитанные задания — 0 и 2 (у 1 нет панели ZZZ), оба в --trade-jobs: суточный csv = сумма записанных сделок
    let mut days = 0;
    for l in d1.lines().skip(1) {
        let f: Vec<&str> = l.split(',').collect();
        let day: i64 = f[1].parse().unwrap();
        let (net, cnt) = by_day.get(&day).copied().unwrap_or_default();
        assert_eq!(
            f[6].parse::<u64>().unwrap(),
            cnt,
            "число сделок суток {day}"
        );
        assert!(close(f[2].parse::<f64>().unwrap(), net), "net суток {day}");
        days += 1;
    }
    assert_eq!(days, by_day.len());
}

/// `statarb run --cells`: каждая клетка многоклеточного прогона (две группы по window/refresh, разные пороги,
/// стоп и срок) совпадает побайтно с отдельным прогоном той же клетки: суточный csv и сделки; потоки не влияют.
#[test]
fn run_cells_equals_single_cell_runs() {
    use super::run::RunArgs;
    use clap::Parser;

    #[derive(Parser)]
    struct W {
        #[command(flatten)]
        a: RunArgs,
    }

    let dir = run_panels_dir();
    let jobs = dir.path().join("jobs.csv");
    std::fs::write(
        &jobs,
        "y,x,from_day,to_day\nAAA,BBB,2,6\nAAA,ZZZ,2,6\nBBB,AAA,3,5\n",
    )
    .unwrap();
    // метка, window, min_obs, beta_fixed, entry_z, exit_z, stop_z, max_hold, refresh
    let cells = [
        ("c0", "240", "120", "", "2.0", "0.2", "inf", "720", "0"),
        ("c1", "240", "120", "", "1.5", "0.5", "4.0", "0", "0"),
        ("c2", "360", "180", "", "2.0", "0.0", "inf", "240", "97"),
        ("c3", "240", "120", "1.0", "2.5", "0.2", "3.5", "480", "0"),
        ("c4", "360", "180", "", "2.5", "0.3", "5.0", "0", "97"),
    ];
    let mut csv =
        String::from("cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh\n");
    for c in &cells {
        csv += &format!(
            "{},{},{},{},{},{},{},{},{}\n",
            c.0, c.1, c.2, c.3, c.4, c.5, c.6, c.7, c.8
        );
    }
    let cells_csv = dir.path().join("cells.csv");
    std::fs::write(&cells_csv, csv).unwrap();
    let base = |daily: &std::path::Path, trades: &std::path::Path, threads: &str| -> Vec<String> {
        [
            "run",
            "--kind",
            "pair",
            "--jobs",
            jobs.to_str().unwrap(),
            "--panels",
            dir.path().to_str().unwrap(),
            "--daily-out",
            daily.to_str().unwrap(),
            "--trades-out",
            trades.to_str().unwrap(),
            "--trade-jobs",
            "0,1,2",
            "--min-span",
            "100",
            "--threads",
            threads,
        ]
        .map(String::from)
        .to_vec()
    };
    let read = |d: &std::path::Path, t: &std::path::Path| {
        (
            std::fs::read_to_string(d).unwrap(),
            std::fs::read_to_string(t).unwrap(),
        )
    };
    // эталон: клетки по одной, склейка в порядке файла
    let (mut want_d, mut want_t) = (String::new(), String::new());
    for (i, c) in cells.iter().enumerate() {
        let (d, t) = (
            dir.path().join(format!("s{i}-d.csv")),
            dir.path().join(format!("s{i}-t.csv")),
        );
        let mut args = base(&d, &t, "1");
        let mut flags = vec![
            "--cell",
            c.0,
            "--window",
            c.1,
            "--min-obs",
            c.2,
            "--entry-z",
            c.4,
            "--exit-z",
            c.5,
            "--stop-z",
            c.6,
            "--max-hold",
            c.7,
            "--refresh",
            c.8,
        ];
        if !c.3.is_empty() {
            flags.extend(["--beta-fixed", c.3]);
        }
        args.extend(flags.into_iter().map(String::from));
        super::run::run(&W::parse_from(args).a).unwrap();
        let (sd, st) = read(&d, &t);
        let skip = usize::from(i > 0);
        want_d += &sd
            .lines()
            .skip(skip)
            .map(|l| format!("{l}\n"))
            .collect::<String>();
        want_t += &st
            .lines()
            .skip(skip)
            .map(|l| format!("{l}\n"))
            .collect::<String>();
    }
    assert!(
        want_t.lines().count() > cells.len(),
        "сделок нет — тест ничего не проверяет"
    );
    for threads in ["1", "4"] {
        let (d, t) = (
            dir.path().join(format!("m{threads}-d.csv")),
            dir.path().join(format!("m{threads}-t.csv")),
        );
        let mut args = base(&d, &t, threads);
        args.extend([
            "--cells".to_string(),
            cells_csv.to_str().unwrap().to_string(),
        ]);
        super::run::run(&W::parse_from(args).a).unwrap();
        let (got_d, got_t) = read(&d, &t);
        assert_eq!(
            got_d, want_d,
            "суточный csv --cells ≠ отдельным прогонам (потоки {threads})"
        );
        assert_eq!(
            got_t, want_t,
            "сделки --cells ≠ отдельным прогонам (потоки {threads})"
        );
    }
}

/// Данные для проверок задержки: пара с OU-спредом, пропуски в open обеих ног, длинная дыра у ноги X,
/// фандинг обеих ног-перпов.
struct DelayCase {
    ly: Vec<f64>,
    lx: Vec<f64>,
    oy: Vec<f64>,
    ox: Vec<f64>,
    fy: Vec<(i64, f64)>,
    fx: Vec<(i64, f64)>,
}

fn delay_case() -> DelayCase {
    let mut r = Lcg(11);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 1.0, &mut r, 5e-4);
    let (ox, cx) = with_gaps(&x, &mut r, Some((2000, 2030)));
    let (oy, cy) = with_gaps(&y, &mut r, None);
    DelayCase {
        ly: lns(&cy),
        lx: lns(&cx),
        oy,
        ox,
        fy: funding_events(100, N, 480, 3, 1.0),
        fx: funding_events(100, N, 480, 3, -1.0),
    }
}

#[test]
fn delay_x_matches_reference_and_shifts_second_leg() {
    let d = delay_case();
    let fxs: [&[(i64, f64)]; 1] = [&d.fx];
    let inp = PairInput {
        first_min: 100,
        ly: &d.ly,
        lx: &d.lx,
        oy: &d.oy,
        ox: &[&d.ox],
        wx: &[1.0],
        fund_y: &d.fy,
        fund_x: &fxs,
    };
    let base = params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp);
    let (t0, c0) = both(&inp, &base);
    assert!(t0.iter().all(|t| t.fill_x == t.fill_in) && c0.x_deferred == 0);
    for delay in [1usize, 2, 5, 30] {
        let p = Params {
            delay_x: delay,
            ..base
        };
        let (tr, c) = both(&inp, &p);
        assert!(tr.iter().all(|t| t.fill_x >= t.fill_in + delay as i64));
        assert!(tr.iter().any(|t| t.fill_x == t.fill_in + delay as i64));
        assert!(
            tr.iter().any(|t| t.funding_bps != 0.0),
            "фандинг не выплачен"
        );
        // Позиция набрана с входа X: сигнал выхода не раньше входа X.
        assert!(tr.iter().all(|t| t.sig_out >= t.fill_x));
        assert_eq!(c.entries, c.trades + c.open_at_end);
    }
}

#[test]
fn delay_x_waits_for_open_of_second_leg() {
    // Дыра только в open ноги X (закрытия целы, z тот же) сразу после входа первой сделки: срок ноги X попадает
    // в дыру, нога ждёт первой минуты с open; первая сделка от дыры не зависит, пока вход не позже её начала.
    let d = delay_case();
    let mut inp = PairInput {
        first_min: 0,
        ly: &d.ly,
        lx: &d.lx,
        oy: &d.oy,
        ox: &[&d.ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let base = params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Perp);
    let (t0, _) = both(&inp, &base);
    let f = t0[0].fill_in as usize;
    let mut ox = d.ox.clone();
    for v in &mut ox[f + 1..f + 12] {
        *v = f64::NAN;
    }
    let oxs: [&[f64]; 1] = [&ox];
    inp.ox = &oxs;
    let delay = 3usize;
    let p = Params {
        delay_x: delay,
        ..base
    };
    let (tr, c) = both(&inp, &p);
    let want = (f + delay..).find(|&i| ox[i].is_finite()).unwrap();
    assert!(want >= f + 12, "срок ноги X не попал в дыру");
    assert_eq!((tr[0].fill_in as usize, tr[0].fill_x as usize), (f, want));
    assert!(c.x_deferred >= 9);
}

#[test]
fn delay_x_closed_form_single_trade() {
    // Сценарий single_trade_gross_fees_by_leg_closed_form с задержкой 1: Y в 47 (open 98), X в 48 (open 100, плоская),
    // позиция набрана в 48, z там ≈ +0,9 ≥ −0,5 — сигнал выхода 48, исполнение 49 (open 100).
    let mut cy = vec![100.0; 6];
    cy.extend([98.0, 99.0, 100.0, 100.0, 100.0, 100.0, 100.0]);
    let n = cy.len();
    let oy: Vec<f64> = (0..n).map(|t| cy[t.saturating_sub(1)]).collect();
    let cx = vec![100.0; n];
    let (lx, ly) = (lns(&cx), lns(&cy));
    let inp = PairInput {
        first_min: 40,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&cx],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let p = Params {
        min_obs: 4,
        delay_x: 1,
        ..flat_x_params()
    };
    let mut tr: Vec<Trade> = Vec::new();
    let c = run_pair(&inp, &p, &mut tr);
    assert_eq!((c.trades, c.entries, c.x_deferred), (1, 1, 0));
    let t = tr[0];
    assert_eq!(
        (t.dir, t.sig_in, t.fill_in, t.fill_x, t.sig_out, t.fill_out),
        (1, 46, 47, 48, 48, 49)
    );
    assert!(close(t.gross_bps, 1e4 * (100.0 / 98.0 - 1.0)));
    let (fp, fs) = (leg_fee_bps(true), spot_leg_fee_bps(true));
    let fee = (1.0 + 100.0 / 98.0) * fp + 2.0 * fs;
    assert!(close(t.fee_bps, fee), "{} против {fee}", t.fee_bps);
    assert!(close(t.net_bps, t.gross_bps - fee));
}

/// Ставки ноги Y раз в `every` минут: `amp·sin(j/4)` — знак и величина меняются, вход и выход идут по уровню.
fn swinging_funding(first: i64, n: usize, every: i64, amp: f64) -> Vec<(i64, f64)> {
    (0..n as i64 / every)
        .map(|j| (first + j * every, amp * (j as f64 / 4.0).sin()))
        .collect()
}

fn funding_params(delay_x: usize, k: usize, hold_payments: usize) -> Params {
    Params {
        max_hold: 3000,
        taker_out: true,
        delay_x,
        rule: Rule::Funding { k, hold_payments },
        ..params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Spot)
    }
}

/// Трейлинг-среднее последних `k` выплат с минутой ≤ `ft` независимым подсчётом по списку.
fn mean_at(ev: &[(i64, f64)], ft: i64, k: usize) -> f64 {
    let v: Vec<f64> = ev.iter().filter(|e| e.0 <= ft).map(|e| e.1).collect();
    if v.len() < k {
        return f64::NAN;
    }
    v[v.len() - k..].iter().sum::<f64>() / k as f64
}

/// Файл ставок как у биржи: `time_ms,rate,interval_h`, время выплаты в мс unix с дрожанием в мс.
fn funding_csv(t0_ms: i64, ev: &[(i64, f64)], first: i64) -> String {
    let mut s = String::from("time_ms,rate,interval_h\n");
    for (m, r) in ev {
        s.push_str(&format!("{},{r},8\n", t0_ms + (m - first) * MINUTE_MS + 3));
    }
    s
}

#[test]
fn funding_minutes_share_the_epoch_of_panels() {
    // E-010: ставки читались как `time_ms / 60000` (unix), панели — минуты от GRID0_MS: выплата не попадала в ряд.
    let t0 = 1_585_152_000_000; // первая выплата BTCUSDT Binance, 2020-03-25 16:00 UTC
    assert_eq!(funding_min(t0), 121_920);
    assert_eq!(funding_min(GRID0_MS), 0);
    assert_eq!(funding_min(GRID0_MS + 10 * MINUTE_MS + 3), 10);
    assert_eq!(funding_min(GRID0_MS - 1), -1);
    let csv = format!("t_open_ms,o,h,l,c\n{t0},1,1,1,1\n");
    let s = convert_csv(csv.as_bytes()).unwrap().unwrap();
    let f = parse_funding(&format!("time_ms,rate,interval_h\n{},0.0001,8\n", t0 + 7)).unwrap();
    assert_eq!(f, vec![(s.first_min, 1e-4)]);
    assert!(parse_funding("time_ms,rate,interval_h\nx,1,8\n").is_err());
}

#[test]
fn funding_from_exchange_files_lands_in_panel_minutes() {
    // Ряд в эпохе панелей, ставки — через загрузчик из текста биржи: выплаты начисляются (D2), вход по ставке (D3)
    // даёт сделки, ядро == референс.
    let t0 = 1_585_152_000_000 + 25 * 60 * MINUTE_MS;
    // Начало ряда — по правилу конвертера панелей, независимо от загрузчика ставок.
    let first = convert_csv(format!("t_open_ms,o,h,l,c\n{t0},1,1,1,1\n").as_bytes())
        .unwrap()
        .unwrap()
        .first_min;
    let mut r = Lcg(31);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 1.0, &mut r, 3e-4);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let ev = swinging_funding(first + 50, N, 60, 6e-4);
    let fy = parse_funding(&funding_csv(t0, &ev, first)).unwrap();
    assert_eq!(fy.len(), ev.len());
    assert!(fy.iter().zip(&ev).all(|(a, b)| a.0 == b.0 && a.1 == b.1));
    let inp = PairInput {
        first_min: first,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &fy,
        fund_x: &[],
    };
    let (d3, _) = both(&inp, &funding_params(0, 2, 5));
    assert!(d3.iter().any(|t| t.funding_bps != 0.0));
    let (d2, _) = both(
        &inp,
        &params(BetaMode::Fixed(1.0), LegKind::Perp, LegKind::Spot),
    );
    assert!(d2.iter().any(|t| t.funding_bps != 0.0), "D2: фандинг ноль");
}

#[test]
fn funding_rule_matches_reference_and_rule_holds() {
    let mut r = Lcg(21);
    let x = walk(N, &mut r, 1e-3, 4.0);
    let y = ou_on(&x, 1.0, &mut r, 3e-4);
    let (ox, cx) = with_gaps(&x, &mut r, None);
    let (oy, cy) = with_gaps(&y, &mut r, None);
    let (lx, ly) = (lns(&cx), lns(&cy));
    let first = 50;
    let fy = swinging_funding(first, N, 60, 6e-4);
    let inp = PairInput {
        first_min: first,
        ly: &ly,
        lx: &lx,
        oy: &oy,
        ox: &[&ox],
        wx: &[1.0],
        fund_y: &fy,
        fund_x: &[],
    };
    let (k, h) = (2usize, 5usize);
    let p = funding_params(0, k, h);
    let (tr, _) = both(&inp, &p);
    assert!(tr.iter().any(|t| t.dir == 1) && tr.iter().any(|t| t.dir == -1));
    assert!(tr.iter().any(|t| t.funding_bps != 0.0));
    let cost = round_trip_cost_bps(&p, 1.0, 1.0);
    let edge = |dir: i8, ft: i64| -f64::from(dir) * mean_at(&fy, ft, k) * 1e4 * h as f64;
    for t in &tr {
        // Вход: на сигнальной минуте доход ожидаемого удержания выше круга издержек, сторона — получающая фандинг.
        let m = mean_at(&fy, t.sig_in, k);
        assert!(
            m.is_finite() && m.abs() * 1e4 * h as f64 > cost,
            "вход {t:?}"
        );
        assert_eq!(t.dir, if m > 0.0 { -1 } else { 1 });
        // Позиция держится, пока доход выше круга; выход — когда перестал (если не закрыта по времени).
        for ft in t.fill_x..t.sig_out {
            assert!(
                edge(t.dir, ft) > cost,
                "держали при доходе ниже круга: {t:?}"
            );
        }
        if t.sig_out - t.fill_x < 3000 {
            assert!(edge(t.dir, t.sig_out) <= cost, "выход {t:?}");
        }
    }
}

#[test]
fn funding_rule_with_delay_gaps_and_two_funding_legs_matches_reference() {
    let d = delay_case();
    let fy = swinging_funding(100, N, 60, 6e-4);
    let fxs: [&[(i64, f64)]; 1] = [&d.fx];
    let inp = PairInput {
        first_min: 100,
        ly: &d.ly,
        lx: &d.lx,
        oy: &d.oy,
        ox: &[&d.ox],
        wx: &[1.0],
        fund_y: &fy,
        fund_x: &fxs,
    };
    for delay in [0usize, 3, 40] {
        let p = Params {
            x_kind: LegKind::Perp,
            ..funding_params(delay, 3, 4)
        };
        let (tr, _) = both(&inp, &p);
        assert!(tr.iter().all(|t| t.fill_x >= t.fill_in + delay as i64));
    }
}

#[test]
fn funding_rule_without_payments_does_not_trade() {
    let d = delay_case();
    let inp = PairInput {
        first_min: 0,
        ly: &d.ly,
        lx: &d.lx,
        oy: &d.oy,
        ox: &[&d.ox],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    let mut tr: Vec<Trade> = Vec::new();
    let c = run_pair(&inp, &funding_params(0, 2, 5), &mut tr);
    assert_eq!((tr.len(), c.entries), (0, 0));
}

#[test]
#[should_panic(expected = "k ≥ 1")]
fn funding_rule_requires_positive_k() {
    let inp = PairInput {
        first_min: 0,
        ly: &[0.0],
        lx: &[0.0],
        oy: &[1.0],
        ox: &[&[1.0]],
        wx: &[1.0],
        fund_y: &[],
        fund_x: &[],
    };
    run_pair(&inp, &funding_params(0, 0, 5), &mut ());
}
