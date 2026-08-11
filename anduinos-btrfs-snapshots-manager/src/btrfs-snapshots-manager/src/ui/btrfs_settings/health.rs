use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use super::filesystem::format_bytes;
use crate::dbus_client::{SmartDiskHealth, SmartHealthStatus, SnapshotsManagerHelperClient};
use crate::i18n::{tr, trf};

pub fn health_page(parent: &adw::PreferencesWindow) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("Disk Health"));
    page.set_icon_name(Some("emblem-ok-symbolic"));

    let summary = adw::PreferencesGroup::new();
    summary.set_title(&tr("System Drive Health"));
    summary.set_description(Some(&tr(
        "Only physical drives backing the current root Btrfs file system are shown.",
    )));
    let summary_row = adw::ActionRow::new();
    summary_row.set_title(&tr("Checking system storage…"));
    summary_row.set_subtitle(&tr("Reading the most important reliability indicators"));
    let summary_icon = gtk::Image::from_icon_name("content-loading-symbolic");
    summary_row.add_prefix(&summary_icon);
    summary.add(&summary_row);
    page.add(&summary);

    let weak_parent = parent.downgrade();
    let weak_page = page.downgrade();
    glib::spawn_future_local(async move {
        let result =
            gio::spawn_blocking(|| SnapshotsManagerHelperClient::new()?.get_smart_disk_health())
                .await
                .map_err(|_| anyhow::anyhow!("The S.M.A.R.T. query stopped unexpectedly"))
                .and_then(|result| result);
        if weak_parent.upgrade().is_none() {
            return;
        }
        let Some(page) = weak_page.upgrade() else {
            return;
        };
        match result {
            Ok(status) if status.available && !status.devices.is_empty() => {
                apply_summary(&summary_row, &summary_icon, &status);
                for disk in &status.devices {
                    page.add(&disk_group(disk));
                }
            }
            Ok(status) => set_unavailable(
                &summary_row,
                &summary_icon,
                status
                    .error
                    .as_deref()
                    .unwrap_or(&tr("No system storage device found")),
            ),
            Err(error) => set_unavailable(&summary_row, &summary_icon, &error.to_string()),
        }
    });
    page
}

fn apply_summary(row: &adw::ActionRow, icon: &gtk::Image, status: &SmartHealthStatus) {
    let healthy = status
        .devices
        .iter()
        .filter(|disk| disk.assessment == "healthy")
        .count();
    let attention = status.devices.len().saturating_sub(healthy);
    if attention == 0 {
        row.set_title(&if healthy == 1 {
            tr("The system drive looks healthy")
        } else {
            tr("All system drives look healthy")
        });
        row.set_subtitle(&tr("No important S.M.A.R.T. warning signs were detected"));
        set_summary_icon(icon, "emblem-ok-symbolic", "success");
    } else {
        row.set_title(&if status.devices.len() == 1 {
            tr("The system drive needs attention")
        } else {
            tr("Some system drives need attention")
        });
        row.set_subtitle(&trf(
            "Review the highlighted indicators on {0} system drive(s)",
            &[&attention.to_string()],
        ));
        set_summary_icon(icon, "dialog-warning-symbolic", "warning");
    }
}

fn set_unavailable(row: &adw::ActionRow, icon: &gtk::Image, message: &str) {
    row.set_title(&tr("Disk health is unavailable"));
    row.set_subtitle(message);
    set_summary_icon(icon, "dialog-warning-symbolic", "warning");
}

fn set_summary_icon(icon: &gtk::Image, icon_name: &str, style: &str) {
    icon.set_icon_name(Some(icon_name));
    icon.remove_css_class("success");
    icon.remove_css_class("warning");
    icon.add_css_class(style);
}

