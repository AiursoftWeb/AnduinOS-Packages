use std::path::Path;
use std::process::{Command, Output};

use crate::secureboot::{DkmsState, SecureBootState};

pub const SOF_PACKAGE: &str = "firmware-sof-anduinos";
pub const UCM_PACKAGE: &str = "alsa-ucm-conf-anduinos";
pub const XBOX_PACKAGE: &str = "anduinos-xbox-controller-driver";
pub const PRINTING_CORE: &[&str] = &["cups", "cups-client"];
pub const PRINTING_DRIVERLESS: &[&str] = &[
    "cups-core-drivers",
    "cups-filters",
    "cups-filters-core-drivers",
    "cups-ipp-utils",
];
pub const PRINTING_DISCOVERY: &[&str] = &["cups-browsed", "avahi-daemon"];
pub const PRINTING_OPTIONAL: &[&str] = &[
    "ipp-usb",
    "cups-pk-helper",
    "printer-driver-all",
    "sane-airscan",
];

#[derive(Clone, Debug)]
pub struct CommandResult {
    pub status: i32,
    pub stdout: String,
    pub stderr: String,
}

impl CommandResult {
    pub fn ok(stdout: impl Into<String>) -> Self {
        Self {
            status: 0,
            stdout: stdout.into(),
            stderr: String::new(),
        }
    }

    pub fn fail(code: i32) -> Self {
        Self {
            status: code,
            stdout: String::new(),
            stderr: String::new(),
        }
    }
}

pub trait Runner {
    fn run(&self, command: &[&str], timeout_secs: u64) -> CommandResult;
}

pub struct SubprocessRunner;

impl Runner for SubprocessRunner {
    fn run(&self, command: &[&str], timeout_secs: u64) -> CommandResult {
        let Some((program, args)) = command.split_first() else {
            return CommandResult::fail(127);
        };
        let _ = timeout_secs;
        match Command::new(program)
            .args(args)
            .env("LC_ALL", "C")
            .output()
        {
            Ok(output) => from_output(output),
            Err(error) => CommandResult {
                status: 127,
                stdout: String::new(),
                stderr: error.to_string(),
            },
        }
    }
}

