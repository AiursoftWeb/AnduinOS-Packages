use crate::history;
use crate::protocol::{decode_request, encode_response, Request, Response};
use crate::{
    evaluate, Action, Artifact, ArtifactKind, Container, FileEntry, GitRef, Host, Process, Query,
    Service, WorldState,
};
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, RwLock};
use std::thread;
use std::time::{Duration, Instant};

pub struct Runtime {
    world: Arc<RwLock<WorldState>>,
    history_path: Option<PathBuf>,
    transition_path: Option<PathBuf>,
}

impl Default for Runtime {
    fn default() -> Self {
        let history_path = history::state_path();
        let transition_path = history::transition_state_path();
        let mut world = WorldState::default();
        if let Some(path) = history_path.as_deref() {
            world.history = history::load(path);
        }
        world.merge_history(history::load_bash_history());
        if let Some(path) = transition_path.as_deref() {
            world.transitions = history::load_transitions(path);
        }
        world.merge_transitions(history::load_bash_transitions());
        let runtime = Self {
            world: Arc::new(RwLock::new(world)),
            history_path,
            transition_path,
        };
        runtime.prewarm_docker();
        runtime.prewarm_local_entities();
        runtime
    }
}

impl Runtime {
    pub fn with_world(world: WorldState) -> Self {
        Self {
            world: Arc::new(RwLock::new(world)),
            history_path: None,
            transition_path: None,
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
                    history_snapshot,
                    transition_snapshot,
                ) = {
                    let Ok(mut world) = self.world.write() else {
                        return Response::Error;
                    };
                    let learned = world.observe_command_with_cwd(&line, exit_code, now_ms, &cwd);
                    let (learned_history, learned_transition) = match learned {
                        Some((history, transition)) => (Some(history), transition),
                        None => (None, None),
                    };
                    let action = world.last_event.as_ref().map(|event| event.action.clone());
                    if matches!(action, Some(Action::DockerList { .. })) {
                        // Invalidate before refreshing. Queries during refresh
                        // must be silent instead of seeing the old generation.
                        world.docker.generation = world.docker.generation.wrapping_add(1);
                        world.docker.refreshed_at_ms = 0;
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
                        world.processes.refreshed_at_ms = 0;
                        Some(world.processes.generation)
                    } else {
                        None
                    };
                    let service = if action == Some(Action::ServiceList) && exit_code == 0 {
                        world.services.generation = world.services.generation.wrapping_add(1);
                        world.services.refreshed_at_ms = 0;
                        Some(world.services.generation)
                    } else {
                        None
                    };
                    let git = if exit_code == 0
                        && (world.git.cwd != cwd
                            || action == Some(Action::GitMutation)
                            || now_ms.saturating_sub(world.git.refreshed_at_ms) > 120_000)
                    {
                        world.git.generation = world.git.generation.wrapping_add(1);
                        world.git.refreshed_at_ms = 0;
                        world.git.cwd = cwd.clone();
                        world.git.refs.clear();
                        Some((world.git.generation, cwd.clone()))
                    } else {
                        None
                    };
                    let hosts = if now_ms.saturating_sub(world.hosts.refreshed_at_ms) > 300_000 {
                        world.hosts.generation = world.hosts.generation.wrapping_add(1);
                        world.hosts.refreshed_at_ms = 0;
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
                        world.files.refreshed_at_ms = 0;
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
                        world.artifacts.refreshed_at_ms = 0;
                        world.artifacts.artifacts.clear();
                        action
                            .clone()
                            .map(|action| (world.artifacts.generation, action, cwd.clone(), now_ms))
                    } else {
                        None
                    };
                    let history_snapshot = learned_history.as_ref().map(|_| world.history.clone());
                    let transition_snapshot = learned_transition
                        .as_ref()
                        .map(|_| world.transitions.clone());
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
                        history_snapshot,
                        transition_snapshot,
                    )
                };
                if let (Some(path), Some(event), Some(snapshot)) = (
                    &self.history_path,
                    learned_history.as_ref(),
                    history_snapshot.as_deref(),
                ) {
                    if let Err(error) = history::record(path, event, snapshot) {
                        debug(&format!("history persistence failed: {error}"));
                    }
                }
                if let (Some(path), Some(event), Some(snapshot)) = (
                    &self.transition_path,
                    learned_transition.as_ref(),
                    transition_snapshot.as_deref(),
                ) {
                    if let Err(error) = history::record_transition(path, event, snapshot) {
                        debug(&format!("transition persistence failed: {error}"));
                    }
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
            Request::Quit => Response::Ack,
        }
    }

    fn refresh_docker(&self, elevated: bool, generation: u64) {
        let world = Arc::clone(&self.world);
        thread::spawn(move || {
            let Some(output) = query_docker(elevated, Duration::from_millis(250)) else {
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

    fn prewarm_docker(&self) {
        let world = Arc::clone(&self.world);
        let generation = world
            .read()
            .map(|world| world.docker.generation)
            .unwrap_or_default();
        thread::spawn(move || {
            let output = query_docker(false, Duration::from_millis(250))
                .or_else(|| query_docker(true, Duration::from_millis(250)));
            let Some(output) = output else {
                return;
            };
            let containers = parse_docker_rows(&output);
            let now_ms = wall_time_ms();
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
        self.refresh_processes(0);
        self.refresh_services(0);
        self.refresh_hosts(0);
        if let Ok(mut world) = self.world.write() {
            world.git.cwd = cwd.clone();
            world.current_cwd = cwd.clone();
            world.files.cwd = cwd.clone();
        }
        self.refresh_git(0, cwd.clone());
        self.refresh_files(0, cwd);
    }

    fn refresh_files(&self, generation: u64, cwd: String) {
        let world = Arc::clone(&self.world);
        thread::spawn(move || {
            let mut entries = scan_file_entries(&cwd);
            entries.sort_by(|left, right| left.name.cmp(&right.name));
            entries.dedup_by(|left, right| left.name == right.name);
            let refreshed_at_ms = wall_time_ms();
            if let Ok(mut world) = world.write() {
                if world.files.generation != generation || world.files.cwd != cwd {
                    return None;
                }
                world.files.entries = entries;
                world.files.refreshed_at_ms = refreshed_at_ms;
            }
            Some(())
        });
    }

    fn refresh_artifacts(&self, generation: u64, action: Action, cwd: String, observed_at_ms: u64) {
        let world = Arc::clone(&self.world);
        thread::spawn(move || {
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
        let world = Arc::clone(&self.world);
        thread::spawn(move || {
            let output = query_command(
                "ps",
                &["-eo", "pid=,comm="],
                None,
                Duration::from_millis(250),
            )?;
            let processes = parse_process_rows(&output);
            if let Ok(mut world) = world.write() {
                if world.processes.generation != generation {
                    return None;
                }
                world.processes.refreshed_at_ms = wall_time_ms();
                world.processes.processes = processes;
            }
            Some(())
        });
    }

    fn refresh_hosts(&self, generation: u64) {
        let world = Arc::clone(&self.world);
        thread::spawn(move || {
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
        let world = Arc::clone(&self.world);
        thread::spawn(move || {
            let output = query_command(
                "systemctl",
                &[
                    "list-units",
                    "--type=service",
                    "--all",
                    "--no-legend",
                    "--plain",
                ],
                None,
                Duration::from_millis(300),
            )?;
            let services = parse_service_rows(&output);
            if let Ok(mut world) = world.write() {
                if world.services.generation != generation {
                    return None;
                }
                world.services.refreshed_at_ms = wall_time_ms();
                world.services.services = services;
            }
            Some(())
        });
    }

    fn refresh_git(&self, generation: u64, cwd: String) {
        let world = Arc::clone(&self.world);
        thread::spawn(move || {
            let output = query_command(
                "git",
                &[
                    "for-each-ref",
                    "--format=%(refname:short)",
                    "refs/heads",
                    "refs/remotes",
                ],
                Some(&cwd),
                Duration::from_millis(300),
            )?;
            let refs = parse_git_refs(&output);
            if let Ok(mut world) = world.write() {
                if world.git.generation != generation || world.git.cwd != cwd {
                    return None;
                }
                world.git.refreshed_at_ms = wall_time_ms();
                world.git.refs = refs;
            }
            Some(())
        });
    }
}

pub fn serve_stdio() -> io::Result<()> {
    // Build the immutable grammar indexes immediately after the helper starts,
    // before a user keystroke can enter the request pipe. Query handling then
    // stays inside the native frontend's strict per-keystroke deadline.
    crate::specs::warm();
    let runtime = Runtime::default();
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    for request_line in stdin.lock().lines() {
        let request_line = request_line?;
        let request = decode_request(&request_line);
        let quit = matches!(request, Ok(Request::Quit));
        let response = request.map_or(Response::Error, |request| runtime.handle(request));
        stdout.write_all(encode_response(&response).as_bytes())?;
        stdout.flush()?;
        if quit {
            break;
        }
    }
    Ok(())
}

fn query_docker(elevated: bool, timeout: Duration) -> Option<String> {
    let mut command = if elevated {
        let mut command = Command::new("sudo");
        command.args(["-n", "docker"]);
        command
    } else {
        Command::new("docker")
    };
    let mut child = command
        .args([
            "container",
            "ls",
            "--format",
            "{{.ID}}\\t{{.Names}}\\t{{.Image}}",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    return None;
                }
                let mut output = Vec::new();
                child.stdout.take()?.read_to_end(&mut output).ok()?;
                return String::from_utf8(output).ok();
            }
            Ok(None) => {}
            Err(_) => return None,
        }
        if started.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn query_command(
    program: &str,
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
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    return None;
                }
                let mut output = Vec::new();
                child.stdout.take()?.read_to_end(&mut output).ok()?;
                return String::from_utf8(output).ok();
            }
            Ok(None) => {}
            Err(_) => return None,
        }
        if started.elapsed() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        thread::sleep(Duration::from_millis(5));
    }
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
        .collect()
}

fn parse_git_refs(output: &str) -> Vec<GitRef> {
    let mut names: Vec<String> = output
        .lines()
        .map(str::trim)
        .filter(|name| !name.is_empty() && !name.ends_with("/HEAD"))
        .map(str::to_owned)
        .collect();
    names.sort();
    names.dedup();
    names.into_iter().map(|name| GitRef { name }).collect()
}

fn scan_file_entries(cwd: &str) -> Vec<FileEntry> {
    let mut entries = Vec::new();
    let mut budget = 1_024;
    scan_directory(Path::new(cwd), "", 3, &mut budget, &mut entries);

    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        let mut budget = 256;
        scan_directory(&home, "~", 1, &mut budget, &mut entries);
        for relative in [".ssh", ".config", ".local/bin"] {
            let mut budget = 256;
            let base = home.join(relative);
            let display = format!("~/{relative}");
            scan_directory(&base, &display, 2, &mut budget, &mut entries);
        }
    }
    let mut budget = 256;
    scan_directory(Path::new("/"), "/", 1, &mut budget, &mut entries);
    for absolute in ["/dev", "/tmp", "/var/tmp"] {
        let mut budget = 512;
        scan_directory(Path::new(absolute), absolute, 2, &mut budget, &mut entries);
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
    if let Ok(config) = fs::read_to_string(ssh.join("config")) {
        names.extend(parse_ssh_config_hosts(&config));
    }
    if let Ok(known_hosts) = fs::read_to_string(ssh.join("known_hosts")) {
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
    out: &mut Vec<FileEntry>,
) {
    if depth == 0 || *budget == 0 {
        return;
    }
    let Ok(read_dir) = fs::read_dir(base) else {
        return;
    };
    let mut children: Vec<_> = read_dir.filter_map(Result::ok).take(512).collect();
    children.sort_by_key(|entry| entry.file_name());
    for entry in children {
        if *budget == 0 {
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
            scan_directory(&entry.path(), &visible, depth - 1, budget, out);
        }
    }
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
    fn parser_rejects_malformed_docker_rows() {
        let rows = parse_docker_rows("abc\tgood\timage\nmissing-fields\n\tbad\timage\n");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].name, "good");
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
}
