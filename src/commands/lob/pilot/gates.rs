//! Гейты боевого пилота: G0 дословно по `PLAN.md` §6, разрыв G-POWER-B,
//! число сессий на профиль и каркас предрегистрации этапа 2 (В-29).
//! Отдельно от `pilot.rs`: чистые функции вердикта без ввода-вывода —
//! их тесты на синтетике не трогают ни сеть, ни бинлоги.

use crate::commands::lob::G0_MIN_PULLED;
use crate::lob::costs::ROUNDTRIP_FEES_BPS;
use crate::stats::count_f64_u64;

use super::metrics::{format_opt_bps, is_positive_finite, median};

/// Число сессий, нужное профилю, чтобы набрать `CONFIRM_MIN_N` (100)
/// наблюдений при измеренной ставке (история 41, `PLAN.md` §2.4): `ceil(n_min
/// / (rate_per_min · session_minutes))`. `None` — ставка или длина сессии
/// неположительны (профиль на этой ставке не считается, а не делится на
/// ноль).
pub fn sessions_needed_for_profile(
    rate_per_min: f64,
    session_minutes: f64,
    n_min: u64,
) -> Option<u64> {
    if !is_positive_finite(rate_per_min) || !is_positive_finite(session_minutes) {
        return None;
    }
    let per_session = rate_per_min * session_minutes;
    if per_session <= 0.0 {
        return None;
    }
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    let sessions = (count_f64_u64(n_min) / per_session).ceil() as u64;
    Some(sessions.max(1))
}

/// Гейт G0 дословно (`PLAN.md` §6): (1) медианный инструмент ≥
/// `G0_MIN_PULLED` (200 — значение гейта; имя константы унаследовано от
/// отменённого дизайна «pulled», её место — `mod.rs`, не в зоне этого
/// таска) уровней за зачтённый час; (2) `m` на 10 с на объединённой
/// выборке ≥ круговых издержек **плюс проскальзывание**. Издержки и
/// проскальзывание по Decision 15 уже посчитаны на каждое наблюдение
/// (`costs::net_bps`, потребитель — `net_observations`/`mean_net_bps` в
/// `process_instrument`), поэтому условие (2) — `pooled_net_mean_bps >= 0`,
/// а не сырое `m10s >= ROUNDTRIP_FEES_BPS`: последнее пропускало бы
/// слагаемое проскальзывания (дозапрос по ревью таска 09(а), ось Данные) —
/// на тонком инструменте с широким спредом высокий `m` легко проигрывает
/// своему же проскальзыванию. Передавать сюда **`net`**, не `m`.
/// Продление до 6 часов один раз — решение вызывающего по этому
/// результату, не эта функция.
pub fn g0_verdict(
    levels_per_hour_by_instrument: &[f64],
    pooled_net_mean_bps: Option<f64>,
) -> String {
    let mut sorted = levels_per_hour_by_instrument.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let med = median(&sorted);
    let rate_pass = med.is_some_and(|m| m >= count_f64_u64(G0_MIN_PULLED));
    let edge_pass = pooled_net_mean_bps.is_some_and(|net| net >= 0.0);
    if !rate_pass {
        return format!(
            "G0 RED (sparse): медианная ставка {} < {G0_MIN_PULLED}/ч — продление до 6 часов один раз",
            med.map(|m| format!("{m:.1}/ч")).unwrap_or_else(|| "n/a".to_string())
        );
    }
    if !edge_pass {
        return format!(
            "G0 RED (edge): объединённый net (m_10s − {ROUNDTRIP_FEES_BPS:.1}bps − проскальзывание) {} < 0",
            format_opt_bps(pooled_net_mean_bps)
        );
    }
    format!(
        "G0 pass: медианная ставка {:.1}/ч >= {G0_MIN_PULLED}, объединённый net {} >= 0",
        med.unwrap_or(0.0),
        format_opt_bps(pooled_net_mean_bps)
    )
}

/// Разрыв G-POWER-B (история 40): требуемый Шарп (`lob power`) минус
/// замеренный. Положительное число — разрыв, ноль или отрицательное —
/// планка достигнута.
pub fn power_b_gap(measured_sharpe: f64, required_sharpe: f64) -> f64 {
    required_sharpe - measured_sharpe
}

/// Каркас предрегистрации, этап 2 (`SETTLED.md` В-29, `PLAN.md` §9): что
/// войдёт в файл, который владелец коммитит **после** двухчасового боевого
/// пилота и **до** первой сессии сбора — режим `H3` и его `k`, границы оси
/// повторяемости, глубина креста профилей, число сессий на профиль,
/// правило остановки в сессиях, хост и его RTT. Печатает только **форму** —
/// имена полей и откуда каждое берётся, ни одного числа, за одним
/// исключением (таск 18, В-30/D05): `selected_h3` — пара `(режим, k)`,
/// которую даёт сетка `K_GRID` этого же боевого пилота (`choose_k`), а не
/// изобретённое число; `None` — сетка не выбрала ни один `k` («не
/// определим» — красный по построению), и строки остаются плейсхолдером,
/// как раньше. Остальные поля числа не получают: их источник (`lob probe`,
/// границы повторяемости, …) этот таск не считает — см. `CONCERNS`.
pub fn stage2_preregistration_skeleton(selected_h3: Option<(&str, f64)>) -> String {
    let h3_mode_line = match selected_h3 {
        Some((mode, _)) => {
            format!("  h3_mode: {mode} — наименьшее k сетки K_GRID (В-30/D05), прошедшее G0 и G1 на этом пилоте")
        }
        None => "  h3_mode: <floor|percentile — какой дал заявленный G0/G-POWER-B на этом пилоте>"
            .to_string(),
    };
    let h3_k_line = match selected_h3 {
        Some((_, k)) => {
            format!("  h3_k: {k} — наименьшее k сетки K_GRID (В-30/D05), прошедшее G0 и G1 на этом пилоте")
        }
        None => {
            "  h3_k: <k для --h3-k — сравнение ставок floor/percentile на этом пилоте>".to_string()
        }
    };
    [
        "предрегистрация, этап 2 (В-29) — коммитом после боевого пилота, до первой сессии сбора:"
            .to_string(),
        h3_mode_line,
        h3_k_line,
        "  repeat_axis_bounds: <границы корзин повторяемости 1 / 2 / >=3 — по распределению repeat_count>".to_string(),
        "  profile_grid_depth: <глубина креста профилей — из ставки уровней в минуту>".to_string(),
        "  sessions_per_profile: <sessions_needed_for_profile(ставка, длина сессии, CONFIRM_MIN_N)>".to_string(),
        "  stopping_rule_sessions: <правило остановки сбора в сессиях>".to_string(),
        "  host_id: <хост, на котором мерялась RTT>".to_string(),
        "  rtt_median_ns / rtt_p95_ns: <lob probe / clock.csv этого пилота>".to_string(),
        "  committed_after_pilot_run: <путь и момент боевого пилота, который дал эти числа>".to_string(),
    ]
    .join("\n")
}
