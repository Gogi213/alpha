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
//! (`lob::runs`): пилотные прогоны обоих кандидатов и каждый перезапуск после
//! шага 5 — тоже испытания (OPEN_QUESTIONS H10, раздел Failure). Значения
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
const REPORT_PBO_PARTITIONS: usize = 8;

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
/// ширины не назначены: горизонт меток в кругах ещё не измерен, вызывающий
/// код передаёт их явно, констант-заглушек здесь нет.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CpcvParams {
    /// Число складок (≥ 2).
    pub folds: usize,
    /// Сброс в начале тестовой складки.
    pub purge: usize,
    /// Сброс в конце тестовой складки.
    pub embargo: usize,
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
mod tests {
    use super::*;

    fn close(a: f64, b: f64, tol: f64) -> bool {
        (a - b).abs() <= tol
    }

    /// Нормальное распределение сверяется с опубликованными процентилями, а
    /// не с самим собой: 99-й процентиль 2.3263, медиана 0, плюс круговой
    /// обход прямой и обратной функций.
    #[test]
    fn normal_cdf_matches_published_percentiles_and_roundtrips() {
        assert!(close(normal_cdf(0.0), 0.5, 1e-9));
        assert!(close(normal_inv_cdf(0.5), 0.0, 1e-9));
        assert!(
            close(normal_inv_cdf(0.99), 2.3263, 1e-4),
            "99-й процентиль обязан быть 2.3263, получили {}",
            normal_inv_cdf(0.99)
        );
        for x in [-2.0, -1.0, -0.25, 0.75, 1.5] {
            // Допуск 1e-5, а не 1e-6: ошибка erf (≤ 1.5e-7) усиливается
            // делением на плотность в точке (при |x| = 2 даёт ~1.4e-6), и
            // более тонкий допуск проверял бы аппроксимацию, а не свойство.
            assert!(
                close(normal_inv_cdf(normal_cdf(x)), x, 1e-5),
                "круговой обход сломан на {x}"
            );
        }
        assert_eq!(normal_inv_cdf(0.0), f64::NEG_INFINITY);
        assert_eq!(normal_inv_cdf(1.0), f64::INFINITY);
    }

    /// Моменты на [1,2,3,4] считаются руками: среднее 2.5, дисперсия 1.25,
    /// Sharpe 2.5/√1.25, скошенность 0 (симметрия), эксцесс 1.64.
    #[test]
    fn moments_match_hand_computation_on_1_2_3_4() {
        let m = moments(&[1.0, 2.0, 3.0, 4.0]).unwrap();
        assert!(close(m.mean, 2.5, 1e-12));
        assert!(close(m.std, 1.25_f64.sqrt(), 1e-12));
        assert!(close(m.mean / m.std, 2.236_067_977_5, 1e-9));
        assert!(close(m.skew, 0.0, 1e-12));
        assert!(close(m.kurtosis, 1.64, 1e-12));
        assert_eq!(m.n, 4);
    }

    /// Без дисперсии Sharpe нет, а не ноль: константа и одиночка отказываются.
    #[test]
    fn sharpe_is_none_without_variance() {
        assert_eq!(sharpe_ratio(&[]), None);
        assert_eq!(sharpe_ratio(&[1.0]), None);
        assert_eq!(sharpe_ratio(&[2.0, 2.0, 2.0, 2.0]), None);
        assert_eq!(sharpe_ratio(&[1.0, f64::NAN, 3.0]), None);
    }

    /// Поправка на множественность работает в правильную сторону: тот же
    /// наблюдаемый Sharpe при 200 испытаниях значит меньше, чем при пяти.
    #[test]
    fn dsr_falls_as_trial_count_grows() {
        let few = [1.8, 2.0, 1.5, 1.0, 0.5];
        let mut many = vec![0.0; 200];
        for (i, v) in many.iter_mut().enumerate() {
            *v = 0.5 * ((i as f64).sin() + 0.3 * (7.0 * i as f64).cos());
        }
        let d_few = dsr(1.0, 252, 0.0, 3.0, &few).unwrap();
        let d_many = dsr(1.0, 252, 0.0, 3.0, &many).unwrap();
        assert!(
            d_few > d_many,
            "DSR при 5 испытаниях ({d_few}) обязан быть выше, чем при 200 ({d_many})"
        );
    }

