//! DSR, PBO и CPCV на итоговом прогоне (план, §7.3).
//!
//! Done-condition шага буквально — «три числа в отчёте», зависимости — 6.3
//! (бэктест: потребитель `backtest::pnl_curve_bps` и `Fill`), 7.1 (`runs.csv`,
//! журнал ведёт `lob::runs`) и 7.2 (`stats`: там есть только wild cluster
//! bootstrap-t, готовых оценщиков DSR/PBO/CPCV нет — этот модуль их не
//! дублирует из `stats`, а вводит здесь, потому что в `stats` их нет вовсе).
//!
//! Вход — абстрактный итоговый прогон: срез покруговых net в bps (потребитель
//! `backtest::roundtrip_net_bps`, своя копия формулы круга здесь не заводится),
//! список Sharpe пробных прогонов для поправки на множественность и матрица
//! «прогоны × периоды» для PBO. Живой итог в песочнице невозможен, поэтому всё —
//! чистые функции от входа, тестируемые на синтетике с известными ответами.
//! Живого итога модуль не выносит.
//!
//! Число испытаний для DSR/PBO считает журнал `runs.csv` шага 7.1
//! (`lob::runs`): пилот каждого кандидата пула и каждый перезапуск после
//! шага 5 — тоже испытания (раздел Failure плана). Значения
//! Sharpe журнал не хранит — только сам факт прогона, не дающий занизить `N`;
//! чтение — `trials_from_runs_csv` ниже.
//!
//! Единицы Sharpe во всех функциях — на наблюдение (покруговой, без
//! annualization): DSR корректен, пока наблюдаемый Sharpe и пробные Sharpe
//! посчитаны в одних единицах, множитель annualization сокращается в разности
//! `SR − SR0` только при общем масштабе, поэтому здесь его нет вовсе.

use std::path::Path;

use crate::lob::backtest::{roundtrip_net_bps, Fill};
use crate::stats::count_f64;

/// Постоянная Эйлера–Маскерони γ: вес между двумя квантилями в формуле
/// ожидаемого Sharpe под нулём (Bailey — López de Prado, 2014).
const EULER_MASCHERONI: f64 = 0.577_215_664_901_532_9;

/// Верхняя граница числа блоков PBO: `C(16,8)/2 = 6435` сплитов, дальше
/// перебор растёт как биномиальный коэффициент и перестаёт быть отчётом.
const MAX_PBO_PARTITIONS: usize = 16;

/// Число блоков PBO в сводном отчёте: `C(8,4)/2 = 35` сплитов — полный
/// перебор, а не выборка сплитов.
///
/// Публична с таска 29: шапка `shortlist-<дата>.md` печатает **почему** PBO
/// не посчитан (`pbo=n/a (days=4 < 8)`), а порог «сколько периодов нужно»
/// — это и есть данное число (`pbo` отказывает при `periods < partitions`).
/// Второго объявления восьмёрки у вызывающего быть не должно.
pub const REPORT_PBO_PARTITIONS: usize = 8;

// ---------------------------------------------------------------------------
// Моменты и Sharpe.
// ---------------------------------------------------------------------------

/// Выборочные моменты ряда покруговых net: среднее, стандартное отклонение
/// (по популяции, делитель `n` — тот же, что у скошенности и эксцесса ниже),
/// скошенность и эксцесс (с единицей, нормальный ряд даёт 3.0, не 0.0 — так
/// их ждёт знаменатель DSR).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Moments {
    /// Число наблюдений.
    pub n: usize,
    /// Среднее.
    pub mean: f64,
    /// Стандартное отклонение (популяционное).
    pub std: f64,
    /// Скошенность (третий стандартизованный момент).
    pub skew: f64,
    /// Эксцесс (четвёртый стандартизованный момент, с единицей).
    pub kurtosis: f64,
}

/// Моменты ряда. `None` — пусто, короче двух наблюдений, есть неконечные
/// значения или нулевая дисперсия (делить не на что, см. отказ
/// `DegenerateVariance` в `stats`: отсутствие дисперсии не есть нулевой
/// результат).
pub fn moments(xs: &[f64]) -> Option<Moments> {
    if xs.len() < 2 || xs.iter().any(|x| !x.is_finite()) {
        return None;
    }
    let n = count_f64(xs.len());
    let mean = xs.iter().sum::<f64>() / n;
    let (mut m2, mut m3, mut m4) = (0.0, 0.0, 0.0);
    for x in xs {
        let d = x - mean;
        let d2 = d * d;
        m2 += d2;
        m3 += d2 * d;
        m4 += d2 * d2;
    }
    m2 /= n;
    if !m2.is_finite() || m2 <= 0.0 {
        return None;
    }
    let std = m2.sqrt();
    Some(Moments {
        n: xs.len(),
        mean,
        std,
        skew: (m3 / n) / (std * m2),
        kurtosis: (m4 / n) / (m2 * m2),
    })
}

