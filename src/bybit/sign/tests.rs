use super::*;

/// Тест-вектор из RFC 4231, Test Case 1 — независимый от Bybit источник
/// для самого примитива: key = 0x0b × 20, data = "Hi There". Сверен
/// независимо через Python `hmac.new(key, data, hashlib.sha256).hexdigest()`
/// перед тем, как попасть сюда.
#[test]
fn hmac_sha256_matches_rfc4231_test_case_1() {
    let key = [0x0b_u8; 20];
    let mut mac = HmacSha256::new_from_slice(&key).unwrap();
    mac.update(b"Hi There");
    let got = hex_lower(&mac.finalize().into_bytes());
    assert_eq!(
        got,
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
    );
}

/// Bybit не публикует числовой пример с секретом (см. doc модуля), но
/// формулу и плейсхолдер `"XXXXXXXXXX"` для `api_key`/`secret_key`
/// публикуют и документация, и демо `Encryption_HMAC.py` биржи — оба
/// сходятся на нём. Ожидаемый дайджест ниже — не из документации: он
/// посчитан нами по той же формуле независимо через Python
/// `hmac`/`hashlib` и сверен через `openssl dgst -sha256 -hmac`, то есть
/// двумя реализациями, ни одна из которых не этот крейт — иначе тест
/// проверял бы крейт самим собой.
#[test]
fn signature_matches_bybit_v5_formula_known_answer() {
    let creds = Credentials::for_test("XXXXXXXXXX", "XXXXXXXXXX");
    let body = r#"{"category":"option"}"#;
    let sig = creds.sign(1_658_385_579_423, 5000, body).unwrap();
    assert_eq!(
        sig,
        "ff137e622dc52629f41b52ea614eab59502e2b0663f153c528cb570379c469b7"
    );
}

