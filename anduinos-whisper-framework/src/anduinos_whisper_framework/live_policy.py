"""Conservative live-preview policy; never writes user preferences."""
import math


def live_mode(settings):
    if settings.get_user_value("live-transcription-mode") is not None:
        return settings.get_string("live-transcription-mode")
    legacy = settings.get_user_value("live-transcription")
    if legacy is not None:
        return "on" if legacy.get_boolean() else "off"
    return settings.get_string("live-transcription-mode")


def permits_preview(mode, capable):
    return mode == "on" or (mode == "auto" and capable is True)


def preview_capable(measurements, selected):
    """Require repeated warm measurements from the selected configuration.

    A phrase can grow to 12 s and previews arrive every 0.8 s. Requiring
    RTF <= 0.025 budgets about 0.3 s for that window, leaving room for final
    recognition and desktop work. This is a conservative heuristic, not a
    guarantee of linear scaling; runtime slowdowns disable automatic preview.
    """
    if not isinstance(measurements, list):
        return False
    records = [r for r in measurements if isinstance(r, dict)
               and r.get("phase") == "warm" and r.get("status") == "success"
               and r.get("backend") == selected.get("backend")
               and r.get("threads") == selected.get("threads")]
    if len(records) < 2:
        return False
    for record in records:
        duration, elapsed = record.get("audio_ms"), record.get("inference_ms")
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in (duration, elapsed)):
            return False
        if elapsed > 300 or elapsed / duration > 0.025:
            return False
    return True
