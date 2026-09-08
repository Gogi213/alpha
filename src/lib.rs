//! Измерительный конвейер для гипотезы плотностей, спроектированный так, чтобы
//! та же реконструкция книги и та же логика уровней работали на записи, на реплее
//! и в живой торговле. Обоснование — `docs/ARCHITECTURE.md`.

pub mod alloc_count;
pub mod binlog;
pub mod book;
pub mod bybit;
pub mod commands;
pub mod lob;
pub mod stats;

/// Гейт GC требует мерить аллокации на горячем пути счётчиком на глобальном
/// аллокаторе. Обёртка стоит один инкремент потоковой переменной на выделение,
/// поэтому включена всегда: под `cfg(test)` она мерила бы сборку, которая
/// в бою не работает.
#[global_allocator]
static GLOBAL: alloc_count::CountingAllocator = alloc_count::CountingAllocator;
