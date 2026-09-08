//! `verify.csv` сайдкаром при записи (шаг 0.8 плана, Decision 24).
//!
//! Отдельная tokio-задача раз в 5 минут берёт REST-снапшот
//! (`GET /v5/market/orderbook`), сверяет его с книгой по `u` и пишет строку в
//! `verify.csv` рядом с `gaps.csv`. Книгу получает клоном через канал,
//! событийного пути не касается: вызывать сверку из цикла записи нельзя — это
//! дефект 0.7 второй раз (`SETTLED.md` В-1).
//!
//! # Почему выравнивание — отдельный вердикт, а не «грязный дифф»
//!
//! Клон книги и REST-снапшот берутся в разные моменты: `book_u != snapshot_u`
//! здесь норма гонки, а не порча данных. Поэтому несовпадение `u` пишется
//! вердиктом `misaligned`, а не `mismatch` — то же различие, что
//! `Verifier::verify_at_u` проводит между `AlignmentError` и `SnapshotDiff`.
//! Недоступный REST — строка отказа `rest_unavailable`: поток событий при этом
//! не прерывается, сайдкар пробует снова через 5 минут.
//!
//! # Канал книга → сайдкар
//!
//! Отправитель (цикл записи) шлёт `BookFrame` раз в 5 минут строго через
//! `try_send`: ожидания нет никогда. Канал ёмкостью ровно один — самый свежий
//! снапшот; переполнение означает, что сайдкар не успел забрать предыдущий, и
//! новый дропается со счётчиком пропусков (`offer_book_snapshot`). Тихий
//! дроп внутри дрена невозможен по построению: лежать в канале может не больше
//! одного кадра.
//!
//! Клон `Book` — это `Vec`-меммув at most 2 × 128 уровней, раз в 5 минут. В
//! счётчик аллокаций горячего пути он не входит: тот меряет
//! `stage_book_update`/`stage_trade` пособытийно (`record.rs`), а эти функции
//! здесь не вызываются и не меняются.
//!
//! # Формат строки `verify.csv`
//!
//! `ts_utc,symbol,snapshot_u,book_u,mismatches,verdict`, где `verdict` — один
//! из `ok` / `mismatch` / `misaligned` / `rest_unavailable`. Пустые поля
//! отказа и рассинхрона (`snapshot_u` при отказе REST, `mismatches` везде,
//! кроме настоящей сверки) — это `None`, а не ноль: ноль расхождений бывает
//! только у вердикта `ok`. CSV — исключение Decision 23 для метаданных-обочин;
//! рыночных данных в этом файле нет.

use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::book::Book;
use crate::bybit::conn::{Clock, SystemClock};
use crate::bybit::rest::{fetch_orderbook_snapshot, BybitPublicRest, PublicRest};
use crate::bybit::verify::compare_with_snapshot;
use crate::commands::record::ts_utc_of_ns;

/// Каденция сверки: строка в `verify.csv` раз в 5 минут (шаг 0.8).
/// Та же константа питает тикер отправителя в `run_session` — две стороны
/// обязаны тикать в одном ритме, а не каждая со своим литералом.
pub const VERIFY_INTERVAL_SECS: u64 = 300;

/// Ёмкость канала книга → сайдкар: ровно один свежий снапшот (см. doc модуля).
/// Больше — значило бы молча стареющие в очереди клоны; меньше нельзя.
pub const VERIFY_CHANNEL_CAPACITY: usize = 1;

/// Глубина REST-снапшота для сверки: топ-50 живой книги (шаг 0.6).
/// `compare_with_snapshot` режет книгу до 50, а снапшот — нет, поэтому лимит
/// обязан равняться 50, а не потолку `ORDERBOOK_SNAPSHOT_LIMIT`: лишние уровни
/// за топом дали бы ложные `mismatch` на позициях, которых книга не держит.
pub const VERIFY_ORDERBOOK_LIMIT: u32 = 50;

