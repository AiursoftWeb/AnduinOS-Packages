// SPDX-License-Identifier: GPL-3.0-or-later
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {fluentThemeName, UnlockThemeController} from './policy.js';

const USER_THEME = 'user-theme@gnome-shell-extensions.gcampax.github.com';

export default class UnlockTheme extends Extension {
    enable() {
        // metadata restricts us to unlock-dialog; never touch the desktop,
        // GDM, authentication actors or Shell's security/session machinery.
        this._signals = [];
        this._pendingApply = 0;
        this._readability = this.dir.get_child('readability.css');
        this._readabilityLoaded = false;
        this._controller = new UnlockThemeController({
            current: () => Main.getThemeStylesheet()?.get_path() ?? null,
            set: path => {
                Main.setThemeStylesheet(path);
                Main.loadTheme();
            },
            addReadability: () => {
                this._theme().load_stylesheet(this._readability);
                this._readabilityLoaded = true;
            },
            removeReadability: () => {
                if (this._readabilityLoaded) {
                    this._theme().unload_stylesheet(this._readability);
                    this._readabilityLoaded = false;
                }
            },
            report: error => console.error(`AnduinOS unlock theme: ${error}`),
        });
        this._stSettings = St.Settings.get();
        this._watch(this._stSettings, 'notify::high-contrast');

        const schema = Gio.SettingsSchemaSource.get_default()
            .lookup('org.gnome.shell.extensions.user-theme', true);
        if (schema) {
            this._userSettings = new Gio.Settings({settings_schema: schema});
            this._watch(this._userSettings, 'changed::name');
        }
        this._apply();
    }

    disable() {
        for (const [object, id] of this._signals ?? [])
            object.disconnect(id);
        this._signals = [];
        if (this._pendingApply) {
            GLib.Source.remove(this._pendingApply);
            this._pendingApply = 0;
        }
        this._controller?.reset();
        this._controller = null;
        this._userSettings = null;
        this._stSettings = null;
        this._readability = null;
    }

    _watch(object, signal) {
        this._signals.push([object, object.connect(signal, () => {
            // St.Settings notifications can arrive inside GNOME's own theme
            // update. Coalesce changes and reload after that update returns,
            // rather than replacing a theme from inside its notification.
            if (!this._pendingApply) {
                this._pendingApply = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                    this._pendingApply = 0;
                    this._apply();
                    return GLib.SOURCE_REMOVE;
                });
            }
        })]);
    }

    _theme() {
        return St.ThemeContext.get_for_stage(global.stage).get_theme();
    }

    _apply() {
        let path = null;
        if (!this._stSettings.high_contrast &&
            Main.extensionManager.lookup(USER_THEME)?.enabled &&
            this._userSettings) {
            const name = this._userSettings.get_string('name');
            if (fluentThemeName(name)) {
                // Match User Themes' search precedence; keep relative image
                // assets resolving against the original Fluent stylesheet.
                const dirs = [
                    GLib.build_filenamev([GLib.get_home_dir(), '.themes']),
                    GLib.build_filenamev([GLib.get_user_data_dir(), 'themes']),
                    ...GLib.get_system_data_dirs().map(dir =>
                        GLib.build_filenamev([dir, 'themes'])),
                ];
                path = dirs.map(dir => GLib.build_filenamev([
                    dir, name, 'gnome-shell', 'gnome-shell.css',
                ])).find(candidate => Gio.File.new_for_path(candidate)
                    .query_exists(null)) ?? null;
            }
        }
        this._controller.update(path);
    }
}
