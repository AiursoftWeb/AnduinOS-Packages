use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use super::super::shared::show_result;
use super::{MaintenanceControl, query_btrfs_status};
use crate::dbus_client::{BtrfsFilesystemStatus, BtrfsScrubDetails, SnapshotsManagerHelperClient};
use crate::i18n::{tr, trf};
use snapshots_manager_common::format_bytes;

#[derive(Clone)]
struct ProgressWidgets {
    window: adw::Window,
    progress: gtk::ProgressBar,
    details: gtk::Label,
    errors: gtk::Label,
}

pub(super) fn connect(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |button| {
        if button.widget_name() == "scrub-monitor" {
            show_progress(&parent, &control, None, false);
        } else {
            start(&parent, &control);
        }
    });
}

pub(super) fn update_control(control: &MaintenanceControl, status: &str) {
    let running = status == "running";
    let subtitle = if running {
        tr("Running…")
    } else if status == "unavailable" {
        tr("Status unavailable")
    } else {
        tr("Ready to check")
    };
    control.row.set_subtitle(&subtitle);
    let label = if running {
        tr("View Progress")
    } else {
        tr("Start Scrub")
    };
    control.button.set_label(&label);
    control.button.set_widget_name(if running {
        "scrub-monitor"
    } else {
        "scrub-start"
    });
    control.button.set_sensitive(status != "unavailable");
}

fn start(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    control.button.set_sensitive(false);
    control.button.set_label(&tr("Starting…"));
    control
        .row
        .set_subtitle(&tr("Starting the integrity check…"));

    let weak_parent = parent.downgrade();
    let control = control.clone();
    glib::spawn_future_local(async move {
        // This marker exists only while the dialog is open. It prevents an old
        // completed result from being mistaken for the scrub just requested.
        let baseline_started_at = match query_btrfs_status().await {
            Ok(status) => status.scrub_details.started_at,
            Err(error) => {
                if let Some(parent) = weak_parent.upgrade() {
                    update_control(&control, "unavailable");
                    show_result(&parent, Err(error));
                }
                return;
            }
        };
        let Some(parent) = weak_parent.upgrade() else {
            return;
        };
        update_control(&control, "running");
        let progress_window =
            show_progress(&parent, &control, baseline_started_at.as_deref(), true);
        let result = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new()?.run_btrfs_maintenance_action("scrub-start")
        })
        .await
        .map_err(|_| anyhow::anyhow!("The maintenance operation stopped unexpectedly"))
        .and_then(|result| result);
        if let Err(error) = result {
            progress_window.close();
            update_control(&control, "ready");
            show_result(&parent, Err(error));
        }
    });
}

fn show_progress(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    baseline_started_at: Option<&str>,
    wait_for_new_run: bool,
) -> adw::Window {
    let window = adw::Window::builder()
        .transient_for(parent)
        .modal(true)
        .deletable(false)
        .resizable(false)
        .default_width(440)
        .default_height(240)
        .title(tr("Checking File System Integrity"))
        .build();
    let content = gtk::Box::new(gtk::Orientation::Vertical, 14);
    content.set_margin_top(24);
    content.set_margin_bottom(24);
    content.set_margin_start(28);
    content.set_margin_end(28);

    let heading = gtk::Label::new(Some(&tr("Scanning data and metadata…")));
    heading.add_css_class("heading");
    heading.set_wrap(true);
    let progress = gtk::ProgressBar::new();
    progress.set_hexpand(true);
    progress.set_show_text(true);
    progress.set_text(Some(&tr("Starting…")));
    progress.pulse();
    let details = gtk::Label::new(Some(&tr("Reading allocated Btrfs data and metadata…")));
    details.set_wrap(true);
    details.set_justify(gtk::Justification::Center);
    details.add_css_class("dim-label");
    let errors = gtk::Label::new(Some(&tr("No errors detected so far")));
    errors.set_wrap(true);
    errors.set_justify(gtk::Justification::Center);
    let cancel = gtk::Button::with_label(&tr("Cancel Check"));
    cancel.set_halign(gtk::Align::Center);

    content.append(&heading);
    content.append(&progress);
    content.append(&details);
    content.append(&errors);
    content.append(&cancel);
    window.set_content(Some(&content));
    window.present();

    let widgets = ProgressWidgets {
        window,
        progress,
        details,
        errors,
    };
    connect_cancel(parent, &widgets.window, &cancel);
    monitor(
        parent,
        control,
        &widgets,
        baseline_started_at.map(str::to_string),
        wait_for_new_run,
    );
    widgets.window
}

