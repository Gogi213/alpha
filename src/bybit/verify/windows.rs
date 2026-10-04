//! Счётчики по окнам (минута/час) для гейта В-177. Только считают: тест 3 и его
//! числа они не меняют, без `enable_windows` ничего не накапливается.

use std::collections::{BTreeMap, HashSet};

pub const MINUTE_MS: i64 = 60_000;
pub const HOUR_MS: i64 = 3_600_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowKind {
    Minute,
    Hour,
}

impl WindowKind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Minute => "m",
            Self::Hour => "h",
        }
    }

    fn len_ms(self) -> i64 {
        match self {
            Self::Minute => MINUTE_MS,
            Self::Hour => HOUR_MS,
        }
    }
}

/// Закрытое окно. `v` — нарушения теста 3 (как в сутках), `vw` — сделка внутри
/// диапазона книги, не внутри спреда, по тику, которого не было в книге в окне
/// (не RPI, не block); `vws` — то же, но внутри спреда (разрешение ob200, только
/// число); `max_run` — самая длинная серия `v` подряд по сделкам. `ne/ve/vwe/
/// max_run_e` — те же счёты, где единица — исполнение тейкера (одинаковые
/// exch_ms + сторона + тик = одно событие).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WindowRow {
    pub kind: WindowKind,
    pub id: i64,
    pub n: u64,
    pub u: u64,
    pub v: u64,
    pub vw: u64,
    pub vws: u64,
    pub max_run: u64,
    pub ne: u64,
    pub ve: u64,
    pub vwe: u64,
    pub max_run_e: u64,
}

impl WindowRow {
    pub const CSV_HEADER: &'static str = "kind,id,n,u,v,vw,vws,max_run,ne,ve,vwe,max_run_e";

    pub fn csv(&self) -> String {
        format!(
            "{},{},{},{},{},{},{},{},{},{},{},{}",
            self.kind.name(),
            self.id,
            self.n,
            self.u,
            self.v,
            self.vw,
            self.vws,
            self.max_run,
            self.ne,
            self.ve,
            self.vwe,
            self.max_run_e
        )
    }
}

struct Acc {
    n: u64,
    u: u64,
    v: u64,
    vw: u64,
    vws: u64,
    run: u64,
    max_run: u64,
    ne: u64,
    ve: u64,
    vwe: u64,
    run_e: u64,
    max_run_e: u64,
    last_exec: Option<(i64, bool, i64)>,
    held: HashSet<i64>,
}

#[derive(Default)]
pub(super) struct Windows {
    open: [BTreeMap<i64, Acc>; 2],
    max_id: [i64; 2],
    done: Vec<WindowRow>,
}

const KINDS: [WindowKind; 2] = [WindowKind::Minute, WindowKind::Hour];

impl Windows {
    /// Окно открывается на первом событии, его «держимые на начало» тики — те,
    /// что стоят в книге в этот момент (`seed`).
    fn acc(&mut self, k: usize, ms: i64, seed: &mut dyn FnMut() -> HashSet<i64>) -> &mut Acc {
        let id = ms.div_euclid(KINDS[k].len_ms());
        self.max_id[k] = self.max_id[k].max(id);
        self.open[k].entry(id).or_insert_with(|| Acc {
            n: 0,
            u: 0,
            v: 0,
            vw: 0,
            vws: 0,
            run: 0,
            max_run: 0,
            ne: 0,
            ve: 0,
            vwe: 0,
            run_e: 0,
            max_run_e: 0,
            last_exec: None,
            held: seed(),
        });
        let keep_from = self.max_id[k] - 1;
        let stale: Vec<i64> = self.open[k]
            .keys()
            .copied()
            .filter(|i| *i < keep_from && *i != id)
            .collect();
        for i in stale {
            self.close(k, i);
        }
        self.open[k]
            .get_mut(&id)
            .expect("окно только что вставлено")
    }

    fn close(&mut self, k: usize, id: i64) {
        if let Some(a) = self.open[k].remove(&id) {
            if a.n > 0 {
                self.done.push(WindowRow {
                    kind: KINDS[k],
                    id,
                    n: a.n,
                    u: a.u,
                    v: a.v,
                    vw: a.vw,
                    vws: a.vws,
                    max_run: a.max_run,
                    ne: a.ne,
                    ve: a.ve,
                    vwe: a.vwe,
                    max_run_e: a.max_run_e,
                });
            }
        }
    }

    pub(super) fn on_update(
        &mut self,
        ms: i64,
        touched: &[i64],
        seed: &mut dyn FnMut() -> HashSet<i64>,
    ) {
        for k in 0..2 {
            let a = self.acc(k, ms, seed);
            a.u += 1;
            a.held.extend(touched.iter().copied());
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn on_trade(
        &mut self,
        ms: i64,
        tick: i64,
        buy: bool,
        in_range: bool,
        in_spread: bool,
        violation: bool,
        excluded: bool,
        seed: &mut dyn FnMut() -> HashSet<i64>,
    ) {
        for k in 0..2 {
            let a = self.acc(k, ms, seed);
            a.n += 1;
            if violation {
                a.v += 1;
                a.run += 1;
                a.max_run = a.max_run.max(a.run);
            } else {
                a.run = 0;
            }
            let missing = in_range && !excluded && !a.held.contains(&tick);
            let vw = missing && !in_spread;
            if missing && in_spread {
                a.vws += 1;
            }
            if vw {
                a.vw += 1;
            }
            let key = (ms, buy, tick);
            if a.last_exec != Some(key) {
                a.last_exec = Some(key);
                a.ne += 1;
                if violation {
                    a.ve += 1;
                    a.run_e += 1;
                    a.max_run_e = a.max_run_e.max(a.run_e);
                } else {
                    a.run_e = 0;
                }
                if vw {
                    a.vwe += 1;
                }
            }
        }
    }

    /// Закрывает всё и отдаёт строки (по возрастанию вида и номера окна).
    pub(super) fn finish(&mut self) -> Vec<WindowRow> {
        for k in 0..2 {
            let ids: Vec<i64> = self.open[k].keys().copied().collect();
            for i in ids {
                self.close(k, i);
            }
        }
        let mut rows = std::mem::take(&mut self.done);
        rows.sort_by_key(|r| (r.kind == WindowKind::Hour, r.id));
        rows
    }
}
