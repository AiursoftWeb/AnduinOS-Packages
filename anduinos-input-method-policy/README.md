# Input method policy

Prevents Mozc's login setup from adding Japanese back after the user removes it.
The installer and GNOME Settings remain responsible for input source selection.
Mozc stays available for users who choose it; this package does not install it
or change existing input sources.

The Ubuntu setup script is preserved with `dpkg-divert`, so Mozc updates retain
the policy. Removing this package restores the upstream setup script.