fn connect_cancel(parent: &adw::PreferencesWindow, window: &adw::Window, cancel: &gtk::Button) {
    let weak_parent = parent.downgrade();
    let weak_window = window.downgrade();
    cancel.connect_clicked(move |button| {
        button.set_sensitive(false);
        button.set_label(&tr("Cancelling…"));
        let button = button.clone();
        let weak_parent = weak_parent.clone();
        let weak_window = weak_window.clone();
        glib::spawn_future_local(async move {
            let result = gio::spawn_blocking(|| {
                SnapshotsManagerHelperClient::new()?.run_btrfs_maintenance_action("scrub-cancel")
            })
            .await
            .map_err(|_| anyhow::anyhow!("The maintenance operation stopped unexpectedly"))
            .and_then(|result| result);
            if let Err(error) = result
                && weak_window.upgrade().is_some()
            {
                button.set_label(&tr("Cancel Check"));
                button.set_sensitive(true);
                if let Some(parent) = weak_parent.upgrade() {
                    show_result(&parent, Err(error));
                }
            }
        });
    });
}

fn monitor(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    widgets: &ProgressWidgets,
    baseline_started_at: Option<String>,
    wait_for_new_run: bool,
) {
    let weak_parent = parent.downgrade();
    let weak_window = widgets.window.downgrade();
    let control = control.clone();
    let progress = widgets.progress.clone();
    let details = widgets.details.clone();
    let errors = widgets.errors.clone();
    glib::spawn_future_local(async move {
        let mut failed_queries = 0_u8;
        let mut startup_queries = 0_u8;
        let mut current_run_observed = !wait_for_new_run;
        loop {
            if weak_window.upgrade().is_none() {
                return;
            }
            let result = query_btrfs_status().await;
            match result {
                Ok(status) if status.scrub != "unavailable" => {
                    failed_queries = 0;
                    if status_is_current(&status, baseline_started_at.as_deref()) {
                        current_run_observed = true;
                    }

                    if !current_run_observed {
                        startup_queries = startup_queries.saturating_add(1);
                        progress.pulse();
                        progress.set_text(Some(&tr("Starting…")));
                        details.set_text(&tr("Waiting for the new scrub to start…"));
                        if startup_queries >= 15 {
                            if let Some(window) = weak_window.upgrade() {
                                window.close();
                            }
                            if let Some(parent) = weak_parent.upgrade() {
                                update_control(&control, "ready");
                                show_result(
                                    &parent,
                                    Err(anyhow::anyhow!(tr(
                                        "Btrfs did not start a new integrity check"
                                    ))),
                                );
                            }
                            return;
                        }
                        glib::timeout_future_seconds(1).await;
                        continue;
                    }

                    update_progress(&progress, &details, &errors, &status.scrub_details);
                    update_control(&control, "running");
                    if status.scrub != "running" {
                        if let Some(window) = weak_window.upgrade() {
                            window.close();
                        }
                        if let Some(parent) = weak_parent.upgrade() {
                            update_control(&control, "ready");
                            show_result_dialog(&parent, &status);
                        }
                        return;
                    }
                }
                Ok(status) => {
                    failed_queries = failed_queries.saturating_add(1);
                    let message = status
                        .error
                        .unwrap_or_else(|| tr("Waiting for scrub status…"));
                    details.set_text(&message);
                    progress.pulse();
                }
                Err(error) => {
                    failed_queries = failed_queries.saturating_add(1);
                    details.set_text(&tr("Waiting for scrub status…"));
                    progress.pulse();
                    if failed_queries >= 3 {
                        if let Some(window) = weak_window.upgrade() {
                            window.close();
                        }
                        if let Some(parent) = weak_parent.upgrade() {
                            update_control(&control, "unavailable");
                            show_result(&parent, Err(error));
                        }
                        return;
                    }
                }
            }
            if failed_queries >= 3 {
                if let Some(window) = weak_window.upgrade() {
                    window.close();
                }
                if let Some(parent) = weak_parent.upgrade() {
                    update_control(&control, "unavailable");
                    show_result(
                        &parent,
                        Err(anyhow::anyhow!("Btrfs scrub status is unavailable")),
                    );
                }
                return;
            }
            glib::timeout_future_seconds(1).await;
        }
    });
}

