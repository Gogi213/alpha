//! `verify.csv` сайдкаром при записи (шаг 0.8 плана, Decision 24, ремонт Р1/Р2).
//!
//! Отдельный ОС-поток (`std::thread`, как авторитет шагов в 0.7) со своим
//! `BybitPublicRest`: раз в 5 минут берёт REST-снапшот, сверяет с книгой по `seq`
//! и пишет строку в `verify.csv`. Тикер один — сон 300с в самом потоке;
//! второго тикера в цикле записи больше нет (дефект В-8: два независимых
//! тикера ели первый тик и давали кадр пятиминутной давности).
//!
//! Связь с циклом — `std::sync::mpsc` (не tokio): цикл форвардит КАЖДОЕ
//! обработанное `Update` через `try_send`, переполнение — дроп+счётчик, как
//! кадры раньше. Цикл никогда не ждёт HTTP.
//!
//! Сайдкар держит непрерывную реплику книги + сейвы (`seq`, `u`, клон книги
//! каждые N=100 применённых апдейтов или 2с, последние ~64) + кольцо ВСЕХ наших
//! апдейтов с момента старейшего сейва с кепом по байтам (~32МБ).
//! Сверка на тике: fetch snapshot → `seq_s`; дождаться (таймаут ~5с), пока
//! max-seen-`seq` ≥ `seq_s`; найти новейший сейв с `seq` ≤ `seq_s`; переиграть
//! кольцо (`seq` в (`save.seq`, `seq_s`]) на клоне с проверкой `u`-непрерывности
//! от `save.u`. Совпало — сравнить; `book_seq` в строке = `seq` реально
//! сравненного состояния (может быть < `snapshot_seq` при overshoot: снапшот
//! между двумя нашими `seq`, книга нашего символа при этом не менялась).
//! Нет сейва ≤ `seq_s` (снапшот старше истории), дыра в `u` на отрезке, таймаут,
//! разрыв `u` в форварде (грязная реплика) — строка `misaligned`, а не пропуск.
//! Первая строка — вскоре после первого снапшота (первый тик сразу по готовности
//! реплики, дальше каждые 300с). `Misaligned` остаётся только для настоящей
//! гонки, а не режимом по умолчанию.
//!
//! Почему ≤-скан, а не точное равенство: `seq` — ГЛОБАЛЬНЫЙ кросс-счётчик биржи
//! (скачет +40..160 на одно сообщение нашего символа, ~2000/с), наши апдейты
//! разрежены в `seq`-пространстве. Точное `replica_seq == snapshot_seq` не
//! наступает почти никогда, а backward-replay с требованием `seq` подряд
//! (`expected = base+1`) невозможен в принципе. Корректное состояние для
//! снапшота@`seq_s` = книга после ВСЕХ наших апдейтов с `seq` ≤ `seq_s`.
//!
//! Ключ выравнивания — сквозной `seq` (WS `seq` и REST `seq` — один счётчик).
//! `u` в двух каналах — два разных счётчика (WS +21/с, REST +5/с) и выровняться
//! не могут никогда; `u`-контроль потока в `Book` при этом не тронут.
//!
//! Формат `verify.csv`: `ts_utc,symbol,snapshot_seq,book_seq,
//! mismatches,verdict` (`ok`/`mismatch`/`misaligned`/`rest_unavailable`).

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::book::{Book, Update};
use crate::bybit::conn::{Clock, SystemClock};
use crate::bybit::rest::{fetch_orderbook_snapshot, BybitPublicRest, PublicRest};
use crate::bybit::verify::compare_with_snapshot;
use crate::commands::record::ts_utc_of_ns;

/// Каденция сверки: строка в `verify.csv` раз в 5 минут (шаг 0.8).
/// Тикер один — спит сам сайдкар-поток; второго тикера в `run_session` нет.
pub const VERIFY_INTERVAL_SECS: u64 = 300;

/// Ёмкость канала обновления → сайдкар: буфер на время HTTP-fetch (~10с × 50
/// сообщений/с = 500) с запасом; переполнение — дроп+счётчик, цикл не ждёт.
pub const VERIFY_UPDATE_CHANNEL_CAPACITY: usize = 8192;

/// Глубина REST-снапшота для сверки: топ-50 живой книги (шаг 0.6).
pub const VERIFY_ORDERBOOK_LIMIT: u32 = 50;

/// Кеп кольца последних обновлений по байтам (~32МБ). Оценка на обновление —
/// 64 байта overhead + 16 байт на уровень; переполнение делает старые базы
/// недостижимыми и даёт честный `misaligned`, а не молчаливый пропуск.
pub const VERIFY_RING_MAX_BYTES: usize = 32 * 1024 * 1024;

/// Таймаут догона, когда снапшот впереди реплики: ждать входящий поток, а не
/// сразу писать `misaligned` (дефект В-8: `u` уходит вперёд на ~17 за RTT).
pub const VERIFY_CATCHUP_TIMEOUT: Duration = Duration::from_secs(5);

/// Каденция сейвов: каждые N применённых апдейтов — (seq,u,клон книги).
/// При разреженном глобальном `seq` сейв — единственная точка, от которой можно
/// переиграть ≤-сканом до снапшота без требования `seq` подряд.
pub const VERIFY_SAVE_EVERY_N: usize = 100;

/// Сколько последних сейвов держать. Кольцо при этом хранит ВСЕ наши апдейты
/// с момента старейшего сейва (плюс 32МБ-кеп), иначе старые сейвы недостижимы.
pub const VERIFY_SAVE_KEEP: usize = 64;

/// Время между сейвами, если апдейтов мало (тихий символ): сейв не реже раз в 2с.
pub const VERIFY_SAVE_INTERVAL: Duration = Duration::from_secs(2);

