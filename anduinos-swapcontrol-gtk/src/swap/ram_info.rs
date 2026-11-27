//! Hardware RAM information via dmidecode and unified /proc/meminfo accounting.
//! dmidecode is called via the helper (pkexec), but ONLY asynchronously
//! to avoid blocking the GTK main loop on first auth.

const KIB: f64 = 1024.0;
const MIB: f64 = 1024.0 * 1024.0;
const GIB: f64 = 1024.0 * 1024.0 * 1024.0;

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

/// Unified memory snapshot parsed from `/proc/meminfo`.
///
/// Aligns `used` with modern Linux tools (`free` / `procps-ng 4.x`,
/// GNOME System Monitor, `inxi`): `used = MemTotal - MemAvailable` when
/// `MemAvailable` is present, falling back to `Buffers + Cached + SReclaimable - Shmem`
/// otherwise. The ring segments (`used + buffers + cached + free`) always sum
/// exactly to `total`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MemInfo {
    pub total: u64,
    pub used: u64,
    pub buffers: u64,
    pub cached: u64,
    pub free: u64,
    pub available: u64,
    pub swap_total: u64,
    pub swap_used: u64,
}

/// Read current memory and swap statistics from `/proc/meminfo`.
pub fn read_meminfo() -> MemInfo {
    match std::fs::read_to_string("/proc/meminfo") {
        Ok(content) => parse_meminfo(&content),
        Err(_) => MemInfo::default(),
    }
}

/// Parse `/proc/meminfo` text into a consistent `MemInfo` breakdown.
pub fn parse_meminfo(content: &str) -> MemInfo {
    let mut total: u64 = 0;
    let mut raw_free: u64 = 0;
    let mut raw_available: Option<u64> = None;
    let mut raw_buffers: u64 = 0;
    let mut raw_cached: u64 = 0;
    let mut s_reclaimable: u64 = 0;
    let mut shmem: u64 = 0;
    let mut swap_total: u64 = 0;
    let mut swap_free: u64 = 0;

    for line in content.lines() {
        let mut parts = line.split_whitespace();
        let Some(key) = parts.next() else {
            continue;
        };
        let Some(val_str) = parts.next() else {
            continue;
        };
        let bytes = val_str.parse::<u64>().unwrap_or(0).saturating_mul(1024);
        match key.trim_end_matches(':') {
            "MemTotal" => total = bytes,
            "MemFree" => raw_free = bytes,
            "MemAvailable" => raw_available = Some(bytes),
            "Buffers" => raw_buffers = bytes,
            "Cached" => raw_cached = bytes,
            "SReclaimable" => s_reclaimable = bytes,
            "Shmem" => shmem = bytes,
            "SwapTotal" => swap_total = bytes,
            "SwapFree" => swap_free = bytes,
            _ => {}
        }
    }

    let available = match raw_available {
        Some(avail) => avail.min(total),
        None => {
            let reclaimable_cache = raw_cached
                .saturating_add(s_reclaimable)
                .saturating_sub(shmem);
            raw_free
                .saturating_add(raw_buffers)
                .saturating_add(reclaimable_cache)
                .min(total)
        }
    };

    let used = total.saturating_sub(available);
    let free = raw_free.min(available);
    let reclaimable = available.saturating_sub(free);
    let buffers = raw_buffers.min(reclaimable);
    let cached = reclaimable.saturating_sub(buffers);
    let swap_used = swap_total.saturating_sub(swap_free);

    MemInfo {
        total,
        used,
        buffers,
        cached,
        free,
        available,
        swap_total,
        swap_used,
    }
}

/// Sum installed DIMM capacity (in GiB/GB reported by dmidecode), if available.
pub fn installed_ram_gb(dimms: &[DimmInfo]) -> Option<u32> {
    let sum: u32 = dimms
        .iter()
        .filter(|d| d.size_gb > 0)
        .map(|d| d.size_gb)
        .sum();
    if sum > 0 {
        Some(sum)
    } else {
        None
    }
}

/// Format the top `Total RAM` stat consistently with the ring center (`{:.1} GiB`),
/// and when `dmidecode` reports physical installed capacity differing from usable
/// kernel RAM (e.g., due to iGPU/firmware reservation), show `usable / installed`.
pub fn format_ram_total(usable_gib: f64, installed_gb: Option<u32>) -> String {
    if let Some(installed) = installed_gb.filter(|&gb| gb > 0) {
        if (usable_gib - installed as f64).abs() >= 0.05 {
            return format!("{:.1} / {} GiB", usable_gib, installed);
        }
    }
    format!("{:.1} GiB", usable_gib)
}

/// Format populated DIMM slots concisely, handling both uniform (`2×8GB`)
/// and heterogeneous (`8GB + 16GB`) configurations.
pub fn format_dimm_summary(dimms: &[DimmInfo]) -> Option<String> {
    let pop: Vec<&DimmInfo> = dimms.iter().filter(|d| d.size_gb > 0).collect();
    if pop.is_empty() {
        return None;
    }
    let first_size = pop[0].size_gb;
    if pop.iter().all(|d| d.size_gb == first_size) {
        Some(format!("{}×{}GB", pop.len(), first_size))
    } else {
        Some(
            pop.iter()
                .map(|d| format!("{}GB", d.size_gb))
                .collect::<Vec<_>>()
                .join(" + "),
        )
    }
}

