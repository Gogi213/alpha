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

/// `unit_variance_trials` — точная выборочная дисперсия `1.0` (делитель
/// `n−1`), не приближённая: гейт G-POWER-A печатает `SR0` без
/// пробных прогонов, и это единственное, что делает такую печать честной.
#[test]
fn unit_variance_trials_has_exact_sample_variance_one() {
    for n in [2usize, 3, 5, 42, 179] {
        let trials = unit_variance_trials(n);
        assert_eq!(trials.len(), n);
        let mean = trials.iter().sum::<f64>() / count_f64(n);
        assert!(close(mean, 0.0, 1e-9), "n={n} mean={mean}");
        let var = trials.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / count_f64(n - 1);
        assert!(close(var, 1.0, 1e-9), "n={n} var={var}");
    }
}

/// Критерий приёмки таска 05: `SR0` гейта G-POWER-A на известном `N`
/// совпадает с ручным расчётом по формуле из докстроки
/// `expected_sharpe_under_null` при `V = 1` — обёртка не меняет формулу.
#[test]
fn sr0_for_trial_count_matches_hand_formula() {
    for n in [1usize, 2, 42, 179] {
        let nf = count_f64(n);
        let expected = if n <= 1 {
            0.0
        } else {
            let q1 = normal_inv_cdf(1.0 - 1.0 / nf);
            let q2 = normal_inv_cdf(1.0 - 1.0 / (nf * std::f64::consts::E));
            (1.0 - EULER_MASCHERONI) * q1 + EULER_MASCHERONI * q2
        };
        let got = expected_sharpe_under_null_for_trial_count(n).unwrap();
        assert!(
            close(got, expected, 1e-9),
            "n={n} got={got} expected={expected}"
        );
    }
    assert_eq!(expected_sharpe_under_null_for_trial_count(0), None);
}

/// Критерий приёмки таска 05: при `N = 1` требуемый Шарп совпадает с
/// недефлированным порогом — при одном испытании `SR0 = 0` и квадратное
/// уравнение вырождается в `x = z/√(T−1−0.5·z²)` без штрафа за отбор.
#[test]
fn required_sharpe_at_n_one_matches_undeflated_threshold() {
    let z = normal_inv_cdf(0.95);
    let t1 = 99.0; // num_obs - 1 = 100 - 1
    let undeflated = z / (t1 - 0.5 * z * z).sqrt();
    let got = required_sharpe_for_dsr(1, 100, 0.95).unwrap();
    assert!(
        close(got, undeflated, 1e-9),
        "got={got} undeflated={undeflated}"
    );
    // Подставленный назад в dsr() с тем же (нулевым) SR0 обязан дать
    // ровно 0.95 — независимая проверка через сам гейт, не переформулировка.
    let back = dsr(got, 100, 0.0, 3.0, &[0.0]).unwrap();
    assert!(close(back, 0.95, 1e-6), "back={back}");
}