/// Вердикт одной сверки. Сериализуется snake_case — те же слова в файле, что
/// в варианте (дрейф ловит тест `verify_header_is_stable`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VerifyVerdict {
    /// Книга на `u` снапшота, ноль расхождений топ-50.
    Ok,
    /// Книга на `u` снапшота, топ-50 разошёлся (`mismatches` > 0).
    Mismatch,
    /// `book_u != snapshot_u`: гонка моментов, а не грязная книга.
    Misaligned,
    /// REST недоступен: строка отказа, поток событий не прерывается.
    RestUnavailable,
}

/// Одна строка `verify.csv`.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct VerifyRow {
    pub ts_utc: String,
    pub symbol: String,
    pub snapshot_u: Option<u64>,
    pub book_u: Option<u64>,
    pub mismatches: Option<u64>,
    pub verdict: VerifyVerdict,
}

/// Шапка `verify.csv` — имена и порядок как поля `VerifyRow` (тот же приём,
/// что `GAPS_HEADER` в `record.rs`: шапка пишется вручную, файл с нулём строк
/// несёт шапку, а не отсутствует).
const VERIFY_HEADER: [&str; 6] = [
    "ts_utc",
    "symbol",
    "snapshot_u",
    "book_u",
    "mismatches",
    "verdict",
];

/// `verify.csv` — один на корень, рядом с `gaps.csv`.
pub fn verify_csv_path(root: &Path) -> PathBuf {
    root.join("verify.csv")
}

/// Создаёт `verify.csv` с шапкой, если его нет или он пуст. Существующий
/// непустой не трогает — перезапуск записи не имеет права терять уже
/// записанные сверки.
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

/// Читает все строки. Пустого файла после `ensure_verify_csv` быть не должно,
/// но подсунутый напрямую — это ноль строк, а не ошибка разбора.
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
// Кадр книги через канал
// ---------------------------------------------------------------------------

/// Снапшот книги для сайдкара: сам клон плюс масштабы, которыми его сравнивать
/// (`compare_with_snapshot` берёт `tick_e9`/`step_e9` параметрами — у `Book`
/// своих геттеров масштаба нет, и это правильно: масштаб — свойство файла и
/// потока, а не книги).
#[derive(Debug, Clone)]
pub struct BookFrame {
    pub book: Book,
    pub tick_e9: i64,
    pub step_e9: i64,
}

/// Неблокирующая отправка кадра. `try_send` синхронен по построению API: этот
/// вызов не ждёт никогда, полный канал — это `false` и +1 к `skipped`, а не
/// пауза цикла записи. Закрытый канал (сайдкар умер) — тоже `false`, а не
/// паника: запись рыночных данных важнее сверки.
pub fn offer_book_snapshot(
    tx: &tokio::sync::mpsc::Sender<BookFrame>,
    frame: BookFrame,
    skipped: &mut u64,
) -> bool {
    match tx.try_send(frame) {
        Ok(()) => true,
        Err(_) => {
            *skipped = skipped.saturating_add(1);
            false
        }
    }
}

/// Дрен канала сайдкара: забирает всё, возвращает только самый свежий.
/// `None` — кадров ещё не было: тик пропускается без строки, а не с выдумкой.
fn drain_latest_book(rx: &mut tokio::sync::mpsc::Receiver<BookFrame>) -> Option<BookFrame> {
    let mut latest = None;
    while let Ok(frame) = rx.try_recv() {
        latest = Some(frame);
    }
    latest
}

// ---------------------------------------------------------------------------
// Одна сверка: REST → выравнивание по u → сравнение → строка
// ---------------------------------------------------------------------------

