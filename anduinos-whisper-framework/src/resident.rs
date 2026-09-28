use crate::commands::{ChineseConverter, clean_transcript, whisper_language};
use crate::diagnostics::sanitize;
use crate::transport::{WorkerError, WorkerTransport};
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

pub const WORKER: &str = "/usr/libexec/anduinos-whisper-worker";
pub const VAD_MODEL: &str = "/usr/share/anduinos-whisper-framework/models/ggml-silero-v6.2.0.bin";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EngineConfig {
    pub model: PathBuf,
    pub language: String,
    pub threads: u32,
    pub backend: String,
    pub beam: u32,
    pub no_fallback: bool,
    pub no_flash_attn: bool,
}
impl EngineConfig {
    pub fn new(model: PathBuf, language: String, threads: u32, backend: String) -> Self {
        Self {
            model,
            language,
            threads,
            backend,
            beam: 5,
            no_fallback: false,
            no_flash_attn: false,
        }
    }
    fn validate(&self) -> Result<(), WorkerError> {
        if !["cpu", "gpu"].contains(&self.backend.as_str())
            || !(1..=256).contains(&self.threads)
            || !(1..=5).contains(&self.beam)
        {
            Err(WorkerError::Protocol("Invalid recognition configuration"))
        } else {
            Ok(())
        }
    }
}

pub struct ResidentEngine {
    pub config: EngineConfig,
    executable: PathBuf,
    transport: Option<WorkerTransport>,
    converter: Option<ChineseConverter>,
    pub last_metrics: Map<String, Value>,
    pub ready_metrics: Map<String, Value>,
    pub timeout: Duration,
}
impl ResidentEngine {
    pub fn new(config: EngineConfig) -> Result<Self, WorkerError> {
        Self::with_executable(config, Path::new(WORKER))
    }
    pub fn with_executable(config: EngineConfig, executable: &Path) -> Result<Self, WorkerError> {
        config.validate()?;
        Ok(Self {
            config,
            executable: executable.to_owned(),
            transport: None,
            converter: None,
            last_metrics: Map::new(),
            ready_metrics: Map::new(),
            timeout: Duration::from_secs(120),
        })
    }
    pub fn is_running(&mut self) -> bool {
        self.transport
            .as_mut()
            .is_some_and(WorkerTransport::is_running)
    }
    pub fn close(&mut self) {
        self.transport = None;
    }
    pub fn transcribe(&mut self, pcm: &[u8], cancel: &AtomicBool) -> Result<String, WorkerError> {
        let started = Instant::now();
        self.last_metrics.clear();
        if pcm.len() < 16000 {
            return Ok(String::new());
        }
        if pcm.len() % 2 != 0 || pcm.len() > 60 * 32000 {
            return Err(WorkerError::Protocol("Invalid speech audio length"));
        }
        if cancel.load(Ordering::Acquire) {
            return Err(WorkerError::Cancelled);
        }
        if !self.is_running() {
            self.close();
            if !self.config.model.is_file() {
                return Err(WorkerError::Protocol(
                    "The selected speech model is not installed",
                ));
            }
            let mut command = Command::new(&self.executable);
            command
                .arg(&self.config.model)
                .arg(whisper_language(&self.config.language))
                .arg(self.config.threads.to_string())
                .arg(&self.config.backend)
                .arg(self.config.beam.to_string());
            if self.config.no_fallback {
                command.arg("--no-fallback");
            }
            if self.config.no_flash_attn {
                command.arg("--no-flash-attn");
            }
            let mut transport = WorkerTransport::spawn(&mut command, self.timeout)?;
            let ready = transport.exchange(&[], cancel)?;
            if ready["status"] != "ready" {
                return Err(WorkerError::Unavailable);
            }
            self.ready_metrics = sanitize(&ready["metrics"]);
            self.last_metrics = self.ready_metrics.clone();
            self.transport = Some(transport);
        }
        let mut request = Vec::with_capacity(pcm.len() + 4);
        request.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
        request.extend_from_slice(pcm);
        // Startup and inference share one request budget. Otherwise a cold
        // probe could consume the full tuning deadline twice.
        let remaining = self.timeout.saturating_sub(started.elapsed());
        if remaining.is_zero() {
            self.close();
            return Err(WorkerError::Timeout);
        }
        self.transport.as_mut().unwrap().set_timeout(remaining);
        let result = self
            .transport
            .as_mut()
            .unwrap()
            .exchange(&request, cancel)?;
        self.last_metrics.extend(sanitize(&result["metrics"]));
        if result["status"] == "cancelled" {
            return Err(WorkerError::Cancelled);
        }
        let Some(text) = result["text"]
            .as_str()
            .filter(|_| result["status"] == "success")
        else {
            self.close();
            return Err(WorkerError::Protocol("Speech recognition failed"));
        };
        let text = clean_transcript(text);
        if !text.is_empty() && self.converter.is_none() {
            self.converter = ChineseConverter::new(&self.config.language)
                .map_err(|_| WorkerError::Protocol("Chinese script conversion failed"))?;
        }
        match &self.converter {
            Some(converter) => converter
                .convert(&text)
                .map_err(|_| WorkerError::Protocol("Chinese script conversion failed")),
            None => Ok(text),
        }
    }
}

