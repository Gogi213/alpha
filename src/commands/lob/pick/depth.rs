//! `lob pick` — измеренная глубина книги: модель значений и **проверка**
//! глубины (шаг 0.4, Decision 18). Чистые функции — медиана по уровням
//! снимка, время-взвешенное усреднение по стороне и вердикт `depth_check` —
//! не делают ввода-вывода; сетевой сбор самих замеров (подключение,
//! копление `DepthSample` за час) — `super::measure`.
//!
//! Таск 27 снял здесь отбор: глубина больше не решает состав пула
//! (BUSINESS-TASK §9 «час живой глубины остаётся как проверка, а не как
//! критерий отбора», §2 «первые десять оставшихся», §3 про `ZECUSDT`;
//! `SETTLED.md` В-35). Функция `survivors_above_depth_floor` и её ошибка
//! `PickError::NoSurvivorsAboveDepthFloor` удалены: состав пула — целиком
//! дело `super::pool`.

/// «>= $2000 в номинале на уровень» (Decision 18), в фиксированной точке 1e9
/// (`ARCHITECTURE.md` A1) — тот же масштаб, что доллары нигде не участвуют
/// в сравнении гейтов, но здесь именно доллар и есть измеряемая величина.
/// Decision 18(б), ревизия 10: порог проверяется на бид и на аск **раздельно**
/// — см. `depth_check` и doc `DepthSample`. С таска 27 это порог отчётной
/// метки, не отбора.
pub const DEPTH_FLOOR_USD_E9: i64 = 2_000 * 1_000_000_000;

/// Медиана целочисленной выборки. `None` на пустом входе — отсутствие
/// уровней означает «данных нет», а не «глубина ноль»: подстановка нуля
/// молча превратила бы «книга ещё не пришла» в «книга пуста», и кандидат
/// с сетевым сбоем выглядел бы как кандидат с нулевой ликвидностью.
///
/// Чётная длина усредняется через `a + (b - a) / 2`, а не `(a + b) / 2`:
/// глубины неотрицательны, поэтому `b >= a` после сортировки и `b - a` не
/// переполняет `i64` там, где `a + b` могло бы (оба значения могут быть
/// сколь угодно велики по отдельности, но их разность — нет).
///
/// Индексы ниже доказаны guard (`n ≥ 1`): нечёт — середина, чёт (`n ≥ 2`) —
/// два средних. Проверка через `get` здесь — мёртвый код ради линта.
#[allow(clippy::indexing_slicing)]
pub fn median_depth_per_level_usd_e9(level_depths_usd_e9: &[i64]) -> Option<i64> {
    if level_depths_usd_e9.is_empty() {
        return None;
    }
    let mut v = level_depths_usd_e9.to_vec();
    v.sort_unstable();
    let n = v.len();
    Some(if n % 2 == 1 {
        v[n / 2]
    } else {
        let a = v[n / 2 - 1];
        let b = v[n / 2];
        a + (b - a) / 2
    })
}

/// Один замер глубины книги: момент (наносекунды от эпохи Unix — тот же
/// `local_ts_ns`, что `bybit::conn` ставит до разбора, H12) и номинал
/// каждого уровня топ-50 в USD·1e9 на этот момент, **раздельно по стороне**.
///
/// Decision 18(б), ревизия 10, закрыла двусмысленность, которую первая
/// реализация читала иначе: «топ-50» — это пятьдесят уровней **одной**
/// стороны, а не сто пополам, и порог глубины обязан выполняться на бид и
/// на аск **независимо**. Причина — операционная, не эстетическая: отдыхать
/// ордером можно только на одной стороне книги, и объединённая медиана по
/// сотне уровней прячет систематически тонкую сторону за толстой ровно там,
/// где заявка и упёрлась бы в исполнение. Раньше здесь было одно поле
/// `level_notional_usd_e9`, собранное `Iterator::chain` из обеих сторон, —
/// то самое прочтение, которое ревизия 10 отвергла явно.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DepthSample {
    pub at_ns: i64,
    pub bid_notional_usd_e9: Vec<i64>,
    pub ask_notional_usd_e9: Vec<i64>,
}

