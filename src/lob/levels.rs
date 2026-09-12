//! Жизнь уровня и шесть признаков его истории (план, §1.1).
//!
//! Уровень **рождается**, когда размер впервые превышает порог `H3`;
//! **умирает**, когда размер падает ниже 20% своего максимума или цена уходит
//! за топ-50. Повторное превышение на той же цене — **новый** уровень, объём
//! через разрыв не переносится.
//!
//! Правила, зафиксированные здесь, потому что план допускает одно прочтение
//! только вместе с ними:
//!
//! - Порог рождения строгий: размер должен быть **больше** `H3`, равенство
//!   не рождает. Порог смерти строгий в другую сторону: мёртв, когда размер
//!   **строго ниже** 20% максимума; ровно 20% — ещё жив (проверяется тестом
//!   на уровне L3: `28 * 5 == 140` не убивает, `27 * 5 < 140` убивает).
//! - Смерть считается целочисленно, `5 * size < max` в 128 битах: граница
//!   проходит по трейту с крейтом, глубже целые (`ARCHITECTURE.md` A1).
//! - Рождение только внутри топ-50: наблюдение вне топ-50 с размером выше
//!   порога уровень не создаёт — такой уровень по определению уже мёртв.
//! - Отсутствие тика в кадре читается как размер ноль: либо уровень снят,
//!   либо цена ушла из отслеживаемого окна — в обоих случаях он мёртв по
//!   правилу «ниже 20%» (максимум всегда положителен, раз рождение строгое).
//! - `repeat_count` считает **все** прошлые рождения на этой цене и стороне
//!   строго внутри скользящего часа, включая рождения прогревных уровней,
//!   которых нет в выборке: они рождались буквально, план спрашивает именно
//!   это. Граница окна строгая: рождение ровно час назад уже не считается.
//! - `repriced` — эвристика, в гейты не входит: в том же кадре, где уровень
//!   умер, на соседней цене (`tick ± 1`) той же стороны родился уровень
//!   сопоставимого размера — размер новорождённого внутри удвоенного
//!   максимума умершего в обе стороны. Печатается как есть.
//! - Монотонность — точная, а не «в основном рос»: пара последовательных
//!   наблюдений с убыванием, случившаяся не позже кадра максимума, гасит
//!   флаг. Убывание после максимума флаг не трогает.
//! - `time_to_max_ms` меряется до **первого** достижения максимума.
//! - Прогрев: рождения раньше `первый_кадр + warmup_ms` отслеживаются (нужны
//!   для `repeat_count`), но в выборку не попадают. Уровень, видимый уже в
//!   первом кадре с размером выше порога, считается рождённым в первом кадре:
//!   более ранней метки у реплея нет. **В режиме `floor` (`H3Mode`) прогрева
//!   нет вовсе** — эмиссия начинается с первого рождения; `warmup_ms`
//!   конфигурации в этом режиме не читается (план D-H3, таск 02).
//! - Порядок выдачи детерминирован: смерти одного кадра идут по возрастанию
//!   `(сторона, тик)`. Один и тот же журнал, прогнанный дважды, даёт
//!   побайтово одинаковый вывод — проверяемое свойство A1/A2 из архитектуры.
//!
//! Классификация исхода (`eaten`/`pulled`/`mixed`, шаг 1.2) — этот модуль:
//! объём трейдов с агрессором против уровня копится в `observe_trade`,
//! на смерти пишется в `traded_lots`, правило 70/20 читает `outcome`.
//! Блочные сделки в объём не входят; сторона агрессора решает, чей уровень
//! трейд ест: бид — продавец, аск — покупатель. Склейка по времени матчинга:
//! у трейда берётся `exch_ms` (время исполнения), у кадра — метка кадра,
//! подачи вне жизни уровня не считаются. Вызывающий подаёт события
//! в неубывающем времени матчинга и уже перевёл цену в тики,
//! а количество — в лоты: здесь только целые, кучи нет.
//!
//! # Касания (таск 35, В-42)
//!
//! Третий тип записи рядом со смертью — **касание** живого уровня
//! (`TouchRecord`): практики торгуют не смерть плотности, а подход цены к
//! ней и отскок (`docs/findings/practitioners-2026-09-13.md`, §1). Правила
//! назначены до данных, числа — только из существующих констант:
//!
//! - Касание **начинается** в кадре, где живой уровень, **живший до кадра**
//!   (`birth_ms < ts_ms`, В-43), **стал лучшей ценой своей стороны** —
//!   наблюдением с индексом 0, не будучи ею на прошлом наблюдении. Кадр
//!   идёт от лучшей цены вглубь — тот же контракт, на котором у вызывающего
//!   стоит `in_top50 = i < 50` (`Book::levels`), второго порядка здесь нет.
//!   Рождение лучшей ценой — **не касание**: «цена дошла» — переход, а не
//!   появление; такой уровень касается при следующем становлении лучшим
//!   после ухода. Кадры одной миллисекунды — один момент: уровень, ставший
//!   лучшим в миллисекунду рождения, читается как родившийся лучшим.
//! - Касание **заканчивается** в кадре, где уровень перестал быть лучшей
//!   ценой (появилась цена лучше), либо смертью уровня — тогда
//!   `ended_by_death`, `end_ms` — кадр смерти. Смерть и уход с лучшей цены
//!   в одном кадре читаются как смерть: конец касания ждёт свипа.
//! - Один уровень касается сколько угодно раз, `touch_index` 0, 1, 2…;
//!   касания уровней прогрева считаются (индекс растёт), но не эмитируются —
//!   тот же режим, что у их смертей.
//! - `frontrun_lots` — сумма лотов той же стороны **строго лучше** уровня по
//!   цене на **последнем кадре до** касания (в кадре касания уровень сам
//!   лучшая цена, лучше него ничего нет): префикс кадра до индекса уровня,
//!   запоминается на каждом наблюдении, читается в момент начала.
//! - `size_max_before` — максимум размера до кадра начала; `size_at_touch` —
//!   размер в кадре начала; `traded_during` — объём против уровня,
//!   накопленный `observe_trade` между началом и концом.
//! - `stack_levels` — сколько уровней той же стороны живы после кадра начала
//!   с размером не ниже `H3` (сам коснувшийся уровень входит, если его размер
//!   не ниже `H3`); считается по состоянию после свипа этого кадра.
//! - `round_zeros` — число нулей в конце `price_tick` в десятичной записи,
//!   0/1/2/3+ (`round_zeros`).
//! - Порядок выдачи детерминирован: касания умерших за кадр — в порядке
//!   `(сторона, тик)` вместе с их смертями, затем касания выживших — в
//!   порядке кадра. Горячий путь тот же: состояние касания — несколько целых
//!   в `Live`, ключи с событием касания копятся в предвыделенном буфере,
//!   записи уходят в `&mut Vec<TouchRecord>` вызывающего.

