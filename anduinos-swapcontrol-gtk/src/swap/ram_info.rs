//! Hardware RAM information via dmidecode.
//! dmidecode is called via the helper (pkexec), but ONLY asynchronously
//! to avoid blocking the GTK main loop on first auth.

#[derive(Debug, Clone, Default)]
pub struct RamInfo {
    pub total_gb: f64,
    pub ram_type: String,
    pub speed_mts: u32,
    pub manufacturer: String,
    pub dimms: Vec<DimmInfo>,
    pub error_correction: String,
}

#[derive(Debug, Clone, Default)]
pub struct DimmInfo {
    pub locator: String,
    pub size_gb: u32,
    pub form_factor: String,
    pub speed_mts: u32,
    pub manufacturer: String,
    pub part_number: String,
}

/// Fast read from /proc/meminfo only (no root, no blocking).
pub fn read_ram_basic() -> RamInfo {
    let mut info = RamInfo::default();
    if let Ok(content) = std::fs::read_to_string("/proc/meminfo") {
        for line in content.lines() {
            if line.starts_with("MemTotal:") {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 {
                    let kb: u64 = parts[1].parse().unwrap_or(0);
                    info.total_gb = kb as f64 / (1024.0 * 1024.0);
                }
                break;
            }
        }
    }
    info
}

/// Full read including dmidecode. Call from a background thread to avoid blocking GTK.
pub fn read_ram_info_full() -> RamInfo {
    let mut info = read_ram_basic();

    let output = std::process::Command::new("pkexec")
        .env("LC_ALL", "C")
        .args([
            "/usr/lib/anduinos-swapcontrol/helper",
            "dmidecode",
            "-t",
            "memory",
        ])
        .output();

    let text = match output {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).to_string(),
        _ => {
            eprintln!("dmidecode: auth needed or unavailable");
            return info;
        }
    };

    parse_dmidecode(&mut info, &text);
    info
}

fn parse_dmidecode(info: &mut RamInfo, text: &str) {
    let mut current_dimm: Option<DimmInfo> = None;
    let mut in_memory_device = false;

    for line in text.lines() {
        let line = line.trim();

        if line.starts_with("Memory Device") || line.starts_with("Physical Memory Array") {
            if let Some(dimm) = current_dimm.take() {
                if dimm.size_gb > 0 {
                    info.dimms.push(dimm);
                }
            }
            in_memory_device = line.starts_with("Memory Device");
            if in_memory_device {
                current_dimm = Some(DimmInfo::default());
            }
            continue;
        }

        if !in_memory_device {
            if let Some(val) = parse_kv(line, "Error Correction Type:") {
                info.error_correction = val.to_string();
            }
            continue;
        }

        if let Some(dimm) = current_dimm.as_mut() {
            if let Some(val) = parse_kv(line, "Locator:") {
                dimm.locator = val.to_string();
            }
            if let Some(val) = parse_kv(line, "Size:") {
                if val != "No Module Installed" && val != "None" {
                    dimm.size_gb = parse_size_to_gb(val);
                }
            }
            if let Some(val) = parse_kv(line, "Type:") {
                if val != "Unknown" && info.ram_type.is_empty() {
                    info.ram_type = val.to_string();
                }
            }
            if let Some(val) = parse_kv(line, "Speed:") {
                dimm.speed_mts = val
                    .split_whitespace()
                    .next()
                    .unwrap_or("0")
                    .parse()
                    .unwrap_or(0);
                if info.speed_mts == 0 {
                    info.speed_mts = dimm.speed_mts;
                }
            }
            if let Some(val) = parse_kv(line, "Manufacturer:") {
                if dimm.manufacturer.is_empty() {
                    dimm.manufacturer = val.to_string();
                }
                if info.manufacturer.is_empty() {
                    info.manufacturer = dimm.manufacturer.clone();
                }
            }
            if let Some(val) = parse_kv(line, "Form Factor:") {
                dimm.form_factor = val.to_string();
            }
            if let Some(val) = parse_kv(line, "Part Number:") {
                dimm.part_number = val.to_string();
            }
            if let Some(val) = parse_kv(line, "Type Detail:") {
                if info.ram_type.is_empty() {
                    info.ram_type = val.to_string();
                }
            }
        }
    }
    if let Some(dimm) = current_dimm.take() {
        if dimm.size_gb > 0 {
            info.dimms.push(dimm);
        }
    }
}

fn parse_kv<'a>(line: &'a str, key: &str) -> Option<&'a str> {
    let rest = line.strip_prefix(key)?;
    let val = rest.trim();
    if val.is_empty() || val == "None" || val == "Unknown" || val == "<OUT OF SPEC>" {
        None
    } else {
        Some(val)
    }
}

fn parse_size_to_gb(s: &str) -> u32 {
    let num: u32 = s
        .split_whitespace()
        .next()
        .unwrap_or("0")
        .parse()
        .unwrap_or(0);
    if s.contains("MB") {
        num / 1024
    } else {
        num
    }
}
