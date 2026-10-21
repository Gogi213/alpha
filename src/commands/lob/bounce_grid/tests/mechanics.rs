//! Мост каталог механик ⇄ разбор имён (В-227): для каждой записи и точки домена имя из
//! шаблона разбирается `ExitForm` и даёт то же имя; вариант совпадает с `id` строки.

use super::super::mechanics::{catalog_json, MECHANICS};
use super::*;

#[test]
fn catalog_names_round_trip_through_exit_form() {
    let mut n = 0;
    for m in MECHANICS.iter().filter(|m| m.kind == "exit") {
        for name in m.sample_names() {
            let f = ExitForm::parse_any(&name).unwrap_or_else(|e| panic!("{name} ({}): {e}", m.id));
            assert_eq!(f.label(), name, "круг имени {}", m.id);
            assert_eq!(f.variant_id(), m.id, "вариант для {name}");
            assert_eq!(f.is_r2(), m.is_r2, "r2 для {name}");
            n += 1;
        }
    }
    assert!(n > 200, "точек слишком мало: {n}");
}

#[test]
fn catalog_json_has_all_rows() {
    let v = catalog_json();
    assert_eq!(v["schema"], 1);
    assert_eq!(v["mechanics"].as_array().unwrap().len(), MECHANICS.len());
}

/// Снимок каталога для Python (`tools/registry/mechanics.json`) не отстаёт от Rust:
/// обновить — `lob mechanics --json > tools/registry/mechanics.json`.
#[test]
fn python_snapshot_matches_catalog() {
    let snap: serde_json::Value =
        serde_json::from_str(include_str!("../../../../../tools/registry/mechanics.json")).unwrap();
    assert_eq!(
        snap,
        catalog_json(),
        "tools/registry/mechanics.json устарел"
    );
}

/// Стоп, тейк и вход: имя из шаблона по точкам домена разбирается `StopForm`/`TakeForm`/`EntryForm` и даёт то же имя.
#[test]
fn catalog_names_round_trip_through_stop_and_take() {
    let (mut ns, mut nt, mut ne) = (0, 0, 0);
    for m in MECHANICS {
        for name in m.sample_names() {
            match m.kind {
                "stop" => {
                    let f = StopForm::parse(&name).unwrap_or_else(|e| panic!("{name}: {e}"));
                    assert_eq!(f.label(), name, "круг имени {}", m.id);
                    ns += 1;
                }
                "take" => {
                    let f = TakeForm::parse(&name).unwrap_or_else(|e| panic!("{name}: {e}"));
                    assert_eq!(f.label(), name, "круг имени {}", m.id);
                    nt += 1;
                }
                "entry" => {
                    let f = EntryForm::parse(&name).unwrap_or_else(|e| panic!("{name}: {e}"));
                    assert_eq!(f.label(), name, "круг имени {}", m.id);
                    ne += 1;
                }
                _ => {}
            }
        }
    }
    assert!(ns >= 10 && nt >= 10, "точек мало: стоп {ns}, тейк {nt}");
    assert!(ne >= 10, "точек мало: вход {ne}");
}

/// Флаги прогона: каждое значение домена принимает разбор флага, а имя записи совпадает с хвостом формы клетки.
#[test]
fn catalog_flags_round_trip_through_run_flags() {
    use crate::commands::lob::backtest::{early_exit_ns_from_ms, early_label, EntryTtl};
    let (mut nt, mut ne) = (0, 0);
    for m in MECHANICS.iter().filter(|m| m.kind == "flag") {
        for name in m.sample_names() {
            match m.id {
                "EntryTtl" => {
                    let secs = name.strip_prefix("ttl").unwrap();
                    assert!(
                        matches!(EntryTtl::parse(secs), Ok(EntryTtl::Secs(_))),
                        "{name}"
                    );
                    nt += 1;
                }
                "EarlyExit" => {
                    let x = name.strip_prefix("early").unwrap();
                    let ms = (x.parse::<f64>().unwrap() * 1_000.0).round() as i64;
                    assert!(early_exit_ns_from_ms(Some(ms)).is_ok(), "{name}");
                    assert_eq!(early_label(ms), x, "круг имени {name}");
                    ne += 1;
                }
                id => panic!("флаг {id} без проверки"),
            }
        }
    }
    assert_eq!((nt, ne), (33, 10));
}
