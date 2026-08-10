use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use super::shared::{confirmation, show_result};
use crate::dbus_client::{
    BtrfsBalanceDetails, BtrfsDefragDetails, BtrfsFilesystemStatus, BtrfsScrubDetails,
    SnapshotsManagerHelperClient,
};
use crate::i18n::{tr, trf};
use snapshots_manager_common::{format_bytes, format_elapsed_time};

#[derive(Clone)]
struct MaintenanceControl {
    row: adw::ActionRow,
    button: gtk::Button,
}

#[derive(Clone)]
struct MaintenanceControls {
    scrub: MaintenanceControl,
    balance: MaintenanceControl,
    defrag: MaintenanceControl,
}

#[derive(Clone)]
struct ScrubProgressWidgets {
    window: adw::Window,
    progress: gtk::ProgressBar,
    details: gtk::Label,
    errors: gtk::Label,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ManagedMaintenance {
    Balance,
    Defrag,
}

#[derive(Clone)]
struct ManagedProgressWidgets {
    window: adw::Window,
    progress: gtk::ProgressBar,
    details: gtk::Label,
}

impl ManagedMaintenance {
    fn start_action(self) -> &'static str {
        match self {
            Self::Balance => "balance-start",
            Self::Defrag => "defrag-home",
        }
    }

    fn cancel_action(self) -> &'static str {
        match self {
            Self::Balance => "balance-cancel",
            Self::Defrag => "defrag-home-cancel",
        }
    }

    fn status(self, status: &BtrfsFilesystemStatus) -> &str {
        match self {
            Self::Balance => &status.balance,
            Self::Defrag => &status.defrag,
        }
    }

    fn generation(self, status: &BtrfsFilesystemStatus) -> u64 {
        match self {
            Self::Balance => status.balance_details.generation,
            Self::Defrag => status.defrag_details.generation,
        }
    }

    fn title(self) -> &'static str {
        match self {
            Self::Balance => "Optimizing Space Allocation",
            Self::Defrag => "Defragmenting Home Files",
        }
    }

    fn heading(self) -> &'static str {
        match self {
            Self::Balance => "Relocating underused block groups…",
            Self::Defrag => "Rewriting Home file extents…",
        }
    }

    fn cancel_label(self) -> &'static str {
        match self {
            Self::Balance => "Cancel Balance",
            Self::Defrag => "Cancel Defragmentation",
        }
    }
}

pub fn maintenance_page(parent: &adw::PreferencesWindow) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("Maintenance"));
    page.set_icon_name(Some("emblem-system-symbolic"));

    let (health, scrub) = maintenance_group(
        &tr("Integrity"),
        &tr(
            "Scrub reads allocated data and metadata, verifies checksums, and reports damage without modifying file data.",
        ),
        &tr("Check file system integrity"),
        &tr("Recommended about once a month"),
        "security-high-symbolic",
        &tr("Start Scrub"),
    );
    scrub.button.set_widget_name("scrub-start");
    scrub.button.add_css_class("suggested-action");
    page.add(&health);

    let (allocation, balance) = maintenance_group(
        &tr("Space Allocation"),
        &tr(
            "A limited balance only relocates data and metadata block groups that are at most 50% full.",
        ),
        &tr("Reclaim underused block groups"),
        &tr("Useful after deleting large amounts of data"),
        "drive-harddisk-symbolic",
        &tr("Start Balance"),
    );
    balance.button.set_widget_name("balance-start");
    page.add(&allocation);

    let (files, defrag) = maintenance_group(
        &tr("File Layout"),
        &tr(
            "Defragmentation rewrites file extents and can increase disk usage by breaking shared snapshot or reflink data.",
        ),
        &tr("Defragment Home files"),
        &tr("Only /home · snapshot storage is excluded"),
        "dialog-warning-symbolic",
        &tr("Defragment…"),
    );
    defrag.button.add_css_class("destructive-action");
    page.add(&files);

    let controls = MaintenanceControls {
        scrub,
        balance,
        defrag,
    };
    connect_scrub(parent, &controls.scrub);
    connect_balance(parent, &controls.balance);
    connect_defrag(parent, &controls.defrag);
    for control in [&controls.scrub, &controls.balance, &controls.defrag] {
        control.button.set_sensitive(false);
    }
    refresh_maintenance(parent, &controls);
    page
}

