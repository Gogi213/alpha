use super::*;
use crate::lob::r1::FLOW_NAMES;

/// Начало наблюдения: минута 600, секунда 36 000 (кратно часу — расчёты по слотам ручные).
const B: i64 = 36_000_000;
const UNDEF: i64 = R1_UNDEF;

fn tr(ms: i64, lots: i64, buy: bool) -> TradeHit {
    TradeHit {
        tick: 100,
        lots,
        aggressor_is_buy: buy,
        block: false,
        rpi: false,
        exch_ms: ms,
    }
}

fn frame(f: &mut R1Flow, ms: i64, bids: &[(i64, i64)], asks: &[(i64, i64)]) {
    f.on_frame(ms, BookView { bids, asks });
}

/// Кадр из одного уровня на сторону.
fn fr1(f: &mut R1Flow, ms: i64, bid: (i64, i64), ask: (i64, i64)) {
    frame(f, ms, &[bid], &[ask]);
}

/// Монета с первым кадром в `B`.
fn flow_with_start() -> Box<R1Flow> {
    let mut f = R1Flow::new();
    fr1(&mut f, B, (100, 10), (101, 10));
    f
}

fn at(f: &R1Flow, side: Side, t0: i64) -> [i64; FLOW_N] {
    let mut out = [0; FLOW_N];
    f.fill(side, t0, &mut out);
    out
}

fn col(out: &[i64; FLOW_N], name: &str) -> i64 {
    let i = FLOW_NAMES
        .iter()
        .position(|n| *n == name)
        .unwrap_or_else(|| panic!("нет колонки {name}"));
    out[i]
}

/// Сверка набора колонок: `(имя, бид-стена, аск-стена)`.
fn check(bid: &[i64; FLOW_N], ask: &[i64; FLOW_N], cases: &[(&str, i64, i64)]) {
    for &(name, b, a) in cases {
        assert_eq!(col(bid, name), b, "{name}, бид-стена");
        assert_eq!(col(ask, name), a, "{name}, аск-стена");
    }
}

#[test]
fn layout_matches_names() {
    for (g, w) in [15, 30, 60].into_iter().enumerate() {
        for k in 0..6 {
            let want = format!(
                "tape_{}_{}_{}s",
                ["press", "with", "all"][k % 3],
                ["lots", "n"][k / 3],
                w
            );
            assert_eq!(FLOW_NAMES[I_TAPE + g * 6 + k], want);
        }
    }
    let groups: [(usize, &[&str]); 12] = [
        (I_60M, &["tape_press_60m_lots", "tape_with_60m_lots"]),
        (
            I_BURST,
            &[
                "tape_burst_press_15s_bp",
                "tape_burst_with_15s_bp",
                "tape_burst_press_30s_bp",
                "tape_burst_with_30s_bp",
            ],
        ),
        (I_AVG, &["tape_press_avg_30s_e2", "tape_with_avg_30s_e2"]),
        (I_SIGN, &["sign_ac_15s_bp", "sign_ac_60s_bp"]),
        (I_VPIN, &["vpin_bp"]),
        (I_SIZE, &["trade_size_p50_15m", "trade_size_p90_15m"]),
        (I_OBI, &["obi1_bp", "obi5_bp", "obi10_bp", "obi50_bp"]),
        (I_MICRO, &["micro_off_cbps"]),
        (I_OFI, &["ofi_10s_lots", "ofi_60s_lots"]),
        (I_FLIPS, &["best_flips_15s", "best_flips_60s"]),
        (I_TAPE + 17, &["tape_all_n_60s"]),
        (
            I_BURST60,
            &["tape_burst_press_60s_bp", "tape_burst_with_60s_bp"],
        ),
    ];
    for (start, names) in groups {
        for (j, name) in names.iter().enumerate() {
            assert_eq!(FLOW_NAMES[start + j], *name);
        }
    }
    assert_eq!(I_FLIPS + 2, I_BURST60);
    assert_eq!(I_BURST60 + 2, FLOW_N);
}

#[test]
fn empty_state_is_all_undefined() {
    let f = R1Flow::new();
    for side in [Side::Bid, Side::Ask] {
        assert!(at(&f, side, B).iter().all(|&v| v == UNDEF));
    }
    // Сделки без единого кадра: начало наблюдения неизвестно — всё не определено.
    let mut f = R1Flow::new();
    f.on_trade(&tr(B, 5, true));
    assert!(at(&f, Side::Bid, B + 4_000_000).iter().all(|&v| v == UNDEF));
    // Пустой кадр не паникует: книжные колонки не определены.
    let mut f = R1Flow::default();
    frame(&mut f, B, &[], &[]);
    let o = at(&f, Side::Ask, B + 20_000);
    for name in [
        "obi1_bp",
        "obi5_bp",
        "obi10_bp",
        "obi50_bp",
        "micro_off_cbps",
    ] {
        assert_eq!(col(&o, name), UNDEF, "{name}");
    }
}

