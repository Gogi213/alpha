use crate::alloc_count::measure;

/// Гейт GC: горячий цикл бутстрапа не аллоцирует на реплику.
///
/// Проверяется свойство, а не число: аллокации при `10 x R` репликах
/// обязаны совпасть с аллокациями при `R`. Абсолютный порог был бы
/// привязкой к текущей реализации и ломался бы от любой безобидной
/// правки подготовки данных; независимость от числа реплик — ровно то
/// утверждение, которое делает Decision 23 и которое ломается при
/// возврате к перестроению карты кластеров внутри цикла.
///
/// Без этого теста откат к прежней реализации не заметил бы никто:
/// все остальные тесты модуля проверяют статистический вывод, а он от
/// такого отката не меняется вовсе.
#[test]
fn the_replication_loop_does_not_allocate_per_replicate() {
    let obs: Vec<(i64, f64)> = (0..20)
        .flat_map(|g| (0..10).map(move |i| (g, 0.5 + 0.01 * (i as f64) + 0.02 * (g as f64))))
        .collect();

    let (r1, few) = measure(|| super::wild_cluster_bootstrap_t(&obs, 99, 0.005, 7));
    let (r2, many) = measure(|| super::wild_cluster_bootstrap_t(&obs, 999, 0.005, 7));
    assert!(r1.is_ok() && r2.is_ok(), "оба прогона обязаны считаться");

    assert_eq!(
        few.allocations, many.allocations,
        "в десять раз больше реплик дали {} аллокаций против {}:              цикл снова выделяет память на итерации",
        many.allocations, few.allocations
    );
}
use super::*;

/// Разрешение сетки — свойство перебора весовых векторов, а не
/// приближение: проверяем его против независимо посчитанной целой степени
/// `weight_values^clusters`, а не тавтологически той же формулой в `f64`.
#[test]
fn p_grid_resolution_matches_the_exact_power_for_several_cluster_counts() {
    for &g in &[7u32, 8, 9, 10, 12] {
        let got = webb_p_grid_resolution(g);
        let want = 1.0 / (WEBB_WEIGHT_VALUES as u128).pow(g) as f64;
        let rel_err = ((got - want) / want).abs();
        assert!(
            rel_err < 1e-9,
            "G={g}: получено {got}, ожидалось {want} (степень {})",
            WEBB_WEIGHT_VALUES
        );
    }
}

/// Арифметика, на которой стоит весь Decision 9: при `G = 7` радемахеровские
/// (двухточечные) веса дают сетку грубее альфы гейта, а веса Уэбба — нет.
/// Без этого теста разница между «128 векторов» и «279936 векторов»
/// осталась бы утверждением в комментарии, а не проверяемым свойством.
#[test]
fn rademacher_at_seven_clusters_is_coarser_than_gate_alpha_but_webb_is_not() {
    let rademacher = p_grid_resolution(7, 2);
    let webb = p_grid_resolution(7, WEBB_WEIGHT_VALUES);

    assert!(
        rademacher > GATE_ALPHA,
        "2^7 = 128 векторов знаков дают минимальный p ≈ 1/128 ≈ 0.0078, \
         грубее альфы гейта {GATE_ALPHA}, а получили {rademacher}"
    );
    assert!(
        webb <= GATE_ALPHA,
        "6^7 = 279936 векторов обязаны быть тоньше альфы гейта {GATE_ALPHA}, \
         а получили {webb}"
    );
}

/// FIX 3. PLAN.md шаг 7.2, done-condition дословно: «отдельный тест
/// печатает достижимое разрешение сетки p при `G = 7, 8, 9, 10` и падает,
/// если оно грубее 0.005». До этого теста ни один существующий тест не
/// делал именно этого:
/// `p_grid_resolution_matches_the_exact_power_for_several_cluster_counts`
/// сверяет формулу с независимо посчитанной степенью и ничего не
/// печатает; `rademacher_at_seven_clusters_...` сравнивает Радемахера с
/// Уэббом только при `G = 7`, а падать он обязан на радемахеровских
/// весах, не на уэббовских. Печать — не декорация: шапка модуля требует
/// это число видимым перед каждым вердиктом гейта G2, и тест обязан
/// печатать то же значение, которое увидит читатель отчёта, а не только
/// тайно проверять его.
#[test]
fn webb_p_grid_resolution_is_finer_than_gate_alpha_at_g_7_8_9_10() {
    for &g in &[7u32, 8, 9, 10] {
        let resolution = webb_p_grid_resolution(g);
        println!("G={g}: достижимое разрешение сетки p = {resolution}");
        assert!(
            resolution <= GATE_ALPHA,
            "G={g}: разрешение сетки {resolution} грубее альфы гейта {GATE_ALPHA} — \
             гейт G2 вернул бы методический красный именно из-за этого"
        );
    }
}

