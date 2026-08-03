use crate::history;
use crate::protocol::{decode_request, encode_response, Request, Response};
use crate::{evaluate, Action, Container, FileEntry, GitRef, Process, Query, Service, WorldState};
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, RwLock};
use std::thread;
use std::time::{Duration, Instant};

pub struct Runtime {
    world: Arc<RwLock<WorldState>>,
    history_path: Option<PathBuf>,
}

impl Default for Runtime {
    fn default() -> Self {
        let history_path = history::state_path();
        let mut world = WorldState::default();
        if let Some(path) = history_path.as_deref() {
            world.history = history::load(path);
        }
        world.merge_history(history::load_bash_history());
        let runtime = Self {
            world: Arc::new(RwLock::new(world)),
            history_path,
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
                    file_refresh,
                    learned,
                    history_snapshot,
                ) = {
                    let Ok(mut world) = self.world.write() else {
                        return Response::Error;
                    };
                    let learned = world.observe_command_with_cwd(&line, exit_code, now_ms, &cwd);
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
                        && (world.git.cwd != cwd || action == Some(Action::GitMutation))
                    {
                        world.git.generation = world.git.generation.wrapping_add(1);
                        world.git.refreshed_at_ms = 0;
                        world.git.cwd = cwd.clone();
                        world.git.refs.clear();
                        Some((world.git.generation, cwd.clone()))
                    } else {
                        None
                    };
                    let files = if world.files.cwd != cwd {
                        world.files.generation = world.files.generation.wrapping_add(1);
                        world.files.refreshed_at_ms = 0;
                        world.files.cwd = cwd.clone();
                        world.files.entries.clear();
                        Some((world.files.generation, cwd.clone()))
                    } else {
                        None
                    };
                    let history_snapshot = learned.as_ref().map(|_| world.history.clone());
                    (
                        docker,
                        process,
                        service,
                        git,
                        files,
                        learned,
                        history_snapshot,
                    )
                };
                if let (Some(path), Some(event), Some(snapshot)) = (
                    &self.history_path,
                    learned.as_ref(),
                    history_snapshot.as_deref(),
                ) {
                    if let Err(error) = history::record(path, event, snapshot) {
                        debug(&format!("history persistence failed: {error}"));
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
                if let Some((generation, cwd)) = file_refresh {
                    self.refresh_files(generation, cwd);
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
            let mut entries: Vec<FileEntry> = fs::read_dir(&cwd)
                .ok()?
                .take(512)
                .filter_map(Result::ok)
                .filter_map(|entry| {
                    let name = entry.file_name().into_string().ok()?;
                    let directory = entry.file_type().ok()?.is_dir();
                    Some(FileEntry { name, directory })
                })
                .collect();
            entries.sort_by(|left, right| left.name.cmp(&right.name));
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
}
