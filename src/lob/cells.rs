//! Совместный кластерный бутстрап двух рядов (план §5.2, R08/R50).
//!
//! Гейт G2 (ячейки исхода-и-истории, предикаты принадлежности к ним,
//! `run_cells`, `decide_g2`) снят таском 17: он был построен под ячеечную
//! модель §5.2, которую спека отменила (`interfaces.md`, «Что построено под
//! отменённый дизайн»), и ни один вызывающий не читал его результат — ни
//! `lob backtest` (вердикт G4, `backtest::G4Verdict`/`decide_g4`), ни
//! `lob shortlist`. Из него сохранён
//! ровно один шов, который спека называет переиспользуемым, а не заново
//! пишет: общий кластерный бутстрап двух рядов на одних и тех же весах
//! Уэбба за одну реплику — «совместный ресэмплинг», не два независимых.
//!
//! Расширяется под разные статистики через `combine`:
//! - `disjoint_contrast` — разность двух взвешенных средних (вычитание).
//!   Публичная функция без текущего вызывающего внутри крейта: оставлена
//!   как есть по прямому указанию `interfaces.md` («Из отменённого
//!   сохраняется одно: `cells::disjoint_contrast`») для будущего контраста
//!   двух серий на общих кластерных репликах — расширять, а не писать
//!   второй бутстрап рядом.
//! - `joint_product_interval` (таск 03, R08/R50) — произведение двух
//!   средних (умножение); единственный текущий вызывающий —
//!   `costs::net_fill_interval` (совместный интервал `net_fill`).
//!
//! `day_sums` в обеих — по суткам `(числитель ряда A, знаменатель ряда A,
//! числитель ряда B, знаменатель ряда B)` в фиксированном порядке: одна и
//! та же реплика одним и тем же весом Уэбба взвешивает обе группы за те
//! же сутки. Знаменатели не переразвешиваются репликой — тот же приём,
//! что у `cluster_robust_t_from_summary` в `stats`.
//!
//! Источник весов — общий `crate::stats::SplitMix64`, а не своя копия
//! (`webb_points_match_stats_bit_for_bit` держит побитовое совпадение с
//! `stats::webb_weight`).

use crate::stats::{count_f64_u64, SplitMix64};

// ---------------------------------------------------------------------------
// Константы. Seed заморожен, как всё предрегистрированное (A2): один и тот
// же вход даёт один и тот же отчёт.
// ---------------------------------------------------------------------------

/// Seed по умолчанию для тестов этого модуля.
pub const CELLS_SEED: u64 = 2026_0502;

/// Нижний квантиль двустороннего интервала контраста: 0.25%.
pub const CONTRAST_CI_LOW_Q: f64 = 0.0025;

/// Верхний квантиль двустороннего интервала контраста: 99.75%.
/// Вместе с нижним даёт двусторонний интервал уровня 99.5%.
pub const CONTRAST_CI_HIGH_Q: f64 = 0.9975;

// ---------------------------------------------------------------------------
// Детерминированный источник весов Уэбба для интервала контраста.
// ---------------------------------------------------------------------------

/// Шесть точек распределения Уэбба (Decision 9): `-√1.5, -1, -√0.5, √0.5, 1,
/// √1.5`. Те же значения, что использует `stats`: интервал контраста обязан
/// идти по тем же кластерным репликам, что и другие бутстрапы, поэтому
/// источник реплик — общий `crate::stats::SplitMix64` (вторая копия удалена:
/// детектор копипасты её не видел, потому что обёртки назывались по-разному).
/// Таблица точек при этом своя, `WEBB_POINTS`: `stats` отдаёт только функцию
/// `webb_weight`, а порядок индексации задаёт вызывающий. Побитовое совпадение
/// потоков держит тест `webb_points_match_stats_bit_for_bit`.
const WEBB_POINTS: [f64; 6] = [
    -1.224_744_871_391_589,
    -1.0,
    -std::f64::consts::FRAC_1_SQRT_2,
    std::f64::consts::FRAC_1_SQRT_2,
    1.0,
    1.224_744_871_391_589,
];

