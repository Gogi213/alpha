//! `lob sync-basis` — замер §4 П-22 (TK-328): часы, опережение и базис пары «спот / фьючерс» одной монеты
//! за сутки. Только чтение двух наборов бинлога (части суток, `*.binlog` и `*.binlog.zst`), ни одного
//! порога: одна строка `ключ,значение` на число, дальше агрегирует `tools/compute/tk328-sync-agg.py`.
//!
//! - **Часы** (`clock,<рынок>,…`): `local_ts − exch_ts` на каждое обновление книги в миллисекундах —
//!   медиана, p99, доля ровно нулевых (нуль на всех — признак «приёмное = биржевое», т.е. импорт архива).
//! - **Опережение** (`lag,<k>,<corr>`): корреляция секундных доходностей mid спота `r_S[t]` и фьючерса
//!   `r_F[t+k]`, `k = −5…5` (по биржевому времени). Максимум при `k < 0` — фьючерс опережает спот.
//! - **Базис** (`basis,…`): `(F_mid − S_mid)/S_mid` в bps по секундам, где есть оба mid: p5/p50/p95;
//!   дрейф (`drift30m`, `drift4h`) — std и p5/p95 изменения базиса за 30 мин / 4 ч.
//!
//! Время — биржевое (`Update::cts_ms`), mid секунды — последний на конец секунды, пропуски заполняются
//! предыдущим значением (книга не менялась). Сутки в сетке — от UTC-полуночи первой записи.

use std::fs::File;
use std::io::BufReader;
use std::path::PathBuf;

use clap::Args;

use crate::book::Book;
use crate::bybit::ws::Event as WsEvent;
use crate::feed::replay::ReplayFeed;
use crate::feed::{Event, Feed};

/// Секунд в сетке: двое суток (запас на части, зашедшие за полночь).
const GRID_SECS: usize = 2 * 86_400;
/// Максимальное смещение lead-lag, секунд.
const MAX_LAG: i64 = 5;
/// Корзин гистограммы часов: 1 мс на корзину, последняя — «≥ 10 с».
const CLOCK_BUCKETS: usize = 10_001;
/// Окна дрейфа базиса: 30 мин и 4 ч, секунд.
const DRIFT_WINDOWS: [(&str, usize); 2] = [("drift30m", 1_800), ("drift4h", 14_400)];

/// Аргументы `lob sync-basis`.
#[derive(Debug, Args)]
pub struct SyncBasisArgs {
    /// Части суток спота (`*.binlog` / `*.binlog.zst`), по порядку.
    #[arg(long, num_args = 1.., required = true)]
    pub spot: Vec<PathBuf>,
    /// Части суток фьючерса, по порядку.
    #[arg(long, num_args = 1.., required = true)]
    pub fut: Vec<PathBuf>,
}

/// Один рынок за сутки: гистограмма `local − exch` и mid по секундам.
pub(crate) struct Market {
    /// `local_ts − exch_ts` в мс: корзина = мс, отрицательные считаются отдельно.
    hist: Vec<u64>,
    negative: u64,
    zero: u64,
    total: u64,
    /// Последний mid секунды (цена, не bps), `NaN` — не было обновления.
    pub(crate) mid: Vec<f64>,
    base_sec: Option<i64>,
    /// Шаг цены бинлога (`tick_e9` заголовка), 1e-9 валюты котировки.
    pub(crate) tick_e9: i64,
    /// Шаг размера бинлога (`step_e9` заголовка, лот), 1e-9 базовой монеты.
    pub(crate) step_e9: i64,
    /// Обновления, отвергнутые книгой (разрыв `u`, пересечение): их mid не учтён.
    pub rejected: u64,
}

impl Market {
    fn new() -> Self {
        Self {
            hist: vec![0; CLOCK_BUCKETS],
            negative: 0,
            zero: 0,
            total: 0,
            mid: vec![f64::NAN; GRID_SECS],
            base_sec: None,
            tick_e9: 0,
            step_e9: 0,
            rejected: 0,
        }
    }

    fn note_clock(&mut self, local_ns: i64, exch_ms: i64) {
        // Округление вниз: 0,9 мс и 0 мс — одна корзина; «ровно нуль» — по нс.
        let d_ns = local_ns - exch_ms * 1_000_000;
        self.total += 1;
        if d_ns == 0 {
            self.zero += 1;
        }
        if d_ns < 0 {
            self.negative += 1;
        } else {
            let b = usize::try_from(d_ns / 1_000_000).unwrap_or(usize::MAX);
            self.hist[b.min(CLOCK_BUCKETS - 1)] += 1;
        }
    }

    fn note_mid(&mut self, exch_ms: i64, mid: f64) {
        let sec = exch_ms.div_euclid(1_000);
        let base = *self.base_sec.get_or_insert(sec.div_euclid(86_400) * 86_400);
        if let Ok(i) = usize::try_from(sec - base) {
            if i < GRID_SECS {
                self.mid[i] = mid;
            }
        }
    }

