import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

// Behavioral harness for patched DING GTK 4 modules (emulateX11WindowType.js,
// desktopGrid.js, desktopManager.js) without requiring a running Mutter session.
function assert(cond, msg) {
    if (!cond)
        throw new Error(`Assertion failed: ${msg}`);
}

const extDir = ARGV[0];
const tmpDir = ARGV[1];

function writeFile(relPath, content) {
    const full = GLib.build_filenamev([tmpDir, relPath]);
    GLib.mkdir_with_parents(GLib.path_get_dirname(full), 0o755);
    GLib.file_set_contents(full, content);
    return full;
}

function readExt(relPath) {
    const [ok, bytes] = GLib.file_get_contents(GLib.build_filenamev([extDir, relPath]));
    assert(ok, `read ${relPath}`);
    return new TextDecoder().decode(bytes);
}

// Create lightweight ESM stubs for Shell-only imports so gjs can load
// emulateX11WindowType.js directly as an ES module.
writeFile('stubs/clutter.js', `
export default class Clutter {
    static Clone = class Clone {
        constructor(props) {
            Object.assign(this, props);
            this.destroyed = false;
            this.parent = null;
        }
        destroy() {
            this.destroyed = true;
            if (this.parent) {
                this.parent.children = this.parent.children.filter(c => c !== this);
                this.parent = null;
            }
        }
    };
}
`);

writeFile('stubs/gio.js', `
export const busSubscriptions = new Map();
let nextSubId = 1;
export default {
    DBusSignalFlags: { NONE: 0 },
    DBus: {
        session: {
            signal_subscribe(_sender, _iface, _member, _path, _arg0, _flags, cb) {
                const id = nextSubId++;
                busSubscriptions.set(id, cb);
                return id;
            },
            signal_unsubscribe(id) {
                busSubscriptions.delete(id);
            },
        },
    },
};
`);

writeFile('stubs/meta.js', `
export default {
    WindowType: {
        NORMAL: 0,
        DESKTOP: 1,
        DOCK: 2,
    },
};
`);

writeFile('stubs/main.js', `
export const layoutManager = {
    uiGroup: {
        children: [],
        add_child(actor) {
            actor.parent = this;
            this.children.push(actor);
        },
    },
};
`);

let emulateSrc = readExt('emulateX11WindowType.js')
    .replace("import Clutter from 'gi://Clutter';", "import Clutter from './stubs/clutter.js';")
    .replace("import Gio from 'gi://Gio';", "import Gio from './stubs/gio.js';")
    .replace("import Meta from 'gi://Meta';", "import Meta from './stubs/meta.js';")
    .replace("import * as Main from 'resource:///org/gnome/shell/ui/main.js';", "import * as Main from './stubs/main.js';");
writeFile('emulateX11WindowType.js', emulateSrc);

// Mock global Shell environment

class Emitter {
    constructor() {
        this._nextId = 1;
        this._handlers = new Map();
        this._objOwners = new Map();
    }
    connect(sig, cb) {
        const id = this._nextId++;
        this._handlers.set(id, {sig, cb});
        return id;
    }
    connect_after(sig, cb) {
        return this.connect(sig, cb);
    }
    connectObject(sig, cb, owner) {
        const id = this.connect(sig, cb);
        if (!this._objOwners.has(owner))
            this._objOwners.set(owner, []);
        this._objOwners.get(owner).push(id);
        return id;
    }
    disconnect(id) {
        this._handlers.delete(id);
    }
    disconnectObject(owner) {
        for (const id of this._objOwners.get(owner) ?? [])
            this._handlers.delete(id);
        this._objOwners.delete(owner);
    }
    emit(sig, ...args) {
        for (const {sig: s, cb} of [...this._handlers.values()]) {
            if (s === sig)
                cb(this, ...args);
        }
    }
}

class FakeActor extends Emitter {
    constructor(win) {
        super();
        this.meta_window = win;
        this.mapped = false;
    }
}

class FakeWindow extends Emitter {
    constructor(title) {
        super();
        this._title = title;
        this._type = 0; // NORMAL
        this._rect = {x: 120, y: 90, width: 1280, height: 768};
        this.minimized = false;
        this.fullscreen = false;
        this.above = false;
        this.sticked = false;
        this.lowerCalls = 0;
        this.moveCalls = 0;
        this.actor = new FakeActor(this);
    }
    get_title() { return this._title; }
    get_window_type() { return this._type; }
    set_type(t) { this._type = t; }
    get_frame_rect() { return {...this._rect}; }
    get_compositor_private() { return this.actor; }
    move_frame(_userOp, x, y) {
        this.moveCalls++;
        this._rect.x = x;
        this._rect.y = y;
        this.emit('position-changed');
    }
    unminimize() { this.minimized = false; }
    unmake_fullscreen() { this.fullscreen = false; }
    make_above() { this.above = true; }
    unmake_above() { this.above = false; }
    stick() { this.sticked = true; }
    unstick() { this.sticked = false; }
    lower() { this.lowerCalls++; }
}

