//! Колонки уровня R1 (TK-025): малые синтетические последовательности, ручной счёт по всем 21
//! колонкам `LEVEL_NAMES` на кадре взвода (`arm_ms`) у всех подходов, чем бы они ни кончились.
//!
//! Все сценарии заданы для бид-стены 10 000 и зеркалятся вокруг 20 000 в аск-стену (`Rig::m`): цена
//! стены остаётся 10 000, поэтому расстояния в bps и ожидаемые значения у зеркала те же. Кадр монеты —
//! пара проходов в порядке живой ленты: сначала бид, потом аск (`Rig::both`). Бид-проход видит чужую
//! лучшую цену прошлого кадра, аск-проход — свежую бид-цену: на кадре, где чужая цена переходит из
//! «далеко» в «близко», стена бид-варианта взводится кадром позже зеркала. Сценарии это обходят: стена
//! на переходе проседает ниже порога (`size < H3`) — взвода нет ни в одном варианте, — а где от
//! стороны зависит отсчёт («далеко» последний раз), ожидание своё для `m`.

use super::*;
use crate::lob::r1::{ArmR1, LEVEL_N, LEVEL_NAMES, R1_UNDEF};

const U: i64 = R1_UNDEF;
const HOUR: i64 = 3_600_000;
const WALL_TICK: i64 = 10_000;

/// Чужая лучшая цена дальше `2·D` (60 тиков при D = 20 bps), далее чужая стена 20 лотов.
const FAR: [(i64, i64); 2] = [(10060, 4), (10080, 20)];
/// Чужая лучшая цена в полосе подхода (15 тиков), чужая стена та же.
const NEAR: [(i64, i64); 2] = [(10015, 4), (10080, 20)];
/// Чужая лучшая цена в полосе, чужой стены нет (лоты 4 < H3 = 5).
const NEAR_BARE: [(i64, i64); 1] = [(10015, 4)];

fn lv(tick: i64, size: i64) -> LevelObs {
    LevelObs {
        tick,
        size_lots: size,
        in_top50: true,
    }
}

/// Порог `H3 = 5` лотов, полоса подхода 20 bps, пол возраста взвода 500 мс.
fn cfg_r1(approach: Option<i64>) -> LevelsConfig {
    LevelsConfig {
        mode: H3Mode::Floor { h3_lots: 5 },
        warmup_ms: 0,
        repeat_window_ms: HOUR,
        approach_bps: approach,
        approach_min_age_ms: 500,
    }
}

/// Цена в зеркале вокруг цены стены: тик стены остаётся на месте.
fn px(m: bool, t: i64) -> i64 {
    if m {
        2 * WALL_TICK - t
    } else {
        t
    }
}

/// Трекер с накопленным выходом; `m = true` — сценарий зеркалится (стена на аске).
struct Rig {
    tr: LevelTracker,
    m: bool,
    out: Vec<LevelRecord>,
    touches: Vec<TouchRecord>,
    ap: Vec<ApproachRecord>,
}

impl Rig {
    fn new(m: bool, r1: bool) -> Self {
        let mut tr = LevelTracker::new(cfg_r1(Some(20)));
        if r1 {
            tr.enable_r1();
        }
        Self {
            tr,
            m,
            out: Vec::with_capacity(16),
            touches: Vec::with_capacity(16),
            ap: Vec::with_capacity(16),
        }
    }

    /// Кадр монеты на метке `ts`: `wall` — книга стороны стены, `opp` — чужой, пары (тик, лоты) бид-варианта,
    /// лучшая цена первой. Проходы идут парой: бид, затем аск.
    fn both(&mut self, ts: i64, wall: &[(i64, i64)], opp: &[(i64, i64)]) {
        let to_obs = |book: &[(i64, i64)]| -> Vec<LevelObs> {
            book.iter().map(|&(t, s)| lv(px(self.m, t), s)).collect()
        };
        let (wall_obs, opp_obs) = (to_obs(wall), to_obs(opp));
        let books = if self.m {
            [opp_obs, wall_obs]
        } else {
            [wall_obs, opp_obs]
        };
        for (book, side) in books.iter().zip([Side::Bid, Side::Ask]) {
            self.tr.observe_frame_with_approaches(
                ts,
                side,
                book,
                &mut self.out,
                &mut self.touches,
                &mut self.ap,
            );
        }
    }

