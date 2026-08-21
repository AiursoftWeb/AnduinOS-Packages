use gtk::gio;
use gtk::prelude::*;
use std::ffi::OsStr;

/// Start a udev event stream for physical USB changes.
///
/// The callback runs on GTK's main context. It must only schedule a debounced,
/// non-interactive sysfs snapshot; no security-key commands belong here.
pub fn start<F>(on_event: F) -> Result<gio::Subprocess, String>
where
    F: Fn() + 'static,
{
    let argv = [
        OsStr::new("udevadm"),
        OsStr::new("monitor"),
        OsStr::new("--udev"),
        OsStr::new("--subsystem-match=usb"),
    ];
    let launcher = gio::SubprocessLauncher::new(
        gio::SubprocessFlags::STDOUT_PIPE | gio::SubprocessFlags::STDERR_SILENCE,
    );
    launcher.set_child_setup(|| unsafe {
        libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
        if libc::getppid() == 1 {
            libc::_exit(1);
        }
    });
    let process = launcher.spawn(&argv).map_err(|error| error.to_string())?;
    let stdout = process
        .stdout_pipe()
        .ok_or_else(|| "udevadm did not provide an event stream".to_string())?;
    let stream = gio::DataInputStream::new(&stdout);
    gtk::glib::spawn_future_local(async move {
        loop {
            match stream.read_line_future(gtk::glib::Priority::DEFAULT).await {
                Ok(Some(line)) if is_udev_event_line(&line) => on_event(),
                Ok(Some(_)) => {}
                Ok(None) | Err(_) => break,
            }
        }
    });
    Ok(process)
}

fn is_udev_event_line(line: &[u8]) -> bool {
    line.starts_with(b"UDEV  [")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_complete_udev_event_headers() {
        assert!(is_udev_event_line(
            b"UDEV  [123.456] add /devices/pci/usb1/1-2 (usb)"
        ));
        assert!(!is_udev_event_line(b"ACTION=add"));
        assert!(!is_udev_event_line(b"KERNEL [123.456] add /devices"));
    }
}
