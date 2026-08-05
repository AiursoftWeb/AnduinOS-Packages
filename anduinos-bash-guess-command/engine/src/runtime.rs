use crate::history;
use crate::protocol::{decode_request, encode_response, Request, Response};
use crate::{
    evaluate, Action, Artifact, ArtifactKind, Container, FileEntry, GitRef, Host, Process, Query,
    Service, WorldState,
};
use std::collections::BTreeSet;
use std::ffi::{CString, OsStr, OsString};
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::OpenOptionsExt;
#[cfg(test)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{
    mpsc::{sync_channel, SyncSender, TrySendError},
    Arc, Condvar, Mutex, RwLock,
};
use std::thread;
use std::time::{Duration, Instant};

const BACKGROUND_WORKERS: usize = 2;
const BACKGROUND_QUEUE_CAPACITY: usize = 16;
const MAX_PATH_DIRECTORIES: usize = 64;
const MAX_PATH_ENTRIES: usize = 32_768;
const MAX_PATH_COMMANDS: usize = 8_192;
const PATH_SCAN_BUDGET: Duration = Duration::from_millis(100);
const MAX_COMMAND_OUTPUT_BYTES: u64 = 2 * 1024 * 1024;
const MAX_DOCKER_ENTITIES: usize = 4_096;
const MAX_PROCESS_ENTITIES: usize = 8_192;
const MAX_SERVICE_ENTITIES: usize = 8_192;
const MAX_GIT_REFS: usize = 8_192;
const FILE_SCAN_BUDGET: Duration = Duration::from_millis(150);
const MAX_CONTEXT_FILE_BYTES: u64 = 1024 * 1024;
const MAX_PROTOCOL_LINE_BYTES: usize = 64 * 1024;
const O_CLOEXEC: i32 = 0o2_000_000;
const O_NOFOLLOW: i32 = 0o400_000;
const O_NONBLOCK: i32 = 0o4_000;

type BackgroundJob = Box<dyn FnOnce() + Send + 'static>;

#[derive(Clone, Copy)]
struct PathScanLimits {
    directories: usize,
    entries: usize,
    commands: usize,
    duration: Duration,
}

const PATH_SCAN_LIMITS: PathScanLimits = PathScanLimits {
    directories: MAX_PATH_DIRECTORIES,
    entries: MAX_PATH_ENTRIES,
    commands: MAX_PATH_COMMANDS,
    duration: PATH_SCAN_BUDGET,
};

#[derive(Clone)]
struct BackgroundQueue {
    sender: SyncSender<BackgroundJob>,
    pending: Arc<(Mutex<usize>, Condvar)>,
}

#[derive(Clone)]
struct CommandPaths {
    fixture_bin: Option<PathBuf>,
}

impl CommandPaths {
    fn trusted() -> Self {
        Self { fixture_bin: None }
    }

    fn fixtures(path: PathBuf) -> Self {
        Self {
            fixture_bin: Some(path),
        }
    }

    fn resolve(&self, name: &str) -> Option<PathBuf> {
        let path = self
            .fixture_bin
            .as_ref()
            .map(|directory| directory.join(name))
            .unwrap_or_else(|| Path::new("/usr/bin").join(name));
        executable_for_current_user(&path).then_some(path)
    }
}

impl BackgroundQueue {
    fn new() -> Self {
        let (sender, receiver) = sync_channel::<BackgroundJob>(BACKGROUND_QUEUE_CAPACITY);
        let receiver = Arc::new(Mutex::new(receiver));
        let pending = Arc::new((Mutex::new(0), Condvar::new()));
        let mut started_workers = 0;
        for index in 0..BACKGROUND_WORKERS {
            let receiver = Arc::clone(&receiver);
            if thread::Builder::new()
                .name(format!("anduinos-observer-{index}"))
                .stack_size(256 * 1024)
                .spawn(move || loop {
                    let job = {
                        let Ok(receiver) = receiver.lock() else {
                            return;
                        };
                        receiver.recv()
                    };
                    match job {
                        Ok(job) => job(),
                        Err(_) => return,
                    }
                })
                .is_ok()
            {
                started_workers += 1;
            }
        }
        if started_workers == 0 {
            debug("unable to start background observers; semantic snapshots are disabled");
        }
        Self { sender, pending }
    }

    fn submit(&self, job: impl FnOnce() + Send + 'static) {
        let pending = Arc::clone(&self.pending);
        if let Ok(mut count) = pending.0.lock() {
            *count = count.saturating_add(1);
        } else {
            debug("background observer accounting is unavailable; dropping refresh");
            return;
        }
        let wrapped = Box::new(move || {
            // A failed observer must not permanently prevent graceful drain.
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(job));
            finish_background_job(&pending);
        });
        match self.sender.try_send(wrapped) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => {
                finish_background_job(&self.pending);
                debug("background observer queue is full; dropping refresh");
            }
            Err(TrySendError::Disconnected(_)) => {
                finish_background_job(&self.pending);
                debug("background observer queue is unavailable; dropping refresh");
            }
        }
    }

    fn wait_for_idle(&self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        let (lock, changed) = &*self.pending;
        let Ok(mut count) = lock.lock() else {
            return false;
        };
        while *count != 0 {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return false;
            }
            let Ok((next, result)) = changed.wait_timeout(count, remaining) else {
                return false;
            };
            count = next;
            if result.timed_out() && *count != 0 {
                return false;
            }
        }
        true
    }
}

