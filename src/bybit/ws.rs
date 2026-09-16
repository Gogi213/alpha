//! Разбор публичных WS-сообщений Bybit v5 и склейка их с книгой.
//!
//! Здесь только разбор и правила потока. Сокет, переподключение и запись живут
//! выше, в `commands/`: так эта часть тестируется без сети и без времени.
//!
//! Поля подтверждены по документации v5:
//! - `orderbook.<depth>.<symbol>` несёт `ts`, `cts`, `u`, `seq`, `type` и массивы
//!   `b`/`a` из пар `[цена, размер]`. Размер `0` удаляет уровень, `u = 1` означает
//!   рестарт сервиса и требует перезаписи книги.
//! - `publicTrade.<symbol>` несёт `T` (время исполнения), `S` (сторона агрессора),
//!   `v`, `p`, `i`, `BT` (блочная сделка) и `seq`. **Поля `cts` у него нет** —
//!   ключ склейки с книгой это `T` против `orderbook.cts`, оба времени матчинга.
//!
//! ## Два потока глубины (T45)
//!
//! Коллектор подписан на два топика стакана по каждому инструменту: быстрый
//! `orderbook.50.<symbol>` (как с самого начала) и глубокий
//! `orderbook.200.<symbol>`. Глубина — **из имени топика**, и она уезжает
//! признаком потока в `book::Update::depth`: у `.50` и `.200` свои книги, свои
//! последовательности `u` и свои файлы, поэтому сообщение обязано нести, из
//! какого потока оно пришло. Признака потока нет в формате бинлога (v3
//! заморожен, В-49) — при живом чтении его даёт топик, при реплее `Update`
//! несёт глубину основного файла (`bybit::conn::ORDERBOOK_DEPTH`).
//!
//! ## Разбор без DOM (таск 24)
//!
//! До этого места `parse_message` строило `serde_json::Value` — дерево с
//! аллокацией на каждый узел (объект, массив, строка) — на **каждое**
//! сообщение, и только из готового дерева читало нужные несколько полей.
//! Замер `docs/findings/recording-2026-09-11.md`/`pilot-2026-09-11.md`
//! назвал это основной стоимостью разбора (`parse_p99_ns` 400–850 мкс на
//! пуле против калибровочного суббюджета `PLAN.md` 3.1 в 200 мкс).
//!
//! Теперь сообщение разбирается прямо в типизированные структуры
//! (`RawOrderbookMsg`, `LevelsE9`, `TradesInto` ниже) через `serde_json` —
//! `serde_json` строит `Value` только когда его об этом явно просят
//! (`serde_json::Value` как тип назначения); при разборе в конкретный тип он
//! читает поля напрямую в целевые слоты, ни разу не создавая обобщённый узел
//! дерева. Строковые поля (`p`, `v`, `S`, необязательный `type`) взяты как
//! `&str` — заимствование из `raw`, а не копия: ни цена, ни размер, ни сторона
//! не содержат экранирования в протоколе площадки, так что заимствование
//! всегда успешно. Уровни книги и сделки разбираются визиторами **на лету**:
//! пара `["цена","размер"]` превращается в `(i64, i64)` в момент чтения, без
//! промежуточного `Vec<(&str, &str)>`; сделка кладётся прямо в
//! переиспользуемый `Vec<Event>` вызывающего (`parse_message_into`), без
//! промежуточного `Vec<RawTradeItem>`.
//!
//! Что остаётся аллоцировать после прогрева, по видам сообщений (число —
//! `tests/collector_bench.rs` и гейт `session.rs::parse_book_write_path_
//! allocations_per_message_after_warmup`): книжное сообщение — по одному
//! `Vec<(i64, i64)>` на **непустую** сторону (`book::Update` владеет
//! `bids`/`asks` и уходит владельцем в канал `ConnEvent::Message` — тип не в
//! зоне таска 24, менять нельзя; дельта с одной пустой стороной — одна
//! аллокация, снапшот 50+50 — по три роста на сторону сверх первой, см.
//! `LEVELS_INITIAL_CAPACITY`); лента сделок — ноль; служебное сообщение —
//! ноль; список событий — ноль (буфер вызывающего). Сверх этого — одна
//! `String` сырого кадра в `tungstenite` (`PLAN.md` 6.1: «транспорт ≤ 1 на
//! кадр, принято»).
//!
//! Какой из двух типов пробовать, решает `fast_topic` — дешёвая
//! проверка префикса `{"topic":"<префикс>` **в начале** сообщения без
//! разбора: в протоколе Bybit `topic` всегда идёт первым полем компактного
//! (без пробелов) JSON — то же самое подтверждают все фикстуры тестов ниже и
//! разведка `docs/plan/RECON-2026-09-11.md`. Всё, что этому образцу не
//! отвечает (пробелы после двоеточий, `topic` не первым полем, вовсе без
//! `topic`), идёт фолбэком `probe_topic` (таск 25, ревью таска 24):
//! типизированный разбор верхнего объекта до поля `topic` — и никакое
//! строковое поле или вложенный ключ `topic` быстрый путь не обманут,
//! потому что он смотрит только на самое начало. Сама проверка структуры
//! (порядок полей, экранирование внутри строк, отсутствующие необязательные
//! поля) остаётся за `serde_json` — префикс лишь выбирает, какую форму
//! пробовать, а корректность проверяет типизированный разбор. Ошибки формы
//! при валидном JSON — `BadShape`/`MissingField`/`BadNumber` по
//! `serde_json::Error::classify()`; `NotJson` — только синтаксис. Обе
//! функции отдают заодно **символ** топика (суффикс после последней точки):
//! мультиплексированное соединение таска 28 маршрутизирует им сообщение к
//! книге своего инструмента, и другого места, где символ уже прочитан, на
//! горячем пути нет.