/// Вердикт одной сверки. Сериализуется snake_case — те же слова в файле, что
/// в варианте (дрейф ловит тест `verify_header_is_stable`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VerifyVerdict {
    /// Книга на `seq` снапшота, ноль расхождений топ-50.
    Ok,
    /// Книга на `seq` снапшота, топ-50 разошёлся (`mismatches` > 0).
    Mismatch,
    /// На равном `seq` сравнить не удалось: гонка, переполнение кольца,
    /// протухшая база, таймаут догона или грязная реплика. Не пропуск строки.
    Misaligned,
    /// REST недоступен: строка отказа, поток событий не прерывается.
    RestUnavailable,
}

/// Одна строка `verify.csv`. Формат и колонки стабильны (ловит тест
/// `verify_header_is_stable`): `snapshot_seq` — `seq` REST-снапшота,
/// `book_seq` — `seq` РЕАЛЬНО сравненного состояния книги (новейший наш апдейт
/// с `seq` ≤ `snapshot_seq` после сейв+кольцо переигрывания). При overshoot
/// (снапшот между двумя нашими `seq`) `book_seq` < `snapshot_seq` — это норма,
/// а не рассинхрон: книга нашего символа между нашими событиями не меняется.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct VerifyRow {
    pub ts_utc: String,
    pub symbol: String,
    pub snapshot_seq: Option<u64>,
    pub book_seq: Option<u64>,
    pub mismatches: Option<u64>,
    pub verdict: VerifyVerdict,
}

/// Шапка `verify.csv` — имена и порядок как поля `VerifyRow`.
const VERIFY_HEADER: [&str; 6] = [
    "ts_utc",
    "symbol",
    "snapshot_seq",
    "book_seq",
    "mismatches",
    "verdict",
];

/// `verify.csv` — один на корень, рядом с `gaps.csv`.
pub fn verify_csv_path(root: &Path) -> PathBuf {
    root.join("verify.csv")
}

/// Создаёт `verify.csv` с шапкой, если его нет или он пуст.
pub fn ensure_verify_csv(path: &Path) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let needs_header = std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true);
    if needs_header {
        let file = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(path)?;
        let mut w = csv::WriterBuilder::new()
            .has_headers(false)
            .from_writer(file);
        w.write_record(VERIFY_HEADER)?;
        w.flush()?;
    }
    Ok(())
}

/// Дописывает строку; шапку создаёт тем же вызовом, если файла не было.
pub fn append_verify_row(path: &Path, row: &VerifyRow) -> anyhow::Result<()> {
    ensure_verify_csv(path)?;
    let file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    let mut w = csv::WriterBuilder::new()
        .has_headers(false)
        .from_writer(file);
    w.serialize(row)?;
    w.flush()?;
    Ok(())
}

/// Читает все строки.
pub fn read_verify_rows(path: &Path) -> anyhow::Result<Vec<VerifyRow>> {
    if std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true)
    {
        return Ok(Vec::new());
    }
    let mut r = csv::Reader::from_path(path)?;
    r.deserialize::<VerifyRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(anyhow::Error::from)
}

// ---------------------------------------------------------------------------
// Сообщения цикл → сайдкар
// ---------------------------------------------------------------------------

/// Форвард каждого обработанного `Update` плюс сброс эпохи при смене шагов.
/// `Reset` шлётся в начале каждой сессии (и при смене шагов): реплика чистится
/// под новые масштабы, старое кольцо и база инвалидируются.
#[derive(Debug, Clone)]
pub enum VerifyMsg {
    Update(Update),
    Reset { tick_e9: i64, step_e9: i64 },
}

/// Неблокирующая отправка. `try_send` не ждёт никогда, полный или закрытый
/// канал — это `false` и +1 к `skipped`, а не пауза цикла записи.
pub fn offer_verify_update(
    tx: &std::sync::mpsc::SyncSender<VerifyMsg>,
    msg: VerifyMsg,
    skipped: &mut u64,
) -> bool {
    match tx.try_send(msg) {
        Ok(()) => true,
        Err(_) => {
            *skipped = skipped.saturating_add(1);
            false
        }
    }
}

// ---------------------------------------------------------------------------
// Реплика сайдкара: живая книга + база + кольцо
// ---------------------------------------------------------------------------

/// Сейв реплики: состояние книги после всех наших апдейтов с `seq` ≤ `seq`.
/// `u` нужен для проверки непрерывности при переигрывании от сейва
/// (`Book::apply` ведёт поток по `u`, `seq` в контроле не участвует).
#[derive(Debug, Clone)]
struct Save {
    seq: u64,
    u: u64,
    book: Book,
}

/// Непрерывная реплика книги в сайдкаре: живая книга, сейвы для ≤-скана
/// и кольцо последних обновлений для переигрывания вперёд от сейва.
/// Выравнивание — по сквозному `seq` через ≤-скан (глобальный кросс-счётчик,
/// наши апдейты разрежены); `u`-контроль потока внутри `Book`
/// (включая рестарт `u == 1`) при этом не тронут.
pub struct VerifyState {
    replica: Book,
    tick_e9: i64,
    step_e9: i64,
    saves: VecDeque<Save>,
    verified_seq: Option<u64>,
    ring: VecDeque<Update>,
    ring_bytes: usize,
    dirty: bool,
    updates_since_save: usize,
    last_save_at: Option<Instant>,
}

impl VerifyState {
    pub fn new(tick_e9: i64, step_e9: i64) -> Self {
        Self {
            replica: Book::new(tick_e9, step_e9),
            tick_e9,
            step_e9,
            saves: VecDeque::new(),
            verified_seq: None,
            ring: VecDeque::new(),
            ring_bytes: 0,
            dirty: false,
            updates_since_save: 0,
            last_save_at: None,
        }
    }