fn disk_group(disk: &SmartDiskHealth) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::new();
    let title = if disk.model.is_empty() {
        tr("Unknown storage device")
    } else {
        disk.model.clone()
    };
    group.set_title(&title);
    group.set_description(Some(&device_identity(disk)));

    let health = adw::ActionRow::new();
    health.set_title(&tr("Overall health"));
    health.set_subtitle(&assessment_detail(disk));
    let drive_icon = gtk::Image::from_icon_name(if is_solid_state(disk) {
        "media-flash-symbolic"
    } else {
        "drive-harddisk-symbolic"
    });
    drive_icon.set_pixel_size(32);
    health.add_prefix(&drive_icon);
    let (label, style) = assessment_label(&disk.assessment);
    let badge = gtk::Label::new(Some(&label));
    badge.add_css_class("pill");
    badge.add_css_class(style);
    badge.set_valign(gtk::Align::Center);
    health.add_suffix(&badge);
    group.add(&health);

    if let Some(temperature) = disk.temperature_celsius {
        group.add(&metric_row(
            &tr("Temperature"),
            &trf("{0} °C", &[&temperature.to_string()]),
            "weather-clear-symbolic",
            false,
        ));
    }
    if let Some(hours) = disk.power_on_hours {
        group.add(&metric_row(
            &tr("Powered on"),
            &trf(
                "{0} hours · about {1} days",
                &[&hours.to_string(), &(hours / 24).to_string()],
            ),
            "preferences-system-time-symbolic",
            false,
        ));
    }
    if let Some(cycles) = disk.power_cycles {
        group.add(&metric_row(
            &tr("Power cycles"),
            &cycles.to_string(),
            "system-shutdown-symbolic",
            false,
        ));
    }
    if let Some(used) = disk.lifetime_used_percent {
        group.add(&endurance_row(used));
    }
    if let Some(warning) = disk.critical_warning {
        group.add(&detail_row(
            &tr("NVMe critical warning"),
            &critical_warning_detail(warning),
            if warning == 0 {
                "emblem-ok-symbolic"
            } else {
                "dialog-error-symbolic"
            },
            warning != 0,
        ));
    }
    if let Some(spare) = disk.available_spare_percent {
        let value = disk
            .available_spare_threshold_percent
            .map(|threshold| {
                trf(
                    "{0}% · warning threshold {1}%",
                    &[&spare.to_string(), &threshold.to_string()],
                )
            })
            .unwrap_or_else(|| trf("{0}%", &[&spare.to_string()]));
        let warning = disk
            .available_spare_threshold_percent
            .is_some_and(|threshold| spare <= threshold);
        group.add(&metric_row(
            &tr("Available spare capacity"),
            &value,
            "battery-level-100-symbolic",
            warning,
        ));
    }
    if let Some(bytes) = disk.bytes_read {
        group.add(&metric_row(
            &tr("Total data read"),
            &format_decimal_bytes(bytes),
            "go-down-symbolic",
            false,
        ));
    }
    if let Some(bytes) = disk.bytes_written {
        group.add(&metric_row(
            &tr("Total data written"),
            &format_decimal_bytes(bytes),
            "go-up-symbolic",
            false,
        ));
    }
    if let Some(value) = disk.unsafe_shutdowns {
        group.add(&metric_row(
            &tr("Unsafe shutdowns"),
            &value.to_string(),
            "dialog-warning-symbolic",
            false,
        ));
    }
    if let Some(value) = disk.error_log_entries {
        group.add(&metric_row(
            &tr("Error log entries"),
            &value.to_string(),
            "view-list-symbolic",
            false,
        ));
    }
    if let Some(minutes) = disk.warning_temperature_minutes.filter(|value| *value > 0) {
        group.add(&metric_row(
            &tr("Time above warning temperature"),
            &trf("{0} minutes", &[&minutes.to_string()]),
            "weather-clear-symbolic",
            true,
        ));
    }
    if let Some(minutes) = disk.critical_temperature_minutes.filter(|value| *value > 0) {
        group.add(&metric_row(
            &tr("Time above critical temperature"),
            &trf("{0} minutes", &[&minutes.to_string()]),
            "dialog-error-symbolic",
            true,
        ));
    }
    for (title, value, icon, warning) in reliability_metrics(disk) {
        group.add(&metric_row(&title, &value.to_string(), icon, warning));
    }
    if disk.threshold_exceeded_in_past {
        let threshold_status = if disk.threshold_failing_now {
            tr("A critical threshold is failing now")
        } else {
            tr("A threshold was exceeded in the past")
        };
        group.add(&detail_row(
            &tr("Attribute thresholds"),
            &threshold_status,
            "dialog-warning-symbolic",
            true,
        ));
    }
    if let Some(error) = &disk.error {
        let row = adw::ActionRow::new();
        row.set_title(&tr("Device report"));
        row.set_subtitle(error);
        row.add_prefix(&gtk::Image::from_icon_name("dialog-warning-symbolic"));
        group.add(&row);
    }
    group
}

