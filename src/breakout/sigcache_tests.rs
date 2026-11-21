//! Тесты TK-607 (L2): ключ кэша сигналов учитывает каждое поле `SigCfg`/`LevelCfg`, сторону и входы запуска; запись кодируется без потерь; повреждение — промах.

use super::params::{Params, SigCfg};
use super::sigcache::{cfg_key_text, roundtrip, SigCache, SigCacheOpts};
use super::tick::{TSig, TickCtr};
use super::Side;
use std::path::Path;

type Mutator = Box<dyn Fn(&mut SigCfg)>;

#[test]
fn cfg_key_text_changes_with_every_field() {
    let base = Params::base().sig;
    let muts: Vec<(&str, Mutator)> = vec![
        ("tf_min", Box::new(|c| c.lv.tf_min += 1)),
        ("k", Box::new(|c| c.lv.k += 1)),
        ("d_pm", Box::new(|c| c.lv.d_pm += 1)),
        ("n", Box::new(|c| c.lv.n += 1)),
        ("tau_pm", Box::new(|c| c.lv.tau_pm += 1)),
        ("l_days", Box::new(|c| c.lv.l_days += 1)),
        ("pk", Box::new(|c| c.lv.pk = !c.lv.pk)),
        ("lx", Box::new(|c| c.lv.lx += 1)),
        ("lw", Box::new(|c| c.lv.lw += 1)),
        ("natr_p", Box::new(|c| c.natr_p += 1)),
        ("natr_min_pm", Box::new(|c| c.natr_min_pm += 1)),
        ("t1h_u", Box::new(|c| c.t1h_u += 1)),
        ("t24_u", Box::new(|c| c.t24_u += 1)),
        ("track_pm", Box::new(|c| c.track_pm += 1)),
        ("e_pm", Box::new(|c| c.e_pm += 1)),
        ("color", Box::new(|c| c.color = !c.color)),
        ("f_pm", Box::new(|c| c.f_pm += 1)),
        ("r_min", Box::new(|c| c.r_min += 1)),
        ("ban", Box::new(|c| c.ban = !c.ban)),
        ("ap", Box::new(|c| c.ap += 1)),
        ("pb", Box::new(|c| c.pb = !c.pb)),
        ("fr_h_min", Box::new(|c| c.fr_h_min += 1)),
        ("fr_k", Box::new(|c| c.fr_k += 1)),
        ("fr_share", Box::new(|c| c.fr_share += 1)),
        ("fr_b_pm", Box::new(|c| c.fr_b_pm += 1)),
        ("fg_m", Box::new(|c| c.fg_m += 1)),
    ];
    let t0 = cfg_key_text(&base);
    assert_eq!(t0, cfg_key_text(&base.clone()));
    for (name, m) in &muts {
        let mut c = base.clone();
        m(&mut c);
        assert_ne!(cfg_key_text(&c), t0, "поле {name} не влияет на ключ");
    }
}

/// Текст ключа базовой конфигурации — дословно как в `master` (до TK-668): иначе промахнутся все записи `sig-cache` (В-213).
#[test]
fn base_key_text_matches_master() {
    let want = "lv[tf_min=60,k=2,d_pm=500,n=2,tau_pm=200,l_days=7,pk=false],natr_p=30,natr_min_pm=400,t1h_u=50000000000,t24_u=0,\
                track_pm=2000,e_pm=300,color=true,f_pm=100,r_min=15,ban=true,ap=0,pb=false,fr_h_min=0,fr_k=120,fr_share=0,fr_b_pm=500";
    assert_eq!(cfg_key_text(&Params::base().sig), want);
}

#[test]
fn lx_lw_nonzero_keys_differ_from_base_and_each_other() {
    let base = Params::base().sig;
    let k0 = cfg_key_text(&base);
    let with = |lx: u8, lw: usize| {
        let mut c = base.clone();
        c.lv.lx = lx;
        c.lv.lw = lw;
        cfg_key_text(&c)
    };
    let keys = [k0, with(1, 0), with(2, 0), with(2, 3), with(0, 3)];
    let distinct: std::collections::HashSet<&String> = keys.iter().collect();
    assert_eq!(distinct.len(), keys.len(), "{keys:?}");
}

fn sig(i: u64) -> TSig {
    TSig {
        ms: i64::MAX - i as i64 * 7,
        sym: (i % 600) as u16,
        level: i as u32 * 31,
        dist: (i * 977) as u32,
        t: (i * 3) as u32,
        day: (i % 80) as u16,
        idx: u32::MAX - i as u32,
        lp: -(i as i64) * 123_456_789_012,
        far: i64::MIN + i as i64,
        ws: i % 2 == 1,
    }
}

