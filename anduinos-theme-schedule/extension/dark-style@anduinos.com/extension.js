import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import Geoclue from 'gi://Geoclue?version=2.0';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

import {plan} from './sun.js';

const INTERFACE_SCHEMA = 'org.gnome.desktop.interface';
const FALLBACK_SUNRISE_HOUR = 7;
const FALLBACK_SUNSET_HOUR = 19;

const DarkStyleToggle = GObject.registerClass(
class DarkStyleToggle extends QuickSettings.QuickToggle {
    _init(extension) {
        super._init({
            title: _('Dark Style'),
            iconName: 'dark-mode-symbolic',
        });

        this._extension = extension;
        this.connect('clicked', () => extension.cycle());

        this._syncIds = [
            extension._interface.connect('changed::color-scheme', () => this._sync()),
            extension._schedule.connect('changed::mode', () => this._sync()),
        ];
        this.connect('destroy', () => {
            extension._interface.disconnect(this._syncIds[0]);
            extension._schedule.disconnect(this._syncIds[1]);
        });
        this._sync();
    }

    _sync() {
        const auto = this._extension.isAuto();
        const dark = this._extension.isDark();
        if (auto) {
            this.set({
                checked: true,
                title: _('Auto'),
                iconName: 'dark-mode-symbolic',
            });
        } else if (dark) {
            this.set({
                checked: true,
                title: _('Dark Style'),
                iconName: 'dark-mode-symbolic',
            });
        } else {
            this.set({
                checked: false,
                title: _('Light Style'),
                iconName: 'weather-clear-symbolic',
            });
        }
    }
});