/// Время-взвешенная медиана глубины **одной стороны** за окно
/// `[samples[0].at_ns, window_end_ns)` (Decision 18: «глубина считается
/// времени-взвешенно», Decision 18(б): раздельно по стороне).
///
/// **Порядок операций — Decision 18(в), ревизия 10, задаёт его явно и в этом
/// порядке:** номинал уровня (размер × цена) в каждом снимке — уже готов на
/// входе, `levels_of(s)` читает его из `DepthSample`, посчитанного раньше;
/// затем медиана **по уровням этого снимка** — `median_depth_per_level_usd_e9`
/// ниже, применённая к каждому снимку отдельно, даёт один скаляр на снимок;
/// и только затем — взвешивание этих скаляров по времени между снимками
/// (длительностью до следующего снимка, для последнего — до конца окна).
///
/// Более ранняя реализация (до ревизии 10, и её собственный doc-комментарий
/// здесь же аргументировал за неё на нескольких абзацах) делала обратное:
/// сперва взвешенное по времени среднее для каждой **позиции** уровня за
/// весь час, и лишь затем медиана по ~50 усреднённым числам. План не считал
/// этот порядок согласованным нигде до ревизии 10 — предыдущий комментарий
/// заявлял обратное, ссылаясь на скобку самого Decision 18 как на решённый
/// вопрос порядка, и это было ошибкой чтения, а не решением плана: скобка
/// говорит про медиану по уровням против суммы по ним (пункт (а)), а не про
/// то, в каком порядке эта медиана встречается со временем. Ревизия 10
/// впервые называет порядок словами и явно отвергает обратный: «номинал от
/// медианного размера считает деньги по цене, которой на тонкой стороне
/// может не быть» — здесь этот обратный порядок и недостижим по построению,
/// потому что на вход уже приходит номинал (размер, уже умноженный на цену
/// того же снимка), а не размер отдельно от цены.
///
/// Побочный эффект правильного порядка: снимкам разной длины (глубина книги
/// у края топ-50 дрожит) больше не нужно взаимное выравнивание позиций —
/// медиана каждого снимка берётся по его собственным уровням, и короткий
/// снимок не голосует «нулевым» уровнем за позицию, которой на бирже не
/// было. Снимок вовсе без уровней этой стороны (`median_depth_per_level_usd_e9`
/// возвращает `None` — см. её doc: пустой вход значит «данных нет», а не
/// «глубина ноль») пропускается целиком и не взвешивается: секундный пробел
/// на одной стороне внутри часа живого потока не должен обнулять весь замер.
///
/// Каст итога точен: частное — средневзвешенная медиана (~1e9–1e15 e9),
/// далеко от границ `i64`.
#[allow(clippy::cast_possible_truncation)]
fn time_weighted_median_for_side(
    samples: &[DepthSample],
    window_end_ns: i64,
    levels_of: impl Fn(&DepthSample) -> &[i64],
) -> Option<i64> {
    let mut weighted_sum: i128 = 0;
    let mut total_weight_ns: i128 = 0;
    for (i, s) in samples.iter().enumerate() {
        let Some(snapshot_median) = median_depth_per_level_usd_e9(levels_of(s)) else {
            continue;
        };
        let next_at = samples.get(i + 1).map_or(window_end_ns, |next| next.at_ns);
        let dt = (next_at - s.at_ns).max(0) as i128;
        total_weight_ns += dt;
        weighted_sum += snapshot_median as i128 * dt;
    }
    if total_weight_ns == 0 {
        return None;
    }
    Some((weighted_sum / total_weight_ns) as i64)
}

/// Время-взвешенная медианная глубина стороны **бид** — см.
/// `time_weighted_median_for_side` про порядок операций.
pub fn time_weighted_median_bid_depth_usd_e9(
    samples: &[DepthSample],
    window_end_ns: i64,
) -> Option<i64> {
    time_weighted_median_for_side(samples, window_end_ns, |s| s.bid_notional_usd_e9.as_slice())
}

