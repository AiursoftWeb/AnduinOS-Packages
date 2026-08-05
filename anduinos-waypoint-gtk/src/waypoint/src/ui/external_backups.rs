use std::sync::mpsc;
use std::time::Duration;

use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

use crate::dbus_client::{
    ExternalBackupDestination, ExternalBackupDiscovery, RecoveryDeployment, WaypointHelperClient,
};
use crate::i18n::{tr, trf};

#[derive(Debug)]
struct DestinationBackups {
    destination: ExternalBackupDestination,
    discovery: ExternalBackupDiscovery,
}

#[derive(Debug)]
struct ViewData {
    destinations: Vec<DestinationBackups>,
    deployments: Vec<RecoveryDeployment>,
}

pub fn show(parent: &adw::ApplicationWindow) {
    let window = adw::Window::new();
    window.set_title(Some(&tr("External Recovery Backups")));
    window.set_default_size(820, 680);
    window.set_modal(true);
    window.set_transient_for(Some(parent));

    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let header = adw::HeaderBar::new();
    header.set_title_widget(Some(&adw::WindowTitle::new(
        &tr("External Recovery Backups"),
        &tr("Portable full-system recovery points"),
    )));
    let export_button = gtk::Button::with_label(&tr("Export Recovery Point…"));
    export_button.add_css_class("suggested-action");
    export_button.set_sensitive(false);
    header.pack_start(&export_button);
    let refresh_button = gtk::Button::from_icon_name("view-refresh-symbolic");
    refresh_button.set_tooltip_text(Some(&tr("Refresh external drives")));
    header.pack_end(&refresh_button);
    root.append(&header);

    let banner = adw::Banner::new(&tr(
        "External backups are not encrypted. Use an encrypted external drive for sensitive systems.",
    ));
    banner.set_revealed(true);
    root.append(&banner);

    let stack = gtk::Stack::new();
    stack.set_vexpand(true);
    let loading = adw::StatusPage::new();
    loading.set_title(&tr("Looking for external drives…"));
    loading.set_description(Some(&tr(
        "Only mounted filesystems with a verified device UUID are considered.",
    )));
    loading.set_icon_name(Some("drive-removable-media-symbolic"));
    stack.add_named(&loading, Some("loading"));

    let scrolled = gtk::ScrolledWindow::new();
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(760);
    let content = gtk::Box::new(gtk::Orientation::Vertical, 18);
    content.set_margin_top(24);
    content.set_margin_bottom(24);
    content.set_margin_start(12);
    content.set_margin_end(12);
    clamp.set_child(Some(&content));
    scrolled.set_child(Some(&clamp));
    stack.add_named(&scrolled, Some("content"));
    root.append(&stack);
    window.set_content(Some(&root));

    let data = std::rc::Rc::new(std::cell::RefCell::new(None::<ViewData>));
    load(&window, &stack, &content, &export_button, &data);

    let window_refresh = window.clone();
    let stack_refresh = stack.clone();
    let content_refresh = content.clone();
    let export_refresh = export_button.clone();
    let data_refresh = data.clone();
    refresh_button.connect_clicked(move |_| {
        load(
            &window_refresh,
            &stack_refresh,
            &content_refresh,
            &export_refresh,
            &data_refresh,
        );
    });

    let window_export = window.clone();
    let data_export = data.clone();
    export_button.connect_clicked(move |_| {
        let data = data_export.borrow();
        let Some(data) = data.as_ref() else {
            return;
        };
        show_export_dialog(
            &window_export,
            data.deployments.clone(),
            data.destinations
                .iter()
                .map(|item| item.destination.clone())
                .collect(),
        );
    });

    window.present();
}

