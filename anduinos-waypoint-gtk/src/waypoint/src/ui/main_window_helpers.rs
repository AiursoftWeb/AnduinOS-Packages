//! Helper functions for MainWindow

use crate::i18n::{tr, trf};
use gtk::glib;
use libadwaita as adw;

/// Create the status banner that shows if Btrfs is available
pub fn create_status_banner() -> (adw::Banner, bool) {
    let banner = adw::Banner::new("");

    let status = crate::dbus_client::WaypointHelperClient::new()
        .and_then(|client| client.recovery_engine_status());
    let (detail, available) = match status {
        Ok(status)
            if status.available
                && status
                    .layout
                    .get("support")
                    .and_then(|value| value.as_str())
                    == Some("supported") =>
        {
            banner.set_revealed(false);
            return (banner, true);
        }
        Ok(status)
            if status
                .layout
                .get("support")
                .and_then(|value| value.as_str())
                != Some("supported") =>
        {
            (
                tr("The required AnduinOS Btrfs mounts could not be verified"),
                false,
            )
        }
        Ok(_) | Err(_) => (
            tr("The recovery service could not load recovery points. Try reopening the app."),
            false,
        ),
    };
    banner.set_title(&trf("System recovery is unavailable: {0}", &[&detail]));
    banner.set_revealed(true);
    (banner, available)
}

/// Show a truthful, cancellable warning when the recovery engine has armed a
/// one-shot restore for the next boot.
pub fn create_pending_restore_banner() -> adw::Banner {
    let banner = adw::Banner::new("");
    let status = crate::dbus_client::WaypointHelperClient::new()
        .and_then(|client| client.recovery_engine_status());
    let Ok(status) = status else {
        banner.set_revealed(false);
        return banner;
    };
    let Some(pending) = status.pending else {
        banner.set_revealed(false);
        return banner;
    };

    banner.set_title(&trf(
        "A system restore to {0} is {1}",
        &[&pending.target_deployment_id, &pending.phase],
    ));
    banner.set_revealed(true);
    if matches!(pending.phase.as_str(), "preparing" | "armed") {
        banner.set_button_label(Some(&tr("Cancel Restore")));
        let banner_for_click = banner.clone();
        banner.connect_button_clicked(move |_| {
            banner_for_click.set_button_label(None);
            banner_for_click.set_title(&tr("Cancelling the scheduled system restore…"));

            let (sender, receiver) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                let result = crate::dbus_client::WaypointHelperClient::new()
                    .and_then(|client| client.cancel_deployment_restore());
                let _ = sender.send(result);
            });

            let banner_for_result = banner_for_click.clone();
            glib::timeout_add_local(std::time::Duration::from_millis(50), move || match receiver
                .try_recv()
            {
                Ok(Ok((true, _))) => {
                    banner_for_result.set_revealed(false);
                    glib::ControlFlow::Break
                }
                Ok(Ok((false, message))) => {
                    banner_for_result
                        .set_title(&trf("Could not cancel the restore: {0}", &[&message]));
                    banner_for_result.set_button_label(Some(&tr("Try Again")));
                    glib::ControlFlow::Break
                }
                Ok(Err(error)) => {
                    banner_for_result.set_title(&trf(
                        "Could not cancel the restore: {0}",
                        &[&error.to_string()],
                    ));
                    banner_for_result.set_button_label(Some(&tr("Try Again")));
                    glib::ControlFlow::Break
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    banner_for_result.set_title(&tr("Could not cancel the scheduled restore"));
                    banner_for_result.set_button_label(Some(&tr("Try Again")));
                    glib::ControlFlow::Break
                }
            });
        });
    }
    banner
}
