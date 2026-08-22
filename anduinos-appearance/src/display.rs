use gtk::gdk::prelude::*;
use gtk::gio::prelude::ListModelExt;
use gtk::glib::object::Cast;

pub fn smallest_monitor_height() -> Option<i32> {
    let display = gtk::gdk::Display::default()?;
    let monitors = display.monitors();
    let mut min_height: Option<i32> = None;
    for index in 0..monitors.n_items() {
        let Some(item) = monitors.item(index) else {
            continue;
        };
        let Ok(monitor) = item.downcast::<gtk::gdk::Monitor>() else {
            continue;
        };
        let height = monitor.geometry().height();
        min_height = Some(min_height.map_or(height, |current| current.min(height)));
    }
    min_height
}

pub fn command_exists(name: &str) -> bool {
    std::env::var_os("PATH")
        .map(|paths| {
            std::env::split_paths(&paths).any(|dir| dir.join(name).is_file())
        })
        .unwrap_or(false)
}