fn load(
    window: &adw::Window,
    stack: &gtk::Stack,
    content: &gtk::Box,
    export_button: &gtk::Button,
    state: &std::rc::Rc<std::cell::RefCell<Option<ViewData>>>,
) {
    stack.set_visible_child_name("loading");
    export_button.set_sensitive(false);
    clear_box(content);
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = (|| -> anyhow::Result<ViewData> {
            let client = WaypointHelperClient::new()?;
            let deployments = client.recovery_engine_status()?.deployments;
            let mut destination_backups = Vec::new();
            for destination in client.list_backup_destinations()? {
                let discovery =
                    client.list_external_backups(destination.filesystem_uuid.clone())?;
                destination_backups.push(DestinationBackups {
                    destination,
                    discovery,
                });
            }
            Ok(ViewData {
                destinations: destination_backups,
                deployments,
            })
        })();
        let _ = sender.send(result);
    });

    let window = window.clone();
    let stack = stack.clone();
    let content = content.clone();
    let export_button = export_button.clone();
    let state = state.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(view_data)) => {
                populate(&window, &content, &view_data);
                export_button.set_sensitive(
                    !view_data.deployments.is_empty() && !view_data.destinations.is_empty(),
                );
                *state.borrow_mut() = Some(view_data);
                stack.set_visible_child_name("content");
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                show_message(
                    &window,
                    &tr("External Backups Unavailable"),
                    &error.to_string(),
                    false,
                );
                let status = adw::StatusPage::new();
                status.set_title(&tr("External backups are unavailable"));
                status.set_description(Some(&error.to_string()));
                status.set_icon_name(Some("dialog-error-symbolic"));
                content.append(&status);
                stack.set_visible_child_name("content");
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => {
                show_message(
                    &window,
                    &tr("External Backups Unavailable"),
                    &tr("The background drive scan stopped unexpectedly."),
                    false,
                );
                glib::ControlFlow::Break
            }
        }
    });
}