use std::collections::{BTreeMap, VecDeque};

use crate::book::Side;

/// Как задан порог `H3` — план D-H3 (таск 02): два режима, какой войдёт в
/// предрегистрацию, решает двухчасовой пилот, не этот код. Явный выбор без
/// умолчания — вызывающий (`commands/lob`) обязан подставить один из двух.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum H3Mode {
    /// Пол в лотах — из `instruments.csv` (пишет отдельный шаг сборки пула),
    /// **без прогрева**: уровень эмитится с первого рождения. Рабочий режим
    /// отладки — держит `levels > 0` на прогонах короче часа.
    Floor { h3_lots: i64 },
    /// Порог — заранее измеренный 99-й перцентиль `size_max` по
    /// времени-взвешенной выборке за скользящий час (измерение — вне этого
    /// модуля, число приходит параметром, как раньше). Прогрев `warmup_ms`
    /// действует как в прежнем определении: рождения раньше него
    /// отслеживаются, но не эмитируются.
    Percentile { h3_lots: i64 },
}

impl H3Mode {
    /// Порог рождения в лотах — общий для обоих режимов кусок конфигурации.
    fn h3_lots(self) -> i64 {
        match self {
            H3Mode::Floor { h3_lots } | H3Mode::Percentile { h3_lots } => h3_lots,
        }
    }
}