/// FIX 4. `p_grid_resolution` в линейном пространстве переполняет `f64`
/// (`weight_values^clusters > f64::MAX`) и превращается в побитово точный
/// `0.0`, начиная с конкретной, измеренной здесь границы, а не «где-то
/// около 400», как было в комментарии до измерения. Тест фиксирует саму
/// границу и проверяет, что логарифмическая версия остаётся конечной,
/// отрицательной и монотонно убывающей по обе её стороны — то есть
/// по-прежнему что-то сообщает читателю отчёта там, где линейная версия
/// уже нет.
#[test]
fn p_grid_resolution_underflows_to_zero_at_397_clusters_but_log10_stays_informative() {
    assert!(
        p_grid_resolution(396, WEBB_WEIGHT_VALUES) > 0.0,
        "на 396 кластерах линейное разрешение обязано быть ещё различимо от нуля"
    );
    assert_eq!(
        p_grid_resolution(397, WEBB_WEIGHT_VALUES),
        0.0,
        "на 397 кластерах 6^397 обязан переполнить f64 и дать точный ноль в числителе 1/x"
    );

    for &g in &[396u32, 397, 1000] {
        let log_resolution = webb_p_grid_resolution_log10(g);
        assert!(
            log_resolution.is_finite(),
            "G={g}: log10(resolution) обязан оставаться конечным числом там, где сама resolution уже 0.0"
        );
        assert!(
            log_resolution < 0.0,
            "G={g}: log10(resolution) обязан быть отрицательным, так как resolution < 1"
        );
    }
    assert!(
        webb_p_grid_resolution_log10(397) < webb_p_grid_resolution_log10(396),
        "разрешение обязано монотонно утончаться (log10 монотонно убывать) через границу переполнения"
    );
    assert!(
        webb_p_grid_resolution_log10(1000) < webb_p_grid_resolution_log10(397),
        "разрешение обязано продолжать монотонно утончаться и после границы переполнения"
    );
}

/// Строит наблюдения без автокорреляции: `clusters` кластеров-суток по
/// `per_cluster` штук, значение — `mean + sigma · гауссовский шум`.
/// Для быстрых тестов формы ответа этого достаточно; автокорреляция —
/// только там, где её прямо требует методика (калибровка размера ниже).
fn synthetic_clusters(
    clusters: i64,
    per_cluster: usize,
    mean: f64,
    sigma: f64,
    seed: u64,
) -> Vec<(i64, f64)> {
    let mut rng = SplitMix64::new(seed);
    let mut out = Vec::with_capacity(clusters as usize * per_cluster);
    for day in 0..clusters {
        for _ in 0..per_cluster {
            out.push((day, mean + sigma * next_gaussian(&mut rng)));
        }
    }
    out
}

/// То же самое, но со стационарным AR(1) внутри кластера при коэффициенте
/// `rho`: PLAN.md шаг 7.2 требует «синтетический ряд с известной
/// автокорреляцией» именно затем, чтобы проверить размер теста там, где
/// наблюдения внутри суток зависимы, — реальность markout внутри одного
/// дня, а не идеализация. Рекурсия `x_t = rho·x_{t-1} + sqrt(1-rho²)·шум`
/// держит дисперсию `x` равной единице на любом шаге, а не растущей.
fn synthetic_ar1_clusters(
    clusters: i64,
    per_cluster: usize,
    mean: f64,
    sigma: f64,
    rho: f64,
    seed: u64,
) -> Vec<(i64, f64)> {
    let mut rng = SplitMix64::new(seed);
    let mut out = Vec::with_capacity(clusters as usize * per_cluster);
    for day in 0..clusters {
        let mut x = next_gaussian(&mut rng);
        for i in 0..per_cluster {
            if i > 0 {
                x = rho * x + (1.0 - rho * rho).sqrt() * next_gaussian(&mut rng);
            }
            out.push((day, mean + sigma * x));
        }
    }
    out
}