fn finish_background_job(pending: &(Mutex<usize>, Condvar)) {
    if let Ok(mut count) = pending.0.lock() {
        *count = count.saturating_sub(1);
        if *count == 0 {
            pending.1.notify_all();
        }
    }
}

pub struct Runtime {
    world: Arc<RwLock<WorldState>>,
    history_path: Option<PathBuf>,
    transition_path: Option<PathBuf>,
    history_enabled: bool,
    background: BackgroundQueue,
    command_paths: CommandPaths,
}

impl Default for Runtime {
    fn default() -> Self {
        Self::new(CommandPaths::trusted())
    }
}

impl Runtime {
    fn new(command_paths: CommandPaths) -> Self {
        let history_enabled = history::enabled();
        let history_path = history::state_path();
        let transition_path = history::transition_state_path();
        let mut world = WorldState::default();
        if history_enabled {
            if let Some(path) = history_path.as_deref() {
                world.history = history::load(path);
            }
            world.merge_history(history::load_bash_history());
            if let Some(path) = transition_path.as_deref() {
                world.transitions = history::load_transitions(path);
            }
            world.merge_transitions(history::load_bash_transitions());
        }
        let runtime = Self {
            world: Arc::new(RwLock::new(world)),
            history_path,
            transition_path,
            history_enabled,
            background: BackgroundQueue::new(),
            command_paths,
        };
        runtime.prewarm_local_entities();
        runtime
    }

    pub fn with_world(world: WorldState) -> Self {
        Self {
            world: Arc::new(RwLock::new(world)),
            history_path: None,
            transition_path: None,
            history_enabled: true,
            background: BackgroundQueue::new(),
            command_paths: CommandPaths::trusted(),
        }
    }

    pub fn handle(&self, request: Request) -> Response {
        match request {
            Request::Query { now_ms, line } => {
                let Ok(world) = self.world.read() else {
                    return Response::None {
                        authoritative: false,
                    };
                };
                let decision = evaluate(
                    Query {
                        line: &line,
                        cursor: line.len(),
                        now_ms,
                    },
                    &world,
                );
                match decision.suggestion {
                    Some(suggestion) => Response::Suggestion {
                        insertion: suggestion.insertion,
                        confidence_milli: (suggestion.candidate.confidence * 1000.0) as u16,
                        source: format!("{:?}", suggestion.candidate.source),
                    },
                    None => {
                        debug(&format!(
                            "no suggestion for {line:?}; now={now_ms}, docker_refresh={}, event={:?}",
                            world.docker.refreshed_at_ms, world.last_event
                        ));
                        Response::None {
                            authoritative: decision.authoritative,
                        }
                    }
                }
            }
            Request::Observe {
                exit_code,
                now_ms,
                line,
                cwd,
            } => {
                let (
                    docker_refresh,
                    process_refresh,
                    service_refresh,
                    git_refresh,
                    host_refresh,
                    file_refresh,
                    artifact_refresh,
                    learned_history,
                    learned_transition,
                ) = {
                    let Ok(mut world) = self.world.write() else {
                        return Response::Error;
                    };
                    let learned = if self.history_enabled {
                        world.observe_command_with_cwd(&line, exit_code, now_ms, &cwd)
                    } else {
                        world.observe_command_without_learning(&line, exit_code, now_ms, &cwd);
                        None
                    };
                    let (learned_history, learned_transition) = match learned {
                        Some((history, transition)) => (Some(history), transition),
                        None => (None, None),
                    };
                    let action = world.last_event.as_ref().map(|event| event.action.clone());
                    if matches!(action, Some(Action::DockerList { .. })) {
                        // Invalidate before refreshing. Queries during refresh
                        // must be silent instead of seeing the old generation.
                        world.docker.generation = world.docker.generation.wrapping_add(1);
                        world.docker.refreshed_at_ms = now_ms;
                        world.docker.containers.clear();
                    }
                    let docker = match action {
                        Some(Action::DockerList { elevated }) if exit_code == 0 => {
                            Some((elevated, world.docker.generation))
                        }
                        _ => None,
                    };
                    let process = if action == Some(Action::ProcessList) && exit_code == 0 {
                        world.processes.generation = world.processes.generation.wrapping_add(1);
                        world.processes.refreshed_at_ms = now_ms;
                        Some(world.processes.generation)
                    } else {
                        None
                    };
                    let service = if action == Some(Action::ServiceList) && exit_code == 0 {
                        world.services.generation = world.services.generation.wrapping_add(1);
                        world.services.refreshed_at_ms = now_ms;
                        Some(world.services.generation)
                    } else {
                        None
                    };
                    let git = if exit_code == 0
                        && action.as_ref().is_some_and(is_git_action)
                        && (world.git.cwd != cwd
                            || action == Some(Action::GitMutation)
                            || now_ms.saturating_sub(world.git.refreshed_at_ms) > 120_000)
                    {
                        world.git.generation = world.git.generation.wrapping_add(1);
                        world.git.refreshed_at_ms = now_ms;
                        world.git.cwd = cwd.clone();
                        world.git.refs.clear();
                        Some((world.git.generation, cwd.clone()))
                    } else {
                        None
                    };
                    let hosts = if now_ms.saturating_sub(world.hosts.refreshed_at_ms) > 300_000 {
                        world.hosts.generation = world.hosts.generation.wrapping_add(1);
                        world.hosts.refreshed_at_ms = now_ms;
                        world.hosts.hosts.clear();
                        Some(world.hosts.generation)
                    } else {
                        None
                    };
                    let files = if world.files.cwd != cwd
                        || now_ms.saturating_sub(world.files.refreshed_at_ms) > 120_000
                        || action.as_ref().is_some_and(action_produces_artifact)
                    {
                        world.files.generation = world.files.generation.wrapping_add(1);
                        world.files.refreshed_at_ms = now_ms;
                        world.files.cwd = cwd.clone();
                        world.files.entries.clear();
                        Some((world.files.generation, cwd.clone()))
                    } else {
                        None
                    };
                    let artifact = if exit_code == 0
                        && action.as_ref().is_some_and(action_produces_artifact)
                    {
                        world.artifacts.generation = world.artifacts.generation.wrapping_add(1);
                        world.artifacts.refreshed_at_ms = now_ms;
                        world.artifacts.artifacts.clear();
                        action
                            .clone()
                            .map(|action| (world.artifacts.generation, action, cwd.clone(), now_ms))
                    } else {
                        None
                    };
                    (
                        docker,
                        process,
                        service,
                        git,
                        hosts,
                        files,
                        artifact,
                        learned_history,
                        learned_transition,
                    )
                };
                if let (Some(path), Some(event)) = (&self.history_path, learned_history) {
                    let path = path.clone();
                    self.background.submit(move || {
                        if let Err(error) = history::record(&path, &event) {
                            debug(&format!("history persistence failed: {error}"));
                        }
                    });
                }
                if let (Some(path), Some(event)) = (&self.transition_path, learned_transition) {
                    let path = path.clone();
                    self.background.submit(move || {
                        if let Err(error) = history::record_transition(&path, &event) {
                            debug(&format!("transition persistence failed: {error}"));
                        }
                    });
                }
                if let Some((elevated, generation)) = docker_refresh {
                    self.refresh_docker(elevated, generation);
                }
                if let Some(generation) = process_refresh {
                    self.refresh_processes(generation);
                }
                if let Some(generation) = service_refresh {
                    self.refresh_services(generation);
                }
                if let Some((generation, cwd)) = git_refresh {
                    self.refresh_git(generation, cwd);
                }
                if let Some(generation) = host_refresh {
                    self.refresh_hosts(generation);
                }
                if let Some((generation, cwd)) = file_refresh {
                    self.refresh_files(generation, cwd);
                }
                if let Some((generation, action, cwd, observed_at_ms)) = artifact_refresh {
                    self.refresh_artifacts(generation, action, cwd, observed_at_ms);
                }
                Response::Ack
            }
            Request::Ping => Response::Pong,
            Request::Quit => {
                let _ = self.background.wait_for_idle(Duration::from_millis(150));
                Response::Ack
            }
        }
    }

