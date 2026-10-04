//! TK-044: вброс заведомой порчи в суточный binlog — проверка гейта В-177 на известной истине.
//! `tk044_inject <вход> <выход> drop-random|drop-run|shift-tick|splice [<чужой.binlog> <час 0..23>]`

#![allow(clippy::indexing_slicing)]

use std::env;
use std::fs::File;
use std::io::{BufReader, BufWriter};

use alpha::binlog::{Reader, Record, Writer};
use hftbacktest::types::{LOCAL_ASK_DEPTH_EVENT, LOCAL_BID_DEPTH_EVENT};

const DAY_NS: i64 = 86_400_000_000_000;
const HOUR_NS: i64 = 3_600_000_000_000;

fn is_delta(r: &Record) -> bool {
    r.ev == LOCAL_BID_DEPTH_EVENT || r.ev == LOCAL_ASK_DEPTH_EVENT
}

fn read_all(
    path: &str,
) -> (
    alpha::binlog::Header,
    Vec<alpha::binlog::StepAt>,
    Vec<Vec<Record>>,
) {
    let mut rd = Reader::open(BufReader::new(File::open(path).expect("open"))).expect("header");
    let (h, s) = (rd.header(), rd.step_schedule().to_vec());
    let mut frames = Vec::new();
    while let Some(f) = rd.read_frame().expect("frame") {
        frames.push(f);
    }
    (h, s, frames)
}

fn main() {
    let a: Vec<String> = env::args().collect();
    let (h, sched, mut frames) = read_all(&a[1]);
    let mode = a[3].as_str();
    let all: Vec<Record> = frames.iter().flatten().copied().collect();
    let mid_ts = all[all.len() / 2].exch_ts_ns;
    let day0 = all[0].exch_ts_ns.div_euclid(DAY_NS) * DAY_NS;
    let mut dropped = 0u64;
    match mode {
        "drop-random" => {
            // каждая 1000-я дельта (0,1 %), детерминированно
            let mut k = 0u64;
            for f in &mut frames {
                f.retain(|r| {
                    if !is_delta(r) {
                        return true;
                    }
                    k += 1;
                    let drop = k.is_multiple_of(1000);
                    dropped += u64::from(drop);
                    !drop
                });
            }
        }
        "drop-run" => {
            // 20 дельт подряд с середины суток
            let mut left = 20;
            for f in &mut frames {
                f.retain(|r| {
                    if r.exch_ts_ns < mid_ts || !is_delta(r) || left == 0 {
                        return true;
                    }
                    left -= 1;
                    dropped += 1;
                    false
                });
            }
        }
        "shift-tick" => {
            // цена каждой дельты в 10 минутах с середины суток сдвинута на +1 тик
            for f in &mut frames {
                for r in f.iter_mut() {
                    if is_delta(r) && r.exch_ts_ns >= mid_ts && r.exch_ts_ns < mid_ts + HOUR_NS / 6
                    {
                        r.price_ticks += 1;
                        dropped += 1;
                    }
                }
            }
        }
        "splice" => {
            // записи часа из чужих суток (метки сдвинуты на целое число суток) вместо родных
            let hour: i64 = a[5].parse().expect("час");
            let (_, _, other) = read_all(&a[4]);
            let o0 = other[0][0].exch_ts_ns.div_euclid(DAY_NS) * DAY_NS;
            let (lo, hi) = (day0 + hour * HOUR_NS, day0 + (hour + 1) * HOUR_NS);
            let shift = day0 - o0;
            let mut foreign: Vec<Vec<Record>> = other
                .into_iter()
                .map(|f| {
                    f.into_iter()
                        .filter(|r| (lo - shift..hi - shift).contains(&r.exch_ts_ns))
                        .map(|mut r| {
                            r.exch_ts_ns += shift;
                            r.local_ts_ns += shift;
                            r
                        })
                        .collect::<Vec<_>>()
                })
                .filter(|f| !f.is_empty())
                .collect();
            let mut out: Vec<Vec<Record>> = Vec::new();
            let mut placed = false;
            for f in frames {
                let keep: Vec<Record> = f
                    .iter()
                    .copied()
                    .filter(|r| !(lo..hi).contains(&r.exch_ts_ns))
                    .collect();
                dropped += (f.len() - keep.len()) as u64;
                if keep.len() != f.len() && !placed {
                    out.push(keep);
                    out.append(&mut foreign);
                    placed = true;
                } else if !keep.is_empty() {
                    out.push(keep);
                }
            }
            frames = out;
        }
        _ => panic!("режим"),
    }
    let w = BufWriter::new(File::create(&a[2]).expect("create"));
    let mut wr = if sched.is_empty() {
        Writer::create(w, h, 3)
    } else {
        Writer::create_with_schedule(w, h, &sched, 3)
    }
    .expect("writer");
    for f in &frames {
        wr.write_frame(f).expect("write");
    }
    wr.flush().expect("flush");
    eprintln!("{mode}: изменено/убрано записей {dropped}");
}
