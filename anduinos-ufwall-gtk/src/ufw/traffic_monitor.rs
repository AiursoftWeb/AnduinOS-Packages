use serde::{Deserialize, Serialize};
use std::process::{Command, Stdio};
use std::io::{BufRead, BufReader};

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct ConnectionStat {
    pub pid: Option<i32>,
    pub process_name: String,
    pub remote_ip: String,
    pub domain_name: Option<String>,
    pub local_port: u16,
    pub remote_port: u16,
    pub protocol: String, // "TCP" or "UDP"
    pub direction: String,
    pub upload_speed: u64,   // Bytes per second
    pub download_speed: u64, // Bytes per second
    pub total_bytes: u64,
    pub total_uploaded: u64,
    pub total_downloaded: u64,
}

pub struct TrafficMonitor {}

impl TrafficMonitor {
    pub fn start_monitoring(sender: async_channel::Sender<Result<Vec<ConnectionStat>, String>>) {
        std::thread::spawn(move || {
            // Find ufwall-auditor path
            let mut auditor_path = std::env::current_exe()
                .unwrap_or_default()
                .parent()
                .unwrap_or(std::path::Path::new(""))
                .join("ufwall-auditor");
            
            if !auditor_path.exists() {
                auditor_path = std::path::PathBuf::from("/usr/libexec/ufwall-gtk/ufwall-auditor");
            }
            if !auditor_path.exists() {
                auditor_path = std::path::PathBuf::from("ufwall-auditor"); // fallback to PATH
            }

            let mut child = match Command::new(&auditor_path)
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .spawn() {
                    Ok(c) => c,
                    Err(e) => {
                        let _ = sender.send_blocking(Err(format!("Failed to start ufwall-auditor: {}. Is it installed?", e)));
                        return;
                    }
                };

            if let Some(stdout) = child.stdout.take() {
                let reader = BufReader::new(stdout);
                for line in reader.lines() {
                    match line {
                        Ok(l) => {
                            if let Ok(stats) = serde_json::from_str::<Vec<ConnectionStat>>(&l) {
                                if sender.send_blocking(Ok(stats)).is_err() {
                                    break;
                                }
                            }
                        }
                        Err(_) => break,
                    }
                }
            }
            
            let _ = child.kill();
        });
    }
}
