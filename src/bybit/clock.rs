//! Дисциплина часов хоста: смещение против NTP и против `serverTime` Bybit,
//! запись в `clock.csv` раз в час на всём протяжении любой записи (шаг 0.5).
//!
//! # Почему этот шаг стоит до недели, а не после неё
//!
//! `PLAN.md`: «Стоит здесь, а не после недели: `exch_ts < local_ts` — вход
//! шага 6.1, и в ревизии 2 сбитые часы обнаружились бы, когда неделя уже
//! потрачена и невосстановима.» Часы хоста — вход всей цепочки измерения:
//! `local_ts` (`bybit::conn`, `[ASSUMPTION H12]`) сравнивается с `exch_ts`
//! биржи в шаге 6.1 и обязан быть строго больше него на каждом событии
//! (`0.1`, «строгое неравенство, а не `≥`» — то же требование, что и здесь).
//! Если часы хоста разъехались с биржей, это неравенство рушится молча:
//! события не теряются, не искажаются видимым образом, книга остаётся
//! корректной — просто временная ось, на которой стоит всё остальное
//! измерение, сдвинута. Обнаружить это после недели записи значит потерять
//! неделю; обнаружить до — значит не начинать её вовсе (`Failure and
//! rollback`: «Смещение часов вне порога: запись не стартует»).
//!
//! # Что здесь измеряется и против чего
//!
//! Два независимых эталона, каждый через один и тот же протокол «раунд
//! запрос-ответ»: внешний пул NTP (`UdpNtpSource`, RFC 4330/5905 поверх
//! `UdpSocket`) и `serverTime` Bybit (`BybitServerTimeSource`, `/v5/market/time`,
//! публичный эндпоинт — Decision 12 про ключи из окружения сюда не относится,
//! подписывать нечего). Независимость эталонов — не избыточность: если
//! разъехались локальные часы, оба источника согласованно покажут одно и то
//! же смещение; если разъехался (или заблокирован) один конкретный эталон,
//! второй это отличит. `clock.csv` пишет обе колонки, и обе проверяются
//! отдельно (`check_rows`) — совпадение было бы совпадением, а не гарантией.
//!
//! # Оценка смещения: почему не наивная разница
//!
//! Один раунд даёт три метки: `local_send_ns` (локальные часы перед
//! отправкой), `remote_ns` (то, что сообщил эталон), `local_recv_ns`
//! (локальные часы сразу по получении ответа). Наивная оценка
//! `remote_ns - local_recv_ns` предполагает, что весь путь пакета — что туда,
//! что обратно — занял ноль времени до момента, когда эталон посмотрел на
//! свои часы, и в реальности смещена на сетевую задержку целиком в
//! пессимистичном случае; `remote_ns - local_send_ns` смещена симметрично в
//! другую сторону. При допущении, что задержка «туда» и «обратно»
//! одинакова (стандартное допущение NTP и алгоритма Кристиана, Cristian,
//! 1989), единственная точка локальных часов, которая наблюдает и отправку,
//! и получение поровну, — это середина интервала между ними. Отсюда
//! `estimate_offset`: `offset = remote_ns - (local_send_ns + local_recv_ns) / 2`.
//! Ошибка такой оценки при несимметричной задержке ограничена половиной RTT
//! в любую сторону — тем же порядком, что и порог гейта `MAX_ABS_OFFSET_NS`
//! (5 мс), то есть наивная оценка сама по себе способна съесть весь бюджет
//! порога независимо от реального рассинхрона часов. Полная NTP-формула с
//! четырьмя метками (используется, когда сервер возвращает время своего
//! приёма запроса отдельно от времени своей отправки ответа) здесь не
//! нужна: ни `serverTime` Bybit, ни используемый минимальный SNTP-запрос не
//! дают такого расщепления, у обоих одна метка на раунд, и трёхточечная
//! оценка Кристиана — это ровно то, что из одной метки можно вытащить.

use crate::bybit::conn::Clock;
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::time::Duration;

/// Общий HTTP-таймаут шага 0.7 (дефект В-5): 10 секунд — два порядка ниже
/// часовой каденции. Процесс без присмотра не вправе висеть на сокете дольше.
const HTTP_TIMEOUT: Duration = Duration::from_secs(10);