/// Конфигурация трекера. Порог `H3` — режим (см. `H3Mode`), окно часа —
/// параметр, а не хардкод: предрегистрированные значения подставляет
/// вызывающий шагом позже.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelsConfig {
    /// Режим порога `H3`: `floor` или `percentile`, без умолчания.
    pub mode: H3Mode,
    /// Прогрев в миллисекундах: в режиме `percentile` рождения раньше него
    /// не попадают в выборку. В режиме `floor` не читается — там прогрева
    /// нет по определению режима.
    pub warmup_ms: i64,
    /// Скользящее окно `repeat_count` в миллисекундах — общее для обоих
    /// режимов, план §3: «скользящий час».
    pub repeat_window_ms: i64,
}

/// Одно наблюдение уровня в кадре одной стороны.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelObs {
    /// Цена в тиках.
    pub tick: i64,
    /// Размер в лотах.
    pub size_lots: i64,
    /// Входит ли цена в топ-50 этой стороны в этом кадре.
    pub in_top50: bool,
}

/// Каким правилом уровень умер. Нужно шагу 1.1, чтобы различать два правила
/// смерти из плана, а не смешивать их в одну кучу.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeathKind {
    /// Размер упал строго ниже 20% максимума (включая исчезновение из кадра).
    BelowFraction,
    /// Цена ушла за топ-50 при живом размере.
    LeftTop,
}

/// Исход уровня по правилу 70/20 из плана (§1.2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// Объём против уровня ≥ 70% максимума: ликвидность съели.
    Eaten,
    /// Объём против уровня ≤ 20% максимума: уровень сняли.
    Pulled,
    /// Между 20% и 70%: смешанный исход.
    Mixed,
}

/// Правило 70/20 строго целочисленно, без деления: `10 * traded >= 7 * max`
/// есть eaten, `5 * traded <= max` есть pulled, иначе mixed. Границы
/// включительные: ровно 70% — eaten, ровно 20% — pulled.
pub fn classify_outcome(traded_lots: i64, size_max: i64) -> Outcome {
    let t = traded_lots as i128;
    let m = size_max as i128;
    if t * 10 >= m * 7 {
        Outcome::Eaten
    } else if t * 5 <= m {
        Outcome::Pulled
    } else {
        Outcome::Mixed
    }
}

/// Один трейд ленты, уже переведённый вызывающим в тики и лоты.
/// Сторона — это сторона агрессора: она выбирает, какой уровень трейд ест.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeHit {
    /// Цена сделки в тиках: обязана совпасть с тиком уровня точь-в-точь.
    pub tick: i64,
    /// Размер сделки в лотах.
    pub lots: i64,
    /// `true` — агрессор-покупатель (ест аск), `false` — продавец (ест бид).
    pub aggressor_is_buy: bool,
    /// Блочная сделка: видимую ликвидность не потребляет, в объём не идёт.
    pub block: bool,
    /// Время исполнения на матчинге. Сравнимо с меткой кадра; подачи
    /// раньше рождения уровня не считаются.
    pub exch_ms: i64,
}

/// Запись умершего уровня: ключи, шесть признаков истории и место под 1.2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LevelRecord {
    /// Сторона книги (нужна шагу 2.1 для полярности по стороне).
    pub side: Side,
    /// Цена в тиках.
    pub price_tick: i64,
    /// Кадр рождения, мс.
    pub birth_ms: i64,
    /// Кадр смерти, мс.
    pub death_ms: i64,
    /// Признак 1: время жизни, мс.
    pub lifetime_ms: i64,
    /// Признак 2: максимум размера в лотах (читает шаг 1.2 для правила 70/20).
    pub size_max: i64,
    /// Признак 3: время до первого достижения максимума, мс.
    pub time_to_max_ms: i64,
    /// Признак 4: размер рос монотонно вплоть до максимума.
    pub size_monotonic: bool,
    /// Признак 5: сколько уровней уже рождалось на этой цене за скользящий час.
    pub repeat_count: u32,
    /// Признак 6: эвристика переставления (печатается, в гейты не входит).
    pub repriced: bool,
    /// Какое из двух правил смерти сработало.
    pub death: DeathKind,
    /// Объём трейдов против уровня в лотах за его жизнь (шаг 1.2).
    pub traded_lots: i64,
}