fn status_is_current(status: &BtrfsFilesystemStatus, baseline_started_at: Option<&str>) -> bool {
    status.scrub == "running"
        || status
            .scrub_details
            .started_at
            .as_deref()
            .is_some_and(|started_at| Some(started_at) != baseline_started_at)
}

fn update_progress(
    progress: &gtk::ProgressBar,
    details: &gtk::Label,
    errors: &gtk::Label,
    scrub: &BtrfsScrubDetails,
) {
    if let (Some(checked), Some(total)) = (scrub.bytes_scrubbed, scrub.total_bytes)
        && total > 0
    {
        let fraction = (checked as f64 / total as f64).clamp(0.0, 1.0);
        progress.set_fraction(fraction);
        progress.set_text(Some(&trf(
            "{0}% complete",
            &[&format!("{:.0}", fraction * 100.0)],
        )));
        let checked = format_bytes(checked);
        let total = format_bytes(total);
        details.set_text(&trf("{0} of {1} checked", &[&checked, &total]));
    } else {
        progress.pulse();
        progress.set_text(Some(&tr("Checking…")));
        details.set_text(&tr("Reading allocated Btrfs data and metadata…"));
    }

    let mut secondary = Vec::new();
    if let Some(rate) = scrub.rate_bytes_per_second.filter(|rate| *rate > 0) {
        secondary.push(trf("{0}/s", &[&format_bytes(rate)]));
    }
    if let Some(time_left) = scrub.time_left.as_deref().filter(|time| *time != "0:00:00") {
        secondary.push(trf("About {0} remaining", &[time_left]));
    }
    if !secondary.is_empty() {
        let primary = details.text();
        details.set_text(&format!("{primary}\n{}", secondary.join(" · ")));
    }

    let detected = scrub
        .read_errors
        .saturating_add(scrub.checksum_errors)
        .saturating_add(scrub.verify_errors)
        .saturating_add(scrub.superblock_errors)
        .saturating_add(scrub.uncorrectable_errors)
        .saturating_add(scrub.unverified_errors);
    let error_text = if detected == 0 {
        tr("No errors detected so far")
    } else {
        trf("Errors detected so far: {0}", &[&detected.to_string()])
    };
    errors.set_text(&error_text);
}

fn show_result_dialog(parent: &adw::PreferencesWindow, status: &BtrfsFilesystemStatus) {
    let (heading, body) = result_presentation(status);
    let dialog = adw::MessageDialog::new(Some(parent), Some(&heading), Some(&body));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}