/// Порог `|offset|` — done-condition шага 0.5 и строка «Смещение часов» в
/// таблице гейта GC (`PLAN.md`). Строгое `<`: план формулирует порог как
/// «меньше», и замер ровно на границе (`offset_ns.abs() == 5_000_000`) —
/// это уже не «часы дисциплинированы», а «ровно на грани», и трактовать
/// такую точку как проходную значило бы разрешить то, что план не разрешил.
pub const MAX_ABS_OFFSET_NS: i64 = 5_000_000;

/// Порог скачка между двумя соседними успешными замерами одного источника —
/// та же строка `PLAN.md`. Строгое `>` для нарушения: план пишет «без
/// скачков > 1 мс», то есть скачок ровно в 1 мс ещё разрешён.
pub const MAX_JUMP_NS: i64 = 1_000_000;

/// Разница эпох NTP (1900-01-01) и Unix (1970-01-01): ровно 70 лет, из
/// которых 17 високосных — каждый четвёртый год этого промежутка
/// (1904, 1908, …, 1968), а сам 1900-й невисокосный по григорианскому
/// правилу «делится на 100, но не на 400». `(70·365 + 17)` дней · 86400 с —
/// протокольная константа формата метки времени NTP (RFC 5905 §6), не наш
/// выбор и не число, которое можно было бы просто объявить в `PLAN.md`: она
/// зависит только от календаря, а не от гипотезы, которую меряет этот репозиторий.
const NTP_UNIX_EPOCH_DELTA_SECS: i64 = (70 * 365 + 17) * 86_400;

/// Три метки одного раунда запрос-ответ к эталону времени. Сырые, не готовое
/// смещение: оценка (`estimate_offset`) — общая для NTP и `serverTime`
/// формула, и её место — одна чистая функция, а не код, продублированный в
/// каждой реализации `ReferenceClock`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RoundTrip {
    /// Локальные часы непосредственно перед отправкой запроса, наносекунды
    /// от эпохи Unix — тот же формат, что `local_ts` шага 0.1
    /// (`bybit::conn::Clock`), чтобы строки `clock.csv` были сравнимы с
    /// логом без пересчёта единиц.
    pub local_send_ns: i64,
    /// Время, которое сообщил эталон (сервер NTP или Bybit).
    pub remote_ns: i64,
    /// Локальные часы сразу по получении ответа.
    pub local_recv_ns: i64,
}

/// Один эталон времени: NTP-пул или `serverTime` Bybit. Каждый вызов —
/// неделимый раунд «запрос → ответ», и трейт не знает про протокол внутри
/// раунда вовсе — только про то, что раунд либо дал `RoundTrip`, либо не
/// дал ничего. Трейт, а не прямой вызов сети (design constraint задачи):
/// единственный способ проверить скачок между замерами без часа ожидания —
/// подставить вместо настоящего источника сценарную последовательность
/// готовых `RoundTrip`.
pub trait ReferenceClock {
    fn round_trip(&mut self) -> Result<RoundTrip, ClockError>;
}

/// Смещение и RTT одного раунда — результат `estimate_offset`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OffsetSample {
    pub offset_ns: i64,
    pub rtt_ns: i64,
}

