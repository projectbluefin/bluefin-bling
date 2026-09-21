// Behavior harness for extensions/syncthing-toggle/toggle.js.
//
// toggle.js imports gi:// and resource:///org/gnome/shell modules that only
// exist inside gnome-shell, so plain `import` cannot load it. The harness reads
// the real source, rewrites only its import block into bindings taken from
// globalThis, and imports the result as a data: module. Everything below the
// import block — the logic under test — is the byte-for-byte shipped source.
//
// Scope: ServiceToggle construction, ServiceIndicator icon resolution, the
// 'clicked' orchestration, _runSystemctl argv, updateStatus, the status probe,
// the metered pause/resume reconciliation, the ~/Sync config seeding, and the
// use-after-destroy windows. The status subprocess is still distinguished from
// _runSystemctl's by SubprocessFlags; only the scenarios that exist to pin the
// status command itself look at its argv.
//
// The Gio.File stub deliberately implements ONLY the _async entry points. A
// regression to query_exists(null) / load_contents(null) /
// replace_contents(..., null) fails here with a TypeError rather than silently
// re-freezing the compositor.
//
// Usage: node syncthing_toggle_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

import {loadGnomeModule} from './gnome_module_loader.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const TOGGLE_JS = join(HERE, '..', 'extensions', 'syncthing-toggle', 'toggle.js');
const EXTENSION_JS = join(HERE, '..', 'extensions', 'syncthing-toggle', 'extension.js');

const STUBS = 'globalThis.__stStubs';

function loadToggleModule() {
    return loadGnomeModule({path: TOGGLE_JS, stubsExpression: STUBS});
}

// extension.js is the entry point GNOME Shell calls, and the only place the
// enable()/disable() lifecycle exists. It needs the same GNOME rewrite plus one
// more: its relative `./toggle.js` import cannot resolve from a data: URL, so it
// is bound to the toggle module this harness already loaded.
function toggleImportPattern() {
    return /^\s*import\s+(\{[^}]*\})\s+from\s+['"]\.\/toggle\.js['"];?\s*$/gm;
}

async function loadExtensionModule() {
    globalThis.__stStubs.__toggle = await loadToggleModule();
    if (!toggleImportPattern().test(readFileSync(EXTENSION_JS, 'utf8')))
        throw new Error("extension.js no longer imports './toggle.js' — harness rewrite is stale");

    return loadGnomeModule({
        path: EXTENSION_JS,
        stubsExpression: STUBS,
        rewrite: source => source.replace(
            toggleImportPattern(),
            (_line, named) => `const ${named} = globalThis.__stStubs.__toggle;`,
        ),
    });
}

// --- Stubs ------------------------------------------------------------------

class FakeMenu {
    constructor() {
        this.header = null;
        this.items = [];
        this.actions = [];
        this._settingsActions = {};
    }

    setHeader(icon, title) {
        this.header = {icon, title};
    }

    addMenuItem(item) {
        this.items.push(item);
    }

    addAction(label, callback) {
        const item = makeMenuAction(label, callback);
        this.actions.push(item);
        return item;
    }
}

// updateStatus() greys the Web GUI entry out while the daemon is down, so the
// item a section hands back has to answer setSensitive().
function makeMenuAction(label, callback) {
    return {
        label,
        callback,
        visible: true,
        sensitive: true,
        setSensitive(value) {
            this.sensitive = value;
        },
    };
}

class FakePopupMenuSection {
    constructor() {
        this.kind = 'section';
        this.actions = [];
    }

    addAction(label, callback) {
        const item = makeMenuAction(label, callback);
        this.actions.push(item);
        return item;
    }
}

class FakePopupSeparatorMenuItem {
    constructor() {
        this.kind = 'separator';
    }
}

class FakeQuickMenuToggle {
    constructor(params = {}) {
        this.constructorParams = params;
        this.title = params.title;
        this.gicon = params.gicon;
        this.toggleMode = params.toggleMode;
        this.subtitle = params.subtitle;
        this.checked = false;
        this.menu = new FakeMenu();
        this._handlers = new Map();
        this._handlerIds = new Map();
        this._nextHandlerId = 0;
        this.destroyed = false;
    }

    connect(signal, callback) {
        this._handlers.set(signal, callback);
        const id = ++this._nextHandlerId;
        this._handlerIds.set(id, signal);
        return id;
    }

    disconnect(id) {
        const signal = this._handlerIds.get(id);
        if (signal === undefined)
            return;
        this._handlerIds.delete(id);
        this._handlers.delete(signal);
    }

    handlerCount() {
        return this._handlerIds.size;
    }

    emit(signal) {
        return this._handlers.get(signal)?.();
    }

    set(props) {
        Object.assign(this, props);
    }

    destroy() {
        this.destroyed = true;
    }
}

class FakeSystemIndicator {
    constructor() {
        this.quickSettingsItems = [];
        this._indicators = [];
        // gnome-shell destroys the quick settings items and then the indicator,
        // so ServiceIndicator.destroy() is reachable twice; this counts how
        // often the base teardown actually ran.
        this.superDestroyCount = 0;
    }

    _addIndicator() {
        const indicator = {visible: false, gicon: null};
        this._indicators.push(indicator);
        return indicator;
    }

    destroy() {
        this.superDestroyCount += 1;
    }
}

