//! Кодек `.abin` касаний (`touches-<SYM>.csv`): колонки в порядке `TouchRecord`; каркас — `bincache`.

use std::path::Path;

use crate::book::Side;
use crate::lob::levels::TouchRecord;

use super::bincache::{put, put_day, put_delta, put_plain, read_cached, unwrap, wrap, Rd};
use super::read::{read_touches_csv, TouchRow};

const MAGIC: &[u8; 4] = b"TBN1";

/// `read_touches_csv` через двоичный кэш (`bincache::read_cached`).
pub(crate) fn read_touches_cached(csv: &Path) -> anyhow::Result<Vec<TouchRow>> {
    read_cached(csv, "tbin", read_touches_csv, encode_file, decode_file)
}

pub(super) fn encode_file(rows: &[TouchRow], len: u64, mtime: u64) -> Option<Vec<u8>> {
    let day = &rows.first()?.day;
    if rows.iter().any(|r| &r.day != day) {
        return None;
    }
    let has_ret = rows[0].ret_bps.is_some();
    if rows.iter().any(|r| r.ret_bps.is_some() != has_ret) {
        return None;
    }
    let t = |f: fn(&TouchRecord) -> i64| rows.iter().map(move |r| f(&r.touch));
    let opt = |p: &mut Vec<u8>, it: &mut dyn Iterator<Item = Option<i64>>| {
        let v: Vec<Option<i64>> = it.collect();
        p.extend(v.iter().map(|x| u8::from(x.is_some())));
        v.into_iter().flatten().for_each(|x| put(p, x));
    };
    let mut p = Vec::new();
    put_day(&mut p, day);
    p.extend(rows.iter().map(|r| u8::from(r.touch.side == Side::Ask)));
    put_plain(&mut p, t(|r| r.price_tick));
    put_plain(&mut p, t(|r| i64::from(r.touch_index)));
    put_delta(&mut p, t(|r| r.start_ms));
    put_plain(&mut p, t(|r| r.end_ms.wrapping_sub(r.start_ms)));
    put_plain(&mut p, t(|r| r.duration_ms));
    put_plain(&mut p, t(|r| r.start_ms.wrapping_sub(r.level_birth_ms)));
    put_plain(&mut p, t(|r| r.size_at_touch));
    put_plain(&mut p, t(|r| r.size_max_before));
    put_plain(&mut p, t(|r| r.traded_during));
    put_plain(&mut p, t(|r| r.frontrun_lots));
    opt(&mut p, &mut rows.iter().map(|r| r.touch.frontrun_tick));
    put_plain(&mut p, t(|r| r.swept_lots));
    p.extend(rows.iter().map(|r| r.touch.round_zeros));
    p.extend(rows.iter().map(|r| u8::from(r.touch.ended_by_death)));
    put_plain(&mut p, t(|r| i64::from(r.stack_levels)));
    opt(&mut p, &mut rows.iter().map(|r| r.touch.stack_next_tick));
    put_plain(&mut p, t(|r| r.traded_first_s[0]));
    put_plain(&mut p, t(|r| r.traded_first_s[1]));
    put_plain(&mut p, t(|r| r.traded_first_s[2]));
    put_plain(&mut p, t(|r| r.flow_1h_lots));
    put_plain(&mut p, t(|r| r.strength_e2[0]));
    put_plain(&mut p, t(|r| r.strength_e2[1]));
    put_plain(&mut p, t(|r| r.strength_e2[2]));
    put_plain(&mut p, t(|r| r.strength_held_e2[0]));
    put_plain(&mut p, t(|r| r.strength_held_e2[1]));
    put_plain(&mut p, t(|r| r.strength_held_e2[2]));
    put_plain(&mut p, t(|r| r.strength_held_e2[3]));
    put_plain(&mut p, t(|r| i64::from(r.repeat_count)));
    put_plain(&mut p, t(|r| r.depth_behind_lots));
    if has_ret {
        for k in 0..3 {
            let col: Vec<Option<f64>> = rows.iter().map(|r| r.ret_bps.unwrap()[k]).collect();
            p.extend(col.iter().map(|x| u8::from(x.is_some())));
            for x in col.into_iter().flatten() {
                p.extend_from_slice(&x.to_bits().to_le_bytes());
            }
        }
    }
    wrap(MAGIC, (len, mtime), rows.len(), has_ret, &p)
}

pub(super) fn decode_file(b: &[u8], len: u64, mtime: u64) -> Option<Vec<TouchRow>> {
    let (n, has_ret, raw) = unwrap(b, MAGIC, (len, mtime))?;
    let mut r = Rd::new(&raw, n);
    let day = r.day()?;
    let side = r.bytes()?;
    let price = r.plain()?;
    let idx = r.plain()?;
    let start = r.delta()?;
    let span = r.plain()?;
    let dur = r.plain()?;
    let age = r.plain()?;
    let size = r.plain()?;
    let smax = r.plain()?;
    let traded = r.plain()?;
    let front = r.plain()?;
    let ftick = r.opt()?;
    let swept = r.plain()?;
    let zeros = r.bytes()?;
    let death = r.bytes()?;
    let stack = r.plain()?;
    let snext = r.opt()?;
    let tf = [r.plain()?, r.plain()?, r.plain()?];
    let flow = r.plain()?;
    let st = [r.plain()?, r.plain()?, r.plain()?];
    let sh = [r.plain()?, r.plain()?, r.plain()?, r.plain()?];
    let rep = r.plain()?;
    let depth = r.plain()?;
    let mut ret: Vec<Vec<Option<f64>>> = Vec::new();
    if has_ret {
        for _ in 0..3 {
            let flags = r.bytes()?;
            let mut col = Vec::with_capacity(n);
            for f in flags {
                col.push(if f == 1 {
                    let w: [u8; 8] = raw.get(r.pos..r.pos + 8)?.try_into().ok()?;
                    r.pos += 8;
                    Some(f64::from_bits(u64::from_le_bytes(w)))
                } else {
                    None
                });
            }
            ret.push(col);
        }
    }
    if !r.done() {
        return None;
    }
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        out.push(TouchRow {
            day: day.clone(),
            touch: TouchRecord {
                side: if side[i] == 1 { Side::Ask } else { Side::Bid },
                price_tick: price[i],
                touch_index: u32::try_from(idx[i]).ok()?,
                start_ms: start[i],
                end_ms: start[i].wrapping_add(span[i]),
                duration_ms: dur[i],
                level_birth_ms: start[i].wrapping_sub(age[i]),
                size_at_touch: size[i],
                size_max_before: smax[i],
                traded_during: traded[i],
                frontrun_lots: front[i],
                frontrun_tick: ftick[i],
                swept_lots: swept[i],
                round_zeros: zeros[i],
                ended_by_death: death[i] == 1,
                stack_levels: u32::try_from(stack[i]).ok()?,
                stack_next_tick: snext[i],
                traded_first_s: [tf[0][i], tf[1][i], tf[2][i]],
                flow_1h_lots: flow[i],
                strength_e2: [st[0][i], st[1][i], st[2][i]],
                strength_held_e2: [sh[0][i], sh[1][i], sh[2][i], sh[3][i]],
                repeat_count: u32::try_from(rep[i]).ok()?,
                depth_behind_lots: depth[i],
            },
            ret_bps: has_ret.then(|| [ret[0][i], ret[1][i], ret[2][i]]),
        });
    }
    Some(out)
}