impl LevelRecord {
    /// Исход по правилу 70/20 от накопленного объёма против максимума.
    pub fn outcome(&self) -> Outcome {
        classify_outcome(self.traded_lots, self.size_max)
    }
}

/// Снимок живого (ещё не умершего) уровня — то, что «стоит в стакане
/// сейчас» на момент последнего кадра (таск 33, дашборд по плотностям).
/// Только чтение состояния трекера; в горячий путь не входит.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LiveLevel {
    pub side: Side,
    pub price_tick: i64,
    pub birth_ms: i64,
    /// Размер в лотах на последнем кадре, где уровень виден.
    pub size_lots: i64,
    /// Максимум размера за жизнь (тот же `size_max`, что у записи).
    pub size_max: i64,
    /// Сколько уровней уже рождалось на этой цене за скользящее окно.
    pub repeat_count: u32,
    /// Объём сделок против уровня в лотах на этот момент.
    pub traded_lots: i64,
}

/// Запись касания живого уровня (таск 35, В-42): уровень стал лучшей ценой
/// своей стороны и перестал ею быть — или умер, не перестав. Правила — в
/// документации модуля («Касания»). Уровень при этом жив и в `LevelRecord`
/// не попадает ничем — поэтому запись отдельная.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TouchRecord {
    /// Сторона книги.
    pub side: Side,
    /// Цена в тиках.
    pub price_tick: i64,
    /// Порядковый номер касания у этого уровня: 0, 1, 2…
    pub touch_index: u32,
    /// Кадр начала касания, мс.
    pub start_ms: i64,
    /// Кадр конца касания (уход с лучшей цены или смерть), мс.
    pub end_ms: i64,
    /// `end_ms − start_ms`.
    pub duration_ms: i64,
    /// Кадр рождения уровня, мс: возраст на момент касания —
    /// `start_ms − level_birth_ms`.
    pub level_birth_ms: i64,
    /// Размер в лотах в кадре начала.
    pub size_at_touch: i64,
    /// Максимум размера до кадра начала.
    pub size_max_before: i64,
    /// Объём сделок против уровня в лотах за касание.
    pub traded_during: i64,
    /// Сумма лотов той же стороны строго лучше уровня по цене на последнем
    /// кадре до касания.
    pub frontrun_lots: i64,
    /// Число нулей в конце `price_tick` в десятичной записи: 0/1/2/3+.
    pub round_zeros: u8,
    /// Касание кончилось смертью уровня, а не уходом с лучшей цены.
    pub ended_by_death: bool,
    /// Живых уровней той же стороны с размером не ниже `H3` после кадра
    /// начала (включая сам уровень).
    pub stack_levels: u32,
}

impl TouchRecord {
    /// Возраст уровня на момент касания, мс.
    pub fn age_ms(&self) -> i64 {
        self.start_ms - self.level_birth_ms
    }
}

/// Потолок счётчика `round_zeros`: «3+» — практики называют круглым и
/// `1.100`, и `1111`, глубже трёх нулей различать нечего (§2 находок).
pub const ROUND_ZEROS_CAP: u8 = 3;

/// Число нулей в конце десятичной записи тика, не больше `ROUND_ZEROS_CAP`:
/// `100 → 2`, `1010 → 1`, `1234 → 0`, `10_000 → 3`. Ноль нулей не имеет —
/// тика ноль у цены не бывает, но арифметика тотальна. Знак не читается.
pub fn round_zeros(price_tick: i64) -> u8 {
    let mut n = price_tick.unsigned_abs();
    let mut zeros = 0u8;
    while n != 0 && n.is_multiple_of(10) && zeros < ROUND_ZEROS_CAP {
        n /= 10;
        zeros += 1;
    }
    zeros
}

