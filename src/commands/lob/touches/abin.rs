//! Кодек `.abin` подходов (`approaches-<SYM>.csv`): колонки в порядке `ApproachRecord`; каркас — `bincache`.

use std::path::Path;

use crate::book::Side;
use crate::lob::levels::{ApproachEnd, ApproachRecord, ArmP08};

use super::bincache::{put_day, put_delta, put_plain, read_cached, unwrap, wrap, Rd};
use super::read::{read_approaches_csv, ApproachRow};

pub(super) const MAGIC: &[u8; 4] = b"ABN3";

/// `read_approaches_csv` через двоичный кэш (`bincache::read_cached`).
pub(crate) fn read_approaches_cached(csv: &Path) -> anyhow::Result<Vec<ApproachRow>> {
    read_cached(csv, "abin", read_approaches_csv, encode_file, decode_file)
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
    put_day(&mut p, day);
    p.extend(rows.iter().map(|r| u8::from(r.approach.side == Side::Ask)));
    put_plain(&mut p, a(|r| r.price_tick));
    put_plain(&mut p, a(|r| i64::from(r.approach_index)));
    put_delta(&mut p, a(|r| r.arm_ms));
    put_plain(&mut p, a(|r| r.arm_dist_bps));
    put_delta(&mut p, a(|r| r.level_birth_ms));
    put_plain(&mut p, a(|r| r.size_at_arm));
    put_plain(&mut p, a(|r| r.best_own_tick));
    put_plain(&mut p, a(|r| r.best_opp_tick));
    put_plain(&mut p, a(|r| r.flow_1h_lots));
    put_plain(&mut p, a(|r| r.strength_e2[0]));
    put_plain(&mut p, a(|r| r.strength_e2[1]));
    put_plain(&mut p, a(|r| r.strength_e2[2]));
    put_plain(&mut p, a(|r| r.depth_behind_lots));
    put_plain(&mut p, a(|r| i64::from(r.stack_levels_at_arm)));
    put_plain(&mut p, a(|r| r.frontrun_lots_at_arm));
    put_plain(&mut p, a(|r| r.stack_span_bps_at_arm));
    put_plain(&mut p, a(|r| i64::from(r.repeat_count_at_arm)));
    if has_p08 {
        let q = |f: fn(&ArmP08) -> i64| rows.iter().map(move |r| f(&r.approach.p08.unwrap()));
        put_plain(&mut p, q(|x| x.traded_lots));
        put_plain(&mut p, q(|x| x.size_max));
        put_plain(&mut p, q(|x| i64::from(x.size_monotonic)));
        put_plain(&mut p, q(|x| x.eat_60s_lots));
        put_plain(&mut p, q(|x| x.size_max_60s_lots));
        put_plain(&mut p, q(|x| x.depth_behind50_lots));
    }
    put_delta(&mut p, a(|r| r.touch_start_ms.unwrap_or(-1)));
    put_delta(&mut p, a(|r| r.disarm_ms));
    p.extend(rows.iter().map(|r| match r.approach.disarm_reason {
        ApproachEnd::Touch => 0u8,
        ApproachEnd::LevelDeath => 1,
        ApproachEnd::PriceLeft => 2,
    }));
    wrap(MAGIC, (len, mtime), rows.len(), has_p08, &p)
}

pub(super) fn decode_file(b: &[u8], len: u64, mtime: u64) -> Option<Vec<ApproachRow>> {
    let (n, has_p08, raw) = unwrap(b, MAGIC, (len, mtime))?;
    let mut r = Rd::new(&raw, n);
    let day = r.day()?;
    let side = r.bytes()?;
    let price = r.plain()?;
    let idx = r.plain()?;
    let arm = r.delta()?;
    let dist = r.plain()?;
    let birth = r.delta()?;
    let size = r.plain()?;
    let own = r.plain()?;
    let opp = r.plain()?;
    let flow = r.plain()?;
    let s0 = r.plain()?;
    let s1 = r.plain()?;
    let s2 = r.plain()?;
    let depth = r.plain()?;
    let stack = r.plain()?;
    let front = r.plain()?;
    let span = r.plain()?;
    let repeat = r.plain()?;
    let p08 = if has_p08 {
        Some([
            r.plain()?,
            r.plain()?,
            r.plain()?,
            r.plain()?,
            r.plain()?,
            r.plain()?,
        ])
    } else {
        None
    };
    let tstart = r.delta()?;
    let disarm = r.delta()?;
    let reason = r.bytes()?;
    if !r.done() {
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
                stack_span_bps_at_arm: span[i],
                repeat_count_at_arm: u32::try_from(repeat[i]).ok()?,
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