use crate::book::Update;

/// Разобранное событие публичного потока.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// Обновление стакана. `Update` уже в целых 1e-9.
    Book(Update),
    /// Одна сделка ленты.
    Trade(Trade),
    /// Ответ на подписку, pong и прочее, что нас не касается.
    Other,
}

/// Сделка. Сторона — это сторона **агрессора**, и именно она решает, съел ли
/// поток уровень: бид-уровень потребляют продавцы.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Trade {
    /// Время исполнения на матчинге, миллисекунды. Сравнимо с `orderbook.cts`.
    pub exch_ms: i64,
    pub price_e9: i64,
    pub qty_e9: i64,
    pub aggressor_is_buy: bool,
    /// Блочная сделка. Такие не потребляют видимую ликвидность стакана, и
    /// засчитанные в объём они превращают снятие уровня в исполнение.
    pub block: bool,
    /// Сделка исполнена об **RPI-заявку** (`RPI` у Bybit). RPI-заявки
    /// одобренных маркет-мейкеров в `orderbook.{depth}` не видны, поэтому такая
    /// сделка не потребляет **видимую** ликвидность уровня: засчитанная в объём,
    /// она превращает снятие плотности в её исполнение — тот же механизм порчи
    /// метки, что у блочной сделки, но невидимый глазу (2026-09-16,
    /// `docs/findings/hft-underground-2026-09-16.md` §12).
    pub rpi: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// Синтаксис (`serde_json` `Category::Syntax`/`Eof`/`Io`) — не JSON.
    NotJson,
    MissingField(&'static str),
    BadNumber(&'static str),
    /// Валидный JSON неверной формы (`Category::Data`: объект вместо
    /// массива, строка вместо числа) — какая форма ожидалась, в аргументе.
    BadShape(&'static str),
}

/// `serde_json` → `ParseError` по классу: только синтаксис — `NotJson`,
/// валидный JSON неверной формы — `BadShape(form)`.
fn classify_json_error(e: &serde_json::Error, form: &'static str) -> ParseError {
    match e.classify() {
        serde_json::error::Category::Data => ParseError::BadShape(form),
        _ => ParseError::NotJson,
    }
}

/// Разбирает десятичную строку в целое 1e-9 без промежуточного `f64`.
///
/// Через `f64` этого делать нельзя: цена вроде `0.00000001` и большие количества
/// не переживают округление, а вся книга стоит на точном сравнении тиков.
pub fn parse_e9(s: &str) -> Option<i64> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    let (neg, body) = match s.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, s.strip_prefix('+').unwrap_or(s)),
    };
    let (int_part, frac_part) = match body.split_once('.') {
        Some((i, f)) => (i, f),
        None => (body, ""),
    };
    if int_part.is_empty() && frac_part.is_empty() {
        return None;
    }
    if !int_part.bytes().all(|b| b.is_ascii_digit())
        || !frac_part.bytes().all(|b| b.is_ascii_digit())
    {
        return None;
    }
    let mut v: i64 = 0;
    for b in int_part.bytes() {
        v = v.checked_mul(10)?.checked_add((b - b'0') as i64)?;
    }
    // Ровно девять знаков после точки: лишние отбрасываются, недостающие дополняются.
    let mut scale = 9;
    for b in frac_part.bytes() {
        if scale == 0 {
            break;
        }
        v = v.checked_mul(10)?.checked_add((b - b'0') as i64)?;
        scale -= 1;
    }
    for _ in 0..scale {
        v = v.checked_mul(10)?;
    }
    Some(if neg { -v } else { v })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TopicKind {
    /// Поток стакана и его глубина из имени топика
    /// (`orderbook.<depth>.<symbol>`): 50 — быстрый поток, 200 — глубокий
    /// (T45). Два потока по одному инструменту несут **разные** книги и
    /// разные последовательности `u`, поэтому глубина — часть вида топика,
    /// а не что-то, что вызывающий добавляет отдельно.
    Book(u32),
    Trade,
    Other,
}