/// Подпись обязана зависеть от каждого слагаемого формулы: тест на
/// одну константу проходил бы и в случае регрессии, где, скажем,
/// `body` потерялся бы из конкатенации.
#[test]
fn signature_changes_when_any_ingredient_changes() {
    let creds = Credentials::for_test("k", "s");
    let base = creds.sign(1, 5000, "{}").unwrap();
    assert_ne!(base, creds.sign(1, 5000, r#"{"x":1}"#).unwrap(), "тело");
    assert_ne!(base, creds.sign(1, 6000, "{}").unwrap(), "recv_window");
    assert_ne!(base, creds.sign(2, 5000, "{}").unwrap(), "timestamp");
    assert_ne!(
        base,
        Credentials::for_test("k2", "s")
            .sign(1, 5000, "{}")
            .unwrap(),
        "api_key"
    );
    assert_ne!(
        base,
        Credentials::for_test("k", "s2")
            .sign(1, 5000, "{}")
            .unwrap(),
        "секрет"
    );
}

#[test]
fn secret_never_appears_in_debug_output() {
    let secret = "s3cr3t-do-not-leak-9f8a7";
    let creds = Credentials::for_test("visible-key", secret);
    let printed = format!("{creds:?}");
    assert!(!printed.contains(secret));
    assert!(!printed.contains("visible-key"));
    assert!(printed.contains("<redacted>"));
}

#[test]
fn from_env_rejects_a_missing_key() {
    with_cleared_env(|| {
        std::env::set_var(API_SECRET_VAR, "secret");
        assert_eq!(
            Credentials::from_env().unwrap_err(),
            CredentialsError::Missing(API_KEY_VAR)
        );
    });
}

#[test]
fn from_env_rejects_a_missing_secret() {
    with_cleared_env(|| {
        std::env::set_var(API_KEY_VAR, "key");
        assert_eq!(
            Credentials::from_env().unwrap_err(),
            CredentialsError::Missing(API_SECRET_VAR)
        );
    });
}

/// Пустая строка — частый исход `KEY=` в `.env`/systemd-юните без
/// значения. Без этой проверки такой ключ проходит `from_env` и падает
/// уже на бирже с гораздо менее понятной ошибкой.
#[test]
fn from_env_rejects_an_empty_value_the_same_as_missing() {
    with_cleared_env(|| {
        std::env::set_var(API_KEY_VAR, "");
        std::env::set_var(API_SECRET_VAR, "secret");
        assert_eq!(
            Credentials::from_env().unwrap_err(),
            CredentialsError::Missing(API_KEY_VAR)
        );
    });
}

#[test]
fn from_env_succeeds_when_both_are_set() {
    with_cleared_env(|| {
        std::env::set_var(API_KEY_VAR, "key");
        std::env::set_var(API_SECRET_VAR, "secret");
        let creds = Credentials::from_env().unwrap();
        assert_eq!(creds.api_key(), "key");
    });
}

/// `sign_into` не имеет права разойтись с `sign` — доказывается сверкой
/// на нескольких входах, включая отрицательный `timestamp_ms` (тип
/// допускает, `write_i64` обязан отдать знак) и `recv_window_ms = 0`
/// (короткий путь в `write_u32`), а не только "круглые" значения из
/// `signature_matches_bybit_v5_formula_known_answer`.
#[test]
fn sign_into_matches_sign_for_the_same_inputs() {
    let cases: &[(i64, u32, &str, &str, &str)] = &[
        (
            1_658_385_579_423,
            5000,
            r#"{"category":"option"}"#,
            "XXXXXXXXXX",
            "XXXXXXXXXX",
        ),
        (1, 5000, "{}", "k", "s"),
        (0, 0, "", "k2", "s2"),
        (-42, 100, r#"{"x":1}"#, "key", "secret"),
    ];
    for (ts, rw, body, key, secret) in cases {
        let creds = Credentials::for_test(key, secret);
        let want = creds.sign(*ts, *rw, body).unwrap();
        let mut got = [0u8; 64];
        creds.sign_into(*ts, *rw, body, &mut got).unwrap();
        assert_eq!(
            std::str::from_utf8(&got).unwrap(),
            want,
            "sign_into разошёлся с sign на {ts}/{rw}/{body:?}"
        );
    }
}

/// Гейт GC / запрет 1 горячего пути: `Credentials::sign_into` — боевая
/// реализация `OrderSigner::sign_into` (`bybit/trade_ws.rs`), и до этого
/// таска аллокация была доказана нулевой только для тестового фейка
/// (`interfaces.md`, «Из ремонта таска 15», «реальный `Credentials::
/// sign_into` делегирует старому `sign()`… буферный HMAC в `sign.rs` —
/// таск 14»). Ключи — тестовые значения переменных окружения, выставленные
/// внутри теста под `ENV_LOCK` (`with_cleared_env`), а не то, что стоит в
/// окружении настоящего прогона: тест не вправе зависеть ни от реальных
/// ключей трейдера, ни от их отсутствия.
#[test]
fn credentials_sign_into_allocates_nothing_after_warmup() {
    with_cleared_env(|| {
        std::env::set_var(API_KEY_VAR, "test-key-for-alloc-count");
        std::env::set_var(API_SECRET_VAR, "test-secret-for-alloc-count");
        let creds = Credentials::from_env().unwrap();
        let mut out = [0u8; 64];

        // Прогрев — первый вызов годится на любые ленивые инициализации.
        creds
            .sign_into(
                1_700_000_000_000,
                5000,
                r#"{"category":"linear"}"#,
                &mut out,
            )
            .unwrap();

        const MEASURED: i64 = 1_000_000;
        let mut total_allocations = 0u64;
        for i in 0..MEASURED {
            let (_, counts) = crate::alloc_count::measure(|| {
                creds
                    .sign_into(
                        1_700_000_000_000 + i,
                        5000,
                        r#"{"category":"linear"}"#,
                        &mut out,
                    )
                    .unwrap()
            });
            total_allocations += counts.allocations;
        }
        assert_eq!(
            total_allocations, 0,
            "sign_into реального Credentials аллоцировал после прогрева"
        );
    });
}
