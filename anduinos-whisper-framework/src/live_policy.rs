//! Conservative preview admission; reading legacy settings must never rewrite them.
use serde_json::Value;

pub fn live_mode(explicit: Option<&str>, legacy: Option<bool>, default: &str) -> String {
    explicit
        .unwrap_or_else(|| match legacy {
            Some(true) => "on",
            Some(false) => "off",
            None => default,
        })
        .to_owned()
}

pub fn permits_preview(mode: &str, capable: bool) -> bool {
    mode == "on" || (mode == "auto" && capable)
}

pub fn preview_capable(measurements: &Value, selected: &Value) -> bool {
    let Some(records) = measurements.as_array() else {
        return false;
    };
    let mut count = 0;
    for record in records {
        if !record.is_object()
            || record["phase"] != "warm"
            || record["status"] != "success"
            || record["backend"] != selected["backend"]
            || record["threads"] != selected["threads"]
        {
            continue;
        }
        let (Some(audio), Some(inference)) =
            (record["audio_ms"].as_f64(), record["inference_ms"].as_f64())
        else {
            return false;
        };
        if !audio.is_finite()
            || !inference.is_finite()
            || audio <= 0.0
            || inference <= 0.0
            || inference > 300.0
            || inference / audio > 0.025
        {
            return false;
        }
        count += 1;
    }
    count >= 2
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn explicit_and_legacy_choices_take_precedence() {
        assert_eq!(live_mode(Some("auto"), Some(false), "off"), "auto");
        assert_eq!(live_mode(None, Some(false), "auto"), "off");
        assert_eq!(live_mode(None, Some(true), "auto"), "on");
        assert_eq!(live_mode(None, None, "auto"), "auto");
        assert!(permits_preview("on", false));
        assert!(!permits_preview("off", true));
        assert!(!permits_preview("auto", false));
    }

    #[test]
    fn requires_two_fast_warm_results_from_selected_configuration() {
        let choice = json!({"backend":"gpu", "threads":2});
        let fast = json!({"backend":"gpu", "threads":2, "phase":"warm",
            "status":"success", "audio_ms":4000, "inference_ms":50});
        assert!(preview_capable(&json!([fast, fast]), &choice));
        assert!(!preview_capable(&json!([fast]), &choice));
        for (key, value) in [
            ("phase", json!("cold")),
            ("backend", json!("cpu")),
            ("threads", json!(4)),
            ("status", json!("error")),
            ("inference_ms", json!(500)),
            ("audio_ms", json!(100)),
            ("audio_ms", json!(0)),
            ("inference_ms", Value::Null),
            ("inference_ms", json!(true)),
        ] {
            let mut invalid = fast.clone();
            invalid[key] = value;
            assert!(!preview_capable(&json!([fast, invalid]), &choice), "{key}");
        }
    }
}