#[test]
fn tape_windows_hand_calc() {
    let mut f = flow_with_start();
    // t0 = B + 100 500, s0 = 36 100; возраст = s0 − секунда сделки; окно W: возраст < W.
    for (ms, lots, buy) in [
        (B + 40_999, 100, true), // возраст 60: вне окна 60 с
        (B + 41_000, 7, true),   // 59: внутри 60 с
        (B + 60_000, 6, false),  // 40
        (B + 80_000, 2, true),   // 20
        (B + 85_000, 50, false), // 15: вне 15 с, внутри 30 с
        (B + 86_000, 20, true),  // 14: внутри 15 с
        (B + 90_000, 3, false),  // 10
        (B + 100_200, 4, true),  // 0: текущий неполный слот
    ] {
        f.on_trade(&tr(ms, lots, buy));
    }
    let t0 = B + 100_500;
    let bid = at(&f, Side::Bid, t0);
    let ask = at(&f, Side::Ask, t0);
    // У бид-стены давят продавцы, у аск-стены — покупатели.
    check(
        &bid,
        &ask,
        &[
            ("tape_press_lots_15s", 3, 24),
            ("tape_with_lots_15s", 24, 3),
            ("tape_all_lots_15s", 27, 27),
            ("tape_press_n_15s", 1, 2),
            ("tape_with_n_15s", 2, 1),
            ("tape_all_n_15s", 3, 3),
            ("tape_press_lots_30s", 53, 26),
            ("tape_with_lots_30s", 26, 53),
            ("tape_all_lots_30s", 79, 79),
            ("tape_press_n_30s", 2, 3),
            ("tape_with_n_30s", 3, 2),
            ("tape_all_n_30s", 5, 5),
            ("tape_press_lots_60s", 59, 33),
            ("tape_with_lots_60s", 33, 59),
            ("tape_all_lots_60s", 92, 92),
            ("tape_press_n_60s", 3, 4),
            ("tape_with_n_60s", 4, 3),
            ("tape_all_n_60s", 7, 7),
            // Меньше часа наблюдения: часовые колонки не определены.
            ("tape_press_60m_lots", UNDEF, UNDEF),
            ("tape_burst_press_15s_bp", UNDEF, UNDEF),
        ],
    );
}

#[test]
fn rings_ignore_block_and_nonpositive_and_count_rpi() {
    let mut f = flow_with_start();
    let mut block = tr(B + 19_000, 50, true);
    block.block = true;
    f.on_trade(&block);
    f.on_trade(&tr(B + 19_000, 0, true));
    f.on_trade(&tr(B + 19_000, -3, false));
    let mut rpi = tr(B + 19_100, 4, true);
    rpi.rpi = true;
    f.on_trade(&rpi);
    f.on_trade(&tr(B + 19_200, 6, false));
    let o = at(&f, Side::Bid, B + 20_500);
    // RPI входит (как в `flow_ring` трекера), блок, нулевые и отрицательные — нет.
    assert_eq!(col(&o, "tape_all_lots_15s"), 10);
    assert_eq!(col(&o, "tape_all_n_15s"), 2);
    // Знаки отфильтрованных сделок на пару не влияют: покупка(RPI) → продажа = −1.
    assert_eq!(col(&o, "sign_ac_15s_bp"), -10_000);
}

