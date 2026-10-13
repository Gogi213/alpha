use super::*;

/// T-35 (Г-85): ключ `frontrun_min=<лоты>` — порог фронтрана вместо «да/нет»; порог 1 = `frontrun`, огромный —
/// ни одного сигнала; без ключа шапка прежняя.
#[test]
fn frontrun_min_key_filters_by_frontrun_lots_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-frmin");
    a.sets = vec![
        "fr:frontrun".to_string(),
        "fm1:frontrun_min=1".to_string(),
        "fmbig:frontrun_min=1000000000".to_string(),
    ];
    let multi = run_bounce_grid(&a).unwrap();
    let by_name = |n: &str| multi.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    // фронтран есть ⇔ впереди уровня ≥ 1 лота: порог 1 — те же касания, что «да/нет» `frontrun`
    assert_eq!(
        body(&by_name("fm1").rounds_path),
        body(&by_name("fr").rounds_path)
    );
    let (fh, big) = read_csv(&by_name("fmbig").forms_path);
    for r in &big {
        assert_eq!(col(&fh, r, "n_signals"), "0", "{r:?}");
    }
    let head = std::fs::read_to_string(&by_name("fm1").forms_path).unwrap();
    assert!(
        head.contains(" frontrun_only=false frontrun_min=1 "),
        "{head}"
    );
    let head = std::fs::read_to_string(&by_name("fr").forms_path).unwrap();
    assert!(!head.contains("frontrun_min"), "без ключа шапка прежняя");
    for bad in ["x:frontrun_min=0", "x:frontrun_min=-1", "x:frontrun_min=a"] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
}

/// М-04 (TK-206): ключ `lot_first=<k>` — множитель размера на первом подходе; k = 1 — байт в байт как без ключа,
/// ключ идёт в шапку набора только заданным, не число > 0 — отказ.
#[test]
fn lot_first_key_one_equals_base_and_marks_header() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-lotfirst");
    a.sets = vec![
        "base:frontrun".to_string(),
        "lf1:frontrun,lot_first=1".to_string(),
        "lf2:frontrun,lot_first=2".to_string(),
    ];
    let multi = run_bounce_grid(&a).unwrap();
    let by_name = |n: &str| multi.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("
")
    };
    assert_eq!(
        body(&by_name("lf1").rounds_path),
        body(&by_name("base").rounds_path)
    );
    let head = std::fs::read_to_string(&by_name("lf2").forms_path).unwrap();
    assert!(head.contains(" lot_first=2"), "{head}");
    let head = std::fs::read_to_string(&by_name("base").forms_path).unwrap();
    assert!(!head.contains("lot_first"), "без ключа шапка прежняя");
    for bad in [
        "x:lot_first=0",
        "x:lot_first=-1",
        "x:lot_first=a",
        "x:lot_first=inf",
    ] {
        assert!(FilterSet::parse(bad).is_err(), "{bad}");
    }
}