    pub fn reset(&mut self, tick_e9: i64, step_e9: i64) {
        *self = Self::new(tick_e9, step_e9);
    }

    pub fn replica_seq(&self) -> Option<u64> {
        self.replica.last_seq()
    }

    pub fn is_dirty(&self) -> bool {
        self.dirty
    }

    pub fn base_seq(&self) -> Option<u64> {
        self.verified_seq
    }

    pub fn ring_len(&self) -> usize {
        self.ring.len()
    }

    pub fn apply_msg(&mut self, msg: VerifyMsg) {
        match msg {
            VerifyMsg::Update(up) => self.apply_forwarded(up),
            VerifyMsg::Reset { tick_e9, step_e9 } => self.reset(tick_e9, step_e9),
        }
    }

    fn update_bytes(up: &Update) -> usize {
        64 + (up.bids.len() + up.asks.len()) * 16
    }

    /// Применяет форварднутое обновление к реплике. Успех — в кольцо с кепом +
    /// периодический сейв (каждые N или 2с); снапшот/рестарт (`is_snapshot` или
    /// `u == 1`) начинает новую эпоху: кольцо и сейвы чистой эпохи
    /// инвалидируются, затем сразу сейв нового начала. Ошибка применения
    /// (разрыв `u`, пересечение, шаги) — грязная реплика и сброс сейвов.
    fn apply_forwarded(&mut self, up: Update) {
        let is_reset = up.is_snapshot || up.u == 1;
        match self.replica.apply(&up) {
            Ok(()) => {
                if is_reset {
                    self.ring.clear();
                    self.ring_bytes = 0;
                    self.saves.clear();
                    self.verified_seq = None;
                    self.dirty = false;
                    self.updates_since_save = 0;
                    self.push_save();
                } else {
                    let bytes = Self::update_bytes(&up);
                    self.ring.push_back(up);
                    self.ring_bytes += bytes;
                    while self.ring_bytes > VERIFY_RING_MAX_BYTES {
                        if let Some(old) = self.ring.pop_front() {
                            self.ring_bytes =
                                self.ring_bytes.saturating_sub(Self::update_bytes(&old));
                            let evicted_seq = old.seq;
                            self.saves.retain(|s| s.seq >= evicted_seq);
                        } else {
                            break;
                        }
                    }
                    self.updates_since_save += 1;
                    self.maybe_save();
                }
            }
            Err(_) => {
                self.dirty = true;
                self.saves.clear();
                self.verified_seq = None;
                self.updates_since_save = 0;
                self.last_save_at = None;
                if is_reset {
                    self.ring.clear();
                    self.ring_bytes = 0;
                }
            }
        }
    }

    fn push_save(&mut self) {
        let (Some(seq), Some(u)) = (self.replica.last_seq(), self.replica.last_u()) else {
            return;
        };
        let book = self.replica.clone();
        self.saves.push_back(Save { seq, u, book });
        self.updates_since_save = 0;
        self.last_save_at = Some(Instant::now());
        while self.saves.len() > VERIFY_SAVE_KEEP {
            self.saves.pop_front();
            if let Some(oldest) = self.saves.front() {
                let floor = oldest.seq;
                let mut kept_bytes = 0;
                self.ring.retain(|up| {
                    if up.seq > floor {
                        kept_bytes += Self::update_bytes(up);
                        true
                    } else {
                        false
                    }
                });
                self.ring_bytes = kept_bytes;
            }
        }
    }

    fn maybe_save(&mut self) {
        if self.saves.is_empty() {
            self.push_save();
            return;
        }
        if self.updates_since_save >= VERIFY_SAVE_EVERY_N {
            self.push_save();
            return;
        }
        if let Some(at) = self.last_save_at {
            if at.elapsed() >= VERIFY_SAVE_INTERVAL {
                self.push_save();
            }
        }
    }

    fn drain_available(&mut self, rx: &std::sync::mpsc::Receiver<VerifyMsg>) {
        while let Ok(msg) = rx.try_recv() {
            self.apply_msg(msg);
        }
    }

    /// Переигрывает кольцо от новейшего сейва с `seq` ≤ цели до цели на клоне.
    /// Возвращает книгу состояния «все наши апдейты с `seq` ≤ цели» плюс `seq`
    /// реально сравненного состояния (может быть < цели при overshoot).
    /// `None` — нет сейва ≤ цели (снапшот старше истории) или ошибка применения
    /// на отрезке (дыра в `u`): честный `misaligned`, а не выдумка.
    /// `seq` подряд НЕ требуется: `seq` глобальный и разрежен, непрерывность
    /// проверяется только по `u` через `Book::apply` от `save.u`.
    fn replay_to(&self, target_seq: u64) -> Option<(Book, u64)> {
        let save = self.saves.iter().rev().find(|s| s.seq <= target_seq)?;
        let mut cloned = save.book.clone();
        let mut actual = save.seq;
        for up in &self.ring {
            if up.seq <= save.seq {
                continue;
            }
            if up.seq > target_seq {
                continue;
            }
            if cloned.apply(up).is_err() {
                return None;
            }
            actual = up.seq;
        }
        Some((cloned, actual))
    }