    /// Известные ответы на синтетике: переобученная кривая (скромный Sharpe,
    /// короткая выборка, сто испытаний) — низкий DSR; сильный сигнал на
    /// длинной выборке при пяти испытаниях — высокий DSR.
    #[test]
    fn dsr_overfitted_curve_is_low_and_strong_curve_is_high() {
        // Разброс испытаний обязателен: при нулевой дисперсии пробных Sharpe
        // SR0 = 0 (отбирать не из чего) и поправка исчезает — переобучение
        // видно только на разбросе.
        let overfit_trials: Vec<f64> = (0..100)
            .map(|i| if i % 2 == 0 { 0.5 } else { -0.5 })
            .collect();
        let weak = dsr(0.3, 60, 0.0, 3.0, &overfit_trials).unwrap();
        assert!(
            weak < 0.5,
            "переобученная кривая обязана дать низкий DSR, получили {weak}"
        );
        let strong_trials = [1.8, 2.0, 1.5, 1.0, 0.5];
        let strong = dsr(2.0, 252, 0.0, 3.0, &strong_trials).unwrap();
        assert!(
            strong > 0.8,
            "сильный сигнал обязан дать высокий DSR, получили {strong}"
        );
    }

    /// Одно испытание — отбора не было: штраф за множественность отсутствует,
    /// и DSR выше, чем при тех же данных, но ста испытаниях.
    #[test]
    fn dsr_single_trial_has_no_selection_penalty() {
        let single = dsr(1.0, 252, 0.0, 3.0, &[1.0]).unwrap();
        let spread: Vec<f64> = (0..100)
            .map(|i| if i % 2 == 0 { 0.5 } else { -0.5 })
            .collect();
        let many = dsr(1.0, 252, 0.0, 3.0, &spread).unwrap();
        assert!(single > many, "single={single} many={many}");
        assert!(
            (0.0..=1.0).contains(&single),
            "DSR — вероятность, обязана лежать в [0,1], получили {single}"
        );
    }

    /// Вырожденный вход — отказ, а не число: пустые испытания, T < 2,
    /// неконечность, неположительный знаменатель, константный ряд.
    #[test]
    fn dsr_refuses_degenerate_input() {
        assert_eq!(dsr(1.0, 252, 0.0, 3.0, &[]), None);
        assert_eq!(dsr(1.0, 1, 0.0, 3.0, &[1.0]), None);
        assert_eq!(dsr(f64::NAN, 252, 0.0, 3.0, &[1.0]), None);
        assert_eq!(dsr(1.0, 252, 0.0, 3.0, &[f64::NAN]), None);
        // Знаменатель 1 − 0 + (−3−1)/4·2² = −3 ≤ 0: эксцесс вне физического
        // смысла ломает корень — отказ, а не число.
        assert_eq!(dsr(2.0, 252, 0.0, -3.0, &[1.0]), None);
        assert_eq!(dsr_from_returns(&[1.0, 1.0, 1.0, 1.0], &[1.0]), None);
        assert_eq!(dsr_from_returns(&[], &[1.0]), None);
    }

    /// PBO = 1.0, когда IS-победитель заучил IS-блоки и валится на OOS: при
    /// S = 4 три сплита, и во всех трёх OOS-ранг ниже медианы.
    #[test]
    fn pbo_is_one_when_is_winner_fails_out_of_sample() {
        let memorized = vec![4.0, 4.0, 1.0, 1.0, -3.0, -3.0, -3.0, -3.0];
        // Приманки — плоские нули: их Sharpe 0.0 ниже любого IS-значения
        // заучившего (все три сплита) и выше любого его OOS-значения.
        // (Отрицательная константа не годится: её −inf делит худший OOS-ранг
        // с заучившим и размывает счёт ровно до медианы.)
        let flat_a = vec![0.0; 8];
        let flat_b = vec![0.0; 8];
        let got = pbo(&[memorized, flat_a, flat_b], 4).unwrap();
        assert!(
            close(got, 1.0, 1e-12),
            "заученный IS-победитель обязан дать PBO = 1.0, получили {got}"
        );
    }