/// Ключ `eaten=<%>` (S1 плана по сторонам): `eaten=100` ничего не выбивает —
/// байты те же, что без ключа; `eaten=-1` выбивает все касания в
/// `n_skipped`; фикстура: у первого касания стена целая (`eaten` 0), у
/// следующих — частично съедена, `eaten=0` оставляет только целые.
#[test]
fn eaten_key_filters_by_wall_state_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-eaten");
    a.sets = vec![
        "all:".to_string(),
        "e100:eaten=100".to_string(),
        "e0:eaten=0".to_string(),
        "none:eaten=-1".to_string(),
    ];
    let multi = run_bounce_grid(&a).unwrap();
    let by_name = |n: &str| multi.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(
        body(&by_name("e100").rounds_path),
        body(&by_name("all").rounds_path)
    );
    assert_eq!(
        body(&by_name("e100").forms_path),
        body(&by_name("all").forms_path)
    );
    let (fh, none) = read_csv(&by_name("none").forms_path);
    for r in &none {
        assert_eq!(col(&fh, r, "n_signals"), "0");
        assert_eq!(col(&fh, r, "n_skipped"), "3");
    }
    let (fh, e0) = read_csv(&by_name("e0").forms_path);
    let (_, all) = read_csv(&by_name("all").forms_path);
    for (r0, ra) in e0.iter().zip(&all) {
        let s0: u64 = col(&fh, r0, "n_signals").parse().unwrap();
        let sa: u64 = col(&fh, ra, "n_signals").parse().unwrap();
        assert!(s0 <= sa, "eaten=0 — подмножество: {r0:?}");
        let k0: u64 = col(&fh, r0, "n_skipped").parse().unwrap();
        assert_eq!(s0 + k0, 3);
    }
    let head = std::fs::read_to_string(&by_name("e0").forms_path).unwrap();
    assert!(head.contains(" eaten_max=Some(0.0) "), "{head}");
    assert_eq!(
        eaten_pct(&TouchRecord {
            size_at_touch: 3,
            size_max_before: 12,
            ..probe_touch()
        }),
        75.0
    );
    assert_eq!(
        eaten_pct(&TouchRecord {
            size_at_touch: 5,
            size_max_before: 0,
            ..probe_touch()
        }),
        0.0
    );
}

pub(super) fn probe_touch() -> TouchRecord {
    TouchRecord {
        side: Side::Bid,
        price_tick: 1,
        touch_index: 0,
        start_ms: 0,
        end_ms: 0,
        duration_ms: 0,
        level_birth_ms: 0,
        size_at_touch: 0,
        size_max_before: 0,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: 0,
        ended_by_death: false,
        stack_levels: 0,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [-1; 3],
        strength_held_e2: [-1; 4],
        repeat_count: 0,
        depth_behind_lots: 0,
    }
}