/// Равномерное `[0, 1)` из верхних 53 бит источника — столько бит несёт
/// мантисса `f64`, младшие биты `SplitMix64` качественно не хуже старших,
/// но нет смысла тратить их на точность, которую `f64` всё равно не хранит.
fn next_f64_uniform(rng: &mut SplitMix64) -> f64 {
    (rng.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
}

/// Box-Muller: пара равномерных даёт одно гауссовское значение (вторую
/// половину пары не считаем — синтетическим тестовым данным экономия на
/// генераторе не нужна, а код проще без буферизации одного значения между
/// вызовами). `max(f64::MIN_POSITIVE)` — не даём `ln(0) = -inf` испортить
/// единственное на миллиарды попаданий значение шума.
fn next_gaussian(rng: &mut SplitMix64) -> f64 {
    let u1 = next_f64_uniform(rng).max(f64::MIN_POSITIVE);
    let u2 = next_f64_uniform(rng);
    (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
}

/// Один и тот же бинлог (здесь — один и тот же вход) с одним и тем же seed
/// обязан дать побайтово одинаковый вывод (ARCHITECTURE.md A2, «реплей
/// детерминирован»): без этого повтор гейта G2 на тех же данных мог бы
/// вынести другой вердикт, и предрегистрация потеряла бы смысл.
#[test]
fn same_data_and_seed_give_exactly_the_same_p_value() {
    let data = synthetic_clusters(20, 8, 3.0, 1.0, 7);
    let p1 = wild_cluster_bootstrap_t(&data, 999, GATE_ALPHA, 42).unwrap();
    let p2 = wild_cluster_bootstrap_t(&data, 999, GATE_ALPHA, 42).unwrap();
    assert_eq!(p1, p2, "одинаковый вход и seed обязаны дать одинаковый p");
}

/// Разные seed при том же входе не обязаны совпасть — иначе seed был бы
/// декорацией, а не источником энтропии реплик. Проверяем, что определение
/// детерминизма выше не вырождено в «функция всегда возвращает одно число».
/// Сигнал взят слабым нарочно: при сильном эффекте ни одна реплика ни при
/// каком seed не превышает наблюдённый `t`, и обе `p` совпадут по
/// формуле `1/(B+1)` не потому, что seed не влияет, а потому, что считать
/// уже нечего — это не проверило бы то, что должен проверять этот тест.
#[test]
fn different_seeds_can_give_different_p_values() {
    let data = synthetic_clusters(20, 8, 0.3, 1.0, 7);
    let p1 = wild_cluster_bootstrap_t(&data, 999, GATE_ALPHA, 1).unwrap();
    let p2 = wild_cluster_bootstrap_t(&data, 999, GATE_ALPHA, 2).unwrap();
    assert_ne!(p1, p2);
}

/// Меньше `G_MIN` кластеров обязано вернуть отказ, а не число и не
/// панику: PLAN.md явно требует различимого «методического красного»
/// именно как значение, которое вызывающий код обязан проверить.
#[test]
fn below_minimum_cluster_count_refuses_instead_of_returning_a_p_value() {
    let data: Vec<(i64, f64)> = (0..6).map(|day| (day, 1.0)).collect();
    let err = wild_cluster_bootstrap_t(&data, 99, GATE_ALPHA, 1).unwrap_err();
    assert_eq!(
        err,
        BootstrapError::TooFewClusters {
            clusters: 6,
            minimum: G_MIN
        }
    );
}

/// Ровно `G_MIN` (7, спека редакции 3, таск 01: `MIN_CLUSTERS`/
/// `CONFIRM_MIN_G` сведены к этой одной константе) обязано пройти отказ
/// по числу кластеров — граница включает минимум, а не только то, что
/// выше него. Раньше эта же выборка отказала бы: старое значение порога
/// (12) исключало ровно семь кластеров.
#[test]
fn exactly_g_min_clusters_passes_the_cluster_count_gate() {
    assert_eq!(G_MIN, 7, "порог назначен спекой, не подобран этим тестом");
    let data = synthetic_clusters(G_MIN as i64, 8, 3.0, 1.0, 7);
    assert!(wild_cluster_bootstrap_t(&data, 999, GATE_ALPHA, 1).is_ok());
}

/// Альфа тоньше достижимой сетки Уэбба обязана вернуть отказ, а не молча
/// посчитанный (и обязательно недостижимый) p. `1e-12` тоньше, чем
/// `1 / 6^12 ≈ 4.6e-10` — минимальный ненулевой p при 12 кластерах.
#[test]
fn alpha_finer_than_the_webb_grid_refuses_instead_of_returning_a_p_value() {
    let data = synthetic_clusters(12, 5, 1.0, 1.0, 3);
    let tiny_alpha = 1e-12;
    let err = wild_cluster_bootstrap_t(&data, 99, tiny_alpha, 7).unwrap_err();
    assert!(matches!(err, BootstrapError::GridTooCoarse { .. }));
}

/// Сильный положительный сигнал при низкой внутрикластерной дисперсии
/// обязан дать p не выше альфы гейта: иначе тест не отличает сигнал от
/// шума, и весь бутстрап бесполезен для G2.
#[test]
fn strong_positive_signal_with_low_variance_gives_a_small_p() {
    let data = synthetic_clusters(20, 8, 6.0, 1.0, 11);
    let p = wild_cluster_bootstrap_t(&data, BOOTSTRAP_REPLICATIONS, GATE_ALPHA, 100).unwrap();
    assert!(
        p <= GATE_ALPHA,
        "p = {p}, ожидался не выше альфы гейта {GATE_ALPHA} при сильном сигнале"
    );
}

/// Чистый шум с нулевым средним не обязан (и на разумных seed не должен)
/// проходить гейт: без этого теста предыдущий проверял бы только то, что
/// функция способна выдать маленький p, а не то, что она делает это
/// избирательно.
#[test]
fn pure_noise_does_not_give_a_small_p() {
    let data = synthetic_clusters(20, 8, 0.0, 1.0, 22);
    let p = wild_cluster_bootstrap_t(&data, BOOTSTRAP_REPLICATIONS, GATE_ALPHA, 200).unwrap();
    assert!(
        p > GATE_ALPHA,
        "p = {p}, чистый шум не должен проходить гейт с альфой {GATE_ALPHA}"
    );
}

/// FIX 1. Наблюдения тождественно равны нулю: каждый кластер имеет
/// нулевую внутрикластерную дисперсию, `sum_sq_cluster_residuals = 0`,
/// значит `variance = 0`, а `mean = 0`, то есть `t_obs = 0.0 / 0.0 =
/// NaN`. До починки `if t_star >= t_obs` при `t_obs = NaN` ложно для
/// абсолютно любой реплики (сравнение с NaN в IEEE-754 всегда false),
/// счётчик «не менее экстремальных» остаётся нулевым, и функция
/// возвращает минимальный достижимый p ≈ 1/(replications+1) —
/// максимальную значимость ровно там, где сигнала нет вообще.
/// Подтверждено эмпирически до починки: `p = Ok(0.001)` на этих же
/// данных. Гейт G2 обязан отказаться, а не соврать.
#[test]
fn all_zero_data_refuses_instead_of_reporting_spurious_significance() {
    let data: Vec<(i64, f64)> = (0..20)
        .flat_map(|day| (0..8).map(move |_| (day, 0.0)))
        .collect();
    let err = wild_cluster_bootstrap_t(&data, 999, GATE_ALPHA, 1).unwrap_err();
    assert!(
        matches!(err, BootstrapError::DegenerateVariance { .. }),
        "ожидался отказ по нулевой дисперсии на тождественно нулевых данных, получили {err:?}"
    );
}

/// То же самое, но с ненулевой константой: `mean = 7.0`, дисперсия всё
/// равно нулевая, поэтому `t_obs = 7.0 / 0.0 = +inf`. До починки
/// `t_star >= t_obs` истинно только если реплика тоже даёт `+inf` (тот
/// же вырожденный случай — вес не меняет нулевую внутрикластерную
/// дисперсию по построению), так что и здесь эмпирически было
/// `p = Ok(0.001)`, а не отказ.
#[test]
fn constant_nonzero_data_refuses_instead_of_reporting_spurious_significance() {
    let data: Vec<(i64, f64)> = (0..20)
        .flat_map(|day| (0..8).map(move |_| (day, 7.0)))
        .collect();
    let err = wild_cluster_bootstrap_t(&data, 999, GATE_ALPHA, 1).unwrap_err();
    assert!(
        matches!(err, BootstrapError::DegenerateVariance { .. }),
        "ожидался отказ по нулевой дисперсии на константных данных, получили {err:?}"
    );
}

/// Эмпирический размер на альфе гейта — done-condition PLAN.md шага 7.2
/// буквально: 20000 синтетических выборок с известной (AR(1), rho = 0.5)
/// автокорреляцией внутри кластера и истинным нулевым средним, доля
/// отказов при alpha = 0.005 обязана попасть в 0.005 ± 0.002. Это и есть
/// то самое «при той альфе, которую использует гейт, а не при удобной
/// 0.05» из done-condition.
///
/// Медленно: 20000 вызовов по `BOOTSTRAP_REPLICATIONS` реплик каждый.
/// Запуск вручную: `cargo test --release -- --ignored
/// bootstrap_empirical_size_matches_gate_alpha_within_tolerance`.
#[test]
#[ignore = "20000 * 9999 реплик — минуты в --release; запускать вручную для калибровки"]
fn bootstrap_empirical_size_matches_gate_alpha_within_tolerance() {
    const OUTER: u32 = 20_000;
    const RHO: f64 = 0.5;
    let mut rejections = 0u32;
    for i in 0..OUTER {
        let data = synthetic_ar1_clusters(20, 8, 0.0, 1.0, RHO, 1_000_000 + i as u64);
        let p = wild_cluster_bootstrap_t(
            &data,
            BOOTSTRAP_REPLICATIONS,
            GATE_ALPHA,
            2_000_000 + i as u64,
        )
        .expect("G=20 ≥ минимума и сетка Уэбба тоньше альфы — отказа здесь не бывает");
        if p <= GATE_ALPHA {
            rejections += 1;
        }
    }
    let empirical_size = f64::from(rejections) / f64::from(OUTER);
    // Печатается независимо от исхода (не только в сообщении assert при
    // провале): FIX 5 требует отчёт об измеренном числе, а не только
    // «прошёл/не прошёл», и следующий, кто перекалибрует альфу или
    // BOOTSTRAP_REPLICATIONS, обязан видеть само число на `--nocapture`.
    println!(
        "эмпирический размер = {empirical_size} ({rejections}/{OUTER} отказов), \
         ожидалось {GATE_ALPHA} ± 0.002"
    );
    assert!(
        (empirical_size - GATE_ALPHA).abs() <= 0.002,
        "эмпирический размер {empirical_size}, ожидалось {GATE_ALPHA} ± 0.002"
    );
}

/// Дымовая версия предыдущего теста для дефолтного прогона: тот же
/// генератор и та же альфа, но на порядок меньше внешних повторов и
/// бутстрап-реплик. Это не проверка калибровки (для неё — игнорируемый
/// тест выше, с его допуском и его числом повторов), а грубая проверка,
/// что размер не разъехался на порядки — именно такую поломку дешёвый
/// прогон обязан ловить.
#[test]
fn bootstrap_empirical_size_smoke_is_in_the_right_ballpark() {
    const OUTER: u32 = 200;
    const REPLICATIONS: u32 = 499;
    const RHO: f64 = 0.5;
    let mut rejections = 0u32;
    for i in 0..OUTER {
        let data = synthetic_ar1_clusters(20, 8, 0.0, 1.0, RHO, 3_000_000 + i as u64);
        let p = wild_cluster_bootstrap_t(&data, REPLICATIONS, GATE_ALPHA, 4_000_000 + i as u64)
            .expect("G=20 ≥ минимума и сетка Уэбба тоньше альфы — отказа здесь не бывает");
        if p <= GATE_ALPHA {
            rejections += 1;
        }
    }
    let empirical_size = f64::from(rejections) / f64::from(OUTER);
    // 200 повторов сами по себе дают биномиальный шум порядка
    // sqrt(0.005 · 0.995 / 200) ≈ 0.005 вокруг истинных 0.005 — этот тест
    // не может (и не должен) держать допуск ±0.002 игнорируемого теста.
    assert!(
        empirical_size <= 0.05,
        "эмпирический размер {empirical_size} на порядок выше альфы {GATE_ALPHA} — \
         похоже на поломку, а не на статистический шум"
    );
}
