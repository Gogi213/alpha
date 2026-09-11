//! `lob pick` — измеренная глубина книги: модель значений и порог отбора
//! (шаг 0.4, Decision 18). Чистые функции — медиана по уровням снимка,
//! время-взвешенное усреднение по стороне, ранжирование и порог глубины
//! (`survivors_above_depth_floor`) — не делают ввода-вывода; сетевой сбор
//! самих замеров (подключение, копление `DepthSample` за час) — `super::measure`.

/// «>= $2000 в номинале на уровень» (Decision 18), в фиксированной точке 1e9
/// (`ARCHITECTURE.md` A1) — тот же масштаб, что доллары нигде не участвуют
/// в сравнении гейтов, но здесь именно доллар и есть измеряемая величина.
/// Decision 18(б), ревизия 10: порог проверяется на бид и на аск **раздельно**
/// — см. `survivors_above_depth_floor` и doc `DepthSample`.
pub const DEPTH_FLOOR_USD_E9: i64 = 2_000 * 1_000_000_000;

// ---------------------------------------------------------------------------
// Ошибки
// ---------------------------------------------------------------------------

/// Отказ чистой части правила Decision 25. Ни один вариант не паникует —
/// это и есть требование задачи: вырожденный вход обязан вернуть ошибку с
/// причиной, а не молча посчитать что-то похожее на ответ (тот самый дефект
/// bootstrap-модуля из `stats/mod.rs`, деливший на нулевую дисперсию).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PickError {
    /// Ни один измеренный кандидат не прошёл порог глубины **на обеих
    /// сторонах** (протокол шага 0.4, порог Decision 18 сохранён ревизией 17)
    /// — толстая сторона не засчитывается за тонкую.
    ///
    /// Других вариантов отказа у чистой части нет нарочно: прежняя
    /// `MinNotionalNotSatisfied` (Decision 22, «минимальный лот обязан покрыть
    /// `minNotionalValue`, иначе ошибка») отменена ревизией 17б — на восьми из
    /// десяти инструментов пула минимальный лот дешевле $5, и это свойство
    /// пула, а не брак отбора. Вместо отказа считается размер-22а
    /// (`order_size_22a`) на инструмент, а разброс номинала идёт колонкой
    /// таблицы, не условием приёмки.
    NoSurvivorsAboveDepthFloor {
        floor_usd_e9: i64,
        candidates: usize,
    },
}

impl std::fmt::Display for PickError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PickError::NoSurvivorsAboveDepthFloor {
                floor_usd_e9,
                candidates,
            } => {
                // Деньги e9 (~1e12) точны в f64; это печать, не арифметика.
                #[allow(clippy::cast_precision_loss)]
                let floor_usd = *floor_usd_e9 as f64 / 1e9;
                write!(
                    f,
                    "ни один из {candidates} измеренных кандидатов не набрал {floor_usd} USD медианной глубины на уровень на обеих сторонах",
                )
            }
        }
    }
}

impl std::error::Error for PickError {}

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

/// Кандидат с готовым измерением — вход последней стадии правила. Всё, что
/// нужно для ранжирования и для строки коммитимой таблицы (Decision 18,
/// done-condition шага 0.4: «окно замера» — колонка таблицы).
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
    /// критерий») — `survivors_above_depth_floor` этого поля не читает вовсе.
    pub reported_turnover_usd_e9: i64,
    /// Медиана размера сделки в лотах за то же окно (план D-H3, таск 08) —
    /// вход `super::h3::h3_lots_floor`. `None` — окно не поймало ни одной
    /// неблочной сделки: не порог отбора, `survivors_above_depth_floor` это
    /// поле не читает.
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

/// Сравнение по темпу событий без деления и без `f64`: `events / window_secs`
/// у `a` против того же у `b` — это `a.events * b.window_secs` против
/// `b.events * a.window_secs` (оба `window_secs > 0` по построению замера).
/// `i128`: `i64::MAX * i64::MAX ≈ 8.5·10^37` меньше `i128::MAX ≈ 1.7·10^38` —
/// произведение не переполняется на всём диапазоне `i64`, значит и на любых
/// реалистичных счётчиках событий и длительностях подавно.
fn event_rate_cmp(a: &MeasuredCandidate, b: &MeasuredCandidate) -> std::cmp::Ordering {
    (a.events as i128 * b.window_secs as i128).cmp(&(b.events as i128 * a.window_secs as i128))
}