    fn update_base(&mut self, new_base_seq: u64, new_base_book: Book) {
        self.verified_seq = Some(new_base_seq);
        let u = new_base_book.last_u().unwrap_or(0);
        if let Some(existing) = self.saves.iter_mut().find(|s| s.seq == new_base_seq) {
            existing.book = new_base_book;
            existing.u = u;
            return;
        }
        let pos = self
            .saves
            .iter()
            .position(|s| s.seq > new_base_seq)
            .unwrap_or(self.saves.len());
        self.saves.insert(
            pos,
            Save {
                seq: new_base_seq,
                u,
                book: new_base_book,
            },
        );
        while self.saves.len() > VERIFY_SAVE_KEEP {
            self.saves.pop_front();
            if let Some(oldest) = self.saves.front() {
                let floor = oldest.seq;
                let mut kept_bytes = 0;
                self.ring.retain(|up| {
                    if up.seq > floor {
                        kept_bytes += Self::update_bytes(up);
                        true
                    } else {
                        false
                    }
                });
                self.ring_bytes = kept_bytes;
            }
        }
    }

    fn write_misaligned(
        &self,
        symbol: &str,
        ts_utc: &str,
        snapshot_seq: Option<u64>,
        verify_csv: &Path,
    ) -> anyhow::Result<VerifyRow> {
        let row = VerifyRow {
            ts_utc: ts_utc.to_string(),
            symbol: symbol.to_string(),
            snapshot_seq,
            book_seq: self.replica_seq(),
            mismatches: None,
            verdict: VerifyVerdict::Misaligned,
        };
        append_verify_row(verify_csv, &row)?;
        Ok(row)
    }

    /// Один тик сверки: fetch → догон до max-seen ≥ `seq_s` → ≤-скан
    /// (сейв + кольцо) → сравнение → строка.
    /// Строка пишется всегда (отказ, рассинхрон, успех) — пропуска тика нет.
    /// `book_seq` в строке успеха — `seq` реально сравненного состояния
    /// (новейший наш апдейт ≤ снапшота), при overshoot меньше `snapshot_seq`.
    /// Чистая от wall-clock функция кроме входящего канала (догон с таймаутом),
    /// поэтому тестируется на фейковом `PublicRest` без сети.
    pub fn verify_tick<R: PublicRest>(
        &mut self,
        rest: &mut R,
        rx: &std::sync::mpsc::Receiver<VerifyMsg>,
        symbol: &str,
        ts_utc: &str,
        verify_csv: &Path,
    ) -> anyhow::Result<VerifyRow> {
        self.drain_available(rx);
        let snap = match fetch_orderbook_snapshot(rest, symbol, VERIFY_ORDERBOOK_LIMIT) {
            Err(_) => {
                let row = VerifyRow {
                    ts_utc: ts_utc.to_string(),
                    symbol: symbol.to_string(),
                    snapshot_seq: None,
                    book_seq: self.replica_seq(),
                    mismatches: None,
                    verdict: VerifyVerdict::RestUnavailable,
                };
                append_verify_row(verify_csv, &row)?;
                return Ok(row);
            }
            Ok(s) => s,
        };
        let snapshot_seq = snap.seq;
        if self.dirty {
            return self.write_misaligned(symbol, ts_utc, Some(snapshot_seq), verify_csv);
        }
        let needs_catchup = match self.replica_seq() {
            None => true,
            Some(replica_seq) => replica_seq < snapshot_seq,
        };
        if needs_catchup {
            let deadline = Instant::now() + VERIFY_CATCHUP_TIMEOUT;
            loop {
                match self.replica_seq() {
                    Some(cur) if cur >= snapshot_seq => break,
                    _ => {}
                }
                let now = Instant::now();
                if now >= deadline {
                    break;
                }
                match rx.recv_timeout(deadline - now) {
                    Ok(VerifyMsg::Reset { tick_e9, step_e9 }) => {
                        self.reset(tick_e9, step_e9);
                        return self.write_misaligned(
                            symbol,
                            ts_utc,
                            Some(snapshot_seq),
                            verify_csv,
                        );
                    }
                    Ok(VerifyMsg::Update(up)) => {
                        self.apply_forwarded(up);
                        if self.dirty {
                            return self.write_misaligned(
                                symbol,
                                ts_utc,
                                Some(snapshot_seq),
                                verify_csv,
                            );
                        }
                    }
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => break,
                    Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                }
            }
            let caught_up = match self.replica_seq() {
                Some(cur) => cur >= snapshot_seq,
                None => false,
            };
            if !caught_up {
                return self.write_misaligned(symbol, ts_utc, Some(snapshot_seq), verify_csv);
            }
            if self.dirty {
                return self.write_misaligned(symbol, ts_utc, Some(snapshot_seq), verify_csv);
            }
        }
        let Some((aligned, actual_seq)) = self.replay_to(snapshot_seq) else {
            return self.write_misaligned(symbol, ts_utc, Some(snapshot_seq), verify_csv);
        };
        let diff = compare_with_snapshot(&aligned, &snap, self.tick_e9, self.step_e9);
        let row = VerifyRow {
            ts_utc: ts_utc.to_string(),
            symbol: symbol.to_string(),
            snapshot_seq: Some(snapshot_seq),
            book_seq: Some(actual_seq),
            mismatches: Some(diff.total() as u64),
            verdict: if diff.is_clean() {
                VerifyVerdict::Ok
            } else {
                VerifyVerdict::Mismatch
            },
        };
        append_verify_row(verify_csv, &row)?;
        // Анатомия расхождения в stderr (раз в 300 с — не спам): первые 5
        // позиций, чтобы отличать позиционный каскад от дрейфа книги.
        if !diff.is_clean() {
            for m in diff
                .bid_mismatches
                .iter()
                .chain(diff.ask_mismatches.iter())
                .take(5)
            {
                eprintln!(
                    "verify mismatch: side={:?} tick={} snap_qty={:?} book_qty={:?}",
                    m.side, m.tick, m.snapshot_qty_e9, m.book_qty_e9
                );
            }
        }
        self.update_base(actual_seq, aligned);
        Ok(row)
    }
}