    /// Сделка против стены (агрессор с чужой для стены стороны).
    fn hit(&mut self, ts: i64, tick: i64, lots: i64) {
        self.tr.observe_trade(TradeHit {
            tick: px(self.m, tick),
            lots,
            aggressor_is_buy: self.m,
            block: false,
            rpi: false,
            exch_ms: ts,
        });
    }
}

fn arm_cols(a: &ApproachRecord) -> ArmR1 {
    *a.r1.clone().expect("на кадре взвода колонки R1 заполнены")
}

fn level_cols(a: &ApproachRecord) -> [i64; LEVEL_N] {
    arm_cols(a).level
}

fn assert_cols(got: &[i64; LEVEL_N], want: &[i64; LEVEL_N]) {
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        assert_eq!(g, w, "колонка {}", LEVEL_NAMES[i]);
    }
}

/// Бид-стена 10 000 до кадра взвода 12 400 включительно (подход ещё открыт).
///
/// Кадры (в скобках — стена, лоты; впереди неё лучшая цена и зона):
/// 1000 рождение (10), впереди 10 010 с 3 лотами; 2000 то же; 3000 (14, +4), зона: 10 008 с 2;
/// 4000 (12, −2), зона: 10 008 с 4 и 10 005 с 3 — рост зоны 3; 4500 сделка 1 лот;
/// 5000 (8, −4 за вычетом сделки −3); 11 000 лучшая цена ушла на 10 006, стена 7 (−1);
/// 12 000 чужая лучшая цена пришла в полосу, стена проседает до 4 (−3) — взвода нет;
/// 12 400 стена 7 (+3) — взвод. До 12 000 чужая лучшая цена далеко, поэтому до перехода взвода нет.
fn main_prefix(m: bool, r1: bool) -> Rig {
    let mut r = Rig::new(m, r1);
    r.both(1000, &[(10010, 3), (10000, 10)], &FAR);
    r.both(2000, &[(10010, 3), (10000, 10)], &FAR);
    r.both(3000, &[(10010, 3), (10008, 2), (10000, 14)], &FAR);
    r.both(
        4000,
        &[(10010, 3), (10008, 4), (10005, 3), (10000, 12)],
        &FAR,
    );
    r.hit(4500, 10000, 1);
    r.both(
        5000,
        &[(10010, 3), (10008, 4), (10005, 3), (10000, 8)],
        &FAR,
    );
    r.both(11_000, &[(10006, 3), (10005, 3), (10000, 7)], &FAR);
    r.both(12_000, &[(10006, 3), (10005, 3), (10000, 4)], &NEAR);
    r.both(
        12_400,
        &[(10006, 3), (10005, 3), (10000, 7), (9992, 2), (9985, 2)],
        &NEAR,
    );
    r
}

/// Продолжение 1: стена становится лучшей ценой — касание на 13 000.
fn run_main(m: bool, r1: bool) -> Rig {
    let mut r = main_prefix(m, r1);
    r.both(13_000, &[(10000, 7)], &NEAR);
    r
}

/// Ожидание `run_main` на кадре взвода 12 400, колонки по порядку `LEVEL_NAMES`; `since_far_ms` зависит от `m`.
const MAIN_WANT: [i64; LEVEL_N] = [
    3,      // cancel_1s: секунда 12 — отмена 3 на кадре 12 000 (проседание)
    4,      // cancel_3s: секунды 10..12 — 1 (кадр 11 000) + 3
    U,      // cancel_60m: стена моложе часа
    9,      // cancel_life: 2 + 3 + 1 + 3
    4,      // wall_add_max_lots: +4 на кадре 3000 (+3 на кадре взвода меньше)
    9_400,  // wall_add_max_age_ms: 12 400 − 3000
    3,      // front_add_max_lots: 10 005 новая с 3 лотами (10 008: +2) на кадре 4000
    8_400,  // front_add_max_age_ms: 12 400 − 4000
    U,      // born_shift_cbps: стена не переустановка
    U,      // born_shift_lots
    U,      // prev_death_gap_ms: на этой цене смертей не было
    U,      // prev_death_outcome
    4_666,  // size_share15_bp: 7·10⁴ / (3 + 3 + 7 + 2); 9985 на 21 тик от лучшей, не в окне
    4,      // nz_levels15
    0,      // best_move_1s_cbps: за 1 с после рождения лучшая цена не двигалась
    -400,   // best_move_10s_cbps: 10 010 → 10 006 против стены (кадр 11 000): −4 тика = −400 cbps
    8_000,  // opp_wall_dist_cbps: чужая стена 10 080, 80 bps
    28_571, // opp_wall_ratio_bp: 20 лотов · 10⁴ / 7 лотов
    3,   // frontrun_delta_10s: 6 (впереди на кадре 11 000) − 3 (на кадре 2000, ≤ 12 400 − 10 000)
    1,   // frontrun_levels: между лучшей ценой 10 006 и стеной занята 10 005
    400, // since_far_ms: бид — стала «далеко» по прошлой цене на кадре 12 000; зеркало ниже
];