fn result_presentation(status: &BtrfsFilesystemStatus) -> (String, String) {
    let scrub = &status.scrub_details;
    let (heading, result) = match status.scrub.as_str() {
        "finished-clean" => (
            tr("Integrity Check Complete"),
            tr("No file system integrity errors were found in allocated data and metadata."),
        ),
        value if value.starts_with("finished-repaired:") => (
            tr("Integrity Check Complete — Repairs Made"),
            trf(
                "Btrfs repaired {0} damaged copies using valid redundant data.",
                &[&scrub.corrected_errors.to_string()],
            ),
        ),
        value if value.starts_with("finished-with-errors:") => (
            tr("Integrity Problems Found"),
            tr(
                "Btrfs found errors that could not be repaired. Back up important files and investigate the storage device.",
            ),
        ),
        "cancelled" => (
            tr("Integrity Check Cancelled"),
            tr("The integrity check was cancelled before it finished."),
        ),
        _ => (
            tr("Integrity Check Result Unavailable"),
            tr("Btrfs did not provide a completed scrub result."),
        ),
    };

    let mut lines = vec![result];
    if let Some(checked) = scrub.bytes_scrubbed.or(scrub.total_bytes) {
        lines.push(trf("Checked: {0}", &[&format_bytes(checked)]));
    }
    if let Some(duration) = scrub.duration.as_deref() {
        lines.push(trf("Duration: {0}", &[duration]));
    }
    if let Some(rate) = scrub.rate_bytes_per_second.filter(|rate| *rate > 0) {
        lines.push(trf("Average rate: {0}/s", &[&format_bytes(rate)]));
    }
    lines.push(String::new());
    lines.push(tr("Diagnostic counters"));
    lines.push(trf("Read errors: {0}", &[&scrub.read_errors.to_string()]));
    lines.push(trf(
        "Checksum errors: {0}",
        &[&scrub.checksum_errors.to_string()],
    ));
    lines.push(trf(
        "Verification errors: {0}",
        &[&scrub.verify_errors.to_string()],
    ));
    lines.push(trf(
        "Superblock errors: {0}",
        &[&scrub.superblock_errors.to_string()],
    ));
    lines.push(trf(
        "Corrected errors: {0}",
        &[&scrub.corrected_errors.to_string()],
    ));
    lines.push(trf(
        "Uncorrectable errors: {0}",
        &[&scrub.uncorrectable_errors.to_string()],
    ));
    lines.push(trf(
        "Unverified errors: {0}",
        &[&scrub.unverified_errors.to_string()],
    ));
    lines.push(String::new());
    lines.push(tr("Scrub verifies allocated Btrfs data and metadata. It does not test unused space or predict sudden drive failure."));
    (heading, lines.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ignores_a_completed_record_from_before_the_new_scrub() {
        let old = BtrfsFilesystemStatus {
            scrub: "finished-clean".into(),
            scrub_details: BtrfsScrubDetails {
                started_at: Some("Mon Aug 10 03:55:45 2026".into()),
                ..BtrfsScrubDetails::default()
            },
            ..BtrfsFilesystemStatus::default()
        };
        assert!(!status_is_current(&old, Some("Mon Aug 10 03:55:45 2026")));

        let running = BtrfsFilesystemStatus {
            scrub: "running".into(),
            ..old.clone()
        };
        assert!(status_is_current(
            &running,
            Some("Mon Aug 10 03:55:45 2026")
        ));

        let newly_finished = BtrfsFilesystemStatus {
            scrub_details: BtrfsScrubDetails {
                started_at: Some("Mon Aug 10 04:32:27 2026".into()),
                ..old.scrub_details.clone()
            },
            ..old
        };
        assert!(status_is_current(
            &newly_finished,
            Some("Mon Aug 10 03:55:45 2026")
        ));
    }

    #[test]
    fn completed_result_reports_scope_and_counters() {
        let status = BtrfsFilesystemStatus {
            scrub: "finished-clean".into(),
            scrub_details: BtrfsScrubDetails {
                duration: Some("0:00:36".into()),
                total_bytes: Some(98_885_677_056),
                rate_bytes_per_second: Some(2_692_178_329),
                ..BtrfsScrubDetails::default()
            },
            ..BtrfsFilesystemStatus::default()
        };
        let (heading, body) = result_presentation(&status);
        assert_eq!(heading, tr("Integrity Check Complete"));
        assert!(body.contains(&tr(
            "No file system integrity errors were found in allocated data and metadata."
        )));
        assert!(body.contains(&trf("Read errors: {0}", &["0"])));
        assert!(body.contains(&trf("Uncorrectable errors: {0}", &["0"])));
    }

    #[test]
    fn failed_result_has_an_actionable_warning() {
        let status = BtrfsFilesystemStatus {
            scrub: "finished-with-errors:2".into(),
            scrub_details: BtrfsScrubDetails {
                checksum_errors: 2,
                uncorrectable_errors: 2,
                ..BtrfsScrubDetails::default()
            },
            ..BtrfsFilesystemStatus::default()
        };
        let (heading, body) = result_presentation(&status);
        assert_eq!(heading, tr("Integrity Problems Found"));
        assert!(body.contains(&tr("Btrfs found errors that could not be repaired. Back up important files and investigate the storage device.")));
        assert!(body.contains(&trf("Checksum errors: {0}", &["2"])));
    }
}
