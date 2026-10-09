//! Двоичный кэш `approaches-<SYM>.csv.abin` (TK-052): колонки · delta · zigzag-varint · zstd.
//! CSV остаётся источником правды: файл годен, только если размер и mtime CSV совпали; при
//! подготовке записи проверяются «двоичное → записи == CSV → записи». Включается `ALPHA_APPROACH_BIN=1`.

use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use crate::book::Side;
use crate::lob::levels::{ApproachEnd, ApproachRecord, ArmP08};

use super::read::{read_approaches_csv, ApproachRow};

const MAGIC: &[u8; 4] = b"ABN1";
const HEAD: usize = 4 + 8 + 8 + 4 + 1;
const DEFAULT_LEVEL: i32 = 3;

pub(crate) fn enabled() -> bool {
    std::env::var_os("ALPHA_APPROACH_BIN").is_some_and(|v| v == "1")
}

/// Рядом с CSV; при `ALPHA_APPROACH_BIN_DIR` — в зеркале пути CSV под этим каталогом (входное дерево не трогаем).
pub(super) fn bin_path(csv: &Path) -> PathBuf {
    let mut s = match std::env::var_os("ALPHA_APPROACH_BIN_DIR") {
        Some(dir) => {
            let rel: PathBuf = csv
                .components()
                .filter(|c| matches!(c, std::path::Component::Normal(_)))
                .collect();
            PathBuf::from(dir).join(rel).into_os_string()
        }
        None => csv.as_os_str().to_owned(),
    };
    s.push(".abin");
    PathBuf::from(s)
}

/// Штамп файла «размер + mtime в нс» — общий для кэшей `.abin`/`.tbin` и сайдкаров счёта событий.
pub(crate) fn stamp(csv: &Path) -> Option<(u64, u64)> {
    let m = std::fs::metadata(csv).ok()?;
    let t = m.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
    Some((
        m.len(),
        t.as_secs() * 1_000_000_000 + u64::from(t.subsec_nanos()),
    ))
}

/// `read_approaches_csv` через двоичный кэш: годный `.abin` — из него, иначе CSV и запись `.abin`.
pub(crate) fn read_approaches_cached(csv: &Path) -> anyhow::Result<Vec<ApproachRow>> {
    if !enabled() {
        return read_approaches_csv(csv);
    }
    let Some((len, mtime)) = stamp(csv) else {
        return read_approaches_csv(csv);
    };
    let bin = bin_path(csv);
    if let Ok(bytes) = std::fs::read(&bin) {
        if let Some(rows) = decode_file(&bytes, len, mtime) {
            return Ok(rows);
        }
    }
    let rows = read_approaches_csv(csv)?;
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
                "abin: {} — сверка не сошлась, кэш не записан",
                csv.display()
            );
        }
    }
    Ok(rows)
}

pub(super) fn put(out: &mut Vec<u8>, v: i64) {
    let mut z = ((v << 1) ^ (v >> 63)) as u64;
    while z >= 0x80 {
        out.push((z as u8) | 0x80);
        z >>= 7;
    }
    out.push(z as u8);
}

pub(super) struct Rd<'a>(pub(super) &'a [u8], pub(super) usize);

impl Rd<'_> {
    pub(super) fn get(&mut self) -> Option<i64> {
        let mut z = 0u64;
        let mut shift = 0;
        loop {
            let b = *self.0.get(self.1)?;
            self.1 += 1;
            z |= u64::from(b & 0x7f).checked_shl(shift)?;
            if b < 0x80 {
                break;
            }
            shift += 7;
        }
        Some(((z >> 1) as i64) ^ -((z & 1) as i64))
    }
}

