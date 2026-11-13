//! Писатель шарда: единственный на шард потребитель очереди приёма — разбор, снятие дублей, кодировщик, блоки на диск.
//!
//! Делает: кадр → `Parser` адаптера → записи → дубль? (личность записи уже была за `SEEN_MS`) → строка формата →
//! блок; блок уходит потоку диска каждые `flush` или при `block_max` байт. Сырые кадры с временем биржи снимаются как
//! копии, если их уже принесла другая сессия (линия) за `RAW_SEEN_MS`; служебные кадры (ответ подписки, pong) пишутся все.
//! Смена суток UTC — по приёму записи: старый файл закрывается, новый начинается с нуля (кодировщик сбрасывается).
//! Не знает сокетов; не держит блокировку метрик во время ожидания диска.

use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Write as _;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use super::codec::{Encoder, Rec, SymEnc, NS_PER_MS};
use super::exchange::{Exchange, Out, Parsed, Parser};
use super::fresh::RAW;
use super::hash::{fnv, FnvBuild};
use super::metrics::{ShardMetrics, WriterStats};
use super::ring::{Item, Payload, RingRx};
use super::sink::{SinkMsg, SinkTx};

/// Сколько помнить сырые кадры для снятия копий между линиями (линии одного шарда расходятся по приходу на секунды).
pub const RAW_SEEN_MS: i64 = 60_000;
/// Сколько минимум помнить личности записей для снятия дублей между линиями (помнится от этого срока до удвоенного): перекрытие замены 20 с, расхождение линий — секунды (замер 12.11: до 4,8 с;
/// TK-532: застрявшая линия до 15 с, деградировавший вход до 120 с, пока шард не откроет замену). 120 с ≈ 50 МБ на процесс при ≈ 3,5 тыс. записей/с.
pub const SEEN_MS: i64 = 120_000;
const NS_PER_DAY: i64 = 86_400 * 1_000_000_000;
/// Закрыть файл ушедших суток по часам не раньше, чем через столько после полуночи (запись из очереди не может быть старше).
const ROTATE_GRACE_NS: i64 = 5_000_000_000;
/// Такт писателя в одиночном запуске (`run`); у процесса такт задаёт `worker`.
const TICK: Duration = Duration::from_millis(1);
const BATCH: usize = 256;
const LAG_SAMPLE_EVERY: u32 = 16;

/// UTC-сутки (YYYY-MM-DD) по индексу суток от эпохи.
pub fn day_string(day: i64) -> String {
    chrono::DateTime::from_timestamp(day * 86_400, 0).map_or_else(
        || "1970-01-01".to_string(),
        |d| d.format("%Y-%m-%d").to_string(),
    )
}

#[derive(Clone, Debug)]
pub struct WriterCfg {
    pub root: PathBuf,
    pub shard: String,
    /// Номер запуска = время старта процесса (с); он же в имени файла.
    pub run_id: i64,
    /// Период сброса блока на диск.
    pub flush: Duration,
    pub block_max: usize,
    /// Часы писателя (нс UTC): очередь кадров и закрытие суток по часам; в проде — стенные, в тестах подменяются.
    pub clock: fn() -> i64,
}

struct SymState {
    enc: SymEnc,
}

/// Личность записи: символ, вид, личность по бирже.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct IdKey {
    sid: u32,
    kind: u8,
    id: i64,
}

/// Личности записей, уже виденные: запись — дубль, если её личность уже была (другая линия, замена при перекрытии, повтор кадра той же
/// сессией). Не требует порядка: запись, пришедшая позже более новой, но ещё не виденная, — новая и пишется.
/// Два поколения множества вместо очереди устаревания: личность помнится от `SEEN_MS` до `2·SEEN_MS`, ничего не удаляется поэлементно,
/// а новое поколение создаётся сразу нужного размера — без перестройки таблицы посреди потока (она давала паузу писателя ≈ 100 мс на миллионах записей).
#[derive(Default)]
struct Seen {
    cur: HashSet<IdKey, FnvBuild>,
    prev: HashSet<IdKey, FnvBuild>,
    cur_since_ms: i64,
}

impl Seen {
    /// `true` — личность новая (писать), `false` — дубль. `now_ms` — приём записи (неубывающий).
    fn first_time(&mut self, now_ms: i64, k: IdKey) -> bool {
        let age = now_ms - self.cur_since_ms;
        if self.cur_since_ms == 0 || age >= 2 * SEEN_MS {
            // первая запись или долгая тишина: помнить нечего
            self.prev = HashSet::default();
            self.cur = HashSet::with_capacity_and_hasher(1 << 14, FnvBuild::default());
            self.cur_since_ms = now_ms;
        } else if age >= SEEN_MS {
            let n = self.cur.len();
            self.prev = std::mem::replace(
                &mut self.cur,
                HashSet::with_capacity_and_hasher(n + n / 4, FnvBuild::default()),
            );
            self.cur_since_ms = now_ms;
        }
        if self.prev.contains(&k) {
            return false;
        }
        self.cur.insert(k)
    }
}