/// Оценка смещения по одному раунду — алгоритм Кристиана (см. doc модуля):
/// `offset = remote_ns - (local_send_ns + local_recv_ns) / 2`.
///
/// Арифметика идёт в `i128`, а не в `i64` напрямую: `remote_ns` эталона и
/// локальная середина интервала могут в вырожденном или намеренно
/// сконструированном входе лежать на разных концах диапазона `i64`
/// (тест `estimate_offset_reports_overflow_instead_of_wrapping_on_i64_extremes`),
/// и обычное вычитание `i64 - i64` в этом случае либо паникует (сборка с
/// проверкой переполнения), либо тихо заворачивается в противоположный
/// знак (сборка без неё) — второе воспроизводит ровно тот дефект, ради
/// которого существует этот модуль: уверенный, но неверный ответ вместо
/// отказа. `i128` вмещает разность любых двух `i64` без переполнения, и
/// результат явно проверяется на обратный путь в `i64` через `try_from`.
pub fn estimate_offset(round_trip: &RoundTrip) -> Result<OffsetSample, ClockError> {
    if round_trip.local_recv_ns < round_trip.local_send_ns {
        // Получение раньше отправки по локальным часам — сами часы сделали
        // шаг назад в промежутке между двумя чтениями одного раунда. Это
        // ровно то рассогласование, которое весь модуль существует, чтобы
        // ловить (`H12`), и опираться на середину интервала, посчитанную
        // на противоречивой паре меток, значит подсунуть гейту число,
        // посчитанное на уже сломанной предпосылке.
        return Err(ClockError::NonMonotonicRoundTrip {
            local_send_ns: round_trip.local_send_ns,
            local_recv_ns: round_trip.local_recv_ns,
        });
    }
    let send = i128::from(round_trip.local_send_ns);
    let recv = i128::from(round_trip.local_recv_ns);
    let remote = i128::from(round_trip.remote_ns);
    let rtt = recv - send;
    let midpoint = send + rtt / 2;
    let offset = remote - midpoint;
    Ok(OffsetSample {
        offset_ns: i64::try_from(offset).map_err(|_| ClockError::Overflow)?,
        rtt_ns: i64::try_from(rtt).map_err(|_| ClockError::Overflow)?,
    })
}

/// Ошибки этого модуля. Ни один вариант не может нести секрет: оба эталона
/// — публичные эндпоинты (NTP всегда, `serverTime` Bybit — по документации
/// v5, `/v5/market/time` не в разделе Authentication), Decision 12 про ключи
/// из окружения сюда просто не применяется — подписывать нечего.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClockError {
    /// Сеть: таймаут, разрыв, DNS. Текст без секретов — их тут и нет.
    Transport(String),
    /// Ответ пришёл, но не разобрался как ожидаемый протокол эталона.
    Decode(String),
    /// Файл `clock.csv`: не открылся, не записался, не прочитался.
    Io(String),
    /// См. `estimate_offset`.
    NonMonotonicRoundTrip {
        local_send_ns: i64,
        local_recv_ns: i64,
    },
    /// См. `estimate_offset`.
    Overflow,
}

/// Одна строка `clock.csv`. Обе колонки на источник — `Option`, не `i64` с
/// зашитым нулём при неудаче: провал раунда NTP или Bybit — это отсутствие
/// данных, а не измеренное нулевое смещение, и превращение одного в другое
/// было бы ровно тем самым «уверенным, но неверным ответом», которого
/// избегает `estimate_offset`. `*_error` несёт причину для той же строки —
/// без него провал виден только как дыра в числах, а какая именно причина
/// (таймаут, разрыв, кривой ответ) осталась бы только в логе процесса,
/// который к моменту разбора `clock.csv` может уже не существовать.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClockRow {
    /// Порядковый номер замера с начала записи, с нуля. Нужен отдельно от
    /// `local_ts_ns`: «скачок между замерами» (`MAX_JUMP_NS`) — это
    /// свойство пары соседних *успешных* замеров одного источника, и без
    /// сквозного номера строки, идентифицирующего каждую, `Violation::Jump`
    /// нечем было бы указать на конкретную пару в файле.
    pub sample_index: u64,
    /// Локальные часы в момент замера, наносекунды от эпохи Unix.
    pub local_ts_ns: i64,
    pub ntp_offset_ns: Option<i64>,
    pub ntp_rtt_ns: Option<i64>,
    pub ntp_error: Option<String>,
    pub bybit_offset_ns: Option<i64>,
    pub bybit_rtt_ns: Option<i64>,
    pub bybit_error: Option<String>,
}

/// Один часовой замер: раунд у обоих эталонов, оценка смещения по каждому,
/// что успел ответить. Провал одного источника не роняет другой и не
/// прерывает замер целиком — NTP-пул и Bybit это два независимых сетевых
/// похода, и временная недоступность одного не обязана стоить записи о
/// втором: строка `clock.csv` в этом случае просто несёт `None` и текст
/// причины в колонках упавшего источника (см. doc `ClockRow`).
pub fn sample<N: ReferenceClock, B: ReferenceClock, C: Clock>(
    sample_index: u64,
    local_clock: &C,
    ntp: &mut N,
    bybit: &mut B,
) -> ClockRow {
    let local_ts_ns = local_clock.now_ns();
    let (ntp_offset_ns, ntp_rtt_ns, ntp_error) = split_reading(read_source(ntp));
    let (bybit_offset_ns, bybit_rtt_ns, bybit_error) = split_reading(read_source(bybit));
    ClockRow {
        sample_index,
        local_ts_ns,
        ntp_offset_ns,
        ntp_rtt_ns,
        ntp_error,
        bybit_offset_ns,
        bybit_rtt_ns,
        bybit_error,
    }
}