/// Ключи контекста (S4): без них байты те же (набор `all`); граница по ходу
/// монеты на фикстуре (6 с записи — хода нет) выбивает всё; ключ режима без
/// `--regime-from` — отказ; с режимом на минуту касания — фильтр по значению
/// из файла суток (пул +50 bps за 4 ч: `pool4h_min=40` пропускает всё,
/// `pool4h_min=60` — ничего); парсер границ; `Range::holds`.
#[test]
fn context_keys_filter_by_pre_touch_return_and_regime() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let plain = run_bounce_grid(&args(dir.path(), false)).unwrap();

    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-ctx");
    a.sets = vec!["all:".to_string(), "r:ret1h_min=-100000".to_string()];
    let multi = run_bounce_grid(&a).unwrap();
    let by_name =
        |m: &BounceGridSummary, n: &str| m.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(
        body(&by_name(&multi, "all").rounds_path),
        body(&plain.rounds_path)
    );
    let (fh, r) = read_csv(&by_name(&multi, "r").forms_path);
    for row in &r {
        assert_eq!(
            col(&fh, row, "n_signals"),
            "0",
            "хода до касания на фикстуре нет"
        );
        assert_eq!(col(&fh, row, "n_skipped"), "3");
    }
    let head = std::fs::read_to_string(&by_name(&multi, "r").forms_path).unwrap();
    assert!(head.contains(" ctx=ret1h_min=-100000 "), "{head}");

    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-noregime");
    a.sets = vec!["p:pool4h_min=0".to_string()];
    assert!(
        run_bounce_grid(&a).is_err(),
        "ключ режима без --regime-from — отказ"
    );

    // Режим суток: одна строка на минуту фикстуры (касания в первой минуте
    // суток 2026-09-08), пул +50 bps за 4 ч, биток пусто.
    let regime = dir.path().join("regime");
    std::fs::create_dir_all(&regime).unwrap();
    // Касания фикстуры — на 70–76 с записи: минуты 0, 60 000 и 120 000 мс
    // покрывают их с запасом.
    let rows: String = [0i64, 60_000, 120_000]
        .iter()
        .map(|m| format!("{m},10.0,50.0,5,,,,\n"))
        .collect();
    std::fs::write(
        regime.join("2026-09-08.csv"),
        format!(
            "minute_ms,pool_ret_1h_bps,pool_ret_4h_bps,n_coins,btc_ret_1h_bps,btc_ret_4h_bps,eth_ret_1h_bps,eth_ret_4h_bps\n{rows}"
        ),
    )
    .unwrap();
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-regime");
    a.regime_from = Some(regime);
    a.sets = vec![
        "p40:pool4h_min=40".to_string(),
        "p60:pool4h_min=60".to_string(),
        "b:btc1h_max=0".to_string(),
    ];
    let m = run_bounce_grid(&a).unwrap();
    assert_eq!(
        body(&by_name(&m, "p40").rounds_path),
        body(&plain.rounds_path),
        "режим держит — те же круги"
    );
    let (fh, p60) = read_csv(&by_name(&m, "p60").forms_path);
    assert!(
        p60.iter().all(|r| col(&fh, r, "n_signals") == "0"),
        "пул ниже границы — нет сигналов"
    );
    let (fh, b) = read_csv(&by_name(&m, "b").forms_path);
    assert!(
        b.iter().all(|r| col(&fh, r, "n_signals") == "0"),
        "битка на минуту нет — выбывает"
    );

    let set = FilterSet::parse("x:ret4h_max=-5.5,pool1h_min=1,btc4h_min=-1,btc4h_max=1").unwrap();
    assert_eq!(
        set.ctx[2],
        Range {
            min: None,
            max: Some(-5.5)
        }
    );
    assert_eq!(
        set.ctx[3],
        Range {
            min: Some(1.0),
            max: None
        }
    );
    assert_eq!(
        set.ctx[6],
        Range {
            min: Some(-1.0),
            max: Some(1.0)
        }
    );
    assert_eq!(
        set.ctx_label(),
        "ret4h_max=-5.5,pool1h_min=1,btc4h_min=-1,btc4h_max=1"
    );
    let r = Range {
        min: Some(0.0),
        max: Some(10.0),
    };
    assert!(r.holds(Some(0.0)) && r.holds(Some(10.0)) && !r.holds(Some(10.1)) && !r.holds(None));
    assert!(Range::default().holds(None));
}

/// Ключ `usd_min=<$>`: номинал стены при касании (цена × размер) ≥ порога;
/// фикстура — бид 99 × тик 0.01 × 10 лотов × шаг 0.1 ≈ $0.99: порог 0 не
/// выбивает (байты те же), порог 1 выбивает всё.
#[test]
fn usd_min_key_filters_by_wall_notional_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-usd");
    a.sets = vec![
        "all:".to_string(),
        "u0:usd_min=0".to_string(),
        "u1:usd_min=1".to_string(),
    ];
    let m = run_bounce_grid(&a).unwrap();
    let by = |n: &str| m.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(body(&by("u0").rounds_path), body(&by("all").rounds_path));
    let (fh, u1) = read_csv(&by("u1").forms_path);
    assert!(u1
        .iter()
        .all(|r| col(&fh, r, "n_signals") == "0" && col(&fh, r, "n_skipped") == "3"));
    let head = std::fs::read_to_string(&by("u1").forms_path).unwrap();
    assert!(head.contains(" usd_min=Some(1.0) "), "{head}");
}

