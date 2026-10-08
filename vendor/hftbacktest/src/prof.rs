//! Замер TK-048: циклы TSC по классам работы строки (feature `rdtsc-prof`). Без feature `timed` просто зовёт замыкание.
//! Классы: 0 — применение книги биржей, 1 — `PartialFillExchange::process` целиком, 2 — `Local::process` целиком,
//! 3 — копия строк окна, 4 — `with_backtest_over_window` целиком (alpha), 5 — тело окна `windowed_with` (alpha).
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

pub static CYC: [AtomicU64; 8] = [const { AtomicU64::new(0) }; 8];
pub static CNT: [AtomicU64; 8] = [const { AtomicU64::new(0) }; 8];

#[cfg(feature = "rdtsc-prof")]
#[inline(always)]
pub fn timed<R>(k: usize, f: impl FnOnce() -> R) -> R {
    #[cfg(target_arch = "x86_64")]
    {
        let t = unsafe { core::arch::x86_64::_rdtsc() };
        let r = f();
        let d = unsafe { core::arch::x86_64::_rdtsc() }.wrapping_sub(t);
        CYC[k].fetch_add(d, Relaxed);
        CNT[k].fetch_add(1, Relaxed);
        r
    }
    #[cfg(not(target_arch = "x86_64"))]
    f()
}

#[cfg(not(feature = "rdtsc-prof"))]
#[inline(always)]
pub fn timed<R>(_k: usize, f: impl FnOnce() -> R) -> R {
    f()
}

/// Снимок (циклы, вызовы) по классам.
pub fn snapshot() -> Vec<(u64, u64)> {
    (0..8).map(|i| (CYC[i].load(Relaxed), CNT[i].load(Relaxed))).collect()
}