fn populate(window: &adw::Window, content: &gtk::Box, data: &ViewData) {
    clear_box(content);
    if data.destinations.is_empty() {
        let empty = adw::StatusPage::new();
        empty.set_title(&tr("No Supported External Drive"));
        empty.set_description(Some(&tr(
            "Connect and mount an ext4, Btrfs, XFS, exFAT, or NTFS3 drive, then refresh.",
        )));
        empty.set_icon_name(Some("drive-removable-media-symbolic"));
        content.append(&empty);
        return;
    }

    for item in &data.destinations {
        let group = adw::PreferencesGroup::new();
        group.set_title(&format!(
            "{} · {}",
            item.destination.filesystem_type.to_uppercase(),
            item.destination.filesystem_uuid
        ));
        group.set_description(Some(&trf(
            "Mounted at {0} · backup streams are addressed by UUID, never by a caller-supplied path",
            &[&item.destination.mount_point.display().to_string()],
        )));
        if item.discovery.backups.is_empty() {
            let row = adw::ActionRow::new();
            row.set_title(&tr("No recovery backups on this drive"));
            row.set_subtitle(&tr(
                "Use “Export Recovery Point…” to create a full portable backup.",
            ));
            group.add(&row);
        }
        for backup in &item.discovery.backups {
            let row = adw::ExpanderRow::new();
            row.set_title(&backup.source.title);
            row.set_subtitle(&trf(
                "{0} · {1} stream · kernel {2}",
                &[
                    &backup.created_at.format("%Y-%m-%d %H:%M UTC").to_string(),
                    &waypoint_common::format_bytes(backup.stream_size_bytes),
                    backup
                        .source
                        .kernel_release
                        .as_deref()
                        .unwrap_or(&tr("unknown")),
                ],
            ));
            let identity = adw::ActionRow::new();
            identity.set_title(&tr("Backup identity"));
            identity.set_subtitle(&trf(
                "{0} · source {1} from {2} · referenced {3}",
                &[
                    &backup.backup_id,
                    &backup.source.id,
                    &backup
                        .source
                        .created_at
                        .format("%Y-%m-%d %H:%M UTC")
                        .to_string(),
                    &waypoint_common::format_bytes(backup.referenced_bytes),
                ],
            ));
            row.add_row(&identity);
            let reason = adw::ActionRow::new();
            reason.set_title(&tr("Source description"));
            reason.set_subtitle(&backup.source.reason);
            row.add_row(&reason);
            let digest = adw::ActionRow::new();
            digest.set_title(&tr("Stream SHA-256"));
            digest.set_subtitle(&backup.stream_sha256);
            row.add_row(&digest);

            let actions = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            let verify = gtk::Button::with_label(&tr("Verify"));
            let import = gtk::Button::with_label(&tr("Import"));
            import.add_css_class("suggested-action");
            let delete = gtk::Button::from_icon_name("user-trash-symbolic");
            delete.add_css_class("destructive-action");
            delete.set_tooltip_text(Some(&tr("Delete this external backup")));
            actions.append(&verify);
            actions.append(&import);
            actions.append(&delete);
            row.add_suffix(&actions);

            let filesystem_uuid = item.destination.filesystem_uuid.clone();
            let backup_id = backup.backup_id.clone();
            let window_verify = window.clone();
            verify.connect_clicked(move |_| {
                let filesystem_uuid = filesystem_uuid.clone();
                let backup_id = backup_id.clone();
                run_operation(
                    &window_verify,
                    &tr("Verifying External Backup"),
                    move || {
                        let result = WaypointHelperClient::new()?
                            .verify_external_backup(filesystem_uuid, backup_id)?;
                        Ok(trf(
                            "The complete {0} stream matches its SHA-256 manifest.",
                            &[&waypoint_common::format_bytes(result.stream_size_bytes)],
                        ))
                    },
                );
            });

            let filesystem_uuid = item.destination.filesystem_uuid.clone();
            let backup_id = backup.backup_id.clone();
            let title = backup.source.title.clone();
            let window_import = window.clone();
            import.connect_clicked(move |_| {
                let dialog = adw::MessageDialog::new(
                    Some(&window_import),
                    Some(&tr("Import External Recovery Point?")),
                    Some(&trf(
                        "“{0}” will be verified and copied into local recovery storage. Importing does not immediately roll back the system.",
                        &[&title],
                    )),
                );
                dialog.add_response("cancel", &tr("Cancel"));
                dialog.add_response("import", &tr("Verify and Import"));
                dialog.set_response_appearance("import", adw::ResponseAppearance::Suggested);
                let window = window_import.clone();
                let filesystem_uuid = filesystem_uuid.clone();
                let backup_id = backup_id.clone();
                dialog.connect_response(Some("import"), move |_, _| {
                    let filesystem_uuid = filesystem_uuid.clone();
                    let backup_id = backup_id.clone();
                    run_operation(&window, &tr("Importing Recovery Point"), move || {
                        let record = WaypointHelperClient::new()?
                            .import_external_backup(filesystem_uuid, backup_id)?;
                        Ok(trf(
                            "“{0}” was imported as local recovery point {1}. It can now be reviewed before scheduling a restart.",
                            &[&record.title, &record.id],
                        ))
                    });
                });
                dialog.present();
            });

            let filesystem_uuid = item.destination.filesystem_uuid.clone();
            let backup_id = backup.backup_id.clone();
            let window_delete = window.clone();
            delete.connect_clicked(move |_| {
                let dialog = adw::MessageDialog::new(
                    Some(&window_delete),
                    Some(&tr("Delete External Backup?")),
                    Some(&tr("This removes the manifest and full recovery stream from the external drive. The local recovery point is not affected.")),
                );
                dialog.add_response("cancel", &tr("Cancel"));
                dialog.add_response("delete", &tr("Delete Backup"));
                dialog.set_response_appearance("delete", adw::ResponseAppearance::Destructive);
                let window = window_delete.clone();
                let filesystem_uuid = filesystem_uuid.clone();
                let backup_id = backup_id.clone();
                dialog.connect_response(Some("delete"), move |_, _| {
                    let filesystem_uuid = filesystem_uuid.clone();
                    let backup_id = backup_id.clone();
                    run_operation(&window, &tr("Deleting External Backup"), move || {
                        WaypointHelperClient::new()?
                            .delete_external_backup(filesystem_uuid, backup_id)?;
                        Ok(tr("The external recovery backup was deleted. Refresh to update the list."))
                    });
                });
                dialog.present();
            });
            group.add(&row);
        }
        for issue in &item.discovery.issues {
            let row = adw::ActionRow::new();
            row.set_title(&trf("Damaged backup entry: {0}", &[&issue.entry]));
            row.set_subtitle(&issue.message);
            row.add_css_class("error");
            group.add(&row);
        }
        content.append(&group);
    }
}

