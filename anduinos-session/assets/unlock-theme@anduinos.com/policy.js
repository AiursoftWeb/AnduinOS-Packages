// SPDX-License-Identifier: GPL-3.0-or-later

// Only reuse the Fluent family we ship and test. Other custom themes keep
// GNOME's native unlock screen. Never turn a theme name into an arbitrary path.
export function fluentThemeName(name) {
    return typeof name === 'string' && name.length <= 128 &&
        /^Fluent(?:-[A-Za-z0-9]+)*$/.test(name);
}

// Own the application stylesheet only for the lifetime of unlock mode.
// Keeping this policy independent of Shell makes teardown/failure testable.
export class UnlockThemeController {
    constructor(loader) {
        this.loader = loader;
        this.path = null;
    }

    update(path) {
        const current = this.loader.current();
        if (current !== this.path) {
            // Another extension took ownership; do not clobber its theme.
            this.loader.removeReadability();
            this.path = null;
            return;
        }
        if (path === this.path)
            return;

        this.reset();
        if (!path)
            return;

        this.path = path;
        try {
            this.loader.set(path);
            this.loader.addReadability();
        } catch (error) {
            this.reset();
            this.loader.report(error);
        }
    }

    reset() {
        this.loader.removeReadability();
        if (this.path && this.loader.current() === this.path)
            this.loader.set(null);
        this.path = null;
    }
}
