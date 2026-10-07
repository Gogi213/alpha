//! Двоичный кэш `touches-<SYM>.csv.abin` (TK-048): колонки · delta · zigzag-varint · zstd — как `abin` для
//! подходов. CSV остаётся источником правды: файл годен, только если размер и mtime CSV совпали; при записи
//! проверяется «двоичное → записи == CSV → записи». Включается `ALPHA_TOUCH_BIN=1`; каталог — `ALPHA_APPROACH_BIN_DIR`.

use std::path::Path;

use crate::book::Side;
use crate::lob::levels::TouchRecord;

use super::abin::{bin_path, put, stamp, Rd};
use super::read::{read_touches_csv, TouchRow};

const MAGIC: &[u8; 4] = b"TBN1";
const HEAD: usize = 4 + 8 + 8 + 4 + 1;
const DEFAULT_LEVEL: i32 = 3;

fn enabled() -> bool {
    std::env::var_os("ALPHA_TOUCH_BIN").is_some_and(|v| v == "1")
}

/// `read_touches_csv` через двоичный кэш: годный `.abin` — из него, иначе CSV и запись `.abin`.
pub(crate) fn read_touches_cached(csv: &Path) -> anyhow::Result<Vec<TouchRow>> {
    if !enabled() {
        return read_touches_csv(csv);
    }
    let Some((len, mtime)) = stamp(csv) else {
        return read_touches_csv(csv);
    };
    let bin = bin_path(csv);
    if let Ok(bytes) = std::fs::read(&bin) {
        if let Some(rows) = decode_file(&bytes, len, mtime) {
            return Ok(rows);
        }
    }
    let rows = read_touches_csv(csv)?;
    if let Some(bytes) = encode_file(&rows, len, mtime) {
        if decode_file(&bytes, len, mtime).as_deref() == Some(&rows[..]) {
            if let Some(parent) = bin.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let tmp = bin.with_extension(format!("abin.tmp{}", std::process::id()));
            if std::fs::write(&tmp, &bytes).is_ok() && std::fs::rename(&tmp, &bin).is_err() {
                let _ = std::fs::remove_file(&tmp);
            }
        } else {
            eprintln!(
                "tbin: {} — сверка не сошлась, кэш не записан",
                csv.display()
            );
        }
    }
    Ok(rows)
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
    let mut p = Vec::new();
    put(&mut p, day.len() as i64);
    p.extend_from_slice(day.as_bytes());
    let t = |f: fn(&TouchRecord) -> i64| rows.iter().map(move |r| f(&r.touch));
    let plain = |p: &mut Vec<u8>, it: &mut dyn Iterator<Item = i64>| it.for_each(|v| put(p, v));
    let delta = |p: &mut Vec<u8>, it: &mut dyn Iterator<Item = i64>| {
        let mut prev = 0i64;
        for v in it {
            put(p, v.wrapping_sub(prev));
            prev = v;
        }
    };
    let opt = |p: &mut Vec<u8>, it: &mut dyn Iterator<Item = Option<i64>>| {
        let v: Vec<Option<i64>> = it.collect();
        p.extend(v.iter().map(|x| u8::from(x.is_some())));
        v.into_iter().flatten().for_each(|x| put(p, x));
    };
    p.extend(rows.iter().map(|r| u8::from(r.touch.side == Side::Ask)));
    plain(&mut p, &mut t(|r| r.price_tick));
    plain(&mut p, &mut t(|r| i64::from(r.touch_index)));
    delta(&mut p, &mut t(|r| r.start_ms));
    plain(&mut p, &mut t(|r| r.end_ms.wrapping_sub(r.start_ms)));
    plain(&mut p, &mut t(|r| r.duration_ms));
    plain(
        &mut p,
        &mut t(|r| r.start_ms.wrapping_sub(r.level_birth_ms)),
    );
    plain(&mut p, &mut t(|r| r.size_at_touch));
    plain(&mut p, &mut t(|r| r.size_max_before));
    plain(&mut p, &mut t(|r| r.traded_during));
    plain(&mut p, &mut t(|r| r.frontrun_lots));
    opt(&mut p, &mut rows.iter().map(|r| r.touch.frontrun_tick));
    plain(&mut p, &mut t(|r| r.swept_lots));
    p.extend(rows.iter().map(|r| r.touch.round_zeros));
    p.extend(rows.iter().map(|r| u8::from(r.touch.ended_by_death)));
    plain(&mut p, &mut t(|r| i64::from(r.stack_levels)));
    opt(&mut p, &mut rows.iter().map(|r| r.touch.stack_next_tick));
    plain(&mut p, &mut t(|r| r.traded_first_s[0]));
    plain(&mut p, &mut t(|r| r.traded_first_s[1]));
    plain(&mut p, &mut t(|r| r.traded_first_s[2]));
    plain(&mut p, &mut t(|r| r.flow_1h_lots));
    plain(&mut p, &mut t(|r| r.strength_e2[0]));
    plain(&mut p, &mut t(|r| r.strength_e2[1]));
    plain(&mut p, &mut t(|r| r.strength_e2[2]));
    plain(&mut p, &mut t(|r| r.strength_held_e2[0]));
    plain(&mut p, &mut t(|r| r.strength_held_e2[1]));
    plain(&mut p, &mut t(|r| r.strength_held_e2[2]));
    plain(&mut p, &mut t(|r| r.strength_held_e2[3]));
    plain(&mut p, &mut t(|r| i64::from(r.repeat_count)));
    plain(&mut p, &mut t(|r| r.depth_behind_lots));
    if has_ret {
        for k in 0..3 {
            let col: Vec<Option<f64>> = rows.iter().map(|r| r.ret_bps.unwrap()[k]).collect();
            p.extend(col.iter().map(|x| u8::from(x.is_some())));
            for x in col.into_iter().flatten() {
                p.extend_from_slice(&x.to_bits().to_le_bytes());
            }
        }
    }
    let level = std::env::var("ALPHA_APPROACH_BIN_LEVEL")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(DEFAULT_LEVEL);
    let z = zstd::encode_all(&p[..], level).ok()?;
    let mut out = Vec::with_capacity(HEAD + z.len());
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(&mtime.to_le_bytes());
    out.extend_from_slice(&u32::try_from(rows.len()).ok()?.to_le_bytes());
    out.push(u8::from(has_ret));
    out.extend_from_slice(&z);
    Some(out)
}