#[test]
fn main_scenario_matches_hand_count_on_both_sides() {
    for m in [false, true] {
        let r = run_main(m, true);
        assert_eq!(r.ap.len(), 1, "m={m}");
        assert_eq!(r.ap[0].arm_ms, 12_400, "m={m}");
        assert_eq!(r.ap[0].disarm_reason, ApproachEnd::Touch);
        assert_eq!(r.ap[0].disarm_ms, 13_000);
        let mut want = MAIN_WANT;
        if m {
            // Аск-проход видит свежую бид-цену: «далеко» последний раз на кадре 11 000, не 12 000.
            want[20] = 1_400;
        }
        assert_cols(&level_cols(&r.ap[0]), &want);
    }
}

/// Н1: колонки потока берутся на аск-проходе, когда обе половины книги на метке кадра, — у зеркальных
/// стен они совпадают.
#[test]
fn flow_columns_are_identical_for_the_mirror_walls() {
    let bid = arm_cols(&run_main(false, true).ap[0]).flow;
    let ask = arm_cols(&run_main(true, true).ap[0]).flow;
    assert_eq!(bid, ask);
    assert!(bid.iter().any(|&v| v != U), "колонки потока не пусты");
}

/// Причинность: колонки подхода берутся на кадре взвода — те же кадры и сделки до него и разное будущее
/// (касание / крупные сделки, рост стены и смерть) дают побайтно те же колонки.
#[test]
fn columns_do_not_depend_on_the_future_of_the_approach() {
    for m in [false, true] {
        let touch = run_main(m, true);
        let mut dead = main_prefix(m, true);
        dead.hit(12_600, 10_000, 6);
        dead.both(13_000, &[(10006, 3), (10005, 3), (10000, 30)], &NEAR);
        dead.both(14_000, &[(10006, 3), (10005, 3)], &NEAR);
        assert_eq!(touch.ap.len(), 1, "m={m}");
        assert_eq!(dead.ap.len(), 1, "m={m}");
        assert_eq!(touch.ap[0].disarm_reason, ApproachEnd::Touch);
        assert_eq!(dead.ap[0].disarm_reason, ApproachEnd::LevelDeath);
        assert_eq!(touch.ap[0].arm_ms, dead.ap[0].arm_ms);
        assert_eq!(arm_cols(&touch.ap[0]), arm_cols(&dead.ap[0]), "m={m}");
    }
}

