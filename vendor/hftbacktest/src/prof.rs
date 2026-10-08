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

/// Замер TK-048: строки, прочитанные процессорами движка (`advance`), по классу состояния круга (0 — до входа без
/// заявок, 1 — есть живая заявка, 2 — позиция без заявок, 3 — вне шага опроса). Класс ставит драйвер alpha,
/// счёт потоково-локальный и включён только `ROW_CLASS_ON` (под `ALPHA_ATTEMPT_STATS`); на итог не влияет.
pub static ROW_CLASS_ON: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

thread_local! {
    static ROW_CLS: std::cell::Cell<usize> = const { std::cell::Cell::new(3) };
    static ROW_CNT: [std::cell::Cell<u64>; 4] = const { [const { std::cell::Cell::new(0) }; 4] };
}

#[inline(always)]
pub fn set_row_class(c: usize) {
    if ROW_CLASS_ON.load(Relaxed) {
        ROW_CLS.with(|x| x.set(c));
    }
}

#[inline(always)]
pub fn add_rows(n: u64) {
    if ROW_CLASS_ON.load(Relaxed) {
        let c = ROW_CLS.with(std::cell::Cell::get);
        ROW_CNT.with(|a| a[c].set(a[c].get() + n));
    }
}

/// Забирает счёт потока (обнуляя).
pub fn take_row_counts() -> [u64; 4] {
    ROW_CNT.with(|a| std::array::from_fn(|i| a[i].replace(0)))
}