/// T4 (П-02, Г-86): ключ `eaten_min=<%>` — зеркало `eaten=<%>` (там «не
/// больше», здесь «не меньше»). Фикстура `touch_frames()` — размер тика 99
/// не падает **до** ни одного из трёх касаний (`size_max_before ==
/// size_at_touch`), поэтому `eaten_pct == 0` у всех: порог 0 не выбивает
/// ничего (байты те же), любой порог выше нуля выбивает все три.
#[test]
fn eaten_min_key_filters_by_wall_erosion_at_touch() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.out_dir = dir.path().join("grid-eaten-min");
    a.sets = vec![
        "all:".to_string(),
        "e0:eaten_min=0".to_string(),
        "e1:eaten_min=1".to_string(),
    ];
    let m = run_bounce_grid(&a).unwrap();
    let by = |n: &str| m.sets.iter().find(|s| s.name == n).unwrap().clone();
    let body = |p: &std::path::Path| -> String {
        std::fs::read_to_string(p)
            .unwrap()
            .lines()
            .filter(|l| !l.starts_with('#'))
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert_eq!(body(&by("e0").rounds_path), body(&by("all").rounds_path));
    let (fh, e1) = read_csv(&by("e1").forms_path);
    assert!(e1
        .iter()
        .all(|r| col(&fh, r, "n_signals") == "0" && col(&fh, r, "n_skipped") == "3"));
    let head = std::fs::read_to_string(&by("e1").forms_path).unwrap();
    assert!(head.contains(" eaten_min=Some(1.0) "), "{head}");
}

/// `--signal approach` не определяет `eaten_min=` (T4) так же, как `eaten=`
/// — размер на взводе и есть старт, истории размера нет.
#[test]
fn eaten_min_key_is_rejected_with_approach_signal() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    // Не-σ формы и снятые умолчания трекера — требования `--touches-from`
    // (проверяются раньше нашего ключа, см. `resolve_plan`); сам кэш не
    // читается до этого отказа, поэтому подойдёт любой существующий каталог.
    a.stop_form = vec!["behind".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.warmup_ms = None;
    a.repeat_window_ms = None;
    a.signal = SignalArg::Approach;
    a.touches_from = Some(dir.path().to_path_buf());
    a.sets = vec!["x:eaten_min=1".to_string()];
    let err = run_bounce_grid(&a).unwrap_err().to_string();
    assert!(err.contains("eaten_min"), "{err}");
}

/// T4 (П-02, Г-86): `--entry-form market` — вход пересекает спред
/// (`entry_crossed`), но не отклоняется как пост-онли (В-72 не действует на
/// рыночный вход: `post_only` снят самой формой) и исполняется.
#[test]
fn entry_form_market_crosses_the_spread_instead_of_being_rejected_postonly() {
    // Свой, тесный спред: `touch_frames()`/`fixture_root()` держат аск в
    // 600–700 bps от стены (99 против 105/106 — простор для теста подхода и
    // ряда σ), а рыночному входу нужен реалистичный спред, который его
    // запас (`MARKET_CROSS_MARGIN_BPS = 500`) пересечёт: аск в 3 тиках от
    // стены (99 → 102, ≈ 300 bps).
    fn tight_spread_frames() -> Vec<Vec<crate::binlog::Record>> {
        vec![
            snap_frame(0, &[(98, 10), (99, 10), (100, 10)], &[(102, 10)]),
            delta_frame(1000, &[(100, 0)], &[]), // 100 снят — 99 лучший: касание.
            delta_frame(2000, &[(100, 10)], &[]), // конец касания.
        ]
    }
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        crate::commands::record::instruments_csv_path(dir.path()),
        "symbol,tick_size,min_order_qty,qty_step,min_notional_value,h3_lots\n\
         SOLUSDT,0.01,0.1,0.1,5,5\n",
    )
    .unwrap();
    write_day(dir.path(), "SOLUSDT", "2026-09-08", &tight_spread_frames());
    std::fs::write(
        dir.path().join("session.json"),
        "{\"started_utc\":\"2026-09-08T00:00:00Z\",\"start_hour_utc\":0,\"instruments\":[\"SOLUSDT\"]}",
    )
    .unwrap();
    std::fs::write(dir.path().join("verify-SOLUSDT.status"), "ok").unwrap();

    let mut a = args(dir.path(), false);
    a.stop_form = vec!["behind".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.entry_form = vec!["market".to_string()];
    a.out_dir = dir.path().join("grid-market");
    let m = run_bounce_grid(&a).unwrap();
    // Фикстура — три кадра (снимок, снятие 100, возврат 100): достаточно
    // событий, чтобы сигнал построился и заявка ушла и пересекла спред, но
    // не достаточно, чтобы обычный дедлайн (мин. 60 с) успел разрешить
    // круг целиком (`incomplete=true`, ожидаемо на такой короткой записи —
    // не предмет этого теста). Проверяем сам механизм T4: заявка
    // действительно **пересекла** спред (`entry_crossed`) и не была
    // отклонена как пост-онли (`n_rejected_postonly`) — это и отличает
    // `market` от `single@fr`/`ladder…`, которые на этой же фикстуре и с тем
    // же `--post-only` (умолчание команды) были бы отклонены `Expired`.
    let (fh, rows) = read_csv(&m.forms_path);
    assert!(!rows.is_empty());
    for r in &rows {
        assert_eq!(
            col(&fh, r, "n_rejected_postonly"),
            "0",
            "рыночный вход не пост-онли — GTX его бы отклонил как Expired"
        );
        assert!(
            col(&fh, r, "entry_crossed").parse::<u32>().unwrap() >= 1,
            "рыночный вход обязан пересечь спред: {r:?}"
        );
    }
}