    /// Бесструктурный шум не обязан пугать: пять детерминированных сидов, 8
    /// рядов, S = 6 — средний PBO остаётся в середине, вдали и от нуля, и от
    /// единицы. Среднее по сидам, а не один сид: один розыгрыш при десяти
    /// коррелированных сплитах шумит сам по себе, свойство — у среднего.
    #[test]
    fn pbo_on_unstructured_noise_stays_near_one_half() {
        let mut mean = 0.0;
        for seed in 1..=5u64 {
            let mut state = 0x1234_5678_9ABC_DEF1u64.wrapping_add(seed.wrapping_mul(97));
            let mut next = move || {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                (state >> 33) as f64 / (1u64 << 31) as f64 * 2.0 - 1.0
            };
            let trials: Vec<Vec<f64>> = (0..8).map(|_| (0..48).map(|_| next()).collect()).collect();
            let got = pbo(&trials, 6).unwrap();
            println!("pbo on deterministic noise (seed {seed}) = {got}");
            mean += got;
        }
        mean /= 5.0;
        println!("pbo on deterministic noise: mean over 5 seeds = {mean}");
        assert!(
            (0.3..=0.7).contains(&mean),
            "шум обязан дать средний PBO около половины, получили {mean}"
        );
        // Детерминизм: тот же вход — тот же PBO.
        let mut state = 0x1234_5678_9ABC_DEF1u64.wrapping_add(1u64.wrapping_mul(97));
        let mut next = move || {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (state >> 33) as f64 / (1u64 << 31) as f64 * 2.0 - 1.0
        };
        let trials: Vec<Vec<f64>> = (0..8).map(|_| (0..48).map(|_| next()).collect()).collect();
        assert_eq!(pbo(&trials, 6).unwrap(), pbo(&trials, 6).unwrap());
    }

    /// Вырожденный вход PBO — отказ: мало испытаний, рваные ряды, нечётные
    /// блоки, блоков больше периодов, перебор за границей.
    #[test]
    fn pbo_refuses_degenerate_input() {
        assert_eq!(pbo(&[vec![1.0, -1.0, 1.0, -1.0]], 2), None);
        assert_eq!(
            pbo(&[vec![1.0, 2.0], vec![1.0]], 2),
            None,
            "рваные ряды — отказ"
        );
        assert_eq!(
            pbo(&[vec![1.0, -1.0], vec![-1.0, 1.0]], 3),
            None,
            "нечётные блоки — отказ"
        );
        assert_eq!(pbo(&[vec![1.0], vec![2.0]], 4), None);
        assert_eq!(
            pbo(&[vec![1.0, -1.0], vec![-1.0, 1.0]], 18),
            None,
            "перебор за границей — отказ"
        );
    }

    /// CPCV различает эдж и удачную полосу: ровный плюс даёт положительный
    /// OOS Sharpe, а «одна удачная полоса плюс дрейф вниз» — отрицательный,
    /// ниже своего же полного Sharpe.
    #[test]
    fn cpcv_rewards_steady_edge_and_penalizes_a_lucky_streak() {
        let steady: Vec<f64> = (0..40)
            .map(|i| if i % 2 == 0 { 1.0 } else { 1.02 })
            .collect();
        let params = CpcvParams {
            folds: 4,
            purge: 0,
            embargo: 0,
        };
        let steady_oos = cpcv_mean_oos_sharpe(&steady, params).unwrap();
        assert!(
            steady_oos > 0.0,
            "ровный эдж обязан дать положительный OOS, получили {steady_oos}"
        );
        let mut lucky = vec![5.0; 8];
        lucky.extend((0..32).map(|i| if i % 2 == 0 { -0.4 } else { -0.6 }));
        let lucky_oos = cpcv_mean_oos_sharpe(&lucky, params).unwrap();
        let lucky_full = sharpe_ratio(&lucky).unwrap();
        assert!(
            lucky_full > 0.0,
            "полоса обязана тянуть полный Sharpe в плюс, получили {lucky_full}"
        );
        assert!(
            lucky_oos < 0.0 && lucky_oos < lucky_full,
            "CPCV обязан наказать удачную полосу: oos={lucky_oos} full={lucky_full}"
        );
    }