fn metric_row(title: &str, value: &str, icon: &str, warning: bool) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    let label = gtk::Label::new(Some(value));
    label.set_selectable(true);
    label.set_valign(gtk::Align::Center);
    if warning {
        label.add_css_class("warning");
    } else {
        label.add_css_class("dim-label");
    }
    row.add_suffix(&label);
    row
}

fn detail_row(title: &str, detail: &str, icon: &str, warning: bool) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.set_subtitle(detail);
    let image = gtk::Image::from_icon_name(icon);
    if warning {
        image.add_css_class("warning");
    }
    row.add_prefix(&image);
    row
}

fn endurance_row(used: u64) -> adw::ActionRow {
    let remaining = 100_u64.saturating_sub(used.min(100));
    let row = adw::ActionRow::new();
    row.set_title(&tr("Estimated SSD life remaining"));
    row.set_subtitle(&tr("Based on the manufacturer endurance counter"));
    row.add_prefix(&gtk::Image::from_icon_name("battery-level-100-symbolic"));
    let meter = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let progress = gtk::ProgressBar::new();
    progress.set_fraction(remaining as f64 / 100.0);
    progress.set_size_request(110, -1);
    progress.set_valign(gtk::Align::Center);
    if remaining <= 10 {
        progress.add_css_class("error");
    } else if remaining <= 20 {
        progress.add_css_class("warning");
    }
    let label = gtk::Label::new(Some(&trf("{0}%", &[&remaining.to_string()])));
    label.add_css_class("numeric");
    meter.append(&progress);
    meter.append(&label);
    row.add_suffix(&meter);
    row
}

fn reliability_metrics(disk: &SmartDiskHealth) -> Vec<(String, u64, &'static str, bool)> {
    let mut metrics = Vec::new();
    if let Some(value) = disk.media_errors {
        metrics.push((
            tr("Media errors"),
            value,
            "dialog-warning-symbolic",
            value > 0,
        ));
    }
    if let Some(value) = disk.reallocated_sectors {
        metrics.push((
            tr("Reallocated sectors"),
            value,
            "drive-harddisk-symbolic",
            value > 0,
        ));
    }
    if let Some(value) = disk.pending_sectors {
        metrics.push((
            tr("Pending sectors"),
            value,
            "hourglass-symbolic",
            value > 0,
        ));
    }
    if let Some(value) = disk.offline_uncorrectable {
        metrics.push((
            tr("Offline uncorrectable sectors"),
            value,
            "dialog-error-symbolic",
            value > 0,
        ));
    }
    if let Some(value) = disk.reported_uncorrectable {
        metrics.push((
            tr("Reported uncorrectable errors"),
            value,
            "dialog-error-symbolic",
            value > 0,
        ));
    }
    if let Some(value) = disk.interface_crc_errors {
        metrics.push((
            tr("Interface CRC errors"),
            value,
            "network-wired-symbolic",
            value > 0,
        ));
    }
    if let Some(value) = disk.spin_retry_count {
        metrics.push((
            tr("Spin retry count"),
            value,
            "media-playback-start-symbolic",
            value > 0,
        ));
    }
    metrics
}

fn device_identity(disk: &SmartDiskHealth) -> String {
    let mut parts = vec![disk.protocol.clone(), disk.device.clone()];
    match disk.rotation_rate_rpm {
        Some(0) => parts.push(tr("Solid-state drive")),
        Some(rpm) => parts.push(trf("{0} RPM", &[&rpm.to_string()])),
        None => {}
    }
    if let Some(capacity) = disk.capacity_bytes {
        parts.push(format_bytes(capacity));
    }
    parts.join(" · ")
}

