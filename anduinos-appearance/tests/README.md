# Appearance tests

Run the package source tests with:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src LANGUAGE=C python3 -m unittest discover -s tests -v
```

The GTK tests use an isolated D-Bus session, Xvfb and an in-memory GSettings
backend. They never toggle extensions in the developer's desktop session.
`test_profile_ui.py` tests real GTK widgets, the 200 ms mapped-window delay,
background execution, duplicate-click protection, error cleanup, disabled
GNOME-inapplicable controls and profile detection after reopening the window.
Extension operations are mocked in this test; `test_profiles.py` covers single-
key state updates, preservation of unrelated extensions, legacy configuration
repair, asynchronous/transitional states, settling, timeouts and ERROR handling.

For optional screenshots, create a temporary directory, set
`APPEARANCE_TEST_SCREENSHOTS` to that directory and run the tests. This requires
FFmpeg; it is not required by the normal test suite.

Before release, also test in a disposable **GNOME desktop** session:

1. Switch between Classic, Separated and Centered. No progress window should
   appear, and no extensions should be toggled.
2. Select GNOME. A modal indeterminate progress bar should appear before the
   three extensions are disabled. GNOME's top bar should return.
3. Reopen Appearance. GNOME should remain selected, with taskbar-only controls
   disabled. Other extensions (including desktop icons) should be unchanged.
4. Select each non-GNOME layout from GNOME. The progress window should appear;
   the requested layout should be configured before Dash to Panel, ArcMenu and
   Blur my Shell are enabled. Repeat this round trip several times.
5. Disable just one of these extensions externally. Selecting a non-GNOME
   layout should repair the incomplete profile, without touching other ones.
6. If a profile extension reports ERROR, no further settings should be changed
   and no profile button should claim successful activation. Recover the broken
   session by logging out and back in before retrying.

The GNOME profile masks its three extensions in `disabled-extensions`; their
UUIDs intentionally stay in `enabled-extensions`. The effective configuration
and Shell's actual INACTIVE state, not membership of the enabled list alone,
determine whether they are disabled. Each settings phase waits for Shell to
finish before proceeding. This avoids the two-key writes performed by the
`gnome-extensions` CLI and overlapping asynchronous rebases in GNOME 50.

The delay lets GTK render before extension work starts. It cannot guarantee
that GNOME Shell itself will never stall while enabling/disabling extensions.