// ---------------------------------------------------------------------------
// Одна прямая сверка без догона (переиспользуемый generic, без сети в тестах)
// ---------------------------------------------------------------------------

/// Прямое сравнение книги ровно на `seq` снапшота. Рассинхрон — `misaligned`,
/// а не `mismatch`; отказ REST — строка `rest_unavailable` обычным возвратом.
/// Ошибку возвращает только запись в CSV.
pub fn verify_one_tick<R: PublicRest>(
    rest: &mut R,
    book: &Book,
    tick_e9: i64,
    step_e9: i64,
    symbol: &str,
    ts_utc: &str,
    verify_csv: &Path,
) -> anyhow::Result<VerifyRow> {
    let book_seq = book.last_seq();
    let row = match fetch_orderbook_snapshot(rest, symbol, VERIFY_ORDERBOOK_LIMIT) {
        Err(_) => VerifyRow {
            ts_utc: ts_utc.to_string(),
            symbol: symbol.to_string(),
            snapshot_seq: None,
            book_seq,
            mismatches: None,
            verdict: VerifyVerdict::RestUnavailable,
        },
        Ok(snap) => {
            if book_seq != Some(snap.seq) {
                VerifyRow {
                    ts_utc: ts_utc.to_string(),
                    symbol: symbol.to_string(),
                    snapshot_seq: Some(snap.seq),
                    book_seq,
                    mismatches: None,
                    verdict: VerifyVerdict::Misaligned,
                }
            } else {
                let diff = compare_with_snapshot(book, &snap, tick_e9, step_e9);
                VerifyRow {
                    ts_utc: ts_utc.to_string(),
                    symbol: symbol.to_string(),
                    snapshot_seq: Some(snap.seq),
                    book_seq,
                    mismatches: Some(diff.total() as u64),
                    verdict: if diff.is_clean() {
                        VerifyVerdict::Ok
                    } else {
                        VerifyVerdict::Mismatch
                    },
                }
            }
        }
    };
    append_verify_row(verify_csv, &row)?;
    Ok(row)
}

// ---------------------------------------------------------------------------
// Сайдкар: ОС-поток со своим соединением (как авторитет шагов в 0.7)
// ---------------------------------------------------------------------------

/// Запускает сайдкар: возвращает отправителя обновлений циклу записи.
/// Приёмник и своё соединение уезжают в `std::thread` — блокирующий HTTP
/// держит только этот поток и ни один воркер tokio-рантайма (дефект В-9).
/// Поток откреплён (JoinHandle дропается): остановка — закрытием канала
/// (все отправители дропнуты), поток выходит сам. REST в цикле записи нет.
pub fn spawn_verify_sidecar(
    base_url: String,
    symbol: String,
    root: PathBuf,
    tick_e9: i64,
    step_e9: i64,
) -> std::sync::mpsc::SyncSender<VerifyMsg> {
    let (tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(VERIFY_UPDATE_CHANNEL_CAPACITY);
    match std::thread::Builder::new()
        .name("verify-sidecar".to_string())
        .spawn(move || {
            let rest = match BybitPublicRest::new(base_url) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("verify: REST-клиент не создался ({e}), сайдкар остановлен");
                    return;
                }
            };
            run_verify_loop_with_rest(rest, rx, symbol, root, tick_e9, step_e9);
        }) {
        Ok(_) => {}
        Err(e) => eprintln!("verify: сайдкар не запустился ({e})"),
    }
    tx
}

