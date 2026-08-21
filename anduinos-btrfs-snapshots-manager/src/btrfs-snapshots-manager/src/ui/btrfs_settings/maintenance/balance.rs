use adw::prelude::*;
use libadwaita as adw;

use super::super::shared::confirmation;
use super::MaintenanceControl;
use super::task_dialog::{self, TaskKind};
use crate::dbus_client::{BtrfsBalanceDetails, BtrfsFilesystemStatus};
use crate::i18n::{tr, trf};

pub(super) fn connect(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |button| {
        if button.widget_name() == "balance-monitor" {
            task_dialog::show(&parent, &control, TaskKind::Balance, None, false);
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
                task_dialog::start(&parent, &control, TaskKind::Balance);
            }
        });
        dialog.present();
    });
}

pub(super) fn update_progress(
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
    task_dialog::append_elapsed(&mut lines, balance.elapsed_seconds);
    details.set_text(&lines.join("\n"));
}

pub(super) fn result_presentation(status: &BtrfsFilesystemStatus) -> (String, String) {
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
    task_dialog::append_elapsed(&mut lines, balance.elapsed_seconds);
    lines.push(String::new());
    lines.push(tr("A limited balance improves allocation layout. It does not check file integrity or guarantee that visible free space will increase."));
    (heading, lines.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn completed_result_reports_relocated_groups() {
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
        let (heading, body) = result_presentation(&status);
        assert_eq!(heading, tr("Space Optimization Complete"));
        assert!(body.contains(&trf(
            "Btrfs examined {0} block groups and relocated {1}.",
            &["120", "3"]
        )));
        assert!(body.contains(&trf("Elapsed: {0}", &["42s"])));
    }
}