/// Состояние идущего касания — целые в `Live`, кучи нет.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Touch {
    start_ms: i64,
    /// Номер кадра начала: `stack` заполняется после свипа того же кадра, и
    /// сравнение по номеру кадра, а не по метке (несколько кадров могут
    /// делить миллисекунду), говорит, что заполнять пора.
    start_frame: u64,
    size_at: i64,
    size_max_before: i64,
    frontrun: i64,
    traded_at_start: i64,
    stack: u32,
    /// Уровень в этом кадре перестал быть лучшей ценой: конец касания ждёт
    /// свипа — смерть в том же кадре имеет приоритет.
    end_pending: bool,
}

/// Живой уровень: всё состояние — несколько целых, кучи нет.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Live {
    birth_ms: i64,
    max: i64,
    max_ms: i64,
    prev: i64,
    first_decrease_ms: Option<i64>,
    repeat: u32,
    seen_frame: u64,
    seen_top50: bool,
    seen_size: i64,
    traded: i64,
    /// Сумма лотов строго лучше уровня по цене на последнем кадре, где он
    /// наблюдался, — фронтран будущего касания.
    better_lots: i64,
    /// Был ли уровень лучшей ценой на последнем наблюдении: касание — переход
    /// `false → true`, не состояние.
    was_best: bool,
    /// Сколько касаний у уровня уже кончилось.
    touch_index: u32,
    touch: Option<Touch>,
}

/// Трекер уровней. Состояние между кадрами — две карты с предвыделенными
/// ёмкостями и переиспользуемые буферы новорождённых, свипа и ключей с
/// событием касания: установившийся кадр без рождений, смертей и касаний
/// не трогает кучу вообще (требование гейта GC).
pub struct LevelTracker {
    cfg: LevelsConfig,
    live: BTreeMap<(u8, i64), Live>,
    births: BTreeMap<(u8, i64), VecDeque<i64>>,
    newborns: Vec<(u8, i64, i64)>,
    sweep: Vec<(u8, i64)>,
    touched: Vec<(u8, i64)>,
    /// Буфер касаний для `observe_frame` без выхода касаний: те же события
    /// считаются, записи отбрасываются, ёмкость переиспользуется.
    touch_scratch: Vec<TouchRecord>,
    start_ms: Option<i64>,
    frame: u64,
}

/// Запись касания из состояния уровня в момент конца.
fn touch_record(
    key: (u8, i64),
    lv: &Live,
    t: Touch,
    end_ms: i64,
    stack: u32,
    ended_by_death: bool,
) -> TouchRecord {
    TouchRecord {
        side: side_of(key),
        price_tick: key.1,
        touch_index: lv.touch_index,
        start_ms: t.start_ms,
        end_ms,
        duration_ms: end_ms - t.start_ms,
        level_birth_ms: lv.birth_ms,
        size_at_touch: t.size_at,
        size_max_before: t.size_max_before,
        traded_during: lv.traded.saturating_sub(t.traded_at_start),
        frontrun_lots: t.frontrun,
        round_zeros: round_zeros(key.1),
        ended_by_death,
        stack_levels: stack,
    }
}

fn side_key(side: Side) -> u8 {
    match side {
        Side::Bid => 0,
        Side::Ask => 1,
    }
}

fn side_of(key: (u8, i64)) -> Side {
    if key.0 == 0 {
        Side::Bid
    } else {
        Side::Ask
    }
}

/// Строго ниже 20%: `5 * size < max` без деления и без остатка.
fn below_fraction(size: i64, max: i64) -> bool {
    size as i128 * 5 < max as i128
}

/// Сопоставимый размер для эвристики `repriced`: внутри удвоения в обе
/// стороны, строго целочисленно.
fn comparable_size(a: i64, b: i64) -> bool {
    let (a, b) = (a as i128, b as i128);
    a <= 2 * b && b <= 2 * a
}

impl LevelTracker {
    /// Создаёт трекер. Порог должен быть положителен, окно — тоже, прогрев
    /// неотрицателен: нулевой порог рождал бы уровень из пустого места.
    pub fn new(cfg: LevelsConfig) -> Self {
        assert!(cfg.mode.h3_lots() > 0, "порог H3 обязан быть положителен");
        assert!(cfg.warmup_ms >= 0, "прогрев не может быть отрицателен");
        assert!(
            cfg.repeat_window_ms > 0,
            "окно repeat_count обязано быть положительно"
        );
        Self {
            cfg,
            live: BTreeMap::new(),
            births: BTreeMap::new(),
            newborns: Vec::with_capacity(8),
            sweep: Vec::with_capacity(8),
            touched: Vec::with_capacity(8),
            touch_scratch: Vec::with_capacity(8),
            start_ms: None,
            frame: 0,
        }
    }