/// Гейт F3/F4: `--queue-model risk-adverse --no-post-only` — прежний движок
/// и прежний вход (обычный лимит `GTC`), и круг (`rounds.csv`) и числа форм
/// (`forms.csv`) обязаны совпасть с прогоном до правок. «Золото» — снятый до
/// правки вывод фикстуры (`golden/rounds.csv`, `golden/forms.csv`):
/// сравниваются все поля по именам, поэтому добавленные правками колонки
/// (`n_fill_by_cross` у F3; `fill_frac`/`entry_vwap`/`legs_filled`/
/// `legs_rejected` у F4) и поле шапки (`queue=…`, `entry_post_only=…`) гейт не
/// обманывают, а любое расхождение прежнего поля — валит. Пост-онли новое
/// умолчание (В-72), поэтому гейт гоняется именно с `--no-post-only`.
#[test]
fn risk_adverse_queue_model_keeps_the_old_bytes() {
    let dir = tempfile::tempdir().unwrap();
    fixture_root(dir.path(), true);
    let mut a = args(dir.path(), false);
    a.stop_form = vec!["pct1".to_string(), "behind".to_string()];
    a.take_form = vec!["1to1".to_string()];
    a.take_floor_fees = None;
    a.h3 = H3Args {
        h3_mode: H3ModeArg::Floor,
        h3_lots: None,
        h3_usd: None,
        h3_strength_pct: None,
        h3_strength_window_bps: None,
    };
    a.warmup_ms = None;
    a.repeat_window_ms = None;
    a.no_post_only = true;
    // F5 (В-74): гейт «те же круги» — с явным прежним режимом входа
    // (`touch`), условия рынка выключены.
    a.entry_ttl_secs = vec!["touch".to_string()];
    // F6 (В-73): та же явная прежняя форма входа — одиночная нога у
    // фронтранера; имя формы остаётся трёхпольным (гейт).
    a.entry_form = vec!["single@fr".to_string()];
    a.out_dir = dir.path().join("grid-gate");
    let m = run_bounce_grid(&a).unwrap();
    assert!(m.rounds > 0, "фикстура обязана давать круги");

    // Поля «золота» по именам: прямой байтовый диф невозможен — шапка несёт
    // `queue=…`, а у `forms.csv` правка F3 добавила колонку.
    let same_fields = |got: &std::path::Path, golden: &str, skip: &[&str]| {
        let (gh, grows) = read_csv_from(golden.as_bytes());
        let (h, rows) = read_csv(got);
        assert_eq!(rows.len(), grows.len(), "{got:?}: число строк");
        for (r, g) in rows.iter().zip(&grows) {
            for (k, name) in gh.iter().enumerate() {
                if skip.contains(&name.as_str()) {
                    continue;
                }
                assert_eq!(
                    col(&h, r, name),
                    g[k],
                    "{got:?}: колонка {name} разошлась (строка {r:?})"
                );
            }
        }
        h
    };

    let head = std::fs::read_to_string(&m.forms_path).unwrap();
    assert!(
        head.contains(" queue=risk-adverse "),
        "модель очереди в шапке: {head}"
    );
    assert!(
        head.contains(" entry_post_only=false "),
        "режим входа в шапке (гейт гоняется с --no-post-only): {head}"
    );
    assert!(
        head.contains(" entry_ttl=touch ") && head.contains(" band_exit_bps=0 "),
        "прежний режим срока жизни входа в шапке (гейт F5): {head}"
    );
    assert!(
        head.contains(" signal=touch ") && head.contains(" entry_forms=single@fr "),
        "прежний сигнал и прежняя форма входа в шапке (гейт F6): {head}"
    );
    assert!(
        head.contains("queue=risk-adverse")
            && head.contains("paths=1:сделки-на-нашей-цене-частично"),
        "три пути исполнения крейта в шапке: {head}"
    );
    let fh = same_fields(
        &m.forms_path,
        include_str!("../golden/forms.csv"),
        &["n_fill_by_cross"],
    );
    let (_, forms) = read_csv(&m.forms_path);
    assert_eq!(forms.len(), 8, "строка на форму");
    for r in &forms {
        assert_eq!(
            col(&fh, r, "n_fill_by_cross"),
            "0",
            "прежний движок исполняет целыми заявками: {r:?}"
        );
        assert_eq!(
            col(&fh, r, "n_rejected_postonly"),
            "0",
            "вход не пересекал спред — отвергнутых ног нет: {r:?}"
        );
    }
    same_fields(&m.rounds_path, include_str!("../golden/rounds.csv"), &[]);
    // Колонки F4 осмысленны на круге фикстуры: вход исполнился целиком одной
    // ногой, поэтому доля — единица, средняя равна цене входа, ног — одна, а
    // не поставленных биржей — ноль (В-78, В-72).
    let (rh, rounds) = read_csv(&m.rounds_path);
    assert!(!rounds.is_empty(), "фикстура обязана давать круги");
    for r in &rounds {
        assert_eq!(col(&rh, r, "fill_frac"), "1.000000", "{r:?}");
        assert_eq!(
            col(&rh, r, "entry_vwap"),
            col(&rh, r, "entry_px"),
            "полное исполнение: средняя равна цене входа — {r:?}"
        );
        assert_eq!(col(&rh, r, "legs_filled"), "1", "{r:?}");
        assert_eq!(col(&rh, r, "legs_rejected"), "0", "{r:?}");
    }

    // Разбор флага: без `--queue-model` команда не запускается вовсе
    // (умолчания в коде нет), `prob:<n>` собирает свой движок.
    assert!(QueueModelKind::parse("").is_err());
    let mut b = args(dir.path(), false);
    b.queue_model = QueueModelKind::Prob { n: 3.0 };
    b.out_dir = dir.path().join("grid-prob");
    let p = run_bounce_grid(&b).unwrap();
    assert_eq!(p.forms, 8, "формы те же, движок другой");
    let head = std::fs::read_to_string(&p.forms_path).unwrap();
    assert!(head.contains(" queue=prob:3 "), "{head}");
    let (ph, prows) = read_csv(&p.forms_path);
    assert_eq!(prows.len(), 8);
    for r in &prows {
        // Колонка есть у обеих моделей; значение — счётчик пути (3).
        assert!(
            col(&ph, r, "n_fill_by_cross").parse::<u64>().is_ok(),
            "счётчик пути (3) обязан быть числом: {r:?}"
        );
    }
}

