"""Conservative live-preview policy; never writes user preferences."""


def live_mode(settings):
    if settings.get_user_value("live-transcription-mode") is not None:
        return settings.get_string("live-transcription-mode")
    legacy = settings.get_user_value("live-transcription")
    if legacy is not None:
        return "on" if legacy.get_boolean() else "off"
    return settings.get_string("live-transcription-mode")