#[test]
fn warm_up_boundaries() {
    // (колонка, длина окна прогрева, мс)
    let table: [(&str, i64); 14] = [
        ("tape_press_lots_15s", 15_000),
        ("tape_all_n_30s", 30_000),
        ("tape_with_lots_60s", 60_000),
        ("tape_press_avg_30s_e2", 30_000),
        ("sign_ac_15s_bp", 15_000),
        ("sign_ac_60s_bp", 60_000),
        ("ofi_10s_lots", 10_000),
        ("ofi_60s_lots", 60_000),
        ("best_flips_15s", 15_000),
        ("best_flips_60s", 60_000),
        ("trade_size_p50_15m", 900_000),
        ("tape_press_60m_lots", 3_600_000),
        ("tape_burst_press_15s_bp", 3_600_000),
        ("tape_burst_press_60s_bp", 3_600_000),
    ];
    // Сделка каждые 500 мс со сменой стороны — окна непустые и в одной, и в другой стороне.
    let build = |t0: i64| {
        let mut f = flow_with_start();
        let mut i = 0;
        while B + 250 + 500 * i <= t0 {
            f.on_trade(&tr(B + 250 + 500 * i, 1, i % 2 == 0));
            i += 1;
        }
        f
    };
    for (name, warm) in table {
        let before = build(B + warm - 1);
        let after = build(B + warm);
        for side in [Side::Bid, Side::Ask] {
            assert_eq!(
                col(&at(&before, side, B + warm - 1), name),
                UNDEF,
                "{name} до"
            );
            assert_ne!(
                col(&at(&after, side, B + warm), name),
                UNDEF,
                "{name} после"
            );
        }
    }
}

#[test]
fn warm_up_counts_from_first_frame_not_first_trade() {
    let mut f = R1Flow::new();
    f.on_trade(&tr(B, 1, true)); // сделка раньше первого кадра: начало наблюдения не сдвигает
    fr1(&mut f, B + 30_000, (100, 10), (101, 10));
    fr1(&mut f, B + 40_000, (100, 10), (101, 10)); // следующий кадр начало не переносит
    f.on_trade(&tr(B + 44_000, 1, true));
    assert_eq!(col(&at(&f, Side::Bid, B + 44_999), "tape_all_n_15s"), UNDEF);
    assert_eq!(col(&at(&f, Side::Bid, B + 45_000), "tape_all_n_15s"), 1);
}

#[test]
fn sixty_minute_lots_and_burst_hand_calc() {
    let mut f = flow_with_start();
    // t0 = 39 630 500: минута 660, окно минут 601..=660.
    for (ms, lots, buy) in [
        (B + 10_000, 1000, false), // минута 600: возраст 60, вне окна
        (B + 65_000, 600, false),  // минута 601: возраст 59, внутри
        (37_805_000, 300, true),   // минута 630
        (39_625_000, 30, false),   // минута 660, возраст 5 с
        (39_629_000, 10, true),    // минута 660, возраст 1 с
    ] {
        f.on_trade(&tr(ms, lots, buy));
    }
    let t0 = 39_630_500;
    let bid = at(&f, Side::Bid, t0);
    let ask = at(&f, Side::Ask, t0);
    // Бид-стена: давят продавцы, 60m = 600 + 30; за стену — 300 + 10.
    // burst = lots_W · 3600 · 10⁴ / (W · lots_60m).
    check(
        &bid,
        &ask,
        &[
            ("tape_press_60m_lots", 630, 310),
            ("tape_with_60m_lots", 310, 630),
            ("tape_burst_press_15s_bp", 114_285, 77_419),
            ("tape_burst_with_15s_bp", 77_419, 114_285),
            ("tape_burst_press_30s_bp", 57_142, 38_709),
            ("tape_burst_with_30s_bp", 38_709, 57_142),
        ],
    );
    // Граница прогрева: на 1 мс раньше часа — не определено, ровно час — определено.
    let o = at(&f, Side::Bid, B + 3_599_999);
    assert_eq!(col(&o, "tape_press_60m_lots"), UNDEF);
    assert_eq!(col(&o, "tape_burst_press_15s_bp"), UNDEF);
    let o = at(&f, Side::Bid, B + 3_600_000);
    assert_ne!(col(&o, "tape_press_60m_lots"), UNDEF);
    // g82-60s: burst = lots_60s · 3600 · 10⁴ / (60 · lots_60m) = lots_60s · 600 000 / lots_60m.
    assert_eq!(
        col(&bid, "tape_burst_press_60s_bp"),
        col(&bid, "tape_press_lots_60s") * 600_000 / col(&bid, "tape_press_60m_lots")
    );
    assert_eq!(
        col(&ask, "tape_burst_with_60s_bp"),
        col(&ask, "tape_with_lots_60s") * 600_000 / col(&ask, "tape_with_60m_lots")
    );
}

#[test]
fn burst_is_undefined_when_side_has_no_hour_volume() {
    let mut f = flow_with_start();
    f.on_trade(&tr(39_625_000, 10, true));
    let o = at(&f, Side::Bid, 39_630_500);
    // Продаж за час нет: лоты определены (0), всплеск давящих — нет; за стену: 10·3.6e7/(15·10).
    assert_eq!(col(&o, "tape_press_60m_lots"), 0);
    assert_eq!(col(&o, "tape_burst_press_15s_bp"), UNDEF);
    assert_eq!(col(&o, "tape_burst_with_15s_bp"), 2_400_000);
}

