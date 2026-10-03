//! Explicit native gate: cargo test --test native_runtime -- --ignored --nocapture
//! Requires the installed native worker, OpenCC and downloaded build models.
use anduinos_whisper_framework::commands::ChineseConverter;
use anduinos_whisper_framework::resident::{EngineConfig, ResidentEngine, VadEngine};
use anduinos_whisper_framework::tuning::{
    AutomaticSelector, BackendTuner, QUICK_BUDGET, SelectionCache,
};
use std::path::Path;
use std::sync::atomic::AtomicBool;

#[path = "support/artifacts.rs"]
mod artifacts;
use artifacts::{model, vad_model, worker};

fn engine(
    config: EngineConfig,
) -> Result<ResidentEngine, anduinos_whisper_framework::transport::WorkerError> {
    ResidentEngine::with_executable(config, &worker())
}

#[test]
#[ignore = "requires installed worker/model and exercises available CPU/GPU backends"]
fn native_quick_selection_is_bounded_and_does_not_capture_audio() {
    let directory = tempfile::tempdir().unwrap();
    let clock = std::time::Instant::now();
    let mut selector = AutomaticSelector::new(
        Path::new("data/benchmark"),
        SelectionCache {
            path: directory.path().join("performance.json"),
            measurements: Vec::new(),
        },
        BackendTuner::new(engine, move || clock.elapsed()),
    );
    selector.worker = worker();
    let config = EngineConfig::new(model(), "en".into(), 4, "auto".into());
    let started = std::time::Instant::now();
    let selected = selector
        .select(
            &config,
            &AtomicBool::new(false),
            false,
            0,
            || {},
            QUICK_BUDGET,
        )
        .unwrap();
    assert!(["cpu", "gpu"].contains(&selected.backend.as_str()));
    assert!(started.elapsed() < std::time::Duration::from_secs(16));
    assert!(!selector.measurements.is_empty());
    assert!(
        selector
            .measurements
            .iter()
            .all(|r| r.get("text").is_none())
    );
    println!(
        "Quick native calibration: status={}, backend={}, records={}, elapsed={:.2}s",
        selector.status,
        selected.backend,
        selector.measurements.len(),
        started.elapsed().as_secs_f64()
    );
}

fn fixture(path: &str) -> Vec<u8> {
    let wav = std::fs::read(path).unwrap();
    assert_eq!(&wav[..4], b"RIFF");
    assert_eq!(&wav[8..12], b"WAVE");
    let mut offset = 12;
    let mut format_ok = false;
    while offset + 8 <= wav.len() {
        let size = u32::from_le_bytes(wav[offset + 4..offset + 8].try_into().unwrap()) as usize;
        let chunk = &wav[offset + 8..offset + 8 + size];
        match &wav[offset..offset + 4] {
            b"fmt " => {
                assert_eq!(&chunk[..4], &[1, 0, 1, 0]); // PCM mono
                assert_eq!(u32::from_le_bytes(chunk[4..8].try_into().unwrap()), 16000);
                assert_eq!(&chunk[14..16], &[16, 0]);
                format_ok = true;
            }
            b"data" => {
                assert!(format_ok);
                return chunk.to_vec();
            }
            _ => {}
        }
        offset += 8 + size + size % 2;
    }
    panic!("Missing PCM fixture");
}

#[test]
#[ignore = "requires native worker, model and OpenCC runtime"]
fn native_asr_reuses_worker_and_vad_classifies_real_audio() {
    let cancel = AtomicBool::new(false);
    let pcm = fixture("data/benchmark/en-short.wav");
    let mut engine = engine(EngineConfig::new(model(), "en".into(), 4, "cpu".into())).unwrap();
    let first = engine.transcribe(&pcm, &cancel).unwrap();
    assert!(!first.is_empty());
    assert!(engine.is_running());
    assert!(engine.last_metrics.contains_key("inference_ms"));
    let second = engine.transcribe(&pcm, &cancel).unwrap();
    assert_eq!(first, second);
    assert!(!engine.last_metrics.contains_key("load_ms"));
    println!("Native ASR cold/warm protocol verified; transcript withheld");
    engine.close();
    assert!(!engine.is_running());
    let mut vad = VadEngine::start(&vad_model(), &worker(), &cancel).unwrap();
    let mut maximum = 0.0_f64;
    for frame in pcm.chunks_exact(VadEngine::FRAME_BYTES) {
        maximum = maximum.max(vad.classify(frame, &cancel).unwrap());
    }
    assert!(maximum > 0.5, "Native VAD should detect fixture speech");
}

