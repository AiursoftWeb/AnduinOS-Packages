use gio::prelude::*;
use glib::variant::ToVariant;

const GEOCLUE_NAME: &str = "org.freedesktop.GeoClue2";
const MANAGER_PATH: &str = "/org/freedesktop/GeoClue2/Manager";
const MANAGER_IFACE: &str = "org.freedesktop.GeoClue2.Manager";
const CLIENT_IFACE: &str = "org.freedesktop.GeoClue2.Client";
const LOCATION_IFACE: &str = "org.freedesktop.GeoClue2.Location";
const DESKTOP_ID: &str = "anduinos-theme-schedule";
const CITY_ACCURACY: u32 = 4;
const TIMEOUT_MS: i32 = 2500;

fn system_proxy(path: &str, interface: &str) -> Option<gio::DBusProxy> {
    gio::DBusProxy::for_bus_sync(
        gio::BusType::System,
        gio::DBusProxyFlags::NONE,
        None,
        GEOCLUE_NAME,
        path,
        interface,
        gio::Cancellable::NONE,
    )
    .ok()
}

fn call(proxy: &gio::DBusProxy, method: &str, params: Option<&glib::Variant>) -> Option<glib::Variant> {
    proxy
        .call_sync(
            method,
            params,
            gio::DBusCallFlags::NONE,
            TIMEOUT_MS,
            gio::Cancellable::NONE,
        )
        .ok()
}

pub fn current_coordinates() -> Option<(f64, f64)> {
    let manager = system_proxy(MANAGER_PATH, MANAGER_IFACE)?;
    let reply = call(&manager, "GetClient", None)?;
    let path = reply.try_child_value(0)?.str()?.to_string();
    let client = system_proxy(&path, CLIENT_IFACE)?;
    client.set_cached_property("DesktopId", Some(&DESKTOP_ID.to_variant()));
    let _ = call(
        &client,
        "org.freedesktop.DBus.Properties.Set",
        Some(&glib::Variant::tuple_from_iter([
            CLIENT_IFACE.to_variant(),
            "DesktopId".to_variant(),
            DESKTOP_ID.to_variant().to_variant(),
        ])),
    );
    let _ = call(
        &client,
        "org.freedesktop.DBus.Properties.Set",
        Some(&glib::Variant::tuple_from_iter([
            CLIENT_IFACE.to_variant(),
            "RequestedAccuracyLevel".to_variant(),
            CITY_ACCURACY.to_variant().to_variant(),
        ])),
    );
    call(&client, "Start", None)?;

    let mut location_path = client
        .cached_property("Location")
        .and_then(|value| value.get::<String>())
        .filter(|path| path != "/");
    if location_path.is_none() {
        for _ in 0..6 {
            std::thread::sleep(std::time::Duration::from_millis(150));
            let reply = call(
                &client,
                "org.freedesktop.DBus.Properties.Get",
                Some(&glib::Variant::tuple_from_iter([
                    CLIENT_IFACE.to_variant(),
                    "Location".to_variant(),
                ])),
            );
            location_path = reply
                .and_then(|value| value.try_child_value(0))
                .and_then(|value| value.as_variant().and_then(|inner| inner.get::<String>()))
                .filter(|path| path != "/");
            if location_path.is_some() {
                break;
            }
        }
    }
    let location_path = location_path?;
    let location = system_proxy(&location_path, LOCATION_IFACE)?;
    let latitude = location.cached_property("Latitude")?.get::<f64>()?;
    let longitude = location.cached_property("Longitude")?.get::<f64>()?;
    if !latitude.is_finite() || !longitude.is_finite() {
        return None;
    }
    if latitude == 0.0 && longitude == 0.0 {
        return None;
    }
    Some((latitude, longitude))
}