#[test]
fn average_trade_30s_hand_calc() {
    let mut f = flow_with_start();
    // s0 = 36 040.
    for (ms, lots, buy) in [
        (B + 35_000, 2, true),  // возраст 5
        (B + 36_000, 1, true),  // 4
        (B + 37_000, 2, false), // 3
        (B + 38_000, 5, false), // 2
        (B + 39_000, 10, true), // 1
    ] {
        f.on_trade(&tr(ms, lots, buy));
    }
    let t0 = B + 40_500;
    // Продавцы: 7 лотов в 2 сделках → 350; покупатели: 13 в 3 → 1300/3 = 433 (усечение).
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[
            ("tape_press_avg_30s_e2", 350, 433),
            ("tape_with_avg_30s_e2", 433, 350),
        ],
    );
    // Покупок в окне нет: средняя «за стену» у бид-стены не определена, а лоты — ноль.
    let mut g = flow_with_start();
    g.on_trade(&tr(B + 38_000, 4, false));
    let o = at(&g, Side::Bid, t0);
    assert_eq!(col(&o, "tape_press_avg_30s_e2"), 400);
    assert_eq!(col(&o, "tape_with_avg_30s_e2"), UNDEF);
    assert_eq!(col(&o, "tape_with_lots_30s"), 0);
}

#[test]
fn sign_autocorrelation_hand_calc() {
    let mut f = flow_with_start();
    // s0 = 36 100. Пара относится к слоту более поздней сделки, предыдущий знак — по монете.
    for (ms, buy) in [
        (B + 60_000, true),  // возраст 40: пары нет
        (B + 70_000, true),  // 30: пара (+,+) = +1
        (B + 90_000, false), // 10: пара (+,−) = −1 — в окне 15 с, хотя партнёр вне его
        (B + 91_000, false), // 9: +1
        (B + 92_000, true),  // 8: −1
        (B + 92_500, false), // 8: −1 (та же секунда, порядок поступления)
    ] {
        f.on_trade(&tr(ms, 1, buy));
    }
    let t0 = B + 100_500;
    // 15 с: −1+1−1−1 = −2 на 4 пары → −5000; 60 с: добавляется +1 → −1 на 5 → −2000.
    // Сторона стены на автокорреляцию не влияет.
    let cases = [
        ("sign_ac_15s_bp", -5000, -5000),
        ("sign_ac_60s_bp", -2000, -2000),
    ];
    check(&at(&f, Side::Bid, t0), &at(&f, Side::Ask, t0), &cases);

    // Усечение к нулю: пары −1, −1, +1 → −1/3 → −3333 (не −3334).
    let mut g = flow_with_start();
    for (i, buy) in [true, false, true, true].into_iter().enumerate() {
        g.on_trade(&tr(B + 10_000 + 1_000 * i as i64, 1, buy));
    }
    assert_eq!(col(&at(&g, Side::Bid, B + 20_500), "sign_ac_15s_bp"), -3333);

    // Одна сделка — пар нет.
    let mut h = flow_with_start();
    h.on_trade(&tr(B + 10_000, 1, true));
    assert_eq!(col(&at(&h, Side::Bid, B + 20_500), "sign_ac_15s_bp"), UNDEF);
}

#[test]
fn vpin_hand_calc() {
    let mut f = flow_with_start();
    // До прогрева 60 мин баров нет (но сделка идёт в оборот часа).
    f.on_trade(&tr(B + 600_000, 400, false));
    assert_eq!((f.bars_len, f.bar_size), (0, 0));
    // (время, лоты, покупка, оборот часа после сделки → размер бара, открытый бар: размер/набрано, закрыто баров)
    let steps = [
        (39_661_000, 40, true, (0, 0), 5),     // 440/50 = 8: 5 баров ровно
        (39_662_000, 360, false, (16, 8), 27), // 800/50 = 16: 22 бара + 8 в открытом
        (39_663_000, 200, true, (20, 12), 37), // дозакрыть 16-бар (8 пок. + 8 прод.), 1000/50 = 20: 9 баров + 12
        (39_664_000, 100, false, (22, 4), 42), // 8 дозакрывают 20-бар; 1100/50 = 22: 4 бара + 4
        (39_665_000, 150, true, (25, 7), 48),  // 18 дозакрывают 22-бар; 1250/50 = 25: 5 баров + 7
        (39_666_000, 100, false, (27, 1), 52), // 18 дозакрывают 25-бар; 1350/50 = 27: 3 бара + 1
    ];
    for (ms, lots, buy, (size, fill), closed) in steps {
        f.on_trade(&tr(ms, lots, buy));
        assert_eq!(
            (f.bar_size, if size == 0 { 0 } else { f.bar_fill }),
            (size, fill)
        );
        assert_eq!(f.bars_len, closed.min(VPIN_BARS));
        let v = col(&at(&f, Side::Bid, ms + 500), "vpin_bp");
        if closed < VPIN_BARS {
            assert_eq!(v, UNDEF, "до 50 закрытых баров");
        } else {
            // В кольце баров 3..=52: Σ|Vпок−Vпрод| = 879, ΣV = 933 → 879·10⁴/933.
            assert_eq!(v, 9421);
            assert_eq!(col(&at(&f, Side::Ask, ms + 500), "vpin_bp"), 9421);
        }
    }
}