/// Реестр осей контекста (пакет C, В-227): прежние индексы не сдвинуты, оси хода монеты
/// первые, имена уникальны, слот режима = индекс оси − `COIN_AXES`.
#[test]
fn ctx_registry_keeps_old_indices() {
    use super::super::sets::{
        CtxSrc, COIN_AXES, CTX_SPECS, KLINE_AXES, LEVEL_AXES, REGIME_AXES, SEC_AXES,
    };
    let old = [
        "ret10m",
        "ret1h",
        "ret4h",
        "pool1h",
        "pool4h",
        "btc1h",
        "btc4h",
        "btc2h",
        "btc3h",
        "btc6h",
        "btc8h",
        "btc12h",
        "btc24h",
        "rng1h",
        "dlow",
        "drnd",
        "btc_lag_1s",
        "btc_lag_2s",
        "btc_lag_4s",
        "btc_lag_8s",
        "btc_lag_16s",
        "btc_lag_32s",
        "btc_lag_64s",
        "btc_lag_118s",
    ];
    assert_eq!(&CTX_AXES[..old.len()], &old);
    for (i, a) in CTX_SPECS.iter().enumerate() {
        assert_eq!(
            matches!(a.src, CtxSrc::Coin(_)),
            i < COIN_AXES,
            "{}",
            a.name
        );
        if let CtxSrc::Coin(slot) = a.src {
            assert_eq!(slot, i);
        }
        assert_eq!(CTX_AXES.iter().filter(|n| **n == a.name).count(), 1);
    }
    assert_eq!(
        REGIME_AXES,
        CTX_SPECS.len() - COIN_AXES - KLINE_AXES - LEVEL_AXES - SEC_AXES
    );
    let sec = CTX_SPECS.len() - SEC_AXES;
    assert!(CTX_SPECS[sec..]
        .iter()
        .all(|a| matches!(a.src, CtxSrc::BtcSeconds(_))));
    let tail = sec - LEVEL_AXES;
    assert!(CTX_SPECS[tail - KLINE_AXES..tail]
        .iter()
        .all(|a| matches!(a.src, CtxSrc::Klines(_))));
    assert!(CTX_SPECS[tail..sec]
        .iter()
        .all(|a| matches!(a.src, CtxSrc::RoundDist)));
}