    fn refresh_docker(&self, elevated: bool, generation: u64) {
        let world = Arc::clone(&self.world);
        let command_paths = self.command_paths.clone();
        self.background.submit(move || {
            let Some(output) = query_docker(&command_paths, elevated, Duration::from_millis(250))
            else {
                debug("Docker refresh failed or timed out");
                return;
            };
            let containers = parse_docker_rows(&output);
            let now_ms = wall_time_ms();
            debug(&format!(
                "Docker refresh produced {} containers at {now_ms}",
                containers.len()
            ));
            if let Ok(mut world) = world.write() {
                if world.docker.generation != generation {
                    return;
                }
                world.docker.refreshed_at_ms = now_ms;
                world.docker.containers = containers;
            }
        });
    }

    fn prewarm_local_entities(&self) {
        let cwd = std::env::current_dir()
            .ok()
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_default();
        let shell_path = std::env::var_os("PATH").unwrap_or_default();
        self.refresh_hosts(0);
        let attempted_at_ms = wall_time_ms();
        if let Ok(mut world) = self.world.write() {
            world.git.cwd = cwd.clone();
            world.current_cwd = cwd.clone();
            world.files.cwd = cwd.clone();
            world.hosts.refreshed_at_ms = attempted_at_ms;
            world.files.refreshed_at_ms = attempted_at_ms;
        }
        self.refresh_commands(0, shell_path, cwd.clone());
        self.refresh_files(0, cwd);
    }

    fn refresh_commands(&self, generation: u64, path: OsString, cwd: String) {
        let world = Arc::clone(&self.world);
        self.background.submit(move || {
            let commands = scan_path_commands(&path, &cwd);
            let refreshed_at_ms = wall_time_ms();
            if let Ok(mut world) = world.write() {
                if world.commands.generation != generation {
                    return;
                }
                world.commands.generation = world.commands.generation.wrapping_add(1);
                world.commands.commands = commands;
                world.commands.refreshed_at_ms = refreshed_at_ms;
            }
        });
    }

    fn refresh_files(&self, generation: u64, cwd: String) {
        let world = Arc::clone(&self.world);
        self.background.submit(move || {
            let mut entries = scan_file_entries(&cwd);
            entries.sort_by(|left, right| left.name.cmp(&right.name));
            entries.dedup_by(|left, right| left.name == right.name);
            let refreshed_at_ms = wall_time_ms();
            if let Ok(mut world) = world.write() {
                if world.files.generation != generation || world.files.cwd != cwd {
                    return;
                }
                world.files.entries = entries;
                world.files.refreshed_at_ms = refreshed_at_ms;
            }
        });
    }