#[test]
fn vpin_ignores_pre_warm_trades() {
    let mut f = flow_with_start();
    // Если бы бары строились и до прогрева, 100 лотов при обороте 100 дали бы 50 баров по 2 лота.
    f.on_trade(&tr(B + 3_000_000, 100, true));
    assert_eq!(f.bars_len, 0);
    f.on_trade(&tr(39_661_000, 100, true));
    // Оборот часа 200 → бар 4 лота → 25 баров: до 50 vpin не определён.
    assert_eq!(f.bars_len, 25);
    assert_eq!(col(&at(&f, Side::Bid, 39_662_000), "vpin_bp"), UNDEF);
}

#[test]
fn vpin_minimum_bar_is_one_lot() {
    let mut f = flow_with_start();
    // Оборот часа 60 < 100 → 60/50 = 1 лот в баре: 60 баров, в окне последние 50, все односторонние.
    f.on_trade(&tr(39_661_000, 60, false));
    assert_eq!(f.bars_len, 50);
    assert_eq!(col(&at(&f, Side::Ask, 39_662_000), "vpin_bp"), 10_000);
}

#[test]
fn trade_size_percentiles_hand_calc() {
    let mut f = flow_with_start();
    // t0 = B + 930 000: минута 615, окно минут 601..=615.
    for (ms, lots) in [
        (B + 5_000, 1 << 40), // минута 600: вне окна (возраст 15)
        (B + 61_000, 100),    // минута 601: возраст 14, внутри; корзина 6
        (B + 301_000, 1),     // минута 605
        (B + 302_000, 1),
        (B + 303_000, 1),
        (B + 304_000, 2),
        (B + 305_000, 3),
        (B + 601_000, 5), // минута 610
        (B + 602_000, 5),
        (B + 603_000, 9),
        (B + 901_000, 1000), // минута 615
    ] {
        f.on_trade(&tr(ms, lots, ms % 2 == 0));
    }
    // n = 10; корзины k: 0→3, 1→2, 2→2, 3→1, 6→1, 9→1.
    // p50: цель ⌈5⌉ = 5 набирается на k=1 → 2; p90: цель 9 — на k=6 → 64.
    // С вышедшей из окна сделкой было бы n=11 и p90 = 512.
    let t0 = B + 930_000;
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[("trade_size_p50_15m", 2, 2), ("trade_size_p90_15m", 64, 64)],
    );
    // Нижняя граница корзины, а не сам размер; крупная сделка.
    let mut g = flow_with_start();
    g.on_trade(&tr(B + 900_000, (1 << 40) + 5, true));
    assert_eq!(
        col(&at(&g, Side::Bid, B + 930_000), "trade_size_p50_15m"),
        1 << 40
    );
    // Нет сделок в окне — не определено.
    let h = flow_with_start();
    assert_eq!(
        col(&at(&h, Side::Bid, B + 930_000), "trade_size_p50_15m"),
        UNDEF
    );
}

type Ladder = Vec<(i64, i64)>;

fn ladder() -> (Ladder, Ladder) {
    let bids = (0..12).map(|i| (100 - i, 10 * (i + 1))).collect();
    let asks = (0..12).map(|i| (101 + i, 12)).collect();
    (bids, asks)
}