fn maintenance_group(
    group_title: &str,
    description: &str,
    row_title: &str,
    row_subtitle: &str,
    icon: &str,
    button_label: &str,
) -> (adw::PreferencesGroup, MaintenanceControl) {
    let group = adw::PreferencesGroup::new();
    group.set_title(group_title);
    group.set_description(Some(description));
    let row = adw::ActionRow::new();
    row.set_title(row_title);
    row.set_subtitle(row_subtitle);
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    let button = gtk::Button::with_label(button_label);
    button.set_valign(gtk::Align::Center);
    row.add_suffix(&button);
    group.add(&row);
    (group, MaintenanceControl { row, button })
}

fn connect_scrub(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |button| {
        if button.widget_name() == "scrub-monitor" {
            show_scrub_progress(&parent, &control, None, false);
        } else {
            start_scrub(&parent, &control);
        }
    });
}

fn connect_balance(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |button| {
        if button.widget_name() == "balance-monitor" {
            show_managed_progress(&parent, &control, ManagedMaintenance::Balance, None, false);
            return;
        }
        let dialog = confirmation(
            &parent,
            &tr("Start a limited balance?"),
            &tr("Only block groups at most 50% full will be relocated. The operation can use significant disk bandwidth but can be cancelled safely."),
            &tr("Start Balance"),
            false,
        );
        let parent = parent.clone();
        let control = control.clone();
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response == "run" {
                start_managed_maintenance(
                    &parent,
                    &control,
                    ManagedMaintenance::Balance,
                );
            }
        });
        dialog.present();
    });
}

fn connect_defrag(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |button| {
        if button.widget_name() == "defrag-monitor" {
            show_managed_progress(&parent, &control, ManagedMaintenance::Defrag, None, false);
            return;
        }
        let dialog = confirmation(
            &parent,
            &tr("Defragment Home files?"),
            &tr("This rewrites files below /home using ZSTD compression. It does not enter /.snapshots, but shared extents with existing snapshots may become private and consume more space."),
            &tr("Defragment"),
            true,
        );
        let parent = parent.clone();
        let control = control.clone();
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response == "run" {
                start_managed_maintenance(&parent, &control, ManagedMaintenance::Defrag);
            }
        });
        dialog.present();
    });
}

fn refresh_maintenance(parent: &adw::PreferencesWindow, controls: &MaintenanceControls) {
    let weak_parent = parent.downgrade();
    let controls = controls.clone();
    glib::spawn_future_local(async move {
        let status = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new()?.get_btrfs_filesystem_status()
        })
        .await
        .ok()
        .and_then(Result::ok);
        if weak_parent.upgrade().is_none() {
            return;
        }
        let Some(status) = status else {
            set_status_unavailable(&controls);
            return;
        };
        if !status.available {
            set_status_unavailable(&controls);
            return;
        }
        update_scrub_control(&controls.scrub, &status.scrub);
        update_managed_control(
            &controls.balance,
            &status.balance,
            ManagedMaintenance::Balance,
        );
        update_managed_control(&controls.defrag, &status.defrag, ManagedMaintenance::Defrag);
    });
}

fn set_status_unavailable(controls: &MaintenanceControls) {
    controls.scrub.row.set_subtitle(&tr("Status unavailable"));
    controls.balance.row.set_subtitle(&tr("Status unavailable"));
    controls.defrag.row.set_subtitle(&tr("Status unavailable"));
}

fn update_managed_control(
    control: &MaintenanceControl,
    status: &str,
    operation: ManagedMaintenance,
) {
    let running = matches!(status, "starting" | "running" | "paused" | "cancelling");
    let subtitle = if running {
        match status {
            "starting" => tr("Starting…"),
            "paused" => tr("Paused"),
            "cancelling" => tr("Cancelling…"),
            _ => tr("Running…"),
        }
    } else {
        match operation {
            ManagedMaintenance::Balance => tr("Ready to optimize allocation"),
            ManagedMaintenance::Defrag => tr("Ready to defragment Home files"),
        }
    };
    control.row.set_subtitle(&subtitle);
    if running {
        control.button.set_label(&tr("View Progress"));
    } else {
        control.button.set_label(&tr(match operation {
            ManagedMaintenance::Balance => "Start Balance",
            ManagedMaintenance::Defrag => "Defragment…",
        }));
    }
    control.button.set_widget_name(match (operation, running) {
        (ManagedMaintenance::Balance, true) => "balance-monitor",
        (ManagedMaintenance::Balance, false) => "balance-start",
        (ManagedMaintenance::Defrag, true) => "defrag-monitor",
        (ManagedMaintenance::Defrag, false) => "defrag-start",
    });
    control.button.set_sensitive(status != "unavailable");
}

