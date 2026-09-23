//! Accuracy-preserving, offline backend measurement on fixed public fixtures.
use crate::calibration::{digest, load, noisy, read_bounded};
use crate::diagnostics::sanitize;
use crate::resident::{EngineConfig, ResidentEngine, WORKER};
use crate::session_engine::RecognitionEngine;
use crate::transport::WorkerError;
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use unicode_normalization::UnicodeNormalization;

pub const POLICY_VERSION: u32 = 6;
pub const QUICK_BUDGET: Duration = Duration::from_secs(10);
pub const FULL_BUDGET: Duration = Duration::from_secs(60);

pub fn available_threads() -> u32 {
    // SAFETY: cpu_set_t is initialized and the supplied byte length matches it.
    unsafe {
        let mut set: libc::cpu_set_t = std::mem::zeroed();
        if libc::sched_getaffinity(0, std::mem::size_of_val(&set), &mut set) == 0 {
            return libc::CPU_COUNT(&set).max(1) as u32;
        }
    }
    std::thread::available_parallelism().map_or(1, |n| n.get() as u32)
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Choice {
    pub backend: String,
    pub threads: u32,
}
impl Choice {
    fn new(backend: &str, threads: u32) -> Self {
        Self {
            backend: backend.into(),
            threads,
        }
    }
    pub fn json(&self) -> Value {
        json!({"backend":self.backend,"threads":self.threads})
    }
    fn from_json(value: &Value) -> Option<Self> {
        let backend = value["backend"].as_str()?;
        let threads = value["threads"].as_u64()?;
        if !["cpu", "gpu"].contains(&backend)
            || threads < 1
            || threads > available_threads().min(256) as u64
        {
            return None;
        }
        Some(Self::new(backend, threads as u32))
    }
}
pub fn candidates(count: u32) -> Vec<Choice> {
    let count = count.max(1);
    let initial = count.min(4);
    let mut result = vec![Choice::new("cpu", initial), Choice::new("gpu", initial)];
    for n in [8, 2] {
        let c = Choice::new("cpu", count.min(n));
        if !result.contains(&c) {
            result.push(c);
        }
    }
    result
}
pub fn normalized(text: &str) -> String {
    // GLib is already required by the service; use its full Unicode case fold
    // rather than shipping a separate, obsolete Unicode table dependency.
    glib::casefold(text.nfkc().collect::<String>())
        .chars()
        .filter(|c| c.is_alphanumeric())
        .collect()
}

pub fn environment_fingerprint(model: &Path, samples: &str, worker: &Path) -> String {
    let mut files = BTreeSet::from([
        model.to_owned(),
        worker.to_owned(),
        worker
            .parent()
            .unwrap_or(Path::new("."))
            .join("anduinos-whisper/libanduinos-whisper.so.1"),
    ]);
    for pattern in [
        "/usr/lib/*/libwhisper.so*",
        "/usr/lib/*/libggml*.so*",
        "/usr/lib/*/ggml/backends0/*.so",
        "/usr/lib/*/libvulkan*.so*",
        "/usr/lib/*/libnvidia*.so*",
        "/usr/lib/*/libcuda.so*",
        "/usr/share/vulkan/icd.d/*.json",
    ] {
        files.extend(glob::glob(pattern).unwrap().flatten());
    }
    let identities: Vec<_> = files
        .iter()
        .map(|p| match p.metadata() {
            Ok(s) => json!([p, s.len(), s.mtime(), s.mtime_nsec()]),
            Err(_) => json!([p, null]),
        })
        .collect();
    let cpu: BTreeSet<_> = std::fs::read_to_string("/proc/cpuinfo")
        .unwrap_or_default()
        .lines()
        .filter_map(|line| {
            let (key, value) = line.split_once(':')?;
            [
                "model name",
                "vendor_id",
                "flags",
                "Features",
                "CPU part",
                "CPU implementer",
            ]
            .contains(&key.trim())
            .then(|| (key.trim().to_owned(), value.trim().to_owned()))
        })
        .collect();
    let affinity = std::fs::read_to_string("/proc/self/status")
        .unwrap_or_default()
        .lines()
        .find(|l| l.starts_with("Cpus_allowed_list:"))
        .unwrap_or("")
        .to_owned();
    let driver: Vec<_> = [
        "/proc/driver/nvidia/version",
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
    ]
    .iter()
    .filter_map(|p| read_bounded(Path::new(p), 8192).ok())
    .collect();
    let environment: Vec<_> = [
        "GGML_VK_VISIBLE_DEVICES",
        "CUDA_VISIBLE_DEVICES",
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
    ]
    .iter()
    .map(|k| (*k, std::env::var(k).unwrap_or_default()))
    .collect();
    // Deliberately version the Rust fingerprint encoding. Existing Python caches
    // are harmless misses, not mistaken for measurements made by this service.
    digest(
        json!([
            "rust-fingerprint-v1",
            POLICY_VERSION,
            std::env::consts::ARCH,
            std::fs::read_to_string("/proc/sys/kernel/osrelease").unwrap_or_default(),
            affinity,
            identities,
            cpu,
            driver,
            environment,
            samples
        ])
        .to_string()
        .as_bytes(),
    )
}

pub struct SelectionCache {
    pub path: PathBuf,
    pub measurements: Vec<Value>,
}
impl Default for SelectionCache {
    fn default() -> Self {
        Self {
            path: glib::user_cache_dir().join("anduinos-whisper/performance.json"),
            measurements: Vec::new(),
        }
    }
}
impl SelectionCache {
    pub fn load(&mut self, fingerprint: &str) -> Option<Choice> {
        self.measurements.clear();
        let record: Value = serde_json::from_slice(&read_bounded(&self.path, 65536).ok()?).ok()?;
        if record["policy"] != POLICY_VERSION || record["fingerprint"] != fingerprint {
            return None;
        }
        let choice = Choice::from_json(&record["selected"])?;
        if let Some(records) = record["measurements"].as_array() {
            self.measurements = records
                .iter()
                .filter(|v| v.is_object())
                .map(|v| Value::Object(sanitize(v)))
                .collect();
        }
        Some(choice)
    }
    pub fn save(
        &self,
        fingerprint: &str,
        selected: &Choice,
        measurements: &[Value],
    ) -> std::io::Result<()> {
        if Choice::from_json(&selected.json()).is_none() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "Invalid backend selection",
            ));
        }
        let parent = self.path.parent().unwrap_or(Path::new("."));
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(parent)?;
        let mut file = tempfile::Builder::new()
            .prefix(".performance-")
            .tempfile_in(parent)?;
        let measurements: Vec<_> = measurements.iter().map(sanitize).collect();
        serde_json::to_writer(
            &mut file,
            &json!({"policy":POLICY_VERSION,"fingerprint":fingerprint,"selected":selected.json(),"measurements":measurements}),
        )?;
        file.flush()?;
        file.as_file().sync_all()?;
        file.persist(&self.path).map_err(|e| e.error)?;
        Ok(())
    }
}