#[test]
fn obi_hand_calc() {
    let (bids, asks) = ladder();
    let mut f = R1Flow::new();
    frame(&mut f, B, &bids, &asks);
    let t0 = B + 1_000;
    // Бид-стена: N=1: (10−12)/22 = −909; N=5: (150−60)/210 = 4285; N=10: (550−120)/670 = 6417;
    // N=50 (в книге по 12): (780−144)/924 = 6883. У аск-стены знак зеркален.
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[
            ("obi1_bp", -909, 909),
            ("obi5_bp", 4285, -4285),
            ("obi10_bp", 6417, -6417),
            ("obi50_bp", 6883, -6883),
        ],
    );
    // Последний кадр заменяет прежний целиком: старые глубокие уровни не остаются.
    frame(&mut f, B + 2_000, &[(100, 30), (99, 10)], &[(101, 20)]);
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[
            ("obi1_bp", 2000, -2000),  // (30−20)/50
            ("obi50_bp", 3333, -3333), // (40−20)/60
        ],
    );
    // Одна сторона пуста: (own − 0)/own = 10⁴; обе пусты — не определено.
    frame(&mut f, B + 3_000, &[(100, 7)], &[]);
    assert_eq!(col(&at(&f, Side::Bid, t0), "obi1_bp"), 10_000);
    assert_eq!(col(&at(&f, Side::Ask, t0), "obi1_bp"), -10_000);
    frame(&mut f, B + 4_000, &[], &[]);
    assert_eq!(col(&at(&f, Side::Bid, t0), "obi1_bp"), UNDEF);
}

#[test]
fn book_deeper_than_depth_is_truncated() {
    let bids: Vec<(i64, i64)> = (0..60).map(|i| (200 - i, 1)).collect();
    let asks = [(201, 1), (202, 1), (203, 1)];
    let mut f = R1Flow::new();
    frame(&mut f, B, &bids, &asks);
    let o = at(&f, Side::Bid, B + 1_000);
    // obi50 берёт 50 уровней бидов: (50−3)/53; obi10: (10−3)/13.
    assert_eq!(col(&o, "obi50_bp"), 8867);
    assert_eq!(col(&o, "obi10_bp"), 5384);
}

#[test]
fn micro_price_offset_hand_calc() {
    let mut f = R1Flow::new();
    // bid 1000×30, ask 1004×10: 10⁶·4·20/(40·2004) = 998 (микроцена выше середины).
    fr1(&mut f, B, (1000, 30), (1004, 10));
    check(
        &at(&f, Side::Bid, B),
        &at(&f, Side::Ask, B),
        &[("micro_off_cbps", 998, -998)],
    );
    // bid 1000×10, ask 1004×31: −84·10⁶/82164 = −1022 (усечение к нулю, не −1023).
    fr1(&mut f, B + 1_000, (1000, 10), (1004, 31));
    check(
        &at(&f, Side::Bid, B + 1_000),
        &at(&f, Side::Ask, B + 1_000),
        &[("micro_off_cbps", -1022, 1022)],
    );
    // Равные размеры — ноль; пустая сторона — не определено.
    fr1(&mut f, B + 2_000, (1000, 10), (1004, 10));
    assert_eq!(col(&at(&f, Side::Bid, B + 2_000), "micro_off_cbps"), 0);
    frame(&mut f, B + 3_000, &[(1000, 10)], &[]);
    assert_eq!(col(&at(&f, Side::Bid, B + 3_000), "micro_off_cbps"), UNDEF);
}

/// Кадры OFI из ручного расчёта: переходы дают 5, 12, −11, −7 (секунды b+1, b+2, b+12, b+13).
fn ofi_flow() -> Box<R1Flow> {
    let mut f = R1Flow::new();
    fr1(&mut f, B, (100, 10), (101, 8));
    fr1(&mut f, B + 1_000, (100, 15), (101, 8)); // 15−10−8+8 = 5
    fr1(&mut f, B + 2_000, (101, 4), (102, 6)); // bid вверх: +4; ask вверх: +qa' = 8 → 12
    fr1(&mut f, B + 12_000, (100, 20), (101, 7)); // bid вниз: −qb' = −4; ask вниз: −qa = −7 → −11
    fr1(&mut f, B + 13_000, (100, 18), (101, 12)); // 18−20−12+7 = −7
    f
}

#[test]
fn ofi_hand_calc_and_window_edges() {
    let f = ofi_flow();
    // s0 = b+20: окно 10 с — переходы b+12 и b+13 → −18; 60 с ещё не прогрето.
    let (bid, ask) = (at(&f, Side::Bid, B + 20_500), at(&f, Side::Ask, B + 20_500));
    check(
        &bid,
        &ask,
        &[("ofi_10s_lots", -18, 18), ("ofi_60s_lots", UNDEF, UNDEF)],
    );
    // s0 = b+60: в окне 60 с все четыре перехода (b+1 имеет возраст 59): 5+12−11−7 = −1; 10 с — пусто.
    let t0 = B + 60_500;
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[("ofi_10s_lots", 0, 0), ("ofi_60s_lots", -1, 1)],
    );
    // s0 = b+61: переход b+1 (возраст 60) выпал: 12−11−7 = −6.
    let t0 = B + 61_500;
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[("ofi_60s_lots", -6, 6)],
    );
}

