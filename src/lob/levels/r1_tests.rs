//! Колонки уровня R1 (TK-025): малые синтетические последовательности, ручной счёт по всем 22
//! колонкам `LEVEL_NAMES`.
//!
//! Все сценарии заданы для бид-стены 10 000 и зеркалятся вокруг 20 000 в аск-стену (`Rig::m`): цена
//! стены остаётся 10 000, поэтому расстояния в bps и ожидаемые значения у зеркала те же.

use super::*;
use crate::lob::r1::{LEVEL_N, LEVEL_NAMES, R1_UNDEF};

const U: i64 = R1_UNDEF;
const HOUR: i64 = 3_600_000;
const WALL_TICK: i64 = 10_000;

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

    /// Кадр: `book` — пары (тик, лоты) бид-варианта, лучшая цена первой; `wall_side` — сторона стены.
    fn frame(&mut self, ts: i64, wall_side: bool, book: &[(i64, i64)]) {
        let levels: Vec<LevelObs> = book.iter().map(|&(t, s)| lv(px(self.m, t), s)).collect();
        let side = if wall_side != self.m {
            Side::Bid
        } else {
            Side::Ask
        };
        self.tr.observe_frame_with_approaches(
            ts,
            side,
            &levels,
            &mut self.out,
            &mut self.touches,
            &mut self.ap,
        );
    }

    fn wall(&mut self, ts: i64, book: &[(i64, i64)]) {
        self.frame(ts, true, book);
    }

    fn opp(&mut self, ts: i64, book: &[(i64, i64)]) {
        self.frame(ts, false, book);
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

fn level_cols(a: &ApproachRecord) -> [i64; LEVEL_N] {
    a.r1.expect("на касании колонки R1 заполнены").level
}

fn assert_cols(got: &[i64; LEVEL_N], want: &[i64; LEVEL_N]) {
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        assert_eq!(g, w, "колонка {}", LEVEL_NAMES[i]);
    }
}

/// Бид-стена 10 000: рост стены и зоны на подходе, отмены, фронтран, ход лучшей цены, чужая стена.
///
/// Кадры стены (в скобках — стена, лоты): 1000 рождение (10); 2000 (10), чужая лучшая цена 10 060 —
/// дальше `2·D`; 3000 взвод (10); 3200 (14, +4); 3500 (12, −2), зона 10 010 +2 и 10 005 +3;
/// 3600 сделка 1 лот; 3800 (8, −4 за вычетом сделки −3); 11 000 лучшая цена ушла на 10 010,
/// стена 5 (−3); 12 000 стена — лучшая цена, 4 (−1): касание.
fn run_main(m: bool, r1: bool) -> Rig {
    let mut r = Rig::new(m, r1);
    r.wall(1000, &[(10020, 3), (10000, 10)]);
    r.opp(1000, &[(10060, 4), (10080, 20)]);
    r.wall(2000, &[(10020, 3), (10000, 10)]);
    r.opp(2500, &[(10015, 4), (10080, 20)]);
    r.wall(3000, &[(10020, 3), (10010, 2), (10000, 10)]);
    r.wall(3200, &[(10020, 3), (10010, 2), (10000, 14)]);
    r.wall(3500, &[(10020, 3), (10010, 4), (10005, 3), (10000, 12)]);
    r.hit(3600, 10000, 1);
    r.wall(3800, &[(10020, 3), (10010, 4), (10005, 3), (10000, 8)]);
    r.wall(11_000, &[(10010, 4), (10005, 3), (10000, 5)]);
    r.wall(12_000, &[(10000, 4), (9990, 6), (9986, 10), (9985, 30)]);
    r
}

