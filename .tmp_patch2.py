import os
os.chdir('src/lob/backtest')
p = 'fast_hold.rs'
s = open(p, encoding='utf-8').read()


def rep(old, new):
    global s
    assert s.count(old) >= 1, old[:60]
    s = s.replace(old, new, 1)


rep("use super::*;\n", "use super::fast_book::{SeriesDepth, TapeBook, STRIDE};\nuse super::*;\n")
rep("""    book: FastMarketDepth,
    /// Локальные сделки""", """    book: FastMarketDepth,
    /// Общая книга окна (`ALPHA_FAST_BOOK`): строки применяет она, один раз на окно; `book` подтягивается до
    /// `lcur` только перед чтением целиком (`sync_book`), `wcur` — до какой строки он уже дошёл.
    tape: Option<*mut TapeBook<'static>>,
    wcur: usize,
    /// Локальные сделки""")
rep("""            max_exch_ts: i64::MIN,
            book,
            trades: Vec::new(),
        }
    }
""", """            max_exch_ts: i64::MIN,
            book,
            tape: None,
            wcur: base,
            trades: Vec::new(),
        }
    }

    /// Трекер на общей книге окна: `tape` построена над тем же срезом `rows` с базой 0; рабочая книга берётся
    /// из неё на строке `base`. Указатель должен жить дольше трекера.
    pub fn with_tape(rows: &'a [Event], base: usize, tape: *mut TapeBook<'static>) -> Self {
        // SAFETY: вызывающий держит `tape` живой и без других ссылок на время вызова.
        let book = unsafe { &mut *tape }.book_at(base);
        let mut t = Self::new(rows, base, book);
        t.tape = Some(tape);
        t
    }

    /// Подтянуть рабочую книгу до курсора (без общей книги — ничего: она и так на курсоре).
    pub fn sync_book(&mut self) {
        let Some(tp) = self.tape else { return };
        if self.wcur == self.lcur {
            return;
        }
        if self.lcur - self.wcur <= STRIDE / 2 {
            for ev in &self.rows[self.wcur..self.lcur] {
                if ev.is(LOCAL_EVENT) {
                    apply_local(&mut self.book, ev);
                }
            }
        } else {
            // SAFETY: см. `with_tape`.
            self.book = unsafe { &mut *tp }.book_at(self.lcur);
        }
        self.wcur = self.lcur;
    }

    /// Подпись входов удержания на курсоре: по ряду общей книги, если он достоверен, иначе по рабочей книге.
    pub fn input_sig(&mut self, state: &StrategyState, now: i64) -> Option<[u64; 5]> {
        if let Some(tp) = self.tape {
            // SAFETY: см. `with_tape`; ряд уже выращен `advance_to`.
            let tape: &TapeBook<'static> = unsafe { &*tp };
            if let Some(sd) = SeriesDepth::new(tape, self.lcur) {
                return state.hold_input_sig(&sd, now);
            }
        }
        self.sync_book();
        state.hold_input_sig(&self.book, now)
    }
""")
rep("""                apply_local(&mut self.book, ev);
                if ev.is(LOCAL_TRADE_EVENT) {""", """                if self.tape.is_none() {
                    apply_local(&mut self.book, ev);
                }
                if ev.is(LOCAL_TRADE_EVENT) {""")
rep("""            self.lcur += 1;
        }
    }
""", """            self.lcur += 1;
        }
        if let Some(tp) = self.tape {
            // SAFETY: см. `with_tape`.
            unsafe { &mut *tp }.grow_to(self.lcur);
        }
    }
""")
rep("    pub fn handoff(&self, t: i64,", "    pub fn handoff(&mut self, t: i64,")
rep("""        if !self.clean || self.max_exch_ts > t {
            return None;
        }
""", """        if !self.clean || self.max_exch_ts > t {
            return None;
        }
        self.sync_book();
""")
rep("let skip = skip_on && sig.skip(state.hold_input_sig(bot.depth(0), now));",
    "let skip = skip_on && sig.skip(bot.tracker.input_sig(state, now));")
rep("""        let action = if skip {
            Action::Idle
        } else {
            match on_event""", """        let action = if skip {
            Action::Idle
        } else {
            bot.tracker.sync_book();
            match on_event""")