fn from_output(output: Output) -> CommandResult {
    CommandResult {
        status: output.status.code().unwrap_or(1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

#[derive(Clone, Debug, Default)]
pub struct DriverOption {
    pub package: String,
    pub description: String,
    pub recommended: bool,
    pub free: bool,
    pub builtin: bool,
    pub installed: bool,
    pub installed_version: Option<String>,
    pub candidate_version: Option<String>,
    pub update_available: bool,
    pub active: bool,
}

#[derive(Clone, Debug, Default)]
pub struct HardwareDevice {
    pub identifier: String,
    pub vendor: String,
    pub model: String,
    pub modalias: String,
    pub active_driver: Option<String>,
    pub driver_state_known: bool,
    pub active_driver_healthy: Option<bool>,
    pub active_driver_version: Option<String>,
    pub active_driver_error: Option<String>,
    pub options: Vec<DriverOption>,
}

impl HardwareDevice {
    pub fn title(&self) -> String {
        let vendor = self.vendor.replace(" Corporation", "");
        let vendor = vendor.trim();
        let model = self.model.trim();
        let model = if let Some(start) = model.rfind('[') {
            model
                .get(start + 1..)
                .and_then(|rest| rest.split(']').next())
                .unwrap_or(model)
                .trim()
        } else {
            model
        };
        if !vendor.is_empty()
            && !model.is_empty()
            && !model.to_lowercase().starts_with(&vendor.to_lowercase())
        {
            format!("{vendor} {model}")
        } else if !model.is_empty() {
            model.to_string()
        } else if !vendor.is_empty() {
            vendor.to_string()
        } else {
            "Graphics device".into()
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct GraphicsScan {
    pub devices: Vec<HardwareDevice>,
    pub error: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum XboxStatus {
    NotInstalled,
    ModuleMissing,
    SecureBootUnknown,
    EnrollmentPending,
    TrustSetupRequired,
    SignatureMismatch,
    LoadStateUnknown,
    Loaded,
    Ready,
}

#[derive(Clone, Debug)]
pub struct XboxState {
    pub status: XboxStatus,
    pub installed: bool,
    pub module_available: bool,
    pub module_loaded: bool,
}

#[derive(Clone, Debug)]
pub struct PackageState {
    pub name: String,
    pub installed: bool,
    pub version: Option<String>,
}

#[derive(Clone, Debug)]
pub struct AudioState {
    pub sof_package: PackageState,
    pub ucm_package: PackageState,
    pub firmware_present: bool,
    pub ucm_profiles_present: bool,
    pub sof_modules: Vec<String>,
    pub active_drivers: Vec<String>,
}

impl AudioState {
    pub fn packages_installed(&self) -> bool {
        self.sof_package.installed && self.ucm_package.installed
    }

    pub fn ready(&self) -> bool {
        self.packages_installed() && self.firmware_present && self.ucm_profiles_present
    }
}

#[derive(Clone, Debug)]
pub struct PrintingState {
    pub service_running: bool,
    pub startup_enabled: bool,
    pub printers: Vec<String>,
    pub disabled_printers: Vec<String>,
    pub default_printer: Option<String>,
    pub core_packages: Vec<PackageState>,
    pub driverless_packages: Vec<PackageState>,
    pub discovery_packages: Vec<PackageState>,
    pub optional_packages: Vec<PackageState>,
}

impl PrintingState {
    pub fn missing_required(&self) -> bool {
        self.core_packages
            .iter()
            .chain(self.driverless_packages.iter())
            .any(|package| !package.installed)
    }
}

#[derive(Clone, Debug)]
pub struct SystemScan {
    pub graphics: GraphicsScan,
    pub secure_boot: SecureBootState,
    pub xbox: XboxState,
    pub dkms: DkmsState,
    pub audio: AudioState,
    pub printing: PrintingState,
}

pub fn package_is_installed(package: &str, runner: &dyn Runner) -> bool {
    let result = runner.run(
        &["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package],
        10,
    );
    result.status == 0 && result.stdout.starts_with("ii ")
}

pub fn package_state(package: &str, runner: &dyn Runner) -> PackageState {
    if !package_is_installed(package, runner) {
        return PackageState {
            name: package.into(),
            installed: false,
            version: None,
        };
    }
    let result = runner.run(&["dpkg-query", "-W", "-f=${Version}", package], 10);
    let version = result.stdout.trim();
    PackageState {
        name: package.into(),
        installed: true,
        version: if result.status == 0 && !version.is_empty() {
            Some(version.to_string())
        } else {
            None
        },
    }
}

pub fn package_candidate_version(package: &str, runner: &dyn Runner) -> Option<String> {
    let result = runner.run(&["apt-cache", "policy", package], 10);
    if result.status != 0 {
        return None;
    }
    for line in result.stdout.lines() {
        let line = line.trim();
        if let Some(value) = line.strip_prefix("Candidate:") {
            let candidate = value.trim();
            if candidate.is_empty() || candidate == "(none)" {
                return None;
            }
            return Some(candidate.to_string());
        }
    }
    None
}

pub fn package_update_available(
    installed: Option<&str>,
    candidate: Option<&str>,
    runner: &dyn Runner,
) -> bool {
    let (Some(installed), Some(candidate)) = (installed, candidate) else {
        return false;
    };
    if installed == candidate {
        return false;
    }
    runner
        .run(&["dpkg", "--compare-versions", installed, "lt", candidate], 10)
        .status
        == 0
}

fn directory_contains_files(directory: &Path, suffix: Option<&str>) -> bool {
    let Ok(entries) = std::fs::read_dir(directory) else {
        return false;
    };
    fn walk(path: &Path, suffix: Option<&str>) -> bool {
        if path.is_file() {
            return suffix.is_none_or(|end| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.ends_with(end))
            });
        }
        let Ok(entries) = std::fs::read_dir(path) else {
            return false;
        };
        entries.flatten().any(|entry| walk(&entry.path(), suffix))
    }
    entries.flatten().any(|entry| walk(&entry.path(), suffix))
}

pub fn active_audio_drivers(output: &str) -> Vec<String> {
    let mut drivers = Vec::new();
    let mut audio_device = false;
    for line in output.lines() {
        if !line.is_empty() && !line.starts_with([' ', '\t']) {
            let lowered = line.to_lowercase();
            audio_device = lowered.contains("audio device")
                || lowered.contains("multimedia audio controller");
            continue;
        }
        if audio_device && line.contains("Kernel driver in use:") {
            if let Some(driver) = line.split_once(':').map(|(_, value)| value.trim()) {
                if !driver.is_empty() && !drivers.iter().any(|item| item == driver) {
                    drivers.push(driver.to_string());
                }
            }
        }
    }
    drivers.sort();
    drivers
}

fn sof_modules(output: &str) -> Vec<String> {
    let mut modules: Vec<String> = output
        .lines()
        .filter_map(|line| {
            let name = line.split_whitespace().next()?;
            name.starts_with("snd_sof").then(|| name.to_string())
        })
        .collect();
    modules.sort();
    modules.dedup();
    modules
}

pub fn audio_state(runner: &dyn Runner) -> AudioState {
    let modules = runner.run(&["lsmod"], 10);
    let pci = runner.run(&["lspci", "-nnk"], 10);
    AudioState {
        sof_package: package_state(SOF_PACKAGE, runner),
        ucm_package: package_state(UCM_PACKAGE, runner),
        firmware_present: ["/lib/firmware/intel/sof", "/lib/firmware/intel/sof-ipc4"]
            .iter()
            .any(|path| directory_contains_files(Path::new(path), None)),
        ucm_profiles_present: directory_contains_files(Path::new("/usr/share/alsa/ucm2"), Some(".conf")),
        sof_modules: if modules.status == 0 {
            sof_modules(&modules.stdout)
        } else {
            Vec::new()
        },
        active_drivers: if pci.status == 0 {
            active_audio_drivers(&pci.stdout)
        } else {
            Vec::new()
        },
    }
}

pub fn printer_queues(output: &str) -> (Vec<String>, Vec<String>) {
    let mut printers = Vec::new();
    let mut disabled = Vec::new();
    for line in output.lines() {
        let line = line.trim();
        let Some(rest) = line.strip_prefix("printer ") else {
            continue;
        };
        let Some((name, status)) = rest.split_once(' ') else {
            continue;
        };
        printers.push(name.to_string());
        if format!(" {} ", status.to_lowercase()).contains(" disabled ") {
            disabled.push(name.to_string());
        }
    }
    (printers, disabled)
}

pub fn printing_state(runner: &dyn Runner) -> PrintingState {
    let service = runner.run(&["systemctl", "is-active", "cups.service"], 10);
    let socket = runner.run(&["systemctl", "is-active", "cups.socket"], 10);
    let service_running = (service.status == 0 && service.stdout.trim() == "active")
        || (socket.status == 0 && socket.stdout.trim() == "active");
    let service_enabled = runner.run(&["systemctl", "is-enabled", "cups.service"], 10);
    let socket_enabled = runner.run(&["systemctl", "is-enabled", "cups.socket"], 10);
    let enabled_states = ["enabled", "static", "indirect"];
    let startup_enabled = (service_enabled.status == 0
        && enabled_states.contains(&service_enabled.stdout.trim()))
        || (socket_enabled.status == 0
            && enabled_states.contains(&socket_enabled.stdout.trim()));
    let queues = runner.run(&["lpstat", "-p"], 10);
    let (printers, disabled) = if queues.status == 0 {
        printer_queues(&queues.stdout)
    } else {
        (Vec::new(), Vec::new())
    };
    let default_result = runner.run(&["lpstat", "-d"], 10);
    let default_printer = if default_result.status == 0 {
        default_result
            .stdout
            .split_once(':')
            .map(|(_, value)| value.trim())
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    } else {
        None
    };
    let states = |packages: &[&str]| packages.iter().map(|name| package_state(name, runner)).collect();
    PrintingState {
        service_running,
        startup_enabled,
        printers,
        disabled_printers: disabled,
        default_printer,
        core_packages: states(PRINTING_CORE),
        driverless_packages: states(PRINTING_DRIVERLESS),
        discovery_packages: states(PRINTING_DISCOVERY),
        optional_packages: states(PRINTING_OPTIONAL),
    }
}

fn parse_driver_line(line: &str, runner: &dyn Runner) -> Option<DriverOption> {
    let line = line.trim();
    if !line.starts_with("driver") || !line.contains(':') {
        return None;
    }
    let value = line.split_once(':')?.1.trim();
    let (package, flags) = value.split_once(" - ")?;
    if !package
        .chars()
        .next()
        .is_some_and(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit())
        || !package
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '+' | '.' | '-'))
    {
        return None;
    }
    let words: Vec<String> = flags
        .to_lowercase()
        .split_whitespace()
        .map(str::to_string)
        .collect();
    let recommended = words.iter().any(|word| word == "recommended");
    let installed = package_state(package, runner);
    let candidate = if recommended {
        package_candidate_version(package, runner)
    } else {
        None
    };
    Some(DriverOption {
        package: package.to_string(),
        description: flags.to_string(),
        recommended,
        free: words.iter().any(|word| word == "free")
            && !words.iter().any(|word| word == "non-free"),
        builtin: words.iter().any(|word| word == "builtin"),
        installed: installed.installed,
        installed_version: installed.version.clone(),
        candidate_version: candidate.clone(),
        update_available: package_update_available(
            installed.version.as_deref(),
            candidate.as_deref(),
            runner,
        ),
        active: false,
    })
}

fn active_graphics_driver(identifier: &str, runner: &dyn Runner) -> (bool, Option<String>) {
    let re = regex_lite_slot(identifier);
    let Some(slot) = re else {
        return (false, None);
    };
    let result = runner.run(&["lspci", "-k", "-s", &slot], 10);
    if result.status != 0 {
        return (false, None);
    }
    for line in result.stdout.lines() {
        if line.contains("Kernel driver in use:") {
            let driver = line.split_once(':').map(|(_, value)| value.trim());
            return (true, driver.filter(|value| !value.is_empty()).map(str::to_string));
        }
    }
    (true, None)
}

fn regex_lite_slot(identifier: &str) -> Option<String> {
    // Last PCI slot like 01:00.0 or 0000:01:00.0
    let mut last = None;
    for idx in 0..identifier.len() {
        let rest = &identifier[idx..];
        if let Some(slot) = capture_slot(rest) {
            last = Some(slot);
        }
    }
    last
}

fn capture_slot(input: &str) -> Option<String> {
    // optional 4 hex digits, colon, 2 hex, colon, 2 hex, dot, 1 octal digit
    let bytes = input.as_bytes();
    let mut i = 0;
    if i + 5 <= bytes.len() && bytes.get(4) == Some(&b':') && is_hex_block(&bytes[0..4]) {
        i = 5;
    }
    if i + 7 > bytes.len() {
        return None;
    }
    if !(is_hex_block(&bytes[i..i + 2])
        && bytes[i + 2] == b':'
        && is_hex_block(&bytes[i + 3..i + 5])
        && bytes[i + 5] == b'.'
        && bytes[i + 6].is_ascii_hexdigit()
        && bytes[i + 6] <= b'7')
    {
        return None;
    }
    let end = i + 7;
    if input.get(end..end + 1).is_some_and(|ch| ch.chars().next().is_some_and(|c| c.is_ascii_hexdigit() || c == '/'))
        && end < input.len()
        && !input[end..].starts_with('/')
        && input.as_bytes().get(end).is_some_and(u8::is_ascii_hexdigit)
    {
        return None;
    }
    Some(input[..end].to_string())
}

fn is_hex_block(bytes: &[u8]) -> bool {
    !bytes.is_empty() && bytes.iter().all(u8::is_ascii_hexdigit)
}

fn options_with_active_driver(
    mut options: Vec<DriverOption>,
    active_driver: Option<&str>,
    active_driver_version: Option<&str>,
) -> Vec<DriverOption> {
    let Some(active_driver) = active_driver else {
        return options;
    };
    let normalized = active_driver.to_lowercase().replace('_', "-");
    let candidates: Vec<usize> = if normalized.starts_with("nvidia") {
        let installed: Vec<usize> = options
            .iter()
            .enumerate()
            .filter(|(_, option)| option.installed && option.package.starts_with("nvidia-driver-"))
            .map(|(index, _)| index)
            .collect();
        if let Some(version) = active_driver_version {
            let series = version.split('.').next().unwrap_or(version);
            let matching: Vec<usize> = installed
                .iter()
                .copied()
                .filter(|&index| {
                    let package = &options[index].package;
                    package == &format!("nvidia-driver-{series}")
                        || package.starts_with(&format!("nvidia-driver-{series}-"))
                })
                .collect();
            if matching.is_empty() {
                installed
            } else {
                matching
            }
        } else {
            installed
        }
    } else {
        let package = format!("xserver-xorg-video-{normalized}");
        options
            .iter()
            .enumerate()
            .filter(|(_, option)| option.package == package)
            .map(|(index, _)| index)
            .collect()
    };
    if candidates.len() != 1 {
        return options;
    }
    let active_package = options[candidates[0]].package.clone();
    for option in &mut options {
        option.active = option.package == active_package;
    }
    options
}

pub fn parse_ubuntu_driver_devices(output: &str, runner: &dyn Runner) -> Vec<HardwareDevice> {
    let mut devices = Vec::new();
    let mut block: Vec<(String, String)> = Vec::new();
    let mut options: Vec<DriverOption> = Vec::new();

    let mut finish = |block: &mut Vec<(String, String)>, options: &mut Vec<DriverOption>| {
        if block.is_empty() && options.is_empty() {
            return;
        }
        let get = |key: &str| {
            block
                .iter()
                .find(|(item, _)| item == key)
                .map(|(_, value)| value.clone())
                .unwrap_or_default()
        };
        let identifier = {
            let path = get("path");
            if !path.is_empty() {
                path
            } else {
                let modalias = get("modalias");
                if !modalias.is_empty() {
                    modalias
                } else {
                    format!("device-{}", devices.len())
                }
            }
        };
        let (driver_state_known, active_driver) = active_graphics_driver(&identifier, runner);
        let (healthy, version, error) =
            active_driver_health(active_driver.as_deref(), driver_state_known, runner);
        let mapped = options_with_active_driver(
            std::mem::take(options),
            active_driver.as_deref(),
            version.as_deref(),
        );
        devices.push(HardwareDevice {
            identifier,
            vendor: get("vendor"),
            model: get("model"),
            modalias: get("modalias"),
            active_driver,
            driver_state_known,
            active_driver_healthy: healthy,
            active_driver_version: version,
            active_driver_error: error,
            options: mapped,
        });
        block.clear();
    };

    for raw in output.lines() {
        let line = raw.trim();
        if line.starts_with("==") && line.ends_with("==") {
            finish(&mut block, &mut options);
            block.push(("path".into(), line.trim_matches('=').trim().to_string()));
            continue;
        }
        if let Some(option) = parse_driver_line(line, runner) {
            options.push(option);
            continue;
        }
        if let Some((key, value)) = line.split_once(':') {
            let key = key.trim();
            if matches!(key, "vendor" | "model" | "modalias") {
                block.push((key.into(), value.trim().to_string()));
            }
        }
    }
    finish(&mut block, &mut options);
    devices.retain(|device| !device.options.is_empty());
    devices
}

fn active_driver_health(
    active_driver: Option<&str>,
    driver_state_known: bool,
    runner: &dyn Runner,
) -> (Option<bool>, Option<String>, Option<String>) {
    if !driver_state_known {
        return (
            None,
            None,
            Some("Kernel driver binding could not be determined".into()),
        );
    }
    let Some(active_driver) = active_driver else {
        return (
            Some(false),
            None,
            Some("No kernel driver is bound to this device".into()),
        );
    };
    if !active_driver
        .to_lowercase()
        .replace('_', "-")
        .starts_with("nvidia")
    {
        return (Some(true), None, None);
    }
    let module = runner.run(&["modinfo", "-F", "version", "nvidia"], 10);
    let module_version = if module.status == 0 {
        module.stdout.lines().next().map(str::trim).filter(|s| !s.is_empty()).map(str::to_string)
    } else {
        None
    };
    let Some(module_version) = module_version else {
        let detail = module.stderr.trim();
        return (
            Some(false),
            None,
            Some(if detail.is_empty() {
                "NVIDIA kernel module metadata is unavailable".into()
            } else {
                detail.to_string()
            }),
        );
    };
    let userspace = runner.run(
        &[
            "nvidia-smi",
            "--query-gpu=driver_version",
            "--format=csv,noheader",
        ],
        10,
    );
    let userspace_version = if userspace.status == 0 {
        userspace
            .stdout
            .lines()
            .next()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    } else {
        None
    };
    let Some(userspace_version) = userspace_version else {
        return (
            Some(false),
            Some(module_version),
            Some("nvidia-smi could not communicate with the driver".into()),
        );
    };
    if userspace_version != module_version {
        return (
            Some(false),
            Some(module_version.clone()),
            Some(format!(
                "NVIDIA version mismatch: kernel {module_version}, userspace {userspace_version}"
            )),
        );
    }
    (Some(true), Some(module_version), None)
}

pub fn graphics_scan(runner: &dyn Runner) -> GraphicsScan {
    let result = runner.run(&["ubuntu-drivers", "devices"], 30);
    if result.status != 0 {
        let detail = result.stderr.trim();
        let detail = if detail.is_empty() {
            result.stdout.trim()
        } else {
            detail
        };
        return GraphicsScan {
            devices: Vec::new(),
            error: Some(if detail.is_empty() {
                "ubuntu-drivers could not inspect this hardware".into()
            } else {
                detail.to_string()
            }),
        };
    }
    GraphicsScan {
        devices: parse_ubuntu_driver_devices(&result.stdout, runner),
        error: None,
    }
}

fn xbox_untrusted(dkms: &DkmsState) -> bool {
    dkms.untrusted_modules.iter().any(|name| {
        let lowered = name.to_lowercase();
        lowered.contains("xpadneo") || lowered.contains("hid-xpadneo")
    })
}

pub fn xbox_state(
    secure_boot: &SecureBootState,
    dkms: &DkmsState,
    runner: &dyn Runner,
) -> XboxState {
    let installed = package_is_installed(XBOX_PACKAGE, runner);
    let module = runner.run(&["modinfo", "-F", "filename", "hid-xpadneo"], 10);
    let module_available = installed && module.status == 0 && !module.stdout.trim().is_empty();
    let modules = runner.run(&["lsmod"], 10);
    let load_state_known = modules.status == 0;
    let loaded = load_state_known
        && modules.stdout.lines().any(|line| {
            matches!(
                line.split_whitespace().next(),
                Some("hid_xpadneo" | "xpadneo")
            )
        });
    let status = if !installed {
        XboxStatus::NotInstalled
    } else if !module_available {
        XboxStatus::ModuleMissing
    } else if !secure_boot.state_known {
        XboxStatus::SecureBootUnknown
    } else if secure_boot.enabled && secure_boot.enrollment_pending {
        XboxStatus::EnrollmentPending
    } else if secure_boot.enabled && !secure_boot.enrolled {
        XboxStatus::TrustSetupRequired
    } else if secure_boot.enabled && xbox_untrusted(dkms) {
        XboxStatus::SignatureMismatch
    } else if !load_state_known {
        XboxStatus::LoadStateUnknown
    } else if loaded {
        XboxStatus::Loaded
    } else {
        XboxStatus::Ready
    };
    XboxState {
        status,
        installed,
        module_available,
        module_loaded: loaded,
    }
}

pub fn scan_system() -> SystemScan {
    let runner = SubprocessRunner;
    let secure_boot = crate::secureboot::inspect();
    let xbox = xbox_state(&secure_boot.state, &secure_boot.dkms, &runner);
    SystemScan {
        graphics: graphics_scan(&runner),
        dkms: secure_boot.dkms.clone(),
        xbox,
        audio: audio_state(&runner),
        printing: printing_state(&runner),
        secure_boot: secure_boot.state,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    struct Fake {
        installed: Vec<String>,
        versions: HashMap<String, String>,
        responses: HashMap<Vec<String>, CommandResult>,
    }

    impl Runner for Fake {
        fn run(&self, command: &[&str], _timeout_secs: u64) -> CommandResult {
            if command.get(0..3) == Some(&["dpkg-query", "-W", "-f=${db:Status-Abbrev}"]) {
                let package = command[3];
                if self.installed.iter().any(|item| item == package) {
                    return CommandResult::ok("ii ");
                }
                return CommandResult::fail(1);
            }
            if command.get(0..3) == Some(&["dpkg-query", "-W", "-f=${Version}"]) {
                if let Some(version) = self.versions.get(command[3]) {
                    return CommandResult::ok(version.clone());
                }
                return CommandResult::fail(1);
            }
            self.responses
                .get(&command.iter().map(|item| (*item).to_string()).collect::<Vec<_>>())
                .cloned()
                .unwrap_or_else(|| CommandResult::fail(1))
        }
    }

    #[test]
    fn parses_ubuntu_drivers_device_and_recommendation() {
        let output = r#"== /sys/devices/pci0000:00/0000:01:00.0 ==
modalias : pci:v000010DEd00002820
vendor   : NVIDIA Corporation
model    : AD107M [GeForce RTX 4060 Max-Q]
driver   : nvidia-driver-590 - distro non-free recommended
driver   : xserver-xorg-video-nouveau - distro free builtin
"#;
        let runner = Fake {
            installed: vec!["nvidia-driver-590".into()],
            versions: HashMap::new(),
            responses: HashMap::new(),
        };
        let devices = parse_ubuntu_driver_devices(output, &runner);
        assert_eq!(devices.len(), 1);
        assert_eq!(devices[0].vendor, "NVIDIA Corporation");
        assert_eq!(devices[0].title(), "NVIDIA GeForce RTX 4060 Max-Q");
        assert!(devices[0].options[0].recommended);
        assert_eq!(devices[0].options[0].package, "nvidia-driver-590");
    }

    #[test]
    fn printer_queue_parser_detects_paused_printers() {
        let (printers, disabled) = printer_queues(
            "printer Office is idle.  enabled since Thu\nprinter Lab disabled since Fri\n",
        );
        assert_eq!(printers, vec!["Office", "Lab"]);
        assert_eq!(disabled, vec!["Lab"]);
    }

    #[test]
    fn audio_driver_parser_reads_kernel_driver() {
        let output = "00:1f.3 Audio device: Intel\n\tKernel driver in use: snd_hda_intel\n";
        assert_eq!(active_audio_drivers(output), vec!["snd_hda_intel"]);
    }
}