/// Ожидание `run_main`, колонки по порядку `LEVEL_NAMES`.
const MAIN_WANT: [i64; LEVEL_N] = [
    1,      // cancel_1s: секунда 12 — отмена 1 на кадре касания
    4,      // cancel_3s: секунды 10..12 — 3 (кадр 11 000) + 1
    U,      // cancel_60m: стена моложе часа
    9,      // cancel_life: 2 + 3 + 3 + 1
    4,      // wall_add_max_lots: +4 на кадре 3200
    8_800,  // wall_add_max_age_ms: 12 000 − 3200
    3,      // front_add_max_lots: 10 005 новая с 3 лотами (10 010: +2) на кадре 3500
    8_500,  // front_add_max_age_ms: 12 000 − 3500
    U,      // born_shift_cbps: стена не переустановка
    U,      // born_shift_lots
    U,      // prev_death_gap_ms: на этой цене смертей не было
    U,      // prev_death_outcome
    2_000,  // size_share15_bp: 4·10⁴ / (4 + 6 + 10); 9985 — ровно 15 тиков, не в окне
    3,      // nz_levels15
    0,      // best_move_1s_cbps: за 1 с лучшая цена не двигалась
    -1_000, // best_move_10s_cbps: 10 020 → 10 010 против стены: −10 bps = −1000 cbps
    8_000,  // opp_wall_dist_cbps: чужая стена 10 080, 80 bps
    50_000, // opp_wall_ratio_bp: 20 лотов / 4 лота
    7,      // frontrun_lots_at_touch: кадр 11 000 — 4 + 3
    4,      // frontrun_delta_10s: 7 − 3 (кадр 2000: впереди 10 020)
    1,      // frontrun_levels: между лучшей ценой 10 010 и стеной занята 10 005
    10_000, // since_far_ms: далеко было на кадре 2000
];

#[test]
fn main_scenario_matches_hand_count_on_both_sides() {
    for m in [false, true] {
        let r = run_main(m, true);
        assert_eq!(r.ap.len(), 1, "m={m}");
        assert_eq!(r.ap[0].disarm_reason, ApproachEnd::Touch);
        assert_eq!(r.ap[0].disarm_ms, 12_000);
        assert_cols(&level_cols(&r.ap[0]), &MAIN_WANT);
    }
}

/// Бид-стена 10 000, умершая и снова родившаяся: прошлая смерть с исходом по правилу 70/20.
/// Первый подход кончается смертью (`r1 = None`), второй — касанием.
fn run_death(m: bool, r1: bool, traded: i64) -> Rig {
    let mut r = Rig::new(m, r1);
    r.opp(1000, &[(10015, 4)]);
    r.wall(1000, &[(10020, 3), (10000, 10)]);
    r.wall(2000, &[(10020, 3), (10000, 10)]);
    if traded > 0 {
        r.hit(2500, 10000, traded);
    }
    r.wall(3000, &[(10020, 3)]);
    r.wall(5000, &[(10020, 3), (10000, 10)]);
    r.wall(6000, &[(10020, 3), (10000, 10)]);
    r.wall(9000, &[(10000, 10)]);
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
            assert!(r.ap[0].r1.is_none(), "снятие смертью колонок не несёт");
            assert_eq!(r.ap[1].disarm_reason, ApproachEnd::Touch);
            let want = [
                0, 0, U, 0, // отмены новой жизни: не было
                0, U, 0, U, // прироста с повторного взвода нет
                U, U, // не переустановка
                6_000, outcome, // смерть на 3000, касание на 9000
                10_000, 1, // стена одна в окне формы
                0,
                U, // 1 с: лучшая цена та же; 10 с: стене 4 с — рано
                U, U, // чужих стен нет
                3, U,
                0,     // фронтран: 10 020 с 3 лотами; 10 с назад уровня ещё не было
                4_000, // since_far: далеко не было, от рождения на 5000
            ];
            assert_cols(&level_cols(&r.ap[1]), &want);
        }
    }
}

/// Бид-стена 10 000 переустановлена: на кадре 2000 она исчезает, а на `new_tick` встаёт стена
/// `new_size` (рождение той же стороны, цена ±1 тик, размер в пределах 2×).
fn run_reprice(m: bool, r1: bool, new_tick: i64, new_size: i64) -> Rig {
    let mut r = Rig::new(m, r1);
    r.opp(1000, &[(10015, 4)]);
    r.wall(1000, &[(10020, 3), (10000, 10)]);
    r.wall(2000, &[(10020, 3), (new_tick, new_size)]);
    r.wall(3000, &[(10020, 3), (new_tick, new_size)]);
    r.wall(4000, &[(new_tick, new_size)]);
    r
}