/// Один тик сайдкара. Чистая от времени функция (метка приходит параметром),
/// поэтому тестируется на фейковом `PublicRest` без сети и без рантайма:
/// чистая сверка даёт `ok` с нулём, рассинхрон `u` — `misaligned`, а не
/// `mismatch`, отказ REST — строку `rest_unavailable` обычным возвратом.
/// Ошибку возвращает только запись в CSV — ни сверка, ни отказ сети её не дают.
pub fn verify_one_tick<R: PublicRest>(
    rest: &mut R,
    frame: &BookFrame,
    symbol: &str,
    ts_utc: &str,
    verify_csv: &Path,
) -> anyhow::Result<VerifyRow> {
    let book_u = frame.book.last_u();
    let row = match fetch_orderbook_snapshot(rest, symbol, VERIFY_ORDERBOOK_LIMIT) {
        Err(_) => VerifyRow {
            ts_utc: ts_utc.to_string(),
            symbol: symbol.to_string(),
            snapshot_u: None,
            book_u,
            mismatches: None,
            verdict: VerifyVerdict::RestUnavailable,
        },
        Ok(snap) => {
            if book_u != Some(snap.u) {
                VerifyRow {
                    ts_utc: ts_utc.to_string(),
                    symbol: symbol.to_string(),
                    snapshot_u: Some(snap.u),
                    book_u,
                    mismatches: None,
                    verdict: VerifyVerdict::Misaligned,
                }
            } else {
                let diff = compare_with_snapshot(&frame.book, &snap, frame.tick_e9, frame.step_e9);
                VerifyRow {
                    ts_utc: ts_utc.to_string(),
                    symbol: symbol.to_string(),
                    snapshot_u: Some(snap.u),
                    book_u,
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
// Сайдкар: tokio-задача со своим соединением
// ---------------------------------------------------------------------------

/// Запускает сайдкар: канал отдаёт вызывающему (циклу записи), приёмник и
/// своё соединение (`BybitPublicRest`) уезжают в tokio-задачу. REST в цикле
/// записи не вызывается нигде — только здесь, и только готовый кадр книги
/// пересекает границу через `try_send`.
pub fn spawn_verify_sidecar(
    base_url: String,
    symbol: String,
    root: PathBuf,
) -> (
    tokio::sync::mpsc::Sender<BookFrame>,
    tokio::task::JoinHandle<()>,
) {
    let (tx, rx) = tokio::sync::mpsc::channel::<BookFrame>(VERIFY_CHANNEL_CAPACITY);
    let handle = tokio::spawn(run_verify_loop(rx, base_url, symbol, root));
    (tx, handle)
}

async fn run_verify_loop(
    mut rx: tokio::sync::mpsc::Receiver<BookFrame>,
    base_url: String,
    symbol: String,
    root: PathBuf,
) {
    let verify_csv = verify_csv_path(&root);
    if let Err(e) = ensure_verify_csv(&verify_csv) {
        eprintln!(
            "verify: {} не создался ({e}), сайдкар остановлен",
            verify_csv.display()
        );
        return;
    }
    let mut rest = match BybitPublicRest::new(base_url) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("verify: REST-клиент не создался ({e}), сайдкар остановлен");
            return;
        }
    };
    let mut ticker = tokio::time::interval(Duration::from_secs(VERIFY_INTERVAL_SECS));
    ticker.tick().await;
    loop {
        ticker.tick().await;
        if rx.is_closed() {
            break;
        }
        let Some(frame) = drain_latest_book(&mut rx) else {
            continue;
        };
        if frame.book.last_u().is_none() {
            continue;
        }
        // `BybitPublicRest::get`, вызванный изнутри рантайма, сам уходит на
        // эфемерный ОС-поток (шаг 0.7): вложенного рантайма здесь нет, паники
        // нет. Блокировка рабочего потока на время одного HTTP раз в 5 минут —
        // принятая цена, событийный путь она не касается вообще.
        let ts_utc = ts_utc_of_ns(SystemClock.now_ns());
        if let Err(e) = verify_one_tick(&mut rest, &frame, &symbol, &ts_utc, &verify_csv) {
            eprintln!("verify: строка не записалась ({e})");
        }
    }
}

// ---------------------------------------------------------------------------
// Тесты шага 0.8 (фейковый PublicRest, без сети и без рантайма)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    use crate::book::Update;
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

    /// Тело `/v5/market/orderbook` с одним бидом и одним аском в тех же числах,
    /// что кадр книги ниже: чистая сверка обязана дать ноль расхождений.
    fn snapshot_body(u: u64, bid_qty: &str) -> String {
        format!(
            r#"{{"retCode":0,"retMsg":"OK","result":{{"s":"{SYMBOL}","b":[["150.00","{bid_qty}"]],"a":[["150.01","3.0"]],"ts":1757800000000,"u":{u}}}}}"#
        )
    }

    fn frame_with_u(u: u64) -> BookFrame {
        let mut book = Book::new(TICK_E9, STEP_E9);
        book.apply(&Update {
            is_snapshot: true,
            u,
            cts_ms: 1_757_800_000_000,
            bids: vec![(150_000_000_000, 2_500_000_000)],
            asks: vec![(150_010_000_000, 3_000_000_000)],
        })
        .unwrap();
        BookFrame {
            book,
            tick_e9: TICK_E9,
            step_e9: STEP_E9,
        }
    }

    fn verify_in_tmp(
        rest: &mut FakeRest,
        frame: &BookFrame,
    ) -> (tempfile::TempDir, VerifyRow, Vec<VerifyRow>) {
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let row = verify_one_tick(rest, frame, SYMBOL, TS_UTC, &csv).unwrap();
        let rows = read_verify_rows(&csv).unwrap();
        (dir, row, rows)
    }

    /// Чистая сверка: книга ровно на `u` снапшота, топы совпали — `ok` и ноль.
    #[test]
    fn clean_check_writes_ok_with_zero_mismatches() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(7, "2.5"))]);
        let (_dir, row, rows) = verify_in_tmp(&mut rest, &frame_with_u(7));
        assert_eq!(row.verdict, VerifyVerdict::Ok);
        assert_eq!(row.mismatches, Some(0));
        assert_eq!(row.snapshot_u, Some(7));
        assert_eq!(row.book_u, Some(7));
        assert_eq!(rows, vec![row]);
    }

    /// Рассинхрон `u` — это `misaligned`, а не `mismatch`: смешивать эти два
    /// исхода запрещено тем же правилом, что `verify_at_u` возвращает
    /// `AlignmentError`, а не грязный дифф.
    #[test]
    fn u_desync_is_misaligned_not_mismatch() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(9, "2.5"))]);
        let (_dir, row, rows) = verify_in_tmp(&mut rest, &frame_with_u(7));
        assert_eq!(row.verdict, VerifyVerdict::Misaligned);
        assert_eq!(row.snapshot_u, Some(9));
        assert_eq!(row.book_u, Some(7));
        assert_eq!(row.mismatches, None, "несверенное не имеет счёта");
        assert_eq!(rows.len(), 1);
    }

    /// Недоступный REST — строка отказа обычным возвратом, поток не
    /// прерывается: второй тик на том же отказе тоже даёт строку, а не ошибку.
    #[test]
    fn rest_outage_writes_refusal_rows_without_stopping() {
        let mut rest = FakeRest::with_responses(vec![
            Err(RestError::Transport("connection reset".to_string())),
            Err(RestError::Transport("connection reset".to_string())),
        ]);
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        let frame = frame_with_u(7);
        for _ in 0..2 {
            let row = verify_one_tick(&mut rest, &frame, SYMBOL, TS_UTC, &csv).unwrap();
            assert_eq!(row.verdict, VerifyVerdict::RestUnavailable);
            assert_eq!(row.snapshot_u, None);
            assert_eq!(row.book_u, Some(7));
            assert_eq!(row.mismatches, None);
        }
        assert_eq!(read_verify_rows(&csv).unwrap().len(), 2);
    }

    /// Тот же `u`, но размер уровня perturbed — настоящий `mismatch` со счётом.
    /// Парный тест к рассинхрону: доказывает, что `mismatch` достижим и от
    /// `misaligned` отличим.
    #[test]
    fn perturbed_snapshot_is_mismatch_with_count() {
        let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(7, "999.0"))]);
        let (_dir, row, _rows) = verify_in_tmp(&mut rest, &frame_with_u(7));
        assert_eq!(row.verdict, VerifyVerdict::Mismatch);
        assert_eq!(row.mismatches, Some(1));
    }

    /// Шапка — контракт (те же имена и порядок, что поля `VerifyRow`), строка
    /// читается назад теми же значениями, включая пустые поля отказа.
    #[test]
    fn verify_header_is_stable_and_refusal_row_round_trips() {
        let dir = tempfile::tempdir().unwrap();
        let csv = verify_csv_path(dir.path());
        ensure_verify_csv(&csv).unwrap();
        ensure_verify_csv(&csv).unwrap();
        assert_eq!(
            std::fs::read_to_string(&csv).unwrap().trim_end(),
            "ts_utc,symbol,snapshot_u,book_u,mismatches,verdict"
        );
        assert!(read_verify_rows(&csv).unwrap().is_empty());
        let row = VerifyRow {
            ts_utc: TS_UTC.to_string(),
            symbol: SYMBOL.to_string(),
            snapshot_u: None,
            book_u: Some(7),
            mismatches: None,
            verdict: VerifyVerdict::RestUnavailable,
        };
        append_verify_row(&csv, &row).unwrap();
        assert_eq!(read_verify_rows(&csv).unwrap(), vec![row]);
    }

    /// Цикл записи не блокируется: `try_send` синхронен (без `.await`) —
    /// возврат из вызова и есть доказательство. Полный канал даёт `false` и
    /// +1 к счётчику, старый кадр при этом цел (дропит нового, не очередь).
    #[test]
    fn offer_never_waits_and_counts_skips_on_full_channel() {
        let (tx, mut rx) = tokio::sync::mpsc::channel::<BookFrame>(VERIFY_CHANNEL_CAPACITY);
        let mut skipped = 0u64;
        assert!(offer_book_snapshot(&tx, frame_with_u(7), &mut skipped));
        assert_eq!(skipped, 0);
        assert!(!offer_book_snapshot(&tx, frame_with_u(8), &mut skipped));
        assert_eq!(skipped, 1, "переполнение — считанный дроп");
        let kept = rx.try_recv().unwrap();
        assert_eq!(kept.book.last_u(), Some(7), "дропит нового, очередь цела");
        assert!(offer_book_snapshot(&tx, frame_with_u(9), &mut skipped));
        assert_eq!(skipped, 1, "место освободилось — счётчик стоит");
    }

    /// Дрен сайдкара берёт только самый свежий, пустой канал — `None`.
    #[test]
    fn sidecar_drain_takes_only_the_latest_and_empty_is_none() {
        let (tx, mut rx) = tokio::sync::mpsc::channel::<BookFrame>(8);
        assert!(drain_latest_book(&mut rx).is_none());
        tx.try_send(frame_with_u(7)).unwrap();
        tx.try_send(frame_with_u(8)).unwrap();
        assert_eq!(drain_latest_book(&mut rx).unwrap().book.last_u(), Some(8));
        assert!(drain_latest_book(&mut rx).is_none());
    }

    /// Сверка идёт ровно на топ-50: запрос шлёт лимит 50, а не потолок снапшота.
    /// Иначе уровни за топом дали бы ложные `mismatch` на каждой строке.
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
            &frame_with_u(7),
            SYMBOL,
            TS_UTC,
            &verify_csv_path(dir.path()),
        )
        .unwrap();
        assert_eq!(*seen.borrow(), Some(VERIFY_ORDERBOOK_LIMIT.to_string()));
    }
}
