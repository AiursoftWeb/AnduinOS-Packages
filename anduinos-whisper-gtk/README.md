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
spoken, the framework requests a preview at most every 0.8 seconds and keeps
only the latest queued preview. Final recognition preempts a running obsolete
preview; actual preview frequency depends on available compute. Partial results
are never inserted. Completed phrases are inserted after a detected non-speech
boundary or when the user stops recording to finish a phrase.

## Noise handling and finishing

The capture pipeline uses an isolated streaming Silero voice detector.
WebRTC noise suppression and bounded automatic gain are
available as an opt-in setting: processing can also harm recognition, so it
is not enabled blindly for every microphone. The
legacy `silence-threshold` preference is retained for compatibility but is no
longer used. Avoid stacking noise reduction on microphones that already
provide processed audio; restart listening after changing this setting.
Echo cancellation is deliberately disabled: it requires a correctly routed
playback reference, which this capture-only pipeline does not have.

A phrase starts after 120 ms of detected voice, retains approximately 350 ms
of pre-roll, and ends after 800 ms of detected non-speech. The 12-second maximum
audio length remains a fallback. A separate watchdog reports capture stalls.
These are initial engineering settings, not a guarantee for every room or
microphone. The microphone test shows processed levels; it does not train a
speaker model or measure transcription accuracy. Other people's speech can
still activate voice detection.

Pressing the microphone button or `Super + H` again stops capture and finishes
the current phrase. Closing the bar cancels outstanding recognition and text
insertion. Starting a new session cancels an unfinished previous session.
Final results remain FIFO, including when slow inference produces a burst;
there are at most eight queued final phrases. Overload stops capture with an
explicit warning while accepted final phrases finish. No audio or transcript
is written to diagnostic logs; timing logs contain only task type and durations.

Regression coverage includes public bilingual clean/noisy audio, native speech
detection, cancellation, bounded scheduling and isolated desktop insertion.
See the framework's [testing guide](../anduinos-whisper-framework/docs/testing.md)
for reproducible commands and limitations. Desktop integration tools live in
`tests/integration/`; they never replace the user's running Shell.

The feature is intentionally optional. Installing it does not enable cloud
speech services. Captured audio is passed through private pipes to a persistent
local worker; the service does not write recordings or transcripts to disk.

The optional Tiny and Small models are hosted by Hugging Face. Before a direct
download begins, the settings app identifies Hugging Face as an unaffiliated
third party, explains that the connection exposes the user's public IP address,
links to its privacy policy, and requires explicit confirmation.

The overlay injects recognized text through GNOME Shell's compositor-owned
virtual keyboard. It uses the normal paste shortcut, including `Ctrl + Shift +
V` for terminals, which works on both X11 and Wayland without granting access
to `/dev/uinput`. For compatibility, the recognized phrase becomes the current
text clipboard item.
