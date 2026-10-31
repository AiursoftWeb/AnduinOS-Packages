//! Single recognition owner plus a bounded PCM queue. The main loop owns GTK/
//! D-Bus state; capture callbacks only admit work or post lightweight events.
use crate::config::{SessionConfig, model_path};
use crate::diagnostics::PerformanceHistory;
use crate::resident::{EngineConfig, ResidentEngine, VAD_MODEL, VadEngine, WORKER};
use crate::segmentation::CaptureEvent;
use crate::session_engine::SessionEngine;
use crate::transport::WorkerError;
use crate::tuning::{AutomaticSelector, BackendTuner, FULL_BUDGET, QUICK_BUDGET, SelectionCache};
use crate::work_queue::{RecognitionQueue, Work};
use serde_json::{Value, json};
use std::path::PathBuf;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

pub enum Event {
    Calibrating {
        session: u64,
        full: bool,
    },
    Preparing(u64),
    FullTuningComplete(SessionConfig),
    Prepared {
        session: u64,
        vad: VadEngine,
    },
    Accepted(u64),
    Overloaded(u64),
    Level {
        session: u64,
        level: f64,
    },
    NoSpeech(u64),
    CaptureFailed {
        session: u64,
        message: String,
    },
    Completed {
        session: u64,
        generation: u64,
        partial: bool,
        text: String,
        ticket: u32,
        at: Instant,
    },
    Failed {
        session: u64,
        message: String,
    },
}
enum Payload {
    Prepare(SessionConfig),
    Audio {
        config: EngineConfig,
        model: String,
        pcm: Vec<u8>,
        metadata: Value,
        partial: bool,
    },
}
struct Job {
    session: u64,
    generation: u64,
    queued: Instant,
    payload: Payload,
}
struct Control {
    session: u64,
    active: bool,
    testing: bool,
    pending: usize,
    generation: u64,
    floor: u64,
    current: Option<(bool, Arc<AtomicBool>)>,
    config: Option<(EngineConfig, String)>,
    preview_allowed: bool,
    live_mode: String,
}
pub struct Shared {
    paths: BackendPaths,
    control: Mutex<Control>,
    queue: RecognitionQueue<Job>,
    events: async_channel::Sender<Event>,
    pub history: Mutex<PerformanceHistory>,
}

