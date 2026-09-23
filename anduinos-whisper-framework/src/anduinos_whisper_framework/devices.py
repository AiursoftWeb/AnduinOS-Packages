"""GTK compatibility: enumerate inputs without importing the voice backend."""
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst


def input_devices() -> list[tuple[str, str, bool]]:
    Gst.init(None)
    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Audio/Source", None)
    if not monitor.start():
        return []
    result, seen = [], set()
    try:
        for device in monitor.get_devices():
            properties = device.get_properties()
            if properties is None:
                continue
            media_class = properties.get_string("media.class") or ""
            device_class = properties.get_string("device.class") or ""
            node_name = properties.get_string("node.name") or ""
            if media_class != "Audio/Source" or device_class == "monitor":
                continue
            if not node_name or node_name in seen:
                continue
            seen.add(node_name)
            is_default = (bool(properties.get_value("is-default"))
                          if properties.has_field("is-default") else False)
            result.append((node_name, device.get_display_name(), is_default))
    finally:
        monitor.stop()
    return sorted(result, key=lambda item: (not item[2], item[1].casefold()))