struct RawEnt {
    last_ms: i64,
    /// (сессия, сколько кадров с этим содержимым она принесла)
    counts: Vec<(u32, u32)>,
}

/// Копии сырых кадров: кадр от сессии — копия, если другая сессия принесла этот кадр больше раз, чем эта.
#[derive(Default)]
struct RawSeen {
    map: HashMap<u64, RawEnt, FnvBuild>,
    order: VecDeque<(i64, u64)>,
}

impl RawSeen {
    /// `true` — первая доставка (писать), `false` — копия (снять).
    fn deliver(&mut self, sender: u32, h: u64, now_ms: i64) -> bool {
        while let Some(&(t, old)) = self.order.front() {
            if now_ms - t <= RAW_SEEN_MS {
                break;
            }
            self.order.pop_front();
            if self.map.get(&old).is_some_and(|e| e.last_ms <= t) {
                self.map.remove(&old);
            }
        }
        self.order.push_back((now_ms, h));
        let e = self.map.entry(h).or_insert_with(|| RawEnt {
            last_ms: now_ms,
            counts: Vec::new(),
        });
        e.last_ms = now_ms;
        let other = e
            .counts
            .iter()
            .filter(|(s, _)| *s != sender)
            .map(|&(_, c)| c)
            .max()
            .unwrap_or(0);
        let mine = match e.counts.iter_mut().find(|(s, _)| *s == sender) {
            Some(c) => {
                c.1 += 1;
                c.1
            }
            None => {
                e.counts.push((sender, 1));
                1
            }
        };
        other < mine
    }
}

struct Core {
    cfg: WriterCfg,
    enc: Encoder,
    index: HashMap<Box<[u8]>, usize, FnvBuild>,
    syms: Vec<SymState>,
    block: Vec<u8>,
    block_rows: u64,
    samples: Vec<i64>,
    nsample: u32,
    block_started: Option<Instant>,
    day: Option<i64>,
    prev_rx: i64,
    raw: RawSeen,
    seen: Seen,
    /// Сообщения диску, ещё не отправленные: уходят после пачки, вне блокировки метрик.
    out: Vec<SinkMsg>,
    /// Пачка сортируется по приёму (ядерные метки реактора): дубль двух линий несёт минимум метки, а не метку обработанного первым.
    sort_batch: bool,
}

impl Core {
    fn sym_id(&mut self, sym: &[u8]) -> usize {
        if let Some(&i) = self.index.get(sym) {
            return i;
        }
        let i = self.syms.len();
        self.syms.push(SymState {
            enc: SymEnc::default(),
        });
        self.index.insert(sym.into(), i);
        i
    }

    fn ensure_file(&mut self, rx: i64) {
        let day = rx.div_euclid(NS_PER_DAY);
        if self.day == Some(day) {
            return;
        }
        self.cut_block();
        self.close_day();
        let path = self
            .cfg
            .root
            .join(day_string(day))
            .join(format!("{}-{}.tsv.zst", self.cfg.shard, self.cfg.run_id));
        self.out.push(SinkMsg::Open { path });
        self.enc.reset();
        for s in &mut self.syms {
            s.enc = SymEnc::default();
        }
        self.day = Some(day);
    }

    fn close_day(&mut self) {
        if self.day.take().is_some() {
            self.out.push(SinkMsg::Close);
        }
    }

    /// Блок строк — диску.
    fn cut_block(&mut self) {
        if self.block.is_empty() {
            return;
        }
        let data = std::mem::replace(
            &mut self.block,
            Vec::with_capacity(self.cfg.block_max + 4096),
        );
        self.out.push(SinkMsg::Block {
            data,
            rows: std::mem::take(&mut self.block_rows),
            samples: std::mem::take(&mut self.samples),
        });
        self.block_started = None;
    }

    /// После каждой записанной строки: счёт, метка приёма для `lag_out`, граница блока.
    fn after_line(&mut self, rx: i64) {
        self.prev_rx = rx;
        self.block_rows += 1;
        if self.block_started.is_none() {
            self.block_started = Some(Instant::now());
            self.samples.push(rx);
        }
        self.nsample += 1;
        if self.nsample >= LAG_SAMPLE_EVERY {
            self.nsample = 0;
            self.samples.push(rx);
        }
        if self.block.len() >= self.cfg.block_max {
            self.cut_block();
        }
    }

