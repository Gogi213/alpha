//! Черновой замер: расходятся ли потоки глубины `.50` и `.200` между собой.
//!
//! Берёт два файла одной монеты за один и тот же период (быстрый и глубокий), строит
//! **две отдельные книги** и продвигает их по биржевому времени (`exch_ts`), применяя
//! сообщения строго в порядке метки. В контрольных точках (раз в секунду биржевого
//! времени) сравнивает верх книг: лучший бид и лучший аск.
//!
//! Что считать дрейфом: если потоки описывают одну и ту же книгу, верх обязан
//! совпадать; систематическое расхождение, растущее со временем, — это дрейф.
//! Разовое расхождение в пределах задержки публикации (`.200` отдаёт пачками раз в
//! ~100 мс) — норма, поэтому отдельно печатается отставание глубокого потока.
//!
//! Запуск: `cargo run --release --example stream_drift -- <быстрый.binlog> <глубокий.binlog>`

#![allow(clippy::indexing_slicing, clippy::cast_precision_loss)]

use std::collections::BTreeMap;
use std::env;
use std::fs::File;

use alpha::binlog::Reader;
use hftbacktest::types::{
    LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT, LOCAL_BID_DEPTH_EVENT,
    LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
};

#[derive(Clone, Copy)]
struct Msg {
    ts_ns: i64,
    ev: u64,
    price: i64,
    qty: i64,
}

impl Msg {
    fn is_bid(&self) -> Option<bool> {
        match self.ev {
            LOCAL_BID_DEPTH_EVENT | LOCAL_BID_DEPTH_SNAPSHOT_EVENT => Some(true),
            LOCAL_ASK_DEPTH_EVENT | LOCAL_ASK_DEPTH_SNAPSHOT_EVENT => Some(false),
            _ => None,
        }
    }

    fn is_snapshot(&self) -> bool {
        matches!(
            self.ev,
            LOCAL_BID_DEPTH_SNAPSHOT_EVENT | LOCAL_ASK_DEPTH_SNAPSHOT_EVENT
        )
    }
}

#[derive(Default)]
struct Book {
    bids: BTreeMap<i64, i64>,
    asks: BTreeMap<i64, i64>,
    snapshot_open: bool,
}

impl Book {
    fn apply(&mut self, m: &Msg) {
        let Some(bid) = m.is_bid() else { return };
        if m.is_snapshot() && !self.snapshot_open {
            self.bids.clear();
            self.asks.clear();
            self.snapshot_open = true;
        } else if !m.is_snapshot() {
            self.snapshot_open = false;
        }
        let side = if bid { &mut self.bids } else { &mut self.asks };
        if m.qty == 0 {
            side.remove(&m.price);
        } else {
            side.insert(m.price, m.qty);
        }
    }

    fn top(&self) -> Option<(i64, i64, i64, i64)> {
        let (&bb, &bq) = self.bids.iter().next_back()?;
        let (&ba, &aq) = self.asks.iter().next()?;
        Some((bb, bq, ba, aq))
    }
}

