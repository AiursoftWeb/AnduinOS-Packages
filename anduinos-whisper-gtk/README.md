# AnduinOS Voice Typing

`anduinos-whisper-gtk` supplies the settings and microphone-calibration pages,
the `Super + H` shortcut, a draggable non-focusing GNOME Shell overlay, and
desktop text insertion. `anduinos-whisper-framework` supplies PipeWire capture,
phrase detection, verified models, and local `whisper.cpp` inference.

Simplified Chinese and Traditional Chinese are separate language choices.
Whisper recognizes both as Chinese; the framework then normalizes every partial
and final transcript with the distribution's offline OpenCC dictionaries. This
prevents Whisper's script preference from leaking mixed-script output into the
selected mode.

The extension starts completely hidden and does not launch the recognition
service during sign-in. `Super + H` reveals the floating bar and starts
dictation on demand; no persistent panel or notification-area icon is added.

The Shell extension owns the complete UI state machine: `closed`, `ready`, or
`listening`. The settings Start button, the microphone button, command-line
controls, and `Super + H` all call that same controller; none may start the
recognition daemon directly. Closing the bar always returns to `closed`.

Live transcription is enabled by default. While a phrase is still being
spoken, the framework retranscribes the accumulated audio about twice per
second and publishes only useful partial results inside the expanding floating
bar. Partial results are never inserted; the completed phrase is inserted only
after the normal silence boundary.

The feature is intentionally optional. Installing it does not enable cloud
speech services. Each captured phrase is written to a private temporary WAV
for local transcription and deleted immediately afterwards.

The optional Tiny and Small models are hosted by Hugging Face. Before a direct
download begins, the settings app identifies Hugging Face as an unaffiliated
third party, explains that the connection exposes the user's public IP address,
links to its privacy policy, and requires explicit confirmation.

The overlay injects recognized text through GNOME Shell's compositor-owned
virtual keyboard. It uses the normal paste shortcut, including `Ctrl + Shift +
V` for terminals, which works on both X11 and Wayland without granting access
to `/dev/uinput`. For compatibility, the recognized phrase becomes the current
text clipboard item.