/// Очередной вес Уэбба из общего генератора. Порядок `next_u64 % 6` — тот же,
/// что `SplitMix64::next_webb_weight` в `stats`, поэтому при том же seed поток
/// совпадает пореплико (A2). Индекс доказуемо < 6 по построению остатка
/// на любом указателе.
#[allow(clippy::indexing_slicing, clippy::cast_possible_truncation)]
fn next_webb(rng: &mut SplitMix64) -> f64 {
    WEBB_POINTS[(rng.next_u64() % WEBB_POINTS.len() as u64) as usize]
}

// ---------------------------------------------------------------------------
// Дизъюнктный контраст: разность двух взвешенных средних.
// ---------------------------------------------------------------------------

/// Разность двух рядов на общих кластерных репликах Уэбба: точечная оценка —
/// разность пуловых средних `mean_a − mean_b`; интервал — процентили wild
/// cluster bootstrap той же разности.
#[derive(Debug, Clone, PartialEq)]
pub struct DisjointContrast {
    /// Знаменатель ряда A.
    pub n_a: u64,
    /// Знаменатель ряда B.
    pub n_b: u64,
    /// Среднее ряда A на наблюдённых (невзвешенных) суммах.
    pub mean_a: f64,
    /// Среднее ряда B на наблюдённых (невзвешенных) суммах.
    pub mean_b: f64,
    /// Разность `mean_a − mean_b`.
    pub diff: f64,
    /// Нижняя граница интервала (квантиль `CONTRAST_CI_LOW_Q`).
    pub ci_low: f64,
    /// Верхняя граница интервала (квантиль `CONTRAST_CI_HIGH_Q`).
    pub ci_high: f64,
    /// Число реплик интервала.
    pub replications: u32,
    /// Seed реплик.
    pub seed: u64,
}

impl DisjointContrast {
    /// Строка контраста: разность с двусторонним интервалом.
    pub fn format_headline(&self) -> String {
        format!(
            "contrast(A-B): diff={:.4} CI=[{:.4},{:.4}] (99.5%, B={} seed={}) n_a={} n_b={}",
            self.diff, self.ci_low, self.ci_high, self.replications, self.seed, self.n_a, self.n_b
        )
    }
}

/// Квантиль уровня `q` на отсортированном по возрастанию срезе, линейная
/// интерполяция между соседями. Вызывающий гарантирует непустоту и `q`
/// внутри [0, 1] (проверено `debug_assert`); индексы `lo`/`hi` лежат в
/// `0..len` по построению `pos`, `get` здесь — мёртвый код ради линта.
/// Касты точные: `len` — длина среза в памяти (< 2^53 всегда, иначе его негде
/// держать), `pos` — в `[0, len)`, усечение к `usize` отбрасывает только дробь.
#[allow(
    clippy::indexing_slicing,
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation
)]
fn quantile_sorted(sorted: &[f64], q: f64) -> f64 {
    debug_assert!(!sorted.is_empty(), "квантиль пустого распределения");
    debug_assert!((0.0..=1.0).contains(&q), "уровень квантиля вне [0,1]");
    let pos = q * (sorted.len() - 1) as f64;
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    if lo == hi {
        sorted[lo]
    } else {
        sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo as f64)
    }
}