export default class DarkStyleScheduleExtension extends Extension {
    enable() {
        this._schedule = this.getSettings();
        this._interface = new Gio.Settings({schema_id: INTERFACE_SCHEMA});
        this._geoclue = null;
        this._timerId = 0;

        this._idleId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._idleId = 0;
            this._replaceToggle();
            return GLib.SOURCE_REMOVE;
        });
        GLib.Source.set_name_by_id(this._idleId, '[AnduinOS] Dark Style schedule');

        this._modeId = this._schedule.connect('changed::mode', () => this._arm());
        this._arm();
    }

    isDark() {
        return this._interface.get_string('color-scheme') === 'prefer-dark';
    }

    isAuto() {
        return this._schedule.get_string('mode') === 'sunset-sunrise';
    }

    setManual(dark) {
        this._schedule.set_string('mode', 'manual');
        this._applyScheme(dark, dark ? 'prefer-dark' : 'prefer-light');
    }

    setAuto() {
        this._schedule.set_string('mode', 'sunset-sunrise');
        this._arm();
    }

    cycle() {
        if (this.isAuto())
            this.setManual(false);
        else if (this.isDark())
            this.setAuto();
        else
            this.setManual(true);
    }

    _applyScheme(dark, scheme) {
        Main.layoutManager.screenTransition.run();
        this._interface.set_string('color-scheme', scheme);
        this._maybeSetYaru(dark);
    }

    _maybeSetYaru(preferDark) {
        if (St.Settings.get().gtkTheme !== 'Yaru')
            return;
        const currentlyDark =
            this._interface.get_string('gtk-theme').endsWith('-dark') &&
            this._interface.get_string('icon-theme').endsWith('-dark');
        if (currentlyDark === preferDark)
            return;
        const theme = preferDark ? 'Yaru-dark' : 'Yaru';
        this._interface.set_string('gtk-theme', theme);
        this._interface.set_string('icon-theme', theme);
    }

    _replaceToggle() {
        const qs = Main.panel.statusArea.quickSettings;
        if (!qs)
            return;

        this._hidden = [];
        this._toggle = new DarkStyleToggle(this);
        this._indicator = new QuickSettings.SystemIndicator();
        this._indicator.quickSettingsItems.push(this._toggle);

        const stock = qs._darkMode?.quickSettingsItems?.[0] ?? null;
        const sibling = qs._doNotDisturb?.quickSettingsItems?.[0] ?? stock;
        if (sibling && qs._addItemsBefore)
            qs._addItemsBefore([this._toggle], sibling, 1);
        else
            qs.addExternalIndicator(this._indicator);

        for (const item of qs._darkMode?.quickSettingsItems ?? []) {
            if (item === this._toggle)
                continue;
            item.visible = false;
            this._hidden.push(item);
        }
    }

    _arm() {
        this._clearTimer();
        if (!this.isAuto())
            return;
        this._applySun();
        this._ensureLocation(() => this._applySun());
    }

    _applySun() {
        if (!this.isAuto())
            return;
        const now = GLib.DateTime.new_now_utc().to_unix();
        const coords = this._coordinates();
        const decision = coords
            ? this._solarPlan(now, coords[0], coords[1])
            : this._fallbackPlan(now);
        this._applyScheme(
            decision.dark,
            decision.dark ? 'prefer-dark' : 'prefer-light',
        );
        const delay = Math.max(30, Math.min(12 * 3600, decision.next - now));
        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, delay, () => {
            this._timerId = 0;
            this._applySun();
            return GLib.SOURCE_REMOVE;
        });
    }

    _solarPlan(now, latitude, longitude) {
        const local = GLib.DateTime.new_now_local();
        const tomorrow = local.add_days(1);
        return plan(
            now,
            local.get_year(),
            local.get_month(),
            local.get_day_of_month(),
            tomorrow.get_year(),
            tomorrow.get_month(),
            tomorrow.get_day_of_month(),
            latitude,
            longitude,
        ) ?? this._fallbackPlan(now);
    }

    _fallbackPlan(now) {
        const local = GLib.DateTime.new_now_local();
        const minutes = local.get_hour() * 60 + local.get_minute();
        const sunrise = FALLBACK_SUNRISE_HOUR * 60;
        const sunset = FALLBACK_SUNSET_HOUR * 60;
        const dark = minutes < sunrise || minutes >= sunset;
        let target = sunrise;
        if (minutes >= sunrise && minutes < sunset)
            target = sunset;
        else if (minutes >= sunset)
            target = sunrise + 24 * 60;
        return {
            dark,
            next: now + ((target - minutes + 24 * 60) % (24 * 60)) * 60,
        };
    }

    _coordinates() {
        if (this._schedule.get_boolean('has-location')) {
            return [
                this._schedule.get_double('latitude'),
                this._schedule.get_double('longitude'),
            ];
        }
        return null;
    }

    _ensureLocation(done) {
        try {
            Geoclue.Simple.new(
                'org.gnome.Shell',
                Geoclue.AccuracyLevel.CITY,
                null,
                (simple, result) => {
                    try {
                        const client = Geoclue.Simple.new_finish(result);
                        const location = client.get_location();
                        const latitude = location.latitude;
                        const longitude = location.longitude;
                        if (Number.isFinite(latitude) && Number.isFinite(longitude) &&
                            !(latitude === 0 && longitude === 0)) {
                            this._schedule.set_double('latitude', latitude);
                            this._schedule.set_double('longitude', longitude);
                            this._schedule.set_boolean('has-location', true);
                            this._geoclue = client;
                        }
                    } catch (error) {
                        logError(error, 'AnduinOS Dark Style could not read GeoClue');
                    }
                    done();
                },
            );
        } catch (error) {
            logError(error, 'AnduinOS Dark Style GeoClue is unavailable');
            done();
        }
    }

    _clearTimer() {
        if (this._timerId) {
            GLib.Source.remove(this._timerId);
            this._timerId = 0;
        }
    }

    disable() {
        this._clearTimer();
        if (this._idleId) {
            GLib.Source.remove(this._idleId);
            this._idleId = 0;
        }
        if (this._modeId && this._schedule) {
            this._schedule.disconnect(this._modeId);
            this._modeId = 0;
        }
        for (const item of this._hidden ?? [])
            item.visible = true;
        this._hidden = [];
        this._toggle?.destroy();
        this._toggle = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._geoclue = null;
        this._schedule = null;
        this._interface = null;
    }
}