#[test]
#[ignore = "requires distribution OpenCC runtime"]
fn native_chinese_conversion() {
    let simplified = ChineseConverter::new("zh-CN").unwrap().unwrap();
    assert_eq!(simplified.convert("語音輸入").unwrap(), "语音输入");
    let traditional = ChineseConverter::new("zh-TW").unwrap().unwrap();
    assert_eq!(traditional.convert("语音输入").unwrap(), "語音輸入");
}

fn children() -> Vec<u32> {
    std::fs::read_to_string("/proc/thread-self/children")
        .unwrap()
        .split_whitespace()
        .map(|pid| pid.parse().unwrap())
        .collect()
}
fn resources(pid: u32) -> (u64, usize, u64) {
    let status = std::fs::read_to_string(format!("/proc/{pid}/status")).unwrap();
    let field = |name: &str| -> u64 {
        status
            .lines()
            .find_map(|line| line.strip_prefix(name))
            .unwrap()
            .split_whitespace()
            .next()
            .unwrap()
            .parse()
            .unwrap()
    };
    (
        field("VmRSS:"),
        std::fs::read_dir(format!("/proc/{pid}/fd"))
            .unwrap()
            .count(),
        field("Threads:"),
    )
}

#[test]
#[ignore = "native resident stress: requires installed worker and build model"]
fn native_resident_resources_cancel_and_restart() {
    use anduinos_whisper_framework::transport::WorkerError;
    use std::sync::atomic::Ordering;
    use std::time::{Duration, Instant};
    let original = children();
    let cancel = AtomicBool::new(false);
    let pcm = fixture("data/benchmark/en-short.wav");
    let mut engine = engine(EngineConfig::new(model(), "en".into(), 4, "cpu".into())).unwrap();
    let expected = engine.transcribe(&pcm, &cancel).unwrap();
    assert!(!expected.is_empty());
    // Warm allocator and inference scratch buffers before measuring growth.
    for _ in 0..3 {
        assert_eq!(engine.transcribe(&pcm, &cancel).unwrap(), expected);
    }
    let spawned: Vec<_> = children()
        .into_iter()
        .filter(|pid| !original.contains(pid))
        .collect();
    assert_eq!(spawned.len(), 1);
    let pid = spawned[0];
    let baseline = resources(pid);
    let repetitions = std::env::var("ANDUINOS_VOICE_STRESS_REQUESTS")
        .map(|value| {
            value
                .parse::<usize>()
                .expect("Invalid stress request count")
        })
        .unwrap_or(12);
    assert!((12..=1000).contains(&repetitions));
    for _ in 0..repetitions {
        assert_eq!(engine.transcribe(&pcm, &cancel).unwrap(), expected);
        let current = resources(pid);
        assert!(
            current.0 <= baseline.0 + 64 * 1024,
            "resident RSS grew by over 64 MiB"
        );
        assert_eq!(current.1, baseline.1, "worker descriptors leaked");
        assert_eq!(current.2, baseline.2, "worker threads leaked");
        assert_eq!(
            children()
                .into_iter()
                .filter(|p| !original.contains(p))
                .collect::<Vec<_>>(),
            spawned
        );
    }
    // Exercise cancellation during a warm, longer request, then use the same
    // engine again. Either acknowledged reuse or a clean restart is acceptable.
    let long_audio: Vec<_> = pcm.iter().copied().cycle().take(30 * 32000).collect();
    let started = Instant::now();
    std::thread::scope(|scope| {
        scope.spawn(|| {
            std::thread::sleep(Duration::from_millis(40));
            cancel.store(true, Ordering::Release);
        });
        assert_eq!(
            engine.transcribe(&long_audio, &cancel),
            Err(WorkerError::Cancelled)
        );
    });
    assert!(started.elapsed() < Duration::from_secs(2));
    cancel.store(false, Ordering::Release);
    assert_eq!(engine.transcribe(&pcm, &cancel).unwrap(), expected);
    engine.close();
    assert_eq!(children(), original, "engine close must reap its worker");
}