fn topic_kind(topic: &str) -> TopicKind {
    if let Some(rest) = topic.strip_prefix(ORDERBOOK_TOPIC_PREFIX) {
        // `orderbook.<depth>.<symbol>`: глубина — сегмент между двумя
        // точками. Нечисловая глубина (`orderbook.full.<symbol>` — тоже
        // топик протокола, но не наш поток) делает топик чужим: разбирать
        // его как книгу значило бы завести поток, которого мы не
        // подписывали, и выдумать ему глубину.
        return match rest.split_once('.') {
            Some((depth, symbol)) if !symbol.is_empty() => match depth.parse::<u32>() {
                Ok(depth) => TopicKind::Book(depth),
                Err(_) => TopicKind::Other,
            },
            _ => TopicKind::Other,
        };
    }
    if topic.starts_with(TRADE_TOPIC_PREFIX) {
        TopicKind::Trade
    } else {
        TopicKind::Other
    }
}

/// Вид топика и его символ. Символ — суффикс после последней точки
/// (`orderbook.50.SOLUSDT` → `SOLUSDT`, `publicTrade.SOLUSDT` → `SOLUSDT`):
/// формат имени топика задан протоколом Bybit v5, не этим файлом. Нужен
/// мультиплексированному соединению (таск 28): один сокет несёт топики
/// многих инструментов, и маршрут события — суффикс его топика, а не
/// конфигурация соединения. `None` — топик не наш (`Other`) или пустой
/// суффикс.
fn split_topic(topic: &str) -> (TopicKind, Option<&str>) {
    let kind = topic_kind(topic);
    let symbol = match kind {
        TopicKind::Book(_) | TopicKind::Trade => topic.rsplit('.').next().filter(|s| !s.is_empty()),
        TopicKind::Other => None,
    };
    (kind, symbol)
}

/// Быстрый путь (см. doc модуля): компактное сообщение площадки начинается
/// ровно с `{"topic":"`. Значение топика кончается закрывающей кавычкой —
/// её позиция и даёт границу символа без разбора остального сообщения.
/// `None` — образец не совпал (в том числе кавычка не нашлась), решает
/// фолбэк.
fn fast_topic(raw: &str) -> Option<(TopicKind, Option<&str>)> {
    const KEY: &str = "{\"topic\":\"";
    let rest = raw.strip_prefix(KEY)?;
    let end = rest.find('"')?;
    Some(split_topic(&rest[..end]))
}

/// Фолбэк: верхний объект читается типизированно до поля `topic`
/// (остальные поля — `IgnoredAny`, без узлов дерева), заодно проверяется
/// синтаксис всего сообщения. `Cow` — значение с экранированием не
/// заимствуется и разбирается в свою строку, а не падает.
#[derive(serde::Deserialize)]
struct TopicProbe<'a> {
    #[serde(borrow)]
    topic: Option<CowStr<'a>>,
}