    fn refresh_artifacts(&self, generation: u64, action: Action, cwd: String, observed_at_ms: u64) {
        let world = Arc::clone(&self.world);
        self.background.submit(move || {
            let artifacts = verify_artifacts(&action, &cwd, observed_at_ms);
            let refreshed_at_ms = wall_time_ms();
            if let Ok(mut world) = world.write() {
                if world.artifacts.generation != generation {
                    return;
                }
                world.artifacts.artifacts = artifacts;
                world.artifacts.refreshed_at_ms = refreshed_at_ms;
            }
        });
    }

    fn refresh_processes(&self, generation: u64) {
        let Some(program) = self.command_paths.resolve("ps") else {
            return;
        };
        let world = Arc::clone(&self.world);
        self.background.submit(move || {
            let Some(output) = query_command(
                &program,
                &["-eo", "pid=,comm="],
                None,
                Duration::from_millis(250),
            ) else {
                return;
            };
            let processes = parse_process_rows(&output);
            if let Ok(mut world) = world.write() {
                if world.processes.generation != generation {
                    return;
                }
                world.processes.refreshed_at_ms = wall_time_ms();
                world.processes.processes = processes;
            }
        });
    }

    fn refresh_hosts(&self, generation: u64) {
        let world = Arc::clone(&self.world);
        self.background.submit(move || {
            let hosts = scan_ssh_hosts();
            if let Ok(mut world) = world.write() {
                if world.hosts.generation != generation {
                    return;
                }
                world.hosts.refreshed_at_ms = wall_time_ms();
                world.hosts.hosts = hosts;
            }
        });
    }

    fn refresh_services(&self, generation: u64) {
        let Some(program) = self.command_paths.resolve("systemctl") else {
            return;
        };
        let world = Arc::clone(&self.world);
        self.background.submit(move || {
            let Some(output) = query_command(
                &program,
                &[
                    "list-units",
                    "--type=service",
                    "--all",
                    "--no-legend",
                    "--plain",
                ],
                None,
                Duration::from_millis(300),
            ) else {
                return;
            };
            let services = parse_service_rows(&output);
            if let Ok(mut world) = world.write() {
                if world.services.generation != generation {
                    return;
                }
                world.services.refreshed_at_ms = wall_time_ms();
                world.services.services = services;
            }
        });
    }

    fn refresh_git(&self, generation: u64, cwd: String) {
        let Some(program) = self.command_paths.resolve("git") else {
            return;
        };
        let world = Arc::clone(&self.world);
        self.background.submit(move || {
            let Some(output) = query_command(
                &program,
                &[
                    "for-each-ref",
                    "--format=%(refname:short)",
                    "refs/heads",
                    "refs/remotes",
                ],
                Some(&cwd),
                Duration::from_millis(300),
            ) else {
                return;
            };
            let refs = parse_git_refs(&output);
            if let Ok(mut world) = world.write() {
                if world.git.generation != generation || world.git.cwd != cwd {
                    return;
                }
                world.git.refreshed_at_ms = wall_time_ms();
                world.git.refs = refs;
            }
        });
    }
}

pub fn serve_stdio() -> io::Result<()> {
    serve_stdio_with_runtime(Runtime::default())
}

pub fn serve_stdio_with_fixture_bin(path: PathBuf) -> io::Result<()> {
    serve_stdio_with_runtime(Runtime::new(CommandPaths::fixtures(path)))
}

fn serve_stdio_with_runtime(runtime: Runtime) -> io::Result<()> {
    // Build the immutable grammar indexes immediately after the helper starts,
    // before a user keystroke can enter the request pipe. Query handling then
    // stays inside the native frontend's strict per-keystroke deadline.
    crate::specs::warm();
    let stdin = io::stdin();
    let mut stdin = stdin.lock();
    let mut stdout = io::stdout().lock();
    loop {
        let request = match read_protocol_line(&mut stdin)? {
            ProtocolLine::Eof => break,
            ProtocolLine::Invalid => Err(crate::protocol::ProtocolError),
            ProtocolLine::Valid(line) => decode_request(&line),
        };
        let quit = matches!(request, Ok(Request::Quit));
        let response = request.map_or(Response::Error, |request| runtime.handle(request));
        stdout.write_all(encode_response(&response).as_bytes())?;
        stdout.flush()?;
        if quit {
            break;
        }
    }
    // EOF is the normal shell-exit path. Give already accepted persistence
    // work the same bounded opportunity to finish as an explicit Quit.
    let _ = runtime.background.wait_for_idle(Duration::from_millis(150));
    Ok(())
}

/// Reads one wire line without ever allocating in proportion to hostile input.
/// Invalid input is fully drained so the following request remains aligned.
#[derive(Debug, PartialEq, Eq)]
enum ProtocolLine {
    Eof,
    Valid(String),
    Invalid,
}

fn read_protocol_line<R: BufRead>(reader: &mut R) -> io::Result<ProtocolLine> {
    let mut line = Vec::with_capacity(256);
    let mut oversized = false;
    loop {
        let (consumed, ended) = {
            let available = reader.fill_buf()?;
            if available.is_empty() {
                if line.is_empty() && !oversized {
                    return Ok(ProtocolLine::Eof);
                }
                return Ok(if oversized {
                    ProtocolLine::Invalid
                } else {
                    String::from_utf8(line)
                        .map(ProtocolLine::Valid)
                        .unwrap_or(ProtocolLine::Invalid)
                });
            }
            let ended_at = available.iter().position(|byte| *byte == b'\n');
            let consumed = ended_at.map_or(available.len(), |index| index + 1);
            if !oversized {
                if line.len().saturating_add(consumed) <= MAX_PROTOCOL_LINE_BYTES {
                    line.extend_from_slice(&available[..consumed]);
                } else {
                    line.clear();
                    oversized = true;
                }
            }
            (consumed, ended_at.is_some())
        };
        reader.consume(consumed);
        if ended {
            return Ok(if oversized {
                ProtocolLine::Invalid
            } else {
                String::from_utf8(line)
                    .map(ProtocolLine::Valid)
                    .unwrap_or(ProtocolLine::Invalid)
            });
        }
    }
}

