//! Счётчики группировки кругов (`ALPHA_GROUP_STATS=1`, TK-049/TK-053); на результат не влияют, умолчание — выкл.

use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::OnceLock;

pub const NAMES: [&str; 36] = [
    "eg_calls",
    "eg_forms",
    "sigs",
    "t0s",
    "memo_preskip",
    "parts",
    "sz1",
    "sz2",
    "sz3",
    "sz4",
    "sz5_8",
    "sz9_16",
    "sz17p",
    "split_t0",
    "split_sigma",
    "split_qty",
    "split_entry",
    "groups_run",
    "group_members",
    "group_none",
    "group_none_members",
    "stored",
    "not_submitted",
    "unclean_eod_step",
    "unclean_eod_outcome",
    "unclean_residual",
    "dist_sum",
    "dist_members",
    "groups_allsame",
    "memo_hits",
    "memo_misses",
    "solo_rounds",
    "fast_swapped",
    "grp_ns",
    "grp_hold_ns",
    "solo_ns",
];
pub const EG_CALLS: usize = 0;
pub const EG_FORMS: usize = 1;
pub const SIGS: usize = 2;
pub const T0S: usize = 3;
pub const MEMO_PRESKIP: usize = 4;
pub const PARTS: usize = 5;
pub const SZ1: usize = 6;
pub const SPLIT_T0: usize = 13;
pub const SPLIT_SIGMA: usize = 14;
pub const SPLIT_QTY: usize = 15;
pub const SPLIT_ENTRY: usize = 16;
pub const GROUPS_RUN: usize = 17;
pub const GROUP_MEMBERS: usize = 18;
pub const GROUP_NONE: usize = 19;
pub const GROUP_NONE_MEMBERS: usize = 20;
pub const STORED: usize = 21;
pub const NOT_SUBMITTED: usize = 22;
pub const UNCLEAN_EOD_STEP: usize = 23;
pub const UNCLEAN_EOD_OUTCOME: usize = 24;
pub const UNCLEAN_RESIDUAL: usize = 25;
pub const DIST_SUM: usize = 26;
pub const DIST_MEMBERS: usize = 27;
pub const GROUPS_ALLSAME: usize = 28;
pub const MEMO_HITS: usize = 29;
pub const MEMO_MISSES: usize = 30;
pub const SOLO_ROUNDS: usize = 31;
pub const FAST_SWAPPED: usize = 32;
pub const GRP_NS: usize = 33;
pub const GRP_HOLD_NS: usize = 34;
pub const SOLO_NS: usize = 35;

static C: [AtomicU64; 36] = [const { AtomicU64::new(0) }; 36];

pub fn on() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_GROUP_STATS").is_some())
}

pub fn add(i: usize, n: u64) {
    if on() {
        C[i].fetch_add(n, Relaxed);
    }
}

/// Старт таймера (только при включённых счётчиках).
pub fn t0() -> Option<std::time::Instant> {
    on().then(std::time::Instant::now)
}

pub fn add_ns(i: usize, t: Option<std::time::Instant>) {
    if let Some(t) = t {
        C[i].fetch_add(
            u64::try_from(t.elapsed().as_nanos()).unwrap_or(u64::MAX),
            Relaxed,
        );
    }
}

/// Корзина размера части: 1, 2, 3, 4, 5–8, 9–16, 17+ → `SZ1 + 0..6`.
pub fn part_size(n: usize) {
    let b = match n {
        0 | 1 => 0,
        2 => 1,
        3 => 2,
        4 => 3,
        5..=8 => 4,
        9..=16 => 5,
        _ => 6,
    };
    add(SZ1 + b, 1);
}

pub fn snapshot() -> [u64; 36] {
    std::array::from_fn(|i| C[i].load(Relaxed))
}

pub fn line(before: &[u64; 36]) -> String {
    let now = snapshot();
    NAMES
        .iter()
        .zip(now.iter().zip(before))
        .map(|(n, (a, b))| format!("{n}={}", a.saturating_sub(*b)))
        .collect::<Vec<_>>()
        .join(" ")
}