fn assessment_label(assessment: &str) -> (String, &'static str) {
    match assessment {
        "healthy" => (tr("Healthy"), "success"),
        "warning" => (tr("Attention"), "warning"),
        "failing" => (tr("Failing"), "error"),
        "disabled" => (tr("Disabled"), "warning"),
        "unavailable" => (tr("Unavailable"), "warning"),
        _ => (tr("Unknown"), "dim-label"),
    }
}

fn assessment_detail(disk: &SmartDiskHealth) -> String {
    match disk.assessment.as_str() {
        "healthy" => tr("No important S.M.A.R.T. warning signs were detected"),
        "warning" => tr("One or more reliability indicators should be reviewed"),
        "failing" => tr("S.M.A.R.T. reports an imminent or current drive failure"),
        "disabled" => tr("S.M.A.R.T. is supported but disabled on this device"),
        "unavailable" if disk.error.is_some() => {
            tr("S.M.A.R.T. could not be read from this device")
        }
        "unavailable" if !disk.smart_available => tr("S.M.A.R.T. is not available for this device"),
        "unknown" if disk.smart_available && disk.smart_enabled => {
            tr("Only partial S.M.A.R.T. data was returned")
        }
        _ => tr("The drive did not provide a conclusive health result"),
    }
}

fn is_solid_state(disk: &SmartDiskHealth) -> bool {
    disk.protocol.eq_ignore_ascii_case("NVMe")
        || disk.rotation_rate_rpm == Some(0)
        || disk.lifetime_used_percent.is_some()
}

fn format_decimal_bytes(bytes: u64) -> String {
    const UNITS: [&str; 6] = ["B", "kB", "MB", "GB", "TB", "PB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1000.0 && unit < UNITS.len() - 1 {
        value /= 1000.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{bytes} {}", UNITS[unit])
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}

fn critical_warning_detail(warning: u64) -> String {
    if warning == 0 {
        return tr("None (0x00)");
    }
    let mut details = Vec::new();
    if warning & (1 << 0) != 0 {
        details.push(tr("Available spare is below its threshold"));
    }
    if warning & (1 << 1) != 0 {
        details.push(tr("Temperature is outside the safe range"));
    }
    if warning & (1 << 2) != 0 {
        details.push(tr("Device reliability is degraded"));
    }
    if warning & (1 << 3) != 0 {
        details.push(tr("Storage media is read-only"));
    }
    if warning & (1 << 4) != 0 {
        details.push(tr("Volatile memory backup failed"));
    }
    if warning & (1 << 5) != 0 {
        details.push(tr("Persistent memory is read-only"));
    }
    if warning & !0x3f != 0 {
        details.push(tr("The device reported an unknown warning bit"));
    }
    format!("0x{warning:02X} · {}", details.join(" · "))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn health_labels_are_stable_wire_mappings() {
        assert_eq!(assessment_label("healthy").0, tr("Healthy"));
        assert_eq!(assessment_label("failing").1, "error");
        assert_eq!(assessment_label("future-value").0, tr("Unknown"));
    }

    #[test]
    fn device_identity_contains_only_system_device_details() {
        let disk = SmartDiskHealth {
            device: "/dev/nvme0".into(),
            protocol: "NVMe".into(),
            capacity_bytes: Some(1024 * 1024 * 1024),
            ..SmartDiskHealth::default()
        };
        let identity = device_identity(&disk);
        assert_eq!(identity, "NVMe · /dev/nvme0 · 1.0 GiB");
    }

    #[test]
    fn formats_nvme_data_units_as_decimal_storage_totals() {
        assert_eq!(format_decimal_bytes(16_654_546_944_000), "16.7 TB");
        assert_eq!(format_decimal_bytes(999), "999 B");
    }

    #[test]
    fn decodes_nvme_critical_warning_bits() {
        assert_eq!(critical_warning_detail(0), tr("None (0x00)"));
        let detail = critical_warning_detail(0b1100);
        assert!(detail.contains("0x0C"));
        assert!(detail.contains(&tr("Device reliability is degraded")));
        assert!(detail.contains(&tr("Storage media is read-only")));
    }
}