    /// Сколько уровней живо прямо сейчас. Нужно тестам, чтобы убедиться, что
    /// фикстура никого не потеряла и не оставила висеть.
    pub fn live_count(&self) -> usize {
        self.live.len()
    }

    /// Снимок всех живых уровней в порядке ключа (сторона, цена) — «что стоит
    /// в стакане сейчас» для дашборда (таск 33). Уровни, родившиеся в прогреве,
    /// включены: они живые, просто при смерти не будут эмитированы.
    pub fn live_levels(&self, out: &mut Vec<LiveLevel>) {
        out.clear();
        for (&key, lv) in &self.live {
            out.push(LiveLevel {
                side: side_of(key),
                price_tick: key.1,
                birth_ms: lv.birth_ms,
                size_lots: lv.seen_size,
                size_max: lv.max,
                repeat_count: lv.repeat,
                traded_lots: lv.traded,
            });
        }
    }

    /// Один кадр одной стороны без выхода касаний: те же события, что у
    /// `observe_frame_with_touches` (состояние касаний ведётся, индексы
    /// растут), записи касаний отбрасываются. Для читателей, которым нужны
    /// только смерти (`watch`, `profiles`). Умершие за кадр дописываются в
    /// `out` в порядке возрастания цены; ёмкость `out` — забота вызывающего,
    /// трекер её не растит сам и в горячем пути не аллоцирует.
    pub fn observe_frame(
        &mut self,
        ts_ms: i64,
        side: Side,
        levels: &[LevelObs],
        out: &mut Vec<LevelRecord>,
    ) {
        // `take` кладёт на место пустой вектор без выделения; ёмкость буфера
        // возвращается назад тем же ходом — куча после прогрева не трогается.
        let mut scratch = std::mem::take(&mut self.touch_scratch);
        scratch.clear();
        self.observe_frame_with_touches(ts_ms, side, levels, out, &mut scratch);
        self.touch_scratch = scratch;
    }

