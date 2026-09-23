//! Owned by the recognition thread. Idle eviction preserves GPU failure memory;
//! explicit revalidation or a changed model/configuration resets it.
use crate::resident::{EngineConfig, ResidentEngine};
use crate::transport::WorkerError;
use serde_json::{Map, Value};
use std::os::unix::fs::MetadataExt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

pub trait RecognitionEngine {
    fn transcribe(&mut self, pcm: &[u8], cancel: &AtomicBool) -> Result<String, WorkerError>;
    fn is_running(&mut self) -> bool;
    fn metrics(&self) -> &Map<String, Value>;
}
impl RecognitionEngine for ResidentEngine {
    fn transcribe(&mut self, pcm: &[u8], cancel: &AtomicBool) -> Result<String, WorkerError> {
        ResidentEngine::transcribe(self, pcm, cancel)
    }
    fn is_running(&mut self) -> bool {
        ResidentEngine::is_running(self)
    }
    fn metrics(&self) -> &Map<String, Value> {
        &self.last_metrics
    }
}

#[derive(PartialEq, Eq)]
struct Identity {
    config: EngineConfig,
    size: u64,
    mtime: i64,
    mtime_ns: i64,
}
impl Identity {
    fn read(config: &EngineConfig) -> Result<Self, WorkerError> {
        let stat = config
            .model
            .metadata()
            .map_err(|_| WorkerError::Protocol("The selected speech model is not installed"))?;
        Ok(Self {
            config: config.clone(),
            size: stat.len(),
            mtime: stat.mtime(),
            mtime_ns: stat.mtime_nsec(),
        })
    }
}