    fn on_record(&mut self, st: &mut WriterStats, rx: i64, settled: bool, rec: &Rec<'_>) {
        let ki = rec.kind().idx();
        let sid = self.sym_id(rec.sym());
        let id = IdKey {
            sid: u32::try_from(sid).unwrap_or(u32::MAX),
            kind: u8::try_from(ki).unwrap_or(u8::MAX),
            id: rec.ident(),
        };
        if !self.seen.first_time(rx.div_euclid(NS_PER_MS), id) {
            st.dups += 1;
            return;
        }
        self.ensure_file(rx);
        if let Some(s) = self.syms.get_mut(sid) {
            self.enc.record(&mut self.block, rx, &mut s.enc, rec);
        }
        self.after_line(rx);
        st.records += 1;
        st.rows += 1;
        if settled {
            let rx_ms = rx.div_euclid(NS_PER_MS);
            st.fresh.add(ki, rx_ms, rx_ms - rec.e());
            if let Some(h) = st.lag_in.get_mut(ki) {
                h.add(
                    rx_ms,
                    u64::try_from((rx - rec.e() * NS_PER_MS).max(0) / 1000).unwrap_or(0),
                );
            }
        }
    }

    fn until_flush(&self, flush: Duration) -> Option<Duration> {
        self.block_started
            .map(|t| (t + flush).saturating_duration_since(Instant::now()))
    }
}

/// Приёмник результата разбора одного кадра: записи — в писатель шарда, хвост — сырой записью (с учётом копий между линиями).
struct Sink<'a> {
    core: &'a mut Core,
    st: &'a mut WriterStats,
    exch: &'a dyn Exchange,
    rx: i64,
    sender: u32,
    settled: bool,
}

impl Out for Sink<'_> {
    fn rec(&mut self, r: &Rec<'_>) {
        self.core.on_record(self.st, self.rx, self.settled, r);
    }

    fn raw(&mut self, frame: &[u8]) {
        let rx_ms = self.rx.div_euclid(NS_PER_MS);
        let lag = self.exch.lag_ms(frame, self.rx);
        if lag.is_some() && !self.core.raw.deliver(self.sender, fnv(frame), rx_ms) {
            self.st.raw_copies += 1;
            self.st.dups += 1;
            return;
        }
        self.core.ensure_file(self.rx);
        self.core.enc.raw(&mut self.core.block, self.rx, frame);
        self.core.after_line(self.rx);
        self.st.raw += 1;
        self.st.rows += 1;
        if let (true, Some(l)) = (self.settled, lag) {
            #[allow(clippy::cast_possible_truncation)]
            self.st.fresh.add(RAW, rx_ms, l.round() as i64);
        }
    }
}

pub struct ShardWriter {
    core: Core,
    exch: Arc<dyn Exchange>,
    parser: Box<dyn Parser>,
    metrics: Arc<ShardMetrics>,
    sink: SinkTx,
    ring: RingRx,
    /// Диагностика: `ALPHA_COLLECT_CAPTURE=1` — все кадры всех сессий (до снятия дублей) в `capture-<шард>.tsv`: «сессия, приём нс, кадр».
    capture: Option<std::io::BufWriter<std::fs::File>>,
}

impl ShardWriter {
    pub fn new(
        cfg: WriterCfg,
        exch: Arc<dyn Exchange>,
        metrics: Arc<ShardMetrics>,
        sink: SinkTx,
        ring: RingRx,
    ) -> Self {
        let parser = exch.new_parser();
        let capture = std::env::var_os("ALPHA_COLLECT_CAPTURE").and_then(|_| {
            std::fs::create_dir_all(&cfg.root).ok()?;
            std::fs::File::create(cfg.root.join(format!("capture-{}.tsv", cfg.shard)))
                .ok()
                .map(std::io::BufWriter::new)
        });
        let core = Core {
            block: Vec::with_capacity(cfg.block_max + 4096),
            cfg,
            enc: Encoder::new(),
            index: HashMap::default(),
            syms: Vec::new(),
            block_rows: 0,
            samples: Vec::new(),
            nsample: 0,
            block_started: None,
            day: None,
            prev_rx: 0,
            raw: RawSeen::default(),
            seen: Seen::default(),
            out: Vec::new(),
            sort_batch: false,
        };
        Self {
            core,
            exch,
            parser,
            metrics,
            sink,
            ring,
            capture,
        }
    }

