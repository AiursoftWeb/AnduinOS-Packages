//! Single-owner, bounded pipe protocol for the existing native ASR/VAD worker.
//! PCM never touches disk. Cancellation only permits reuse after an explicit ACK.
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use serde_json::Value;

#[derive(Debug, PartialEq, Eq)]
pub enum WorkerError {
    Cancelled,
    Timeout,
    Unavailable,
    Protocol(&'static str),
}

impl std::fmt::Display for WorkerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Cancelled => "Speech recognition cancelled",
            Self::Timeout => "Speech recognition timed out",
            Self::Unavailable => "The recognition worker could not be started",
            Self::Protocol(message) => message,
        })
    }
}
impl std::error::Error for WorkerError {}

pub struct WorkerTransport {
    process: Option<Child>,
    response: Vec<u8>,
    timeout: Duration,
}

impl WorkerTransport {
    pub fn set_timeout(&mut self, timeout: Duration) {
        self.timeout = timeout;
    }
    pub fn spawn(command: &mut Command, timeout: Duration) -> Result<Self, WorkerError> {
        let child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| WorkerError::Unavailable)?;
        let mut transport = Self {
            process: Some(child),
            response: Vec::new(),
            timeout,
        };
        let child = transport.process.as_ref().unwrap();
        for fd in [
            child.stdin.as_ref().unwrap().as_raw_fd(),
            child.stdout.as_ref().unwrap().as_raw_fd(),
        ] {
            // SAFETY: these descriptors are owned by the live Child; flags do not transfer ownership.
            let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
            if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
            {
                transport.close();
                return Err(WorkerError::Unavailable);
            }
        }
        Ok(transport)
    }

    pub fn is_running(&mut self) -> bool {
        self.process
            .as_mut()
            .is_some_and(|child| matches!(child.try_wait(), Ok(None)))
    }

    fn signal(&mut self, signal: i32) {
        if self.is_running() {
            let pid = self.process.as_ref().unwrap().id();
            // SAFETY: the owned child has not been reaped, so its PID cannot be reused.
            unsafe {
                libc::kill(pid as libc::pid_t, signal);
            }
        }
    }

    pub fn exchange(&mut self, request: &[u8], cancel: &AtomicBool) -> Result<Value, WorkerError> {
        let result = self.exchange_inner(request, cancel);
        // An acknowledged cancellation may retain the worker. Every other failure
        // destroys the stream: an incomplete frame must never poison a future request.
        if result.is_err() && result != Err(WorkerError::Cancelled) {
            self.close();
        }
        result
    }

    fn exchange_inner(
        &mut self,
        request: &[u8],
        cancel: &AtomicBool,
    ) -> Result<Value, WorkerError> {
        let deadline = Instant::now() + self.timeout;
        let mut offset = 0;
        let mut abort_deadline = None;
        loop {
            let now = Instant::now();
            if cancel.load(Ordering::Acquire) {
                if offset < request.len() || request.is_empty() {
                    self.close();
                    return Err(WorkerError::Cancelled);
                }
                if abort_deadline.is_none() {
                    self.signal(libc::SIGUSR1);
                    abort_deadline = Some(now + Duration::from_millis(500));
                }
                if abort_deadline.is_some_and(|end| now >= end) {
                    self.close();
                    return Err(WorkerError::Cancelled);
                }
            }
            if now >= deadline {
                return Err(WorkerError::Timeout);
            }
            if let Some(end) = self.response.iter().position(|byte| *byte == b'\n') {
                let tail = self.response.split_off(end + 1);
                let line = std::mem::replace(&mut self.response, tail);
                let result: Value = serde_json::from_slice(&line[..end])
                    .map_err(|_| WorkerError::Protocol("Invalid recognition worker response"))?;
                if !result.is_object() || result.get("metrics").is_some_and(|m| !m.is_object()) {
                    return Err(WorkerError::Protocol("Invalid recognition worker response"));
                }
                if abort_deadline.is_some() {
                    if result["status"] != "cancelled" {
                        self.close();
                    }
                    return Err(WorkerError::Cancelled);
                }
                return Ok(result);
            }
            let child = self
                .process
                .as_mut()
                .ok_or(WorkerError::Protocol("Recognition worker exited"))?;
            let input = child.stdin.as_mut().unwrap();
            let output = child.stdout.as_mut().unwrap();
            let mut fds = [
                libc::pollfd {
                    fd: output.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: input.as_raw_fd(),
                    events: if offset < request.len() {
                        libc::POLLOUT
                    } else {
                        0
                    },
                    revents: 0,
                },
            ];
            // SAFETY: fds references two initialized pollfd values, valid for this call.
            let ready = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, 20) };
            if ready < 0 {
                if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(WorkerError::Protocol("Recognition worker pipe failed"));
            }
            if fds[1].revents & libc::POLLOUT != 0 {
                let end = request.len().min(offset + 16384);
                match input.write(&request[offset..end]) {
                    Ok(0) => return Err(WorkerError::Protocol("Recognition worker exited")),
                    Ok(count) => offset += count,
                    Err(e)
                        if matches!(
                            e.kind(),
                            io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                        ) => {}
                    Err(_) => return Err(WorkerError::Protocol("Recognition worker exited")),
                }
            }
            if fds[0].revents != 0 {
                let mut buffer = [0; 65536];
                match output.read(&mut buffer) {
                    Ok(0) => return Err(WorkerError::Protocol("Recognition worker exited")),
                    Ok(count) => self.response.extend_from_slice(&buffer[..count]),
                    Err(e)
                        if matches!(
                            e.kind(),
                            io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                        ) => {}
                    Err(_) => return Err(WorkerError::Protocol("Recognition worker exited")),
                }
                if self.response.len() > 1_048_576 {
                    return Err(WorkerError::Protocol(
                        "Recognition worker response is too large",
                    ));
                }
            }
        }
    }

    pub fn close(&mut self) {
        self.response.clear();
        self.signal(libc::SIGTERM);
        if let Some(mut child) = self.process.take() {
            let deadline = Instant::now() + Duration::from_millis(500);
            while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(10));
            }
            if !matches!(child.try_wait(), Ok(Some(_))) {
                let _ = child.kill();
            }
            let _ = child.wait();
        }
    }
}

