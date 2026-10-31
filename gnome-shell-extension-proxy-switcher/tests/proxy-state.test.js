import {isProxyEnabled, ProxyMode} from
    '../assets/proxy-switcher@anduinos.com/state.js';

const cases = [
    [ProxyMode.NONE, false],
    [ProxyMode.MANUAL, true],
    [ProxyMode.AUTO, true],
    ['unexpected', false],
    [null, false],
];

for (const [mode, expected] of cases) {
    const actual = isProxyEnabled(mode);
    if (actual !== expected) {
        throw new Error(
            `isProxyEnabled(${JSON.stringify(mode)}) returned ${actual}; expected ${expected}`,
        );
    }
}

print(`proxy state tests passed (${cases.length} cases)`);