    /// Mid на конец последней **полной** секунды до `ms` (без заглядывания вперёд), с переносом вперёд;
    /// `fill` — `forward_fill(&self.mid)`. `None` — до `ms` обновлений не было.
    pub(crate) fn mid_before(&self, fill: &[f64], ms: i64) -> Option<f64> {
        let base = self.base_sec?;
        let i = usize::try_from(ms.div_euclid(1_000) - 1 - base).ok()?;
        fill.get(i.min(GRID_SECS - 1))
            .copied()
            .filter(|v| v.is_finite())
    }

    /// Квантиль гистограммы часов, мс (по неотрицательным).
    fn clock_quantile(&self, q: f64) -> f64 {
        let n: u64 = self.hist.iter().sum();
        if n == 0 {
            return f64::NAN;
        }
        let target = (q * (n - 1) as f64).floor() as u64;
        let mut acc = 0u64;
        for (ms, &c) in self.hist.iter().enumerate() {
            acc += c;
            if acc > target {
                return ms as f64;
            }
        }
        (CLOCK_BUCKETS - 1) as f64
    }
}

/// Прогнать части суток одного рынка через `ReplayFeed` + `Book`.
pub(crate) fn read_market(parts: &[PathBuf]) -> anyhow::Result<Market> {
    let mut m = Market::new();
    for path in parts {
        let file = File::open(path).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        let mut feed = ReplayFeed::open(0, BufReader::with_capacity(1 << 20, file))
            .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        let header_tick = {
            let f = File::open(path)?;
            crate::binlog::Reader::open(BufReader::new(f))
                .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?
                .header()
        };
        m.tick_e9 = header_tick.tick_e9;
        m.step_e9 = header_tick.step_e9;
        let mut book = Book::new(header_tick.tick_e9, header_tick.step_e9);
        while let Some(ev) = feed.next_event() {
            let Event::Market {
                local_ts_ns,
                payload: WsEvent::Book(up),
                ..
            } = ev
            else {
                continue;
            };
            m.note_clock(local_ts_ns, up.cts_ms);
            if book.apply(&up).is_err() {
                m.rejected += 1;
                continue;
            }
            if let (Some(b), Some(a)) = (book.best_bid_tick_opt(), book.best_ask_tick_opt()) {
                let mid = (b + a) as f64 * 0.5 * header_tick.tick_e9 as f64 * 1e-9;
                m.note_mid(up.cts_ms, mid);
            }
        }
    }
    Ok(m)
}

/// Mid по секундам с переносом вперёд; до первого значения — `NaN`.
pub(crate) fn forward_fill(raw: &[f64]) -> Vec<f64> {
    let mut last = f64::NAN;
    raw.iter()
        .map(|&v| {
            if !v.is_nan() {
                last = v;
            }
            last
        })
        .collect()
}

/// Корреляция Пирсона по парам, где обе величины конечны; `None` — меньше трёх пар или нулевой разброс.
pub(crate) fn pearson(a: &[f64], b: &[f64]) -> Option<f64> {
    let pairs: Vec<(f64, f64)> = a
        .iter()
        .zip(b)
        .filter(|(x, y)| x.is_finite() && y.is_finite())
        .map(|(&x, &y)| (x, y))
        .collect();
    let n = pairs.len() as f64;
    if pairs.len() < 3 {
        return None;
    }
    let (mx, my) = (
        pairs.iter().map(|p| p.0).sum::<f64>() / n,
        pairs.iter().map(|p| p.1).sum::<f64>() / n,
    );
    let (mut sxy, mut sxx, mut syy) = (0.0, 0.0, 0.0);
    for (x, y) in &pairs {
        sxy += (x - mx) * (y - my);
        sxx += (x - mx) * (x - mx);
        syy += (y - my) * (y - my);
    }
    if sxx <= 0.0 || syy <= 0.0 {
        return None;
    }
    Some(sxy / (sxx * syy).sqrt())
}

/// Квантиль отсортированного среза (ближайший ранг снизу).
pub(crate) fn quantile(sorted: &[f64], q: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    sorted[((sorted.len() - 1) as f64 * q).floor() as usize]
}

fn sorted_finite(v: impl Iterator<Item = f64>) -> Vec<f64> {
    let mut out: Vec<f64> = v.filter(|x| x.is_finite()).collect();
    out.sort_by(f64::total_cmp);
    out
}

fn std_of(v: &[f64]) -> f64 {
    if v.len() < 2 {
        return f64::NAN;
    }
    let m = v.iter().sum::<f64>() / v.len() as f64;
    (v.iter().map(|x| (x - m) * (x - m)).sum::<f64>() / (v.len() - 1) as f64).sqrt()
}