    /// Один кадр одной стороны. Умершие за кадр дописываются в `out`
    /// в порядке возрастания цены, кончившиеся касания — в `touches`
    /// (порядок — документация модуля, «Касания»); ёмкость обоих — забота
    /// вызывающего, трекер её не растит сам и в горячем пути не аллоцирует.
    /// Кадр идёт от лучшей цены вглубь (`Book::levels`): наблюдение с
    /// индексом 0 — лучшая цена стороны.
    pub fn observe_frame_with_touches(
        &mut self,
        ts_ms: i64,
        side: Side,
        levels: &[LevelObs],
        out: &mut Vec<LevelRecord>,
        touches: &mut Vec<TouchRecord>,
    ) {
        if self.start_ms.is_none() {
            self.start_ms = Some(ts_ms);
        }
        self.frame += 1;
        let frame = self.frame;
        let h3 = self.cfg.mode.h3_lots();
        let window = self.cfg.repeat_window_ms;
        let s = side_key(side);

        self.newborns.clear();
        self.touched.clear();
        // Сумма лотов строго лучше текущего наблюдения по цене — префикс
        // кадра до его индекса: на индексе 0 ноль, дальше копится.
        let mut better_lots: i64 = 0;
        for (i, ob) in levels.iter().enumerate() {
            let key = (s, ob.tick);
            let best = i == 0;
            match self.live.get_mut(&key) {
                Some(lv) => {
                    lv.seen_frame = frame;
                    lv.seen_top50 = ob.in_top50;
                    lv.seen_size = ob.size_lots;
                    if ob.size_lots < lv.prev && lv.first_decrease_ms.is_none() {
                        lv.first_decrease_ms = Some(ts_ms);
                    }
                    lv.prev = ob.size_lots;
                    let max_before = lv.max;
                    if ob.size_lots > lv.max {
                        lv.max = ob.size_lots;
                        lv.max_ms = ts_ms;
                    }
                    // Фронтран касания — с последнего кадра до него: читается
                    // до того, как значение этого кадра его перезапишет.
                    let frontrun = lv.better_lots;
                    lv.better_lots = better_lots;
                    // Касание — переход на лучшую цену уровня, жившего до
                    // кадра (В-43): был не лучшим на последнем наблюдении и
                    // родился раньше этой метки. Родившийся лучшей ценой (или
                    // ставший ею в миллисекунду рождения) касается только
                    // после ухода с лучшей цены и возврата.
                    let arrives = best && !lv.was_best && lv.birth_ms < ts_ms;
                    lv.was_best = best;
                    match (&mut lv.touch, arrives, best) {
                        (None, true, _) => {
                            lv.touch = Some(Touch {
                                start_ms: ts_ms,
                                start_frame: frame,
                                size_at: ob.size_lots,
                                size_max_before: max_before,
                                frontrun,
                                traded_at_start: lv.traded,
                                stack: 0,
                                end_pending: false,
                            });
                            self.touched.push(key);
                        }
                        (Some(t), _, false) => {
                            // Конец, отложенный до свипа, разрешается в том же
                            // кадре — второго ожидающего конца не бывает.
                            debug_assert!(!t.end_pending);
                            t.end_pending = true;
                            self.touched.push(key);
                        }
                        (None, false, _) | (Some(_), _, true) => {}
                    }
                }
                None => {
                    if ob.in_top50 && ob.size_lots > h3 {
                        let repeat = self.count_prior_births(key, ts_ms, window);
                        // Рождение лучшей ценой — не касание (В-43): «цена
                        // дошла» — это переход, а не появление; запоминается
                        // только `was_best`.
                        self.live.insert(
                            key,
                            Live {
                                birth_ms: ts_ms,
                                max: ob.size_lots,
                                max_ms: ts_ms,
                                prev: ob.size_lots,
                                first_decrease_ms: None,
                                repeat,
                                seen_frame: frame,
                                seen_top50: true,
                                seen_size: ob.size_lots,
                                traded: 0,
                                better_lots,
                                was_best: best,
                                touch_index: 0,
                                touch: None,
                            },
                        );
                        self.newborns.push((s, ob.tick, ob.size_lots));
                    }
                }
            }
            better_lots = better_lots.saturating_add(ob.size_lots);
        }

        // Свип двухфазный и по своей стороне: кадр несёт одну сторону, и
        // отсутствие тика читается как ноль только в ней — уровни второй
        // стороны этот вызов не трогает (иначе бид и аск убивали бы друг друга
        // по очереди на каждом штампе). Итерация карты уже идёт по возрастанию
        // ключа — порядок выдачи детерминирован. Куча не растёт, пока хватает
        // ёмкостей буферов. Тем же обходом считается «завал» — выжившие этой
        // стороны с размером не ниже `H3` — для касаний, начавшихся в кадре.
        let live = &self.live;
        let sweep = &mut self.sweep;
        sweep.clear();
        let mut stack: u32 = 0;
        for (key, lv) in live.iter() {
            if key.0 != s {
                continue;
            }
            if lv.seen_frame != frame || !lv.seen_top50 || below_fraction(lv.seen_size, lv.max) {
                sweep.push(*key);
            } else if lv.seen_size >= h3 {
                stack = stack.saturating_add(1);
            }
        }
        let newborns = &self.newborns;
        let warm_end = self
            .start_ms
            .unwrap_or(ts_ms)
            .saturating_add(self.effective_warmup_ms());
        let live = &mut self.live;
        for (ks, tick) in self.sweep.drain(..) {
            // Ключ только что найден в свипе, который построен обходом `live`
            // выше без единой вставки между, — отсутствие было бы дефектом
            // логики, а не данных. Паники при этом нет по режиму линтов:
            // в релизе дефект даст пропуск уровня (видимый), а в дебаге —
            // срабатывание ассёрта ниже.
            debug_assert!(live.contains_key(&(ks, tick)));
            let Some(lv) = live.remove(&(ks, tick)) else {
                continue;
            };
            if lv.birth_ms < warm_end {
                continue;
            }
            // Касание, оборванное смертью, идёт перед самой смертью: у
            // начавшегося в этом кадре «завал» — по свипу этого же кадра.
            if let Some(t) = lv.touch {
                let stack = if t.start_frame == frame {
                    stack
                } else {
                    t.stack
                };
                touches.push(touch_record((ks, tick), &lv, t, ts_ms, stack, true));
            }
            let kind = if lv.seen_frame == frame && !lv.seen_top50 {
                DeathKind::LeftTop
            } else {
                DeathKind::BelowFraction
            };
            let repriced = newborns.iter().any(|&(ns, nt, nsize)| {
                ns == ks
                    && nt.checked_sub(tick).is_some_and(|d| d == 1 || d == -1)
                    && comparable_size(nsize, lv.max)
            });
            out.push(LevelRecord {
                side: side_of((ks, tick)),
                price_tick: tick,
                birth_ms: lv.birth_ms,
                death_ms: ts_ms,
                lifetime_ms: ts_ms - lv.birth_ms,
                size_max: lv.max,
                time_to_max_ms: lv.max_ms - lv.birth_ms,
                size_monotonic: lv.first_decrease_ms.is_none_or(|t| t > lv.max_ms),
                repeat_count: lv.repeat,
                repriced,
                death: kind,
                traded_lots: lv.traded,
            });
        }

        // Касания выживших: начавшимся в кадре — «завал» по свипу, ушедшим с
        // лучшей цены — запись и следующий индекс. Ключ, которого в карте
        // уже нет, умер в свипе выше — его касание уже выдано со смертью.
        for key in self.touched.drain(..) {
            let Some(lv) = live.get_mut(&key) else {
                continue;
            };
            let Some(t) = &mut lv.touch else {
                continue;
            };
            if t.start_frame == frame {
                t.stack = stack;
            }
            if t.end_pending {
                let t = *t;
                if lv.birth_ms >= warm_end {
                    touches.push(touch_record(key, lv, t, ts_ms, t.stack, false));
                }
                lv.touch = None;
                lv.touch_index = lv.touch_index.saturating_add(1);
            }
        }
    }