/// Общий шов совместного кластерного бутстрапа двух рядов, расширяемый под
/// разные статистики: дизъюнктный контраст соединяет два взвешенных
/// средних вычитанием, совместный интервал `net_fill` (`joint_product_interval`,
/// таск 03, R08/R50) — умножением. Реплика одна на обе серии: один и тот же
/// вес Уэбба взвешивает суммы обоих рядов за те же сутки — «совместный
/// ресэмплинг», а не два независимых. `day_sums` — по суткам `(числитель
/// ряда A, знаменатель ряда A, числитель ряда B, знаменатель ряда B)` в
/// фиксированном порядке. Знаменатели не переразвешиваются репликой — тот
/// же приём, что у `cluster_robust_t_from_summary` в `stats`: реплика
/// перетряхивает числители, а не сами счётчики наблюдений.
/// `None` — любой из рядов пуст (нулевой знаменатель).
/// Возвращает `(среднее A, знаменатель A, среднее B, знаменатель B, точка
/// = combine(среднее A, среднее B), реплики combine, отсортированные по
/// возрастанию)`.
/// Деление на счётчики точное: числа наблюдений в памяти, далеко от 2^53.
/// Ёмкость из `u32`: `u32` вкладывается в `usize` на всех целях со std.
#[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
fn joint_two_series_bootstrap(
    day_sums: &[(f64, u64, f64, u64)],
    replications: u32,
    seed: u64,
    combine: impl Fn(f64, f64) -> f64,
) -> Option<(f64, u64, f64, u64, f64, Vec<f64>)> {
    let (tot_a, n_a, tot_b, n_b) =
        day_sums
            .iter()
            .fold((0.0, 0u64, 0.0, 0u64), |(sa, ca, sb, cb), d| {
                (
                    sa + d.0,
                    ca.saturating_add(d.1),
                    sb + d.2,
                    cb.saturating_add(d.3),
                )
            });
    if n_a == 0 || n_b == 0 {
        return None;
    }
    let mean_a = tot_a / count_f64_u64(n_a);
    let mean_b = tot_b / count_f64_u64(n_b);
    let point = combine(mean_a, mean_b);
    let mut reps = Vec::with_capacity(replications as usize);
    if replications == 0 {
        reps.push(point);
    } else {
        let mut rng = SplitMix64::new(seed);
        for _ in 0..replications {
            let mut boot_a = 0.0;
            let mut boot_b = 0.0;
            for d in day_sums {
                let w = next_webb(&mut rng);
                boot_a += w * d.0;
                boot_b += w * d.2;
            }
            let r = combine(boot_a / count_f64_u64(n_a), boot_b / count_f64_u64(n_b));
            // Веса и суммы конечны по построению входа: вырожденная реплика
            // здесь невозможна арифметически, проверка — страховка A2.
            debug_assert!(r.is_finite(), "совместная реплика обязана быть конечной");
            reps.push(r);
        }
    }
    reps.sort_by(|a, b| a.total_cmp(b));
    Some((mean_a, n_a, mean_b, n_b, point, reps))
}

/// Дизъюнктный контраст: точечная оценка — разность пуловых средних;
/// интервал — процентили wild cluster bootstrap разности на весах Уэбба.
/// `None` — пуст ряд A или пуст ряд B. Публична: сохранена без вызывающего
/// внутри крейта по прямому указанию `interfaces.md` (см. докстрока модуля) —
/// расширять при следующей надобности в контрасте двух серий, не писать
/// второй бутстрап.
pub fn disjoint_contrast(
    day_sums: &[(f64, u64, f64, u64)],
    replications: u32,
    seed: u64,
) -> Option<DisjointContrast> {
    let (mean_a, n_a, mean_b, n_b, diff, diffs) =
        joint_two_series_bootstrap(day_sums, replications, seed, |a, b| a - b)?;
    Some(DisjointContrast {
        n_a,
        n_b,
        mean_a,
        mean_b,
        diff,
        ci_low: quantile_sorted(&diffs, CONTRAST_CI_LOW_Q),
        ci_high: quantile_sorted(&diffs, CONTRAST_CI_HIGH_Q),
        replications,
        seed,
    })
}

