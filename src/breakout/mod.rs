//! П-42: пробой часового уровня на минутках Binance USD-M, лонг и шорт отдельно (агрегатный рантайм).
//!
//! Конвейер: `data` (CSV → кэш `.b42`, цены — `i64` ×1e8 точно как у биржи) → `levels` (часовые бары → пивоты
//! → уровни; зависит только от `LevelCfg`) → `signals` (минутный проход: ложный пробой, зона входа, цвет, пул;
//! зависит от `SigCfg`) → `portfolio` (слияние сигналов монет по минутам: потолок P, порядок, паузы) с `trade`
//! (выход одной сделки по минуткам, худший порядок внутри минутки). Выход сделки зависит только от траектории
//! цены и параметров выхода, поэтому исход считается в момент принятия сигнала.
//!
//! Правила и оси — `docs/research/P-42-level-breakout.md`; уточнения реализации — его §12.
//! Деньги — `f64` (доллары), цены и размеры позиций — целые.
#![allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss
)]

pub mod cli;
pub mod data;
pub mod levels;
pub mod params;
pub mod portfolio;
pub mod signals;
pub mod trade;

#[cfg(test)]
mod tests;

use std::sync::Mutex;

/// Сторона: лонг ловит пробой потолка (пивоты-максимумы), шорт — пробой пола (пивоты-минимумы).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Side {
    Long,
    Short,
}

impl Side {
    /// Знак: в «длинной раме» цены шорта берутся с обратным знаком.
    pub fn s(self) -> i64 {
        match self {
            Side::Long => 1,
            Side::Short => -1,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Side::Long => "long",
            Side::Short => "short",
        }
    }
}

/// Параллельное отображение: задания раздаются потокам по атомарному счётчику, порядок результатов — как у входа.
pub fn par_map<T: Sync, R: Send>(items: &[T], threads: usize, f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let slots: Vec<Mutex<Option<R>>> = items.iter().map(|_| Mutex::new(None)).collect();
    let next = AtomicUsize::new(0);
    std::thread::scope(|sc| {
        for _ in 0..threads.max(1) {
            sc.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                let Some(it) = items.get(i) else { break };
                let r = f(it);
                if let Ok(mut g) = slots[i].lock() {
                    *g = Some(r);
                }
            });
        }
    });
    slots
        .into_iter()
        .filter_map(|m| m.into_inner().ok().flatten())
        .collect()
}