let activeStack = [];
globalThis.global = {
    display: Object.assign(new Emitter(), {
        sort_windows_by_stacking: list => list,
    }),
    window_manager: new Emitter(),
    workspace_manager: {
        get_active_workspace: () => ({
            list_windows: () => activeStack,
        }),
    },
};

const {EmulateX11WindowType} = await import(`file://${tmpDir}/emulateX11WindowType.js`);
const Main = await import(`file://${tmpDir}/stubs/main.js`);
const Meta = (await import(`file://${tmpDir}/stubs/meta.js`)).default;
const {busSubscriptions} = await import(`file://${tmpDir}/stubs/gio.js`);

// Case 1: Desktop window creation, initial placement, and suspend-inhibit clone
const mgr = new EmulateX11WindowType();
mgr.setMonitorData([
    {x: 0, y: 0, width: 1280, height: 800, windowMarginLeft: 0, windowMarginTop: 32, windowMarginRight: 0, windowMarginBottom: 0},
    {x: 1280, y: 0, width: 1920, height: 1080, windowMarginLeft: 0, windowMarginTop: 0, windowMarginRight: 0, windowMarginBottom: 48},
]);
const hiddenWindows = new Set();
mgr.setWaylandClient({
    query_window_belongs_to: () => true,
    hide_from_window_list: w => hiddenWindows.add(w),
    show_in_window_list: w => hiddenWindows.delete(w),
});
mgr.enable();

const win1 = new FakeWindow('Desktop Icons 1');
global.display.emit('window-created', win1);
assert(win1.get_window_type() === Meta.WindowType.DESKTOP, 'win1 must become DESKTOP');
assert(win1.get_frame_rect().x === 0 && win1.get_frame_rect().y === 32, 'win1 initial (0, 32)');
assert(hiddenWindows.has(win1), 'win1 hidden from window list');
assert(Main.layoutManager.uiGroup.children.length === 1, 'suspend inhibit clone attached');
const clone1 = Main.layoutManager.uiGroup.children[0];
assert(clone1.opacity === 0 && clone1.reactive === false && clone1.width === 1 && clone1.height === 1, 'clone must be 1x1 invisible non-reactive');

// Case 2: Workarea margin change (dash-to-panel hides top 32px bar, adds bottom 48px bar)
mgr.setMonitorData([
    {x: 0, y: 0, width: 1280, height: 800, windowMarginLeft: 0, windowMarginTop: 0, windowMarginRight: 0, windowMarginBottom: 48},
    {x: 1280, y: 0, width: 1920, height: 1080, windowMarginLeft: 0, windowMarginTop: 0, windowMarginRight: 0, windowMarginBottom: 48},
]);
mgr.refreshWindowsPosition();
assert(win1.get_frame_rect().x === 0 && win1.get_frame_rect().y === 0, 'win1 updated to (0, 0) on margin change');

// Case 3: Compositor force-placement drift snaps back without recursion
const beforeMoves = win1.moveCalls;
win1._rect.x = 64;
win1._rect.y = 64;
win1.emit('position-changed');
assert(win1.get_frame_rect().x === 0 && win1.get_frame_rect().y === 0, 'win1 snapped back to (0, 0)');
assert(win1.moveCalls === beforeMoves + 1, 'exactly 1 corrective move_frame without recursion');

// Case 4: Minimize / fullscreen guards
win1.minimized = true;
win1.emit('notify::minimized');
assert(win1.minimized === false, 'win1 unminimized automatically');
win1.fullscreen = true;
win1.emit('notify::fullscreen');
assert(win1.fullscreen === false, 'win1 unfullscreened automatically');

// Case 5: Dual-monitor stacking guard (lowers below NORMAL, never ping-pongs with DESKTOP)
const win2 = new FakeWindow('Desktop Icons 2');
global.display.emit('window-created', win2);
assert(win2.get_frame_rect().x === 1280 && win2.get_frame_rect().y === 0, 'win2 placed on second monitor');
assert(Main.layoutManager.uiGroup.children.length === 2, 'second monitor clone attached');

activeStack = [win1, win2];
win2.emit('raised');
assert(win2.lowerCalls === 0, 'must not ping-pong when only DESKTOP window is below');

const normalWin = new FakeWindow('Files');
activeStack = [normalWin, win1, win2];
win1.emit('raised');
assert(win1.lowerCalls === 1, 'must lower when NORMAL window is below');

// Case 6: Unlock refreshes placement & disable cleans up all clones/subscriptions
win1._rect.y = 100;
for (const cb of busSubscriptions.values())
    cb(null, null, null, null, null, new GLib.Variant('(b)', [false]));
assert(win1.get_frame_rect().y === 0, 'screen unlock restored win1 placement');

mgr.disable();
assert(Main.layoutManager.uiGroup.children.length === 0, 'all suspend inhibit clones destroyed on disable');
assert(busSubscriptions.size === 0, 'ScreenSaver subscription cleaned up on disable');

print('6 GTK4 DING behavioral cases passed');