/// `Cow<'a, str>`, который заимствует, когда может. Голый
/// `#[serde(borrow)] Option<Cow<'a, str>>` serde разворачивает в обычный
/// `String` (специальный разбор у него есть только для `Cow` без
/// `Option`) — символ топика тогда не пережил бы собственную строку, и
/// маршрут фолбэка всегда был бы `None` (это и поймал тест
/// `parse_returns_the_topic_symbol_for_both_book_and_trade`).
struct CowStr<'a>(std::borrow::Cow<'a, str>);

impl<'de: 'a, 'a> serde::Deserialize<'de> for CowStr<'a> {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V<'a>(std::marker::PhantomData<&'a ()>);
        impl<'de: 'a, 'a> serde::de::Visitor<'de> for V<'a> {
            type Value = CowStr<'a>;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("строку topic")
            }
            fn visit_borrowed_str<E>(self, v: &'de str) -> Result<CowStr<'a>, E> {
                Ok(CowStr(std::borrow::Cow::Borrowed(v)))
            }
            fn visit_str<E>(self, v: &str) -> Result<CowStr<'a>, E> {
                Ok(CowStr(std::borrow::Cow::Owned(v.to_string())))
            }
            fn visit_string<E>(self, v: String) -> Result<CowStr<'a>, E> {
                Ok(CowStr(std::borrow::Cow::Owned(v)))
            }
        }
        d.deserialize_str(V(std::marker::PhantomData))
    }
}

fn probe_topic(raw: &str) -> Result<(TopicKind, Option<&str>), ParseError> {
    let probe: TopicProbe =
        serde_json::from_str(raw).map_err(|e| classify_json_error(&e, "topic"))?;
    Ok(match probe.topic.map(|c| c.0) {
        // Заимствованное значение живёт столько же, сколько само сообщение
        // — символ отдаётся срезом, без своей строки на событие.
        Some(std::borrow::Cow::Borrowed(t)) => split_topic(t),
        // Экранирование внутри имени топика протокол Bybit не порождает:
        // вид сообщения ещё определим, а маршрут по такому имени — нет
        // (срез не переживёт собственную строку). Честнее отдать `None`,
        // чем строить `String` на событие.
        Some(std::borrow::Cow::Owned(t)) => (topic_kind(&t), None),
        None => (TopicKind::Other, None),
    })
}

/// Первая ёмкость `Vec` уровней одной стороны. Дельта `orderbook.50` несёт
/// 1–5 уровней на сторону (фикстура таска 24, `tests/collector_bench.rs`, и
/// разведка `docs/plan/RECON-2026-09-11.md`); восемь — ближайшая степень
/// двойки сверху: одна аллокация на сторону без роста. Снапшот (50 на
/// сторону) дорастает удвоениями 8→16→32→64 — три роста, но снапшот приходит
/// раз на соединение/ресинк, не на каждое сообщение. Пустая сторона (`[]`,
/// частый случай дельты) не аллоцирует вовсе — резерв берётся при первой паре.
/// `serde_json` не даёт `size_hint` для массивов, поэтому число уровней до
/// разбора неизвестно.
const LEVELS_INITIAL_CAPACITY: usize = 8;

/// Пары `[цена, размер]` одной стороны, разобранные в целые 1e-9 **прямо из
/// потока** `serde_json` — без промежуточного `Vec<(&str, &str)>` и второго
/// прохода. Единственная аллокация на сторону — сам `Vec<(i64, i64)>`, и её
/// несёт `book::Update` (тип не в зоне таска 24): он уходит владельцем в
/// канал `ConnEvent::Message`, поэтому ни переиспользовать, ни занять из
/// скретча его нельзя, не меняя `book::Update` и `ConnEvent`.
///
/// Плохое число не роняет разбор ошибкой `serde` с потерей имени поля:
/// визитор дочитывает массив (иначе JSON останется недочитанным и `serde_json`
/// доложит синтаксическую ошибку вместо настоящей причины), а `bad` несёт имя
/// поля для `ParseError::BadNumber` — тот же вариант ошибки, что и до таска 24.
#[derive(Default)]
struct LevelsE9 {
    pairs: Vec<(i64, i64)>,
    bad: Option<&'static str>,
}

