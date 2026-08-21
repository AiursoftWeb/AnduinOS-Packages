use crate::i18n::i18n;
use gtk::gio;
use gtk::prelude::*;

/// Display a modal progress window while a blocking security-key operation runs.
/// The task is executed on GLib's blocking thread pool so the GTK main loop remains responsive.
pub async fn run_with_progress<F, T>(
    parent: &gtk::Window,
    message: &str,
    task: F,
) -> Result<T, String>
where
    F: FnOnce() -> Result<T, String> + Send + 'static,
    T: Send + 'static,
{
    let dialog = gtk::Window::builder()
        .transient_for(parent)
        .modal(true)
        .deletable(false)
        .resizable(false)
        .default_width(380)
        .default_height(140)
        .title(i18n("AnduinOS YubiKey Security Center"))
        .build();

    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(16)
        .margin_start(28)
        .margin_end(28)
        .margin_top(24)
        .margin_bottom(24)
        .build();
    content.append(
        &gtk::Spinner::builder()
            .halign(gtk::Align::Center)
            .spinning(true)
            .width_request(32)
            .height_request(32)
            .build(),
    );
    content.append(
        &gtk::Label::builder()
            .label(message)
            .halign(gtk::Align::Center)
            .justify(gtk::Justification::Center)
            .wrap(true)
            .css_classes(["heading"])
            .build(),
    );

    dialog.set_child(Some(&content));
    dialog.present();

    let result = gio::spawn_blocking(task)
        .await
        .map_err(|_| i18n("The YubiKey operation stopped unexpectedly."))?;
    dialog.close();
    result
}