impl Drop for WorkerTransport {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn worker(script: &str, timeout: Duration) -> WorkerTransport {
        WorkerTransport::spawn(Command::new("python3").args(["-u", "-c", script]), timeout).unwrap()
    }

    #[test]
    fn handles_split_and_buffered_responses() {
        let mut worker = worker(
            "import sys,time; sys.stdout.write('{'); sys.stdout.flush(); time.sleep(.01); print('\"status\":\"ready\"}'); print('{\"status\":\"success\",\"text\":\"你好\"}')",
            Duration::from_secs(2),
        );
        let cancel = AtomicBool::new(false);
        assert_eq!(worker.exchange(&[], &cancel).unwrap()["status"], "ready");
        assert_eq!(worker.exchange(&[], &cancel).unwrap()["text"], "你好");
    }

    #[test]
    fn malformed_responses_destroy_the_worker() {
        for response in ["[]", "{\"metrics\":null}", "not json"] {
            let mut worker = worker(&format!("print({response:?})"), Duration::from_secs(2));
            assert!(matches!(
                worker.exchange(&[], &AtomicBool::new(false)),
                Err(WorkerError::Protocol(_))
            ));
            assert!(worker.process.is_none());
        }
    }

    #[test]
    fn cancellation_reuses_only_an_acknowledged_stream() {
        for acknowledgement in ["cancelled", "success", "none"] {
            let directory = tempfile::tempdir().unwrap();
            let received = directory.path().join("received");
            let script = format!(
                r#"
import pathlib,signal,sys,time
def cancelled(*_):
    if {acknowledgement:?} != 'none':
        print('{{"status":"{acknowledgement}"}}', flush=True)
signal.signal(signal.SIGUSR1, cancelled)
print('{{"status":"ready"}}', flush=True)
sys.stdin.buffer.read(1)
pathlib.Path({path:?}).touch()
sys.stdin.buffer.read(1)
print('{{"status":"success","text":"next request"}}', flush=True)
time.sleep(30)
"#,
                path = received.to_str().unwrap(),
            );
            let mut transport = worker(&script, Duration::from_secs(3));
            let cancel = AtomicBool::new(false);
            assert_eq!(transport.exchange(&[], &cancel).unwrap()["status"], "ready");
            std::thread::scope(|scope| {
                scope.spawn(|| {
                    let deadline = Instant::now() + Duration::from_secs(2);
                    while !received.exists() && Instant::now() < deadline {
                        std::thread::sleep(Duration::from_millis(1));
                    }
                    assert!(
                        received.exists(),
                        "worker must receive the complete request"
                    );
                    cancel.store(true, Ordering::Release);
                });
                assert_eq!(
                    transport.exchange(b"1", &cancel),
                    Err(WorkerError::Cancelled)
                );
            });
            if acknowledgement == "cancelled" {
                assert!(transport.is_running());
                cancel.store(false, Ordering::Release);
                assert_eq!(
                    transport.exchange(b"2", &cancel).unwrap()["text"],
                    "next request"
                );
            } else {
                // A late successful result is not an ACK. A missing ACK is bounded.
                assert!(transport.process.is_none());
            }
        }
    }

    #[test]
    fn timeout_and_startup_cancellation_reap_children() {
        let mut stalled = worker("import time; time.sleep(30)", Duration::from_millis(50));
        assert_eq!(
            stalled.exchange(&[], &AtomicBool::new(false)),
            Err(WorkerError::Timeout)
        );
        assert!(stalled.process.is_none());
        let mut cancelled = worker("import time; time.sleep(30)", Duration::from_secs(2));
        assert_eq!(
            cancelled.exchange(&[], &AtomicBool::new(true)),
            Err(WorkerError::Cancelled)
        );
        assert!(cancelled.process.is_none());
    }
}