    /// Purge и embargo реально выедают скоринг: складка из двух наблюдений с
    /// purge = embargo = 1 не считается, без сброса — считается; одна
    /// складка и пустой ряд — отказ.
    #[test]
    fn cpcv_purge_and_embargo_consume_short_folds() {
        let xs = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0];
        assert_eq!(
            cpcv_mean_oos_sharpe(
                &xs,
                CpcvParams {
                    folds: 4,
                    purge: 1,
                    embargo: 1
                }
            ),
            None,
            "защитные зоны съели складку целиком"
        );
        assert!(cpcv_mean_oos_sharpe(
            &xs,
            CpcvParams {
                folds: 4,
                purge: 0,
                embargo: 0
            }
        )
        .is_some());
        assert_eq!(
            cpcv_mean_oos_sharpe(
                &xs,
                CpcvParams {
                    folds: 1,
                    purge: 0,
                    embargo: 0
                }
            ),
            None
        );
        assert_eq!(
            cpcv_mean_oos_sharpe(
                &[],
                CpcvParams {
                    folds: 4,
                    purge: 0,
                    embargo: 0
                }
            ),
            None
        );
    }

    /// Вход от бэктеста reuse'ит формулу круга, а не копирует её: лонг
    /// 100 → 101 даёт 92.5 bps чистыми, пусто и битый круг — отказ.
    #[test]
    fn per_trade_net_uses_backtest_roundtrip_without_copying_it() {
        let fills = [
            Fill {
                dir: 1,
                entry_px: 100.0,
                exit_px: 101.0,
                qty: 1.0,
            },
            Fill {
                dir: 1,
                entry_px: 100.0,
                exit_px: 99.0,
                qty: 1.0,
            },
        ];
        let nets = per_trade_net_bps(&fills).unwrap();
        assert!(close(nets[0], 92.5, 1e-9));
        assert!(close(nets[1], -107.5, 1e-9));
        assert_eq!(per_trade_net_bps(&[]), None);
        let bad = [Fill {
            dir: 0,
            entry_px: 100.0,
            exit_px: 101.0,
            qty: 1.0,
        }];
        assert_eq!(per_trade_net_bps(&bad), None);
    }

    /// Done-condition буквально: сводка несёт все три числа и число испытаний.
    #[test]
    fn report_carries_all_three_done_numbers() {
        let per_trade: Vec<f64> = (0..48)
            .map(|i| if i % 2 == 0 { 1.0 } else { 1.02 })
            .collect();
        let trial_sharpes = [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0];
        let mut state = 77u64;
        let mut next = move || {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            (state >> 33) as f64 / (1u64 << 31) as f64 * 2.0 - 1.0
        };
        let pbo_trials: Vec<Vec<f64>> = (0..8).map(|_| (0..48).map(|_| next()).collect()).collect();
        let rep = final_metrics(
            &per_trade,
            &trial_sharpes,
            &pbo_trials,
            CpcvParams {
                folds: 4,
                purge: 0,
                embargo: 0,
            },
        );
        assert_eq!(rep.trials, 8);
        assert!(rep.dsr.is_some() && rep.pbo.is_some() && rep.cpcv_oos_sharpe.is_some());
        let text = rep.summary_lines().join("\n");
        assert!(text.contains("trials=8"), "испытания: {text}");
        assert!(text.contains("dsr="), "DSR: {text}");
        assert!(text.contains("pbo="), "PBO: {text}");
        assert!(text.contains("cpcv_oos_sharpe="), "CPCV: {text}");
    }

    /// Журнал 7.1 кормит число испытаний: два пилота и перезапуск — три
    /// испытания; долг и правка после результатов — учёт, а не испытания.
    /// Битый журнал — `None`, а не молчаливый ноль (иначе DSR занизил бы
    /// поправку на множественность).
    #[test]
    fn runs_csv_trial_count_comes_from_journal() {
        use crate::lob::runs::{append_run_row, RunKind, RunRow};

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("runs.csv");
        assert_eq!(
            trials_from_runs_csv(&path),
            Some(0),
            "до первого запуска 3.1 журнала нет — это ноль, а не недоступность"
        );
        for (kind, symbol) in [
            (RunKind::Pilot, "SOLUSDT"),
            (RunKind::Pilot, "HYPEUSDT"),
            (RunKind::Rerun, "SOLUSDT"),
            (RunKind::Debt, ""),
        ] {
            append_run_row(
                &path,
                &RunRow {
                    ts_utc: "2026-09-08T00:00:00Z".to_string(),
                    symbol: symbol.to_string(),
                    kind,
                    detail: "синтетика".to_string(),
                },
            )
            .unwrap();
        }
        assert_eq!(trials_from_runs_csv(&path), Some(3));
        std::fs::write(
            &path,
            "ts_utc,symbol,kind,detail\n2026-09-08T00:00:00Z,SOLUSDT,pilott,опечатка\n",
        )
        .unwrap();
        assert_eq!(trials_from_runs_csv(&path), None);
    }
}
