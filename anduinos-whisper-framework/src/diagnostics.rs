//! Allowlisted metadata only. No logs, audio, paths, device names or transcripts.
use serde_json::{Map, Value, json};
use std::collections::{HashMap, VecDeque};
use std::time::Instant;

pub fn sanitize(record: &Value) -> Map<String, Value> {
    let mut result = Map::new();
    for key in [
        "audio_ms",
        "endpoint_ms",
        "queue_ms",
        "initialization_ms",
        "load_ms",
        "mel_ms",
        "encode_ms",
        "decode_ms",
        "batch_decode_ms",
        "sample_ms",
        "inference_ms",
        "delivery_ms",
        "peak_rss_mib",
        "state_initialization_ms",
        "state_release_ms",
        "prompt_decode_ms",
    ] {
        if let Some(value) = record[key]
            .as_f64()
            .filter(|n| n.is_finite() && (0.0..=86_400_000.0).contains(n))
        {
            result.insert(
                key.into(),
                json!((value * 1000.0).round_ties_even() / 1000.0),
            );
        }
    }
    for (key, allowed) in [
        (
            "endpoint_reason",
            &["silence", "max-duration", "finish"][..],
        ),
        ("phase", &["cold", "warm"]),
        ("kind", &["final", "partial", "benchmark"]),
        ("backend", &["cpu", "gpu", "unknown"]),
        ("model", &["tiny", "base", "small"]),
        ("status", &["success", "cancelled", "timeout", "error"]),
        ("engine", &["cli", "resident", "vad"]),
        ("fallback", &["gpu_failed"]),
    ] {
        if let Some(value) = record[key].as_str().filter(|s| allowed.contains(s)) {
            result.insert(key.into(), json!(value));
        }
    }
    if let Some(threads) = record["threads"].as_u64().filter(|n| (1..=256).contains(n)) {
        result.insert("threads".into(), json!(threads));
    }
    result
}

pub fn sanitize_report(raw: &str) -> Result<String, &'static str> {
    if raw.len() > 131072 {
        return Err("Diagnostic report too large");
    }
    let report: Value = serde_json::from_str(raw).map_err(|_| "Invalid diagnostic report")?;
    if report["schema_version"].as_u64() != Some(1) {
        return Err("Unsupported diagnostic report");
    }
    let records = report["measurements"]
        .as_array()
        .ok_or("Invalid diagnostic measurements")?;
    if records.len() > 100 || records.iter().any(|r| !r.is_object()) {
        return Err("Invalid diagnostic measurements");
    }
    serde_json::to_string_pretty(&json!({"schema_version":1,"measurements":records.iter().map(sanitize).collect::<Vec<_>>()})).map_err(|_|"Invalid diagnostic measurements")
}

pub struct PerformanceHistory {
    records: VecDeque<(u32, Map<String, Value>)>,
    delivery: HashMap<u32, Instant>,
    sequence: u32,
    limit: usize,
}
impl Default for PerformanceHistory {
    fn default() -> Self {
        Self::new(100)
    }
}
impl PerformanceHistory {
    pub fn new(limit: usize) -> Self {
        assert!((1..=100).contains(&limit));
        Self {
            records: VecDeque::new(),
            delivery: HashMap::new(),
            sequence: 0,
            limit,
        }
    }
    pub fn append(&mut self, record: &Value) -> u32 {
        self.sequence = self.sequence.wrapping_add(1).max(1);
        if self.records.len() == self.limit {
            let (ticket, _) = self.records.pop_front().unwrap();
            self.delivery.remove(&ticket);
        }
        self.records.push_back((self.sequence, sanitize(record)));
        self.sequence
    }
    pub fn ready_for_delivery(&mut self, ticket: u32, started: Instant) {
        if self.records.iter().any(|(key, _)| *key == ticket) {
            self.delivery.insert(ticket, started);
        }
    }
    pub fn acknowledge_delivery(&mut self, ticket: u32, now: Instant) -> bool {
        let Some(start) = self.delivery.remove(&ticket) else {
            return false;
        };
        if let Some((_, record)) = self.records.iter_mut().find(|(key, _)| *key == ticket) {
            if let Some(elapsed) = now.checked_duration_since(start) {
                record.extend(sanitize(
                    &json!({"delivery_ms": elapsed.as_secs_f64()*1000.0}),
                ));
            }
            true
        } else {
            false
        }
    }
    pub fn export_json(&self) -> String {
        serde_json::to_string_pretty(&json!({"schema_version":1,
            "measurements":self.records.iter().map(|(_,r)| r).collect::<Vec<_>>() }))
        .unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn export_revalidates_report_shape_size_and_private_fields() {
        let report=sanitize_report(r#"{"schema_version":1,"measurements":[{"text":"SECRET","inference_ms":12}],"path":"SECRET"}"#).unwrap();
        assert!(!report.contains("SECRET"));
        assert!(report.contains("inference_ms"));
        for raw in [
            "null",
            r#"{"schema_version":true,"measurements":[]}"#,
            r#"{"schema_version":1,"measurements":[null]}"#,
        ] {
            assert!(sanitize_report(raw).is_err());
        }
        assert!(sanitize_report(&" ".repeat(131073)).is_err());
        assert!(
            sanitize_report(
                &json!({"schema_version":1,"measurements":vec![json!({});101]}).to_string()
            )
            .is_err()
        );
    }
    #[test]
    fn privacy_allowlist_rejects_text_and_invalid_numbers() {
        let result = sanitize(
            &json!({"text":"secret", "path":"/home/private", "audio_ms":true,
            "threads":1.5, "backend":"GPU model name", "queue_ms":-1, "inference_ms":12.3456}),
        );
        assert_eq!(
            result,
            json!({"inference_ms":12.346}).as_object().unwrap().clone()
        );
    }
    #[test]
    fn bounded_history_expires_and_acknowledges_tickets_once() {
        let mut history = PerformanceHistory::new(1);
        let first = history.append(&json!({"status":"success","text":"secret"}));
        history.ready_for_delivery(first, Instant::now());
        let second = history.append(&json!({"kind":"final"}));
        assert!(!history.acknowledge_delivery(first, Instant::now()));
        history.ready_for_delivery(second, Instant::now());
        assert!(history.acknowledge_delivery(second, Instant::now()));
        assert!(!history.acknowledge_delivery(second, Instant::now()));
        assert!(!history.export_json().contains("secret"));
    }
}