#[test]
#[ignore = "native recurrent VAD stress with public audio; no microphone"]
fn native_vad_replay_is_deterministic_and_bounded() {
    use anduinos_whisper_framework::calibration::digest;
    let manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read("data/benchmark/manifest.json").unwrap()).unwrap();
    let mut pcm = Vec::new();
    for sample in manifest["samples"].as_array().unwrap() {
        let path = format!("data/benchmark/{}", sample["file"].as_str().unwrap());
        assert_eq!(
            digest(&std::fs::read(&path).unwrap()),
            sample["sha256"].as_str().unwrap()
        );
        pcm.extend(fixture(&path));
        pcm.extend(vec![0; 64000]);
    }
    pcm.resize(
        pcm.len().div_ceil(VadEngine::FRAME_BYTES) * VadEngine::FRAME_BYTES,
        0,
    );
    let frames: Vec<_> = pcm.chunks_exact(VadEngine::FRAME_BYTES).collect();
    let count = std::env::var("ANDUINOS_VAD_STRESS_FRAMES")
        .map(|v| v.parse::<usize>().expect("Invalid VAD stress frame count"))
        .unwrap_or(2000);
    assert!((2000..=300000).contains(&count));
    let original = children();
    let cancel = AtomicBool::new(false);
    let mut expected = Vec::with_capacity(count);
    for replay in 0..2 {
        let mut vad = VadEngine::start(&vad_model(), &worker(), &cancel).unwrap();
        let spawned: Vec<_> = children()
            .into_iter()
            .filter(|p| !original.contains(p))
            .collect();
        assert_eq!(spawned.len(), 1);
        let pid = spawned[0];
        let mut baseline = None;
        for index in 0..count {
            let probability = vad.classify(frames[index % frames.len()], &cancel).unwrap();
            if replay == 0 {
                expected.push(probability);
            } else {
                assert_eq!(probability, expected[index], "VAD state changed on replay");
            }
            if (index + 1) % 1000 == 0 {
                let current = resources(pid);
                let initial = baseline.get_or_insert(current);
                assert!(
                    current.0 <= initial.0 + 16 * 1024,
                    "VAD RSS growth exceeded 16 MiB"
                );
                assert_eq!(current.1, initial.1, "VAD leaked descriptors");
                assert_eq!(current.2, initial.2, "VAD leaked threads");
                assert_eq!(
                    children()
                        .into_iter()
                        .filter(|p| !original.contains(p))
                        .collect::<Vec<_>>(),
                    spawned
                );
                if (index + 1) % 10000 == 0 {
                    println!("VAD replay {replay}: {} frames", index + 1);
                }
            }
        }
        drop(vad);
        assert_eq!(children(), original, "VAD child not reaped");
    }
    assert!(expected.iter().any(|p| *p >= 0.5));
    assert!(expected.iter().any(|p| *p < 0.5));
    println!(
        "Deterministic VAD: two replays of {} simulated seconds",
        count as f64 * 0.032
    );
}

