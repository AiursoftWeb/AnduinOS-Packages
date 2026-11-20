# AnduinOS Appearance

AnduinOS Appearance configures the taskbar, panel widgets, desktop icons, login-screen wallpaper, and related GNOME Shell extensions without requiring users to edit extension settings individually.

Open the application menu and search for **AnduinOS Appearance**.

## Choose a taskbar style

The **Taskbar Style** page previews the selected layout before applying it.

![Taskbar style settings](images/taskbar-style.png)

Three layouts are available:

- **Classic** places the start button and applications in a traditional taskbar arrangement.
- **Seperated** visually separates taskbar areas.
- **Centered** centers the application area for a Windows 11-like arrangement.

The label **Seperated** is the spelling currently shown by the application.

You can place the taskbar at the bottom, top, left, or right of the display. Options that apply only to one layout appear when that layout is selected. For example, Classic can combine multiple windows from the same application into one icon.

Changing the layout also adjusts the start-menu height to fit the smallest connected display. The calculation uses the display's logical height, so it accounts for Wayland scaling and helps keep the menu usable on smaller or scaled screens.

## Control panel widgets and desktop icons

Open **Panel Widgets** to show or hide optional information and built-in panel elements.

![Panel widget settings](images/panel-widgets.png)

The page controls:

- **Show Weather** for the weather extension;
- **Show Network** for live network statistics;
- **Show Desktop Icons** for icons and files on the desktop;
- **Show Activities Button** for GNOME's Activities entry.

Turning off a widget disables its GNOME Shell extension for the current user; it does not uninstall the extension. Turning it on makes the extension available again.

When **Show Desktop Icons** is enabled, files in the user's Desktop directory appear on the desktop. Applications can also be pinned to the desktop from the application menu or by placing a trusted `.desktop` launcher in that directory.

## Change the GDM wallpaper

Open **GDM Wallpaper** to choose the image displayed on the sign-in screen. Applying the change requires administrator authentication because the GDM theme is shared by all users.

Use an image that remains readable behind the login controls. The desktop wallpaper and the GDM wallpaper are separate settings; changing one does not automatically replace the other.

## Light and dark themes in Flatpak applications

AnduinOS automatically exposes the selected host GTK3 theme to Flatpak GTK3 applications. When you switch between light and dark appearance, newly opened compatible Flatpak applications should follow the same choice without a manual Flatpak override.

The `anduinos-theme-sync` user service provides this bridge. It does not force a theme through the global `GTK_THEME` environment variable and leaves unrelated user-defined Flatpak overrides unchanged.

GTK4 and libadwaita applications follow the standard desktop appearance portal instead. Qt applications, websites, and applications with their own theme selector may use separate settings and are not controlled by the GTK3 bridge.

If a compatible Flatpak application keeps the old theme, close every window belonging to that application and open it again. You can inspect the bridge with:

```bash title="Check Flatpak GTK3 theme synchronization"
systemctl --user status theme-sync.service
```

## Advanced settings

The **Advanced** page provides shortcuts for the regular wallpaper settings and the GNOME Shell extensions used by the AnduinOS desktop. Use these controls when you need to adjust an extension beyond the common switches exposed on the main pages.

If the panel disappears or an extension behaves unexpectedly, reopen AnduinOS Appearance, restore the intended taskbar layout, and verify that the required extension is enabled. Signing out and back in reloads the complete GNOME Shell session.

For advanced launcher-file management, see [Manage App Icons](../../../Skills/System-Management/Manage-App-Icons.md).