#[test]
fn ofi_ignores_repeated_and_incomplete_frames() {
    let mut f = ofi_flow();
    // Тот же кадр повторно (две стороны трекера шлют одну книгу): вкладов нет.
    fr1(&mut f, B + 13_000, (100, 18), (101, 12));
    fr1(&mut f, B + 13_500, (100, 18), (101, 12));
    // Кадр без аска, затем полный: перехода между ними нет — OFI не трогается.
    frame(&mut f, B + 14_000, &[(100, 18)], &[]);
    fr1(&mut f, B + 15_000, (100, 50), (101, 50));
    assert_eq!(col(&at(&f, Side::Bid, B + 20_500), "ofi_10s_lots"), -18);
}

#[test]
fn best_price_flips_hand_calc() {
    let mut f = R1Flow::new();
    fr1(&mut f, B, (100, 5), (110, 5));
    fr1(&mut f, B + 1_000, (101, 5), (110, 5)); // бид: смена в b+1
    fr1(&mut f, B + 2_000, (101, 5), (110, 5)); // без смены
    fr1(&mut f, B + 20_000, (101, 5), (111, 5)); // аск: b+20
    fr1(&mut f, B + 30_000, (102, 5), (111, 5)); // бид: b+30
    fr1(&mut f, B + 50_000, (101, 5), (111, 5)); // бид: b+50
                                                 // s0 = b+60: 15 с — окно b+46..b+60 (бид 1, аск 0); 60 с — b+1..b+60 (бид 3, аск 1).
                                                 // «Своя» сторона стены: у бид-стены — бид.
    let t0 = B + 60_500;
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[("best_flips_15s", 1, 0), ("best_flips_60s", 3, 1)],
    );
    // s0 = b+61: смена b+1 (возраст 60) выпала.
    let t0 = B + 61_500;
    check(
        &at(&f, Side::Bid, t0),
        &at(&f, Side::Ask, t0),
        &[("best_flips_60s", 2, 1)],
    );
}

#[test]
fn old_slots_do_not_leak_after_silence_or_wraparound() {
    // Слот секундного кольца занят секундой b+10, затем b+70 — тем же индексом.
    let mut f = flow_with_start();
    f.on_trade(&tr(B + 10_000, 7, false));
    f.on_trade(&tr(B + 70_000, 3, false));
    let o = at(&f, Side::Bid, B + 70_500);
    assert_eq!(col(&o, "tape_press_lots_60s"), 3);
    assert_eq!(col(&o, "tape_press_n_60s"), 1);

    // Долгое молчание: слот не перезаписан, но вне окна — в сумму не идёт.
    let mut g = flow_with_start();
    g.on_trade(&tr(B + 10_000, 7, false));
    let o = at(&g, Side::Bid, B + 500_000);
    assert_eq!(col(&o, "tape_press_lots_60s"), 0);
    assert_eq!(col(&o, "tape_press_n_60s"), 0);
    assert_eq!(col(&o, "tape_press_avg_30s_e2"), UNDEF);
    assert_eq!(col(&o, "sign_ac_60s_bp"), UNDEF);
    assert_eq!(col(&o, "trade_size_p50_15m"), UNDEF);

    // Минутное кольцо: минута 600 и минута 660 — один индекс.
    let mut h = flow_with_start();
    h.on_trade(&tr(B + 10_000, 1000, false));
    h.on_trade(&tr(39_605_000, 5, false));
    let o = at(&h, Side::Bid, 39_610_000);
    assert_eq!(col(&o, "tape_press_60m_lots"), 5);

    // OFI и смены цены в перезаписанном слоте.
    let mut k = R1Flow::new();
    fr1(&mut k, B, (100, 10), (101, 10));
    fr1(&mut k, B + 10_000, (101, 10), (102, 10)); // b+10: вклад и смены
    fr1(&mut k, B + 70_000, (101, 10), (102, 10)); // b+70, тот же слот: без вкладов
    let o = at(&k, Side::Bid, B + 70_500);
    assert_eq!(col(&o, "ofi_60s_lots"), 0);
    assert_eq!(col(&o, "best_flips_60s"), 0);
}