// Stand-in for the GLib error domain. toggle.js reaches it through
// `e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.EXISTS)`.
const IO_ERROR_ENUM = {
    NOT_FOUND: 'g-io-error-not-found',
    EXISTS: 'g-io-error-exists',
    CANCELLED: 'g-io-error-cancelled',
    NOT_SUPPORTED: 'g-io-error-not-supported',
};

class FakeGioError extends Error {
    constructor(code, message) {
        super(message);
        this.code = code;
    }

    matches(domain, code) {
        return domain === IO_ERROR_ENUM && this.code === code;
    }
}

// Gio.FileInfo, reduced to the one attribute toggle.js sets. Real GFileInfo is
// a bag of typed attributes; a Map keyed by attribute name is the whole of it
// that matters here.
class FakeFileInfo {
    constructor() {
        this.attributes = new Map();
    }

    set_attribute_uint32(attribute, value) {
        this.attributes.set(attribute, value);
    }

    get_attribute_uint32(attribute) {
        return this.attributes.get(attribute);
    }
}

class FakeFileSystem {
    constructor() {
        this.dirs = new Set(['/']);
        this.files = new Map();
        this.created = [];
        this.writes = [];
        // Modes the code under test asked for, keyed by path. A plain object,
        // not a Map: scenarios hand these straight to JSON.stringify.
        this.modes = {};
        // Every set_attributes_async call, in order, with the flags it used —
        // NOFOLLOW_SYMLINKS is part of what the mode change has to get right.
        this.attributeCalls = [];
        // Stand-in for a filesystem with no unix modes (or a symlinked target).
        this.setAttributesFails = false;
    }

    addDir(path) {
        let current = '';
        for (const part of path.split('/')) {
            if (!part)
                continue;
            current += `/${part}`;
            this.dirs.add(current);
        }
    }

    addFile(path, text) {
        const idx = path.lastIndexOf('/');
        this.addDir(idx <= 0 ? '/' : path.slice(0, idx));
        this.files.set(path, text);
    }

    exists(path) {
        return this.dirs.has(path) || this.files.has(path);
    }
}

// Async-only Gio.File. Every finish() is reached through a queued callback, so
// the awaits in toggle.js suspend exactly as they do under GIO — and the
// synchronous entry points simply do not exist.
class FakeFile {
    constructor(path, fs) {
        this.path = path;
        this._fs = fs;
    }

    get_parent() {
        if (this.path === '/')
            return null;
        const idx = this.path.lastIndexOf('/');
        return new FakeFile(idx <= 0 ? '/' : this.path.slice(0, idx), this._fs);
    }

    query_info_async(_attributes, _flags, _priority, _cancellable, callback) {
        const found = this._fs.exists(this.path);
        queueMicrotask(() => callback(this, {found}));
    }

    query_info_finish(res) {
        if (!res.found)
            throw new FakeGioError(IO_ERROR_ENUM.NOT_FOUND, `No such file: ${this.path}`);
        return {kind: 'file-info'};
    }

    make_directory_async(_priority, _cancellable, callback) {
        const existed = this._fs.exists(this.path);
        queueMicrotask(() => callback(this, {existed}));
    }

    make_directory_finish(res) {
        if (res.existed)
            throw new FakeGioError(IO_ERROR_ENUM.EXISTS, `Already exists: ${this.path}`);
        this._fs.dirs.add(this.path);
        this._fs.created.push(this.path);
        return true;
    }

    set_attributes_async(info, flags, _priority, _cancellable, callback) {
        queueMicrotask(() => callback(this, {info, flags}));
    }

    set_attributes_finish(res) {
        if (this._fs.setAttributesFails) {
            throw new FakeGioError(
                IO_ERROR_ENUM.NOT_SUPPORTED,
                `Setting attributes not supported: ${this.path}`,
            );
        }
        const mode = res.info.get_attribute_uint32('unix::mode');
        if (mode !== undefined)
            this._fs.modes[this.path] = mode;
        this._fs.attributeCalls.push({path: this.path, flags: res.flags, mode});
        return [true, res.info];
    }

    load_contents_async(_cancellable, callback) {
        queueMicrotask(() => callback(this, {}));
    }

    load_contents_finish() {
        const text = this._fs.files.get(this.path);
        if (text === undefined)
            throw new FakeGioError(IO_ERROR_ENUM.NOT_FOUND, `No such file: ${this.path}`);
        return [true, new TextEncoder().encode(text), null];
    }

    replace_contents_bytes_async(bytes, _etag, makeBackup, flags, _cancellable, callback) {
        queueMicrotask(() => callback(this, {bytes, makeBackup, flags}));
    }

    replace_contents_finish(res) {
        const text = new TextDecoder('utf-8').decode(res.bytes.data ?? res.bytes);
        this._fs.files.set(this.path, text);
        this._fs.writes.push({
            path: this.path,
            flags: res.flags,
            makeBackup: res.makeBackup,
            text,
        });
        return [true, null];
    }
}

// Shape of a freshly generated syncthing config, trimmed to the elements
// toggle.js inspects. Tests that care about <defaults> pass their own.
const DEFAULT_GENERATED_CONFIG = `<configuration version="37">
    <device id="LOCAL-DEVICE-ID" name="host" compression="metadata"></device>
    <gui enabled="true" tls="false" debugging="false">
        <address>127.0.0.1:8384</address>
        <apikey>super-secret-api-key</apikey>
    </gui>
</configuration>`;

