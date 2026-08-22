mod geoclue;
mod sun;

use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::{SystemTime, UNIX_EPOCH};

use gio::prelude::*;
use glib::ControlFlow;

const SCHEDULE_SCHEMA: &str = "com.anduinos.ThemeSchedule";
const INTERFACE_SCHEMA: &str = "org.gnome.desktop.interface";
const FALLBACK_SUNRISE_HOUR: u32 = 7;
const FALLBACK_SUNSET_HOUR: u32 = 19;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Mode {
    Manual,
    SunsetSunrise,
}

impl Mode {
    fn parse(value: &str) -> Self {
        if value == "sunset-sunrise" {
            Self::SunsetSunrise
        } else {
            Self::Manual
        }
    }
}

struct App {
    schedule: gio::Settings,
    interface: gio::Settings,
    timer: Cell<Option<glib::SourceId>>,
}

impl App {
    fn mode(&self) -> Mode {
        Mode::parse(&self.schedule.string("mode"))
    }

    fn coordinates(&self) -> Option<(f64, f64)> {
        if let Some(fresh) = geoclue::current_coordinates() {
            let _ = self.schedule.set_double("latitude", fresh.0);
            let _ = self.schedule.set_double("longitude", fresh.1);
            let _ = self.schedule.set_boolean("has-location", true);
            return Some(fresh);
        }
        if self.schedule.boolean("has-location") {
            Some((
                self.schedule.double("latitude"),
                self.schedule.double("longitude"),
            ))
        } else {
            None
        }
    }

    fn apply(self: &Rc<Self>) {
        if let Some(source) = self.timer.take() {
            source.remove();
        }
        if self.mode() != Mode::SunsetSunrise {
            return;
        }

        let now = unix_now();
        let (dark, next) = match self.coordinates() {
            Some((latitude, longitude)) => solar_plan(now, latitude, longitude),
            None => fallback_plan(now),
        };
        set_color_scheme(&self.interface, dark);

        let delay = (next - now).clamp(30, 12 * 3600) as u32;
        let app = Rc::clone(self);
        self.timer.set(Some(glib::timeout_add_seconds_local(delay, move || {
            app.apply();
            ControlFlow::Break
        })));
    }
}

fn unix_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

fn solar_plan(now: i64, latitude: f64, longitude: f64) -> (bool, i64) {
    let Some(local) = glib::DateTime::from_unix_local(now)
        .ok()
        .or_else(|| glib::DateTime::now_local().ok())
    else {
        return fallback_plan(now);
    };
    let today = sun::events_utc(
        local.year(),
        local.month() as u32,
        local.day_of_month() as u32,
        latitude,
        longitude,
    );
    let tomorrow = local.add_days(1).ok().and_then(|next| {
        sun::events_utc(
            next.year(),
            next.month() as u32,
            next.day_of_month() as u32,
            latitude,
            longitude,
        )
    });
    match today {
        Some(events) => (
            sun::is_dark(now, events),
            sun::next_transition(now, events, tomorrow),
        ),
        None => fallback_plan(now),
    }
}

fn fallback_plan(now: i64) -> (bool, i64) {
    let local = glib::DateTime::now_local().ok();
    let Some(local) = local else {
        return (true, now + 3600);
    };
    let minutes = local.hour() * 60 + local.minute();
    let sunrise = (FALLBACK_SUNRISE_HOUR * 60) as i32;
    let sunset = (FALLBACK_SUNSET_HOUR * 60) as i32;
    let dark = minutes < sunrise || minutes >= sunset;
    let target = if minutes < sunrise {
        sunrise
    } else if minutes < sunset {
        sunset
    } else {
        sunrise + 24 * 60
    };
    let delta_min = (target - minutes).rem_euclid(24 * 60);
    (dark, now + i64::from(delta_min) * 60)
}

fn set_color_scheme(settings: &gio::Settings, dark: bool) {
    let wanted = if dark { "prefer-dark" } else { "prefer-light" };
    if settings.string("color-scheme") != wanted {
        let _ = settings.set_string("color-scheme", wanted);
    }
}

fn main() {
    let schedule = match gio::SettingsSchemaSource::default()
        .and_then(|source| source.lookup(SCHEDULE_SCHEMA, true))
    {
        Some(_) => gio::Settings::new(SCHEDULE_SCHEMA),
        None => {
            eprintln!("Missing {SCHEDULE_SCHEMA} schema");
            std::process::exit(1);
        }
    };
    let interface = gio::Settings::new(INTERFACE_SCHEMA);
    let app = Rc::new(App {
        schedule: schedule.clone(),
        interface,
        timer: Cell::new(None),
    });

    let pending = Rc::new(RefCell::new(None::<glib::SourceId>));
    let queue_apply = Rc::new({
        let app = Rc::clone(&app);
        let pending = Rc::clone(&pending);
        move || {
            if let Some(source) = pending.borrow_mut().take() {
                source.remove();
            }
            let app = Rc::clone(&app);
            let pending_later = Rc::clone(&pending);
            *pending.borrow_mut() = Some(glib::timeout_add_local(
                std::time::Duration::from_millis(200),
                move || {
                    pending_later.borrow_mut().take();
                    app.apply();
                    ControlFlow::Break
                },
            ));
        }
    });

    let queue = Rc::clone(&queue_apply);
    schedule.connect_changed(Some("mode"), move |_, _| queue());
    app.apply();

    let loop_ = glib::MainLoop::new(None, false);
    let quit = loop_.clone();
    ctrlc(move || quit.quit());
    loop_.run();
}

fn ctrlc(quit: impl Fn() + 'static) {
    glib::unix_signal_add_local(libc_sigterm(), move || {
        quit();
        ControlFlow::Break
    });
}

fn libc_sigterm() -> i32 {
    15
}