fn query_docker(command_paths: &CommandPaths, elevated: bool, timeout: Duration) -> Option<String> {
    let docker = command_paths.resolve("docker")?;
    let mut command = if elevated {
        let sudo = command_paths.resolve("sudo")?;
        let mut command = Command::new(sudo);
        command.arg("-n").arg(docker);
        command
    } else {
        Command::new(docker)
    };
    command.args([
        "container",
        "ls",
        "--format",
        "{{.ID}}\\t{{.Names}}\\t{{.Image}}",
    ]);
    run_command(command, timeout)
}

fn query_command(
    program: &Path,
    args: &[&str],
    cwd: Option<&str>,
    timeout: Duration,
) -> Option<String> {
    let mut command = Command::new(program);
    command.args(args);
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    run_command(command, timeout)
}

fn run_command(mut command: Command, timeout: Duration) -> Option<String> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let stdout = child.stdout.take()?;
    let reader = match thread::Builder::new()
        .name("anduinos-output-reader".into())
        .stack_size(128 * 1024)
        .spawn(move || {
            let mut output = Vec::new();
            stdout
                .take(MAX_COMMAND_OUTPUT_BYTES + 1)
                .read_to_end(&mut output)
                .map(|_| output)
        }) {
        Ok(reader) => reader,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
    };
    let started = Instant::now();
    let successful = loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                break status.success();
            }
            Ok(None) => {}
            Err(_) => break false,
        }
        if started.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            break false;
        }
        thread::sleep(Duration::from_millis(5));
    };
    let output = reader.join().ok()?.ok()?;
    if !successful || output.len() as u64 > MAX_COMMAND_OUTPUT_BYTES {
        return None;
    }
    String::from_utf8(output).ok()
}

fn parse_docker_rows(output: &str) -> Vec<Container> {
    output
        .lines()
        .enumerate()
        .filter_map(|(rank, row)| {
            let mut fields = row.splitn(3, '\t');
            let id = fields.next()?.trim();
            let name = fields.next()?.trim();
            let image = fields.next()?.trim();
            if id.is_empty() || name.is_empty() {
                return None;
            }
            Some(Container {
                id: id.to_owned(),
                name: name.to_owned(),
                image: image.to_owned(),
                running: true,
                listing_rank: rank as u32,
            })
        })
        .take(MAX_DOCKER_ENTITIES)
        .collect()
}

fn parse_process_rows(output: &str) -> Vec<Process> {
    output
        .lines()
        .filter_map(|row| {
            let mut fields = row.split_whitespace();
            Some(Process {
                pid: fields.next()?.parse().ok()?,
                command: fields.next()?.to_owned(),
            })
        })
        .take(MAX_PROCESS_ENTITIES)
        .collect()
}

fn parse_service_rows(output: &str) -> Vec<Service> {
    output
        .lines()
        .filter_map(|row| {
            let name = row.split_whitespace().next()?;
            name.ends_with(".service").then(|| Service {
                name: name.to_owned(),
            })
        })
        .take(MAX_SERVICE_ENTITIES)
        .collect()
}

fn parse_git_refs(output: &str) -> Vec<GitRef> {
    let mut names: Vec<String> = output
        .lines()
        .map(str::trim)
        .filter(|name| !name.is_empty() && !name.ends_with("/HEAD"))
        .map(str::to_owned)
        .take(MAX_GIT_REFS)
        .collect();
    names.sort();
    names.dedup();
    names.into_iter().map(|name| GitRef { name }).collect()
}

fn scan_path_commands(path: &OsStr, cwd: &str) -> Vec<String> {
    scan_path_commands_with_limits(path, cwd, PATH_SCAN_LIMITS)
}

fn scan_path_commands_with_limits(path: &OsStr, cwd: &str, limits: PathScanLimits) -> Vec<String> {
    let mut commands = BTreeSet::new();
    let mut scanned_entries = 0;
    let started = Instant::now();
    for directory in std::env::split_paths(path).take(limits.directories) {
        if started.elapsed() >= limits.duration || scanned_entries >= limits.entries {
            break;
        }
        let directory = if directory.is_absolute() {
            directory
        } else {
            Path::new(cwd).join(directory)
        };
        let Ok(entries) = fs::read_dir(directory) else {
            continue;
        };
        for entry in entries.filter_map(Result::ok) {
            scanned_entries += 1;
            if scanned_entries > limits.entries
                || commands.len() >= limits.commands
                || started.elapsed() >= limits.duration
            {
                break;
            }
            let Ok(name) = entry.file_name().into_string() else {
                continue;
            };
            if !safe_command_name(&name) {
                continue;
            }
            if executable_for_current_user(&entry.path()) {
                commands.insert(name);
            }
        }
    }
    commands.into_iter().collect()
}