/// Defaults are fixed installed locations. Explicit paths support source-built
/// test artifacts; no D-Bus method or environment variable can change them.
#[derive(Clone)]
pub struct BackendPaths {
    pub worker: PathBuf,
    pub model: Option<PathBuf>,
    pub vad: PathBuf,
    pub fixtures: PathBuf,
}
impl Default for BackendPaths {
    fn default() -> Self {
        Self {
            worker: WORKER.into(),
            model: None,
            vad: VAD_MODEL.into(),
            fixtures: "/usr/share/anduinos-whisper-framework/benchmark".into(),
        }
    }
}
impl BackendPaths {
    fn model(&self, key: &str) -> PathBuf {
        self.model.clone().unwrap_or_else(|| model_path(key))
    }
}
impl Shared {
    pub fn model_installed(&self, key: &str) -> bool {
        self.paths
            .model(key)
            .metadata()
            .is_ok_and(|s| s.is_file() && s.len() >= 1_000_000)
    }
    fn emit(&self, event: Event) {
        let _ = self.events.try_send(event);
    }
    fn invalidate_locked(&self, c: &mut Control) {
        c.generation = c.generation.wrapping_add(1);
        c.floor = c.generation;
        if let Some((true, cancel)) = &c.current {
            cancel.store(true, Ordering::Release);
        }
        self.queue.clear(true);
    }
    pub fn invalidate_partials(&self) {
        self.invalidate_locked(&mut self.control.lock().unwrap());
    }
    pub fn new_session(&self, active: bool, testing: bool) -> u64 {
        let mut c = self.control.lock().unwrap();
        if let Some((_, cancel)) = &c.current {
            cancel.store(true, Ordering::Release);
        }
        self.queue.clear(false);
        c.session = c.session.wrapping_add(1);
        c.active = active;
        c.testing = testing;
        c.pending = 0;
        c.config = None;
        c.preview_allowed = false;
        self.invalidate_locked(&mut c);
        c.session
    }
    pub fn prepare(&self, session: u64, config: SessionConfig) {
        let c = self.control.lock().unwrap();
        if c.session != session || !c.active {
            return;
        }
        let result = self.queue.put(Work::Final(Job {
            session,
            generation: 0,
            queued: Instant::now(),
            payload: Payload::Prepare(config),
        }));
        debug_assert!(result.is_ok());
    }
    pub fn current_session(&self) -> u64 {
        self.control.lock().unwrap().session
    }
    pub fn active(&self) -> bool {
        self.control.lock().unwrap().active
    }
    pub fn testing(&self) -> bool {
        self.control.lock().unwrap().testing
    }
    pub fn pending(&self) -> usize {
        self.control.lock().unwrap().pending
    }
    pub fn stop_accepting(&self) {
        let mut c = self.control.lock().unwrap();
        c.active = false;
        self.invalidate_locked(&mut c);
    }
    pub fn set_live_mode(&self, mode: String) {
        self.control.lock().unwrap().live_mode = mode;
    }
    pub fn complete_final(&self, session: u64) -> bool {
        let mut c = self.control.lock().unwrap();
        if c.session != session {
            return false;
        }
        c.pending = c.pending.saturating_sub(1);
        true
    }
    pub fn partial_valid(&self, session: u64, generation: u64) -> bool {
        let c = self.control.lock().unwrap();
        c.session == session
            && c.active
            && generation > c.floor
            && crate::live_policy::permits_preview(&c.live_mode, c.preview_allowed)
    }
    pub fn capture(&self, session: u64, event: Result<CaptureEvent, String>) {
        let mut c = self.control.lock().unwrap();
        if c.session != session || !(c.active || c.testing) {
            return;
        }
        let event = match event {
            Ok(event) => event,
            Err(message) => {
                self.emit(Event::CaptureFailed { session, message });
                return;
            }
        };
        let (pcm, metadata, partial) = match event {
            CaptureEvent::Level(level) => {
                self.emit(Event::Level { session, level });
                return;
            }
            CaptureEvent::NoSpeech => {
                self.emit(Event::NoSpeech(session));
                return;
            }
            CaptureEvent::Final {
                pcm,
                endpoint_ms,
                reason,
            } => (
                pcm,
                json!({"endpoint_ms":endpoint_ms,"endpoint_reason":reason}),
                false,
            ),
            CaptureEvent::Partial(pcm) => (pcm, json!({}), true),
        };
        if !c.active {
            return;
        }
        if partial && !crate::live_policy::permits_preview(&c.live_mode, c.preview_allowed) {
            return;
        }
        let Some((config, model)) = c.config.clone() else {
            return;
        };
        if partial {
            c.generation = c.generation.wrapping_add(1);
        } else {
            self.invalidate_locked(&mut c);
        }
        let job = Job {
            session,
            generation: c.generation,
            queued: Instant::now(),
            payload: Payload::Audio {
                config,
                model,
                pcm,
                metadata,
                partial,
            },
        };
        if partial {
            let _ = self.queue.put(Work::Partial(job));
        } else if self.queue.put(Work::Final(job)).is_ok() {
            c.pending += 1;
            self.emit(Event::Accepted(session));
        } else {
            c.active = false;
            self.emit(Event::Overloaded(session));
        }
    }
}
pub struct Runtime {
    pub shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}