fn show_export_dialog(
    parent: &adw::Window,
    deployments: Vec<RecoveryDeployment>,
    destinations: Vec<ExternalBackupDestination>,
) {
    let window = adw::Window::new();
    window.set_title(Some(&tr("Export Recovery Point")));
    window.set_modal(true);
    window.set_transient_for(Some(parent));
    window.set_default_size(560, 360);
    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
    root.append(&adw::HeaderBar::new());
    let clamp = adw::Clamp::new();
    let body = gtk::Box::new(gtk::Orientation::Vertical, 18);
    body.set_margin_top(24);
    body.set_margin_bottom(24);
    body.set_margin_start(12);
    body.set_margin_end(12);

    let selection = adw::PreferencesGroup::new();
    selection.set_title(&tr("Full System Backup"));
    selection.set_description(Some(&tr(
        "The immutable System deployment is exported as one portable Btrfs stream. Personal Files are not included.",
    )));
    let deployment_model = gtk::StringList::new(
        &deployments
            .iter()
            .map(|item| item.title.as_str())
            .collect::<Vec<_>>(),
    );
    let deployment_row = adw::ComboRow::new();
    deployment_row.set_title(&tr("Recovery Point"));
    deployment_row.set_model(Some(&deployment_model));
    selection.add(&deployment_row);
    let destination_labels = destinations
        .iter()
        .map(|item| {
            format!(
                "{} · {}",
                item.filesystem_type.to_uppercase(),
                item.filesystem_uuid
            )
        })
        .collect::<Vec<_>>();
    let destination_model = gtk::StringList::new(
        &destination_labels
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>(),
    );
    let destination_row = adw::ComboRow::new();
    destination_row.set_title(&tr("External Drive"));
    destination_row.set_model(Some(&destination_model));
    selection.add(&destination_row);
    body.append(&selection);
    let export = gtk::Button::with_label(&tr("Export and Verify"));
    export.add_css_class("suggested-action");
    export.set_halign(gtk::Align::End);
    body.append(&export);
    clamp.set_child(Some(&body));
    root.append(&clamp);
    window.set_content(Some(&root));

    let parent = parent.clone();
    let export_window = window.clone();
    export.connect_clicked(move |_| {
        let deployment_index = deployment_row.selected() as usize;
        let destination_index = destination_row.selected() as usize;
        let Some(deployment) = deployments.get(deployment_index) else {
            return;
        };
        let Some(destination) = destinations.get(destination_index) else {
            return;
        };
        let deployment_id = deployment.id.clone();
        let filesystem_uuid = destination.filesystem_uuid.clone();
        export_window.close();
        run_operation(&parent, &tr("Exporting Recovery Point"), move || {
            let manifest =
                WaypointHelperClient::new()?.export_deployment(deployment_id, filesystem_uuid)?;
            Ok(trf(
                "External backup {0} was written and committed atomically ({1}).",
                &[
                    &manifest.backup_id,
                    &waypoint_common::format_bytes(manifest.stream_size_bytes),
                ],
            ))
        });
    });
    window.present();
}

fn run_operation<F>(parent: &adw::Window, title: &str, operation: F)
where
    F: FnOnce() -> anyhow::Result<String> + Send + 'static,
{
    let progress = adw::MessageDialog::new(
        Some(parent),
        Some(title),
        Some(&tr(
            "Do not disconnect the external drive or power off the computer.",
        )),
    );
    progress.present();
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = sender.send(operation());
    });
    let parent = parent.clone();
    glib::timeout_add_local(Duration::from_millis(100), move || {
        match receiver.try_recv() {
            Ok(result) => {
                progress.close();
                match result {
                    Ok(message) => show_message(&parent, &tr("Operation Complete"), &message, true),
                    Err(error) => {
                        show_message(&parent, &tr("Operation Failed"), &error.to_string(), false)
                    }
                }
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => {
                progress.close();
                show_message(
                    &parent,
                    &tr("Operation Failed"),
                    &tr("The background operation stopped unexpectedly."),
                    false,
                );
                glib::ControlFlow::Break
            }
        }
    });
}

fn show_message(parent: &adw::Window, title: &str, message: &str, success: bool) {
    let dialog = adw::MessageDialog::new(Some(parent), Some(title), Some(message));
    dialog.add_response("ok", &tr("OK"));
    if success {
        dialog.set_response_appearance("ok", adw::ResponseAppearance::Suggested);
    }
    dialog.present();
}

fn clear_box(container: &gtk::Box) {
    while let Some(child) = container.first_child() {
        container.remove(&child);
    }
}