#[allow(clippy::too_many_lines)]
fn run_verify_loop_with_rest<R: PublicRest>(
    mut rest: R,
    rx: std::sync::mpsc::Receiver<VerifyMsg>,
    symbol: String,
    root: PathBuf,
    tick_e9: i64,
    step_e9: i64,
) {
    let verify_csv = verify_csv_path(&root);
    if let Err(e) = ensure_verify_csv(&verify_csv) {
        eprintln!(
            "verify: {} не создался ({e}), сайдкар остановлен",
            verify_csv.display()
        );
        return;
    }
    let mut state = VerifyState::new(tick_e9, step_e9);
    let mut first = true;
    loop {
        if first {
            match rx.recv() {
                Ok(msg) => state.apply_msg(msg),
                Err(_) => break,
            }
            state.drain_available(&rx);
            if state.replica_seq().is_none() {
                continue;
            }
            let ts_utc = ts_utc_of_ns(SystemClock.now_ns());
            if let Err(e) = state.verify_tick(&mut rest, &rx, &symbol, &ts_utc, &verify_csv) {
                eprintln!("verify: строка не записалась ({e})");
            }
            first = false;
        } else {
            let deadline = Instant::now() + Duration::from_secs(VERIFY_INTERVAL_SECS);
            loop {
                let now = Instant::now();
                if now >= deadline {
                    break;
                }
                match rx.recv_timeout(deadline - now) {
                    Ok(msg) => state.apply_msg(msg),
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => break,
                    Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => return,
                }
            }
            let ts_utc = ts_utc_of_ns(SystemClock.now_ns());
            if let Err(e) = state.verify_tick(&mut rest, &rx, &symbol, &ts_utc, &verify_csv) {
                eprintln!("verify: строка не записалась ({e})");
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Тесты шага 0.8 (фейковый PublicRest, без сети; P2 — один воркер + закрытый порт)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    use crate::bybit::rest::RestError;

    const TICK_E9: i64 = 10_000_000; // 0.01
    const STEP_E9: i64 = 1_000_000; // 0.001
    const SYMBOL: &str = "TSTUSDT";
    const TS_UTC: &str = "2026-09-08T00:05:00Z";

    struct FakeRest {
        responses: VecDeque<Result<String, RestError>>,
    }

    impl FakeRest {
        fn with_responses(responses: Vec<Result<String, RestError>>) -> Self {
            Self {
                responses: responses.into(),
            }
        }
    }

    impl PublicRest for FakeRest {
        fn get(&mut self, _path: &str, _query: &[(&str, &str)]) -> Result<String, RestError> {
            self.responses
                .pop_front()
                .expect("тест не подготовил столько ответов")
        }
    }

    fn snapshot_body(seq: u64, bid_qty: &str) -> String {
        // `u` REST — другой счётчик, чем `u` WS: заведомо отличается от `u`
        // реплики (как вживую 20.6M против 131.9M), выравнивание — только по `seq`.
        let rest_u = 20_000_000 + seq;
        format!(
            r#"{{"retCode":0,"retMsg":"OK","result":{{"s":"{SYMBOL}","b":[["150.00","{bid_qty}"]],"a":[["150.01","3.0"]],"ts":1757800000000,"u":{rest_u},"seq":{seq}}}}}"#
        )
    }

    fn book_with_seq(seq: u64) -> Book {
        let mut book = Book::new(TICK_E9, STEP_E9);
        book.apply(&Update {
            is_snapshot: true,
            u: 1_000_000 + seq,
            seq,
            cts_ms: 1_757_800_000_000,
            bids: vec![(150_000_000_000, 2_500_000_000)],
            asks: vec![(150_010_000_000, 3_000_000_000)],
        })
        .unwrap();
        book
    }

    fn empty_delta(seq: u64) -> Update {
        Update {
            is_snapshot: false,
            u: 1_000_000 + seq,
            seq,
            cts_ms: 1_757_800_000_001,
            bids: vec![],
            asks: vec![],
        }
    }

    fn snapshot_update(seq: u64) -> Update {
        Update {
            is_snapshot: true,
            u: 1_000_000 + seq,
            seq,
            cts_ms: 1_757_800_000_000,
            bids: vec![(150_000_000_000, 2_500_000_000)],
            asks: vec![(150_010_000_000, 3_000_000_000)],
        }
    }

    fn sparse_snapshot(seq: u64, u: u64) -> Update {
        Update {
            is_snapshot: true,
            u,
            seq,
            cts_ms: 1_757_800_000_000,
            bids: vec![(150_000_000_000, 2_500_000_000)],
            asks: vec![(150_010_000_000, 3_000_000_000)],
        }
    }

    fn sparse_delta(seq: u64, u: u64) -> Update {
        Update {
            is_snapshot: false,
            u,
            seq,
            cts_ms: 1_757_800_000_001,
            bids: vec![],
            asks: vec![],
        }
    }

    fn verify_direct_in_tmp(rest: &mut FakeRest, book: &Book) -> (tempfile::TempDir, VerifyRow) {
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let row = verify_one_tick(rest, book, TICK_E9, STEP_E9, SYMBOL, TS_UTC, &csv).unwrap();
        (dir, row)
    }

    /// Чистая сверка: книга ровно на `seq` снапшота — `ok` и ноль
    /// (`u` при этом заведомо разные — как вживую).
    #[test]
    fn clean_check_writes_ok_with_zero_mismatches() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(7, "2.5"))]);
        let (_dir, row) = verify_direct_in_tmp(&mut rest, &book_with_seq(7));
        assert_eq!(row.verdict, VerifyVerdict::Ok);
        assert_eq!(row.mismatches, Some(0));
        assert_eq!(row.snapshot_seq, Some(7));
        assert_eq!(row.book_seq, Some(7));
    }

    /// Рассинхрон `seq` в прямой сверке — `misaligned`, а не `mismatch`.
    #[test]
    fn seq_desync_is_misaligned_not_mismatch() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(9, "2.5"))]);
        let (_dir, row) = verify_direct_in_tmp(&mut rest, &book_with_seq(7));
        assert_eq!(row.verdict, VerifyVerdict::Misaligned);
        assert_eq!(row.mismatches, None);
    }

    /// Недоступный REST — строка отказа обычным возвратом, дважды подряд.
    #[test]
    fn rest_outage_writes_refusal_rows_without_stopping() {
        let mut rest = FakeRest::with_responses(vec![
            Err(RestError::Transport("connection reset".to_string())),
            Err(RestError::Transport("connection reset".to_string())),
        ]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let book = book_with_seq(7);
        for _ in 0..2 {
            let row =
                verify_one_tick(&mut rest, &book, TICK_E9, STEP_E9, SYMBOL, TS_UTC, &csv).unwrap();
            assert_eq!(row.verdict, VerifyVerdict::RestUnavailable);
            assert_eq!(row.snapshot_seq, None);
            assert_eq!(row.mismatches, None);
        }
        assert_eq!(read_verify_rows(&csv).unwrap().len(), 2);
    }

    /// Тот же `seq`, но размер perturbed — настоящий `mismatch` со счётом.
    #[test]
    fn perturbed_snapshot_is_mismatch_with_count() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(7, "999.0"))]);
        let (_dir, row) = verify_direct_in_tmp(&mut rest, &book_with_seq(7));
        assert_eq!(row.verdict, VerifyVerdict::Mismatch);
        assert_eq!(row.mismatches, Some(1));
    }

    /// Шапка — контракт, строка отказа читается назад.
    #[test]
    fn verify_header_is_stable_and_refusal_row_round_trips() {
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        ensure_verify_csv(&csv).unwrap();
        ensure_verify_csv(&csv).unwrap();
        assert_eq!(
            std::fs::read_to_string(&csv).unwrap().trim_end(),
            "ts_utc,symbol,snapshot_seq,book_seq,mismatches,verdict"
        );
        assert!(read_verify_rows(&csv).unwrap().is_empty());
        let row = VerifyRow {
            ts_utc: TS_UTC.to_string(),
            symbol: SYMBOL.to_string(),
            snapshot_seq: None,
            book_seq: Some(7),
            mismatches: None,
            verdict: VerifyVerdict::RestUnavailable,
        };
        append_verify_row(&csv, &row).unwrap();
        assert_eq!(read_verify_rows(&csv).unwrap(), vec![row]);
    }

    /// Цикл записи не блокируется: `try_send` синхронен — возврат и есть
    /// доказательство. Полный канал — `false` и +1, дропит нового.
    #[test]
    fn offer_never_waits_and_counts_skips_on_full_channel() {
        let (tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(1);
        let mut skipped = 0u64;
        assert!(offer_verify_update(
            &tx,
            VerifyMsg::Update(snapshot_update(7)),
            &mut skipped
        ));
        assert_eq!(skipped, 0);
        assert!(!offer_verify_update(
            &tx,
            VerifyMsg::Update(snapshot_update(8)),
            &mut skipped
        ));
        assert_eq!(skipped, 1);
        let kept = rx.try_recv().unwrap();
        match kept {
            VerifyMsg::Update(up) => assert_eq!(up.seq, 7, "дропит нового, очередь цела"),
            VerifyMsg::Reset { .. } => panic!("ждали Update"),
        }
    }

    /// Сверка идёт ровно на топ-50: запрос шлёт лимит 50.
    #[test]
    fn check_requests_top50_not_the_snapshot_ceiling() {
        use std::cell::RefCell;
        use std::rc::Rc;
        struct Spy {
            limit: Rc<RefCell<Option<String>>>,
        }
        impl PublicRest for Spy {
            fn get(&mut self, _path: &str, query: &[(&str, &str)]) -> Result<String, RestError> {
                for (k, v) in query {
                    if *k == "limit" {
                        *self.limit.borrow_mut() = Some(v.to_string());
                    }
                }
                Ok(snapshot_body(7, "2.5"))
            }
        }
        let seen = Rc::new(RefCell::new(None));
        let mut spy = Spy {
            limit: seen.clone(),
        };
        let dir = tempfile::tempdir().unwrap();
        verify_one_tick(
            &mut spy,
            &book_with_seq(7),
            TICK_E9,
            STEP_E9,
            SYMBOL,
            TS_UTC,
            &verify_csv_path(dir.path()),
        )
        .unwrap();
        assert_eq!(*seen.borrow(), Some(VERIFY_ORDERBOOK_LIMIT.to_string()));
    }

    /// Р1: снапшот с `seq` впереди книги даёт `Ok` после догона, а не `Misaligned`.
    /// Книга на 7, снапшот на 9 с тем же содержимым; дельты 8-9 (пустые, только
    /// двигают `seq`) приходят уже во время догона — старый `u`-порядок дал бы вечный `Misaligned`.
    #[test]
    fn snapshot_ahead_catches_up_to_ok_instead_of_misaligned() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(9, "2.5"))]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(snapshot_update(7)));
        assert_eq!(state.replica_seq(), Some(7));
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(50));
            tx.send(VerifyMsg::Update(empty_delta(8))).unwrap();
            tx.send(VerifyMsg::Update(empty_delta(9))).unwrap();
        });
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(
            row.verdict,
            VerifyVerdict::Ok,
            "догон до seq=9 обязан дать Ok: {row:?}"
        );
        assert_eq!(row.snapshot_seq, Some(9));
        assert_eq!(row.book_seq, Some(9));
        assert_eq!(row.mismatches, Some(0));
        assert_eq!(state.base_seq(), Some(9));
    }

    /// Снапшот позади реплики переигрывается кольцом от базы: книга на 10,
    /// база на 7, снапшот на 8 с тем же содержимым — `Ok` на равном `seq`.
    #[test]
    fn snapshot_behind_replays_ring_to_ok() {
        let mut rest = FakeRest::with_responses(vec![
            Ok(snapshot_body(7, "2.5")),
            Ok(snapshot_body(8, "2.5")),
        ]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(snapshot_update(7)));
        let first = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(first.verdict, VerifyVerdict::Ok);
        state.apply_msg(VerifyMsg::Update(empty_delta(8)));
        state.apply_msg(VerifyMsg::Update(empty_delta(9)));
        state.apply_msg(VerifyMsg::Update(empty_delta(10)));
        assert_eq!(state.replica_seq(), Some(10));
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(row.verdict, VerifyVerdict::Ok);
        assert_eq!(row.snapshot_seq, Some(8));
        assert_eq!(row.book_seq, Some(8));
    }

    /// Снапшот вне кольца — честный `misaligned`: цель старше базы.
    /// База на 7, реплика на 10, снапшот на 5 — переиграть не из чего.
    #[test]
    fn snapshot_outside_ring_is_misaligned() {
        let mut rest = FakeRest::with_responses(vec![
            Ok(snapshot_body(7, "2.5")),
            Ok(snapshot_body(5, "2.5")),
        ]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(snapshot_update(7)));
        let first = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(first.verdict, VerifyVerdict::Ok);
        state.apply_msg(VerifyMsg::Update(empty_delta(8)));
        state.apply_msg(VerifyMsg::Update(empty_delta(9)));
        state.apply_msg(VerifyMsg::Update(empty_delta(10)));
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(row.verdict, VerifyVerdict::Misaligned);
        assert_eq!(row.snapshot_seq, Some(5));
        assert_eq!(row.mismatches, None);
        assert_eq!(read_verify_rows(&csv).unwrap().len(), 2);
    }

    /// Ремонт Р1: overshoot — снапшот между двумя нашими seq даёт `Ok` через
    /// сейв+кольцо. Наши seq 1_000_000 и 1_011_600 (разрыв 11.6K как вживую),
    /// `u` подряд (1000, 1001); снапшот на 1_005_000 тем же содержимым —
    /// состояние для него = книга после 1_000_000, `book_seq` = 1_000_000.
    #[test]
    fn overshoot_between_sparse_seqs_is_ok_via_save_and_ring() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(1_005_000, "2.5"))]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(sparse_snapshot(1_000_000, 1000)));
        state.apply_msg(VerifyMsg::Update(sparse_delta(1_011_600, 1001)));
        assert_eq!(state.replica_seq(), Some(1_011_600));
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(
            row.verdict,
            VerifyVerdict::Ok,
            "overshoot обязан дать Ok: {row:?}"
        );
        assert_eq!(row.snapshot_seq, Some(1_005_000));
        assert_eq!(row.book_seq, Some(1_000_000));
        assert_eq!(row.mismatches, Some(0));
    }

    /// Точное попадание при разреженном `seq`: снапшот ровно на нашем seq —
    /// `Ok` с `book_seq` равным снапшоту.
    #[test]
    fn exact_hit_on_sparse_seq_is_ok() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(1_011_600, "2.5"))]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(sparse_snapshot(1_000_000, 1000)));
        state.apply_msg(VerifyMsg::Update(sparse_delta(1_011_600, 1001)));
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(row.verdict, VerifyVerdict::Ok);
        assert_eq!(row.snapshot_seq, Some(1_011_600));
        assert_eq!(row.book_seq, Some(1_011_600));
    }

    /// Снапшот старше сейвов — честный `misaligned`: сейв на 1_000_000,
    /// снапшот на 999_000 — переиграть не из чего.
    #[test]
    fn snapshot_older_than_saves_is_misaligned() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(999_000, "2.5"))]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(sparse_snapshot(1_000_000, 1000)));
        state.apply_msg(VerifyMsg::Update(sparse_delta(1_011_600, 1001)));
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(row.verdict, VerifyVerdict::Misaligned);
        assert_eq!(row.snapshot_seq, Some(999_000));
        assert_eq!(row.mismatches, None);
    }

    /// Дыра в `u` на отрезке — честный `misaligned`: чистая реплика на
    /// 1_011_600 (u=1002), но середина кольца (1_005_000, u=1001) потеряна
    /// (эвикция/переполнение) — переигрывание от сейва 1_000_000 рвётся по `u`.
    #[test]
    fn u_gap_on_replay_segment_is_misaligned() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(1_011_600, "2.5"))]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(sparse_snapshot(1_000_000, 1000)));
        state.apply_msg(VerifyMsg::Update(sparse_delta(1_005_000, 1001)));
        state.apply_msg(VerifyMsg::Update(sparse_delta(1_011_600, 1002)));
        assert_eq!(state.replica_seq(), Some(1_011_600));
        assert!(!state.is_dirty());
        // Симулируем эвикцию середины кольца: реплика цела, а отрезок дырявый.
        state.ring.retain(|up| up.seq != 1_005_000);
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(row.verdict, VerifyVerdict::Misaligned);
        assert_eq!(row.snapshot_seq, Some(1_011_600));
        assert_eq!(row.mismatches, None);
    }

    /// Грязная реплика (разрыв `u` в форварде) — честный `misaligned` строкой,
    /// а не пропуск тика и не выдумка сравнения.
    #[test]
    fn dirty_replica_writes_misaligned_row_instead_of_skipping() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(7, "2.5"))]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
        let mut state = VerifyState::new(TICK_E9, STEP_E9);
        state.apply_msg(VerifyMsg::Update(snapshot_update(7)));
        state.apply_msg(VerifyMsg::Update(empty_delta(9)));
        assert!(state.is_dirty());
        let row = state
            .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
            .unwrap();
        assert_eq!(row.verdict, VerifyVerdict::Misaligned);
        assert_eq!(row.snapshot_seq, Some(7));
        assert_eq!(read_verify_rows(&csv).unwrap().len(), 1);
    }

    /// Р2: сайдкар — ОС-поток и при одном воркере тикер не рвёт каденс.
    /// Спавн идёт вне рантайма (старый `tokio::spawn` там паникует), HTTP —
    /// на закрытый порт 127.0.0.1:9 (отказ быстрый, без 10с ожидания).
    #[test]
    fn sidecar_os_thread_does_not_block_single_worker_runtime() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        let csv = verify_csv_path(&root);
        let tx = spawn_verify_sidecar(
            "http://127.0.0.1:9".to_string(),
            SYMBOL.to_string(),
            root,
            TICK_E9,
            STEP_E9,
        );
        let mut skipped = 0u64;
        assert!(offer_verify_update(
            &tx,
            VerifyMsg::Update(snapshot_update(7)),
            &mut skipped
        ));
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .unwrap();
        rt.block_on(async {
            let start = Instant::now();
            let mut interval = tokio::time::interval(Duration::from_millis(25));
            interval.tick().await;
            for _ in 0..10 {
                interval.tick().await;
            }
            let elapsed = start.elapsed();
            assert!(
                elapsed < Duration::from_secs(2),
                "тикер встал на {elapsed:?} при одном воркере"
            );
        });
        let mut rows = Vec::new();
        for _ in 0..50 {
            rows = read_verify_rows(&csv).unwrap_or_default();
            if !rows.is_empty() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        assert!(
            !rows.is_empty(),
            "первая строка не появилась вскоре после первого снапшота"
        );
        assert_eq!(rows[0].verdict, VerifyVerdict::RestUnavailable);
        drop(tx);
    }
}