rep("""#[cfg(test)]
thread_local! {
    pub(super) static FORCE_ON""", """/// `ALPHA_FAST_BOOK=1` (поверх `ALPHA_FAST_HOLD=1`) — общая книга окна для кругов; умолчание — выкл.
pub fn fast_book_on() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_FAST_BOOK").is_some_and(|v| v == "1"))
}

/// `ALPHA_FAST_BOOK_CHECK=1` — сверять книгу общего ряда на старте круга со снимком движка; несовпадение — счётчик
/// и круг идёт на книге движка.
fn fast_book_check_on() -> bool {
    static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("ALPHA_FAST_BOOK_CHECK").is_some_and(|v| v == "1"))
}

pub static FAST_BOOK_MISMATCH: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

#[cfg(test)]
thread_local! {
    pub(super) static FORCE_ON""")
rep("""    latency: ExecLatency,
    queue_model: QueueModelKind,
}

thread_local! {""", """    latency: ExecLatency,
    queue_model: QueueModelKind,
    tape: Option<*mut TapeBook<'static>>,
}

thread_local! {""")
rep("""    queue_model: QueueModelKind,
    f: impl FnOnce() -> R,
) -> R {""", """    queue_model: QueueModelKind,
    tape: Option<&mut TapeBook<'_>>,
    f: impl FnOnce() -> R,
) -> R {""")
rep("""            latency,
            queue_model,
        }))""", """            latency,
            queue_model,
            tape: tape
                .filter(|_| fast_book_on())
                .map(|t| std::ptr::from_mut(t).cast::<TapeBook<'static>>()),
        }))""")
rep("""    let book = DepthSnapshot::of(bt.depth(asset_no)).build(ctx.tick, ctx.lot);
    let state_start = state.clone();
    let mut fb = FastBot::new(HoldTracker::new(rows, cur, book), t);""", """    let tape = ctx.tape.filter(|&tp| {
        !fast_book_check_on() || {
            // SAFETY: см. `with_fast_ctx` — книга окна жива и свободна на время шага круга.
            let same = DepthSnapshot::of(&unsafe { &mut *tp }.book_at(cur))
                == DepthSnapshot::of(bt.depth(asset_no));
            if !same {
                FAST_BOOK_MISMATCH.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }
            same
        }
    });
    let state_start = state.clone();
    let tracker = match tape {
        Some(tp) => HoldTracker::with_tape(rows, cur, tp),
        None => HoldTracker::new(
            rows,
            cur,
            DepthSnapshot::of(bt.depth(asset_no)).build(ctx.tick, ctx.lot),
        ),
    };
    let mut fb = FastBot::new(tracker, t);""")
open(p, 'w', encoding='utf-8').write(s)

p = '../backtest.rs'
s = open(p, encoding='utf-8').read()
rep("""    let mut buf: Vec<Event> = Vec::new();
    drive_bounce_with::<Backtest<FastMarketDepth>, FastMarketDepth, _>(""", """    let mut buf: Vec<Event> = Vec::new();
    let mut tape_slot: Option<(usize, fast_book::TapeBook<'_>)> = None;
    drive_bounce_with::<Backtest<FastMarketDepth>, FastMarketDepth, _>(""")
rep("""            note_attempt(attempt, rest.len());
            let last =""", """            note_attempt(attempt, rest.len());
            let tape = if fast_hold::fast_book_on() {
                events.as_events().map(|all| {
                    if tape_slot.as_ref().is_none_or(|(k, _)| *k != w.start) {
                        tape_slot = Some((
                            w.start,
                            fast_book::TapeBook::new(
                                &all[w.start..],
                                0,
                                &w.depth,
                                windows.tick_size,
                                windows.lot_size,
                            ),
                        ));
                    }
                    &mut tape_slot.as_mut().expect("слот выставлен").1
                })
            } else {
                None
            };
            let last =""")
rep("""                            cfg.queue_model,
                            || step(bt, data_end),""", """                            cfg.queue_model,
                            tape,
                            || step(bt, data_end),""")
open(p, 'w', encoding='utf-8').write(s)