/// Sharpe ряда покруговых net: среднее делить на стандартное отклонение, в
/// единицах на наблюдение. `None` — ряд не считается (см. `moments`).
pub fn sharpe_ratio(xs: &[f64]) -> Option<f64> {
    moments(xs).map(|m| m.mean / m.std)
}

// ---------------------------------------------------------------------------
// Нормальное распределение без зависимостей.
// ---------------------------------------------------------------------------

/// Функция ошибок, аппроксимация Абрамовица–Стеган 7.1.26 (точность ~1e-7 —
/// на два порядка тоньше любого допуска в тестах ниже).
fn erf(x: f64) -> f64 {
    let sign = if x < 0.0 { -1.0 } else { 1.0 };
    let ax = x.abs();
    let t = 1.0 / (1.0 + 0.327_591_1 * ax);
    let y = 1.0
        - (((((1.061_405_429 * t - 1.453_152_027) * t) + 1.421_413_741) * t - 0.284_496_736) * t
            + 0.254_829_592)
            * t
            * (-ax * ax).exp();
    sign * y
}

/// Функция распределения стандартного нормального закона.
pub fn normal_cdf(x: f64) -> f64 {
    0.5 * (1.0 + erf(x / std::f64::consts::SQRT_2))
}

/// Квантиль стандартного нормального закона (аппроксимация Аклама, точность
/// ~1e-9 в середине, ~1e-7 на хвостах). `None` вне `(0, 1)` не возвращается —
/// функция тотальна: границы дают бесконечности, как и положено квантилю.
pub fn normal_inv_cdf(p: f64) -> f64 {
    if p <= 0.0 {
        return f64::NEG_INFINITY;
    }
    if p >= 1.0 {
        return f64::INFINITY;
    }
    if !(p.is_finite()) {
        return f64::NAN;
    }
    const A: [f64; 6] = [
        -3.969_683_028_665_376e+01,
        2.209_460_984_245_205e+02,
        -2.759_285_104_469_687e+02,
        1.383_577_518_672_69e+02,
        -3.066_479_806_614_716e+01,
        2.506_628_277_459_239e+00,
    ];
    const B: [f64; 5] = [
        -5.447_609_879_822_406e+01,
        1.615_858_368_580_409e+02,
        -1.556_989_798_598_866e+02,
        6.680_131_188_771_972e+01,
        -1.328_068_155_288_572e+01,
    ];
    const C: [f64; 6] = [
        -7.784_894_002_430_293e-03,
        -3.223_964_580_411_365e-01,
        -2.400_758_277_161_838e+00,
        -2.549_732_539_343_734e+00,
        4.374_664_141_464_968e+00,
        2.938_163_982_698_783e+00,
    ];
    const D: [f64; 4] = [
        7.784_695_709_041_462e-03,
        3.224_671_290_700_398e-01,
        2.445_134_137_142_996e+00,
        3.754_408_661_907_416e+00,
    ];
    if p < 0.024_25 {
        let q = (-2.0 * p.ln()).sqrt();
        (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    } else if p <= 0.975_75 {
        let q = p - 0.5;
        let r = q * q;
        (((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q
            / (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1.0)
    } else {
        let q = (-2.0 * (1.0 - p).ln()).sqrt();
        -(((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    }
}

// ---------------------------------------------------------------------------
// DSR.
// ---------------------------------------------------------------------------

/// Ожидаемый Sharpe лучшего из испытаний под нулём (Bailey — López de Prado,
/// 2014): `SR0 = √V · ((1−γ)·q(1−1/N) + γ·q(1−1/(N·e)))`, где `V` — выборочная
/// дисперсия Sharpe пробных прогонов, `N` — их число (длина среза: пилотные
/// прогоны и перезапуски входят сюда через `runs.csv`, см. шапку модуля).
/// Одно испытание — отбора не было, ожидание ноль. `None` — испытаний нет или
/// их Sharpe неконечны.
pub fn expected_sharpe_under_null(trial_sharpes: &[f64]) -> Option<f64> {
    if trial_sharpes.iter().any(|s| !s.is_finite()) {
        return None;
    }
    match trial_sharpes.len() {
        0 => None,
        1 => Some(0.0),
        n => {
            let mean = trial_sharpes.iter().sum::<f64>() / count_f64(n);
            let var = trial_sharpes
                .iter()
                .map(|s| (s - mean) * (s - mean))
                .sum::<f64>()
                / count_f64(n - 1);
            if !var.is_finite() || var < 0.0 {
                return None;
            }
            if var == 0.0 {
                // Все испытания одинаковы — разброса отбирать не из чего.
                return Some(0.0);
            }
            let nf = count_f64(n);
            let q1 = normal_inv_cdf(1.0 - 1.0 / nf);
            let q2 = normal_inv_cdf(1.0 - 1.0 / (nf * std::f64::consts::E));
            Some(var.sqrt() * ((1.0 - EULER_MASCHERONI) * q1 + EULER_MASCHERONI * q2))
        }
    }
}

/// Синтетическая выборка длины `n` с точным нулевым средним и точной
/// выборочной дисперсией `1.0` (делитель `n−1`, как в `expected_sharpe_
/// under_null`): один элемент `(n−1)/√n`, остальные `n−1` — `−1/√n`. Сумма
/// нулевая по построению, сумма квадратов даёт дисперсию ровно `1` —
/// проверено тестом ниже, а не принято на веру.
fn unit_variance_trials(n: usize) -> Vec<f64> {
    if n < 2 {
        return vec![0.0; n];
    }
    let nf = count_f64(n);
    let mut trials = vec![-1.0 / nf.sqrt(); n];
    trials[0] = (nf - 1.0) / nf.sqrt();
    trials
}

/// `SR0` для гейта G-POWER-A (`PLAN.md` §6): до первой сессии сбора пробных
/// Sharpe не существует, только их число `n` из шага отбора
/// (`shortlist::nominal_grid_size`/`total_trials`). `expected_sharpe_
/// under_null` требует сам срез, а не только его длину — здесь ему
/// передаётся синтетический срез с выборочной дисперсией, принятой равной
/// единице: стандартное допущение под нулевой гипотезой, когда дисперсия
/// пробных Sharpe ещё не измерена (Bailey — López de Prado, 2014, тот же
/// параметр `V`, стоящий здесь `1` за неимением измерения — не занижает и не
/// завышает поправку сам по себе, единица — нейтральный масштаб). Формула
/// SR0 не дублируется: это обёртка, не вторая реализация. `None` — `n = 0`.
pub fn expected_sharpe_under_null_for_trial_count(n: usize) -> Option<f64> {
    if n == 0 {
        return None;
    }
    expected_sharpe_under_null(&unit_variance_trials(n))
}

/// Deflated Sharpe Ratio: вероятность, что истинный Sharpe положителен с
/// поправкой на `N` испытаний, `Φ((SR − SR0)·√(T−1) / √(1 − skew·SR +
/// (kurt−1)/4·SR²))`. `num_obs` — число покруговых наблюдений `T` (не число
/// испытаний). `None` — не считается наблюдаемый ряд, нет испытаний,
/// знаменатель неположителен или любой вход неконечен: отсутствие данных не
/// есть нулевой DSR.
pub fn dsr(
    observed_sr: f64,
    num_obs: usize,
    skew: f64,
    kurtosis: f64,
    trial_sharpes: &[f64],
) -> Option<f64> {
    if !observed_sr.is_finite() || !skew.is_finite() || !kurtosis.is_finite() || num_obs < 2 {
        return None;
    }
    let sr0 = expected_sharpe_under_null(trial_sharpes)?;
    if !sr0.is_finite() {
        return None;
    }
    let denom_sq = 1.0 - skew * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr * observed_sr;
    if !denom_sq.is_finite() || denom_sq <= 0.0 {
        return None;
    }
    Some(normal_cdf(
        (observed_sr - sr0) * (count_f64(num_obs - 1)).sqrt() / denom_sq.sqrt(),
    ))
}

/// DSR прямо по покруговым net: моменты считаются здесь, испытания —
/// параметром (см. `dsr`). `None` — см. обе функции ниже.
pub fn dsr_from_returns(returns: &[f64], trial_sharpes: &[f64]) -> Option<f64> {
    let m = moments(returns)?;
    dsr(m.mean / m.std, m.n, m.skew, m.kurtosis, trial_sharpes)
}

/// Требуемый Шарп на наблюдение для гейта G-POWER-A: наименьший `x`, при
/// котором `dsr(x, num_obs, 0.0, 3.0, …)` (с `SR0` из
/// `expected_sharpe_under_null_for_trial_count(n_trials)`) достигает
/// `dsr_target`. Скошенность `0` и эксцесс `3.0` — нормальный ряд (`Moments`
/// нормирует так же — нормальный ряд даёт `3.0`, не `0.0`); до сбора данных
/// другой оценки формы распределения нет.
///
/// `dsr` вырождается при этих `skew`/`kurtosis` в `Φ((x−SR0)·√(T−1) /
/// √(1+0.5·x²)) = dsr_target`, `z = Φ⁻¹(dsr_target)` — квадратное уравнение
/// `(T−1−0.5·z²)·x² − 2·(T−1)·SR0·x + ((T−1)·SR0² − z²) = 0`; решение —
/// больший корень (тот, где `x − SR0` того же знака, что `z`), численный
/// поиск здесь не нужен. `None` — `num_obs < 2`, `dsr_target` вне `(0, 1)`,
/// `n_trials` без `SR0` или коэффициент при `x²` неположителен (`z` слишком
/// велик относительно `T`).
pub fn required_sharpe_for_dsr(n_trials: usize, num_obs: usize, dsr_target: f64) -> Option<f64> {
    if num_obs < 2 || !dsr_target.is_finite() || !(0.0..1.0).contains(&dsr_target) {
        return None;
    }
    let sr0 = expected_sharpe_under_null_for_trial_count(n_trials)?;
    let z = normal_inv_cdf(dsr_target);
    if !z.is_finite() {
        return None;
    }
    let t1 = count_f64(num_obs - 1);
    let a = t1 - 0.5 * z * z;
    if a <= 0.0 {
        return None;
    }
    let b = -2.0 * t1 * sr0;
    let c = t1 * sr0 * sr0 - z * z;
    let disc = b * b - 4.0 * a * c;
    if disc < 0.0 {
        return None;
    }
    Some((-b + disc.sqrt()) / (2.0 * a))
}

/// Целевой DSR вердикта (гейт G3-в, `PLAN.md` раздел 6; тот же плановый
/// уровень, что `commands::lob::power::POWER_DSR_TARGET` для G-POWER-A —
/// число одно и то же по плану, но эта константа не импортируется оттуда:
/// `power.rs` вне зоны этого таска, а `0.95` здесь не новое число, а то же
/// значение из `PLAN.md` («требуемый Шарп… для DSR = 0.95»), названное второй
/// именованной константой в её собственном модуле).
pub const DSR_TARGET: f64 = 0.95;

/// DSR наблюдаемого Шарпа при известном числе испытаний, без явного среза
/// пробных Шарпов — та же нейтральная замена `V = 1` (единичная дисперсия
/// пробных Шарпов), что уже несёт `expected_sharpe_under_null_for_trial_count`
/// для гейта G-POWER-A, применённая к полному `dsr()`, а не только к его
/// `SR0`. Не вторая формула: тот же `dsr()`, тот же синтетический срез
/// `unit_variance_trials(n_trials)`, только явно параметризованный числом
/// испытаний вместо готового среза — ровно то, что нужно вердикту таска 13,
/// когда пробные Шарпы всех `N` испытаний физически не посчитаны (это заняло
/// бы `N` дополнительных прогонов бэктеста), а их число уже есть в
/// `runs.csv`. `None` — см. `dsr`/`expected_sharpe_under_null_for_trial_count`.
pub fn dsr_for_trial_count(
    observed_sr: f64,
    num_obs: usize,
    skew: f64,
    kurtosis: f64,
    n_trials: usize,
) -> Option<f64> {
    dsr(
        observed_sr,
        num_obs,
        skew,
        kurtosis,
        &unit_variance_trials(n_trials),
    )
}

// ---------------------------------------------------------------------------
// Джекнайф-по-суткам (A03): устойчивость точечной оценки к исключению одних
// суток-кластера. Не бутстрап и не гейт — диагностика рядом с вердиктом
// (`PLAN.md` В-2: «джекнайф-по-суткам чувствительность» печатается всегда,
// когда есть за что: вердикт при `G = G_MIN` не назовёшь честным без неё).
// ---------------------------------------------------------------------------

/// Одна точка джекнайфа: оценка при исключённых сутках `excluded_day` вместо
/// самого значения — воспроизводимость важнее произвольной сортировки.
#[derive(Debug, Clone, PartialEq)]
pub struct JackknifeSensitivity {
    /// Точечные оценки без одних суток, по одной на исключённые сутки.
    pub leave_one_out: Vec<(String, f64)>,
    /// Наименьшая из оценок.
    pub min: f64,
    /// Наибольшая из оценок.
    pub max: f64,
    /// Размах `max - min`: чем он шире, тем меньше можно доверять точечной
    /// оценке на границе `G = G_MIN` (одни сутки решают исход).
    pub range: f64,
}

/// Джекнайф-по-суткам из уже посчитанных оценок «без суток X» (вызывающий —
/// `commands::lob::shortlist` — считает их отдельными прогонами
/// `run_profiles_with_fill_model` на времянке без одних суток; этот модуль
/// формул повторного счёта не вводит, только сводит готовые числа).
/// `None` — меньше двух оценок: без исключения одних суток чувствительность
/// не из чего мерить (единственная оценка «без суток X» ничем не
/// отличается от точечной оценки на всех сутках).
pub fn jackknife_sensitivity(leave_one_out: &[(String, f64)]) -> Option<JackknifeSensitivity> {
    if leave_one_out.len() < 2 || leave_one_out.iter().any(|(_, v)| !v.is_finite()) {
        return None;
    }
    let min = leave_one_out
        .iter()
        .map(|(_, v)| *v)
        .fold(f64::INFINITY, f64::min);
    let max = leave_one_out
        .iter()
        .map(|(_, v)| *v)
        .fold(f64::NEG_INFINITY, f64::max);
    Some(JackknifeSensitivity {
        leave_one_out: leave_one_out.to_vec(),
        min,
        max,
        range: max - min,
    })
}

// ---------------------------------------------------------------------------
// PBO.
// ---------------------------------------------------------------------------

/// Sharpe среза пробного прогона по выбранным блокам (блок — непрерывный
/// отрезок длины `block_len`, выбрано предикатом). Возвращает конечное число
/// или бесконечность при нулевой дисперсии с ненулевым средним (ранжирование
/// ниже такие значения упорядочивает корректно); пустой отбор или неконечные
/// данные дают `0.0` — сплит с таким отбором невозможен по построению.
fn sharpe_over_blocks(
    trial: &[f64],
    block_len: usize,
    partitions: usize,
    select: impl Fn(usize) -> bool,
) -> f64 {
    let mut n = 0usize;
    let mut sum = 0.0;
    let mut sumsq = 0.0;
    // Чанки вместо срезов `[b*bl..(b+1)*bl]`: вызывающие передают ряды,
    // длина которых кратна покрытию (`periods`, строки равной длины), так что
    // первые `partitions` чанков — те же данные без паникующего синтаксиса.
    // Хвост, не кратный чанку, отбрасывается обеими версиями одинаково.
    for (b, chunk) in trial.chunks_exact(block_len).enumerate().take(partitions) {
        if !select(b) {
            continue;
        }
        for x in chunk {
            if !x.is_finite() {
                return 0.0;
            }
            n += 1;
            sum += x;
            sumsq += x * x;
        }
    }
    if n == 0 {
        return 0.0;
    }
    let mean = sum / count_f64(n);
    let var = sumsq / count_f64(n) - mean * mean;
    if var > 0.0 {
        mean / var.sqrt()
    } else if mean > 0.0 {
        f64::INFINITY
    } else if mean < 0.0 {
        f64::NEG_INFINITY
    } else {
        0.0
    }
}

/// Probability of Backtest Overfitting (Bailey et al., 2014) на матрице
/// «испытания × периоды»: время режется на чётное число `partitions`
/// непрерывных блоков, перебираются все разбиения пополам IS/OOS (каждая
/// дополняющая пара один раз — блок 0 всегда в IS), в каждом сплите лучший по
/// IS получает относительный OOS-ранг `(хуже + 0.5·равные + 0.5) / N`
/// (середина ранга: ties и `N = 2` не вырождаются в ноль тождественно), PBO —
/// доля сплитов с рангом ниже медианы (`< 0.5`). Сплиты блочные без purging:
/// purging и embargo — принадлежность CPCV ниже, здесь опубликованная
/// процедура без них. `None` — меньше двух испытаний, рваные или пустые ряды,
/// нечётное число блоков, блоков больше периодов или больше границы перебора.
pub fn pbo(trials: &[Vec<f64>], partitions: usize) -> Option<f64> {
    if trials.len() < 2
        || partitions < 2
        || !partitions.is_multiple_of(2)
        || partitions > MAX_PBO_PARTITIONS
    {
        return None;
    }
    let periods = trials.first()?.len();
    if periods < partitions || !trials.iter().all(|r| r.len() == periods) {
        return None;
    }
    let block_len = periods / partitions;
    if block_len == 0 {
        return None;
    }
    let rest = partitions - 1;
    let want = partitions / 2 - 1;
    let n = trials.len();
    let mut below = 0usize;
    let mut total = 0usize;
    for mask in 0..(1u32 << rest) {
        if mask.count_ones() as usize != want {
            continue;
        }
        let is_block = |b: usize| b == 0 || (mask >> (b - 1)) & 1 == 1;
        let mut best = 0;
        let mut best_v = f64::NEG_INFINITY;
        for (i, trial) in trials.iter().enumerate() {
            let v = sharpe_over_blocks(trial, block_len, partitions, is_block);
            if v > best_v {
                best_v = v;
                best = i;
            }
        }
        // `best` — только из индексов `enumerate` по непустому `trials`
        // (длина ≥ 2 проверена выше), вне диапазона ему взяться неоткуда.
        #[allow(clippy::indexing_slicing)]
        let picked = sharpe_over_blocks(&trials[best], block_len, partitions, |b| !is_block(b));
        let mut worse = 0usize;
        let mut equal = 0usize;
        for (j, trial) in trials.iter().enumerate() {
            if j == best {
                continue;
            }
            let v = sharpe_over_blocks(trial, block_len, partitions, |b| !is_block(b));
            // Точное равенство float здесь не небрежность, и `float_cmp`
            // заглушен сознательно. PBO делит вес ничьей пополам
            // (`0.5 * equal` ниже), а ничья возникает не «случайно
            // совпавшими» числами: `sharpe_over_blocks` на вырожденном
            // испытании — нефинитные значения или пустая выборка —
            // возвращает ровно `0.0`, побитово одинаковый для всех таких
            // испытаний. То есть `==` ловит именно тот случай, ради
            // которого поправка на ничью и написана. Сравнение с допуском
            // вместо этого склеивало бы разные испытания в ничью и занижало
            // PBO.
            #[allow(clippy::float_cmp)]
            let tie = v == picked;
            if v < picked {
                worse += 1;
            } else if tie {
                equal += 1;
            }
        }
        if (count_f64(worse) + 0.5 * count_f64(equal) + 0.5) / (count_f64(n)) < 0.5 {
            below += 1;
        }
        total += 1;
    }
    if total == 0 {
        return None;
    }
    Some(count_f64(below) / count_f64(total))
}

// ---------------------------------------------------------------------------
// CPCV.
// ---------------------------------------------------------------------------

/// Параметры комбинаторного purged CV (López de Prado, гл. 7): число складок
/// и ширина защитных зон в покруговых наблюдениях. Purging здесь — сброс
/// `purge` наблюдений в начале каждой тестовой складки (метки соседних кругов
/// делят горизонт 10 с, см. Decision 20), embargo — сброс `embargo` наблюдений
/// в её конце против автокорреляционного просачивания. Производственные
/// ширины **для покруговых рядов** не назначены: горизонт меток в кругах
/// ещё не измерен, вызывающий код передаёт их явно, заглушки под них здесь
/// нет. Для суточного ряда сводного отчёта ширины выведены и объявлены
/// один раз — `REPORT_CPCV_PARAMS` ниже (там же, почему на суточных
/// наблюдениях защитные зоны нулевые); это не заглушка под покруговые, а
/// параметры другого ряда.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CpcvParams {
    /// Число складок (≥ 2).
    pub folds: usize,
    /// Сброс в начале тестовой складки.
    pub purge: usize,
    /// Сброс в конце тестовой складки.
    pub embargo: usize,
}

/// Параметры CPCV сводного отчёта для ряда «`net` профиля по календарным
/// суткам» (`commands::lob::shortlist`, таск 29). Ни одно из трёх чисел не
/// выбрано здесь — каждое выведено из уже записанного:
///
/// * `purge = 0`, `embargo = 0` — защитные зоны стоят против просачивания
///   меток, делящих горизонт 10 с (doc `CpcvParams` выше, Decision 20: выход
///   тейкером ровно на `t0 + 10 с`). Наблюдение этого ряда — агрегат за
///   календарные сутки, соседние наблюдения разнесены сутками, и пересечения
///   меток, ради которого наблюдения сбрасывают, здесь нет по построению.
///   Производственные ширины для **покруговых** рядов планом по-прежнему не
///   назначены (см. doc `CpcvParams`) — эта константа их не назначает.
/// * `folds = 2` — единственное число складок, названное существующим
///   текстом: doc `CpcvParams::folds` говорит «(≥ 2)», и `cpcv_mean_oos_
///   sharpe` отказывает ниже. Большее число ни задачей, ни планом не
///   назначено, а назначить его самому значило бы изобрести число.
pub const REPORT_CPCV_PARAMS: CpcvParams = CpcvParams {
    folds: 2,
    purge: 0,
    embargo: 0,
};

/// Наименьшая длина ряда, при которой параметры вообще применимы:
/// `folds · (purge + embargo + 2)` — по одной складке длины `len / folds`,
/// из которой сброс съедает `purge + embargo`, и правилу
/// `cpcv_mean_oos_sharpe` «складка короче двух наблюдений — отказ».
/// Живёт рядом с оценщиком, а не у вызывающего: иначе порог, которым шапка
/// объясняет `n/a`, разошёлся бы с порогом, по которому считает функция.
#[must_use]
pub const fn min_obs_for_cpcv(params: CpcvParams) -> usize {
    params.folds * (params.purge + params.embargo + 2)
}

/// Средний OOS Sharpe по складкам CPCV на итоговых покруговых net: каждая
/// складка по очереди тестовая, защитные зоны из скоринга выброшены, складка
/// без дисперсии пропускается (см. `moments`), а не обнуляет среднее.
/// `None` — складок меньше двух, ряд пуст, складка короче двух наблюдений
/// после сброса или ни одна складка не посчиталась: параметры несовместны со
/// входом, а не «эффекта нет».
pub fn cpcv_mean_oos_sharpe(per_trade_net: &[f64], params: CpcvParams) -> Option<f64> {
    let CpcvParams {
        folds,
        purge,
        embargo,
    } = params;
    if folds < 2 || per_trade_net.is_empty() {
        return None;
    }
    let fold_len = per_trade_net.len() / folds;
    let kept = fold_len.checked_sub(purge)?.checked_sub(embargo)?;
    if kept < 2 {
        return None;
    }
    let mut sum = 0.0;
    let mut scored = 0usize;
    for k in 0..folds {
        let base = k * fold_len;
        // Границы доказаны выше: `kept = fold_len - purge - embargo ≥ 2`,
        // откуда `base + purge + kept = base + fold_len - embargo ≤ len`
        // (k < folds, fold_len = len / folds). Срез тотален по построению.
        #[allow(clippy::indexing_slicing)]
        let fold = &per_trade_net[base + purge..base + fold_len - embargo];
        if let Some(s) = sharpe_ratio(fold) {
            sum += s;
            scored += 1;
        }
    }
    if scored == 0 {
        return None;
    }
    Some(sum / count_f64(scored))
}

/// Средний OOS Sharpe **процедуры отбора** по складкам CPCV (задача §6.4
/// п. 4, `SETTLED.md` В-19: «PBO и CPCV проверяют процедуру отбора, а не
/// выбранный профиль»). Вход — та же матрица «испытания × периоды», что у
/// `pbo`. Каждая складка по очереди тестовая: отбор происходит **заново**
/// на IS-периодах (все, кроме тестовой складки) переданным правилом, и
/// Sharpe считается по OOS-периодам того испытания, которое правило выбрало
/// **в этой** складке. Защитные зоны выброшены из OOS-скоринга, как в
/// `cpcv_mean_oos_sharpe`; хвост, не кратный складке, тестовым не бывает и
/// остаётся в IS. Итог — среднее по посчитавшимся складкам.
///
/// Правило отбора — параметр, а не жилец этого модуля: им владеет
/// шорт-лист (`commands::lob::shortlist`), здесь только формула.
/// `select(trials, is_periods) -> Option<usize>` возвращает индекс строки;
/// `None` от правила пропускает складку, а не обнуляет её.
///
/// `None` — испытаний меньше двух (отбирать не из чего, и это не «нулевой
/// эффект»), складок меньше двух, матрица пуста или рвана, тестовая складка
/// короче двух наблюдений после сброса, либо ни одна складка не посчиталась.
pub fn cpcv_selection_oos_sharpe(
    trials: &[Vec<f64>],
    params: CpcvParams,
    select: impl Fn(&[Vec<f64>], &[usize]) -> Option<usize>,
) -> Option<f64> {
    let CpcvParams {
        folds,
        purge,
        embargo,
    } = params;
    if folds < 2 || trials.len() < 2 {
        return None;
    }
    let periods = trials.first()?.len();
    if periods == 0 || !trials.iter().all(|r| r.len() == periods) {
        return None;
    }
    let fold_len = periods / folds;
    let kept = fold_len.checked_sub(purge)?.checked_sub(embargo)?;
    if kept < 2 {
        return None;
    }
    let mut sum = 0.0;
    let mut scored = 0usize;
    for k in 0..folds {
        let base = k * fold_len;
        let is_periods: Vec<usize> = (0..periods)
            .filter(|j| *j < base || *j >= base + fold_len)
            .collect();
        let Some(i) = select(trials, &is_periods) else {
            continue;
        };
        let Some(row) = trials.get(i) else {
            continue;
        };
        let oos: Vec<f64> = (base + purge..base + fold_len - embargo)
            .filter_map(|j| row.get(j).copied())
            .collect();
        if let Some(s) = sharpe_ratio(&oos) {
            sum += s;
            scored += 1;
        }
    }
    if scored == 0 {
        return None;
    }
    Some(sum / count_f64(scored))
}

// ---------------------------------------------------------------------------
// Вход от бэктеста, сводный отчёт, число испытаний из runs.csv.
// ---------------------------------------------------------------------------

/// Покруговые net в bps из закрытых кругов бэктеста (потребитель
/// `backtest::roundtrip_net_bps`: формула круга одна, здесь только порядок).
/// `None` — кругов нет или хотя бы один не посчитался (то же правило, что
/// `pnl_curve_bps`: молча выкидывать круг значило бы пересчитывать выборку
/// задним числом).
pub fn per_trade_net_bps(fills: &[Fill]) -> Option<Vec<f64>> {
    if fills.is_empty() {
        return None;
    }
    let mut out = Vec::with_capacity(fills.len());
    for f in fills {
        out.push(roundtrip_net_bps(f)?);
    }
    Some(out)
}

/// Три числа шага 7.3 в отчёте: DSR итоговой кривой с поправкой на испытания,
/// PBO по матрице испытаний и средний OOS Sharpe CPCV.
#[derive(Debug, Clone, PartialEq)]
pub struct FinalMetrics {
    /// Число испытаний (длина среза пробных Sharpe, источник — `runs.csv`).
    pub trials: usize,
    /// Deflated Sharpe Ratio итоговой кривой (`None` — не посчитался).
    pub dsr: Option<f64>,
    /// Вероятность переобучения (`None` — не посчиталась).
    pub pbo: Option<f64>,
    /// Средний OOS Sharpe CPCV (`None` — не посчитался).
    pub cpcv_oos_sharpe: Option<f64>,
}

/// Собрать три числа из абстрактного итогового прогона: покруговые net,
/// Sharpe пробных прогонов (поправка DSR), матрица «испытания × периоды» для
/// PBO и параметры CPCV. PBO гонится на фиксированных 8 блоках (полный
/// перебор из 35 сплитов, см. `REPORT_PBO_PARTITIONS`).
pub fn final_metrics(
    per_trade_net: &[f64],
    trial_sharpes: &[f64],
    pbo_trials: &[Vec<f64>],
    cpcv: CpcvParams,
) -> FinalMetrics {
    FinalMetrics {
        trials: trial_sharpes.len(),
        dsr: dsr_from_returns(per_trade_net, trial_sharpes),
        pbo: pbo(pbo_trials, REPORT_PBO_PARTITIONS),
        cpcv_oos_sharpe: cpcv_mean_oos_sharpe(per_trade_net, cpcv),
    }
}

impl FinalMetrics {
    /// Строки отчёта: все три числа с числом испытаний; отсутствие числа
    /// печатается как `none`, а не ноль (см. правило `None` выше).
    pub fn summary_lines(&self) -> Vec<String> {
        vec![format!(
            "final_metrics: trials={} dsr={} pbo={} cpcv_oos_sharpe={}",
            self.trials,
            fmt_opt(self.dsr),
            fmt_opt(self.pbo),
            fmt_opt(self.cpcv_oos_sharpe),
        )]
    }
}

/// Печать опционального числа: `none`, если данных нет.
fn fmt_opt(v: Option<f64>) -> String {
    match v {
        Some(x) if x.is_finite() => format!("{x:.4}"),
        Some(_) => "nonfinite".to_string(),
        None => "none".to_string(),
    }
}

/// Число испытаний для DSR/PBO из журнала `runs.csv` (шаг 7.1).
///
/// Тонкая обёртка: формат строк и правило «что считается испытанием» живут в
/// `lob::runs`, здесь только связь «журнал → число». `None` — журнал не
/// читается или строка битая (считать нельзя, а не ноль); отсутствие файла —
/// `Some(0)` (прогонов ещё не было). Вызывающий код обязан проверить `None`
/// до подстановки в DSR/PBO, а не подменять его единицей.
pub fn trials_from_runs_csv(path: &Path) -> Option<usize> {
    crate::lob::runs::trials_from_runs_csv(path)
}

#[cfg(test)]
mod tests;