pub trait BenchmarkEngine: RecognitionEngine {
    fn set_timeout(&mut self, timeout: Duration);
}
impl BenchmarkEngine for ResidentEngine {
    fn set_timeout(&mut self, timeout: Duration) {
        self.timeout = timeout;
    }
}
pub struct BackendTuner<E: BenchmarkEngine = ResidentEngine> {
    factory: Box<dyn FnMut(EngineConfig) -> Result<E, WorkerError>>,
    clock: Box<dyn Fn() -> Duration>,
    pub measurements: Vec<Value>,
}
impl Default for BackendTuner {
    fn default() -> Self {
        let start = Instant::now();
        Self::new(ResidentEngine::new, move || start.elapsed())
    }
}
fn median(values: &[f64]) -> f64 {
    let mut values = values.to_vec();
    values.sort_by(f64::total_cmp);
    let n = values.len();
    if n % 2 == 0 {
        (values[n / 2 - 1] + values[n / 2]) / 2.0
    } else {
        values[n / 2]
    }
}
impl<E: BenchmarkEngine> BackendTuner<E> {
    pub fn new(
        factory: impl FnMut(EngineConfig) -> Result<E, WorkerError> + 'static,
        clock: impl Fn() -> Duration + 'static,
    ) -> Self {
        Self {
            factory: Box::new(factory),
            clock: Box::new(clock),
            measurements: Vec::new(),
        }
    }
    pub fn run(
        &mut self,
        config: &EngineConfig,
        samples: &[Vec<u8>],
        cancel: &AtomicBool,
        threads: u32,
        cpu_count: u32,
        budget: Duration,
    ) -> Result<Choice, WorkerError> {
        self.measurements.clear();
        if budget.is_zero() || budget > FULL_BUDGET || samples.is_empty() || threads > 256 {
            return Err(WorkerError::Protocol(
                "Invalid performance measurement configuration",
            ));
        }
        let deadline = (self.clock)() + budget;
        let mut conditions = Vec::new();
        for sample in samples {
            conditions.push(sample.clone());
            conditions.push(noisy(sample)?);
        }
        let mut sequence = vec![samples[0].as_slice()];
        for condition in &conditions {
            sequence.extend([condition.as_slice(), condition.as_slice()]);
        }
        let probes = if threads > 0 {
            vec![Choice::new("cpu", threads), Choice::new("gpu", threads)]
        } else {
            candidates(cpu_count)
        };
        let mut baseline: Option<Vec<String>> = None;
        let mut scores: Vec<(f64, Choice)> = Vec::new();
        let mut fastest_clean: Option<f64> = None;
        for choice in probes {
            if (self.clock)() >= deadline {
                break;
            }
            if cancel.load(Ordering::Acquire) {
                return Err(WorkerError::Cancelled);
            }
            let mut probe = config.clone();
            probe.backend = choice.backend.clone();
            probe.threads = choice.threads;
            probe.beam = 5;
            let result = (|| -> Result<(), WorkerError> {
                let mut engine = (self.factory)(probe)?;
                let mut outputs = Vec::new();
                let mut warm = Vec::new();
                for (i, audio) in sequence.iter().enumerate() {
                    let now = (self.clock)();
                    if now >= deadline {
                        return Err(WorkerError::Timeout);
                    }
                    if cancel.load(Ordering::Acquire) {
                        return Err(WorkerError::Cancelled);
                    }
                    engine.set_timeout(
                        (deadline - now)
                            .min(Duration::from_secs(30))
                            .max(Duration::from_millis(1)),
                    );
                    let started = (self.clock)();
                    let text = engine.transcribe(audio, cancel)?;
                    let elapsed = (self.clock)().saturating_sub(started).as_secs_f64() * 1000.0;
                    outputs.push(normalized(&text));
                    let mut metric = engine.metrics().clone();
                    metric.extend(json!({"kind":"benchmark","status":"success","phase":if i==0 {"cold"} else {"warm"},"audio_ms":audio.len() as f64 /32.0,"inference_ms":elapsed}).as_object().unwrap().clone());
                    self.measurements.push(Value::Object(metric));
                    if i > 0 {
                        warm.push(elapsed);
                    }
                    if engine.metrics().get("backend").and_then(Value::as_str)
                        != Some(&choice.backend)
                        || outputs[i].is_empty()
                    {
                        return Ok(());
                    }
                    if i > 0
                        && baseline
                            .as_ref()
                            .is_some_and(|b| outputs[i] != b[(i - 1) / 2])
                    {
                        return Ok(());
                    }
                    if i == 1 && outputs[0] != outputs[1] {
                        return Ok(());
                    }
                    if i >= 2 && i % 2 == 0 && outputs[i] != outputs[i - 1] {
                        return Ok(());
                    }
                    if i == 2 && fastest_clean.is_some_and(|fast| median(&warm) > fast * 1.5) {
                        return Ok(());
                    }
                }
                let signature: Vec<_> = outputs.into_iter().skip(1).step_by(2).collect();
                if baseline.is_none() {
                    if choice.backend != "cpu" {
                        return Ok(());
                    }
                    baseline = Some(signature.clone());
                }
                if baseline.as_ref() != Some(&signature) {
                    return Ok(());
                }
                let score = median(&warm);
                if score.is_finite() && score > 0.0 {
                    scores.push((score, choice.clone()));
                    let clean = median(&warm[..2]);
                    fastest_clean = Some(fastest_clean.map_or(clean, |f| f.min(clean)));
                }
                Ok(())
            })();
            match result {
                Err(WorkerError::Cancelled) => return Err(WorkerError::Cancelled),
                Err(_) => self.measurements.push(json!({"kind":"benchmark","status":"error","backend":choice.backend,"threads":choice.threads})),
                Ok(()) => {}
            }
        }
        scores.sort_by(|a, b| {
            a.0.total_cmp(&b.0)
                .then(a.1.backend.cmp(&b.1.backend))
                .then(a.1.threads.cmp(&b.1.threads))
        });
        let best = scores.first().ok_or(WorkerError::Protocol(
            "No reliable performance benchmark result; CPU fallback remains available",
        ))?;
        if let Some(cpu) = scores.iter().find(|(_, c)| c.backend == "cpu") {
            if best.1.backend == "gpu" && best.0 > cpu.0 * 0.9 {
                return Ok(cpu.1.clone());
            }
        }
        Ok(best.1.clone())
    }
}