fn read_source<S: ReferenceClock>(source: &mut S) -> Result<OffsetSample, ClockError> {
    estimate_offset(&source.round_trip()?)
}

fn split_reading(
    result: Result<OffsetSample, ClockError>,
) -> (Option<i64>, Option<i64>, Option<String>) {
    match result {
        Ok(s) => (Some(s.offset_ns), Some(s.rtt_ns), None),
        Err(e) => (None, None, Some(format!("{e:?}"))),
    }
}

/// Одна точка серии одного источника, для проверки на скачки и порог.
/// `offset_ns = None` — источник не ответил на этом замере: точка выпадает
/// из проверки скачка (см. `check_series`), а не подставляется как ноль.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SeriesPoint {
    pub sample_index: u64,
    pub offset_ns: Option<i64>,
}

/// Нарушение done-condition шага 0.5, найденное в серии замеров одного
/// источника. Несёт `source` и номера строк, чтобы нарушившая строка была
/// узнаваема в выводе без обратного похода в файл — требование из тестов
/// на скачок и на дрейф: «строка, вызвавшая нарушение, идентифицируема».
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Violation {
    OffsetOutOfBounds {
        source: &'static str,
        sample_index: u64,
        offset_ns: i64,
    },
    Jump {
        source: &'static str,
        from_index: u64,
        to_index: u64,
        delta_ns: i64,
    },
}

/// Проверяет одну серию точек одного источника на оба свойства
/// done-condition шага 0.5: `|offset| < MAX_ABS_OFFSET_NS` на каждой точке,
/// и скачок между соседними *успешными* точками серии не больше
/// `MAX_JUMP_NS`. Пропуски (`offset_ns = None`, провал раунда источника)
/// не считаются ни соблюдением, ни нарушением порога и не создают "скачок
/// к нулю и обратно" вокруг себя — они просто исключают точку из проверки:
/// следующая успешная точка сравнивается с предыдущей успешной, а не с
/// провалом, как будто он был нулевым смещением.
///
/// `delta_ns` в `Violation::Jump` считается через `i128` и насыщается
/// `i64::MAX` при передаче наружу на намеренно вырожденном входе
/// (`sample_index`/`offset_ns` на границах `i64`, тест
/// `check_series_flags_a_jump_between_i64_extremes_without_panicking`) —
/// решение "нарушение или нет" уже принято до этого приведения по величине
/// в `i128`, так что усечение может только занизить показанную величину
/// скачка, но не может скрыть сам факт нарушения.
pub fn check_series(source: &'static str, points: &[SeriesPoint]) -> Vec<Violation> {
    check_points(source, points.iter().copied())
}

/// Общее тело `check_series`: генерично по источнику точек, не только по
/// срезу. `check_series` зовёт его на `points.iter().copied()`; `check_rows`
/// зовёт его напрямую на двух `map`-итераторах по строкам, без
/// промежуточного `Vec<SeriesPoint>` — обход здесь в любом случае
/// последовательный, с одним `prev`, произвольный доступ по индексу не
/// нужен, и собирать точки в вектор ради этого было бы аллокацией без
/// причины на пути, который целиком существует ради подсчёта аллокаций
/// (гейт GC).
fn check_points(source: &'static str, points: impl Iterator<Item = SeriesPoint>) -> Vec<Violation> {
    let mut violations = Vec::new();
    let mut prev: Option<(u64, i64)> = None;
    for point in points {
        let Some(offset_ns) = point.offset_ns else {
            continue;
        };
        // Не `offset_ns.abs()`: `i64::MIN.abs()` паникует переполнением —
        // `-i64::MIN` не представимо в `i64` (диапазон несимметричен на
        // единицу). Тест `check_series_flags_a_jump_between_i64_extremes...`
        // подаёт именно `i64::MIN` намеренно; `i128` вмещает модуль любого
        // `i64` без исключения.
        if i128::from(offset_ns).unsigned_abs() >= MAX_ABS_OFFSET_NS as u128 {
            violations.push(Violation::OffsetOutOfBounds {
                source,
                sample_index: point.sample_index,
                offset_ns,
            });
        }
        if let Some((prev_index, prev_offset)) = prev {
            let delta = (i128::from(offset_ns) - i128::from(prev_offset)).unsigned_abs();
            if delta > MAX_JUMP_NS as u128 {
                violations.push(Violation::Jump {
                    source,
                    from_index: prev_index,
                    to_index: point.sample_index,
                    delta_ns: i64::try_from(delta).unwrap_or(i64::MAX),
                });
            }
        }
        prev = Some((point.sample_index, offset_ns));
    }
    violations
}