/// Время-взвешенная медианная глубина стороны **аск** — см.
/// `time_weighted_median_for_side` про порядок операций.
pub fn time_weighted_median_ask_depth_usd_e9(
    samples: &[DepthSample],
    window_end_ns: i64,
) -> Option<i64> {
    time_weighted_median_for_side(samples, window_end_ns, |s| s.ask_notional_usd_e9.as_slice())
}

/// Кандидат с готовым измерением — вход проверки глубины и строки
/// коммитимой таблицы (Decision 18, done-condition шага 0.4: «окно замера» —
/// колонка таблицы).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MeasuredCandidate {
    pub symbol: String,
    pub window_start_utc_ms: i64,
    pub window_secs: i64,
    /// Число событий книги за окно — тот же счётчик, что определяет оба
    /// поля глубины ниже через `time_weighted_median_{bid,ask}_depth_usd_e9`
    /// (там это `samples.len()`): каждый принятый апдейт книги — одно
    /// событие и один замер разом, отдельного счётчика заводить незачем.
    pub events: i64,
    /// Decision 18(б), ревизия 10: «топ-50» — пятьдесят уровней одной
    /// стороны, порог и ранжирование читают обе стороны раздельно, не одно
    /// объединённое число (см. doc `DepthSample`).
    pub median_bid_depth_usd_e9: i64,
    pub median_ask_depth_usd_e9: i64,
    /// Только для печати в таблице (Decision 18: «отчётный оборот никогда не
    /// критерий») — `depth_check` этого поля не читает вовсе.
    pub reported_turnover_usd_e9: i64,
    /// Медиана размера сделки в лотах за то же окно (план D-H3, таск 08) —
    /// вход `super::h3::h3_lots_floor`. `None` — окно не поймало ни одной
    /// неблочной сделки: не порог отбора, `depth_check` это поле не читает.
    pub median_trade_lots: Option<i64>,
}

impl MeasuredCandidate {
    /// Худшая из двух сторон — связывающая величина и для порога, и для
    /// ранжирования: отдыхать ордером можно только на одной стороне, и
    /// именно она определяет, исполнится ли заявка, а не более толстая
    /// соседняя (см. doc `DepthSample`, Decision 18(б)).
    pub fn min_side_depth_usd_e9(&self) -> i64 {
        self.median_bid_depth_usd_e9
            .min(self.median_ask_depth_usd_e9)
    }
}

/// Вердикт часового замера глубины по одному инструменту пула — **строка
/// отчёта, не критерий отбора** (BUSINESS-TASK §9: «час живой глубины
/// остаётся как проверка, а не как критерий отбора»; §2: пул — «первые
/// десять оставшихся» после трёх исключений по правилам; §3: `ZECUSDT` «не
/// исключается … и это печатается строкой отчёта»). До таска 27 порог
/// `DEPTH_FLOOR_USD_E9` резал пул до восьми — аудит 2026-09-12 назвал это
/// единственным прямым противоречием задаче; решение — `SETTLED.md` В-35.
///
/// Три состояния, и `NotMeasured` не сливается с `BelowFloor`: символ, по
/// которому окно не собрало ни одного замера стороны (оборванное
/// соединение, мёртвая лента), и символ с тонкой книгой — разные факты, и
/// в отчёте они обязаны различаться.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DepthCheck {
    /// Медианная глубина не ниже `DEPTH_FLOOR_USD_E9` на **обеих** сторонах.
    Ok,
    /// Замер есть, но хотя бы одна сторона ниже порога (Decision 18б:
    /// толстая сторона не засчитывается за тонкую).
    BelowFloor,
    /// Замера нет — `measure_prefiltered` не вернула кандидата вовсе.
    NotMeasured,
}

impl DepthCheck {
    /// Значение колонки `depth_check` в `instruments.csv` и `candidates.csv`.
    pub fn as_str(self) -> &'static str {
        match self {
            DepthCheck::Ok => "ok",
            DepthCheck::BelowFloor => "below_floor",
            DepthCheck::NotMeasured => "not_measured",
        }
    }

    /// Требует ли метка строки предупреждения в stdout (`lob pick`) —
    /// «инструмент остаётся в пуле, глубина ниже порога».
    pub fn is_warning(self) -> bool {
        !matches!(self, DepthCheck::Ok)
    }
}