/// Итог совместного интервала произведения двух средних (таск 03, R08/R50):
/// расширение `joint_two_series_bootstrap` с `combine = умножение` вместо
/// вычитания дизъюнктного контраста. Публична только сигнатура, нужная
/// `costs::net_fill_interval` — сама статистика (среднее A × среднее B)
/// нейтральна к тому, что именно A и B значат для вызывающего.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct JointProduct {
    /// Знаменатель ряда A (например, число исполнившихся входов).
    pub n_a: u64,
    /// Знаменатель ряда B (например, общее число попыток).
    pub n_b: u64,
    /// Среднее ряда A по наблюдённым (невзвешенным) суммам.
    pub mean_a: f64,
    /// Среднее ряда B по наблюдённым (невзвешенным) суммам.
    pub mean_b: f64,
    /// Точка: `mean_a * mean_b` на наблюдённых данных.
    pub point: f64,
    /// Нижний перцентиль совместных реплик `combine` на уровне `alpha`
    /// (односторонний: то, что сравнивается с нулём в вызывающем гейте).
    pub lower: f64,
    /// Число реплик.
    pub replications: u32,
    /// Seed реплик.
    pub seed: u64,
}

/// Совместный интервал произведения `mean_a * mean_b` на общих кластерных
/// репликах Уэбба — то же расширение `joint_two_series_bootstrap`, что и
/// `disjoint_contrast`, с `combine = умножение`. `alpha` — односторонний
/// уровень нижнего перцентиля, обязательный параметр: спека (R08/R50) не
/// называет число для этого гейта — вызывающий обязан передать своё.
/// `None` — любой из рядов пуст.
#[allow(clippy::cast_precision_loss)]
pub(crate) fn joint_product_interval(
    day_sums: &[(f64, u64, f64, u64)],
    alpha: f64,
    replications: u32,
    seed: u64,
) -> Option<JointProduct> {
    debug_assert!((0.0..=1.0).contains(&alpha), "альфа интервала вне [0,1]");
    let (mean_a, n_a, mean_b, n_b, point, reps) =
        joint_two_series_bootstrap(day_sums, replications, seed, |a, b| a * b)?;
    Some(JointProduct {
        n_a,
        n_b,
        mean_a,
        mean_b,
        point,
        lower: quantile_sorted(&reps, alpha),
        replications,
        seed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Реплик в тестах: сетка от их числа не зависит по существу
    /// (свойство перебора весовых векторов, см. `stats`), а прогон в
    /// десять раз быстрее.
    const TEST_REPLICATIONS: u32 = 999;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    /// Два кластера (сутки) с известными числами проверяют формулу
    /// буквально: `mean_a = (10+20)/(4+6) = 3.0`, `mean_b = (5+15)/(4+6)
    /// = 2.0`, разность `= 1.0`. Точечная оценка обязана лежать внутри
    /// своего же интервала на сильном сигнале.
    #[test]
    fn disjoint_contrast_matches_hand_computed_diff_and_ci_bounds() {
        let day_sums = [(10.0, 4u64, 5.0, 4u64), (20.0, 6u64, 15.0, 6u64)];
        let c =
            disjoint_contrast(&day_sums, TEST_REPLICATIONS, CELLS_SEED).expect("обе серии непусты");
        assert_eq!((c.n_a, c.n_b), (10, 10));
        assert!(close(c.mean_a, 3.0), "mean_a = 30/10");
        assert!(close(c.mean_b, 2.0), "mean_b = 20/10");
        assert!(close(c.diff, 1.0), "diff = mean_a - mean_b");
        assert!(
            c.ci_low <= c.diff && c.diff <= c.ci_high,
            "точечная оценка внутри своего интервала"
        );
        let text = c.format_headline();
        assert!(text.contains("diff=1.0000"), "печать: {text}");
        assert!(text.contains("n_a=10") && text.contains("n_b=10"), "{text}");

        assert_eq!(
            disjoint_contrast(&[], TEST_REPLICATIONS, CELLS_SEED),
            None,
            "пустой вход обязан дать None"
        );
    }

    /// Таск 03 (R08/R50): `joint_product_interval` — расширение того же шва,
    /// что дизъюнктный контраст, с `combine = умножение`. Те же два кластера
    /// (сутки) с известными числами проверяют формулу буквально:
    /// `mean_a = (10+20)/(4+6) = 3.0`, `mean_b = (5+15)/(4+6) = 2.0`,
    /// точка `= 6.0`. Разные seed обязаны дать разные (но конечные) нижние
    /// границы — иначе seed декоративен, как и у `wild_cluster_bootstrap_t`.
    #[test]
    fn joint_product_interval_matches_hand_computed_point_and_reacts_to_seed() {
        let day_sums = [(10.0, 4u64, 5.0, 4u64), (20.0, 6u64, 15.0, 6u64)];
        let jp = joint_product_interval(&day_sums, 0.05, TEST_REPLICATIONS, CELLS_SEED)
            .expect("обе серии непусты");
        assert_eq!((jp.n_a, jp.n_b), (10, 10));
        assert!(close(jp.mean_a, 3.0), "mean_a = 30/10");
        assert!(close(jp.mean_b, 2.0), "mean_b = 20/10");
        assert!(close(jp.point, 6.0), "точка = mean_a*mean_b = 3*2");
        assert!(jp.lower.is_finite(), "нижняя граница обязана быть конечной");
        assert!(
            jp.lower <= jp.point,
            "нижний перцентиль не может быть выше точки на положительном сигнале"
        );

        let other_seed = joint_product_interval(&day_sums, 0.05, TEST_REPLICATIONS, CELLS_SEED + 1)
            .expect("обе серии непусты");
        assert_ne!(
            jp.lower, other_seed.lower,
            "другой seed обязан дать другую реплику нижней границы"
        );

        assert_eq!(
            joint_product_interval(&[], 0.05, TEST_REPLICATIONS, CELLS_SEED),
            None
        );
    }

    /// Веса Уэбба записаны здесь и в `stats` **разными выражениями**: там
    /// `(1.5).sqrt()` и `(0.5).sqrt()` считаются в рантайме, здесь стоят
    /// литерал и `FRAC_1_SQRT_2`. Комментарий к `WEBB_POINTS` утверждает, что
    /// поток весов совпадает с потоком `stats` пореплико, и это утверждение
    /// держится ровно на побитовом равенстве шести чисел.
    ///
    /// Сегодня оно верно — оба выражения дают одинаковые f64 по корректному
    /// округлению, — но верно по удаче, а не потому, что кто-то это проверяет.
    /// Правка одного литерала развела бы два потока молча, и ни один тест не
    /// покраснел бы. Сравнение идёт по `to_bits`, а не по `==`: равенство
    /// f64 здесь и есть предмет проверки, приблизительное сравнение проверяло
    /// бы не то.
    #[test]
    fn webb_points_match_stats_bit_for_bit() {
        assert_eq!(
            WEBB_POINTS.len() as u32,
            crate::stats::WEBB_WEIGHT_VALUES,
            "число весов разошлось с сеткой stats"
        );
        for i in 0..crate::stats::WEBB_WEIGHT_VALUES {
            assert_eq!(
                WEBB_POINTS[i as usize].to_bits(),
                crate::stats::webb_weight(i).to_bits(),
                "вес {i} разошёлся с stats::webb_weight побитово"
            );
        }
    }

    /// Граница модулей в духе шагов 1.1/4.1/5.1: анализ не знает про транспорт
    /// и часы. Вещественное число здесь разрешено формулой (bps, интервал),
    /// поэтому его нет среди запрещённых — в отличие от списка уровней.
    ///
    /// Запрещённые фрагменты собраны из частей: литерал целиком триггерил бы
    /// эту же проверку сам на себя.
    #[test]
    fn module_stays_detached_from_transport_and_clocks() {
        const SRC: &str = include_str!("cells.rs");
        let banned = [
            concat!("by", "bit"),
            concat!("tok", "io"),
            concat!("Inst", "ant"),
            concat!("System", "Time"),
            concat!("std::", "time"),
        ];
        for b in banned {
            assert!(!SRC.contains(b), "исходник тянет запрещённое: {b}");
        }
    }
}