impl<'de> serde::Deserialize<'de> for LevelsE9 {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct LevelsVisitor;
        impl<'de> serde::de::Visitor<'de> for LevelsVisitor {
            type Value = LevelsE9;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("массив пар [\"цена\", \"размер\"]")
            }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> Result<LevelsE9, A::Error> {
                let mut out = LevelsE9::default();
                while let Some((p, q)) = seq.next_element::<(&'de str, &'de str)>()? {
                    if out.bad.is_some() {
                        continue;
                    }
                    if out.pairs.capacity() == 0 {
                        out.pairs.reserve_exact(LEVELS_INITIAL_CAPACITY);
                    }
                    match (parse_e9(p), parse_e9(q)) {
                        (Some(price), Some(qty)) => out.pairs.push((price, qty)),
                        (None, _) => out.bad = Some("level price"),
                        (Some(_), None) => out.bad = Some("level size"),
                    }
                }
                Ok(out)
            }
        }
        d.deserialize_seq(LevelsVisitor)
    }
}

/// Часть `orderbook.<depth>.<symbol>`, которая нужна разбору — сам объект
/// `data`. Остальные поля (`s` и т.п.) не читаются вовсе: `serde_json`
/// молча пропускает незнакомые ключи, не строя для них узел дерева.
#[derive(serde::Deserialize)]
struct RawOrderbookData {
    u: Option<u64>,
    seq: Option<u64>,
    cts: Option<i64>,
    #[serde(default)]
    b: LevelsE9,
    #[serde(default)]
    a: LevelsE9,
}

#[derive(serde::Deserialize)]
struct RawOrderbookMsg<'a> {
    #[serde(rename = "type", borrow)]
    kind: Option<&'a str>,
    ts: Option<i64>,
    cts: Option<i64>,
    data: Option<RawOrderbookData>,
}

fn orderbook_update(msg: RawOrderbookMsg, depth: u32) -> Result<Update, ParseError> {
    let data = msg.data.ok_or(ParseError::MissingField("data"))?;
    let u = data.u.ok_or(ParseError::MissingField("u"))?;
    // `seq` — сквозной счётчик WS и REST (в отличие от `u`, у которого
    // в двух каналах два разных счётчика). В контроле потока не участвует.
    let seq = data.seq.ok_or(ParseError::MissingField("seq"))?;
    // `cts` — время матчинга. У некоторых сообщений его нет; тогда берём `ts`,
    // время формирования, и это ухудшение точности, а не эквивалент.
    let cts_ms = data
        .cts
        .or(msg.cts)
        .or(msg.ts)
        .ok_or(ParseError::MissingField("cts"))?;
    if let Some(field) = data.b.bad.or(data.a.bad) {
        return Err(ParseError::BadNumber(field));
    }
    Ok(Update {
        is_snapshot: msg.kind == Some("snapshot"),
        // Глубина — из топика (T45), не из конфигурации: у `.50` и `.200`
        // свои книги и свои последовательности `u`.
        depth,
        u,
        seq,
        cts_ms,
        bids: data.b.pairs,
        asks: data.a.pairs,
    })
}

#[derive(serde::Deserialize)]
struct RawTradeItem<'a> {
    #[serde(rename = "T")]
    exch_ms: Option<i64>,
    #[serde(rename = "p", borrow)]
    price: Option<&'a str>,
    #[serde(rename = "v", borrow)]
    qty: Option<&'a str>,
    #[serde(rename = "S", borrow)]
    side: Option<&'a str>,
    #[serde(rename = "BT", default)]
    block: bool,
    /// Поле `RPI` у Bybit; `default` — чтобы лента без него читалась как
    /// `false` («не размечено»), а не падала разбором.
    #[serde(rename = "RPI", default)]
    rpi: bool,
}

fn trade_from_raw(t: &RawTradeItem) -> Result<Trade, ParseError> {
    let exch_ms = t.exch_ms.ok_or(ParseError::MissingField("T"))?;
    let price_e9 = t
        .price
        .and_then(parse_e9)
        .ok_or(ParseError::BadNumber("p"))?;
    let qty_e9 = t.qty.and_then(parse_e9).ok_or(ParseError::BadNumber("v"))?;
    let side = t.side.ok_or(ParseError::MissingField("S"))?;
    Ok(Trade {
        exch_ms,
        price_e9,
        qty_e9,
        aggressor_is_buy: side.eq_ignore_ascii_case("buy"),
        block: t.block,
        rpi: t.rpi,
    })
}