/// Проверка глубины по инструменту пула: `None` — замера нет вовсе.
/// Возвращает метку, не решение: ни один вызывающий не имеет права
/// выбрасывать кандидата по её значению (см. doc `DepthCheck`).
pub fn depth_check(measured: Option<&MeasuredCandidate>) -> DepthCheck {
    match measured {
        None => DepthCheck::NotMeasured,
        Some(m) if m.min_side_depth_usd_e9() >= DEPTH_FLOOR_USD_E9 => DepthCheck::Ok,
        Some(_) => DepthCheck::BelowFloor,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn e9(dollars: i64) -> i64 {
        dollars * 1_000_000_000
    }

    // -- median_depth_per_level_usd_e9 --------------------------------------

    #[test]
    fn median_of_odd_length_is_the_middle_value() {
        let levels = vec![e9(10), e9(30), e9(20)];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(20)));
    }

    #[test]
    fn median_of_even_length_averages_the_two_middle_values() {
        let levels = vec![e9(10), e9(20), e9(30), e9(40)];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(25)));
    }

    /// Все уровни одной глубины — книга без формы, вырожденный, но реальный
    /// случай (например, все 50 уровней у минимального лота). Медиана обязана
    /// вернуть ровно эту глубину, не среднее с искажением от `a + (b-a)/2`.
    #[test]
    fn median_of_all_equal_values_returns_that_value() {
        let levels = vec![e9(7); 50];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(e9(7)));
    }

    #[test]
    fn median_of_empty_input_is_none_not_a_panic() {
        assert_eq!(median_depth_per_level_usd_e9(&[]), None);
    }

    #[test]
    fn median_of_a_single_level_is_that_level() {
        assert_eq!(median_depth_per_level_usd_e9(&[e9(42)]), Some(e9(42)));
    }

    /// Требуемый тест: медиана и сумма расходятся, и порог обязан применяться
    /// к медиане. Один толстый уровень ($50000) плюс 49 тонких ($10 каждый):
    /// сумма перескакивает порог $2000 с большим запасом, а медиана — нет,
    /// потому что реальная ликвидность у 49 из 50 уровней ничтожна.
    #[test]
    fn median_disagrees_with_sum_and_the_floor_must_use_the_median() {
        let mut levels = vec![e9(10); 49];
        levels.push(e9(50_000));
        let sum: i64 = levels.iter().sum();
        let median = median_depth_per_level_usd_e9(&levels).unwrap();

        assert_eq!(sum, e9(10) * 49 + e9(50_000));
        assert!(sum >= DEPTH_FLOOR_USD_E9, "сумма прошла бы порог");
        assert_eq!(
            median,
            e9(10),
            "медиана — типичный, а не выдающийся уровень"
        );
        assert!(
            median < DEPTH_FLOOR_USD_E9,
            "медиана обязана провалить порог"
        );
    }

    /// Крупные значения — без переполнения при усреднении двух средних:
    /// `a + (b - a) / 2`, а не `(a + b) / 2`.
    #[test]
    fn median_does_not_overflow_on_the_largest_i64_values() {
        let levels = vec![i64::MAX - 2, i64::MAX];
        assert_eq!(median_depth_per_level_usd_e9(&levels), Some(i64::MAX - 1));
        let levels_eq = vec![i64::MAX, i64::MAX];
        assert_eq!(median_depth_per_level_usd_e9(&levels_eq), Some(i64::MAX));
    }

    // -- time_weighted_median_{bid,ask}_depth_usd_e9 -------------------------

    #[test]
    fn time_weighted_median_weighs_by_duration_to_next_sample() {
        // Один уровень: глубина 10 держится 1с, потом 30 держится 3с — среднее
        // взвешенное (10*1 + 30*3)/4 = 25, не простое среднее (10+30)/2=20.
        // Один уровень на снимок — медиана снимка равна ему самому, порядок
        // операций (Изменение 2) здесь ничего не меняет.
        let samples = vec![
            DepthSample {
                at_ns: 0,
                bid_notional_usd_e9: vec![e9(10)],
                ask_notional_usd_e9: vec![],
            },
            DepthSample {
                at_ns: 1_000_000_000,
                bid_notional_usd_e9: vec![e9(30)],
                ask_notional_usd_e9: vec![],
            },
        ];
        let window_end_ns = 4_000_000_000;
        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(e9(25))
        );
    }

    #[test]
    fn time_weighted_median_of_empty_samples_is_none_not_a_panic() {
        assert_eq!(time_weighted_median_bid_depth_usd_e9(&[], 1_000), None);
        assert_eq!(time_weighted_median_ask_depth_usd_e9(&[], 1_000), None);
    }

    /// Требуемый тест (Изменение 1): толстый бид не должен просочиться в
    /// медиану аска через общее хранилище — раньше `DepthSample` нёс обе
    /// стороны в одном `Vec` (`level_notional_usd_e9`), собранном
    /// `Iterator::chain`, и наблюдение с сотней толстых бидов подняло бы
    /// объединённую медиану выше порога, даже если аск тонок или пуст.
    #[test]
    fn time_weighted_median_does_not_pool_the_two_sides() {
        let samples = vec![DepthSample {
            at_ns: 0,
            bid_notional_usd_e9: vec![DEPTH_FLOOR_USD_E9 * 100; 50], // толстый бид
            ask_notional_usd_e9: vec![e9(1); 50],                    // тонкий аск
        }];
        let window_end_ns = 1_000_000_000;
        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(DEPTH_FLOOR_USD_E9 * 100)
        );
        assert_eq!(
            time_weighted_median_ask_depth_usd_e9(&samples, window_end_ns),
            Some(e9(1)),
            "медиана аска обязана считаться по уровням аска, не смешиваться с бидом"
        );
    }

    /// Требуемый тест (Изменение 2, Decision 18в ревизии 10): порядок
    /// операций — сначала медиана по уровням КАЖДОГО снимка, затем
    /// взвешивание этих скаляров по времени; не наоборот. Числа подобраны
    /// так, что два порядка дают разный ответ на одних и тех же данных: A
    /// (3 уровня, короче) и B (2 уровня) получают равный вес по времени
    /// (1с каждый, window_end = 2с).
    ///
    /// Правильный порядок: median(A=[10,20,30]) = 20, median(B=[1000,2000])
    /// = 1000 + (2000-1000)/2 = 1500; взвешенное среднее по равным весам —
    /// (20 + 1500) / 2 = 760.
    ///
    /// Обратный порядок (сначала взвесить по времени каждую ПОЗИЦИЮ уровня
    /// за оба снимка — с недостающей позицией B[2], учтённой как 0, — и
    /// только потом взять медиану по позициям) даёт другое число: позиции
    /// [505, 1010, 15], медиана 505. Это и есть старое поведение, которое
    /// ревизия 10 отвергла — `assert_ne!` ниже утверждает, что реализация
    /// не должна давать этот ответ.
    #[test]
    fn time_weighted_median_computes_per_snapshot_median_before_time_weighting() {
        let samples = vec![
            DepthSample {
                at_ns: 0,
                bid_notional_usd_e9: vec![e9(10), e9(20), e9(30)],
                ask_notional_usd_e9: vec![],
            },
            DepthSample {
                at_ns: 1_000_000_000,
                bid_notional_usd_e9: vec![e9(1000), e9(2000)],
                ask_notional_usd_e9: vec![],
            },
        ];
        let window_end_ns = 2_000_000_000;

        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(e9(760)),
            "медиана каждого снимка обязана считаться первой, до взвешивания по времени"
        );
        assert_ne!(
            time_weighted_median_bid_depth_usd_e9(&samples, window_end_ns),
            Some(e9(505)),
            "505 — ответ обратного (отвергнутого ревизией 10) порядка операций"
        );
    }

    /// Раньше короткий снимок дополнялся нулём на недостающих ПОЗИЦИЯХ
    /// уровня, потому что порядок операций был обратным (см. предыдущий
    /// тест). В правильном порядке (Decision 18в) у каждого снимка — своя
    /// медиана по своим же уровням, дополнять нечем: снимок короче на один
    /// уровень просто даёт медиану по тому, что в нём есть, не паникует и
    /// не голосует «нулевым» уровнем, которого на бирже не было.
    #[test]
    fn time_weighted_median_computes_each_snapshot_independently_without_padding() {
        let samples = vec![
            DepthSample {
                at_ns: 0,
                bid_notional_usd_e9: vec![e9(10), e9(20)],
                ask_notional_usd_e9: vec![],
            },
            DepthSample {
                at_ns: 1,
                bid_notional_usd_e9: vec![e9(25)], // короче на один уровень
                ask_notional_usd_e9: vec![],
            },
        ];
        // median(A=[10,20]) = 10 + (20-10)/2 = 15; median(B=[25]) = 25.
        // Равные веса (window_end=2, dtA=dtB=1): (15+25)/2 = 20.
        assert_eq!(
            time_weighted_median_bid_depth_usd_e9(&samples, 2),
            Some(e9(20))
        );
    }

    // -- depth_check и его вход (таск 27) ------------------------------------

    fn measured_sided(
        symbol: &str,
        bid_depth: i64,
        ask_depth: i64,
        turnover: i64,
        events: i64,
        window_secs: i64,
    ) -> MeasuredCandidate {
        MeasuredCandidate {
            symbol: symbol.to_string(),
            window_start_utc_ms: 0,
            window_secs,
            events,
            median_bid_depth_usd_e9: bid_depth,
            median_ask_depth_usd_e9: ask_depth,
            reported_turnover_usd_e9: turnover,
            median_trade_lots: None,
        }
    }

    // -- depth_check (таск 27, §9: проверка, а не критерий отбора) -----------

    /// Критерий приёмки таска 27 (BUSINESS-TASK §9: «час живой глубины
    /// остаётся как проверка, а не как критерий отбора»): глубина ниже
    /// порога — метка отчёта, и у неё ровно три состояния. Замер есть и
    /// обе стороны не ниже порога — `ok`; замер есть, любая сторона ниже —
    /// `below_floor` (толстая сторона не прикрывает тонкую, Decision 18б);
    /// замера нет вовсе — `not_measured`, а не `below_floor`: оборвавшееся
    /// соединение и тонкая книга — разные факты, и слитые в один они
    /// сделали бы отчёт неотличимым от прежнего отсева.
    #[test]
    fn depth_check_labels_the_three_states_separately() {
        let at_floor = measured_sided(
            "AT_FLOOR",
            DEPTH_FLOOR_USD_E9,
            DEPTH_FLOOR_USD_E9,
            0,
            1,
            3600,
        );
        let thin_ask = measured_sided(
            "THIN_ASK",
            DEPTH_FLOOR_USD_E9 * 10,
            DEPTH_FLOOR_USD_E9 - 1,
            0,
            1,
            3600,
        );
        let thin_bid = measured_sided(
            "THIN_BID",
            DEPTH_FLOOR_USD_E9 - 1,
            DEPTH_FLOOR_USD_E9 * 10,
            0,
            1,
            3600,
        );

        assert_eq!(depth_check(Some(&at_floor)), DepthCheck::Ok);
        assert_eq!(depth_check(Some(&thin_ask)), DepthCheck::BelowFloor);
        assert_eq!(depth_check(Some(&thin_bid)), DepthCheck::BelowFloor);
        assert_eq!(depth_check(None), DepthCheck::NotMeasured);

        // Метки — то, что уезжает колонкой `depth_check` в оба CSV.
        assert_eq!(DepthCheck::Ok.as_str(), "ok");
        assert_eq!(DepthCheck::BelowFloor.as_str(), "below_floor");
        assert_eq!(DepthCheck::NotMeasured.as_str(), "not_measured");
    }
}