pub struct VadEngine {
    transport: WorkerTransport,
    pub ready_metrics: Map<String, Value>,
    failed: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    #[test]
    fn cold_start_and_inference_share_timeout_and_next_call_recovers() {
        let directory = tempfile::tempdir().unwrap();
        let executable = directory.path().join("worker");
        std::fs::write(&executable, "#!/usr/bin/env python3\nimport sys,time,struct\ntime.sleep(.07)\nprint('{\"status\":\"ready\"}',flush=True)\nwhile True:\n data=sys.stdin.buffer.read(4)\n if len(data)!=4: break\n size=struct.unpack('<I',data)[0]\n sys.stdin.buffer.read(size)\n time.sleep(.07)\n print('{\"status\":\"success\",\"text\":\"hello\"}',flush=True)\n").unwrap();
        std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o700)).unwrap();
        let model = directory.path().join("model");
        std::fs::write(&model, []).unwrap();
        let mut engine = ResidentEngine::with_executable(
            EngineConfig::new(model, "en".into(), 1, "cpu".into()),
            &executable,
        )
        .unwrap();
        engine.timeout = Duration::from_millis(110);
        assert_eq!(
            engine.transcribe(&[0; 16000], &AtomicBool::new(false)),
            Err(WorkerError::Timeout)
        );
        assert!(!engine.is_running());
        engine.timeout = Duration::from_secs(2);
        assert_eq!(
            engine
                .transcribe(&[0; 16000], &AtomicBool::new(false))
                .unwrap(),
            "hello"
        );
        assert!(engine.is_running());
    }
}
impl VadEngine {
    pub const FRAME_BYTES: usize = 1024;
    pub fn is_running(&mut self) -> bool {
        !self.failed && self.transport.is_running()
    }
    pub fn start(
        model: &Path,
        executable: &Path,
        cancel: &AtomicBool,
    ) -> Result<Self, WorkerError> {
        if cancel.load(Ordering::Acquire) {
            return Err(WorkerError::Cancelled);
        }
        if !model.is_file() {
            return Err(WorkerError::Unavailable);
        }
        let mut transport = WorkerTransport::spawn(
            Command::new(executable).arg("--vad").arg(model),
            Duration::from_secs(1),
        )?;
        let ready = transport.exchange(&[], cancel)?;
        if ready["status"] != "ready" || ready["mode"] != "vad" {
            return Err(WorkerError::Unavailable);
        }
        Ok(Self {
            transport,
            ready_metrics: sanitize(&ready["metrics"]),
            failed: false,
        })
    }
    pub fn classify(&mut self, pcm: &[u8], cancel: &AtomicBool) -> Result<f64, WorkerError> {
        if pcm.len() != Self::FRAME_BYTES {
            return Err(WorkerError::Protocol(
                "Speech detection expects exactly 32 ms of S16LE audio",
            ));
        }
        if self.failed {
            return Err(WorkerError::Protocol("Speech detection is not running"));
        }
        let mut request = Vec::with_capacity(pcm.len() + 4);
        request.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
        request.extend_from_slice(pcm);
        let result = self
            .transport
            .exchange(&request, cancel)
            .and_then(|result| {
                result["probability"]
                    .as_f64()
                    .filter(|p| {
                        result["status"] == "success" && p.is_finite() && (0.0..=1.0).contains(p)
                    })
                    .ok_or(WorkerError::Protocol("Invalid speech detection response"))
            });
        if result.is_err() {
            self.failed = true;
            self.transport.close();
        }
        result
    }
}