/// `check_series` для обеих колонок `clock.csv` разом — точка входа,
/// которую вызывает всё, что проверяет done-condition шага 0.5 целиком
/// (`lob clock`, `lob verify`), а не по одному источнику вручную.
pub fn check_rows(rows: &[ClockRow]) -> Vec<Violation> {
    let mut violations = check_points(
        "ntp",
        rows.iter().map(|r| SeriesPoint {
            sample_index: r.sample_index,
            offset_ns: r.ntp_offset_ns,
        }),
    );
    violations.extend(check_points(
        "bybit",
        rows.iter().map(|r| SeriesPoint {
            sample_index: r.sample_index,
            offset_ns: r.bybit_offset_ns,
        }),
    ));
    violations
}

/// Дописывает одну строку в `clock.csv`. Заголовок пишется один раз — когда
/// файл ещё не существует или пуст, а не при каждом вызове: `Data and state`
/// (`PLAN.md`) описывает CSV-файлы вроде этого как то, что «читает человек»
/// весь срок записи, и повторяющийся заголовок посреди файла сделал бы его
/// нечитаемым без ручной чистки. Открывается на дозапись (`append(true)`),
/// а не перезаписывается целиком — та же причина, по которой суточный
/// бинлог (Decision 7) не перезаписывается на каждом кадре: часовой замер
/// не имеет права терять уже записанные часы при следующей записи.
pub fn append_row(path: &Path, row: &ClockRow) -> Result<(), ClockError> {
    let needs_header = std::fs::metadata(path)
        .map(|m| m.len() == 0)
        .unwrap_or(true);
    let file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| ClockError::Io(e.to_string()))?;
    let mut writer = csv::WriterBuilder::new()
        .has_headers(needs_header)
        .from_writer(file);
    writer
        .serialize(row)
        .map_err(|e| ClockError::Io(e.to_string()))?;
    writer.flush().map_err(|e| ClockError::Io(e.to_string()))?;
    Ok(())
}

/// Читает `clock.csv` целиком — используется тестами (round-trip) и
/// вызывающим кодом, которому нужна вся серия для `check_rows` (`lob verify`
/// сверяет уже записанный файл, а не держит замеры в памяти процесса записи).
pub fn read_rows(path: &Path) -> Result<Vec<ClockRow>, ClockError> {
    let mut reader = csv::Reader::from_path(path).map_err(|e| ClockError::Io(e.to_string()))?;
    reader
        .deserialize::<ClockRow>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| ClockError::Io(e.to_string()))
}

/// Источник тактов часового цикла: «пора снять ещё один замер» или «цикл
/// окончен». Трейт, а не `tokio::time::interval` впрямую, по той же причине,
/// что и `ReferenceClock`, — часовой каденс не проверить без часа ожидания,
/// если код напрямую спит на реальном таймере; сценарный тикер даёт то же
/// свойство (одна строка на такт) за миллисекунды. Решение "когда цикл
/// заканчивается" этот модуль не принимает — Decision 21 отдаёт его
/// `lob watch`, а `Ticker` здесь лишь исполняет уже принятое снаружи решение.
pub trait Ticker {
    fn next_tick(&mut self) -> bool;
}