fn executable_for_current_user(path: &Path) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    let Ok(path) = CString::new(path.as_os_str().as_bytes()) else {
        return false;
    };
    // SAFETY: `path` is a live NUL-terminated CString and access does not retain
    // the pointer. X_OK evaluates the real user's permissions, ACLs and mount.
    unsafe { access(path.as_ptr(), 1) == 0 }
}

unsafe extern "C" {
    fn access(pathname: *const std::ffi::c_char, mode: std::ffi::c_int) -> std::ffi::c_int;
}

fn safe_command_name(name: &str) -> bool {
    let mut bytes = name.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        && bytes.all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'+' | b'@' | b'-')
        })
}

fn scan_file_entries(cwd: &str) -> Vec<FileEntry> {
    let mut entries = Vec::new();
    let started = Instant::now();
    let mut budget = 1_024;
    scan_directory(Path::new(cwd), "", 3, &mut budget, &started, &mut entries);

    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        let mut budget = 256;
        scan_directory(&home, "~", 1, &mut budget, &started, &mut entries);
        for relative in [".ssh", ".config", ".local/bin"] {
            let mut budget = 256;
            let base = home.join(relative);
            let display = format!("~/{relative}");
            scan_directory(&base, &display, 2, &mut budget, &started, &mut entries);
        }
    }
    let mut budget = 256;
    scan_directory(Path::new("/"), "/", 1, &mut budget, &started, &mut entries);
    for absolute in ["/dev", "/tmp", "/var/tmp"] {
        let mut budget = 512;
        scan_directory(
            Path::new(absolute),
            absolute,
            2,
            &mut budget,
            &started,
            &mut entries,
        );
    }
    entries
}

fn scan_ssh_hosts() -> Vec<Host> {
    let Some(ssh) = std::env::var_os("HOME")
        .map(PathBuf::from)
        .map(|home| home.join(".ssh"))
    else {
        return Vec::new();
    };
    let mut names = Vec::new();
    if let Some(config) = read_limited_text(&ssh.join("config"), MAX_CONTEXT_FILE_BYTES) {
        names.extend(parse_ssh_config_hosts(&config));
    }
    if let Some(known_hosts) = read_limited_text(&ssh.join("known_hosts"), MAX_CONTEXT_FILE_BYTES) {
        names.extend(parse_known_hosts(&known_hosts));
    }
    names.sort();
    names.dedup();
    names.truncate(512);
    names.into_iter().map(|name| Host { name }).collect()
}

fn parse_ssh_config_hosts(contents: &str) -> Vec<String> {
    contents
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            let mut fields = line.split_whitespace();
            let directive = fields.next()?;
            directive.eq_ignore_ascii_case("host").then_some(fields)
        })
        .flatten()
        .filter(|name| valid_host(name) && !name.contains(['*', '?', '!']))
        .map(str::to_owned)
        .collect()
}

fn parse_known_hosts(contents: &str) -> Vec<String> {
    let mut hosts = Vec::new();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split_whitespace();
        let first = fields.next().unwrap_or_default();
        let encoded_hosts = if first.starts_with('@') {
            fields.next().unwrap_or_default()
        } else {
            first
        };
        if encoded_hosts.starts_with('|') {
            continue;
        }
        for encoded in encoded_hosts.split(',') {
            let name = if let Some(bracketed) = encoded.strip_prefix('[') {
                bracketed
                    .split_once("]:")
                    .map(|(host, _)| host)
                    .unwrap_or(bracketed)
            } else {
                encoded
            };
            if valid_host(name) {
                hosts.push(name.to_owned());
            }
        }
    }
    hosts
}

fn valid_host(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 253
        && name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
}

fn scan_directory(
    base: &Path,
    display: &str,
    depth: usize,
    budget: &mut usize,
    started: &Instant,
    out: &mut Vec<FileEntry>,
) {
    if depth == 0 || *budget == 0 || started.elapsed() >= FILE_SCAN_BUDGET {
        return;
    }
    let Ok(read_dir) = fs::read_dir(base) else {
        return;
    };
    let mut children: Vec<_> = read_dir.filter_map(Result::ok).take(512).collect();
    children.sort_by_key(|entry| entry.file_name());
    for entry in children {
        if *budget == 0 || started.elapsed() >= FILE_SCAN_BUDGET {
            break;
        }
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        // `DirEntry::file_type` does not follow symlinks. Recursion therefore
        // cannot escape through a symlink cycle.
        let directory = file_type.is_dir();
        let visible = if display.is_empty() {
            name.clone()
        } else if display == "/" {
            format!("/{name}")
        } else {
            format!("{display}/{name}")
        };
        out.push(FileEntry {
            name: visible.clone(),
            directory,
        });
        *budget -= 1;
        if directory {
            scan_directory(&entry.path(), &visible, depth - 1, budget, started, out);
        }
    }
}

fn read_limited_text(path: &Path, limit: u64) -> Option<String> {
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)
        .open(path)
        .ok()?;
    if !file.metadata().ok()?.is_file() {
        return None;
    }
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > limit {
        return None;
    }
    String::from_utf8(bytes).ok()
}

fn action_produces_artifact(action: &Action) -> bool {
    matches!(
        action,
        Action::SshKeygen { .. }
            | Action::MakeDirectory { .. }
            | Action::GitClone { .. }
            | Action::PythonVenv { .. }
    )
}

fn is_git_action(action: &Action) -> bool {
    matches!(
        action,
        Action::GitMutation | Action::GitStage | Action::GitCommit
    )
}

