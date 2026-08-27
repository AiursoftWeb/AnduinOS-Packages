# AnduinOS Voice Typing

`anduinos-whisper-gtk` supplies the settings and microphone-calibration pages,
the `Super + H` shortcut, a draggable non-focusing GNOME Shell overlay, and
desktop text insertion. `anduinos-whisper-framework` supplies PipeWire capture,
phrase detection, verified models, and local `whisper.cpp` inference.

Live transcription is enabled by default. While a phrase is still being
spoken, the framework periodically retranscribes the accumulated audio and
publishes only its newest partial result to the floating bar. Partial results
are never inserted; the completed phrase is inserted only after the normal
silence boundary.

The feature is intentionally optional. Installing it does not enable cloud
speech services. Each captured phrase is written to a private temporary WAV
for local transcription and deleted immediately afterwards.

The overlay injects recognized text through GNOME Shell's compositor-owned
virtual keyboard. It uses the normal paste shortcut, including `Ctrl + Shift +
V` for terminals, which works on both X11 and Wayland without granting access
to `/dev/uinput`. For compatibility, the recognized phrase becomes the current
text clipboard item.