fn update_scrub_control(control: &MaintenanceControl, status: &str) {
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

fn start_scrub(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    control.button.set_sensitive(false);
    control.button.set_label(&tr("Starting…"));
    control
        .row
        .set_subtitle(&tr("Starting the integrity check…"));

    let weak_parent = parent.downgrade();
    let control = control.clone();
    glib::spawn_future_local(async move {
        // Capture only an in-memory marker for the currently displayed task.
        // It prevents a completed record from an earlier scrub from closing
        // the new progress dialog. Nothing is persisted by the application.
        let baseline_started_at = match query_btrfs_status().await {
            Ok(status) => status.scrub_details.started_at,
            Err(error) => {
                if let Some(parent) = weak_parent.upgrade() {
                    update_scrub_control(&control, "unavailable");
                    show_result(&parent, Err(error));
                }
                return;
            }
        };
        let Some(parent) = weak_parent.upgrade() else {
            return;
        };
        update_scrub_control(&control, "running");
        let progress_window =
            show_scrub_progress(&parent, &control, baseline_started_at.as_deref(), true);
        let result = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new()?.run_btrfs_maintenance_action("scrub-start")
        })
        .await
        .map_err(|_| anyhow::anyhow!("The maintenance operation stopped unexpectedly"))
        .and_then(|result| result);
        match result {
            Ok(_) => {}
            Err(error) => {
                progress_window.close();
                update_scrub_control(&control, "ready");
                show_result(&parent, Err(error));
            }
        }
    });
}

fn show_scrub_progress(
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

    let widgets = ScrubProgressWidgets {
        window,
        progress,
        details,
        errors,
    };
    connect_scrub_cancel(parent, &widgets.window, &cancel);
    monitor_scrub(
        parent,
        control,
        &widgets,
        baseline_started_at.map(str::to_string),
        wait_for_new_run,
    );
    widgets.window
}