/// Часовой цикл целиком: пока тикер не скажет "хватит", снять замер у обоих
/// эталонов и дописать строку в `path`. Останавливается на первой ошибке
/// записи файла (`ClockError::Io`) — это не то же самое, что провал одного
/// из эталонов (тот уже обработан внутри `sample` как `None` в своей
/// колонке): если писать в `clock.csv` не получается, продолжать цикл
/// вслепую значило бы потерять и уже снятые, и будущие замеры одинаково
/// молча, а гейт GC требует, чтобы дисциплина часов была наблюдаема на всём
/// протяжении записи, а не «наблюдалась, пока диск не подвёл».
pub fn run<N: ReferenceClock, B: ReferenceClock, C: Clock, T: Ticker>(
    path: &Path,
    local_clock: &C,
    ntp: &mut N,
    bybit: &mut B,
    ticker: &mut T,
) -> Result<Vec<ClockRow>, ClockError> {
    let mut rows = Vec::new();
    let mut sample_index = 0u64;
    while ticker.next_tick() {
        let row = sample(sample_index, local_clock, ntp, bybit);
        append_row(path, &row)?;
        rows.push(row);
        sample_index += 1;
    }
    Ok(rows)
}

/// Разбирает ответ NTP-сервера (48 байт, RFC 5905 §6) в `RoundTrip`.
/// Отдельная чистая функция от `UdpNtpSource::round_trip` — по той же
/// причине, по которой `ws.rs` отделён от `conn.rs`: разбор проверяется
/// строками байт, без сокета и без сети.
fn parse_ntp_response(
    response: &[u8],
    local_send_ns: i64,
    local_recv_ns: i64,
) -> Result<RoundTrip, ClockError> {
    if response.len() < 48 {
        return Err(ClockError::Decode(format!(
            "короткий ответ NTP: {} байт вместо 48",
            response.len()
        )));
    }
    // LI — два старших бита первого байта. `11` = "не синхронизирован":
    // сервер сам помечает свой ответ как не заслуживающий доверия
    // (RFC 5905 §7.3) — использовать такую метку для дисциплины часов
    // значит опираться на то, что источник прямо назвал ненадёжным.
    let leap_indicator = response
        .first()
        .map(|b| b >> 6)
        .ok_or_else(|| ClockError::Decode("короткий ответ NTP: нет первого байта".to_string()))?;
    if leap_indicator == 0b11 {
        return Err(ClockError::Decode(
            "NTP: leap indicator = 3, сервер не синхронизирован".to_string(),
        ));
    }
    // stratum = 0 — "kiss-o'-death": сервер отказал в ответе (перегрузка,
    // ограничение частоты и т.п., RFC 5905 §7.4) вместо реального времени;
    // тело такого пакета формально валидно, но метка в нём не время.
    let stratum = response
        .get(1)
        .copied()
        .ok_or_else(|| ClockError::Decode("короткий ответ NTP: нет stratum".to_string()))?;
    if stratum == 0 {
        return Err(ClockError::Decode(
            "NTP: stratum = 0 (kiss-o'-death), сервер отказал в ответе".to_string(),
        ));
    }
    // Transmit Timestamp — последнее 64-битное поле пакета: 32 бита секунд
    // с 1900-01-01, 32 бита дробной части секунды как Q32 (RFC 5905 §6).
    let secs = u32::from_be_bytes(
        response
            .get(40..44)
            .ok_or_else(|| ClockError::Decode("короткий ответ NTP: нет поля секунд".to_string()))?
            .try_into()
            .map_err(|_| ClockError::Decode("NTP: поле секунд не легло в u32".to_string()))?,
    );
    let frac = u32::from_be_bytes(
        response
            .get(44..48)
            .ok_or_else(|| ClockError::Decode("короткий ответ NTP: нет дробной части".to_string()))?
            .try_into()
            .map_err(|_| ClockError::Decode("NTP: дробная часть не легла в u32".to_string()))?,
    );
    let unix_secs = i64::from(secs) - NTP_UNIX_EPOCH_DELTA_SECS;
    // frac / 2^32 секунд → наносекунды. `u64` держит `u32::MAX * 1e9`
    // (~4.3e18) без переполнения (`u64::MAX` ~1.8e19) до сдвига.
    let frac_ns = (u64::from(frac) * 1_000_000_000) >> 32;
    let remote_ns = unix_secs
        .checked_mul(1_000_000_000)
        .and_then(|ns| ns.checked_add(frac_ns as i64))
        .ok_or(ClockError::Overflow)?;
    Ok(RoundTrip {
        local_send_ns,
        remote_ns,
        local_recv_ns,
    })
}

