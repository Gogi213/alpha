//! Планировщик кругов на стековых сопрограммах (TK-049, скелет): круг — блокирующий код, который в
//! `elapse`/`wait_next_feed` отдаёт планировщику метку следующего пробуждения; планировщик будит круг с
//! минимальным ключом (метка, вид события, номер круга). Порядок при равных метках — как в `EventSet`
//! крейта (LocalData < LocalOrder < ExchData < ExchOrder), затем номер круга.

use std::cmp::Reverse;
use std::collections::BinaryHeap;

use corosensei::stack::DefaultStack;
use corosensei::{Coroutine, CoroutineResult, Yielder};

/// Стек одного круга; живых кругов одновременно сотни.
pub const CIRCLE_STACK_BYTES: usize = 256 * 1024;

/// Вид пробуждения; порядок вариантов — порядок при равных метках.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum WakeKind {
    LocalData = 0,
    LocalOrder = 1,
    ExchData = 2,
    ExchOrder = 3,
}

/// Что круг сообщает планировщику при передаче управления.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Wake {
    pub ts: i64,
    pub kind: WakeKind,
}

/// Канал круга: `now` — метка, на которой его разбудили.
pub type CircleYielder = Yielder<i64, Wake>;
type Co<R> = Coroutine<i64, Wake, R, DefaultStack>;

pub struct Sched<R> {
    circles: Vec<Option<Co<R>>>,
    heap: BinaryHeap<Reverse<(i64, WakeKind, u32)>>,
}

impl<R: 'static> Sched<R> {
    pub fn new() -> Self {
        Self {
            circles: Vec::new(),
            heap: BinaryHeap::new(),
        }
    }

    /// Добавить круг; первое пробуждение — `first`. Возвращает номер круга.
    pub fn spawn<F>(&mut self, first: Wake, body: F) -> std::io::Result<u32>
    where
        F: FnOnce(&CircleYielder, i64) -> R + 'static,
    {
        let id = self.circles.len() as u32;
        let stack = DefaultStack::new(CIRCLE_STACK_BYTES)?;
        self.circles.push(Some(Coroutine::with_stack(stack, body)));
        self.heap.push(Reverse((first.ts, first.kind, id)));
        Ok(id)
    }

    /// Будит круги по возрастанию ключа до конца; результат круга — в `done(id, r)`.
    pub fn run(&mut self, mut done: impl FnMut(u32, R)) {
        while let Some(Reverse((ts, _, id))) = self.heap.pop() {
            let Some(co) = self.circles[id as usize].as_mut() else {
                continue;
            };
            match co.resume(ts) {
                CoroutineResult::Yield(w) => {
                    debug_assert!(w.ts >= ts, "круг просит прошлое");
                    self.heap.push(Reverse((w.ts, w.kind, id)));
                }
                CoroutineResult::Return(r) => {
                    self.circles[id as usize] = None;
                    done(id, r);
                }
            }
        }
    }
}

impl<R: 'static> Default for Sched<R> {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    fn wake(ts: i64, kind: WakeKind) -> Wake {
        Wake { ts, kind }
    }

    #[test]
    fn wakes_in_key_order_with_event_set_ties() {
        let log = Rc::new(RefCell::new(Vec::new()));
        let mut s: Sched<u32> = Sched::new();
        // круг 0: ExchOrder на 10, затем LocalData на 20; круг 1: LocalOrder на 10, затем ExchData на 20;
        // круг 2: LocalData на 10 (самый ранний вид при равной метке), затем конец.
        let l = log.clone();
        s.spawn(wake(10, WakeKind::ExchOrder), move |y, now| {
            l.borrow_mut().push((0, now));
            let now = y.suspend(wake(20, WakeKind::LocalData));
            l.borrow_mut().push((0, now));
            0
        })
        .unwrap();
        let l = log.clone();
        s.spawn(wake(10, WakeKind::LocalOrder), move |y, now| {
            l.borrow_mut().push((1, now));
            let now = y.suspend(wake(20, WakeKind::ExchData));
            l.borrow_mut().push((1, now));
            1
        })
        .unwrap();
        let l = log.clone();
        s.spawn(wake(10, WakeKind::LocalData), move |_, now| {
            l.borrow_mut().push((2, now));
            2
        })
        .unwrap();
        let mut finished = Vec::new();
        s.run(|id, r| finished.push((id, r)));
        assert_eq!(
            *log.borrow(),
            vec![(2, 10), (1, 10), (0, 10), (0, 20), (1, 20)]
        );
        assert_eq!(finished, vec![(2, 2), (0, 0), (1, 1)]);
    }

    #[test]
    fn same_key_goes_by_circle_id_and_time_never_goes_back() {
        let log = Rc::new(RefCell::new(Vec::new()));
        let mut s: Sched<()> = Sched::new();
        for id in 0..5u32 {
            let l = log.clone();
            s.spawn(wake(0, WakeKind::LocalData), move |y, mut now| {
                for step in 1..=3i64 {
                    l.borrow_mut().push((now, id));
                    now = y.suspend(wake(now + step, WakeKind::LocalData));
                }
            })
            .unwrap();
        }
        s.run(|_, _| {});
        let v = log.borrow();
        assert!(v.windows(2).all(|w| w[0] <= w[1]));
        assert_eq!(v.len(), 15);
    }

    #[test]
    fn switch_cost_probe() {
        const CIRCLES: u32 = 200;
        const STEPS: i64 = 5_000;
        let mut s: Sched<()> = Sched::new();
        for _ in 0..CIRCLES {
            s.spawn(wake(0, WakeKind::LocalData), |y, mut now| {
                for _ in 0..STEPS {
                    now = y.suspend(wake(now + 10_000_000, WakeKind::LocalData));
                }
            })
            .unwrap();
        }
        let t = std::time::Instant::now();
        s.run(|_, _| {});
        let n = u64::from(CIRCLES) * (STEPS as u64 + 1);
        eprintln!(
            "sched: {n} пробуждений, {:.1} нс на пробуждение (куча на {CIRCLES} кругов)",
            t.elapsed().as_nanos() as f64 / n as f64
        );
    }
}
