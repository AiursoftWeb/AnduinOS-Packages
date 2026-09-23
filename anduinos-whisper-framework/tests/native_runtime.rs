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

#[test]
#[ignore = "public corpus migration gate; requires native model and Python reference dependencies"]
fn native_corpus_matches_python_reference() {
    use anduinos_whisper_framework::calibration::{digest, noisy};
    use serde_json::Value;
    let script = r#"
import json,sys,wave
from pathlib import Path
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.calibration_audio import noisy
language=sys.argv[1]
manifest=json.loads(Path('data/benchmark/manifest.json').read_text())
results=[]
with ResidentEngine(Path(sys.argv[2]),language,4,backend='cpu',executable=sys.argv[3]) as engine:
 for sample in manifest['samples']:
  if language!='auto' and sample['language']!=language: continue
  with wave.open('data/benchmark/'+sample['file'],'rb') as audio: pcm=audio.readframes(audio.getnframes())
  for condition,data in [('clean',pcm),('noisy',noisy(pcm))]:
   results.append({'file':sample['file'],'condition':condition,'text':engine.transcribe(data),'backend':engine.last_metrics['backend']})
print(json.dumps(results))
"#;
    let manifest: Value =
        serde_json::from_slice(&std::fs::read("data/benchmark/manifest.json").unwrap()).unwrap();
    let cancel = AtomicBool::new(false);
    let mut checked = 0;
    for language in ["auto", "en", "zh-Hans"] {
        let reference = std::process::Command::new("python3")
            .args(["-c", script, language])
            .arg(model())
            .arg(worker())
            .env("PYTHONPATH", "tests/reference:src")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .output()
            .unwrap();
        assert!(
            reference.status.success(),
            "Python reference failed: {}",
            String::from_utf8_lossy(&reference.stderr)
        );
        let records: Vec<Value> = serde_json::from_slice(&reference.stdout).unwrap();
        let mut engine =
            engine(EngineConfig::new(model(), language.into(), 4, "cpu".into())).unwrap();
        for record in records {
            let name = record["file"].as_str().unwrap();
            let path = format!("data/benchmark/{name}");
            let sample = manifest["samples"]
                .as_array()
                .unwrap()
                .iter()
                .find(|s| s["file"] == name)
                .unwrap();
            assert_eq!(
                digest(&std::fs::read(&path).unwrap()),
                sample["sha256"].as_str().unwrap()
            );
            let mut pcm = fixture(&path);
            if record["condition"] == "noisy" {
                pcm = noisy(&pcm).unwrap();
            }
            let result = engine.transcribe(&pcm, &cancel).unwrap();
            // Do not print transcripts on success or failure, even though these
            // particular inputs are public. Report the reproducing case only.
            assert!(
                result == record["text"].as_str().unwrap(),
                "Migration output mismatch: language={language}, fixture={name}, condition={}",
                record["condition"]
            );
            assert_eq!(record["backend"], "cpu");
            assert_eq!(engine.last_metrics["backend"], "cpu");
            checked += 1;
        }
    }
    assert_eq!(checked, 16);
    println!(
        "All {checked} clean/noisy corpus cases match the Python reference exactly (auto/en/zh-Hans)"
    );
}
