export const ProxyMode = Object.freeze({
    NONE: 'none',
    MANUAL: 'manual',
    AUTO: 'auto',
});

export function isProxyEnabled(mode) {
    return mode === ProxyMode.MANUAL || mode === ProxyMode.AUTO;
}