/// Секундные лог-доходности (`NaN` там, где mid нет или не менялся стартовый).
fn log_returns(mid: &[f64]) -> Vec<f64> {
    let mut r = vec![f64::NAN; mid.len()];
    for i in 1..mid.len() {
        if mid[i].is_finite() && mid[i - 1].is_finite() && mid[i] > 0.0 && mid[i - 1] > 0.0 {
            r[i] = (mid[i] / mid[i - 1]).ln();
        }
    }
    r
}

/// `corr(r_S[t], r_F[t+k])` для `k = −MAX_LAG…MAX_LAG`.
pub(crate) fn lead_lag(spot: &[f64], fut: &[f64]) -> Vec<(i64, Option<f64>)> {
    let (rs, rf) = (log_returns(spot), log_returns(fut));
    (-MAX_LAG..=MAX_LAG)
        .map(|k| {
            // r_F сдвинут на k: пара (rs[t], rf[t+k]).
            let (a, b): (&[f64], &[f64]) = if k >= 0 {
                let k = k as usize;
                (&rs[..rs.len() - k], &rf[k..])
            } else {
                let k = (-k) as usize;
                (&rs[k..], &rf[..rf.len() - k])
            };
            (k, pearson(a, b))
        })
        .collect()
}

/// Базис в bps по секундам; `NaN`, где нет mid обоих рынков.
pub(crate) fn basis_bps(spot: &[f64], fut: &[f64]) -> Vec<f64> {
    spot.iter()
        .zip(fut)
        .map(|(&s, &f)| {
            if s.is_finite() && f.is_finite() && s > 0.0 {
                (f - s) / s * 1e4
            } else {
                f64::NAN
            }
        })
        .collect()
}

/// Изменение базиса за `w` секунд по всем парам `(t, t+w)`, где базис есть в обеих точках.
pub(crate) fn basis_drift(basis: &[f64], w: usize) -> Vec<f64> {
    if basis.len() <= w {
        return Vec::new();
    }
    (0..basis.len() - w)
        .filter_map(|t| {
            let d = basis[t + w] - basis[t];
            d.is_finite().then_some(d)
        })
        .collect()
}

fn clock_lines(tag: &str, m: &Market) -> Vec<String> {
    vec![
        format!("clock,{tag},n,{}", m.total),
        format!("clock,{tag},median_ms,{}", m.clock_quantile(0.5)),
        format!("clock,{tag},p99_ms,{}", m.clock_quantile(0.99)),
        format!(
            "clock,{tag},share_zero,{}",
            if m.total == 0 {
                f64::NAN
            } else {
                m.zero as f64 / m.total as f64
            }
        ),
        format!(
            "clock,{tag},share_negative,{}",
            if m.total == 0 {
                f64::NAN
            } else {
                m.negative as f64 / m.total as f64
            }
        ),
        format!("clock,{tag},rejected,{}", m.rejected),
    ]
}

/// Строки отчёта по двум прочитанным рынкам.
pub(crate) fn report(spot: &Market, fut: &Market) -> Vec<String> {
    let mut out = clock_lines("spot", spot);
    out.extend(clock_lines("fut", fut));
    // Сетки спота и фьючерса обязаны начинаться с одной полуночи, иначе секунды не сопоставить.
    if spot.base_sec != fut.base_sec {
        out.push(format!(
            "error,base_sec_mismatch,{:?},{:?}",
            spot.base_sec, fut.base_sec
        ));
        return out;
    }
    let (s, f) = (forward_fill(&spot.mid), forward_fill(&fut.mid));
    for (k, c) in lead_lag(&s, &f) {
        out.push(format!("lag,{k},{}", c.map_or(f64::NAN, |v| v)));
    }
    let basis = basis_bps(&s, &f);
    let b = sorted_finite(basis.iter().copied());
    out.push(format!("basis,n,{}", b.len()));
    for (name, q) in [("p5", 0.05), ("p50", 0.5), ("p95", 0.95)] {
        out.push(format!("basis,{name},{}", quantile(&b, q)));
    }
    for (name, w) in DRIFT_WINDOWS {
        let d = sorted_finite(basis_drift(&basis, w).into_iter());
        out.push(format!("{name},n,{}", d.len()));
        out.push(format!("{name},std,{}", std_of(&d)));
        out.push(format!("{name},p5,{}", quantile(&d, 0.05)));
        out.push(format!("{name},p95,{}", quantile(&d, 0.95)));
    }
    out
}

/// Запуск: прочитать оба рынка и напечатать отчёт.
pub fn run_sync_basis(args: &SyncBasisArgs) -> anyhow::Result<Vec<String>> {
    let spot = read_market(&args.spot)?;
    let fut = read_market(&args.fut)?;
    Ok(report(&spot, &fut))
}

#[cfg(test)]
mod tests;