function makeStubs(options) {
    const log = {
        notifications: [],
        launchedUris: [],
        iconStrings: [],
        systemctl: [],
        statusSubprocesses: 0,
        errors: [],
        // Every Gio.Subprocess.new(), with the cancellable it was handed and
        // whether cancelling that cancellable actually killed the child.
        subprocesses: [],
        // GLib main-loop sources still installed, keyed by source id.
        sources: new Map(),
        nextSourceId: 1,
        openPreferencesCalls: 0,
        externalIndicators: [],
        // `syncthing generate` is kept out of log.systemctl so the verb
        // assertions stay about systemctl.
        generate: [],
        // One entry per `syncthing generate`: the recorded directory modes at
        // the instant that child was spawned.
        modesAtGenerate: [],
        // Callbacks held back by options.deferStatus / deferGenerate /
        // deferSystemctl, so a scenario can land disable() inside one specific
        // await window and then release it.
        pendingStatus: [],
        pendingGenerate: [],
        pendingSystemctl: [],
        systemctlDeferred: Boolean(options.deferSystemctl),
        statusCancelled: false,
    };

    const settingsValues = {
        'service-name': options.serviceName ?? 'syncthing.service',
        'icon-name': options.iconName ?? '',
        port: options.port ?? 8384,
        'start-stop-only': options.startStopOnly ?? false,
    };

    const settings = {
        get_string(key) {
            return String(settingsValues[key]);
        },
        get_int(key) {
            return Number(settingsValues[key]);
        },
        get_boolean(key) {
            return Boolean(settingsValues[key]);
        },
    };

    const homeDir = options.homeDir ?? '/home/tester';
    const stateDir = `${homeDir}/.local/state/syncthing`;
    const configPath = `${stateDir}/config.xml`;

    const fs = new FakeFileSystem();
    fs.setAttributesFails = options.setAttributesFails ?? false;
    fs.addDir(homeDir);
    if (options.syncDirExists ?? true)
        fs.addDir(`${homeDir}/Sync`);
    // Provisioned by default — dakota ships a config through /etc/skel, and
    // that is the case every pre-existing scenario runs under.
    if (options.configExists ?? true)
        fs.addFile(configPath, options.existingConfig ?? DEFAULT_GENERATED_CONFIG);
    log.fs = fs;

    // Release a callback held back by one of the defer options. GIO still
    // invokes a cancelled callback — with G_IO_ERROR_CANCELLED — which is
    // exactly the use-after-destroy window; 'ok' models the other one, where
    // the child answered before force_exit landed.
    log.releaseStatus = mode => {
        log.statusCancelled = mode === 'cancelled';
        for (const deliver of log.pendingStatus.splice(0))
            deliver();
    };
    log.releaseGenerate = () => {
        for (const deliver of log.pendingGenerate.splice(0))
            deliver();
    };
    log.releaseSystemctl = () => {
        log.systemctlDeferred = false;
        for (const deliver of log.pendingSystemctl.splice(0))
            deliver();
    };

    // What the status read reports. A successful start/stop moves it, so the
    // status read the toggle does after every call sees the unit it just acted
    // on rather than a frozen string. `is-active` prints one word; `status`
    // prints a journal-shaped block. options.statusStdout overrides both.
    let unitRunning = options.unitRunning ?? false;
    const statusText = argv => {
        if (options.statusStdout !== undefined)
            return options.statusStdout;
        if (argv[2] === 'is-active')
            return unitRunning ? 'active\n' : 'inactive\n';
        return unitRunning ? 'Active: active (running) since now' : 'Active: inactive (dead)';
    };

    // _runSystemctl uses SubprocessFlags.NONE; checkStatus uses STDOUT_PIPE.
    // Splitting on the flag keeps the status command out of these assertions.
    function makeSubprocess(argv, flags) {
        // Cancelling a Cancellable does not drop the pending callback: it still
        // fires, with G_IO_ERROR_CANCELLED. The record also tracks force_exit,
        // because the cancellable alone only abandons the local wait — the
        // systemctl child keeps running unless the extension kills it.
        const record = {argv, flags, cancellable: null, forcedExit: false};
        log.subprocesses.push(record);
        const proc = {
            force_exit() {
                record.forcedExit = true;
            },
        };

        if (flags === Gio.SubprocessFlags.STDOUT_PIPE) {
            log.statusSubprocesses += 1;
            log.statusArgv = argv;
            return Object.assign(proc, {
                communicate_utf8_async(_stdin, cancellable, callback) {
                    record.cancellable = cancellable;
                    const deliver = () => callback(this, 'res');
                    if (options.deferStatus)
                        log.pendingStatus.push(deliver);
                    else
                        queueMicrotask(deliver);
                },
                communicate_utf8_finish() {
                    if (record.cancellable?.cancelled || log.statusCancelled)
                        throw new FakeGioError(IO_ERROR_ENUM.CANCELLED, 'Operation was cancelled');
                    return [true, statusText(argv), ''];
                },
            });
        }

        const isGenerate = argv[0] !== 'systemctl';
        if (isGenerate) {
            log.generate.push(argv);
            // The directory modes as they stood the moment the child was
            // spawned. `syncthing generate` writes key.pem and the REST apikey
            // from here on, so anything tightened afterwards is tightened too
            // late — this snapshot is what pins the ordering.
            log.modesAtGenerate.push({...fs.modes});
        } else
            log.systemctl.push(argv);

        return Object.assign(proc, {
            wait_check_async(cancellable, callback) {
                record.cancellable = cancellable;
                const deliver = () => callback(this, 'res');
                if (isGenerate && options.deferGenerate)
                    log.pendingGenerate.push(deliver);
                else if (!isGenerate && log.systemctlDeferred)
                    log.pendingSystemctl.push(deliver);
                else
                    queueMicrotask(deliver);
            },
            wait_check_finish() {
                if (record.cancellable?.cancelled)
                    throw new FakeGioError(IO_ERROR_ENUM.CANCELLED, 'Operation was cancelled');
                if (isGenerate) {
                    if (options.generateFails)
                        throw new Error('syncthing generate failed');
                    if (!options.generateProducesNothing) {
                        const home = argv
                            .find(entry => entry.startsWith('--home='))
                            ?.slice('--home='.length);
                        fs.addFile(
                            `${home}/config.xml`,
                            options.generatedConfig ?? DEFAULT_GENERATED_CONFIG,
                        );
                    }
                    return true;
                }
                if (options.systemctlFails)
                    throw new Error('systemctl failed');
                if (argv[2] === 'start')
                    unitRunning = true;
                else if (argv[2] === 'stop')
                    unitRunning = false;
                return true;
            },
        });
    }

    class FakeCancellable {
        constructor() {
            this.cancelled = false;
            this._handlers = new Map();
            this._nextId = 0;
        }

        connect(callback) {
            const id = ++this._nextId;
            this._handlers.set(id, callback);
            return id;
        }

        disconnect(id) {
            this._handlers.delete(id);
        }

        cancel() {
            if (this.cancelled)
                return;
            this.cancelled = true;
            for (const callback of [...this._handlers.values()])
                callback();
        }

        get handlerCount() {
            return this._handlers.size;
        }
    }

    const networkMonitor = {
        metered: options.metered ?? false,
        handlers: new Map(),
        nextHandlerId: 0,
        get_network_metered() {
            return this.metered;
        },
        connect(signal, callback) {
            const id = ++this.nextHandlerId;
            this.handlers.set(id, {signal, callback});
            return id;
        },
        disconnect(id) {
            this.handlers.delete(id);
        },
        emit(signal) {
            for (const handler of [...this.handlers.values()]) {
                if (handler.signal === signal)
                    handler.callback();
            }
        },
    };

    const Gio = {
        SubprocessFlags: {NONE: 0, STDOUT_PIPE: 1},
        Subprocess: {
            new(argv, flags) {
                if (options.subprocessThrows)
                    throw new Error('spawn refused');
                return makeSubprocess(argv, flags);
            },
        },
        Cancellable: FakeCancellable,
        FileCreateFlags: {NONE: 0, PRIVATE: 1, REPLACE_DESTINATION: 2},
        FileQueryInfoFlags: {NONE: 0, NOFOLLOW_SYMLINKS: 1},
        FileInfo: FakeFileInfo,
        IOErrorEnum: IO_ERROR_ENUM,
        File: {
            new_for_path(path) {
                return new FakeFile(path, fs);
            },
        },
        NetworkMonitor: {
            get_default: () => networkMonitor,
        },
        icon_new_for_string(name) {
            log.iconStrings.push(name);
            return {kind: 'gicon', name};
        },
        app_info_launch_default_for_uri(uri) {
            if (options.launchThrows)
                throw new Error('no handler');
            log.launchedUris.push(uri);
            return true;
        },
    };

    const GObject = {
        registerClass(klass) {
            return klass;
        },
    };

    // Sources are recorded, never armed: the scenarios fire the callback
    // themselves, so a poll interval change cannot slow the suite down.
    const GLib = {
        PRIORITY_DEFAULT: 0,
        PRIORITY_LOW: 300,
        SOURCE_REMOVE: false,
        SOURCE_CONTINUE: true,
        get_home_dir() {
            return homeDir;
        },
        Bytes: {
            new(data) {
                return {data};
            },
        },
        timeout_add_seconds(_priority, seconds, callback) {
            const id = log.nextSourceId++;
            log.sources.set(id, {seconds, callback});
            return id;
        },
        Source: {
            remove(id) {
                log.sources.delete(id);
            },
        },
    };

    const Main = {
        notify(title, body) {
            log.notifications.push({title, body});
        },
        sessionMode: {allowSettings: options.allowSettings ?? true},
        panel: {
            statusArea: {
                quickSettings: {
                    addExternalIndicator(indicator) {
                        log.externalIndicators.push(indicator);
                    },
                },
            },
        },
    };

    // extension.js extends Extension and is constructed by the shell with no
    // arguments, so the stub supplies the same surface makeExtensionObject does.
    class FakeExtension {
        constructor() {
            this.uuid = 'syncthing-toggle@projectbluefin.io';
            this.path =
                options.extensionPath ??
                '/usr/share/gnome-shell/extensions/syncthing-toggle';
        }

        getSettings() {
            return settings;
        }

        openPreferences() {
            log.openPreferencesCalls += 1;
        }
    }

    const stubs = {
        Gio,
        GLib,
        GObject,
        Main,
        Extension: FakeExtension,
        PopupMenu: {
            PopupMenuSection: FakePopupMenuSection,
            PopupSeparatorMenuItem: FakePopupSeparatorMenuItem,
        },
        QuickMenuToggle: FakeQuickMenuToggle,
        SystemIndicator: FakeSystemIndicator,
        gettext: text => text,
    };

    return {stubs, log, settings, networkMonitor, paths: {homeDir, stateDir, configPath}};
}