fn load(path: &str) -> Result<Vec<Msg>, Box<dyn std::error::Error>> {
    let mut reader = Reader::open(File::open(path)?)?;
    let mut out = Vec::new();
    while let Some(frame) = reader.read_frame()? {
        for r in &frame {
            out.push(Msg {
                ts_ns: r.exch_ts_ns,
                ev: r.ev,
                price: r.price_ticks,
                qty: r.qty_lots,
            });
        }
    }
    Ok(out)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().skip(1).collect();
    let (fast_path, deep_path) = (
        args.first()
            .expect("usage: stream_drift <fast.binlog> <deep.binlog>"),
        args.get(1)
            .expect("usage: stream_drift <fast.binlog> <deep.binlog>"),
    );
    let fast = load(fast_path)?;
    let deep = load(deep_path)?;
    println!(
        "быстрый: {} записей, глубокий: {} записей",
        fast.len(),
        deep.len()
    );

    let (mut fb, mut db) = (Book::default(), Book::default());
    let (mut i, mut j) = (0usize, 0usize);
    let (mut checks, mut agree_both) = (0u64, 0u64);
    let (mut agree_bid, mut agree_ask) = (0u64, 0u64);
    let (mut max_dbid, mut max_dask) = (0i64, 0i64);
    let (mut small_dbid, mut small_dask) = (0u64, 0u64);
    let mut big = (0u64, 0i64);
    let mut examples: Vec<String> = Vec::new();
    // Согласие по четвертям окна: дрейф проявлялся бы падением согласия со временем.
    let t_first = deep.first().map(|m| m.ts_ns).unwrap_or(0);
    let t_last = deep.last().map(|m| m.ts_ns).unwrap_or(0);
    let span = (t_last - t_first).max(1);
    let mut quarter = [(0u64, 0u64); 4];

    // Сравнение на метке глубокого сообщения: быструю книгу подтягиваем ровно к
    // этому времени (`< = t_deep`) и не дальше, иначе расхождение объяснялось бы
    // просто тем, что быстрый поток ушёл вперёд. Так обе книги описывают состояние
    // на один и тот же биржевой момент, и любое расхождение — настоящее.
    while j < deep.len() {
        let t_deep = deep[j].ts_ns;
        while i < fast.len() && fast[i].ts_ns <= t_deep {
            fb.apply(&fast[i]);
            i += 1;
        }
        // Вся пачка глубокого сообщения — целиком: сравнивать внутри пачки
        // нельзя, там книга обновлена наполовину (записи одного сообщения
        // биржи несут одну метку).
        while j < deep.len() && deep[j].ts_ns == t_deep {
            db.apply(&deep[j]);
            j += 1;
        }

        let (Some((fbb, _fbq, fba, _)), Some((dbb, _dbq, dba, _))) = (fb.top(), db.top()) else {
            continue;
        };
        checks += 1;
        let dbid = fbb - dbb;
        let dask = fba - dba;
        let q = (((t_deep - t_first) * 4 / span) as usize).min(3);
        quarter[q].1 += 1;
        if dbid == 0 && dask == 0 {
            quarter[q].0 += 1;
            agree_both += 1;
        } else {
            if dbid == 0 {
                agree_bid += 1;
            }
            if dask == 0 {
                agree_ask += 1;
            }
            if dbid.abs() <= 1 && dask.abs() <= 1 {
                if dbid.abs() == 1 {
                    small_dbid += 1;
                }
                if dask.abs() == 1 {
                    small_dask += 1;
                }
            } else {
                big.0 += 1;
                big.1 = big.1.max(dbid.abs()).max(dask.abs());
                if examples.len() < 5 {
                    examples.push(format!(
                        "  t={t_deep} расхождение: быстрый {fbb}/{fba} против глубокий {dbb}/{dba} (Δбид={dbid}, Δаск={dask})"
                    ));
                }
            }
            max_dbid = max_dbid.max(dbid.abs());
            max_dask = max_dask.max(dask.abs());
        }
    }

    let pct = |n: u64| 100.0 * n as f64 / checks.max(1) as f64;
    println!("сравнений по метке глубокого потока: {checks}");
    println!(
        "верх совпал полностью: {agree_both} ({:.2} %); бид совпал: {} ({:.2} %); аск совпал: {} ({:.2} %)",
        pct(agree_both),
        agree_bid + agree_both,
        pct(agree_bid + agree_both),
        agree_ask + agree_both,
        pct(agree_ask + agree_both)
    );
    println!(
        "расхождение ≤1 тика: бид {small_dbid}, аск {small_dask}; «крупных» (>1 тика): {} (максимум {} тиков)",
        big.0, big.1
    );
    println!("максимальное расхождение: бид {max_dbid} тиков, аск {max_dask} тиков");
    println!("согласие по четвертям окна:");
    for (k, (ok, total)) in quarter.iter().enumerate() {
        println!(
            "  четверть {}: {:.2} % ({ok} из {total})",
            k + 1,
            100.0 * *ok as f64 / (*total).max(1) as f64
        );
    }
    for e in examples {
        println!("{e}");
    }
    Ok(())
}