    /// Один элемент очереди; `true` — это `Shutdown`.
    fn handle(&mut self, item: Item, st: &mut WriterStats, now_ns: i64) -> bool {
        if matches!(item.payload, Payload::Shutdown) {
            return true;
        }
        let rx = item.rx_ns.max(self.core.prev_rx);
        if rx > item.rx_ns {
            st.clamped += 1;
        }
        let rx_ms = rx.div_euclid(NS_PER_MS);
        match item.payload {
            Payload::Frame(bytes) => {
                if let Some(w) = self.capture.as_mut() {
                    let _ = write!(w, "{}	{}	", item.sender, item.rx_ns);
                    let _ = w.write_all(&bytes);
                    let _ = w.write_all(
                        b"
",
                    );
                }
                st.queue_lag.add(
                    rx_ms,
                    u64::try_from((now_ns - item.rx_ns).max(0) / 1000).unwrap_or(0),
                );
                if let Some(sym) = self.exch.symbol(&bytes) {
                    touch_seen(st, sym, rx_ms);
                }
                let mut out = Sink {
                    core: &mut self.core,
                    st,
                    exch: &*self.exch,
                    rx,
                    sender: item.sender,
                    settled: item.settled,
                };
                if self.parser.parse(&bytes, &mut out) == Parsed::Raw {
                    out.raw(&bytes);
                }
            }
            Payload::Control { what, detail } => {
                self.core.ensure_file(rx);
                self.core
                    .enc
                    .control(&mut self.core.block, rx, what, &detail);
                self.core.after_line(rx);
                st.rows += 1;
            }
            Payload::Ping(rtt_us) => {
                self.core.ensure_file(rx);
                self.core.enc.ping(&mut self.core.block, rx, rtt_us);
                self.core.after_line(rx);
                st.rows += 1;
            }
            Payload::Shutdown => {}
        }
        false
    }

    fn send_out(&mut self) {
        for msg in self.core.out.drain(..) {
            if !self.sink.send(msg) {
                break;
            }
        }
    }

    /// Закрыть сутки по часам: файл ушедших суток закрывается, не дожидаясь первой записи нового дня (`.ok` — сразу).
    fn rotate_by_clock(&mut self, now_ns: i64) {
        if let Some(day) = self.core.day {
            if now_ns - (day + 1) * NS_PER_DAY > ROTATE_GRACE_NS {
                self.core.cut_block();
                self.core.close_day();
                // запись ушедших суток, пришедшая в очередь позже (обратное давление), не откроет закрытый файл заново: приём поднимается
                // до полуночи — тем же ограничением `rx ≥ приём предыдущей записи`, что держит `dns` ≥ 0 (счётчик `rx_clamped`)
                self.core.prev_rx = self.core.prev_rx.max((day + 1) * NS_PER_DAY);
            }
        }
    }

    /// Сортировать пачку такта по `rx` (стабильно): при ядерных метках запись из двух линий в одной пачке берётся с меньшей меткой.
    pub fn set_sort_batch(&mut self, on: bool) {
        self.core.sort_batch = on;
    }

    /// Один такт писателя: пачка из очереди → записи; блок по сроку; закрытие суток по часам; блоки — диску. `scratch` — пустой буфер для пачки
    /// (его ёмкость переиспользуется). `true` — писатель закончил (`Shutdown` или все отправители ушли): файл закрыт, поток диска остановлен.
    pub fn step(&mut self, scratch: &mut Vec<Item>) -> bool {
        let alive = self.ring.take(scratch);
        let metrics = Arc::clone(&self.metrics);
        let flush = self.core.cfg.flush;
        let now_ns = (self.core.cfg.clock)();
        let mut done = !alive;
        if self.core.sort_batch {
            // `Shutdown` — всегда последним (его метка не имеет значения)
            scratch.sort_by_key(|i| match i.payload {
                Payload::Shutdown => i64::MAX,
                _ => i.rx_ns,
            });
        }
        while !scratch.is_empty() {
            let n = scratch.len().min(BATCH);
            let mut st = metrics.writer();
            for it in scratch.drain(..n) {
                if !done {
                    done = self.handle(it, &mut st, now_ns);
                }
            }
        }
        {
            let _st = metrics.writer();
            if self.core.until_flush(flush).is_some_and(|d| d.is_zero()) {
                self.core.cut_block();
            }
            self.rotate_by_clock(now_ns);
        }
        if done {
            self.core.cut_block();
            self.core.close_day();
            self.core.out.push(SinkMsg::Quit);
        }
        self.send_out();
        done
    }

    /// Когда писателю нужно проснуться по сроку блока (не позже такта).
    pub fn next_wake(&self) -> Duration {
        self.core
            .until_flush(self.core.cfg.flush)
            .map_or(TICK, |d| d.min(TICK))
    }

    /// Поток одного писателя (проверки): такты до `Shutdown` или ухода отправителей; в конце файл закрыт, поток диска остановлен.
    pub fn run(mut self) {
        let mut scratch = Vec::new();
        while !self.step(&mut scratch) {
            std::thread::sleep(self.next_wake());
        }
    }
}

fn touch_seen(st: &mut WriterStats, sym: &[u8], rx_ms: i64) {
    if let Some(e) = st.seen.get_mut(sym) {
        e.1 = rx_ms;
    } else {
        st.seen.insert(
            sym.into(),
            (String::from_utf8_lossy(sym).to_lowercase(), rx_ms),
        );
    }
}