function makeExtensionObject(settings, options) {
    const log = {openPreferencesCalls: 0};
    return {
        object: {
            uuid: 'syncthing-toggle@projectbluefin.io',
            path: options.extensionPath ?? '/usr/share/gnome-shell/extensions/syncthing-toggle',
            getSettings: () => settings,
            openPreferences() {
                log.openPreferencesCalls += 1;
            },
        },
        log,
    };
}

// Let queued microtask callbacks and the awaits chained behind them settle.
async function settle() {
    for (let i = 0; i < 200; i++)
        await Promise.resolve();
}

// --- Scenarios --------------------------------------------------------------

async function build(options) {
    const {stubs, log, settings, networkMonitor, paths} = makeStubs(options);
    globalThis.__stStubs = stubs;
    globalThis.logError = (...args) => log.errors.push(args.map(String));

    const module = await loadToggleModule();
    const extension = makeExtensionObject(settings, options);
    const indicator = new module.ServiceIndicator(extension.object);
    return {indicator, log, networkMonitor, paths, extensionLog: extension.log};
}

// Drive the real entry point instead of constructing the indicator directly:
// enable()/disable() live in extension.js and are the only place the lifecycle
// — the initial reconcile, the poll source, the teardown — can be observed.
async function buildExtension(options) {
    const {stubs, log, networkMonitor, paths} = makeStubs(options);
    globalThis.__stStubs = stubs;
    globalThis.logError = (...args) => log.errors.push(args.map(String));

    const module = await loadExtensionModule();
    const extension = new module.default();
    extension.enable();
    await settle();
    return {extension, indicator: extension._indicator, log, networkMonitor, paths};
}