/// Больше испытаний — выше планка: требуемый Шарп при 179 испытаниях
/// обязан быть строго больше, чем при одном (штраф за множественность
/// работает в правильную сторону и здесь, не только в `dsr`).
#[test]
fn required_sharpe_grows_with_trial_count() {
    let one = required_sharpe_for_dsr(1, 100, 0.95).unwrap();
    let many = required_sharpe_for_dsr(179, 100, 0.95).unwrap();
    assert!(many > one, "one={one} many={many}");
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
/// 100 → 101 даёт 92.5 bps чистыми, пусто и битый круг — отказ. Круги —
/// полное исполнение (F3), `entry_vwap == entry_px`.
#[test]
fn per_trade_net_uses_backtest_roundtrip_without_copying_it() {
    let fill = |dir: i8, entry_px: f64, exit_px: f64| Fill {
        dir,
        entry_px,
        exit_px,
        qty: 1.0,
        entry_taker: false,
        exit_taker: true,
        entry_vwap: entry_px,
        fill_frac: 1.0,
        legs_filled: 1,
        fill_by_cross: false,
    };
    let fills = [fill(1, 100.0, 101.0), fill(1, 100.0, 99.0)];
    let nets = per_trade_net_bps(&fills).unwrap();
    assert!(close(nets[0], 95.59, 1e-9));
    assert!(close(nets[1], -104.41, 1e-9));
    assert_eq!(per_trade_net_bps(&[]), None);
    let bad = [fill(0, 100.0, 101.0)];
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

/// `dsr_for_trial_count` — та же формула, что `dsr` на синтетическом
/// срезе `unit_variance_trials`, не переформулировка: подстановка того же
/// среза напрямую в `dsr` обязана дать то же число.
#[test]
fn dsr_for_trial_count_matches_dsr_on_unit_variance_trials() {
    let direct = dsr(1.2, 100, 0.0, 3.0, &unit_variance_trials(50));
    let via_wrapper = dsr_for_trial_count(1.2, 100, 0.0, 3.0, 50);
    assert_eq!(direct, via_wrapper);
    assert!(via_wrapper.unwrap().is_finite());
}

/// Джекнайф: три оценки дают известные `min`/`max`/`range`; меньше двух
/// оценок или неконечное число — отказ, а не подделанная чувствительность.
#[test]
fn jackknife_sensitivity_reports_known_range() {
    let points = vec![
        ("2026-05-01".to_string(), 3.0),
        ("2026-05-02".to_string(), 1.0),
        ("2026-05-03".to_string(), 4.0),
    ];
    let got = jackknife_sensitivity(&points).expect("три точки — достаточно");
    assert!(close(got.min, 1.0, 1e-12));
    assert!(close(got.max, 4.0, 1e-12));
    assert!(close(got.range, 3.0, 1e-12));
    assert_eq!(got.leave_one_out, points);
    assert_eq!(
        jackknife_sensitivity(&[("2026-05-01".to_string(), 1.0)]),
        None,
        "одна точка — не из чего мерить чувствительность"
    );
    assert_eq!(
        jackknife_sensitivity(&[("a".to_string(), 1.0), ("b".to_string(), f64::NAN)]),
        None,
        "неконечная оценка — отказ, а не подделанный размах"
    );
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

/// CPCV проверяет **процедуру отбора**, а не профиль (задача §6.4 п. 4,
/// `SETTLED.md` В-19): правило отбора — лучший по среднему на
/// IS-периодах, то же, которым выбирает шорт-лист. Оба ответа выведены
/// руками. Матрица, где одно испытание стабильно лучшее и стабильно
/// прибыльное, обязана дать **положительный** средний OOS Sharpe:
/// правило берёт его в обеих складках, а его OOS-сутки все
/// положительны. Матрица-качели, где каждое испытание выигрывает IS
/// ровно той половиной, на которой оно прибыльно, обязана дать
/// **отрицательный**: выбранное на IS проваливает OOS — ровно то, ради
/// чего CPCV и считают, и по одному профилю это не видно вовсе.
#[test]
fn cpcv_selection_separates_a_steady_winner_from_a_seesaw() {
    let best_mean = |trials: &[Vec<f64>], is: &[usize]| -> Option<usize> {
        let mut best: Option<(usize, f64)> = None;
        for (i, row) in trials.iter().enumerate() {
            let vals: Vec<f64> = is.iter().filter_map(|&j| row.get(j).copied()).collect();
            let Some(m) = moments(&vals) else { continue };
            if best.is_none_or(|(_, b)| m.mean > b) {
                best = Some((i, m.mean));
            }
        }
        best.map(|(i, _)| i)
    };

    let steady = vec![
        vec![2.0, 2.1, 1.9, 2.05, 2.0, 1.95, 2.1, 2.0],
        vec![-1.0, -1.2, -0.8, -1.1, -0.9, -1.3, -1.0, -1.1],
        vec![0.1, -0.2, 0.3, -0.4, 0.2, -0.1, 0.4, -0.3],
    ];
    let v = cpcv_selection_oos_sharpe(&steady, REPORT_CPCV_PARAMS, best_mean)
        .expect("восемь суток, две складки — оценка обязана состояться");
    assert!(v > 0.0, "стабильный победитель обязан пережить OOS: {v}");

    let seesaw = vec![
        vec![-3.0, -2.0, -3.5, -2.5, 2.0, 3.0, 2.5, 3.5],
        vec![2.0, 3.0, 2.5, 3.5, -3.0, -2.0, -3.5, -2.5],
    ];
    let v = cpcv_selection_oos_sharpe(&seesaw, REPORT_CPCV_PARAMS, best_mean)
        .expect("та же форма входа — оценка обязана состояться");
    assert!(
        v < 0.0,
        "победитель IS обязан провалить OOS на качелях: {v}"
    );
}

/// Параметры CPCV сводного отчёта и наименьшая длина ряда, при которой
/// они применимы. Ожидание взято из правила, записанного в doc
/// `cpcv_mean_oos_sharpe` («складка короче двух наблюдений после сброса
/// — отказ»), а не из кода под тестом: длина складки `len / folds`,
/// сброс съедает `purge + embargo`, значит наименьший ряд —
/// `folds · (purge + embargo + 2)`. При `REPORT_CPCV_PARAMS` это ровно
/// четверо суток: на них оценка обязана состояться, на трёх — отказ.
#[test]
fn report_cpcv_params_admit_exactly_four_daily_observations() {
    assert_eq!(min_obs_for_cpcv(REPORT_CPCV_PARAMS), 4);
    let four = [1.0, -0.5, 2.0, 0.25];
    assert!(cpcv_mean_oos_sharpe(&four, REPORT_CPCV_PARAMS).is_some());
    assert!(cpcv_mean_oos_sharpe(&four[..3], REPORT_CPCV_PARAMS).is_none());
}