#[test]
fn born_shift_is_from_the_dead_price_and_signed_away_from_the_market() {
    // (новый тик, размер, ожидаемый сдвиг cbps, ожидаемая разница лотов).
    let cases = [
        (10_001, 12, -100, 2), // бид вверх, к цене: −1 bps от 10 000
        (9_999, 12, 100, 2),   // бид вниз, от цены
        (10_001, 6, -100, -4), // меньше прежней, но в пределах 2×
        (10_001, 25, U, U),    // больше 2× — не переустановка
    ];
    for (tick, size, cbps, lots) in cases {
        for m in [false, true] {
            let r = run_reprice(m, true, tick, size);
            assert_eq!(r.ap.len(), 1, "tick={tick} size={size} m={m}");
            let want = [
                0, 0, U,
                0, // отмен нет; окно 3 с как раз наблюдается (3000 мс)
                0, U, 0, U, // с повторного взвода прироста нет
                cbps, lots, //
                U, U, // на новой цене смертей не было
                10_000, 1, // стена одна в окне формы
                0,
                U, // 1 с — та же лучшая цена; возраст 2 с до 10 с не дорос
                U, U, // чужих стен нет
                3, U, 0,     // фронтран: 10 020 с 3 лотами
                2_000, // since_far: от рождения на 2000
            ];
            assert_cols(&level_cols(&r.ap[0]), &want);
        }
    }
}

/// Цена отходит дальше `2·D` и возвращается: повторный взвод обнуляет накопленные приросты.
#[test]
fn a_new_arming_restarts_the_growth_interval() {
    for m in [false, true] {
        let mut r = Rig::new(m, true);
        r.opp(1000, &[(10015, 4)]);
        r.wall(1000, &[(10020, 3), (10000, 10)]);
        r.wall(2000, &[(10020, 3), (10000, 10)]); // взвод
        r.wall(2500, &[(10020, 3), (10000, 14)]); // +4 в первом подходе
        r.opp(3000, &[(10060, 4)]);
        r.wall(3500, &[(10020, 3), (10000, 14)]); // чужая цена далеко: подход снят
        r.opp(4000, &[(10015, 4)]);
        r.wall(4500, &[(10020, 3), (10000, 14)]); // повторный взвод
        r.wall(5000, &[(10000, 14)]); // касание
        assert_eq!(r.ap.len(), 2, "m={m}");
        assert_eq!(r.ap[0].disarm_reason, ApproachEnd::PriceLeft);
        assert!(r.ap[0].r1.is_none(), "уход цены колонок не несёт");
        let c = level_cols(&r.ap[1]);
        assert_eq!(
            (c[4], c[5]),
            (0, U),
            "прирост первого подхода не переносится"
        );
        assert_eq!((c[6], c[7]), (0, U));
        assert_eq!(c[21], 1_500, "since_far: далеко было на кадре 3500");
        assert_eq!(c[3], 0);
    }
}

/// Окно 60 минут: цены стены и монеты старше часа; отмена за границей окна не считается.
#[test]
fn cancel_60m_counts_the_hour_window_only_for_an_old_wall() {
    for m in [false, true] {
        let mut r = Rig::new(m, true);
        r.opp(1000, &[(10060, 4)]);
        r.wall(1000, &[(10020, 3), (10000, 10)]);
        r.wall(100_000, &[(10020, 3), (10000, 8)]); // минута 1: −2, вне окна минут 2..61
        r.wall(130_000, &[(10020, 3), (10000, 7)]); // минута 2: −1, первая минута окна
        r.wall(3_000_000, &[(10020, 3), (10000, 5)]); // минута 50: −2
        r.opp(3_000_100, &[(10015, 4)]);
        r.wall(3_620_000, &[(10020, 3), (10000, 9)]); // взвод (рост — не отмена)
        r.wall(3_650_000, &[(10020, 3), (10000, 7)]); // минута 60: −2
        r.wall(3_700_000, &[(10000, 6)]); // минута 61, касание: −1
        assert_eq!(r.ap.len(), 1, "m={m}");
        let c = level_cols(&r.ap[0]);
        assert_eq!(c[0], 1, "cancel_1s");
        assert_eq!(c[1], 1, "cancel_3s: кадр 3_650_000 вне трёх секунд");
        assert_eq!(c[2], 6, "cancel_60m: 1 + 2 + 2 + 1");
        assert_eq!(c[3], 8, "cancel_life: ещё и 2 из минуты 1");
        assert_eq!((c[4], c[5]), (0, U), "после взвода стена не росла");
    }
}