// The single installed poll source. Reported rather than asserted on here so an
// extension that installs none produces a failing report, not a crash.
function pollSource(log) {
    const [id] = [...log.sources.keys()];
    if (id === undefined)
        return {id: null, seconds: 0, callback: () => null};
    return {id, ...log.sources.get(id)};
}

function toggleState(indicator) {
    return {
        checked: indicator._toggle.checked,
        subtitle: indicator._toggle.subtitle,
        indicatorVisible: indicator._indicator.visible,
    };
}

// toggleState plus the Web GUI entry, for the scenarios that assert nothing
// moved rather than that something specific did.
function widgetState(indicator) {
    return {
        ...toggleState(indicator),
        webGuiSensitive: indicator._toggle.webGuiItem.sensitive,
    };
}

function verbs(log) {
    return log.subprocesses.filter(call => call.argv[0] === 'systemctl').map(call => call.argv[2]);
}

const scenarios = {
    // The indicator falls back to the shipped symbolic icon only when the
    // icon-name setting is blank; a custom value is used verbatim after trim.
    async 'icon-resolution'(options) {
        const {indicator, log} = await build(options);
        return {
            iconStrings: log.iconStrings,
            indicatorGicon: indicator._indicator.gicon?.name ?? null,
            toggleGicon: indicator._toggle.gicon?.name ?? null,
        };
    },

    // ServiceToggle's constructor wires the Quick Settings widget: title,
    // toggle mode, the Loading subtitle, the menu header, the Web GUI action,
    // a separator, and a settings entry registered under the extension uuid.
    async 'toggle-construction'(options) {
        const {indicator} = await build(options);
        const toggle = indicator._toggle;
        return {
            title: toggle.title,
            toggleMode: toggle.toggleMode,
            subtitle: toggle.subtitle,
            headerTitle: toggle.menu.header?.title ?? null,
            headerIconIsToggleIcon: toggle.menu.header?.icon === toggle.gicon,
            sectionActionLabels: toggle._itemsSection.actions.map(a => a.label),
            menuItemKinds: toggle.menu.items.map(i => i.kind),
            menuActionLabels: toggle.menu.actions.map(a => a.label),
            settingsActionUuids: Object.keys(toggle.menu._settingsActions),
            settingsActionVisible: toggle.menu.actions[0]?.visible ?? null,
            quickSettingsItemCount: indicator.quickSettingsItems.length,
            quickSettingsItemIsToggle: indicator.quickSettingsItems[0] === toggle,
            webGuiItemIsSectionAction: toggle.webGuiItem === toggle._itemsSection.actions[0],
        };
    },

    // The Web GUI item builds its URL from the port setting at click time.
    async 'web-gui-url'(options) {
        const {indicator, log} = await build(options);
        indicator._toggle._itemsSection.actions[0].callback();
        return {launchedUris: log.launchedUris, errors: log.errors};
    },

    // Extension Settings routes to the extension object's openPreferences().
    async 'settings-action'(options) {
        const {indicator, extensionLog} = await build(options);
        indicator._toggle.menu.actions[0].callback();
        return {openPreferencesCalls: extensionLog.openPreferencesCalls};
    },

    // updateStatus is the single writer of indicator visibility and the
    // toggle's checked/subtitle pair.
    async 'update-status'(options) {
        const {indicator} = await build(options);
        indicator.updateStatus(true);
        const active = widgetState(indicator);
        indicator.updateStatus(false);
        const inactive = widgetState(indicator);
        return {active, inactive};
    },

    // The 'clicked' handler notifies, then drives systemctl. options.checked is
    // the toggle state GNOME Shell has already applied when the signal fires.
    async 'clicked'(options) {
        const {indicator, log} = await build(options);
        indicator._toggle.checked = options.checked ?? true;
        await indicator._toggle._handlers.get('clicked')();
        await settle();
        return {
            notifications: log.notifications,
            systemctl: log.systemctl,
            generate: log.generate,
            createdDirs: log.fs.created,
            pausedForMetered: indicator._pausedForMetered,
            checked: indicator._toggle.checked,
            statusSubprocesses: log.statusSubprocesses,
            errors: log.errors,
        };
    },

    // enable() reconciles once: it reads the unit state nobody clicked, and —
    // because notify::network-metered only fires on a *change* — checks the
    // current metered state itself. A session that starts on mobile data with
    // the unit enabled from last time gets no signal at all.
    async 'enable'(options) {
        const {indicator, log} = await buildExtension(options);
        const source = pollSource(log);
        return {
            verbs: verbs(log),
            statusSubprocesses: log.statusSubprocesses,
            notifications: log.notifications,
            toggle: toggleState(indicator),
            installedSources: log.sources.size,
            pollSeconds: source.seconds,
            programs: [...new Set(log.subprocesses.map(call => call.argv[0]))],
            externalIndicators: log.externalIndicators.length,
            errors: log.errors,
        };
    },

    // The unit can be started or stopped by anything on the system, so the
    // toggle re-reads it on a timer instead of trusting its own last click.
    async 'poll-tick'(options) {
        // One options object, mutated in place: the stubs read it lazily, so
        // the scenario can move the world between phases.
        const opts = {...options, unitRunning: true};
        const {indicator, log} = await buildExtension(opts);
        const before = toggleState(indicator);
        const source = pollSource(log);

        // The unit dies behind the extension's back.
        opts.statusStdout = 'Active: inactive (dead)';
        log.statusSubprocesses = 0;
        const returnValue = source.callback();
        await settle();
        return {before, returnValue, statusSubprocesses: log.statusSubprocesses, after: toggleState(indicator)};
    },

    // disable() must leave nothing that can fire against torn-down widgets:
    // no source, no signal handlers, and no subprocess a late callback spawns.
    async 'disable'(options) {
        const {extension, indicator, log, networkMonitor} = await buildExtension({
            ...options,
            unitRunning: true,
        });
        const source = pollSource(log);
        const toggle = indicator._toggle;
        const cancellable = indicator._cancellable;

        extension.disable();
        log.subprocesses.length = 0;
        log.notifications.length = 0;

        // A stale tick, a late click and a late metered signal all arrive after
        // teardown in a real session.
        const staleTimerReturn = source.callback();
        toggle.emit('clicked');
        networkMonitor.metered = true;
        networkMonitor.emit('notify::network-metered');
        await settle();

        let secondDestroyError = null;
        try {
            indicator.destroy();
        } catch (error) {
            secondDestroyError = String(error?.message ?? error);
        }

        return {
            liveSources: log.sources.size,
            networkMonitorHandlers: networkMonitor.handlers.size,
            toggleHandlers: toggle.handlerCount(),
            cancellableCancelled: Boolean(cancellable?.cancelled),
            superDestroyCount: indicator.superDestroyCount,
            staleTimerReturn,
            subprocessesAfterDisable: log.subprocesses.map(call => call.argv),
            notificationsAfterDisable: log.notifications,
            secondDestroyError,
        };
    },

    // enable()'s status read is still in flight when disable() lands.
    async 'cancellation'(options) {
        const {stubs, log, networkMonitor} = makeStubs({...options, unitRunning: true});
        globalThis.__stStubs = stubs;
        globalThis.logError = (...args) => log.errors.push(args.map(String));

        const module = await loadExtensionModule();
        const extension = new module.default();
        extension.enable();
        // No settle(): the subprocess callbacks are still queued.
        const inFlight = log.subprocesses.length;
        const indicator = extension._indicator;
        const cancellable = indicator._cancellable;
        const subtitleBefore = indicator._toggle.subtitle;

        extension.disable();
        await settle();

        return {
            inFlightAtDisable: inFlight,
            everyCallCancellable:
                log.subprocesses.length > 0 &&
                log.subprocesses.every(call => Boolean(call.cancellable)),
            // A Cancellable only abandons the local wait; the systemctl child
            // survives unless cancellation is wired to force_exit().
            everySubprocessForcedExit:
                log.subprocesses.length > 0 &&
                log.subprocesses.every(call => call.forcedExit === true),
            cancellableHandlersLeft: cancellable ? cancellable.handlerCount : -1,
            subtitleBefore,
            subtitleAfter: indicator._toggle.subtitle,
            indicatorVisible: indicator._indicator.visible,
            notifications: log.notifications,
            networkMonitorHandlers: networkMonitor.handlers.size,
        };
    },

    // Turning the toggle on over a metered connection is refused before
    // anything is spawned.
    async 'metered-click'(options) {
        const {indicator, log} = await buildExtension({...options, metered: true});
        log.subprocesses.length = 0;
        log.systemctl.length = 0;
        log.notifications.length = 0;

        indicator._toggle.checked = true;
        await indicator._toggle.emit('clicked');
        await settle();
        return {
            systemctl: log.systemctl,
            checked: indicator._toggle.checked,
            notifications: log.notifications,
        };
    },

    // Going metered while the unit runs pauses it, and the pause is announced
    // only once the unit is really down.
    async 'metered-signal'(options) {
        const {extension, indicator, log, networkMonitor} = await buildExtension({
            ...options,
            unitRunning: true,
        });
        log.subprocesses.length = 0;
        log.notifications.length = 0;

        // An unmetered notify must not stop anything.
        networkMonitor.emit('notify::network-metered');
        await settle();
        const whileUnmetered = verbs(log);

        networkMonitor.metered = true;
        log.subprocesses.length = 0;
        networkMonitor.emit('notify::network-metered');
        await settle();

        const result = {
            whileUnmetered,
            whenMetered: verbs(log),
            notifications: log.notifications,
            toggle: toggleState(indicator),
            errors: log.errors,
        };

        extension.disable();
        log.subprocesses.length = 0;
        networkMonitor.emit('notify::network-metered');
        await settle();
        result.afterDisable = verbs(log);
        return result;
    },

    // disable() lands in the window between a systemctl call that succeeded and
    // the notification it would justify.
    async 'disable-mid-click'(options) {
        const {extension, indicator, log} = await buildExtension(options);
        log.subprocesses.length = 0;
        log.systemctl.length = 0;
        log.notifications.length = 0;
        log.statusSubprocesses = 0;

        indicator._toggle.checked = true;
        const clicked = indicator._toggle.emit('clicked');
        // Wait for `start` to have succeeded and the status re-read to be in
        // flight, then tear the extension down under it.
        for (let i = 0; i < 50 && log.statusSubprocesses === 0; i++)
            await Promise.resolve();
        const startedBeforeDisable = log.systemctl.map(argv => argv[2]);
        extension.disable();
        await clicked;
        await settle();

        return {
            startedBeforeDisable,
            systemctl: log.systemctl,
            notifications: log.notifications,
            subtitle: indicator._toggle.subtitle,
        };
    },

    // systemctl can refuse: a missing unit, a masked unit, a failing ExecStart.
    // Announcing the new state before the call means claiming "sharing enabled"
    // for a service that never came up, and `enable` would make it stick.
    async 'failed-start'(options) {
        const {indicator, log} = await buildExtension(options);
        log.subprocesses.length = 0;
        log.systemctl.length = 0;
        log.notifications.length = 0;
        log.errors.length = 0;

        options.systemctlFails = true;
        indicator._toggle.checked = true;
        await indicator._toggle.emit('clicked');
        await settle();
        return {
            verbs: verbs(log),
            notifications: log.notifications,
            toggle: toggleState(indicator),
            errors: log.errors,
        };
    },

    // The same rule on the metered path. Saying "paused" while the unit keeps
    // replicating is worse than silence: the user reads it as "safe to stay on
    // mobile data", which is the one thing the guard exists to prevent.
    async 'failed-metered-stop'(options) {
        const opts = {...options, unitRunning: true};
        const {log, networkMonitor} = await buildExtension(opts);
        log.subprocesses.length = 0;
        log.notifications.length = 0;
        log.errors.length = 0;

        opts.systemctlFails = true;
        networkMonitor.metered = true;
        networkMonitor.emit('notify::network-metered');
        await settle();
        return {verbs: verbs(log), notifications: log.notifications, errors: log.errors};
    },

    // checkStatus' own probe: which command it runs and what it concludes. The
    // poll fires this on a timer, so the command has to stay cheap.
    async 'status-probe'(options) {
        const {indicator, log} = await build(options);
        await indicator.checkStatus();
        await settle();
        return {
            statusArgv: log.statusArgv ?? null,
            statusSubprocesses: log.statusSubprocesses,
            widgets: widgetState(indicator),
            errors: log.errors,
        };
    },

    // A metered network arrives while sharing is on, then leaves again. Both
    // edges must run the same verb set as the equivalent manual toggle.
    async 'metered-transition'(options) {
        const {indicator, log, networkMonitor} = await buildExtension({
            ...options,
            unitRunning: options.unitRunning ?? true,
        });
        if (options.checked !== undefined)
            indicator._toggle.checked = options.checked;
        if (options.pausedForMetered !== undefined)
            indicator._pausedForMetered = options.pausedForMetered;
        log.systemctl.length = 0;
        log.notifications.length = 0;

        networkMonitor.metered = true;
        networkMonitor.emit('notify::network-metered');
        await settle();
        const paused = {
            systemctl: [...log.systemctl],
            notifications: [...log.notifications],
            pausedForMetered: indicator._pausedForMetered,
            toggle: toggleState(indicator),
        };

        log.systemctl.length = 0;
        log.notifications.length = 0;

        networkMonitor.metered = false;
        networkMonitor.emit('notify::network-metered');
        await settle();
        const resumed = {
            systemctl: [...log.systemctl],
            notifications: [...log.notifications],
            pausedForMetered: indicator._pausedForMetered,
            toggle: toggleState(indicator),
        };

        return {paused, resumed, errors: log.errors};
    },

    // Turning the toggle on over a metered link: refused now, remembered, and
    // honoured by the next unmetered transition.
    async 'metered-click-then-unmeter'(options) {
        const {indicator, log, networkMonitor} = await buildExtension({
            ...options,
            metered: true,
        });
        log.systemctl.length = 0;
        log.notifications.length = 0;

        indicator._toggle.checked = true;
        await indicator._toggle.emit('clicked');
        await settle();
        const refused = {
            systemctl: [...log.systemctl],
            generate: [...log.generate],
            notifications: [...log.notifications],
            checked: indicator._toggle.checked,
            pausedForMetered: indicator._pausedForMetered,
        };

        log.systemctl.length = 0;
        log.notifications.length = 0;

        networkMonitor.metered = false;
        networkMonitor.emit('notify::network-metered');
        await settle();
        return {
            refused,
            resumed: {
                systemctl: [...log.systemctl],
                notifications: [...log.notifications],
                pausedForMetered: indicator._pausedForMetered,
            },
            errors: log.errors,
        };
    },

    // _ensureSyncFolderConfig from an empty state dir: which directories it
    // creates, how it invokes syncthing, and exactly what it writes back.
    async 'config-seed'(options) {
        const {indicator, log, paths} = await build({
            configExists: false,
            syncDirExists: false,
            ...options,
        });
        await indicator._ensureSyncFolderConfig();
        await settle();
        return {
            paths,
            createdDirs: log.fs.created,
            generate: log.generate,
            modes: log.fs.modes,
            modesAtGenerate: log.modesAtGenerate,
            attributeCalls: log.fs.attributeCalls,
            writes: log.fs.writes,
            errors: log.errors,
        };
    },

    // disable() destroys the quick settings items before the indicator, so a
    // status callback arriving afterwards must not reach updateStatus().
    // options.release picks the delivery: 'cancelled' is G_IO_ERROR_CANCELLED,
    // 'ok' is the child having answered before force_exit landed.
    async 'destroy-during-status'(options) {
        const {extension, indicator, log} = await buildExtension({
            ...options,
            deferStatus: true,
        });
        const pending = indicator.checkStatus();
        await settle();
        const before = widgetState(indicator);

        extension.disable();
        const forceExitsAfterCancel = log.subprocesses
            .filter(call => call.forcedExit)
            .map(call => call.argv[0]);

        log.releaseStatus(options.release ?? 'cancelled');
        await pending;
        await settle();

        return {
            before,
            after: widgetState(indicator),
            forceExitsAfterCancel: [...new Set(forceExitsAfterCancel)],
            errors: log.errors,
        };
    },

    // disable() lands while `syncthing generate` is still running: neither the
    // config rewrite nor the systemctl sequence behind it may resume.
    async 'destroy-during-seed'(options) {
        const {extension, indicator, log} = await buildExtension({
            configExists: false,
            syncDirExists: false,
            deferGenerate: true,
            ...options,
        });
        log.systemctl.length = 0;
        log.notifications.length = 0;

        indicator._toggle.checked = true;
        const pending = indicator._toggle.emit('clicked');
        await settle();
        const generateStarted = log.generate.length;

        extension.disable();
        log.releaseGenerate();
        await pending;
        await settle();

        return {
            generateStarted,
            systemctl: log.systemctl,
            writtenPaths: log.fs.writes.map(write => write.path),
            forceExits: [
                ...new Set(
                    log.subprocesses.filter(call => call.forcedExit).map(call => call.argv[0]),
                ),
            ],
            notifications: log.notifications,
            errors: log.errors,
        };
    },

    // disable() lands while the start verb itself is outstanding: the status
    // refresh chained behind it must never spawn.
    async 'destroy-during-systemctl'(options) {
        const {extension, indicator, log} = await buildExtension({
            ...options,
            deferSystemctl: true,
        });
        log.systemctl.length = 0;
        log.notifications.length = 0;
        log.statusSubprocesses = 0;

        indicator._toggle.checked = true;
        const pending = indicator._toggle.emit('clicked');
        await settle();
        const started = log.systemctl.map(argv => argv[2]);

        extension.disable();
        log.releaseSystemctl();
        await pending;
        await settle();

        return {
            started,
            verbs: log.systemctl.map(argv => argv[2]),
            statusSubprocesses: log.statusSubprocesses,
            notifications: log.notifications,
            errors: log.errors,
        };
    },
};

const [scenarioName, optionsJson] = process.argv.slice(2);
const scenario = scenarios[scenarioName];
if (!scenario) {
    console.error(`unknown scenario: ${scenarioName}`);
    process.exit(2);
}

scenario(optionsJson ? JSON.parse(optionsJson) : {})
    .then(result => process.stdout.write(JSON.stringify(result)))
    .catch(error => {
        console.error(error?.stack ?? String(error));
        process.exit(1);
    });