pub struct SessionEngine<E: RecognitionEngine = ResidentEngine> {
    factory: Box<dyn FnMut(EngineConfig) -> Result<E, WorkerError>>,
    engine: Option<E>,
    identity: Option<Identity>,
    gpu_failed: bool,
    last_used: Instant,
    idle_timeout: Duration,
    pub last_metrics: Map<String, Value>,
}
impl Default for SessionEngine {
    fn default() -> Self {
        Self::with_factory(ResidentEngine::new, Duration::from_secs(180))
    }
}
impl<E: RecognitionEngine> SessionEngine<E> {
    pub fn gpu_failed(&self) -> bool {
        self.gpu_failed
    }
    pub fn with_factory(
        factory: impl FnMut(EngineConfig) -> Result<E, WorkerError> + 'static,
        idle_timeout: Duration,
    ) -> Self {
        Self {
            factory: Box::new(factory),
            engine: None,
            identity: None,
            gpu_failed: false,
            last_used: Instant::now(),
            idle_timeout,
            last_metrics: Map::new(),
        }
    }
    pub fn close(&mut self) {
        self.engine = None;
    }
    pub fn invalidate(&mut self) {
        self.close();
        self.identity = None;
        self.gpu_failed = false;
        self.last_metrics.clear();
    }
    pub fn release_if_idle(&mut self, now: Instant) {
        if now.saturating_duration_since(self.last_used) >= self.idle_timeout {
            self.close();
        }
    }
    pub fn prepare(
        &mut self,
        config: &EngineConfig,
        fixture: &[u8],
        cancel: &AtomicBool,
    ) -> Result<bool, WorkerError> {
        if cancel.load(Ordering::Acquire) {
            return Err(WorkerError::Cancelled);
        }
        let identity = Identity::read(config)?;
        if self.identity.as_ref() == Some(&identity)
            && self
                .engine
                .as_mut()
                .is_some_and(RecognitionEngine::is_running)
        {
            self.last_metrics.clear();
            self.last_used = Instant::now();
            return Ok(false);
        }
        self.close();
        self.transcribe(config, fixture, cancel)?;
        Ok(true)
    }
    pub fn transcribe(
        &mut self,
        config: &EngineConfig,
        pcm: &[u8],
        cancel: &AtomicBool,
    ) -> Result<String, WorkerError> {
        self.last_metrics.clear();
        let result = self.transcribe_inner(config, pcm, cancel);
        self.last_used = Instant::now();
        result
    }
    fn transcribe_inner(
        &mut self,
        config: &EngineConfig,
        pcm: &[u8],
        cancel: &AtomicBool,
    ) -> Result<String, WorkerError> {
        if cancel.load(Ordering::Acquire) {
            return Err(WorkerError::Cancelled);
        }
        let identity = Identity::read(config)?;
        if self.identity.as_ref() != Some(&identity) {
            self.close();
            self.identity = Some(identity);
            self.gpu_failed = false;
        }
        let mut effective = config.clone();
        if self.gpu_failed {
            effective.backend = "cpu".into();
        }
        if self.engine.is_none() {
            self.engine = Some((self.factory)(effective.clone())?);
        }
        let result = self.engine.as_mut().unwrap().transcribe(pcm, cancel);
        let text = match result {
            Ok(text) => text,
            Err(WorkerError::Cancelled) => {
                // Only an acknowledged cancellation leaves a reusable process.
                if !self.engine.as_mut().unwrap().is_running() {
                    self.close();
                }
                return Err(WorkerError::Cancelled);
            }
            Err(error) => {
                self.close();
                // Missing/incompatible worker is not a GPU fault. Never retry a
                // cancellation, change model quality, or introduce a CLI fallback.
                if error == WorkerError::Unavailable || effective.backend != "gpu" {
                    return Err(error);
                }
                self.gpu_failed = true;
                effective.backend = "cpu".into();
                self.engine = Some((self.factory)(effective)?);
                match self.engine.as_mut().unwrap().transcribe(pcm, cancel) {
                    Ok(text) => text,
                    Err(error) => {
                        self.close();
                        return Err(error);
                    }
                }
            }
        };
        self.last_metrics = self.engine.as_ref().unwrap().metrics().clone();
        if self.gpu_failed {
            self.last_metrics
                .insert("fallback".into(), "gpu_failed".into());
        }
        Ok(text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::VecDeque;
    use std::rc::Rc;

    struct Fake {
        result: Option<Result<String, WorkerError>>,
        live: bool,
        metrics: Map<String, Value>,
    }
    impl RecognitionEngine for Fake {
        fn transcribe(&mut self, _: &[u8], _: &AtomicBool) -> Result<String, WorkerError> {
            self.result.take().unwrap_or(Ok("recognized".into()))
        }
        fn is_running(&mut self) -> bool {
            self.live
        }
        fn metrics(&self) -> &Map<String, Value> {
            &self.metrics
        }
    }
    fn config() -> EngineConfig {
        EngineConfig::new("Cargo.toml".into(), "en".into(), 2, "gpu".into())
    }
    fn setup(errors: Vec<WorkerError>) -> (SessionEngine<Fake>, Rc<RefCell<Vec<EngineConfig>>>) {
        let calls = Rc::new(RefCell::new(Vec::new()));
        let observed = calls.clone();
        let mut errors = VecDeque::from(errors);
        let session = SessionEngine::with_factory(
            move |config| {
                observed.borrow_mut().push(config);
                Ok(Fake {
                    result: errors.pop_front().map(Err),
                    live: true,
                    metrics: Map::new(),
                })
            },
            Duration::from_secs(10),
        );
        (session, calls)
    }
    #[test]
    fn warmup_reuses_worker_and_configuration_changes_reload() {
        let (mut session, calls) = setup(vec![]);
        let mut config = config();
        let cancel = AtomicBool::new(false);
        assert!(session.prepare(&config, b"fixture", &cancel).unwrap());
        assert!(!session.prepare(&config, b"fixture", &cancel).unwrap());
        config.threads = 4;
        assert!(session.prepare(&config, b"fixture", &cancel).unwrap());
        session.engine.as_mut().unwrap().live = false;
        assert!(session.prepare(&config, b"fixture", &cancel).unwrap());
        assert_eq!(calls.borrow().len(), 3);
    }
    #[test]
    fn fallback_is_once_same_model_and_survives_idle_eviction() {
        let (mut session, calls) = setup(vec![WorkerError::Timeout]);
        let config = config();
        let cancel = AtomicBool::new(false);
        session.transcribe(&config, b"audio", &cancel).unwrap();
        assert_eq!(session.last_metrics["fallback"], "gpu_failed");
        session.release_if_idle(Instant::now() + Duration::from_secs(11));
        session.transcribe(&config, b"audio", &cancel).unwrap();
        session.invalidate();
        session.transcribe(&config, b"audio", &cancel).unwrap();
        let calls = calls.borrow();
        assert_eq!(
            calls.iter().map(|c| c.backend.as_str()).collect::<Vec<_>>(),
            ["gpu", "cpu", "cpu", "gpu"]
        );
        assert!(calls.iter().all(|c| c.model == config.model));
    }
    #[test]
    fn errors_and_cancellation_never_create_retry_loops() {
        for error in [WorkerError::Unavailable, WorkerError::Cancelled] {
            let (mut session, calls) = setup(vec![error]);
            assert!(
                session
                    .transcribe(&config(), b"audio", &AtomicBool::new(false))
                    .is_err()
            );
            assert_eq!(calls.borrow().len(), 1);
        }
        let (mut session, calls) = setup(vec![WorkerError::Timeout, WorkerError::Timeout]);
        assert_eq!(
            session.transcribe(&config(), b"audio", &AtomicBool::new(false)),
            Err(WorkerError::Timeout)
        );
        assert_eq!(calls.borrow().len(), 2);
        assert!(session.engine.is_none());
        let (mut session, calls) = setup(vec![]);
        assert_eq!(
            session.prepare(&config(), b"fixture", &AtomicBool::new(true)),
            Err(WorkerError::Cancelled)
        );
        assert!(calls.borrow().is_empty());
    }
}
