use gtk::prelude::*;

use crate::i18n::{i18n, i18n_fmt};

/// Show a modal "in progress" dialog over `parent`, with a spinning indicator and message.
/// Runs `task` on a background thread so the UI stays responsive.
/// Returns the task result when done, then closes the dialog.
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
        .default_width(360)
        .default_height(120)
        .title(&i18n("Swap Control"))
        .build();

    let box_ = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(16)
        .margin_start(28)
        .margin_end(28)
        .margin_top(24)
        .margin_bottom(24)
        .build();

    let spinner = gtk::Spinner::builder()
        .halign(gtk::Align::Center)
        .spinning(true)
        .width_request(32)
        .height_request(32)
        .build();
    box_.append(&spinner);

    let label = gtk::Label::builder()
        .label(message)
        .halign(gtk::Align::Center)
        .wrap(true)
        .css_classes(["heading"])
        .build();
    box_.append(&label);

    dialog.set_child(Some(&box_));
    dialog.present();

    // Run work on background thread — GTK main loop stays free
    let result = tokio::task::spawn_blocking(task)
        .await
        .map_err(|e| i18n_fmt(&i18n("Task panicked: {0}"), &[&e.to_string()]))?;

    dialog.close();
    result
}