pub(super) fn decode_file(b: &[u8], len: u64, mtime: u64) -> Option<Vec<TouchRow>> {
    if b.len() < HEAD || &b[..4] != MAGIC {
        return None;
    }
    if u64::from_le_bytes(b[4..12].try_into().ok()?) != len
        || u64::from_le_bytes(b[12..20].try_into().ok()?) != mtime
    {
        return None;
    }
    let n = u32::from_le_bytes(b[20..24].try_into().ok()?) as usize;
    let has_ret = b[24] == 1;
    let raw = zstd::decode_all(&b[HEAD..]).ok()?;
    let mut r = Rd(&raw, 0);
    let dl = usize::try_from(r.get()?).ok()?;
    let day = std::str::from_utf8(raw.get(r.1..r.1 + dl)?)
        .ok()?
        .to_string();
    r.1 += dl;
    let bytes_col = |r: &mut Rd| -> Option<Vec<u8>> {
        let v = r.0.get(r.1..r.1 + n)?.to_vec();
        r.1 += n;
        Some(v)
    };
    let plain = |r: &mut Rd| -> Option<Vec<i64>> { (0..n).map(|_| r.get()).collect() };
    let delta = |r: &mut Rd| -> Option<Vec<i64>> {
        let mut prev = 0i64;
        (0..n)
            .map(|_| {
                prev = prev.wrapping_add(r.get()?);
                Some(prev)
            })
            .collect()
    };
    let opt = |r: &mut Rd| -> Option<Vec<Option<i64>>> {
        let flags = bytes_col(r)?;
        flags
            .into_iter()
            .map(|f| {
                if f == 1 {
                    r.get().map(Some)
                } else {
                    Some(None)
                }
            })
            .collect()
    };
    let side = bytes_col(&mut r)?;
    let price = plain(&mut r)?;
    let idx = plain(&mut r)?;
    let start = delta(&mut r)?;
    let span = plain(&mut r)?;
    let dur = plain(&mut r)?;
    let age = plain(&mut r)?;
    let size = plain(&mut r)?;
    let smax = plain(&mut r)?;
    let traded = plain(&mut r)?;
    let front = plain(&mut r)?;
    let ftick = opt(&mut r)?;
    let swept = plain(&mut r)?;
    let zeros = bytes_col(&mut r)?;
    let death = bytes_col(&mut r)?;
    let stack = plain(&mut r)?;
    let snext = opt(&mut r)?;
    let tf = [plain(&mut r)?, plain(&mut r)?, plain(&mut r)?];
    let flow = plain(&mut r)?;
    let st = [plain(&mut r)?, plain(&mut r)?, plain(&mut r)?];
    let sh = [
        plain(&mut r)?,
        plain(&mut r)?,
        plain(&mut r)?,
        plain(&mut r)?,
    ];
    let rep = plain(&mut r)?;
    let depth = plain(&mut r)?;
    let mut ret: Vec<Vec<Option<f64>>> = Vec::new();
    if has_ret {
        for _ in 0..3 {
            let flags = bytes_col(&mut r)?;
            let mut col = Vec::with_capacity(n);
            for f in flags {
                col.push(if f == 1 {
                    let w: [u8; 8] = raw.get(r.1..r.1 + 8)?.try_into().ok()?;
                    r.1 += 8;
                    Some(f64::from_bits(u64::from_le_bytes(w)))
                } else {
                    None
                });
            }
            ret.push(col);
        }
    }
    if r.1 != raw.len() {
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
