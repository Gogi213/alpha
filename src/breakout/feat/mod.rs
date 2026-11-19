//! TK-609: признаки П-42 «поток сделок» (16) и «производные метрики» (14) — порт `tools/p42/tk594_feat.py` на Rust.
//!
//! Равенство с Python — побитовое (правила R1–R4, `docs/findings/rust-max-plan-2026-11-18.md` §4.2): суммы numpy воспроизводит
//! `npcompat`, разбор чисел pandas — `pdcompat`, чтение суток — `trades` (`DaySrc`), запуск монет потоками и CSV — `io`.
//! Бинарь — `src/bin/alpha-feat.rs`; движок счёта эти модули не использует.

pub mod deriv;
pub mod f12;
pub mod flow;
pub mod io;
pub mod npcompat;
pub mod pdcompat;
pub mod trades;

#[cfg(test)]
mod f12_tests;
#[cfg(test)]
mod tests;