impl Runtime {
    pub fn new() -> (Self, async_channel::Receiver<Event>) {
        Self::with_paths(BackendPaths::default())
    }
    pub fn with_paths(paths: BackendPaths) -> (Self, async_channel::Receiver<Event>) {
        let (shared, receiver) = shared_channel(paths);
        let worker = shared.clone();
        let thread = std::thread::spawn(move || worker_loop(worker));
        (
            Self {
                shared,
                thread: Some(thread),
            },
            receiver,
        )
    }
    pub fn shutdown(&mut self) {
        self.shared.new_session(false, false);
        let _ = self.shared.queue.put(Work::Quit);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
fn shared_channel(paths: BackendPaths) -> (Arc<Shared>, async_channel::Receiver<Event>) {
    let (events, receiver) = async_channel::unbounded();
    let shared = Arc::new(Shared {
        paths,
        control: Mutex::new(Control {
            session: 0,
            active: false,
            testing: false,
            pending: 0,
            generation: 0,
            floor: 0,
            current: None,
            config: None,
            preview_allowed: false,
            live_mode: "auto".into(),
        }),
        queue: RecognitionQueue::new(8),
        events,
        history: Mutex::new(PerformanceHistory::default()),
    });
    (shared, receiver)
}
impl Drop for Runtime {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn listening() -> (Arc<Shared>, async_channel::Receiver<Event>, u64) {
        let (shared, receiver) = shared_channel(BackendPaths::default());
        let session = shared.new_session(true, false);
        shared.control.lock().unwrap().config = Some((
            EngineConfig::new("unused".into(), "en".into(), 1, "cpu".into()),
            "base".into(),
        ));
        shared.set_live_mode("on".into());
        (shared, receiver, session)
    }
    fn final_audio() -> Result<CaptureEvent, String> {
        Ok(CaptureEvent::Final {
            pcm: vec![0; 16000],
            endpoint_ms: 800.0,
            reason: "silence",
        })
    }
    #[test]
    fn overload_stops_admission_without_losing_eight_accepted_finals() {
        let (shared, receiver, session) = listening();
        for _ in 0..9 {
            shared.capture(session, final_audio());
        }
        assert!(!shared.active());
        assert_eq!(shared.pending(), 8);
        for _ in 0..8 {
            assert!(matches!(receiver.try_recv(), Ok(Event::Accepted(id)) if id == session));
            assert!(matches!(
                shared.queue.get(Duration::ZERO),
                Some(Work::Final(_))
            ));
        }
        assert!(matches!(receiver.try_recv(), Ok(Event::Overloaded(id)) if id == session));
        assert!(shared.queue.get(Duration::ZERO).is_none());
        for _ in 0..8 {
            assert!(shared.complete_final(session));
        }
        assert_eq!(shared.pending(), 0);
    }
    #[test]
    fn finish_retains_finals_but_stop_cancels_work_and_rejects_old_callbacks() {
        let (shared, receiver, session) = listening();
        shared.capture(session, final_audio());
        shared.stop_accepting();
        assert_eq!(shared.pending(), 1);
        assert!(matches!(
            shared.queue.get(Duration::ZERO),
            Some(Work::Final(_))
        ));
        let cancel = Arc::new(AtomicBool::new(false));
        shared.control.lock().unwrap().current = Some((false, cancel.clone()));
        shared.new_session(false, false);
        assert!(cancel.load(Ordering::Acquire));
        assert_eq!(shared.pending(), 0);
        while receiver.try_recv().is_ok() {}
        shared.capture(session, final_audio());
        shared.capture(session, Ok(CaptureEvent::Level(0.5)));
        assert!(receiver.try_recv().is_err());
        assert!(!shared.complete_final(session));
    }
    #[test]
    fn final_invalidates_inflight_preview_and_new_previews_replace_queued_work() {
        let (shared, _, session) = listening();
        shared.capture(session, Ok(CaptureEvent::Partial(vec![1; 16000])));
        shared.capture(session, Ok(CaptureEvent::Partial(vec![2; 16000])));
        let Some(Work::Partial(job)) = shared.queue.get(Duration::ZERO) else {
            panic!("missing partial");
        };
        assert!(matches!(job.payload,Payload::Audio{pcm,..} if pcm[0]==2));
        assert!(shared.partial_valid(session, job.generation));
        let cancel = Arc::new(AtomicBool::new(false));
        shared.control.lock().unwrap().current = Some((true, cancel.clone()));
        shared.capture(session, final_audio());
        assert!(cancel.load(Ordering::Acquire));
        assert!(!shared.partial_valid(session, job.generation));
        assert!(matches!(
            shared.queue.get(Duration::ZERO),
            Some(Work::Final(_))
        ));
    }
}

fn worker_loop(shared: Arc<Shared>) {
    // Construct !Send OpenCC/session state inside its owning thread.
    let worker = shared.paths.worker.clone();
    let mut engine = SessionEngine::with_factory(
        move |config| ResidentEngine::with_executable(config, &worker),
        Duration::from_secs(180),
    );
    let worker = shared.paths.worker.clone();
    let started = Instant::now();
    let tuner = BackendTuner::new(
        move |config| ResidentEngine::with_executable(config, &worker),
        move || started.elapsed(),
    );
    let mut selector =
        AutomaticSelector::new(&shared.paths.fixtures, SelectionCache::default(), tuner);
    selector.worker = shared.paths.worker.clone();
    loop {
        let job = match shared.queue.get(Duration::from_secs(5)) {
            None => {
                engine.release_if_idle(Instant::now());
                continue;
            }
            Some(Work::Quit) => return,
            Some(Work::Final(job) | Work::Partial(job)) => job,
        };
        let partial = matches!(job.payload, Payload::Audio { partial: true, .. });
        let cancel = Arc::new(AtomicBool::new(false));
        {
            let mut c = shared.control.lock().unwrap();
            if c.session != job.session
                || (partial
                    && (!c.active
                        || job.generation != c.generation
                        || job.generation <= c.floor
                        || !crate::live_policy::permits_preview(&c.live_mode, c.preview_allowed)))
            {
                continue;
            }
            c.current = Some((partial, cancel.clone()));
        }
        let (kind, audio_ms) = match &job.payload {
            Payload::Prepare(_) => ("prepare", 0.0),
            Payload::Audio { pcm, partial, .. } => (
                if *partial { "partial" } else { "final" },
                pcm.len() as f64 / 32.0,
            ),
        };
        let result = run_job(&shared, &job, &cancel, &mut engine, &mut selector);
        if let Err(error) = result {
            let status = match error {
                WorkerError::Cancelled => "cancelled",
                WorkerError::Timeout => "timeout",
                _ => "error",
            };
            shared
                .history
                .lock()
                .unwrap()
                .append(&json!({"kind":kind,"status":status,"audio_ms":audio_ms}));
            if error != WorkerError::Cancelled && !partial {
                shared.emit(Event::Failed {
                    session: job.session,
                    message: error.to_string(),
                });
            }
        }
        shared.control.lock().unwrap().current = None;
    }
}
fn run_job(
    shared: &Shared,
    job: &Job,
    cancel: &AtomicBool,
    engine: &mut SessionEngine,
    selector: &mut AutomaticSelector,
) -> Result<(), WorkerError> {
    match &job.payload {
        Payload::Prepare(config) => {
            let mut selected = EngineConfig::new(
                shared.paths.model(&config.model),
                config.language.clone(),
                config.threads,
                config.backend.clone(),
            );
            let full = config.full_tuning;
            let budget = if full { FULL_BUDGET } else { QUICK_BUDGET };
            let result = selector.select(
                &selected,
                cancel,
                full,
                config.generation,
                || {
                    engine.close();
                    shared.emit(Event::Calibrating {
                        session: job.session,
                        full,
                    });
                },
                budget,
            );
            for measurement in &selector.measurements {
                let mut record = measurement.clone();
                record["model"] = config.model.clone().into();
                shared.history.lock().unwrap().append(&record);
            }
            let choice = result?;
            if full {
                shared.emit(Event::FullTuningComplete(config.clone()));
            }
            if selector.status == "measured"
                || (selector.status == "manual" && choice.backend == "gpu" && engine.gpu_failed())
            {
                engine.invalidate();
            }
            selected.backend = choice.backend;
            selected.threads = choice.threads;
            shared.emit(Event::Preparing(job.session));
            let (fixture, _) = crate::calibration::load(&selector.directory, &config.language)?;
            if engine.prepare(&selected, &fixture, cancel)? {
                let mut record = Value::Object(engine.last_metrics.clone());
                record["kind"] = "benchmark".into();
                record["status"] = "success".into();
                shared.history.lock().unwrap().append(&record);
            }
            let vad = VadEngine::start(&shared.paths.vad, &shared.paths.worker, cancel)?;
            let mut record = Value::Object(vad.ready_metrics.clone());
            record.as_object_mut().unwrap().extend(json!({"kind":"benchmark","engine":"vad","backend":"cpu","threads":1,"status":"success"}).as_object().unwrap().clone());
            shared.history.lock().unwrap().append(&record);
            let mut c = shared.control.lock().unwrap();
            if cancel.load(Ordering::Acquire) || c.session != job.session || !c.active {
                return Err(WorkerError::Cancelled);
            }
            c.config = Some((selected, config.model.clone()));
            c.preview_allowed = selector.preview_allowed;
            shared.emit(Event::Prepared {
                session: job.session,
                vad,
            });
        }
        Payload::Audio {
            config,
            model,
            pcm,
            metadata,
            partial,
        } => {
            let started = Instant::now();
            let text = engine.transcribe(config, pcm, cancel)?;
            let at = Instant::now();
            let mut record = Value::Object(engine.last_metrics.clone());
            record
                .as_object_mut()
                .unwrap()
                .extend(metadata.as_object().unwrap().clone());
            record.as_object_mut().unwrap().extend(json!({"kind":if *partial{"partial"}else{"final"},"model":model,"status":"success","queue_ms":started.duration_since(job.queued).as_secs_f64()*1000.0}).as_object().unwrap().clone());
            let ticket = shared.history.lock().unwrap().append(&record);
            if *partial && at.duration_since(started) > Duration::from_millis(400) {
                let mut c = shared.control.lock().unwrap();
                if c.session == job.session && c.live_mode == "auto" {
                    c.preview_allowed = false;
                }
            }
            shared.emit(Event::Completed {
                session: job.session,
                generation: job.generation,
                partial: *partial,
                text,
                ticket,
                at,
            });
        }
    }
    Ok(())
}