fn verify_artifacts(action: &Action, cwd: &str, observed_at_ms: u64) -> Vec<Artifact> {
    match action {
        Action::SshKeygen {
            private_key: Some(private_key),
        } => {
            let display = format!("{private_key}.pub");
            let path = resolve_shell_path(&display, cwd);
            path.is_file()
                .then_some(Artifact {
                    path: display,
                    kind: ArtifactKind::PublicKey,
                })
                .into_iter()
                .collect()
        }
        Action::SshKeygen { private_key: None } => newest_public_key(observed_at_ms)
            .into_iter()
            .map(|path| Artifact {
                path,
                kind: ArtifactKind::PublicKey,
            })
            .collect(),
        Action::MakeDirectory { paths } => paths
            .iter()
            .filter(|display| resolve_shell_path(display, cwd).is_dir())
            .map(|display| Artifact {
                path: display.clone(),
                kind: ArtifactKind::Directory,
            })
            .collect(),
        Action::GitClone { destination } => resolve_shell_path(destination, cwd)
            .is_dir()
            .then_some(Artifact {
                path: destination.clone(),
                kind: ArtifactKind::Directory,
            })
            .into_iter()
            .collect(),
        Action::PythonVenv { path } => {
            let display = format!("{}/bin/activate", path.trim_end_matches('/'));
            resolve_shell_path(&display, cwd)
                .is_file()
                .then_some(Artifact {
                    path: display,
                    kind: ArtifactKind::ActivationScript,
                })
                .into_iter()
                .collect()
        }
        _ => Vec::new(),
    }
}

fn resolve_shell_path(display: &str, cwd: &str) -> PathBuf {
    if let Some(rest) = display.strip_prefix("~/") {
        return std::env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join(rest);
    }
    let path = Path::new(display);
    if path.is_absolute() {
        path.to_owned()
    } else {
        Path::new(cwd).join(path)
    }
}

fn newest_public_key(observed_at_ms: u64) -> Option<String> {
    let ssh = std::env::var_os("HOME").map(PathBuf::from)?.join(".ssh");
    let mut keys: Vec<(u64, String)> = fs::read_dir(ssh)
        .ok()?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            if !name.ends_with(".pub") || !entry.file_type().ok()?.is_file() {
                return None;
            }
            let modified = entry
                .metadata()
                .ok()?
                .modified()
                .ok()?
                .duration_since(std::time::UNIX_EPOCH)
                .ok()?
                .as_millis() as u64;
            (modified.saturating_add(120_000) >= observed_at_ms)
                .then(|| (modified, format!("~/.ssh/{name}")))
        })
        .collect();
    keys.sort_by_key(|(modified, _)| std::cmp::Reverse(*modified));
    keys.into_iter().next().map(|(_, path)| path)
}