/// Монета наблюдается больше часа, а стена моложе: `cancel_60m` не определена, остальные — да.
#[test]
fn cancel_60m_is_undefined_for_a_wall_younger_than_an_hour() {
    for m in [false, true] {
        let mut r = Rig::new(m, true);
        r.opp(1000, &[(10060, 4)]);
        r.wall(1000, &[(10020, 3)]);
        r.wall(2_000_000, &[(10020, 3), (10000, 10)]);
        r.opp(3_000_100, &[(10015, 4)]);
        r.wall(3_620_000, &[(10020, 3), (10000, 10)]);
        r.wall(3_700_000, &[(10000, 9)]);
        assert_eq!(r.ap.len(), 1, "m={m}");
        let c = level_cols(&r.ap[0]);
        assert_eq!((c[0], c[1], c[2], c[3]), (1, 1, U, 1));
    }
}

/// Окна 1 с и 3 с определены, только когда монета наблюдается не меньше окна.
#[test]
fn short_cancel_windows_need_a_warm_coin() {
    // (кадр касания, cancel_1s, cancel_3s).
    for (touch_ms, c1, c3) in [(1_900, U, U), (2_500, 0, U), (4_000, 0, 0)] {
        for m in [false, true] {
            let mut r = Rig::new(m, true);
            r.opp(1000, &[(10015, 4)]);
            r.wall(1000, &[(10020, 3), (10000, 10)]);
            r.wall(1500, &[(10020, 3), (10000, 10)]); // взвод: возраст ровно 500 мс
            r.wall(touch_ms, &[(10000, 10)]);
            assert_eq!(r.ap.len(), 1, "touch={touch_ms} m={m}");
            let c = level_cols(&r.ap[0]);
            assert_eq!((c[0], c[1], c[2]), (c1, c3, U), "touch={touch_ms} m={m}");
        }
    }
}

/// Без `enable_r1` вывод побайтно прежний: те же уровни, касания, подходы, у подходов `r1 = None`;
/// с флагом — те же записи, кроме поля `r1`.
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
            r.opp(1000, &[(10015, 4)]);
            r.wall(1000, &[(10020, 3), (10000, 10)]);
            r.wall(2000, &[(10020, 3), (10000, 10)]);
            r.opp(2500, &[(10060, 4)]);
            r.wall(3000, &[(10020, 3), (10000, 10)]);
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
            // Колонки есть ровно у снятых касанием.
            for a in &on.ap {
                assert_eq!(
                    a.r1.is_some(),
                    a.disarm_reason == ApproachEnd::Touch,
                    "r1 только на касании: {:?}",
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

/// Уровень, рождённый до `enable_r1`, состояния не имеет: на касании `r1 = None`, а не неверные колонки.
#[test]
fn a_level_born_before_enable_r1_has_no_columns() {
    let mut r = Rig::new(false, false);
    r.opp(1000, &[(10015, 4)]);
    r.wall(1000, &[(10020, 3), (10000, 10)]);
    r.wall(2000, &[(10020, 3), (10000, 10)]);
    r.tr.enable_r1();
    r.wall(3000, &[(10000, 10)]);
    assert_eq!(r.ap.len(), 1);
    assert_eq!(r.ap[0].disarm_reason, ApproachEnd::Touch);
    assert!(r.ap[0].r1.is_none());
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
    let (b, a) = (Side::Bid, Side::Ask);
    // Один цикл: уход цены (чужая лучшая далеко) → возврат → взвод → сделка → касание → конец касания.
    let mut cycle = |base: i64| {
        tr.observe_frame_with_approaches(base, a, &far, &mut out, &mut touches, &mut ap);
        tr.observe_frame_with_approaches(
            base + 1000,
            b,
            &with_front,
            &mut out,
            &mut touches,
            &mut ap,
        );
        tr.observe_frame_with_approaches(base + 2000, a, &near, &mut out, &mut touches, &mut ap);
        tr.observe_frame_with_approaches(
            base + 3000,
            b,
            &with_front,
            &mut out,
            &mut touches,
            &mut ap,
        );
        tr.observe_trade(TradeHit {
            tick: 10000,
            lots: 1,
            aggressor_is_buy: false,
            block: false,
            rpi: false,
            exch_ms: base + 3500,
        });
        tr.observe_frame_with_approaches(
            base + 4000,
            b,
            &level_only,
            &mut out,
            &mut touches,
            &mut ap,
        );
        tr.observe_frame_with_approaches(
            base + 5000,
            b,
            &with_front,
            &mut out,
            &mut touches,
            &mut ap,
        );
        assert!(ap.iter().any(|x| x.r1.is_some()), "касание с колонками");
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