/// Итог разбора `publicTrade`: сами сделки уже лежат в буфере вызывающего,
/// здесь — только то, что превращается в `ParseError`.
#[derive(Default)]
struct TradeOutcome {
    saw_data: bool,
    bad: Option<ParseError>,
}

/// Сид разбора `publicTrade`: каждая сделка кладётся **прямо в `out`** —
/// переиспользуемый `Vec<Event>` вызывающего — без промежуточного
/// `Vec<RawTradeItem>`; после прогрева сообщение ленты не аллоцирует
/// вовсе. Верхний объект читается ключ за ключом: `data` — сидом списка,
/// всё остальное (`topic`, `type`, `ts`) пропускается `IgnoredAny` без
/// единого узла дерева.
struct TradesInto<'v>(&'v mut Vec<Event>);

impl<'de> serde::de::DeserializeSeed<'de> for TradesInto<'_> {
    type Value = TradeOutcome;
    fn deserialize<D: serde::Deserializer<'de>>(self, d: D) -> Result<TradeOutcome, D::Error> {
        d.deserialize_map(self)
    }
}

impl<'de> serde::de::Visitor<'de> for TradesInto<'_> {
    type Value = TradeOutcome;
    fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        f.write_str("сообщение publicTrade с массивом data")
    }
    fn visit_map<A: serde::de::MapAccess<'de>>(self, mut map: A) -> Result<TradeOutcome, A::Error> {
        let out = self.0;
        let mut outcome = TradeOutcome::default();
        while let Some(key) = map.next_key::<&'de str>()? {
            if key == "data" {
                outcome.saw_data = true;
                outcome.bad = map.next_value_seed(TradeListInto(&mut *out))?;
            } else {
                map.next_value::<serde::de::IgnoredAny>()?;
            }
        }
        Ok(outcome)
    }
}

struct TradeListInto<'v>(&'v mut Vec<Event>);

impl<'de> serde::de::DeserializeSeed<'de> for TradeListInto<'_> {
    type Value = Option<ParseError>;
    fn deserialize<D: serde::Deserializer<'de>>(self, d: D) -> Result<Self::Value, D::Error> {
        d.deserialize_seq(self)
    }
}

impl<'de> serde::de::Visitor<'de> for TradeListInto<'_> {
    type Value = Option<ParseError>;
    fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        f.write_str("массив сделок publicTrade")
    }
    fn visit_seq<A: serde::de::SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
        let mut bad = None;
        while let Some(t) = seq.next_element::<RawTradeItem<'de>>()? {
            if bad.is_some() {
                continue;
            }
            match trade_from_raw(&t) {
                Ok(trade) => self.0.push(Event::Trade(trade)),
                Err(e) => bad = Some(e),
            }
        }
        Ok(bad)
    }
}

/// Разбор в переиспользуемый буфер вызывающего: `out` очищается (`clear` —
/// длина в ноль, ёмкость цела) и получает события сообщения. Единственный
/// горячий вход (`bybit::conn::Connection::run` держит один `Vec<Event>` на
/// соединение); `parse_message` ниже — обёртка для тестов и вызывающих без
/// своего буфера. На `Err` содержимое `out` не определено (частично
/// разобранная лента) — вызывающий не читает его, следующий вызов очистит.
pub fn parse_message_into<'a>(
    raw: &'a str,
    out: &mut Vec<Event>,
) -> Result<Option<&'a str>, ParseError> {
    out.clear();
    let ((kind, symbol), validated) = match fast_topic(raw) {
        Some(found) => (found, false),
        None => (probe_topic(raw)?, true),
    };
    match kind {
        TopicKind::Book(depth) => {
            let msg: RawOrderbookMsg =
                serde_json::from_str(raw).map_err(|e| classify_json_error(&e, "orderbook"))?;
            out.push(Event::Book(orderbook_update(msg, depth)?));
            Ok(symbol)
        }
        TopicKind::Trade => {
            let mut de = serde_json::Deserializer::from_str(raw);
            let outcome = serde::de::DeserializeSeed::deserialize(TradesInto(out), &mut de)
                .map_err(|e| classify_json_error(&e, "publicTrade"))?;
            de.end().map_err(|_| ParseError::NotJson)?;
            if !outcome.saw_data {
                return Err(ParseError::MissingField("data"));
            }
            if let Some(err) = outcome.bad {
                return Err(err);
            }
            Ok(symbol)
        }
        TopicKind::Other => {
            // Неизвестный топик с быстрого пути — сообщение обязано остаться
            // валидным JSON (иначе это `NotJson`, не тихий `Other`);
            // фолбэк уже прочитал его целиком, второй проход не нужен.
            if !validated {
                serde_json::from_str::<serde::de::IgnoredAny>(raw)
                    .map_err(|_| ParseError::NotJson)?;
            }
            out.push(Event::Other);
            Ok(symbol)
        }
    }
}