fn wall_time_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn debug(message: &str) {
    if std::env::var_os("ANDUINOS_QUIET_DEBUG").is_some() {
        eprintln!("anduinos-quietd: {message}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::Response;
    use std::io::Cursor;

    #[test]
    fn protocol_reader_bounds_memory_and_recovers_at_the_next_line() {
        let mut input = vec![b'x'; MAX_PROTOCOL_LINE_BYTES + 1];
        input.extend_from_slice(b"\nP\n");
        let mut reader = Cursor::new(input);
        assert_eq!(
            read_protocol_line(&mut reader).unwrap(),
            ProtocolLine::Invalid
        );
        assert_eq!(
            read_protocol_line(&mut reader).unwrap(),
            ProtocolLine::Valid("P\n".into())
        );
        assert_eq!(read_protocol_line(&mut reader).unwrap(), ProtocolLine::Eof);
    }

    #[test]
    fn protocol_reader_rejects_non_utf8_without_losing_framing() {
        let mut reader = Cursor::new(b"\xff\nX\n".to_vec());
        assert_eq!(
            read_protocol_line(&mut reader).unwrap(),
            ProtocolLine::Invalid
        );
        assert_eq!(
            read_protocol_line(&mut reader).unwrap(),
            ProtocolLine::Valid("X\n".into())
        );
    }

    #[test]
    fn query_is_pure_and_uses_the_supplied_snapshot() {
        let mut world = WorldState::default();
        world.docker.generation = 1;
        world.docker.refreshed_at_ms = 900;
        world.docker.containers.push(Container {
            id: "123456789abc".into(),
            name: "mysql_db".into(),
            image: "mysql:8".into(),
            running: true,
            listing_rank: 0,
        });
        world.observe_command("docker ps | grep mysql", 0, 950);
        let runtime = Runtime::with_world(world);
        let response = runtime.handle(Request::Query {
            now_ms: 1_000,
            line: "docker exec -it ".into(),
        });
        assert!(matches!(
            response,
            Response::Suggestion { insertion, .. } if insertion == "mysql_db"
        ));
    }

    #[test]
    fn disabled_history_keeps_context_but_never_learns_commands() {
        let mut runtime = Runtime::with_world(WorldState::default());
        runtime.history_enabled = false;
        assert_eq!(
            runtime.handle(Request::Observe {
                exit_code: 0,
                now_ms: 1_000,
                line: "personal-tool private-action".into(),
                cwd: "/repo".into(),
            }),
            Response::Ack
        );
        let world = runtime.world.read().unwrap();
        assert!(world.history.is_empty());
        assert!(world.transitions.is_empty());
        assert_eq!(world.current_cwd, "/repo");
        assert!(world.last_event.is_some());
    }

    #[test]
    fn parser_rejects_malformed_docker_rows() {
        let rows = parse_docker_rows("abc\tgood\timage\nmissing-fields\n\tbad\timage\n");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].name, "good");
    }

    #[test]
    fn external_entity_parsers_have_independent_object_caps() {
        let docker = (0..MAX_DOCKER_ENTITIES + 10)
            .map(|index| format!("{index:x}\tcontainer-{index}\timage\n"))
            .collect::<String>();
        let processes = (0..MAX_PROCESS_ENTITIES + 10)
            .map(|index| format!("{} command\n", index + 1))
            .collect::<String>();
        let services = (0..MAX_SERVICE_ENTITIES + 10)
            .map(|index| format!("service-{index}.service loaded active\n"))
            .collect::<String>();
        let refs = (0..MAX_GIT_REFS + 10)
            .map(|index| format!("refs/{index}\n"))
            .collect::<String>();
        assert_eq!(parse_docker_rows(&docker).len(), MAX_DOCKER_ENTITIES);
        assert_eq!(parse_process_rows(&processes).len(), MAX_PROCESS_ENTITIES);
        assert_eq!(parse_service_rows(&services).len(), MAX_SERVICE_ENTITIES);
        assert_eq!(parse_git_refs(&refs).len(), MAX_GIT_REFS);
    }

    #[test]
    fn artifact_observer_only_publishes_files_that_exist() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-quiet-artifacts-{}-{}",
            std::process::id(),
            wall_time_ms()
        ));
        fs::create_dir_all(root.join(".venv/bin")).unwrap();
        fs::write(root.join(".venv/bin/activate"), "# activate\n").unwrap();
        fs::create_dir(root.join("created")).unwrap();
        fs::create_dir_all(root.join("src/components")).unwrap();
        fs::write(root.join("src/components/button.rs"), "fn button() {}\n").unwrap();

        let cwd = root.to_string_lossy();
        assert_eq!(
            verify_artifacts(
                &Action::PythonVenv {
                    path: ".venv".into()
                },
                &cwd,
                0
            ),
            vec![Artifact {
                path: ".venv/bin/activate".into(),
                kind: ArtifactKind::ActivationScript
            }]
        );
        assert_eq!(
            verify_artifacts(
                &Action::MakeDirectory {
                    paths: vec!["missing".into(), "created".into()]
                },
                &cwd,
                0
            ),
            vec![Artifact {
                path: "created".into(),
                kind: ArtifactKind::Directory
            }]
        );
        let scanned = scan_file_entries(&cwd);
        assert!(scanned
            .iter()
            .any(|entry| entry.name == "src/components/button.rs" && !entry.directory));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn ssh_host_parser_keeps_aliases_and_skips_patterns_and_hashes() {
        assert_eq!(
            parse_ssh_config_hosts(
                "Host prod web-01\n  HostName 10.0.0.1\nHost *.internal !blocked\n"
            ),
            vec!["prod", "web-01"]
        );
        assert_eq!(
            parse_known_hosts(
                "example.com,10.0.0.2 ssh-ed25519 AAAA\n[git.example.com]:2222 ssh-rsa AAAA\n|1|hash|hash ssh-ed25519 AAAA\n"
            ),
            vec!["example.com", "10.0.0.2", "git.example.com"]
        );
    }

    #[test]
    fn context_reader_rejects_special_files() {
        assert!(read_limited_text(Path::new("/dev/zero"), 1_024).is_none());
    }

    #[test]
    fn path_scanner_keeps_only_safe_executable_files_and_resolves_relative_entries() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-quiet-commands-{}-{}",
            std::process::id(),
            wall_time_ms()
        ));
        let bin = root.join("bin");
        fs::create_dir_all(&bin).unwrap();
        for (name, mode) in [
            ("dstat", 0o755),
            ("not-executable", 0o644),
            ("bad name", 0o755),
        ] {
            let path = bin.join(name);
            fs::write(&path, "#!/bin/sh\n").unwrap();
            let mut permissions = fs::metadata(&path).unwrap().permissions();
            permissions.set_mode(mode);
            fs::set_permissions(path, permissions).unwrap();
        }

        assert_eq!(
            scan_path_commands(OsStr::new("bin"), root.to_str().unwrap()),
            vec!["dstat"]
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn path_scanner_obeys_directory_entry_and_command_budgets() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-quiet-command-limits-{}-{}",
            std::process::id(),
            wall_time_ms()
        ));
        let mut directories = Vec::new();
        for directory_index in 0..4 {
            let directory = root.join(format!("bin-{directory_index}"));
            fs::create_dir_all(&directory).unwrap();
            for command_index in 0..4 {
                let path = directory.join(format!("command-{directory_index}-{command_index}"));
                fs::write(&path, "#!/bin/sh\n").unwrap();
                let mut permissions = fs::metadata(&path).unwrap().permissions();
                permissions.set_mode(0o755);
                fs::set_permissions(path, permissions).unwrap();
            }
            directories.push(directory);
        }
        let path = std::env::join_paths(&directories).unwrap();
        let commands = scan_path_commands_with_limits(
            &path,
            root.to_str().unwrap(),
            PathScanLimits {
                directories: 2,
                entries: 5,
                commands: 3,
                duration: Duration::from_secs(1),
            },
        );
        assert_eq!(commands.len(), 3);
        assert!(commands
            .iter()
            .all(|command| !command.starts_with("command-2-")));
        fs::remove_dir_all(root).unwrap();
    }
}
