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

fn sparse_snapshot_with_bid(seq: u64, u: u64, bid_qty_e9: i64) -> Update {
    Update {
        is_snapshot: true,
        u,
        seq,
        cts_ms: 1_757_800_000_000,
        bids: vec![(150_000_000_000, bid_qty_e9)],
        asks: vec![(150_010_000_000, 3_000_000_000)],
    }
}

fn sparse_delta_with_bid(seq: u64, u: u64, bid_qty_e9: i64) -> Update {
    Update {
        is_snapshot: false,
        u,
        seq,
        cts_ms: 1_757_800_000_001,
        bids: vec![(150_000_000_000, bid_qty_e9)],
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

/// Скобки: снапшот между двумя нашими `seq` совпадает с `after`, но не с
/// `before` — `Ok` через верхнюю границу, а не ложный `mismatch`.
/// `before` на 1_000_000 (2.5), `after` на 1_011_600 (3.5), снапшот на
/// 1_005_000 с 3.5. `book_seq` — `seq` состояния `before`.
#[test]
fn bracket_rescues_when_snapshot_matches_after_not_before() {
    let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(1_005_000, "3.5"))]);
    let dir = tempfile::tempdir().unwrap();
    let csv = verify_csv_path(dir.path());
    let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
    let mut state = VerifyState::new(TICK_E9, STEP_E9);
    state.apply_msg(VerifyMsg::Update(sparse_snapshot_with_bid(
        1_000_000,
        1000,
        2_500_000_000,
    )));
    state.apply_msg(VerifyMsg::Update(sparse_delta_with_bid(
        1_011_600,
        1001,
        3_500_000_000,
    )));
    let row = state
        .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
        .unwrap();
    assert_eq!(
        row.verdict,
        VerifyVerdict::Ok,
        "скобки обязаны спасти: {row:?}"
    );
    assert_eq!(row.snapshot_seq, Some(1_005_000));
    assert_eq!(row.book_seq, Some(1_000_000));
    assert_eq!(row.mismatches, Some(0));
}

/// Скобки: снапшот отличается от ОБОИХ состояний — настоящий `mismatch`,
/// а не спасение гонкой. Тот же расклад, снапшот с 999.0.
#[test]
fn bracket_true_mismatch_when_differs_from_both_states() {
    let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(1_005_000, "999.0"))]);
    let dir = tempfile::tempdir().unwrap();
    let csv = verify_csv_path(dir.path());
    let (_tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
    let mut state = VerifyState::new(TICK_E9, STEP_E9);
    state.apply_msg(VerifyMsg::Update(sparse_snapshot_with_bid(
        1_000_000,
        1000,
        2_500_000_000,
    )));
    state.apply_msg(VerifyMsg::Update(sparse_delta_with_bid(
        1_011_600,
        1001,
        3_500_000_000,
    )));
    let row = state
        .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
        .unwrap();
    assert_eq!(row.verdict, VerifyVerdict::Mismatch);
    assert_eq!(row.snapshot_seq, Some(1_005_000));
    assert_eq!(row.book_seq, Some(1_000_000));
    assert_eq!(row.mismatches, Some(1));
}

/// Скобки через догон: `after` приезжает входящим потоком уже во время тика.
/// Реплика на 7 (2.5), снапшот на 8 с 3.5; дельты 8 (пустая) и 9 (3.5)
/// приходят в догоне — `Ok` через дожданную верхнюю границу.
#[test]
fn bracket_waits_for_after_via_stream_to_ok() {
    let mut rest = FakeRest::with_responses(vec![Ok(snapshot_body(8, "3.5"))]);
    let dir = tempfile::tempdir().unwrap();
    let csv = verify_csv_path(dir.path());
    let (tx, rx) = std::sync::mpsc::sync_channel::<VerifyMsg>(16);
    let mut state = VerifyState::new(TICK_E9, STEP_E9);
    state.apply_msg(VerifyMsg::Update(snapshot_update(7)));
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(50));
        tx.send(VerifyMsg::Update(empty_delta(8))).unwrap();
        tx.send(VerifyMsg::Update(Update {
            is_snapshot: false,
            u: 1_000_000 + 9,
            seq: 9,
            cts_ms: 1_757_800_000_001,
            bids: vec![(150_000_000_000, 3_500_000_000)],
            asks: vec![],
        }))
        .unwrap();
    });
    let row = state
        .verify_tick(&mut rest, &rx, SYMBOL, TS_UTC, &csv)
        .unwrap();
    assert_eq!(
        row.verdict,
        VerifyVerdict::Ok,
        "дожданный after обязан спасти: {row:?}"
    );
    assert_eq!(row.snapshot_seq, Some(8));
    assert_eq!(row.book_seq, Some(8));
    assert_eq!(row.mismatches, Some(0));
}
