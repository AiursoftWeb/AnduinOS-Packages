use adw::prelude::*;
use libadwaita as adw;

use super::super::shared::confirmation;
use super::MaintenanceControl;
use super::task_dialog::{self, TaskKind};
use crate::dbus_client::{BtrfsDefragDetails, BtrfsFilesystemStatus};
use crate::i18n::{tr, trf};

pub(super) fn connect(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |button| {
        if button.widget_name() == "defrag-monitor" {
            task_dialog::show(&parent, &control, TaskKind::Defrag, None, false);
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
                task_dialog::start(&parent, &control, TaskKind::Defrag);
            }
        });
        dialog.present();
    });
}

pub(super) fn update_progress(
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
    task_dialog::append_elapsed(&mut lines, defrag.elapsed_seconds);
    details.set_text(&lines.join("\n"));
}

pub(super) fn result_presentation(status: &BtrfsFilesystemStatus) -> (String, String) {
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
    task_dialog::append_elapsed(&mut lines, defrag.elapsed_seconds);
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
    fn completed_result_reports_scope_and_risk() {
        let status = BtrfsFilesystemStatus {
            defrag: "finished".into(),
            defrag_details: BtrfsDefragDetails {
                elapsed_seconds: Some(9),
                items_processed: 18,
                ..BtrfsDefragDetails::default()
            },
            ..BtrfsFilesystemStatus::default()
        };
        let (heading, body) = result_presentation(&status);
        assert_eq!(heading, tr("Home Defragmentation Complete"));
        assert!(body.contains(&trf("Items processed: {0}", &["18"])));
        assert!(body.contains(&tr(
            "Defragmentation can increase disk usage when files share data with snapshots or reflinks."
        )));
    }
}