fn same(a: &TSig, b: &TSig) -> bool {
    format!("{a:?}") == format!("{b:?}")
}

#[test]
fn entry_codec_round_trip() {
    for n in [0u64, 1, 1000] {
        let sigs: Vec<TSig> = (0..n).map(sig).collect();
        let ctr = TickCtr {
            cands: 11,
            signals: [5, 6],
            missing_days: 7,
            ap_wrong: 8,
            fg: [0; 4],
        };
        let (v, c) = roundtrip(&sigs, &ctr).expect("кодек");
        assert_eq!(v.len(), sigs.len());
        assert!(v.iter().zip(&sigs).all(|(a, b)| same(a, b)));
        assert_eq!(format!("{c:?}"), format!("{ctr:?}"));
    }
}

fn open(dir: &Path, cache: &Path, trades: &Path, names: &[&str]) -> SigCache {
    SigCache::open(
        &SigCacheOpts {
            dir: dir.to_path_buf(),
            min_free_gb: 0,
        },
        cache,
        trades,
        names,
    )
    .expect("кэш сигналов")
}

#[test]
fn keys_follow_inputs_and_entries_survive_only_intact() {
    let tmp = tempfile::tempdir().expect("tmp");
    let (cache, trades, dir) = (
        tmp.path().join("c"),
        tmp.path().join("t"),
        tmp.path().join("sc"),
    );
    std::fs::create_dir_all(&cache).expect("c");
    std::fs::create_dir_all(trades.join("AAA")).expect("t");
    std::fs::write(cache.join("AAA.b42"), b"x").expect("b42");
    let zip = trades
        .join("AAA")
        .join(format!("AAA-aggTrades-{}.zip", super::data::day_string(20)));
    std::fs::write(&zip, b"zip-1").expect("zip");
    let cfg = Params::base().sig;
    let key = |sc: &SigCache, side| sc.key(&cfg, side).expect("ключ");
    let sc = open(&dir, &cache, &trades, &["AAA", "BBB"]);
    let k = key(&sc, Side::Long);
    assert_eq!(
        k,
        key(&open(&dir, &cache, &trades, &["AAA", "BBB"]), Side::Long),
        "тот же запуск — тот же ключ"
    );
    assert_ne!(k, key(&sc, Side::Short));
    assert_ne!(
        k,
        key(&open(&dir, &cache, &trades, &["BBB", "AAA"]), Side::Long),
        "порядок монет"
    );
    assert_ne!(
        k,
        key(&open(&dir, &cache, &trades, &["AAA"]), Side::Long),
        "набор монет"
    );
    std::fs::write(&zip, b"zip-22").expect("zip");
    assert_ne!(
        k,
        key(&open(&dir, &cache, &trades, &["AAA", "BBB"]), Side::Long),
        "суточный zip изменился"
    );
    std::fs::write(&zip, b"zip-1").expect("zip");
    std::fs::write(cache.join("AAA.b42"), b"yy").expect("b42");
    assert_ne!(
        k,
        key(&open(&dir, &cache, &trades, &["AAA", "BBB"]), Side::Long),
        ".b42 изменился"
    );
    std::fs::write(
        trades
            .join("AAA")
            .join(format!("AAA-aggTrades-{}.zip", super::data::day_string(33))),
        b"new",
    )
    .expect("zip");
    let sc2 = open(&dir, &cache, &trades, &["AAA", "BBB"]);
    let k2 = key(&sc2, Side::Long);
    assert_ne!(k, k2, "появился новый суточный файл (был «нет файла»)");
    let mut ap = cfg.clone();
    ap.ap = 1;
    assert!(sc2.key(&ap, Side::Long).is_none(), "ap != 0 не кэшируется");

    assert!(sc2.get(&k2).is_none());
    let sigs: Vec<TSig> = (0..50).map(sig).collect();
    let ctr = TickCtr {
        cands: 3,
        signals: [1, 2],
        missing_days: 0,
        ap_wrong: 0,
        fg: [0; 4],
    };
    sc2.put(&k2, &sigs, &ctr);
    let (v, c) = sc2.get(&k2).expect("попадание");
    assert!(v.iter().zip(&sigs).all(|(a, b)| same(a, b)) && v.len() == 50);
    assert_eq!(c.cands, 3);
    let p = dir.join(format!("{k2}.sg"));
    let mut b = std::fs::read(&p).expect("файл");
    let n = b.len();
    b[n - 2] ^= 0x33;
    std::fs::write(&p, &b).expect("запись");
    assert!(sc2.get(&k2).is_none());
    assert_eq!(sc2.counters().bad, 1);
}