/// Бид-стена 10 000, умершая и снова родившаяся: прошлая смерть с исходом по правилу 70/20.
/// Оба подхода несут колонки на своём кадре взвода: первый кончается смертью, второй — касанием.
fn run_death(m: bool, r1: bool, traded: i64) -> Rig {
    let mut r = Rig::new(m, r1);
    r.both(1000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
    r.both(2000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
    if traded > 0 {
        r.hit(2500, 10000, traded);
    }
    r.both(3000, &[(10010, 3)], &NEAR_BARE);
    r.both(5000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
    r.both(6000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
    r.both(9000, &[(10000, 10)], &NEAR_BARE);
    r
}

#[test]
fn previous_death_gap_and_outcome_follow_the_70_20_rule() {
    // Исход: съедена (≥ 70% максимума), снята (≤ 20%), между.
    for (traded, outcome) in [(7, 1), (0, 0), (5, 2)] {
        for m in [false, true] {
            let r = run_death(m, true, traded);
            assert_eq!(r.ap.len(), 2, "traded={traded} m={m}");
            assert_eq!(r.ap[0].disarm_reason, ApproachEnd::LevelDeath);
            assert_eq!(r.ap[0].arm_ms, 2000);
            assert_eq!(r.ap[1].disarm_reason, ApproachEnd::Touch);
            assert_eq!(r.ap[1].arm_ms, 6000);
            // Первый подход, снятый смертью, несёт колонки своего взвода (2000): прошлых смертей нет.
            let first = [
                0, U, U,
                0, // отмен нет; 3 с монета ещё не наблюдается (1000 мс)
                0, U, 0, U, // прироста нет
                U, U, // не переустановка
                U, U, // смертей на этой цене не было
                7_692, 2, // 10·10⁴ / (3 + 10); занято 10 010 и 10 000
                0,
                U, // 1 с — та же лучшая цена; возраст 1 с до 10 с не дорос
                U, U, // чужих стен нет
                U,
                0,     // 10 с назад уровня ещё не было; впереди одна цена — зона пуста
                1_000, // since_far: далеко не было, от рождения на 1000
            ];
            assert_cols(&level_cols(&r.ap[0]), &first);
            let second = [
                0, 0, U,
                0, // отмены новой жизни: не было; окно 3 с наблюдается (5000 мс)
                0, U, 0, U, // прироста нет
                U, U, // не переустановка
                3_000, outcome, // смерть на 3000, взвод на 6000
                7_692, 2, // та же форма стека
                0, U, // 1 с: та же лучшая цена; возраст 1 с
                U, U, // чужих стен нет
                U, 0,     // 10 с назад уровня ещё не было
                1_000, // since_far: от рождения на 5000 до взвода 6000
            ];
            assert_cols(&level_cols(&r.ap[1]), &second);
        }
    }
}

/// Бид-стена 10 000 переустановлена: на кадре 2000 она исчезает, а на `new_tick` встаёт стена
/// `new_size` (рождение той же стороны, цена ±1 тик, размер в пределах 2×).
fn run_reprice(m: bool, r1: bool, new_tick: i64, new_size: i64) -> Rig {
    let mut r = Rig::new(m, r1);
    r.both(1000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
    r.both(2000, &[(10010, 3), (new_tick, new_size)], &NEAR_BARE);
    r.both(3000, &[(10010, 3), (new_tick, new_size)], &NEAR_BARE);
    r.both(4000, &[(new_tick, new_size)], &NEAR_BARE);
    r
}

#[test]
fn born_shift_is_from_the_dead_price_and_signed_away_from_the_market() {
    // (новый тик, размер, ожидаемый сдвиг cbps, ожидаемая разница лотов, доля стены в окне 15 тиков).
    let cases = [
        (10_001, 12, -100, 2, 8_000), // бид вверх, к цене: −1 bps от 10 000; 12·10⁴ / 15
        (9_999, 12, 100, 2, 8_000),   // бид вниз, от цены
        (10_001, 6, -100, -4, 6_666), // меньше прежней, но в пределах 2×; 6·10⁴ / 9
        (10_001, 25, U, U, 8_928),    // больше 2× — не переустановка; 25·10⁴ / 28
    ];
    for (tick, size, cbps, lots, share) in cases {
        for m in [false, true] {
            let r = run_reprice(m, true, tick, size);
            assert_eq!(r.ap.len(), 1, "tick={tick} size={size} m={m}");
            assert_eq!(r.ap[0].arm_ms, 3000);
            let want = [
                0, U, U,
                0, // отмен нет; окно 3 с: монета наблюдается 2000 мс — не определено
                0, U, 0, U, // с рождения прироста нет
                cbps, lots, //
                U, U, // на новой цене смертей не было
                share, 2, // стена и 10 010 в окне формы
                0,
                U, // 1 с — та же лучшая цена; возраст 1 с до 10 с не дорос
                U, U, // чужих стен нет
                U,
                0,     // 10 с назад уровня ещё не было; зона пуста
                1_000, // since_far: от рождения на 2000 до взвода 3000
            ];
            assert_cols(&level_cols(&r.ap[0]), &want);
        }
    }
}

/// Цена уходит дальше `2·D` и возвращается: повторный взвод считает прирост от рождения. Колонки первого
/// подхода сняты на его взводе — прироста после взвода в них нет.
#[test]
fn a_repeated_arming_counts_growth_from_birth() {
    for m in [false, true] {
        let mut r = Rig::new(m, true);
        let (w10, w14) = ([(10010, 3), (10000, 10)], [(10010, 3), (10000, 14)]);
        r.both(1000, &w10, &NEAR_BARE);
        r.both(2000, &w10, &NEAR_BARE); // взвод
        r.both(2500, &w14, &NEAR_BARE); // +4 в первом подходе
        r.both(3000, &w14, &[(10060, 4)]); // чужая цена далеко: подход снят (бид-проход видит это кадром позже)
        r.both(3500, &w14, &[(10060, 4)]);
        r.both(4000, &w14, &NEAR_BARE);
        r.both(4500, &w14, &NEAR_BARE); // повторный взвод (бид-вариант — на кадре 4500, зеркало — на 4000)
        r.both(5000, &[(10000, 14)], &NEAR_BARE); // касание
        assert_eq!(r.ap.len(), 2, "m={m}");
        assert_eq!(r.ap[0].disarm_reason, ApproachEnd::PriceLeft);
        assert_eq!(r.ap[1].disarm_reason, ApproachEnd::Touch);
        assert_eq!(r.ap[0].arm_ms, 2000);
        let first = level_cols(&r.ap[0]);
        assert_eq!((first[4], first[5]), (0, U), "на взводе прироста ещё нет");
        let second = level_cols(&r.ap[1]);
        let arm = if m { 4000 } else { 4500 };
        assert_eq!(r.ap[1].arm_ms, arm, "m={m}");
        assert_eq!(
            (second[4], second[5]),
            (4, arm - 2500),
            "прирост первого подхода входит: счёт от рождения"
        );
        assert_eq!(
            (second[6], second[7]),
            (0, U),
            "зона между 10 010 и стеной пуста"
        );
        assert_eq!(second[3], 0, "отмен не было");
    }
}

/// Рост стены и зоны на самом кадре взвода входит в максимум (интервал от рождения до взвода включительно).
#[test]
fn growth_on_the_arm_frame_is_counted() {
    for m in [false, true] {
        let mut r = Rig::new(m, true);
        r.both(1000, &[(10010, 3), (10000, 10)], &FAR);
        r.both(2000, &[(10010, 3), (10000, 10)], &FAR);
        r.both(3000, &[(10010, 3), (10000, 4)], &NEAR); // переход чужой цены в полосу: стена проседает, взвода нет
        r.both(3400, &[(10010, 3), (10008, 4), (10000, 10)], &NEAR); // взвод: стена +6, зона +4
        r.both(4000, &[(10000, 10)], &NEAR); // касание
        assert_eq!(r.ap.len(), 1, "m={m}");
        assert_eq!(r.ap[0].arm_ms, 3400, "m={m}");
        let c = level_cols(&r.ap[0]);
        assert_eq!((c[4], c[5]), (6, 0), "прирост стены на кадре взвода");
        assert_eq!((c[6], c[7]), (4, 0), "прирост зоны на кадре взвода");
        assert_eq!(c[0], 6, "cancel_1s: секунда 3 — просадка 6 на кадре 3000");
        assert_eq!(c[1], U, "cancel_3s: монета наблюдается 2400 мс");
    }
}

/// Окно 60 минут: цены стены и монеты старше часа; отмена за границей окна не считается.
#[test]
fn cancel_60m_counts_the_hour_window_only_for_an_old_wall() {
    for m in [false, true] {
        let mut r = Rig::new(m, true);
        r.both(1000, &[(10010, 3), (10000, 10)], &FAR);
        r.both(100_000, &[(10010, 3), (10000, 8)], &FAR); // минута 1: −2, вне окна минут 2..61
        r.both(130_000, &[(10010, 3), (10000, 7)], &FAR); // минута 2: −1, первая минута окна
        r.both(3_000_000, &[(10010, 3), (10000, 5)], &FAR); // минута 50: −2
        r.both(3_620_000, &[(10010, 3), (10000, 4)], &NEAR); // минута 60: −1; переход в полосу — взвода нет
        r.both(3_700_000, &[(10010, 3), (10000, 9)], &NEAR); // минута 61: взвод, +5
        r.both(3_800_000, &[(10000, 9)], &NEAR); // касание
        assert_eq!(r.ap.len(), 1, "m={m}");
        assert_eq!(r.ap[0].arm_ms, 3_700_000, "m={m}");
        let c = level_cols(&r.ap[0]);
        assert_eq!(c[0], 0, "cancel_1s");
        assert_eq!(c[1], 0, "cancel_3s");
        assert_eq!(c[2], 4, "cancel_60m: минуты 2..61 — 1 + 2 + 1");
        assert_eq!(c[3], 6, "cancel_life: ещё и 2 из минуты 1");
        assert_eq!((c[4], c[5]), (5, 0), "рост на кадре взвода");
    }
}

/// Монета наблюдается больше часа, а стена моложе: `cancel_60m` не определена, остальные — да.
#[test]
fn cancel_60m_is_undefined_for_a_wall_younger_than_an_hour() {
    for m in [false, true] {
        let mut r = Rig::new(m, true);
        r.both(1000, &[(10010, 3)], &FAR);
        r.both(2_000_000, &[(10010, 3), (10000, 10)], &FAR);
        r.both(3_620_000, &[(10010, 3), (10000, 4)], &NEAR); // проседание −6, переход в полосу
        r.both(3_700_000, &[(10010, 3), (10000, 9)], &NEAR); // взвод
        r.both(3_800_000, &[(10000, 9)], &NEAR); // касание
        assert_eq!(r.ap.len(), 1, "m={m}");
        assert_eq!(r.ap[0].arm_ms, 3_700_000, "m={m}");
        let c = level_cols(&r.ap[0]);
        assert_eq!((c[0], c[1], c[2], c[3]), (0, 0, U, 6));
    }
}

/// Окна 1 с и 3 с определены, только когда монета наблюдается не меньше окна.
#[test]
fn short_cancel_windows_need_a_warm_coin() {
    // (кадр взвода, cancel_1s, cancel_3s); монета наблюдается с 1000.
    for (arm_ms, c1, c3) in [
        (1_500, U, U),
        (1_999, U, U),
        (2_000, 0, U),
        (3_999, 0, U),
        (4_000, 0, 0),
    ] {
        for m in [false, true] {
            let mut r = Rig::new(m, true);
            r.both(1000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
            r.both(arm_ms, &[(10010, 3), (10000, 10)], &NEAR_BARE); // взвод: возраст от 500 мс
            r.both(arm_ms + 500, &[(10000, 10)], &NEAR_BARE); // касание
            assert_eq!(r.ap.len(), 1, "arm={arm_ms} m={m}");
            assert_eq!(r.ap[0].arm_ms, arm_ms, "arm={arm_ms} m={m}");
            let c = level_cols(&r.ap[0]);
            assert_eq!((c[0], c[1], c[2]), (c1, c3, U), "arm={arm_ms} m={m}");
        }
    }
}

/// Без `enable_r1` вывод побайтно прежний: те же уровни, касания, подходы, у подходов `r1 = None`;
/// с флагом — те же записи, кроме поля `r1`, и колонки у каждого подхода.
#[test]
fn without_enable_r1_the_output_is_the_same_and_r1_stays_none() {
    type Run = fn(bool, bool) -> Rig;
    let runs: [Run; 5] = [
        run_main,
        |m, r1| run_death(m, r1, 7),
        |m, r1| run_reprice(m, r1, 10_001, 12),
        |m, r1| run_reprice(m, r1, 9_999, 25),
        |m, r1| {
            let mut r = Rig::new(m, r1);
            r.both(1000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
            r.both(2000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
            r.both(2500, &[(10010, 3), (10000, 10)], &[(10060, 4)]);
            r.both(3000, &[(10010, 3), (10000, 10)], &[(10060, 4)]);
            r.both(3500, &[(10010, 3), (10000, 10)], &[(10060, 4)]);
            r
        },
    ];
    for run in runs {
        for m in [false, true] {
            let off = run(m, false);
            let on = run(m, true);
            assert!(off.tr.r1.is_none());
            assert!(on.tr.r1.is_some());
            assert!(off.ap.iter().all(|a| a.r1.is_none()));
            assert!(!off.ap.is_empty(), "сценарий обязан давать подходы");
            assert_eq!(off.out, on.out, "уровни");
            assert_eq!(off.touches, on.touches, "касания");
            assert_eq!(off.tr.live_count(), on.tr.live_count());
            let stripped: Vec<ApproachRecord> = on
                .ap
                .iter()
                .map(|a| ApproachRecord { r1: None, ..*a })
                .collect();
            assert_eq!(stripped, off.ap, "подходы без поля r1");
            // Колонки есть у каждого подхода, чем бы он ни кончился.
            for a in &on.ap {
                assert!(
                    a.r1.is_some(),
                    "колонки на кадре взвода у подхода, снятого как {:?}",
                    a.disarm_reason
                );
            }
        }
    }
}

#[test]
fn enable_r1_is_a_no_op_without_the_approach_band_and_idempotent_with_it() {
    let mut off = LevelTracker::new(cfg_r1(None));
    off.enable_r1();
    assert!(
        off.r1.is_none(),
        "без полосы подхода записей нет — состояние не нужно"
    );

    let mut tr = LevelTracker::new(cfg_r1(Some(20)));
    assert!(tr.r1.is_none());
    tr.enable_r1();
    let first = tr.r1.as_deref().map(|r| r as *const _);
    assert!(first.is_some());
    tr.enable_r1();
    assert_eq!(tr.r1.as_deref().map(|r| r as *const _), first);
}

/// Уровень, рождённый и взведённый до `enable_r1`, состояния не имеет: у подхода `r1 = None`, а не неверные колонки.
#[test]
fn a_level_born_before_enable_r1_has_no_columns() {
    let mut r = Rig::new(false, false);
    r.both(1000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
    r.both(2000, &[(10010, 3), (10000, 10)], &NEAR_BARE);
    r.tr.enable_r1();
    r.both(3000, &[(10000, 10)], &NEAR_BARE);
    assert_eq!(r.ap.len(), 1);
    assert_eq!(r.ap[0].disarm_reason, ApproachEnd::Touch);
    assert!(r.ap[0].r1.is_none());
}

/// Один кадр монеты — пара проходов, бид затем аск.
fn pair_frame(
    tr: &mut LevelTracker,
    ts: i64,
    bids: &[LevelObs],
    asks: &[LevelObs],
    bufs: (
        &mut Vec<LevelRecord>,
        &mut Vec<TouchRecord>,
        &mut Vec<ApproachRecord>,
    ),
) {
    let (out, touches, ap) = bufs;
    tr.observe_frame_with_approaches(ts, Side::Bid, bids, out, touches, ap);
    tr.observe_frame_with_approaches(ts, Side::Ask, asks, out, touches, ap);
}

/// Горячий путь: после прогрева кадры, сделки, взводы, касания и снятия с `enable_r1` не аллоцируют.
#[test]
fn r1_frames_allocate_nothing_after_warmup() {
    let mut tr = LevelTracker::new(cfg_r1(Some(20)));
    tr.enable_r1();
    let mut out = Vec::with_capacity(16);
    let mut touches = Vec::with_capacity(16);
    let mut ap = Vec::with_capacity(16);
    let with_front = [lv(10020, 3), lv(10010, 2), lv(10000, 10)];
    // Без смертей и рождений в цикле: очереди `births` растут на каждом рождении ключа — это не R1.
    let level_only = [lv(10000, 10)];
    let far = [lv(10060, 4), lv(10080, 20)];
    let near = [lv(10015, 4), lv(10080, 20)];
    // Один цикл: уход цены (чужая лучшая далеко) → возврат → взвод → сделка → касание → конец касания.
    let mut cycle = |base: i64| {
        let mut frame = |dt: i64, bids: &[LevelObs], asks: &[LevelObs]| {
            pair_frame(
                &mut tr,
                base + dt,
                bids,
                asks,
                (&mut out, &mut touches, &mut ap),
            );
        };
        frame(0, &with_front, &far);
        frame(1000, &with_front, &far);
        frame(2000, &with_front, &near);
        frame(3000, &with_front, &near);
        tr.observe_trade(TradeHit {
            tick: 10000,
            lots: 1,
            aggressor_is_buy: false,
            block: false,
            rpi: false,
            exch_ms: base + 3500,
        });
        let mut frame = |dt: i64, bids: &[LevelObs], asks: &[LevelObs]| {
            pair_frame(
                &mut tr,
                base + dt,
                bids,
                asks,
                (&mut out, &mut touches, &mut ap),
            );
        };
        frame(4000, &level_only, &near);
        frame(5000, &with_front, &near);
        assert!(ap.iter().any(|x| x.r1.is_some()), "подход с колонками");
        ap.clear();
        touches.clear();
        out.clear();
    };
    cycle(1_000);
    cycle(10_000);
    let (_, counts) = crate::alloc_count::measure(|| {
        for i in 0..500i64 {
            cycle(20_000 + 10_000 * i);
        }
    });
    assert_eq!(counts.allocations, 0, "R1 на горячем пути не аллоцирует");
}