/// NTP-эталон поверх `UdpSocket` стандартной библиотеки. Не нужен отдельный
/// крейт: минимальный SNTP-клиент (RFC 4330) — один пакет туда, один
/// обратно, разбор — 48 байт по фиксированным смещениям (`parse_ntp_response`).
pub struct UdpNtpSource {
    socket: std::net::UdpSocket,
}

impl UdpNtpSource {
    /// `server_addr` и `timeout` — параметры, не константы: какой NTP-пул
    /// опрашивать и сколько ждать ответа — решение эксплуатации хоста
    /// (`[ASSUMPTION H12]`: «часы дисциплинируются NTP», но не называет
    /// конкретный пул или окно ожидания), не то, что этот файл вправе зашить.
    pub fn connect(
        server_addr: impl std::net::ToSocketAddrs,
        timeout: std::time::Duration,
    ) -> Result<Self, ClockError> {
        let socket = std::net::UdpSocket::bind("0.0.0.0:0")
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        socket
            .connect(server_addr)
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        socket
            .set_read_timeout(Some(timeout))
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        socket
            .set_write_timeout(Some(timeout))
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        Ok(Self { socket })
    }
}

impl ReferenceClock for UdpNtpSource {
    fn round_trip(&mut self) -> Result<RoundTrip, ClockError> {
        // LI=00, VN=011 (версия 3), Mode=011 (клиент) — минимальный запрос
        // SNTP (RFC 4330 §4): единственный байт, который обязан прочитать
        // сервер, остальные 47 нулевые.
        let mut request = [0u8; 48];
        request[0] = 0b00_011_011;
        let local_send_ns = crate::bybit::conn::SystemClock.now_ns();
        self.socket
            .send(&request)
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        let mut response = [0u8; 48];
        let n = self
            .socket
            .recv(&mut response)
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        let local_recv_ns = crate::bybit::conn::SystemClock.now_ns();
        let body = response
            .get(..n)
            .ok_or_else(|| ClockError::Transport("NTP: длина ответа вне буфера".to_string()))?;
        parse_ntp_response(body, local_send_ns, local_recv_ns)
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ServerTimeResult {
    time_nano: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ServerTimeEnvelope {
    ret_code: i32,
    ret_msg: String,
    result: ServerTimeResult,
}

/// Разбирает тело ответа `/v5/market/time` в `RoundTrip`. Форма ответа —
/// `{"retCode":0,"retMsg":"OK","result":{"timeSecond":"...","timeNano":"..."}}`
/// — сверена живым запросом к `api.bybit.com/v5/market/time`, не только по
/// документации. `timeNano`, не `timeSecond`: наносекундная метка того же
/// порядка точности, что и `local_ts_ns` этого модуля, секундная огрубила бы
/// оценку смещения на порядки относительно порога в 5 мс.
fn parse_bybit_server_time(
    body: &str,
    local_send_ns: i64,
    local_recv_ns: i64,
) -> Result<RoundTrip, ClockError> {
    let envelope: ServerTimeEnvelope =
        serde_json::from_str(body).map_err(|e| ClockError::Decode(e.to_string()))?;
    if envelope.ret_code != 0 {
        return Err(ClockError::Decode(format!(
            "serverTime retCode={} retMsg={}",
            envelope.ret_code, envelope.ret_msg
        )));
    }
    let remote_ns: i64 = envelope.result.time_nano.parse().map_err(|_| {
        ClockError::Decode(format!(
            "timeNano не число: {:?}",
            envelope.result.time_nano
        ))
    })?;
    Ok(RoundTrip {
        local_send_ns,
        remote_ns,
        local_recv_ns,
    })
}

/// `serverTime` Bybit как эталон. Публичный эндпоинт — без ключей и без
/// подписи (`sign.rs` тут ни при чём, Decision 12 касается только приватных
/// запросов). Тот же приём, что `BybitPrivateRest` в `probe.rs`: `reqwest` в
/// `Cargo.toml` собран без фичи `blocking` (не наш файл, чтобы это менять),
/// поэтому здесь свой рантайм на один поток и `block_on` на каждый вызов —
/// цена этого моста ничтожна на масштабе «раз в час».
pub struct BybitServerTimeSource {
    client: reqwest::Client,
    runtime: tokio::runtime::Runtime,
    /// `{base_url}/v5/market/time`, склеенный один раз здесь: `base_url` не
    /// меняется за время жизни источника, и пересобирать эту строку на
    /// каждом `round_trip` было бы аллокацией без причины на пути, который
    /// и так раз в час ходит в сеть.
    url: String,
}

impl BybitServerTimeSource {
    /// `base_url` — параметр, не хардкод `api.bybit.com`: у Bybit есть
    /// демо-окружение на другом хосте, и какое из них опрашивать — решение
    /// вызывающего кода (`commands/`), не этого файла.
    pub fn new(base_url: impl Into<String>) -> Result<Self, ClockError> {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        let client = reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            .build()
            .map_err(|e| ClockError::Transport(e.to_string()))?;
        Ok(Self {
            client,
            runtime,
            url: format!("{}/v5/market/time", base_url.into()),
        })
    }
}

impl ReferenceClock for BybitServerTimeSource {
    fn round_trip(&mut self) -> Result<RoundTrip, ClockError> {
        // `RequestBuilder` строится синхронно, до входа в `async move`, и
        // владеет всем, что ему нужно, сам (`reqwest::Client` внутри —
        // `Arc`, клон дёшев): `self.url` читается заимствованием
        // (`as_str`), без клона и без пересборки строки на каждый вызов.
        let request = self.client.get(self.url.as_str());
        let local_send_ns = crate::bybit::conn::SystemClock.now_ns();
        let body = self.runtime.block_on(async move {
            let resp = request
                .send()
                .await
                .map_err(|e| ClockError::Transport(e.to_string()))?;
            resp.text()
                .await
                .map_err(|e| ClockError::Transport(e.to_string()))
        })?;
        let local_recv_ns = crate::bybit::conn::SystemClock.now_ns();
        parse_bybit_server_time(&body, local_send_ns, local_recv_ns)
    }
}

/// Метки реакционного пути (таск 15, `interfaces.md`: «монотонные через
/// трейт `Clock`, не `SystemTime`»). `SystemClock` намеренно не гарантирует
/// монотонность — его собственная документация называет цену: сравнимость с
/// биржевой эпохой ценой возможного отката при коррекции NTP. Стадии `lob
/// react` (`recv → разбор → книга → триггер → send`) сравнивают метки только
/// между собой, в пределах одного прогона, и такой откат превратил бы
/// длительность стадии в отрицательное число — что и проверяет тест ниже.
///
/// Реализация — единственное место в этом проходе, где `Instant` вызывается
/// напрямую: сама точка, где монотонность заводится. Горячий путь
/// (`lob/strategy.rs`, `commands/lob/react.rs`) вызывает только
/// `Clock::now_ns()` — им запрещён прямой вызов `Instant::now()`
/// (`interfaces.md`, запрет 2), и это проверяется грепом их собственного
/// исходника, как `levels.rs` проверяет себя.
#[derive(Debug, Clone, Copy)]
pub struct MonotonicClock {
    origin: std::time::Instant,
    origin_epoch_ns: i64,
}

impl MonotonicClock {
    /// Заводит начало отсчёта один раз: `Instant::now()` — точка опоры,
    /// `SystemClock` — во сколько эпохи Unix она пришлась, только для того,
    /// чтобы печатаемые метки были на что похожи. Разность двух вызовов
    /// `now_ns()` этого экземпляра ниже не зависит от того, насколько точно
    /// угадана эта точка — сдвиг общий для обеих меток и сокращается.
    pub fn start() -> Self {
        Self {
            origin: std::time::Instant::now(),
            origin_epoch_ns: crate::bybit::conn::SystemClock.now_ns(),
        }
    }
}

impl Clock for MonotonicClock {
    fn now_ns(&self) -> i64 {
        let elapsed_ns = i64::try_from(self.origin.elapsed().as_nanos()).unwrap_or(i64::MAX);
        self.origin_epoch_ns.saturating_add(elapsed_ns)
    }
}

#[cfg(test)]
mod tests;