pub struct AutomaticSelector<E: BenchmarkEngine = ResidentEngine> {
    pub directory: PathBuf,
    pub worker: PathBuf,
    pub cache: SelectionCache,
    pub tuner: BackendTuner<E>,
    failed_fingerprint: Option<String>,
    pub status: &'static str,
    pub measurements: Vec<Value>,
    pub preview_allowed: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Map;
    use std::cell::Cell;
    use std::os::unix::fs::PermissionsExt;
    use std::rc::Rc;

    #[derive(Clone)]
    struct Behavior {
        gpu_warm: f64,
        gpu_actual: &'static str,
        gpu_text: &'static str,
        noisy_regression: bool,
        error: bool,
    }
    impl Default for Behavior {
        fn default() -> Self {
            Self {
                gpu_warm: 0.2,
                gpu_actual: "gpu",
                gpu_text: "same",
                noisy_regression: false,
                error: false,
            }
        }
    }
    struct Fake {
        gpu: bool,
        calls: usize,
        clock: Rc<Cell<Duration>>,
        behavior: Behavior,
        metrics: Map<String, Value>,
        timeout: Duration,
    }
    impl RecognitionEngine for Fake {
        fn transcribe(&mut self, _: &[u8], cancel: &AtomicBool) -> Result<String, WorkerError> {
            if cancel.load(Ordering::Acquire) {
                return Err(WorkerError::Cancelled);
            }
            if self.behavior.error {
                return Err(WorkerError::Protocol("failed"));
            }
            let elapsed = Duration::from_secs_f64(if self.gpu {
                if self.calls == 0 {
                    8.0
                } else {
                    self.behavior.gpu_warm
                }
            } else {
                1.0
            });
            self.clock.set(self.clock.get() + elapsed.min(self.timeout));
            if elapsed > self.timeout {
                return Err(WorkerError::Timeout);
            }
            self.calls += 1;
            Ok(if self.gpu {
                if self.behavior.noisy_regression && self.calls >= 4 {
                    "wrong"
                } else {
                    self.behavior.gpu_text
                }
            } else {
                "same"
            }
            .into())
        }
        fn is_running(&mut self) -> bool {
            true
        }
        fn metrics(&self) -> &Map<String, Value> {
            &self.metrics
        }
    }
    impl BenchmarkEngine for Fake {
        fn set_timeout(&mut self, timeout: Duration) {
            self.timeout = timeout;
        }
    }
    fn tuner(behavior: Behavior) -> (BackendTuner<Fake>, Rc<Cell<usize>>) {
        let clock = Rc::new(Cell::new(Duration::ZERO));
        let clock_fn = clock.clone();
        let calls = Rc::new(Cell::new(0));
        let seen = calls.clone();
        (
            BackendTuner::new(
                move |config| {
                    seen.set(seen.get() + 1);
                    assert_eq!(config.beam, 5);
                    assert_eq!(config.model, Path::new("/same-model"));
                    let gpu = config.backend == "gpu";
                    Ok(Fake {gpu,calls:0,clock:clock.clone(),behavior:behavior.clone(),timeout:FULL_BUDGET,
                metrics:json!({"backend":if gpu {behavior.gpu_actual} else {"cpu"},"threads":config.threads}).as_object().unwrap().clone()})
                },
                move || clock_fn.get(),
            ),
            calls,
        )
    }
    fn config() -> EngineConfig {
        EngineConfig::new("/same-model".into(), "en".into(), 0, "auto".into())
    }
    fn run(tuner: &mut BackendTuner<Fake>, budget: Duration) -> Result<Choice, WorkerError> {
        tuner.run(
            &config(),
            &[vec![1; 16000]],
            &AtomicBool::new(false),
            0,
            2,
            budget,
        )
    }
    #[test]
    fn candidate_order_normalization_and_budget_validation() {
        assert_eq!(
            candidates(1),
            vec![Choice::new("cpu", 1), Choice::new("gpu", 1)]
        );
        assert_eq!(
            candidates(3),
            vec![
                Choice::new("cpu", 3),
                Choice::new("gpu", 3),
                Choice::new("cpu", 2)
            ]
        );
        assert_eq!(candidates(128).len(), 4);
        assert_eq!(
            normalized("ＳＴＲＡＳＳＥ Straße，语音！"),
            "strassestrasse语音"
        );
        for budget in [Duration::ZERO, Duration::from_secs(61)] {
            assert!(run(&mut tuner(Behavior::default()).0, budget).is_err());
        }
    }
    #[test]
    fn fast_warm_gpu_wins_despite_cold_start_and_small_gains_prefer_cpu() {
        let (mut t, _) = tuner(Behavior::default());
        assert_eq!(run(&mut t, FULL_BUDGET).unwrap(), Choice::new("gpu", 2));
        assert_eq!(
            t.measurements
                .iter()
                .filter(|m| m["phase"] == "cold")
                .count(),
            2
        );
        let (mut t, _) = tuner(Behavior {
            gpu_warm: 0.95,
            ..Behavior::default()
        });
        assert_eq!(run(&mut t, FULL_BUDGET).unwrap().backend, "cpu");
    }
    #[test]
    fn cpu_disguised_as_gpu_and_quality_regressions_are_rejected() {
        for behavior in [
            Behavior {
                gpu_actual: "cpu",
                ..Behavior::default()
            },
            Behavior {
                gpu_text: "missing words",
                ..Behavior::default()
            },
            Behavior {
                noisy_regression: true,
                ..Behavior::default()
            },
        ] {
            let (mut t, _) = tuner(behavior);
            assert_eq!(run(&mut t, FULL_BUDGET).unwrap().backend, "cpu");
        }
    }
    #[test]
    fn quick_budget_retains_valid_cpu_and_cancel_never_falls_back() {
        let (mut t, _) = tuner(Behavior::default());
        assert_eq!(run(&mut t, QUICK_BUDGET).unwrap().backend, "cpu");
        assert!(t.measurements.iter().any(|m| m["status"] == "error"));
        assert_eq!(
            t.run(
                &config(),
                &[vec![1; 16000]],
                &AtomicBool::new(true),
                0,
                2,
                FULL_BUDGET
            ),
            Err(WorkerError::Cancelled)
        );
    }
    #[test]
    fn cache_is_private_bounded_and_sanitized() {
        let directory = tempfile::tempdir().unwrap();
        let mut cache = SelectionCache {
            path: directory.path().join("private/performance.json"),
            measurements: vec![],
        };
        cache
            .save(
                "fingerprint",
                &Choice::new("cpu", 1),
                &[json!({"kind":"benchmark","text":"SECRET"})],
            )
            .unwrap();
        assert_eq!(
            cache.path.metadata().unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            cache
                .path
                .parent()
                .unwrap()
                .metadata()
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert_eq!(cache.load("fingerprint"), Some(Choice::new("cpu", 1)));
        assert!(
            !std::fs::read_to_string(&cache.path)
                .unwrap()
                .contains("SECRET")
        );
        assert!(cache.load("new driver").is_none());
        std::fs::write(&cache.path, vec![b'x'; 65537]).unwrap();
        assert!(cache.load("fingerprint").is_none());
    }
    #[test]
    fn model_and_private_library_changes_invalidate_fingerprint() {
        let directory = tempfile::tempdir().unwrap();
        let model = directory.path().join("model");
        let worker = directory.path().join("worker");
        std::fs::write(&model, b"old").unwrap();
        std::fs::write(&worker, b"worker").unwrap();
        let first = environment_fingerprint(&model, "sample", &worker);
        std::fs::write(&model, b"replacement").unwrap();
        let second = environment_fingerprint(&model, "sample", &worker);
        assert_ne!(first, second);
        std::fs::create_dir(directory.path().join("anduinos-whisper")).unwrap();
        std::fs::write(
            directory
                .path()
                .join("anduinos-whisper/libanduinos-whisper.so.1"),
            b"library",
        )
        .unwrap();
        assert_ne!(second, environment_fingerprint(&model, "sample", &worker));
    }
    #[test]
    fn selector_cache_generation_manual_path_and_failure_memory() {
        let directory = tempfile::tempdir().unwrap();
        let (t, calls) = tuner(Behavior::default());
        let mut selector = AutomaticSelector::new(
            Path::new("data/benchmark"),
            SelectionCache {
                path: directory.path().join("performance.json"),
                measurements: vec![],
            },
            t,
        );
        let cancel = AtomicBool::new(false);
        selector
            .select(&config(), &cancel, false, 0, || {}, FULL_BUDGET)
            .unwrap();
        assert_eq!(selector.status, "measured");
        let measured_calls = calls.get();
        selector
            .select(
                &config(),
                &cancel,
                false,
                0,
                || panic!("cached selection must not announce measurement"),
                FULL_BUDGET,
            )
            .unwrap();
        assert_eq!(calls.get(), measured_calls);
        assert!(selector.measurements.is_empty());
        selector
            .select(&config(), &cancel, false, 1, || {}, FULL_BUDGET)
            .unwrap();
        assert!(calls.get() > measured_calls);
        let mut manual = config();
        manual.backend = "cpu".into();
        selector
            .select(
                &manual,
                &cancel,
                false,
                1,
                || panic!("manual selection must not benchmark"),
                QUICK_BUDGET,
            )
            .unwrap();
        assert_eq!(selector.status, "manual");
        assert!(selector.measurements.is_empty());
        assert!(!selector.preview_allowed);
        assert_eq!(
            selector.select(
                &manual,
                &AtomicBool::new(true),
                false,
                0,
                || {},
                QUICK_BUDGET
            ),
            Err(WorkerError::Cancelled)
        );
        let (t, calls) = tuner(Behavior {
            error: true,
            ..Behavior::default()
        });
        selector.tuner = t;
        selector
            .select(&config(), &cancel, true, 2, || {}, FULL_BUDGET)
            .unwrap();
        let failed_calls = calls.get();
        assert_eq!(selector.status, "fallback");
        selector
            .select(
                &config(),
                &cancel,
                false,
                2,
                || panic!("must not repeat failed probe"),
                FULL_BUDGET,
            )
            .unwrap();
        assert_eq!(calls.get(), failed_calls);
        selector
            .select(&config(), &cancel, false, 3, || {}, FULL_BUDGET)
            .unwrap();
        assert!(calls.get() > failed_calls);
    }
}
impl Default for AutomaticSelector {
    fn default() -> Self {
        Self::new(
            Path::new("/usr/share/anduinos-whisper-framework/benchmark"),
            SelectionCache::default(),
            BackendTuner::default(),
        )
    }
}
impl<E: BenchmarkEngine> AutomaticSelector<E> {
    pub fn new(directory: &Path, cache: SelectionCache, tuner: BackendTuner<E>) -> Self {
        Self {
            directory: directory.to_owned(),
            worker: WORKER.into(),
            cache,
            tuner,
            failed_fingerprint: None,
            status: "idle",
            measurements: Vec::new(),
            preview_allowed: false,
        }
    }
    pub fn select(
        &mut self,
        config: &EngineConfig,
        cancel: &AtomicBool,
        force: bool,
        generation: u32,
        on_measure: impl FnOnce(),
        budget: Duration,
    ) -> Result<Choice, WorkerError> {
        self.measurements.clear();
        self.preview_allowed = false;
        if !["auto", "cpu", "gpu"].contains(&config.backend.as_str())
            || config.threads > 256
            || budget.is_zero()
            || budget > FULL_BUDGET
        {
            return Err(WorkerError::Protocol(
                "Invalid backend override or measurement budget",
            ));
        }
        if cancel.load(Ordering::Acquire) {
            return Err(WorkerError::Cancelled);
        }
        let threads = (if config.threads == 0 {
            4
        } else {
            config.threads
        })
        .min(available_threads());
        let fallback = Choice::new("cpu", threads);
        if config.backend != "auto" {
            self.status = "manual";
            return Ok(Choice::new(&config.backend, threads));
        }
        let result = (|| -> Result<Choice, WorkerError> {
            let (pcm, sample) = load(&self.directory, &config.language)?;
            let mut samples = vec![pcm];
            let mut digests = vec![sample];
            if config.language == "auto" {
                let (pcm, sample) = load(&self.directory, "zh-Hans")?;
                samples.push(pcm);
                digests.push(sample);
            }
            let measured_threads = config.threads.min(available_threads());
            let fingerprint = digest(
                format!(
                    "{}:{generation}:{}:{measured_threads}",
                    environment_fingerprint(&config.model, &digests.join(":"), &self.worker),
                    config.language
                )
                .as_bytes(),
            );
            if !force {
                if let Some(choice) = self.cache.load(&fingerprint) {
                    self.status = "cached";
                    self.preview_allowed = crate::live_policy::preview_capable(
                        &json!(self.cache.measurements),
                        &choice.json(),
                    );
                    return Ok(choice);
                }
                if self.failed_fingerprint.as_ref() == Some(&fingerprint) {
                    self.status = "fallback";
                    return Ok(fallback.clone());
                }
            }
            self.status = "measuring";
            on_measure();
            let measured = self.tuner.run(
                config,
                &samples,
                cancel,
                measured_threads,
                available_threads(),
                budget,
            );
            self.measurements = self
                .tuner
                .measurements
                .iter()
                .skip(self.tuner.measurements.len().saturating_sub(100))
                .map(|r| Value::Object(sanitize(r)))
                .collect();
            let selected = match measured {
                Ok(selected) => selected,
                Err(WorkerError::Cancelled) => return Err(WorkerError::Cancelled),
                Err(e) => {
                    self.failed_fingerprint = Some(fingerprint);
                    return Err(e);
                }
            };
            let _ = self.cache.save(&fingerprint, &selected, &self.measurements);
            self.status = "measured";
            self.failed_fingerprint = None;
            self.preview_allowed =
                crate::live_policy::preview_capable(&json!(self.measurements), &selected.json());
            Ok(selected)
        })();
        match result {
            Err(WorkerError::Cancelled) => Err(WorkerError::Cancelled),
            Err(_) => {
                self.status = "fallback";
                Ok(fallback)
            }
            Ok(selected) => Ok(selected),
        }
    }
}