// Independent CLI decoding baseline; no legacy backend implementation required.
fn cli_transcript(pcm: &[u8], language: &str) -> String {
    use anduinos_whisper_framework::commands::{clean_transcript, whisper_language};
    let directory = tempfile::tempdir().unwrap();
    let audio = directory.path().join("phrase.wav");
    let mut writer = hound::WavWriter::create(
        &audio,
        hound::WavSpec {
            channels: 1,
            sample_rate: 16000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    for bytes in pcm.chunks_exact(2) {
        writer
            .write_sample(i16::from_le_bytes(bytes.try_into().unwrap()))
            .unwrap();
    }
    writer.finalize().unwrap();
    let cli =
        std::env::var_os("ANDUINOS_WHISPER_CLI").unwrap_or_else(|| "/usr/bin/whisper-cli".into());
    let output = std::process::Command::new("timeout")
        .arg("120")
        .arg(cli)
        .arg("--model")
        .arg(model())
        .arg("--file")
        .arg(audio)
        .args([
            "--language",
            whisper_language(language),
            "--threads",
            "4",
            "--no-timestamps",
            "--suppress-nst",
            "--no-gpu",
        ])
        .output()
        .expect("Cannot run whisper-cli baseline via timeout");
    assert!(
        output.status.success(),
        "CLI baseline failed: {}",
        output.status
    );
    let text = clean_transcript(&String::from_utf8(output.stdout).unwrap());
    match ChineseConverter::new(language).unwrap() {
        Some(converter) => converter.convert(&text).unwrap(),
        None => text,
    }
}

// WER for English, CER for Chinese, matching the retained corpus specification.
fn error_count(expected: &str, actual: &str, language: &str) -> usize {
    use unicode_normalization::UnicodeNormalization;
    let units = |text: &str| -> Vec<String> {
        let text = glib::casefold(text.nfkc().collect::<String>());
        if language.starts_with("zh") {
            text.chars()
                .filter(|c| c.is_alphanumeric())
                .map(|c| c.to_string())
                .collect()
        } else {
            text.split(|c: char| !c.is_alphanumeric())
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
                .collect()
        }
    };
    let expected = units(expected);
    let actual = units(actual);
    let mut row: Vec<_> = (0..=actual.len()).collect();
    for (i, left) in expected.iter().enumerate() {
        let mut next = vec![i + 1];
        for (j, right) in actual.iter().enumerate() {
            next.push(
                (row[j + 1] + 1)
                    .min(next[j] + 1)
                    .min(row[j] + usize::from(left != right)),
            );
        }
        row = next;
    }
    row[actual.len()]
}

#[test]
#[ignore = "public corpus accuracy gate; requires native models and whisper-cli"]
fn native_corpus_accuracy_against_cli() {
    use anduinos_whisper_framework::calibration::{digest, noisy};
    use serde_json::Value;
    let manifest: Value =
        serde_json::from_slice(&std::fs::read("data/benchmark/manifest.json").unwrap()).unwrap();
    let cancel = AtomicBool::new(false);
    let mut checked = 0;
    for language in ["auto", "en", "zh-Hans"] {
        let mut engine =
            engine(EngineConfig::new(model(), language.into(), 4, "cpu".into())).unwrap();
        for sample in manifest["samples"].as_array().unwrap() {
            if language != "auto" && sample["language"] != language {
                continue;
            }
            let name = sample["file"].as_str().unwrap();
            let path = format!("data/benchmark/{name}");
            assert_eq!(
                digest(&std::fs::read(&path).unwrap()),
                sample["sha256"].as_str().unwrap()
            );
            let clean = fixture(&path);
            for (condition, pcm) in [("clean", clean.clone()), ("noisy", noisy(&clean).unwrap())] {
                let baseline = cli_transcript(&pcm, language);
                let result = engine.transcribe(&pcm, &cancel).unwrap();
                assert!(
                    !baseline.is_empty() && !result.is_empty(),
                    "Empty corpus recognition"
                );
                let expected = sample["text"].as_str().unwrap();
                let scoring_language = sample["language"].as_str().unwrap();
                let baseline_errors = error_count(expected, &baseline, scoring_language);
                let errors = error_count(expected, &result, scoring_language);
                // Public fixture identifiers and counts only; no transcript logging.
                println!(
                    "{language}/{name}/{condition}: Rust errors={errors}, CLI errors={baseline_errors}"
                );
                assert!(
                    errors <= baseline_errors,
                    "Accuracy regression: language={language}, fixture={name}, condition={condition}"
                );
                assert_eq!(engine.last_metrics["backend"], "cpu");
                checked += 1;
            }
        }
    }
    assert_eq!(checked, 16);
    println!("All {checked} clean/noisy corpus cases pass CLI accuracy nonregression");
}