/// Format a `used / total` capacity pair adaptively so small non-zero usage
/// (such as `14.7 MiB` of a `17.0 GiB` swap partition) never rounds down to
/// `0.0 / 17.0 GiB`.
pub fn format_usage_pair(used_bytes: u64, total_bytes: u64) -> String {
    if total_bytes < 1024 * 1024 * 1024 {
        let total_mib = total_bytes as f64 / MIB;
        if used_bytes == 0 {
            format!("0 / {:.0} MiB", total_mib)
        } else if used_bytes < 1024 * 1024 {
            let used_kib = (used_bytes as f64 / KIB).max(1.0);
            format!("{:.0} KiB / {:.0} MiB", used_kib, total_mib)
        } else {
            format!("{:.1} / {:.0} MiB", used_bytes as f64 / MIB, total_mib)
        }
    } else {
        let total_gib = total_bytes as f64 / GIB;
        if used_bytes == 0 {
            format!("0.0 / {:.1} GiB", total_gib)
        } else if used_bytes < 1024 * 1024 {
            let used_kib = (used_bytes as f64 / KIB).max(1.0);
            format!("{:.0} KiB / {:.1} GiB", used_kib, total_gib)
        } else {
            let used_mib = used_bytes as f64 / MIB;
            if used_mib < 1023.95 {
                format!("{:.1} MiB / {:.1} GiB", used_mib, total_gib)
            } else {
                format!("{:.1} / {:.1} GiB", used_bytes as f64 / GIB, total_gib)
            }
        }
    }
}

/// Fast read from /proc/meminfo only (no root, no blocking).
pub fn read_ram_basic() -> RamInfo {
    let mem = read_meminfo();
    RamInfo {
        total_gb: mem.total as f64 / GIB,
        ..RamInfo::default()
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_meminfo_matches_memavailable_accounting_and_closes_ring() {
        let sample = "\
MemTotal:       16148070 kB
MemFree:        11953766 kB
MemAvailable:   14260634 kB
Buffers:           52428 kB
Cached:          2726297 kB
Shmem:            314572 kB
SReclaimable:     157286 kB
SwapTotal:      17825792 kB
SwapFree:       17810739 kB
";
        let info = parse_meminfo(sample);
        assert_eq!(info.total, 16_148_070 * 1024);
        assert_eq!(info.available, 14_260_634 * 1024);
        assert_eq!(info.used, info.total - info.available);
        assert_eq!(info.used + info.buffers + info.cached + info.free, info.total);
        assert_eq!(info.swap_total, 17_825_792 * 1024);
        assert_eq!(info.swap_used, (17_825_792 - 17_810_739) * 1024);
    }

    #[test]
    fn parse_meminfo_fallback_accounts_for_shmem_and_sreclaimable() {
        let sample = "\
MemTotal:       16000000 kB
MemFree:        10000000 kB
Buffers:          500000 kB
Cached:          3000000 kB
Shmem:            500000 kB
SReclaimable:     400000 kB
";
        let info = parse_meminfo(sample);
        // reclaimable_cache = 3000000 + 400000 - 500000 = 2900000 kB
        // available = 10000000 + 500000 + 2900000 = 13400000 kB
        // used = 16000000 - 13400000 = 2600000 kB
        assert_eq!(info.available, 13_400_000 * 1024);
        assert_eq!(info.used, 2_600_000 * 1024);
        assert_eq!(info.buffers, 500_000 * 1024);
        assert_eq!(info.cached, 2_900_000 * 1024);
        assert_eq!(info.free, 10_000_000 * 1024);
        assert_eq!(info.used + info.buffers + info.cached + info.free, info.total);
    }

    #[test]
    fn format_ram_total_shows_usable_and_installed_when_different() {
        assert_eq!(format_ram_total(15.42, None), "15.4 GiB");
        assert_eq!(format_ram_total(15.42, Some(16)), "15.4 / 16 GiB");
        assert_eq!(format_ram_total(15.98, Some(16)), "16.0 GiB");
    }

    #[test]
    fn format_dimm_summary_handles_uniform_and_mixed_modules() {
        let uniform = vec![
            DimmInfo { size_gb: 8, ..Default::default() },
            DimmInfo { size_gb: 8, ..Default::default() },
        ];
        assert_eq!(format_dimm_summary(&uniform).as_deref(), Some("2×8GB"));
        assert_eq!(installed_ram_gb(&uniform), Some(16));

        let mixed = vec![
            DimmInfo { size_gb: 8, ..Default::default() },
            DimmInfo { size_gb: 16, ..Default::default() },
        ];
        assert_eq!(format_dimm_summary(&mixed).as_deref(), Some("8GB + 16GB"));
        assert_eq!(installed_ram_gb(&mixed), Some(24));
    }

    #[test]
    fn format_usage_pair_handles_zero_tiny_mib_and_gib_values() {
        let total_17g = 17 * 1024 * 1024 * 1024;
        assert_eq!(format_usage_pair(0, total_17g), "0.0 / 17.0 GiB");
        assert_eq!(format_usage_pair(256 * 1024, total_17g), "256 KiB / 17.0 GiB");
        assert_eq!(format_usage_pair(1, total_17g), "1 KiB / 17.0 GiB");
        let used_14_7m = (14.7 * MIB) as u64;
        assert_eq!(format_usage_pair(used_14_7m, total_17g), "14.7 MiB / 17.0 GiB");
        let used_573_4m = (573.4 * MIB) as u64;
        let total_7_7g = (7.7 * GIB) as u64;
        assert_eq!(format_usage_pair(used_573_4m, total_7_7g), "573.4 MiB / 7.7 GiB");
        let used_2_5g = (2.5 * GIB) as u64;
        assert_eq!(format_usage_pair(used_2_5g, total_17g), "2.5 / 17.0 GiB");
        // Just below 1 GiB should roll over cleanly instead of displaying "1024.0 MiB"
        assert_eq!(
            format_usage_pair(1024 * 1024 * 1024 - 1024, total_17g),
            "1.0 / 17.0 GiB"
        );
    }
}
