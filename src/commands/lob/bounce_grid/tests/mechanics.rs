//! Мост каталог механик ⇄ разбор имён (В-227): для каждой записи и точки домена имя из
//! шаблона разбирается `ExitForm` и даёт то же имя; вариант совпадает с `id` строки.

use super::super::mechanics::{catalog_json, MECHANICS};
use super::*;

#[test]
fn catalog_names_round_trip_through_exit_form() {
    let mut n = 0;
    for m in MECHANICS {
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