/// Разбирает одно текстовое сообщение публичного потока в свежий `Vec` —
/// обёртка над `parse_message_into` для тех, у кого нет своего буфера.
pub fn parse_message(raw: &str) -> Result<Vec<Event>, ParseError> {
    let mut out = Vec::new();
    parse_message_into(raw, &mut out)?;
    Ok(out)
}

/// Сообщения подписки. Отдельными функциями, чтобы они были в тестах, а не
/// в строке посреди сетевого кода.
pub fn sub_orderbook(depth: u32, symbol: &str) -> String {
    format!(
        r#"{{"op":"subscribe","args":["orderbook.{depth}.{symbol}"]}}"#,
        depth = depth,
        symbol = symbol
    )
}

/// Длина `args` подписки на один инструмент — все топики потоков, как их
/// считает Bybit: имя топика без кавычек и запятых (проверка предела 21 000
/// символов ведётся по содержимому массива `args`, `bybit::conn::
/// MAX_ARGS_CHARS`). Функция здесь, а не у вызывающего, потому что имена
/// топиков — протокол этого файла. `depths` — потоки стакана, на которые
/// подписывается соединение (T45: `.50` и `.200`, `bybit::conn::
/// SUBSCRIBED_DEPTHS`); топик ленты один.
pub fn pool_args_chars(depths: &[u32], symbol: &str) -> usize {
    // Арифметика, не `format!`: функция зовётся на каждый инструмент
    // раскладки (761 раз на старте), и строить ради длины временные строки
    // незачем. Совпадение с реально отправленным `args` держит тест
    // `pool_args_chars_matches_the_topics_sub_pool_actually_sends`.
    depths
        .iter()
        .map(|&d| ORDERBOOK_TOPIC_PREFIX.len() + decimal_len(d) + 1 + symbol.len())
        .sum::<usize>()
        + TRADE_TOPIC_PREFIX.len()
        + symbol.len()
}

const ORDERBOOK_TOPIC_PREFIX: &str = "orderbook.";
const TRADE_TOPIC_PREFIX: &str = "publicTrade.";

fn decimal_len(depth: u32) -> usize {
    let mut n = 1;
    let mut v = depth;
    while v >= 10 {
        v /= 10;
        n += 1;
    }
    n
}

/// Одна подписка на много инструментов сразу: `orderbook.<depth>.<sym>` по
/// каждому потоку `depths` и `publicTrade.<sym>` каждого инструмента — в один
/// массив `args` одного сообщения (T45 добавил второй поток стакана, не
/// второе соединение: `.50` и `.200` одного инструмента идут по одному
/// сокету, чтобы порядок между потоками задавала биржа, а не наши сокеты).
/// Для linear (Futures) Bybit v5 не ограничивает **число** `args` в запросе
/// («No args limit for Futures and Spread for now»), ограничена только
/// суммарная длина `args` **соединения** (21 000 символов) — её считает
/// вызывающий раскладкой пула (`feed::live::plan_connections`), не эта
/// функция.
pub fn sub_pool(depths: &[u32], symbols: &[&str]) -> String {
    let mut s = String::from(r#"{"op":"subscribe","args":["#);
    let mut sep = "";
    for sym in symbols {
        for &depth in depths {
            s.push_str(sep);
            sep = ",";
            s.push_str(&format!(r#""{ORDERBOOK_TOPIC_PREFIX}{depth}.{sym}""#));
        }
        s.push_str(sep);
        sep = ",";
        s.push_str(&format!(r#""{TRADE_TOPIC_PREFIX}{sym}""#));
    }
    s.push_str("]}");
    s
}

#[cfg(test)]
mod tests;