/// Оси по свечам монеты (Г-57/Г-08): окно — только минуты до касания; неполный час и пустые
/// сутки — `None`; расстояние — от цены уровня до минимума суток к моменту касания.
#[test]
fn kline_axes_use_only_past_minutes() {
    use super::super::sets::{KlineAxis, KlineSeries};
    let dir = std::env::temp_dir().join(format!("tk204-klines-{}", std::process::id()));
    std::fs::create_dir_all(dir.join("jan")).unwrap();
    let day0 = 1_767_225_600_000_i64; // 2026-01-01 00:00 UTC
    let mut csv = String::from("minute_ms,open,high,low,close,volume,turnover\n");
    for i in 0..120_i64 {
        let (hi, lo) = (100.0 + (i % 10) as f64, 99.0 - (i % 7) as f64);
        csv += &format!("{},0,{hi},{lo},100,0,0\n", day0 + i * 60_000);
    }
    std::fs::write(dir.join("jan").join("ref-XUSDT-1m.csv"), csv).unwrap();
    let mut k = KlineSeries::default();
    k.ensure_day(&dir, "XUSDT", "2026-01-01").unwrap();
    let t = day0 + 90 * 60_000;
    // минуты 30..90: high до 109, low до 93 → (109 − 93) / 100 · 1e4
    let rng = k.axis(KlineAxis::Rng1h, t, 10_000, 0.01).unwrap();
    assert!((rng - 1600.0).abs() < 1e-9, "{rng}");
    assert_eq!(
        k.axis(KlineAxis::Rng1h, day0 + 30 * 60_000, 10_000, 0.01),
        None
    );
    // минимум суток до t — 93 (минута 6 по циклу low = 99 − 6)
    let d = k.axis(KlineAxis::DayLowDist, t, 10_000, 0.01).unwrap();
    assert_eq!(d, 700.0); // (100 − 93) / 0.01 тиков — точно, целые тики
                          // граница dlow_max = k: касание ровно на k тиков выше минимума суток (93) даёт ровно k
    for tick in [1e-6, 0.0001, 0.01, 0.1, 0.5] {
        let low_ticks = (93.0_f64 / tick).round() as i64;
        for dist in [0_i64, 1, 4, 16] {
            let d = k.axis(KlineAxis::DayLowDist, t, low_ticks + dist, tick);
            assert_eq!(d, Some(dist as f64), "tick {tick} dist {dist}");
        }
    }
    assert_eq!(k.axis(KlineAxis::DayLowDist, day0, 10_000, 0.01), None);
    assert!(KlineSeries::default()
        .ensure_day(&dir, "NOPEUSDT", "2026-01-01")
        .is_err());
    std::fs::remove_dir_all(&dir).ok();
}