fn connect_scrub_cancel(
    parent: &adw::PreferencesWindow,
    window: &adw::Window,
    cancel: &gtk::Button,
) {
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

fn monitor_scrub(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    widgets: &ScrubProgressWidgets,
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
                    if scrub_status_is_current(&status, baseline_started_at.as_deref()) {
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
                                update_scrub_control(&control, "ready");
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

                    update_scrub_progress(&progress, &details, &errors, &status.scrub_details);
                    update_scrub_control(&control, "running");
                    if status.scrub != "running" {
                        if let Some(window) = weak_window.upgrade() {
                            window.close();
                        }
                        if let Some(parent) = weak_parent.upgrade() {
                            update_scrub_control(&control, "ready");
                            show_scrub_result(&parent, &status);
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
                            update_scrub_control(&control, "unavailable");
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
                    update_scrub_control(&control, "unavailable");
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

async fn query_btrfs_status() -> anyhow::Result<BtrfsFilesystemStatus> {
    gio::spawn_blocking(|| SnapshotsManagerHelperClient::new()?.get_btrfs_filesystem_status())
        .await
        .map_err(|_| anyhow::anyhow!("The Btrfs status query stopped unexpectedly"))
        .and_then(|result| result)
}

fn scrub_status_is_current(
    status: &BtrfsFilesystemStatus,
    baseline_started_at: Option<&str>,
) -> bool {
    status.scrub == "running"
        || status
            .scrub_details
            .started_at
            .as_deref()
            .is_some_and(|started_at| Some(started_at) != baseline_started_at)
}

fn update_scrub_progress(
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

fn show_scrub_result(parent: &adw::PreferencesWindow, status: &BtrfsFilesystemStatus) {
    let (heading, body) = scrub_result_presentation(status);
    let dialog = adw::MessageDialog::new(Some(parent), Some(&heading), Some(&body));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}

fn scrub_result_presentation(status: &BtrfsFilesystemStatus) -> (String, String) {
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

fn start_managed_maintenance(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    operation: ManagedMaintenance,
) {
    update_managed_control(control, "starting", operation);
    control.button.set_sensitive(false);

    let weak_parent = parent.downgrade();
    let control = control.clone();
    glib::spawn_future_local(async move {
        let baseline_generation = match query_btrfs_status().await {
            Ok(status) => operation.generation(&status),
            Err(error) => {
                if let Some(parent) = weak_parent.upgrade() {
                    update_managed_control(&control, "unavailable", operation);
                    show_result(&parent, Err(error));
                }
                return;
            }
        };
        let Some(parent) = weak_parent.upgrade() else {
            return;
        };
        update_managed_control(&control, "running", operation);
        let progress_window = show_managed_progress(
            &parent,
            &control,
            operation,
            Some(baseline_generation),
            true,
        );
        let action = operation.start_action();
        let result = gio::spawn_blocking(move || {
            SnapshotsManagerHelperClient::new()?.run_btrfs_maintenance_action(action)
        })
        .await
        .map_err(|_| anyhow::anyhow!("The maintenance operation stopped unexpectedly"))
        .and_then(|result| result);
        if let Err(error) = result {
            progress_window.close();
            update_managed_control(&control, "idle", operation);
            show_result(&parent, Err(error));
        }
    });
}

fn show_managed_progress(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    operation: ManagedMaintenance,
    baseline_generation: Option<u64>,
    wait_for_new_run: bool,
) -> adw::Window {
    let window = adw::Window::builder()
        .transient_for(parent)
        .modal(true)
        .deletable(false)
        .resizable(false)
        .default_width(440)
        .default_height(220)
        .title(tr(operation.title()))
        .build();
    let content = gtk::Box::new(gtk::Orientation::Vertical, 14);
    content.set_margin_top(24);
    content.set_margin_bottom(24);
    content.set_margin_start(28);
    content.set_margin_end(28);

    let heading = gtk::Label::new(Some(&tr(operation.heading())));
    heading.add_css_class("heading");
    heading.set_wrap(true);
    let progress = gtk::ProgressBar::new();
    progress.set_hexpand(true);
    progress.set_show_text(true);
    progress.set_text(Some(&tr("Starting…")));
    progress.pulse();
    let details = gtk::Label::new(Some(&tr("Waiting for Btrfs to start…")));
    details.set_wrap(true);
    details.set_justify(gtk::Justification::Center);
    details.add_css_class("dim-label");
    let cancel = gtk::Button::with_label(&tr(operation.cancel_label()));
    cancel.set_halign(gtk::Align::Center);

    content.append(&heading);
    content.append(&progress);
    content.append(&details);
    content.append(&cancel);
    window.set_content(Some(&content));
    window.present();

    let widgets = ManagedProgressWidgets {
        window,
        progress,
        details,
    };
    connect_managed_cancel(parent, &widgets.window, &cancel, operation);
    monitor_managed_maintenance(
        parent,
        control,
        &widgets,
        operation,
        baseline_generation,
        wait_for_new_run,
    );
    widgets.window
}

fn connect_managed_cancel(
    parent: &adw::PreferencesWindow,
    window: &adw::Window,
    cancel: &gtk::Button,
    operation: ManagedMaintenance,
) {
    let weak_parent = parent.downgrade();
    let weak_window = window.downgrade();
    cancel.connect_clicked(move |button| {
        button.set_sensitive(false);
        button.set_label(&tr("Cancelling…"));
        let button = button.clone();
        let weak_parent = weak_parent.clone();
        let weak_window = weak_window.clone();
        glib::spawn_future_local(async move {
            let action = operation.cancel_action();
            let result = gio::spawn_blocking(move || {
                SnapshotsManagerHelperClient::new()?.run_btrfs_maintenance_action(action)
            })
            .await
            .map_err(|_| anyhow::anyhow!("The maintenance operation stopped unexpectedly"))
            .and_then(|result| result);
            if let Err(error) = result
                && weak_window.upgrade().is_some()
            {
                button.set_label(&tr(operation.cancel_label()));
                button.set_sensitive(true);
                if let Some(parent) = weak_parent.upgrade() {
                    show_result(&parent, Err(error));
                }
            }
        });
    });
}

fn monitor_managed_maintenance(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    widgets: &ManagedProgressWidgets,
    operation: ManagedMaintenance,
    baseline_generation: Option<u64>,
    wait_for_new_run: bool,
) {
    let weak_parent = parent.downgrade();
    let weak_window = widgets.window.downgrade();
    let control = control.clone();
    let progress = widgets.progress.clone();
    let details = widgets.details.clone();
    glib::spawn_future_local(async move {
        let mut failed_queries = 0_u8;
        let mut startup_queries = 0_u8;
        let mut current_run_observed = !wait_for_new_run;
        loop {
            if weak_window.upgrade().is_none() {
                return;
            }
            match query_btrfs_status().await {
                Ok(status) if operation.status(&status) != "unavailable" => {
                    failed_queries = 0;
                    let task_status = operation.status(&status);
                    let generation = operation.generation(&status);
                    if managed_status_is_active(task_status)
                        || baseline_generation.is_some_and(|baseline| generation != baseline)
                    {
                        current_run_observed = true;
                    }

                    if !current_run_observed {
                        startup_queries = startup_queries.saturating_add(1);
                        progress.pulse();
                        progress.set_text(Some(&tr("Starting…")));
                        details.set_text(&tr("Waiting for Btrfs to start…"));
                        if startup_queries >= 15 {
                            close_managed_with_error(
                                &weak_parent,
                                &weak_window,
                                &control,
                                operation,
                                anyhow::anyhow!(tr("Btrfs did not start a new maintenance task")),
                            );
                            return;
                        }
                        glib::timeout_future_seconds(1).await;
                        continue;
                    }

                    update_managed_progress(&progress, &details, &status, operation);
                    update_managed_control(&control, task_status, operation);
                    if !managed_status_is_active(task_status) {
                        if let Some(window) = weak_window.upgrade() {
                            window.close();
                        }
                        if let Some(parent) = weak_parent.upgrade() {
                            update_managed_control(&control, "idle", operation);
                            show_managed_result(&parent, &status, operation);
                        }
                        return;
                    }
                }
                Ok(status) => {
                    failed_queries = failed_queries.saturating_add(1);
                    details.set_text(
                        &status
                            .error
                            .unwrap_or_else(|| tr("Waiting for Btrfs status…")),
                    );
                    progress.pulse();
                }
                Err(error) => {
                    failed_queries = failed_queries.saturating_add(1);
                    details.set_text(&tr("Waiting for Btrfs status…"));
                    progress.pulse();
                    if failed_queries >= 3 {
                        close_managed_with_error(
                            &weak_parent,
                            &weak_window,
                            &control,
                            operation,
                            error,
                        );
                        return;
                    }
                }
            }
            if failed_queries >= 3 {
                close_managed_with_error(
                    &weak_parent,
                    &weak_window,
                    &control,
                    operation,
                    anyhow::anyhow!(tr("Btrfs maintenance status is unavailable")),
                );
                return;
            }
            glib::timeout_future_seconds(1).await;
        }
    });
}

fn managed_status_is_active(status: &str) -> bool {
    matches!(status, "starting" | "running" | "paused" | "cancelling")
}

fn close_managed_with_error(
    weak_parent: &glib::WeakRef<adw::PreferencesWindow>,
    weak_window: &glib::WeakRef<adw::Window>,
    control: &MaintenanceControl,
    operation: ManagedMaintenance,
    error: anyhow::Error,
) {
    if let Some(window) = weak_window.upgrade() {
        window.close();
    }
    if let Some(parent) = weak_parent.upgrade() {
        update_managed_control(control, "unavailable", operation);
        show_result(&parent, Err(error));
    }
}

fn update_managed_progress(
    progress: &gtk::ProgressBar,
    details: &gtk::Label,
    status: &BtrfsFilesystemStatus,
    operation: ManagedMaintenance,
) {
    match operation {
        ManagedMaintenance::Balance => {
            update_balance_progress(progress, details, &status.balance_details)
        }
        ManagedMaintenance::Defrag => {
            update_defrag_progress(progress, details, &status.defrag_details)
        }
    }
}

fn update_balance_progress(
    progress: &gtk::ProgressBar,
    details: &gtk::Label,
    balance: &BtrfsBalanceDetails,
) {
    let fraction = balance
        .percent_remaining
        .map(|remaining| 1.0 - (remaining.min(100) as f64 / 100.0))
        .or_else(|| {
            let completed = balance.chunks_balanced?;
            let total = balance.chunks_total?;
            (total > 0).then(|| (completed as f64 / total as f64).clamp(0.0, 1.0))
        });
    if let Some(fraction) = fraction {
        progress.set_fraction(fraction);
        progress.set_text(Some(&trf(
            "{0}% complete",
            &[&format!("{:.0}", fraction * 100.0)],
        )));
    } else {
        progress.pulse();
        progress.set_text(Some(&tr("Working…")));
    }

    let mut lines = Vec::new();
    if let (Some(completed), Some(total)) = (balance.chunks_balanced, balance.chunks_total) {
        lines.push(trf(
            "Block groups completed: {0} of about {1}",
            &[&completed.to_string(), &total.to_string()],
        ));
    } else {
        lines.push(tr("Examining underused data and metadata block groups…"));
    }
    if let Some(considered) = balance.chunks_considered {
        lines.push(trf(
            "Block groups considered: {0}",
            &[&considered.to_string()],
        ));
    }
    append_elapsed(&mut lines, balance.elapsed_seconds);
    details.set_text(&lines.join("\n"));
}

fn update_defrag_progress(
    progress: &gtk::ProgressBar,
    details: &gtk::Label,
    defrag: &BtrfsDefragDetails,
) {
    progress.pulse();
    progress.set_text(Some(&tr("Working…")));
    let mut lines = vec![tr("Rewriting Home file extents with ZSTD compression…")];
    if defrag.items_processed > 0 {
        lines.push(trf(
            "Items processed: {0}",
            &[&defrag.items_processed.to_string()],
        ));
    }
    append_elapsed(&mut lines, defrag.elapsed_seconds);
    details.set_text(&lines.join("\n"));
}

fn append_elapsed(lines: &mut Vec<String>, elapsed_seconds: Option<u64>) {
    if let Some(seconds) = elapsed_seconds {
        let seconds = i64::try_from(seconds).unwrap_or(i64::MAX);
        lines.push(trf("Elapsed: {0}", &[&format_elapsed_time(seconds)]));
    }
}

fn show_managed_result(
    parent: &adw::PreferencesWindow,
    status: &BtrfsFilesystemStatus,
    operation: ManagedMaintenance,
) {
    let (heading, body) = match operation {
        ManagedMaintenance::Balance => balance_result_presentation(status),
        ManagedMaintenance::Defrag => defrag_result_presentation(status),
    };
    let dialog = adw::MessageDialog::new(Some(parent), Some(&heading), Some(&body));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}

fn balance_result_presentation(status: &BtrfsFilesystemStatus) -> (String, String) {
    let balance = &status.balance_details;
    let (heading, summary) = match status.balance.as_str() {
        "finished" => (
            tr("Space Optimization Complete"),
            tr("Btrfs finished relocating underused data and metadata block groups."),
        ),
        "cancelled" => (
            tr("Space Optimization Cancelled"),
            tr("The limited balance was cancelled safely before it finished."),
        ),
        "failed" => (
            tr("Space Optimization Failed"),
            balance
                .error
                .clone()
                .unwrap_or_else(|| tr("Btrfs could not complete the limited balance.")),
        ),
        _ => (
            tr("Space Optimization Result Unavailable"),
            tr("Btrfs did not provide a completed balance result."),
        ),
    };
    let mut lines = vec![summary];
    if let (Some(relocated), Some(total)) = (balance.chunks_balanced, balance.chunks_total) {
        lines.push(trf(
            "Btrfs examined {0} block groups and relocated {1}.",
            &[&total.to_string(), &relocated.to_string()],
        ));
    }
    append_elapsed(&mut lines, balance.elapsed_seconds);
    lines.push(String::new());
    lines.push(tr("A limited balance improves allocation layout. It does not check file integrity or guarantee that visible free space will increase."));
    (heading, lines.join("\n"))
}

fn defrag_result_presentation(status: &BtrfsFilesystemStatus) -> (String, String) {
    let defrag = &status.defrag_details;
    let (heading, summary) = match status.defrag.as_str() {
        "finished" => (
            tr("Home Defragmentation Complete"),
            tr("Btrfs finished rewriting eligible file extents below /home with ZSTD compression."),
        ),
        "cancelled" => (
            tr("Home Defragmentation Cancelled"),
            tr("Home file defragmentation was cancelled before it finished."),
        ),
        "failed" => (
            tr("Home Defragmentation Failed"),
            defrag
                .error
                .clone()
                .unwrap_or_else(|| tr("Btrfs could not complete Home file defragmentation.")),
        ),
        _ => (
            tr("Home Defragmentation Result Unavailable"),
            tr("Btrfs did not provide a completed defragmentation result."),
        ),
    };
    let mut lines = vec![summary];
    if defrag.items_processed > 0 {
        lines.push(trf(
            "Items processed: {0}",
            &[&defrag.items_processed.to_string()],
        ));
    }
    append_elapsed(&mut lines, defrag.elapsed_seconds);
    lines.push(String::new());
    lines.push(tr(
        "Defragmentation can increase disk usage when files share data with snapshots or reflinks.",
    ));
    (heading, lines.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn managed_maintenance_states_are_explicit() {
        for status in ["starting", "running", "paused", "cancelling"] {
            assert!(managed_status_is_active(status));
        }
        for status in ["idle", "finished", "cancelled", "failed"] {
            assert!(!managed_status_is_active(status));
        }
    }

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
        assert!(!scrub_status_is_current(
            &old,
            Some("Mon Aug 10 03:55:45 2026")
        ));

        let running = BtrfsFilesystemStatus {
            scrub: "running".into(),
            ..old.clone()
        };
        assert!(scrub_status_is_current(
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
        assert!(scrub_status_is_current(
            &newly_finished,
            Some("Mon Aug 10 03:55:45 2026")
        ));
    }

    #[test]
    fn completed_scrub_result_reports_scope_and_counters() {
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
        let (heading, body) = scrub_result_presentation(&status);
        assert_eq!(heading, tr("Integrity Check Complete"));
        assert!(body.contains(&tr(
            "No file system integrity errors were found in allocated data and metadata."
        )));
        assert!(body.contains(&trf("Read errors: {0}", &["0"])));
        assert!(body.contains(&trf("Uncorrectable errors: {0}", &["0"])));
    }

    #[test]
    fn failed_scrub_result_has_an_actionable_warning() {
        let status = BtrfsFilesystemStatus {
            scrub: "finished-with-errors:2".into(),
            scrub_details: BtrfsScrubDetails {
                checksum_errors: 2,
                uncorrectable_errors: 2,
                ..BtrfsScrubDetails::default()
            },
            ..BtrfsFilesystemStatus::default()
        };
        let (heading, body) = scrub_result_presentation(&status);
        assert_eq!(heading, tr("Integrity Problems Found"));
        assert!(body.contains(&tr("Btrfs found errors that could not be repaired. Back up important files and investigate the storage device.")));
        assert!(body.contains(&trf("Checksum errors: {0}", &["2"])));
    }

    #[test]
    fn completed_balance_result_reports_relocated_groups() {
        let status = BtrfsFilesystemStatus {
            balance: "finished".into(),
            balance_details: BtrfsBalanceDetails {
                elapsed_seconds: Some(42),
                chunks_balanced: Some(3),
                chunks_total: Some(120),
                ..BtrfsBalanceDetails::default()
            },
            ..BtrfsFilesystemStatus::default()
        };
        let (heading, body) = balance_result_presentation(&status);
        assert_eq!(heading, tr("Space Optimization Complete"));
        assert!(body.contains(&trf(
            "Btrfs examined {0} block groups and relocated {1}.",
            &["120", "3"]
        )));
        assert!(body.contains(&trf("Elapsed: {0}", &["42s"])));
    }

    #[test]
    fn completed_defrag_result_reports_scope_and_risk() {
        let status = BtrfsFilesystemStatus {
            defrag: "finished".into(),
            defrag_details: BtrfsDefragDetails {
                elapsed_seconds: Some(9),
                items_processed: 18,
                ..BtrfsDefragDetails::default()
            },
            ..BtrfsFilesystemStatus::default()
        };
        let (heading, body) = defrag_result_presentation(&status);
        assert_eq!(heading, tr("Home Defragmentation Complete"));
        assert!(body.contains(&trf("Items processed: {0}", &["18"])));
        assert!(body.contains(&tr(
            "Defragmentation can increase disk usage when files share data with snapshots or reflinks."
        )));
    }
}
