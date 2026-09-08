//! Счётчик аллокаций на глобальном аллокаторе.
//!
//! Гейт GC (`PLAN.md`) требует «ноль аллокаций на событие на пути разбор-и-запись»
//! и меряет это «счётчиком на глобальном аллокаторе». До этого модуля такого
//! инструмента в репозитории не было, и утверждения об экономности проверялись
//! чтением кода — то есть не проверялись: горячий цикл легко возвращается к
//! аллокации на итерацию при рефакторинге, и ни один тест этого не замечает.
//!
//! Счётчик потоковый (`thread_local`), а не глобальный. Тесты `cargo test` идут
//! параллельно в разных потоках, и общий атомарный счётчик мерил бы сумму по
//! всему процессу: такой тест падал бы или проходил в зависимости от того, что
//! делают соседние тесты, то есть был бы бесполезен именно там, где нужен.
//!
//! Обёртка стоит один инкремент потоковой переменной на аллокацию и никакой
//! синхронизации между потоками, поэтому включена всегда, а не под `cfg(test)`:
//! иначе она мерила бы сборку, которая в бою не работает.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

thread_local! {
    static ALLOCS: Cell<u64> = const { Cell::new(0) };
    static BYTES: Cell<u64> = const { Cell::new(0) };
}

/// Аллокатор, считающий обращения текущего потока и передающий работу системному.
pub struct CountingAllocator;

unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // `try_with`, а не `with`: во время разрушения потоковых переменных
        // обращение к ним паникует, а аллокатор в этот момент ещё зовут.
        // Паника внутри аллокатора — это прерывание процесса, поэтому потеря
        // нескольких последних отсчётов предпочтительнее.
        let _ = ALLOCS.try_with(|c| c.set(c.get() + 1));
        let _ = BYTES.try_with(|c| c.set(c.get() + layout.size() as u64));
        unsafe { System.alloc(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        // Перевыделение считается аллокацией: рост `Vec` внутри горячего цикла —
        // ровно тот дефект, который гейт ищет, и он выражается именно здесь,
        // а не в `alloc`.
        let _ = ALLOCS.try_with(|c| c.set(c.get() + 1));
        let _ = BYTES.try_with(|c| c.set(c.get() + new_size.saturating_sub(layout.size()) as u64));
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

/// Сколько аллокаций и байт совершил текущий поток за время работы `f`.
///
/// Возвращает результат `f` вместе с отсчётами, чтобы вызывающий не мог
/// случайно измерить не тот участок.
pub fn measure<T, F: FnOnce() -> T>(f: F) -> (T, Counts) {
    let a0 = ALLOCS.with(|c| c.get());
    let b0 = BYTES.with(|c| c.get());
    let out = f();
    let a1 = ALLOCS.with(|c| c.get());
    let b1 = BYTES.with(|c| c.get());
    (
        out,
        Counts {
            allocations: a1 - a0,
            bytes: b1 - b0,
        },
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Counts {
    pub allocations: u64,
    pub bytes: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_a_known_allocation() {
        let (v, counts) = measure(|| {
            let mut v: Vec<u64> = Vec::with_capacity(16);
            v.push(1);
            v
        });
        assert_eq!(v.len(), 1);
        assert_eq!(
            counts.allocations, 1,
            "одно выделение под ёмкость и ни одного роста"
        );
        assert!(counts.bytes >= 16 * 8);
    }

    /// Рост вектора считается: именно так выглядит аллокация, просочившаяся
    /// в горячий цикл, и `alloc` её не видит — видит `realloc`.
    #[test]
    fn counts_growth_as_an_allocation() {
        let (_, counts) = measure(|| {
            let mut v: Vec<u64> = Vec::new();
            for i in 0..1000u64 {
                v.push(i);
            }
            v
        });
        assert!(
            counts.allocations > 1,
            "рост из пустого вектора обязан быть виден, было {}",
            counts.allocations
        );
    }

    #[test]
    fn work_without_the_heap_costs_nothing() {
        let mut buf = [0u64; 64];
        let (sum, counts) = measure(|| {
            let mut s = 0u64;
            for (i, slot) in buf.iter_mut().enumerate() {
                *slot = i as u64;
                s += *slot;
            }
            s
        });
        assert_eq!(sum, (0..64u64).sum::<u64>());
        assert_eq!(counts.allocations, 0, "на стеке аллокаций быть не должно");
    }
}
