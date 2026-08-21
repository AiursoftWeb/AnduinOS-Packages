use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use super::super::shared::show_result;
use super::{MaintenanceControl, balance, defrag, query_btrfs_status};
use crate::dbus_client::{BtrfsFilesystemStatus, SnapshotsManagerHelperClient};
use crate::i18n::{tr, trf};
use snapshots_manager_common::format_elapsed_time;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum TaskKind {
    Balance,
    Defrag,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TaskPhase {
    Idle,
    Starting,
    Running,
    Paused,
    Cancelling,
    Finished,
    Cancelled,
    Failed,
    Unavailable,
    Unknown,
}

impl TaskPhase {
    fn from_wire(value: &str) -> Self {
        match value {
            "idle" => Self::Idle,
            "starting" => Self::Starting,
            "running" => Self::Running,
            "paused" => Self::Paused,
            "cancelling" => Self::Cancelling,
            "finished" => Self::Finished,
            "cancelled" => Self::Cancelled,
            "failed" => Self::Failed,
            "unavailable" => Self::Unavailable,
            _ => Self::Unknown,
        }
    }

    fn is_active(self) -> bool {
        matches!(
            self,
            Self::Starting | Self::Running | Self::Paused | Self::Cancelling
        )
    }
}

#[derive(Clone)]
struct ProgressWidgets {
    window: adw::Window,
    progress: gtk::ProgressBar,
    details: gtk::Label,
}

impl TaskKind {
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

pub(super) fn update_control(control: &MaintenanceControl, status: &str, operation: TaskKind) {
    let phase = TaskPhase::from_wire(status);
    let running = phase.is_active();
    let subtitle = if running {
        match phase {
            TaskPhase::Starting => tr("Starting…"),
            TaskPhase::Paused => tr("Paused"),
            TaskPhase::Cancelling => tr("Cancelling…"),
            _ => tr("Running…"),
        }
    } else {
        match operation {
            TaskKind::Balance => tr("Ready to optimize allocation"),
            TaskKind::Defrag => tr("Ready to defragment Home files"),
        }
    };
    control.row.set_subtitle(&subtitle);
    if running {
        control.button.set_label(&tr("View Progress"));
    } else {
        control.button.set_label(&tr(match operation {
            TaskKind::Balance => "Start Balance",
            TaskKind::Defrag => "Defragment…",
        }));
    }
    control.button.set_widget_name(match (operation, running) {
        (TaskKind::Balance, true) => "balance-monitor",
        (TaskKind::Balance, false) => "balance-start",
        (TaskKind::Defrag, true) => "defrag-monitor",
        (TaskKind::Defrag, false) => "defrag-start",
    });
    control
        .button
        .set_sensitive(phase != TaskPhase::Unavailable);
}

pub(super) fn start(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    operation: TaskKind,
) {
    update_control(control, "starting", operation);
    control.button.set_sensitive(false);

    let weak_parent = parent.downgrade();
    let control = control.clone();
    glib::spawn_future_local(async move {
        let baseline_generation = match query_btrfs_status().await {
            Ok(status) => operation.generation(&status),
            Err(error) => {
                if let Some(parent) = weak_parent.upgrade() {
                    update_control(&control, "unavailable", operation);
                    show_result(&parent, Err(error));
                }
                return;
            }
        };
        let Some(parent) = weak_parent.upgrade() else {
            return;
        };
        update_control(&control, "running", operation);
        let progress_window = show(
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
            update_control(&control, "idle", operation);
            show_result(&parent, Err(error));
        }
    });
}

pub(super) fn show(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    operation: TaskKind,
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

    let widgets = ProgressWidgets {
        window,
        progress,
        details,
    };
    connect_cancel(parent, &widgets.window, &cancel, operation);
    monitor(
        parent,
        control,
        &widgets,
        operation,
        baseline_generation,
        wait_for_new_run,
    );
    widgets.window
}

fn connect_cancel(
    parent: &adw::PreferencesWindow,
    window: &adw::Window,
    cancel: &gtk::Button,
    operation: TaskKind,
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

fn monitor(
    parent: &adw::PreferencesWindow,
    control: &MaintenanceControl,
    widgets: &ProgressWidgets,
    operation: TaskKind,
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
                    let phase = TaskPhase::from_wire(task_status);
                    let generation = operation.generation(&status);
                    if phase.is_active()
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
                            close_with_error(
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

                    update_progress(&progress, &details, &status, operation);
                    update_control(&control, task_status, operation);
                    if !phase.is_active() {
                        if let Some(window) = weak_window.upgrade() {
                            window.close();
                        }
                        if let Some(parent) = weak_parent.upgrade() {
                            update_control(&control, "idle", operation);
                            show_result_dialog(&parent, &status, operation);
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
                        close_with_error(&weak_parent, &weak_window, &control, operation, error);
                        return;
                    }
                }
            }
            if failed_queries >= 3 {
                close_with_error(
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

fn close_with_error(
    weak_parent: &glib::WeakRef<adw::PreferencesWindow>,
    weak_window: &glib::WeakRef<adw::Window>,
    control: &MaintenanceControl,
    operation: TaskKind,
    error: anyhow::Error,
) {
    if let Some(window) = weak_window.upgrade() {
        window.close();
    }
    if let Some(parent) = weak_parent.upgrade() {
        update_control(control, "unavailable", operation);
        show_result(&parent, Err(error));
    }
}

fn update_progress(
    progress: &gtk::ProgressBar,
    details: &gtk::Label,
    status: &BtrfsFilesystemStatus,
    operation: TaskKind,
) {
    match operation {
        TaskKind::Balance => balance::update_progress(progress, details, &status.balance_details),
        TaskKind::Defrag => defrag::update_progress(progress, details, &status.defrag_details),
    }
}

pub(super) fn append_elapsed(lines: &mut Vec<String>, elapsed_seconds: Option<u64>) {
    if let Some(seconds) = elapsed_seconds {
        let seconds = i64::try_from(seconds).unwrap_or(i64::MAX);
        lines.push(trf("Elapsed: {0}", &[&format_elapsed_time(seconds)]));
    }
}

fn show_result_dialog(
    parent: &adw::PreferencesWindow,
    status: &BtrfsFilesystemStatus,
    operation: TaskKind,
) {
    let (heading, body) = match operation {
        TaskKind::Balance => balance::result_presentation(status),
        TaskKind::Defrag => defrag::result_presentation(status),
    };
    let dialog = adw::MessageDialog::new(Some(parent), Some(&heading), Some(&body));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn active_states_are_explicit() {
        for status in ["starting", "running", "paused", "cancelling"] {
            assert!(TaskPhase::from_wire(status).is_active());
        }
        for status in ["idle", "finished", "cancelled", "failed"] {
            assert!(!TaskPhase::from_wire(status).is_active());
        }
    }
}