pub(super) fn encode_file(rows: &[ApproachRow], len: u64, mtime: u64) -> Option<Vec<u8>> {
    let day = &rows.first()?.day;
    if rows.iter().any(|r| &r.day != day) || rows.iter().any(|r| r.approach.r1.is_some()) {
        return None;
    }
    let has_p08 = rows[0].approach.p08.is_some();
    if rows.iter().any(|r| r.approach.p08.is_some() != has_p08) {
        return None;
    }
    if rows
        .iter()
        .any(|r| r.approach.touch_start_ms.is_some_and(|t| t < 0))
    {
        return None;
    }
    let a = |f: fn(&ApproachRecord) -> i64| rows.iter().map(move |r| f(&r.approach));
    let mut p = Vec::new();
    put(&mut p, day.len() as i64);
    p.extend_from_slice(day.as_bytes());
    let plain = |p: &mut Vec<u8>, it: &mut dyn Iterator<Item = i64>| it.for_each(|v| put(p, v));
    let delta = |p: &mut Vec<u8>, it: &mut dyn Iterator<Item = i64>| {
        let mut prev = 0;
        for v in it {
            put(p, v - prev);
            prev = v;
        }
    };
    p.extend(rows.iter().map(|r| u8::from(r.approach.side == Side::Ask)));
    plain(&mut p, &mut a(|r| r.price_tick));
    plain(&mut p, &mut a(|r| i64::from(r.approach_index)));
    delta(&mut p, &mut a(|r| r.arm_ms));
    plain(&mut p, &mut a(|r| r.arm_dist_bps));
    delta(&mut p, &mut a(|r| r.level_birth_ms));
    plain(&mut p, &mut a(|r| r.size_at_arm));
    plain(&mut p, &mut a(|r| r.best_own_tick));
    plain(&mut p, &mut a(|r| r.best_opp_tick));
    plain(&mut p, &mut a(|r| r.flow_1h_lots));
    plain(&mut p, &mut a(|r| r.strength_e2[0]));
    plain(&mut p, &mut a(|r| r.strength_e2[1]));
    plain(&mut p, &mut a(|r| r.strength_e2[2]));
    plain(&mut p, &mut a(|r| r.depth_behind_lots));
    plain(&mut p, &mut a(|r| i64::from(r.stack_levels_at_arm)));
    plain(&mut p, &mut a(|r| r.frontrun_lots_at_arm));
    if has_p08 {
        let q = |f: fn(&ArmP08) -> i64| rows.iter().map(move |r| f(&r.approach.p08.unwrap()));
        plain(&mut p, &mut q(|x| x.traded_lots));
        plain(&mut p, &mut q(|x| x.size_max));
        plain(&mut p, &mut q(|x| i64::from(x.size_monotonic)));
        plain(&mut p, &mut q(|x| x.eat_60s_lots));
        plain(&mut p, &mut q(|x| x.size_max_60s_lots));
        plain(&mut p, &mut q(|x| x.depth_behind50_lots));
    }
    delta(&mut p, &mut a(|r| r.touch_start_ms.unwrap_or(-1)));
    delta(&mut p, &mut a(|r| r.disarm_ms));
    p.extend(rows.iter().map(|r| match r.approach.disarm_reason {
        ApproachEnd::Touch => 0u8,
        ApproachEnd::LevelDeath => 1,
        ApproachEnd::PriceLeft => 2,
    }));
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
    out.push(u8::from(has_p08));
    out.extend_from_slice(&z);
    Some(out)
}

pub(super) fn decode_file(b: &[u8], len: u64, mtime: u64) -> Option<Vec<ApproachRow>> {
    if b.len() < HEAD || &b[..4] != MAGIC {
        return None;
    }
    if u64::from_le_bytes(b[4..12].try_into().ok()?) != len
        || u64::from_le_bytes(b[12..20].try_into().ok()?) != mtime
    {
        return None;
    }
    let n = u32::from_le_bytes(b[20..24].try_into().ok()?) as usize;
    let has_p08 = b[24] == 1;
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
        let mut prev = 0;
        (0..n)
            .map(|_| {
                prev += r.get()?;
                Some(prev)
            })
            .collect()
    };
    let side = bytes_col(&mut r)?;
    let price = plain(&mut r)?;
    let idx = plain(&mut r)?;
    let arm = delta(&mut r)?;
    let dist = plain(&mut r)?;
    let birth = delta(&mut r)?;
    let size = plain(&mut r)?;
    let own = plain(&mut r)?;
    let opp = plain(&mut r)?;
    let flow = plain(&mut r)?;
    let s0 = plain(&mut r)?;
    let s1 = plain(&mut r)?;
    let s2 = plain(&mut r)?;
    let depth = plain(&mut r)?;
    let stack = plain(&mut r)?;
    let front = plain(&mut r)?;
    let p08 = if has_p08 {
        Some([
            plain(&mut r)?,
            plain(&mut r)?,
            plain(&mut r)?,
            plain(&mut r)?,
            plain(&mut r)?,
            plain(&mut r)?,
        ])
    } else {
        None
    };
    let tstart = delta(&mut r)?;
    let disarm = delta(&mut r)?;
    let reason = bytes_col(&mut r)?;
    if r.1 != raw.len() {
        return None;
    }
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        out.push(ApproachRow {
            day: day.clone(),
            approach: ApproachRecord {
                side: if side[i] == 1 { Side::Ask } else { Side::Bid },
                price_tick: price[i],
                approach_index: u32::try_from(idx[i]).ok()?,
                arm_ms: arm[i],
                arm_dist_bps: dist[i],
                level_birth_ms: birth[i],
                size_at_arm: size[i],
                best_own_tick: own[i],
                best_opp_tick: opp[i],
                flow_1h_lots: flow[i],
                strength_e2: [s0[i], s1[i], s2[i]],
                depth_behind_lots: depth[i],
                stack_levels_at_arm: u32::try_from(stack[i]).ok()?,
                frontrun_lots_at_arm: front[i],
                p08: p08.as_ref().map(|c| ArmP08 {
                    traded_lots: c[0][i],
                    size_max: c[1][i],
                    size_monotonic: c[2][i] != 0,
                    eat_60s_lots: c[3][i],
                    size_max_60s_lots: c[4][i],
                    depth_behind50_lots: c[5][i],
                }),
                r1: None,
                touch_start_ms: (tstart[i] >= 0).then_some(tstart[i]),
                disarm_ms: disarm[i],
                disarm_reason: match reason[i] {
                    0 => ApproachEnd::Touch,
                    1 => ApproachEnd::LevelDeath,
                    2 => ApproachEnd::PriceLeft,
                    _ => return None,
                },
            },
        });
    }
    Some(out)
}