/// `drnd`: расстояние цены уровня до ближайшего кратного 100 тиков, тики.
#[test]
fn round_dist_axis_is_ticks_to_nearest_hundred() {
    use super::super::sets::{round_dist, CtxSrc, CTX_SPECS};
    let i = CTX_SPECS
        .iter()
        .position(|a| matches!(a.src, CtxSrc::RoundDist))
        .unwrap();
    assert_eq!(CTX_AXES[i], "drnd");
    for (p, want) in [
        (0, 0.0),
        (7, 7.0),
        (93, 7.0),
        (150, 50.0),
        (10_200, 0.0),
        (-3, 3.0),
    ] {
        assert_eq!(round_dist(p), want, "{p}");
    }
}

/// Оси секундного ряда BTC (Г-72): окно — только закрытые секунды до секунды касания; нет
/// секунд у границы ряда — `None` (не 0); ход в bps по тикам.
#[test]
fn btc_lag_axes_use_only_closed_seconds() {
    use super::super::sets::BtcSeconds;
    let dir = std::env::temp_dir().join(format!("tk204-btcsec-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let day0 = 1_767_225_600_000_i64; // 2026-01-01 00:00 UTC
    let mut csv = String::from(
        "second_ms,close_ticks
",
    );
    for i in 0..86_400_i64 {
        // close секунды i = 1_000_000 + i тиков
        csv.push_str(&format!(
            "{},{}
",
            day0 + i * 1000,
            1_000_000 + i
        ));
    }
    std::fs::write(dir.join("2026-01-01.csv"), csv).unwrap();
    let mut b = BtcSeconds::default();
    b.ensure_day(&dir, "2026-01-01").unwrap();
    // касание в секунде 100.5: последняя закрытая секунда 99 (close 1_000_099), лаг 4 → секунда 95
    let start = day0 + 100_500;
    let want = (1_000_099 - 1_000_095) as f64 / 1_000_095_f64 * 1e4;
    assert_eq!(b.lag_bps(4, start), Some(want));
    // секунда касания не входит: касание в 100.0 даёт то же окно (последняя закрытая — 99)
    assert_eq!(b.lag_bps(4, day0 + 100_000), Some(want));
    // окно уходит за начало ряда (сутки до — нет файла) → None
    assert_eq!(b.lag_bps(118, day0 + 100_000), None);
    assert!(b.lag_bps(1, day0 + 500).is_none());
    assert!(b.lag_bps(1, day0 + 2_500).is_some());
    // нет файла суток — отказ
    assert!(BtcSeconds::default()
        .ensure_day(&dir, "2026-01-02")
        .is_err());
    std::fs::remove_dir_all(&dir).ok();
}