    /// Один трейд ленты. Находит живой уровень той стороны, которую трейд ест
    /// на этом тике, и добавляет объём — иначе молча пропускает. Блочные,
    /// нулевые и поданные раньше рождения не считаются. Поиск в карте
    /// кучу не трогает, внутри — одно целое сложение с насыщением.
    pub fn observe_trade(&mut self, tr: TradeHit) {
        if tr.block || tr.lots <= 0 {
            return;
        }
        let key = (u8::from(tr.aggressor_is_buy), tr.tick);
        if let Some(lv) = self.live.get_mut(&key) {
            if tr.exch_ms < lv.birth_ms {
                return;
            }
            lv.traded = lv.traded.saturating_add(tr.lots);
        }
    }

    /// Прогрев, который реально действует на эмиссию: `floor` — всегда ноль
    /// (режим отладки, без прогрева по определению), `percentile` — как
    /// сконфигурировано. `cfg.warmup_ms` в режиме `floor` не читается.
    fn effective_warmup_ms(&self) -> i64 {
        match self.cfg.mode {
            H3Mode::Floor { .. } => 0,
            H3Mode::Percentile { .. } => self.cfg.warmup_ms,
        }
    }

    /// Сколько рождений уже было на этом ключе строго внутри окна, и запись
    /// текущего. Очередь чистится спереди: старые рождения выпадают сами.
    fn count_prior_births(&mut self, key: (u8, i64), ts_ms: i64, window_ms: i64) -> u32 {
        let q = self.births.entry(key).or_default();
        let cutoff = ts_ms - window_ms;
        while q.front().is_some_and(|&t| t <= cutoff) {
            q.pop_front();
        }
        let n = u32::try_from(q.len()).unwrap_or(u32::MAX);
        q.push_back(ts_ms);
        n
    }
}

#[cfg(test)]
mod tests;
