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
use crate::bybit::verify::{compare_with_bracket, compare_with_snapshot};
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

    /// Первое `seq` строго выше цели — верхняя граница скобки. Минимум по кольцу,
    /// а не первый в порядке прибытия: порядок прибытия и есть порядок `seq`.
    fn first_seq_above(&self, target_seq: u64) -> Option<u64> {
        self.ring
            .iter()
            .filter(|up| up.seq > target_seq)
            .map(|up| up.seq)
            .min()
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
    /// (сейв + кольцо) → скобки → строка.
    /// Нужны ДВА состояния: `before` (новейшее ≤ `seq_s`) и `after` (первое строго
    /// выше `seq_s`, догон входящим потоком в пределах catch-up таймаута).
    /// Mismatch — уровень отличается от ОБОИХ состояний; `book_seq` в строке —
    /// `seq` состояния `before`. Нет `before` (кольцо не покрывает) — `misaligned`;
    /// нет `after` (таймаут) — решение по одному `before`, а не пропуск.
    /// Строка пишется всегда (отказ, рассинхрон, успех) — пропуска тика нет.
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
        let before_diff = compare_with_snapshot(&aligned, &snap, self.tick_e9, self.step_e9);
        if before_diff.is_clean() {
            let row = VerifyRow {
                ts_utc: ts_utc.to_string(),
                symbol: symbol.to_string(),
                snapshot_seq: Some(snapshot_seq),
                book_seq: Some(actual_seq),
                mismatches: Some(0),
                verdict: VerifyVerdict::Ok,
            };
            append_verify_row(verify_csv, &row)?;
            self.update_base(actual_seq, aligned);
            return Ok(row);
        }
        // `before` расходится — ищем верхнюю границу скобки. Сначала всё, что
        // уже приехало во время fetch, затем ждём входящий поток в пределах
        // существующего catch-up таймаута. Нет `after` — решение по `before`.
        self.drain_available(rx);
        if self.dirty {
            return self.write_misaligned(symbol, ts_utc, Some(snapshot_seq), verify_csv);
        }
        let mut after_book: Option<Book> = None;
        if let Some(first_above) = self.first_seq_above(snapshot_seq) {
            if let Some((after, _)) = self.replay_to(first_above) {
                after_book = Some(after);
            }
        }
        if after_book.is_none() {
            let deadline = Instant::now() + VERIFY_CATCHUP_TIMEOUT;
            loop {
                if self.first_seq_above(snapshot_seq).is_some() {
                    break;
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
            if let Some(first_above) = self.first_seq_above(snapshot_seq) {
                if let Some((after, _)) = self.replay_to(first_above) {
                    after_book = Some(after);
                }
            }
        }
        let diff = match after_book {
            Some(ref after) => {
                compare_with_bracket(&aligned, Some(after), &snap, self.tick_e9, self.step_e9)
            }
            None => before_diff,
        };
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
        // уровней, чтобы отличать сдвиг от дрейфа книги.
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
mod tests;
