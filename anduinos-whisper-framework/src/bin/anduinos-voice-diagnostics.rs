use anduinos_whisper_framework::{APP_ID, OBJECT_PATH, diagnostics::sanitize_report};

fn export() -> Result<String, &'static str> {
    let unavailable =
        "No compatible running voice session. Start dictation, then export before closing it.";
    let connection = gio::bus_get_sync(gio::BusType::Session, None::<&gio::Cancellable>)
        .map_err(|_| unavailable)?;
    let response = connection
        .call_sync(
            Some(APP_ID),
            OBJECT_PATH,
            APP_ID,
            "GetDiagnostics",
            None,
            None,
            gio::DBusCallFlags::NO_AUTO_START,
            3000,
            None::<&gio::Cancellable>,
        )
        .map_err(|_| unavailable)?;
    let invalid = "The voice session returned an invalid diagnostic report.";
    let (report,) = response.get::<(String,)>().ok_or(invalid)?;
    sanitize_report(&report).map_err(|_| invalid)
}
fn main() {
    match export() {
        Ok(report) => println!("{report}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
