# Display scaling in visual setup

The row between theme selection and taskbar layout controls the primary logical
monitor through Mutter's `org.gnome.Mutter.DisplayConfig` interface. Opening the
page never changes settings. Selecting a scale verifies and then persists the
configuration; failures restore the dropdown to the actual current value.

Recommendations use the current physical pixel resolution and EDID dimensions
(or Mutter's physical dimensions where available). Effective PPI is pixel PPI
divided by display scale; additional text scaling is not included.

Built-in screens score 4, portable DMI chassis types add 4, a system battery adds
2, and a diagonal below 20 inches adds 1. A score of at least 6 selects the
146-PPI laptop target; other physical screens use 96 PPI. External screens never
inherit their host's laptop score. These are comfort heuristics, not universal
ideal sizes or the Windows recommendation algorithm.

The recommendation minimizes effective-PPI error among supported current-mode
scales. Its dropdown entry is marked “Recommended”. Missing dimensions and
mirrored primary screens have no recommendation. A virtual screen's reported
dimensions may not describe the user's physical monitor.

All active modes, rotations, colour modes, RGB ranges, underscan settings, and
secondary scales are retained. Adjacent displays are repositioned to stay
connected when the primary logical size changes. Mutter rejects invalid or
unsupported combinations before application. Mirrored screens use the
intersection of supported scales. Multiple screens on a backend that requires
global scaling cannot be changed individually through this control.

Display information refreshes while the row is mapped, including external
settings changes and hotplug events. Refresh and apply run off the GTK thread.
