//! Ядро раннего старта пампов (TK-492, G-017, В-97): кэш колонок на контракт, признаки, эпизоды, контроли.
//!
//! Определения, окна и пороги не назначаются — их задаёт протокол П-34 (TK-491) файлом `params.json`.
//! Правило пропусков (явное): минута без сделок — `close = NaN`, объёмы и число сделок 0; слот OI без
//! строки (дыра) — `NaN` во всех колонках OI, ничего не дорисовывается (П-34 §1).
//! Сетка минут общая со `statarb::panel` (`GRID0_MS`), слот OI = 5 минут.

pub mod agg;
pub mod cache;
pub mod cli;
pub mod control;
pub mod control_ref;
pub mod episodes;
pub mod episodes_ref;
pub mod feat;
pub mod feat_ref;
pub mod params;
pub mod run;
pub mod stats;
pub mod trace;
pub mod trade;
pub mod types;
pub mod verdict;

#[cfg(test)]
mod control_tests;
#[cfg(test)]
mod episodes_tests;
#[cfg(test)]
mod feat_tests;
#[cfg(test)]
mod run_tests;
#[cfg(test)]
mod stats_tests;
#[cfg(test)]
mod tests;