/// Финальная стадия протокола шага 0.4 (порог Decision 18, сохранён
/// ревизией 17): порог глубины обязан пройти на **обеих**
/// сторонах независимо (18(б)) — не сумма и не среднее двух; ранг по
/// худшей из двух сторон (`min_side_depth_usd_e9`, та же логика, что и
/// сам порог: толстая сторона не должна прятать тонкую ни в гейте, ни в
/// ранжировании), при равенстве — по темпу событий, при равенстве и там —
/// по символу (детерминизм, не смысл). Отчётный оборот здесь не участвует
/// вовсе, поэтому не может продвинуть кандидата ниже порога. Пул не сужается
/// до фиксированного числа финалистов: возвращаются все прошедшие порог,
/// ранжированные, — заморозка состава пула решена спекой редакции 3.
pub fn survivors_above_depth_floor(
    measured: &[MeasuredCandidate],
) -> Result<Vec<MeasuredCandidate>, PickError> {
    let mut survivors: Vec<&MeasuredCandidate> = measured
        .iter()
        .filter(|m| {
            m.median_bid_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
                && m.median_ask_depth_usd_e9 >= DEPTH_FLOOR_USD_E9
        })
        .collect();
    if survivors.is_empty() {
        return Err(PickError::NoSurvivorsAboveDepthFloor {
            floor_usd_e9: DEPTH_FLOOR_USD_E9,
            candidates: measured.len(),
        });
    }
    survivors.sort_by(|a, b| {
        b.min_side_depth_usd_e9()
            .cmp(&a.min_side_depth_usd_e9())
            .then_with(|| event_rate_cmp(b, a))
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    Ok(survivors.into_iter().cloned().collect())
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

    // -- survivors_above_depth_floor -----------------------------------------------------

    /// Оба борта равны `depth` — совместимо со старым однозначным чтением
    /// большинства тестов ниже, которые не проверяют асимметрию сторон.
    /// Асимметричные сценарии используют `measured_sided` напрямую.
    fn measured(
        symbol: &str,
        depth: i64,
        turnover: i64,
        events: i64,
        window_secs: i64,
    ) -> MeasuredCandidate {
        measured_sided(symbol, depth, depth, turnover, events, window_secs)
    }

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

    /// Требуемый тест (Изменение 1, Decision 18б ревизии 10): порог обязан
    /// выполняться на КАЖДОЙ стороне отдельно — толстый бид не может
    /// прикрыть тонкий аск. Бид далеко выше порога, аск далеко ниже — со
    /// старым (объединённым по обеим сторонам) прочтением такой кандидат
    /// мог пройти отбор, потому что усреднённая по сотне уровней глубина
    /// оставалась бы выше порога даже с пустым или крайне тонким аском.
    #[test]
    fn survivors_above_depth_floor_requires_the_depth_floor_on_both_sides_independently() {
        let m = vec![measured_sided(
            "FAT_BID_THIN_ASK",
            DEPTH_FLOOR_USD_E9 * 10,
            DEPTH_FLOOR_USD_E9 / 10,
            0,
            1,
            3600,
        )];
        assert_eq!(
            survivors_above_depth_floor(&m).unwrap_err(),
            PickError::NoSurvivorsAboveDepthFloor {
                floor_usd_e9: DEPTH_FLOOR_USD_E9,
                candidates: 1
            }
        );
    }

    #[test]
    fn survivors_above_depth_floor_excludes_candidates_below_the_depth_floor() {
        let m = vec![
            measured("HI", DEPTH_FLOOR_USD_E9 + 1, 0, 10, 3600),
            measured("LO", DEPTH_FLOOR_USD_E9 - 1, 0, 10, 3600),
        ];
        let out = survivors_above_depth_floor(&m).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].symbol, "HI");
    }

    #[test]
    fn survivors_above_depth_floor_errors_when_all_candidates_are_below_the_floor() {
        let m = vec![measured("A", DEPTH_FLOOR_USD_E9 - 1, 0, 1, 3600)];
        assert_eq!(
            survivors_above_depth_floor(&m).unwrap_err(),
            PickError::NoSurvivorsAboveDepthFloor {
                floor_usd_e9: DEPTH_FLOOR_USD_E9,
                candidates: 1
            }
        );
    }

    #[test]
    fn survivors_above_depth_floor_on_empty_input_is_an_error_not_a_panic() {
        assert!(survivors_above_depth_floor(&[]).is_err());
    }

    /// Требуемый тест: отчётный оборот не может продвинуть кандидата ниже
    /// порога глубины — победитель определяется исключительно измеренной
    /// глубиной среди тех, кто прошёл порог.
    #[test]
    fn reported_turnover_cannot_promote_a_candidate_below_the_depth_floor() {
        let m = vec![
            measured(
                "HUGE_TURNOVER_THIN_BOOK",
                DEPTH_FLOOR_USD_E9 - 1,
                e9(999_999_999),
                1000,
                3600,
            ),
            measured(
                "SMALL_TURNOVER_DEEP_BOOK",
                DEPTH_FLOOR_USD_E9 + 1,
                e9(1),
                1,
                3600,
            ),
        ];
        let out = survivors_above_depth_floor(&m).unwrap();
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].symbol, "SMALL_TURNOVER_DEEP_BOOK");
    }

    #[test]
    fn survivors_above_depth_floor_ranks_survivors_by_measured_depth_descending() {
        let m = vec![
            measured("LOW", DEPTH_FLOOR_USD_E9 + 100, 0, 1, 3600),
            measured("HIGH", DEPTH_FLOOR_USD_E9 + 999, 0, 1, 3600),
            measured("MID", DEPTH_FLOOR_USD_E9 + 500, 0, 1, 3600),
        ];
        let out = survivors_above_depth_floor(&m).unwrap();
        assert_eq!(
            out.iter().map(|c| c.symbol.clone()).collect::<Vec<_>>(),
            vec!["HIGH", "MID", "LOW"],
            "все прошедшие порог возвращаются, без сужения до фиксированного числа"
        );
    }

    /// Требуемый тест: равная измеренная глубина — победитель по темпу
    /// событий, а не по порядку появления во входном срезе.
    #[test]
    fn ties_in_depth_break_by_event_rate_deterministically() {
        let depth = DEPTH_FLOOR_USD_E9 + 1;
        let slow = measured("SLOW", depth, 0, 10, 3600); // 10 событий/час
        let fast = measured("FAST", depth, 0, 100, 3600); // 100 событий/час
        let forward = survivors_above_depth_floor(&[slow.clone(), fast.clone()]).unwrap();
        let backward = survivors_above_depth_floor(&[fast, slow]).unwrap();
        assert_eq!(forward[0].symbol, "FAST");
        assert_eq!(
            forward.iter().map(|c| c.symbol.clone()).collect::<Vec<_>>(),
            backward
                .iter()
                .map(|c| c.symbol.clone())
                .collect::<Vec<_>>(),
            "порядок входа не должен влиять на результат"
        );
    }

    #[test]
    fn ties_in_depth_and_event_rate_break_by_symbol() {
        let depth = DEPTH_FLOOR_USD_E9 + 1;
        let m = vec![
            measured("Z", depth, 0, 10, 3600),
            measured("A", depth, 0, 10, 3600),
        ];
        let out = survivors_above_depth_floor(&m).unwrap();
        assert_eq!(out[0].symbol, "A");
    }

    #[test]
    fn event_rate_comparison_does_not_overflow_on_extreme_counters() {
        let depth = DEPTH_FLOOR_USD_E9 + 1;
        let extreme = measured("EXTREME", depth, 0, i64::MAX, 1);
        let normal = measured("NORMAL", depth, 0, 1, 1);
        let out = survivors_above_depth_floor(&[extreme, normal]).unwrap();
        assert_eq!(
            out[0].symbol, "EXTREME",
            "не должно паниковать и обязано ранжировать верно"
        );
    }

    #[test]
    fn survivors_above_depth_floor_returns_one_when_only_one_candidate_survives() {
        let m = vec![measured("ONLY", DEPTH_FLOOR_USD_E9 + 1, 0, 1, 3600)];
        assert_eq!(survivors_above_depth_floor(&m).unwrap().len(), 1);
    }

    /// Таск 01: сужение пула до фиксированного числа финалистов удалено —
    /// прежняя функция переименована в `survivors_above_depth_floor` и
    /// больше не режет вывод. Пять кандидатов, все выше порога, обязаны
    /// вернуться все пять, а не два.
    #[test]
    fn survivors_above_depth_floor_does_not_cap_the_pool_at_two() {
        let m: Vec<MeasuredCandidate> = (0..5)
            .map(|i| measured(&format!("SYM{i}"), DEPTH_FLOOR_USD_E9 + 1 + i, 0, 1, 3600))
            .collect();
        assert_eq!(survivors_above_depth_floor(&m).unwrap().len(), 5);
    }
}