#[test]
fn late_event_does_not_overwrite_newer_slot() {
    let mut f = flow_with_start();
    f.on_trade(&tr(B + 100_000, 5, true)); // секунда b+100, индекс 40
    f.on_trade(&tr(B + 40_000, 9, true)); // b+40 — тот же индекс, запоздалая: отбрасывается
    let o = at(&f, Side::Ask, B + 100_500);
    assert_eq!(col(&o, "tape_all_lots_60s"), 5);
    assert_eq!(col(&o, "tape_all_n_60s"), 1);
    // Минутное кольцо: минута 700 и запоздалая 640 (индекс 40).
    let mut g = flow_with_start();
    g.on_trade(&tr(42_001_000, 4, true));
    g.on_trade(&tr(38_401_000, 9, true));
    let o = at(&g, Side::Ask, 42_010_000);
    assert_eq!(col(&o, "tape_press_60m_lots"), 4);
}

const K: i64 = 2_000;

/// Отражение книги: цена p → K − p, стороны меняются местами, порядок «лучшая первой» сохраняется.
fn mirrored(bids: &[(i64, i64)], asks: &[(i64, i64)]) -> (Ladder, Ladder) {
    let flip = |side: &[(i64, i64)]| side.iter().map(|&(p, q)| (K - p, q)).collect();
    (flip(asks), flip(bids))
}

/// Одна и та же лента и книга; `mirror` отражает цены, меняет стороны книги и агрессоров.
fn scenario(mirror: bool) -> Box<R1Flow> {
    let frames: [(i64, Ladder, Ladder); 5] = [
        (
            B,
            vec![(1000, 30), (999, 20), (998, 10)],
            vec![(1002, 10), (1003, 25), (1004, 5)],
        ),
        (
            B + 1_000,
            vec![(1000, 35), (999, 20)],
            vec![(1002, 8), (1003, 25)],
        ),
        (
            39_661_500,
            vec![(1001, 12), (1000, 35), (999, 20)],
            vec![(1003, 9), (1004, 5)],
        ),
        (
            39_664_500,
            vec![(1001, 12), (1000, 35)],
            vec![(1002, 20), (1003, 9)],
        ),
        // bid + ask = K: микроцена отражается в себя.
        (
            39_665_500,
            vec![(999, 40), (998, 3)],
            vec![(1001, 17), (1002, 20)],
        ),
    ];
    let trades = [
        (B + 600_000, 400, false),
        (39_620_000, 21, false),
        (39_640_000, 33, true),
        (39_661_000, 40, true),
        (39_662_000, 360, false),
        (39_663_000, 200, true),
        (39_664_000, 100, false),
        (39_665_000, 150, true),
        (39_666_000, 100, false),
    ];
    let mut f = R1Flow::new();
    for (i, (ms, bids, asks)) in frames.iter().enumerate() {
        if i == 1 {
            for &(t, lots, buy) in &trades {
                f.on_trade(&tr(t, lots, buy != mirror));
            }
        }
        if mirror {
            let (b, a) = mirrored(bids, asks);
            frame(&mut f, *ms, &b, &a);
        } else {
            frame(&mut f, *ms, bids, asks);
        }
    }
    f
}

#[test]
fn mirror_symmetry_of_all_columns() {
    let t0 = 39_666_500;
    let orig = scenario(false);
    let mir = scenario(true);
    let bid = at(&orig, Side::Bid, t0);
    let ask = at(&orig, Side::Ask, t0);
    // Сценарий нетривиален: все 40 колонок определены, а стороны стены различаются.
    assert!(bid.iter().all(|&v| v != UNDEF), "{bid:?}");
    assert!(ask.iter().all(|&v| v != UNDEF), "{ask:?}");
    assert_ne!(bid, ask);
    // Бид-стена исходных данных = аск-стена отражённых, и наоборот.
    assert_eq!(bid, at(&mir, Side::Ask, t0));
    assert_eq!(ask, at(&mir, Side::Bid, t0));
    assert_ne!(bid, at(&mir, Side::Bid, t0));
}

#[test]
fn ratio_and_orient_helpers() {
    assert_eq!(ratio(5, 0), UNDEF);
    assert_eq!(ratio(-7, 2), -3);
    assert_eq!(ratio(7, 2), 3);
    assert_eq!(ratio(i128::MAX, 1), i64::MAX);
    assert_eq!(ratio(i128::MIN, 1), i64::MIN + 1);
    assert_eq!(orient(UNDEF, true), UNDEF);
    assert_eq!(orient(5, true), -5);
    assert_eq!(orient(5, false), 5);
}
