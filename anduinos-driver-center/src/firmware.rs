use serde::Deserialize;

#[derive(Clone, Debug, Default)]
pub struct FirmwareDevice {
    pub device_id: String,
    pub name: String,
    pub version: Option<String>,
    pub vendor: Option<String>,
    pub update_version: Option<String>,
    pub summary: Option<String>,
}

#[derive(Clone, Debug, Default)]
pub struct FirmwareHistory {
    pub name: String,
    pub version: Option<String>,
    pub timestamp: Option<u64>,
    pub state: i32,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Default)]
pub struct FirmwareSnapshot {
    pub devices: Vec<FirmwareDevice>,
    pub history: Vec<FirmwareHistory>,
    pub daemon_version: Option<String>,
    pub connected: bool,
    pub error: Option<String>,
}

impl FirmwareSnapshot {
    pub fn updates(&self) -> Vec<&FirmwareDevice> {
        self.devices
            .iter()
            .filter(|device| device.update_version.is_some())
            .collect()
    }
}

#[derive(Deserialize)]
struct DevicesWire {
    #[serde(default, rename = "Devices")]
    devices: Vec<DeviceWire>,
}

#[derive(Deserialize)]
struct DeviceWire {
    #[serde(default, rename = "DeviceId")]
    device_id: String,
    #[serde(default, rename = "Name")]
    name: Option<String>,
    #[serde(default, rename = "Version")]
    version: Option<String>,
    #[serde(default, rename = "Vendor")]
    vendor: Option<String>,
    #[serde(default, rename = "Summary")]
    summary: Option<String>,
    #[serde(default, rename = "UpdateError")]
    update_error: Option<String>,
    #[serde(default, rename = "UpdateState")]
    update_state: Option<i32>,
    #[serde(default, rename = "Created")]
    created: Option<u64>,
    #[serde(default, rename = "Modified")]
    modified: Option<u64>,
    #[serde(default, rename = "Releases")]
    releases: Vec<ReleaseWire>,
}

#[derive(Deserialize)]
struct ReleaseWire {
    #[serde(default, rename = "Version")]
    version: Option<String>,
}

fn json_command(args: &[&str]) -> Result<Vec<u8>, String> {
    let output = std::process::Command::new("fwupdmgr")
        .args(args)
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(detail.trim().to_string());
    }
    Ok(output.stdout)
}

fn daemon_version() -> Option<String> {
    let output = std::process::Command::new("fwupdmgr")
        .arg("--version")
        .output()
        .ok()?;
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .find(|line| line.contains("org.freedesktop.fwupd"))
        .and_then(|line| line.split_whitespace().last())
        .map(str::to_string)
}

pub fn snapshot() -> FirmwareSnapshot {
    let daemon_version = daemon_version();
    let devices_out = match json_command(&["get-devices", "--json"]) {
        Ok(bytes) => bytes,
        Err(error) => {
            return FirmwareSnapshot {
                error: Some(if error.is_empty() {
                    "fwupdmgr is not available".into()
                } else {
                    error
                }),
                connected: false,
                daemon_version,
                ..FirmwareSnapshot::default()
            };
        }
    };
    let parsed: DevicesWire =
        serde_json::from_slice(&devices_out).unwrap_or(DevicesWire { devices: Vec::new() });
    let mut devices: Vec<FirmwareDevice> = parsed
        .devices
        .into_iter()
        .map(|device| FirmwareDevice {
            device_id: device.device_id,
            name: device.name.unwrap_or_else(|| "Firmware device".into()),
            version: device.version,
            vendor: device.vendor,
            summary: device.summary,
            update_version: None,
        })
        .filter(|device| !device.device_id.is_empty())
        .collect();

    if let Ok(updates_out) = json_command(&["get-updates", "--json"]) {
        if let Ok(updates) = serde_json::from_slice::<DevicesWire>(&updates_out) {
            for update in updates.devices {
                if let Some(version) = update
                    .releases
                    .first()
                    .and_then(|release| release.version.clone())
                {
                    if let Some(device) = devices
                        .iter_mut()
                        .find(|device| device.device_id == update.device_id)
                    {
                        device.update_version = Some(version);
                    }
                }
            }
        }
    }

    let mut history = Vec::new();
    if let Ok(history_out) = json_command(&["get-history", "--json"]) {
        if let Ok(parsed) = serde_json::from_slice::<DevicesWire>(&history_out) {
            history = parsed
                .devices
                .into_iter()
                .map(|device| FirmwareHistory {
                    name: device
                        .name
                        .unwrap_or_else(|| "Firmware device".into()),
                    version: device.version,
                    timestamp: device.modified.or(device.created),
                    state: device.update_state.unwrap_or(0),
                    error: device.update_error,
                })
                .collect();
        }
    }

    FirmwareSnapshot {
        devices,
        history,
        daemon_version,
        connected: true,
        error: None,
    }
}
