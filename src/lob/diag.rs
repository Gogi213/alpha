//! Нейтральная диагностика замеров (TK-049, `ALPHA_ATTEMPT_STATS`; С-12, КТ-4a.1): счётчики строк глубины и удаления
//! своих заявок. Живёт вне `lob::backtest`, чтобы `lob::strategy` не импортировал прогонщик (A9). На счёт не влияет;
//! старые пути `lob::backtest::fast_depth::*` — реэкспорт.

/// Строки глубины по удалению от своей лучшей цены в тиках: ≤3 / ≤10 / ≤30 / дальше — замер TK-049, на счёт не влияет.
pub static DEPTH_ROW_BANDS: [std::sync::atomic::AtomicU64; 4] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 4];

/// Классы обновлений глубины (замер TK-049, включается `ALPHA_ATTEMPT_STATS`): 0 — сдвигает лучшую цену своей стороны,
/// 1 — на лучшей цене без сдвига, 2 — на тике стены (наблюдаемый стратегией уровень), 3 — 1..3 тика от лучшей,
/// 4 — остальное; 5 — независимый счёт: любые строки в пределах ±3 тика от тика стены.
pub static DEPTH_ROW_CLASS: [std::sync::atomic::AtomicU64; 6] =
    [const { std::sync::atomic::AtomicU64::new(0) }; 6];
pub static BAND_STATS_ON: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
/// По умолчанию `DEPTH_ROW_BANDS` не считается (атомарный `fetch_add` на каждое обновление — диагностика, ≈2–3 % ЦП);
/// строка печатается только под `ALPHA_ATTEMPT_STATS`, там счёт включён всегда. `ALPHA_BAND_COUNT_OFF=0` — включить счёт
/// без остальной статистики (TK-048, 08.10: умолчание перевёрнуто; `=1` прежних обёрток остаётся no-op).
pub static BAND_COUNT_OFF: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(true);

thread_local! {
    static WATCH_TICK: std::cell::Cell<i64> = const { std::cell::Cell::new(i64::MIN) };
}

pub fn set_watch_tick(tick: i64) {
    if BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed) {
        WATCH_TICK.with(|w| w.set(tick));
    }
}

/// Удаление цены своей заявки от лучшей цены той же стороны в тиках (замер TK-049, `ALPHA_ATTEMPT_STATS`):
/// [вход/выход][<0 пересекает / 0 / 1..3 / 4..10 / >10].
pub static ORDER_DIST: [[std::sync::atomic::AtomicU64; 5]; 2] =
    [const { [const { std::sync::atomic::AtomicU64::new(0) }; 5] }; 2];

pub fn note_order_dist<MD: hftbacktest::depth::MarketDepth>(
    exit: bool,
    buy: bool,
    px: f64,
    depth: &MD,
) {
    if !BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    #[allow(clippy::cast_possible_truncation)]
    let t = (px / depth.tick_size()).round() as i64;
    let d = if buy {
        depth.best_bid_tick() - t
    } else {
        t - depth.best_ask_tick()
    };
    let k = match d {
        i64::MIN..=-1 => 0,
        0 => 1,
        1..=3 => 2,
        4..=10 => 3,
        _ => 4,
    };
    ORDER_DIST[usize::from(exit)][k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
}

#[inline]
pub fn note_band(price_tick: i64, best_tick: i64, moves_best: bool) {
    let stats = BAND_STATS_ON.load(std::sync::atomic::Ordering::Relaxed);
    if !stats && BAND_COUNT_OFF.load(std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    let d = (price_tick - best_tick).unsigned_abs();
    let k = usize::from(d > 3) + usize::from(d > 10) + usize::from(d > 30);
    DEPTH_ROW_BANDS[k].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    if !stats {
        return;
    }
    let w = WATCH_TICK.with(std::cell::Cell::get);
    let c = if moves_best {
        0
    } else if d == 0 {
        1
    } else if price_tick == w {
        2
    } else if d <= 3 {
        3
    } else {
        4
    };
    DEPTH_ROW_CLASS[c].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    if w != i64::MIN && (price_tick - w).abs() <= 3 {
        DEPTH_ROW_CLASS[5].fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
}
