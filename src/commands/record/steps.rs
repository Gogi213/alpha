//! Шаги инструмента (`tickSize`/`qtyStep`): проверка, горячий детектор,
//! чтение из `instruments.csv` и часовой авторитет в ОС-потоке с решением по
//! подозрению. Отдельно от рекордера: это два детектора смены шагов из шапки
//! `record.rs`, а не файл суток — `Recorder` их зовёт, но не владеет ими.

use std::path::Path;
use std::time::Duration;

use crate::bybit::rest::{fetch_all_linear_instruments, BybitPublicRest, PublicRest};

use super::errors::{RecordError, StepViolation};
use super::gaps::GapKind;
use super::{Recorder, HOURLY_REFRESH_SECS};

pub(super) fn validate_steps(tick_e9: i64, step_e9: i64) -> Result<(), RecordError> {
    if tick_e9 <= 0 || step_e9 <= 0 {
        return Err(RecordError::BadSteps { tick_e9, step_e9 });
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Горячий детектор смены шагов: одна целочисленная операция на поле.
// ---------------------------------------------------------------------------

/// Проверяет, что цена кратна сохранённому тику, а размер — сохранённому шагу.
/// Вызывается на уже разобранных `i64` до изменения книги и файла — первое же
/// затронутое событие даёт `Err`, а не тихую запись по неверному масштабу.
/// Ноль аллокаций: только `%` и сравнение.
pub fn check_level_step(
    price_e9: i64,
    qty_e9: i64,
    tick_e9: i64,
    step_e9: i64,
) -> Result<(), StepViolation> {
    if price_e9 % tick_e9 != 0 || qty_e9 % step_e9 != 0 {
        return Err(StepViolation {
            price_e9,
            qty_e9,
            tick_e9,
            step_e9,
        });
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Шаги из instruments.csv (пишет `lob pick`, шаг 0.4).
// ---------------------------------------------------------------------------

/// Строка `instruments.csv` — только колонки, нужные записи. Остальные
/// (`min_order_qty`, `min_notional_value`, …) игнорируются разбором, но их
/// наличие в файле обязательно: файл читается по именам заголовков, и
/// перепутанные колонки дали бы чужие шаги молча — от этого страхует тест
/// `steps_come_from_the_right_columns`, где все четыре числа различны.
#[derive(serde::Deserialize)]
struct InstrumentStepsRow {
    symbol: String,
    tick_size: String,
    qty_step: String,
}

/// `(tick_e9, step_e9)` символа из `instruments.csv`. Тот же масштаб 1e-9 и
/// тот же разбор `parse_e9`, что книга и живой поток (A1) — три независимых
/// масштаба здесь разошлись бы при переносе константы.
pub fn load_steps_for_symbol(
    instruments_csv: &Path,
    symbol: &str,
) -> Result<(i64, i64), RecordError> {
    // Единственный читатель `instruments.csv` (дозапрос по ревью таска 08,
    // ось Craft) — терпит метку `debug` первой строкой, голый
    // `csv::Reader::from_path` читал бы её как заголовок.
    let mut r = crate::commands::lob::pick::instruments_csv_reader(instruments_csv)?;
    for row in r.deserialize::<InstrumentStepsRow>() {
        let row: InstrumentStepsRow = row?;
        if row.symbol != symbol {
            continue;
        }
        let tick_e9 = crate::bybit::ws::parse_e9(row.tick_size.trim()).ok_or_else(|| {
            RecordError::Steps(format!("{symbol}: tick_size не разобрался как число"))
        })?;
        let step_e9 = crate::bybit::ws::parse_e9(row.qty_step.trim()).ok_or_else(|| {
            RecordError::Steps(format!("{symbol}: qty_step не разобрался как число"))
        })?;
        validate_steps(tick_e9, step_e9)?;
        return Ok((tick_e9, step_e9));
    }
    Err(RecordError::Steps(format!(
        "{symbol}: нет в {} — сначала `lob pick`",
        instruments_csv.display()
    )))
}

/// Авторитетные шаги из `instruments-info` (холодный детектор). Ошибка сети
/// или отсутствие символа — это `Steps`, а не паника: часовой авторитет
/// переживает её и пробует снова через час.
fn refresh_steps<R: PublicRest>(rest: &mut R, symbol: &str) -> Result<(i64, i64), RecordError> {
    let instruments = fetch_all_linear_instruments(rest)
        .map_err(|e| RecordError::Steps(format!("instruments-info: {e}")))?;
    let inst = instruments
        .iter()
        .find(|i| i.symbol == symbol)
        .ok_or_else(|| RecordError::Steps(format!("{symbol}: нет в instruments-info")))?;
    validate_steps(inst.tick_e9, inst.qty_step_e9)?;
    Ok((inst.tick_e9, inst.qty_step_e9))
}

/// Горячий путь будит авторитет, не дожидаясь HTTP (ремонт 0.7 Р1).
/// `try_send` на канале ёмкостью 1: полный канал — это уже pending
/// пробуждение, второе не нужно; закрытый — авторитет умер, ждать некого.
/// Вызов не блокируется никогда — это и держит «ноль HTTP в событийном пути».
pub(super) fn request_steps_refresh(wake_tx: &std::sync::mpsc::SyncSender<()>) {
    let _ = wake_tx.try_send(());
}

/// Один цикл авторитета: fetch → `mpsc`, затем ожидание до часа или
/// пробуждения. Общая часть боевого и тестового спавна (ремонт 0.7 Р2) —
/// источник шагов за трейтом, а не жёсткий `BybitPublicRest` в теле цикла.
fn run_steps_authority_loop<R: PublicRest>(
    rest: &mut R,
    symbol: &str,
    steps_tx: std::sync::mpsc::Sender<(i64, i64)>,
    wake_rx: std::sync::mpsc::Receiver<()>,
) {
    loop {
        match refresh_steps(rest, symbol) {
            Ok(steps) => {
                if steps_tx.send(steps).is_err() {
                    break;
                }
            }
            Err(e) => {
                eprintln!("record: instruments-info недоступен ({e}), повтор через час");
            }
        }
        match wake_rx.recv_timeout(Duration::from_secs(HOURLY_REFRESH_SECS)) {
            Ok(()) => while wake_rx.try_recv().is_ok() {},
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
}

/// Часовой авторитет шагов в ОС-потоке (шаг 0.7, Decision 24).
/// Отдельный `std::thread` (НЕ tokio-задача) со своим `BybitPublicRest`:
/// цикл fetch → `mpsc` → `recv_timeout` 1h/пробуждение. Внутри ОС-потока
/// `block_on` легален — чужого рантайма там нет, поэтому вложенный рантайм
/// невозможен. Цикл записи никогда не ждёт HTTP: он только толкает
/// `try_send` в канал-будильник и дренирует готовое из канала шагов.
pub(super) fn spawn_steps_authority(
    base_url: String,
    symbol: String,
    wake_rx: std::sync::mpsc::Receiver<()>,
) -> std::sync::mpsc::Receiver<(i64, i64)> {
    let (tx, rx) = std::sync::mpsc::channel::<(i64, i64)>();
    let spawned = std::thread::Builder::new()
        .name("steps-authority".to_string())
        .spawn(move || {
            let mut rest = match BybitPublicRest::new(base_url) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("record: авторитет шагов не создался ({e})");
                    return;
                }
            };
            run_steps_authority_loop(&mut rest, &symbol, tx, wake_rx);
        });
    if let Err(e) = spawned {
        eprintln!("record: авторитет шагов не запустился ({e})");
    }
    rx
}

/// Тестовый спавн того же цикла с фейковым источником шагов (ремонт 0.7 Р2).
/// Боевой код его не зовёт — только тесты стыка «подозрение → авторитет →
/// ротация» без сети.
#[cfg(test)]
pub(super) fn spawn_steps_authority_with_rest<R: PublicRest + Send + 'static>(
    rest: R,
    symbol: String,
    wake_rx: std::sync::mpsc::Receiver<()>,
) -> std::sync::mpsc::Receiver<(i64, i64)> {
    let (tx, rx) = std::sync::mpsc::channel::<(i64, i64)>();
    let spawned = std::thread::Builder::new()
        .name("steps-authority-test".to_string())
        .spawn(move || {
            let mut rest = rest;
            run_steps_authority_loop(&mut rest, &symbol, tx, wake_rx);
        });
    if let Err(e) = spawned {
        eprintln!("record: тестовый авторитет не запустился ({e})");
    }
    rx
}

/// Решение по горячему подозрению на последнем известном авторитете
/// (ремонт 0.7 Р2): свежие шаги отличаются — ротация файла со строкой
/// `step_change`, совпадают или свежести нет — строка подавления
/// `book_invariant` (первая на часть файла, дальше счётчик у вызывающего).
/// Возвращает `Some` новых шагов при ротации, `None` при подавлении.
/// Тот же код зовёт и цикл записи, и тесты стыка — шов один, а не два.
pub(super) fn resolve_suspicion(
    rec: &mut Recorder,
    latest: Option<(i64, i64)>,
    ts_utc: &str,
    detail: &str,
    logged: &mut bool,
    suppressed: &mut u64,
) -> Result<Option<(i64, i64)>, RecordError> {
    if let Some((new_tick, new_step)) = latest {
        if new_tick != rec.tick_e9() || new_step != rec.step_e9() {
            rec.rotate_on_step_change(new_tick, new_step, ts_utc, detail)?;
            return Ok(Some((new_tick, new_step)));
        }
    }
    if !*logged {
        rec.log_gap(GapKind::BookInvariant, ts_utc, detail)?;
        *logged = true;
    }
    *suppressed = suppressed.saturating_add(1);
    Ok(None)
}

/// Дрен канала авторитета: забирает всё, возвращает только последнее.
/// `None` — свежести нет (авторитет ещё не ответил или недоступен):
/// вызывающий идёт в ветку подавления, а не ждёт сеть.
pub(super) fn drain_latest_steps(
    rx: &mut std::sync::mpsc::Receiver<(i64, i64)>,
) -> Option<(i64, i64)> {
    let mut latest = None;
    while let Ok(v) = rx.try_recv() {
        latest = Some(v);
    }
    latest
}
