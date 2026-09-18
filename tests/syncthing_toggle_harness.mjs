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
// the metered pause/resume reconciliation, the config.xml seeding, and the
// destroyed-flag guards that keep a cancelled callback off finalized widgets.
//
// The Gio.File stub deliberately implements ONLY the _async entry points. A
// regression back to query_exists(null) / load_contents(null) /
// replace_contents(..., null) fails here with a TypeError rather than silently
// re-freezing the compositor.
//
// Usage: node syncthing_toggle_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const TOGGLE_JS = join(HERE, '..', 'extensions', 'syncthing-toggle', 'toggle.js');

const IMPORT_LINE_RE =
    /^\s*import\s+(?:(\*\s+as\s+\w+)|(\{[^}]*\})|(\w+))\s+from\s+['"](?:gi:\/\/|resource:\/\/\/)[^'"]+['"];?\s*$/gm;

function loadToggleModule() {
    const source = readFileSync(TOGGLE_JS, 'utf8');
    const matches = [...source.matchAll(IMPORT_LINE_RE)];
    if (matches.length === 0)
        throw new Error('no gi:// or resource:/// imports found — harness rewrite is stale');

    const rewritten = source.replace(
        IMPORT_LINE_RE,
        (_line, namespaceImport, namedImport, defaultImport) => {
            if (namespaceImport) {
                const name = namespaceImport.split(/\s+as\s+/)[1];
                return `const ${name} = globalThis.__stStubs.${name};`;
            }
            if (namedImport) {
                // `{ gettext as _ }` is import syntax; destructuring needs `{ gettext: _ }`.
                const pattern = namedImport.replace(/\s+as\s+/g, ': ');
                return `const ${pattern} = globalThis.__stStubs;`;
            }
            return `const ${defaultImport} = globalThis.__stStubs.${defaultImport};`;
        },
    );
    const url = `data:text/javascript;base64,${Buffer.from(rewritten, 'utf8').toString('base64')}`;
    return import(url);
}

// --- Stubs ------------------------------------------------------------------

// Stand-in for the GLib error domain. toggle.js reaches it through
// `e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.EXISTS)`.
const IO_ERROR_ENUM = {
    NOT_FOUND: 'g-io-error-not-found',
    EXISTS: 'g-io-error-exists',
    CANCELLED: 'g-io-error-cancelled',
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

class FakeFileSystem {
    constructor() {
        this.dirs = new Set(['/']);
        this.files = new Map();
        this.created = [];
        this.writes = [];
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
// the awaits in toggle.js suspend exactly as they do under GIO.
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
        const pending = {bytes, makeBackup, flags};
        queueMicrotask(() => callback(this, pending));
    }

    replace_contents_finish(res) {
        const text = new TextDecoder('utf-8').decode(res.bytes.data ?? res.bytes);
        this._fs.files.set(this.path, text);
        this._fs.writes.push({path: this.path, flags: res.flags, makeBackup: res.makeBackup, text});
        return [true, null];
    }
}

class FakeCancellable {
    constructor(log) {
        this._log = log;
        this._handlers = new Map();
        this._nextId = 1;
        this.cancelled = false;
    }

    connect(callback) {
        const id = this._nextId++;
        this._handlers.set(id, callback);
        this._log.cancellableHandlers += 1;
        return id;
    }

    disconnect(id) {
        if (this._handlers.delete(id))
            this._log.cancellableHandlers -= 1;
    }

    cancel() {
        this.cancelled = true;
        for (const callback of [...this._handlers.values()])
            callback();
    }
}

class FakeNetworkMonitor {
    constructor(metered) {
        this._metered = metered;
        this._handlers = new Map();
        this._nextId = 1;
        this.disconnected = [];
    }

    get_network_metered() {
        return this._metered;
    }

    connect(signal, callback) {
        const id = this._nextId++;
        this._handlers.set(id, {signal, callback});
        return id;
    }

    disconnect(id) {
        this.disconnected.push(id);
        this._handlers.delete(id);
    }

    setMetered(value) {
        this._metered = value;
        for (const {signal, callback} of [...this._handlers.values()]) {
            if (signal === 'notify::network-metered')
                callback();
        }
    }
}

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
    }

    connect(signal, callback) {
        this._handlers.set(signal, callback);
        return this._handlers.size;
    }

    set(props) {
        Object.assign(this, props);
    }
}

class FakeSystemIndicator {
    constructor() {
        this.quickSettingsItems = [];
        this._indicators = [];
    }

    _addIndicator() {
        const indicator = {visible: false, gicon: null};
        this._indicators.push(indicator);
        return indicator;
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
        generate: [],
        statusSubprocesses: 0,
        statusArgv: null,
        errors: [],
        forceExits: [],
        cancellableHandlers: 0,
        pendingStatus: [],
        pendingGenerate: [],
        pendingSystemctl: [],
        systemctlDeferred: Boolean(options.deferSystemctl),
        statusCancelled: false,
        timeouts: [],
        removedSources: [],
        sources: new Map(),
        nextSourceId: 1,
    };

    const homeDir = options.homeDir ?? '/home/tester';
    const stateDir = `${homeDir}/.local/state/syncthing`;
    const configPath = `${stateDir}/config.xml`;

    const fs = new FakeFileSystem();
    fs.addDir(homeDir);
    if (options.syncDirExists ?? true)
        fs.addDir(`${homeDir}/Sync`);
    if (options.configExists ?? true)
        fs.addFile(configPath, options.existingConfig ?? DEFAULT_GENERATED_CONFIG);
    log.fs = fs;

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

    const networkMonitor = new FakeNetworkMonitor(options.metered ?? false);
    log.networkMonitor = networkMonitor;
    log.setMetered = value => networkMonitor.setMetered(value);

    // Release a status probe whose callback was held back with
    // options.deferStatus. GIO still invokes the callback after a cancel — with
    // G_IO_ERROR_CANCELLED — which is exactly the use-after-destroy window.
    log.releaseStatus = mode => {
        log.statusCancelled = mode === 'cancelled';
        for (const deliver of log.pendingStatus.splice(0))
            deliver();
    };

    // Same trick for `syncthing generate`, which is the longest await on the
    // click path and therefore the widest window for disable() to land in.
    log.releaseGenerate = () => {
        for (const deliver of log.pendingGenerate.splice(0))
            deliver();
    };

    // ...and for the first systemctl verb, so disable() can land between the
    // start and the status refresh chained behind it.
    log.releaseSystemctl = () => {
        log.systemctlDeferred = false;
        for (const deliver of log.pendingSystemctl.splice(0))
            deliver();
    };

    // The status probe is the only STDOUT_PIPE subprocess; `syncthing generate`
    // and the systemctl verbs are split by argv[0].
    function makeSubprocess(argv, flags) {
        const proc = {
            force_exit() {
                log.forceExits.push(argv);
            },
        };

        if (flags === Gio.SubprocessFlags.STDOUT_PIPE) {
            log.statusSubprocesses += 1;
            log.statusArgv = argv;
            proc.communicate_utf8_async = (_stdin, _cancellable, callback) => {
                const deliver = () => callback(proc, 'res');
                if (options.deferStatus)
                    log.pendingStatus.push(deliver);
                else
                    queueMicrotask(deliver);
            };
            proc.communicate_utf8_finish = () => {
                if (log.statusCancelled) {
                    throw new FakeGioError(
                        IO_ERROR_ENUM.CANCELLED,
                        'Operation was cancelled',
                    );
                }
                return [true, options.statusStdout ?? '', ''];
            };
            return proc;
        }

        const isGenerate = argv[0] === 'syncthing';
        if (isGenerate)
            log.generate.push(argv);
        else
            log.systemctl.push(argv);

        proc.wait_check_async = (_cancellable, callback) => {
            const deliver = () => callback(proc, 'res');
            if (isGenerate && options.deferGenerate)
                log.pendingGenerate.push(deliver);
            else if (!isGenerate && log.systemctlDeferred)
                log.pendingSystemctl.push(deliver);
            else
                queueMicrotask(deliver);
        };
        proc.wait_check_finish = () => {
            if (isGenerate) {
                if (options.generateFails)
                    throw new Error('syncthing generate failed');
                if (!options.generateProducesNothing) {
                    const home = argv.find(a => a.startsWith('--home='))?.slice('--home='.length);
                    fs.addFile(
                        `${home}/config.xml`,
                        options.generatedConfig ?? DEFAULT_GENERATED_CONFIG,
                    );
                }
                return true;
            }
            if (options.systemctlFails)
                throw new Error('systemctl failed');
            return true;
        };
        return proc;
    }

    const Gio = {
        SubprocessFlags: {NONE: 0, STDOUT_PIPE: 1},
        FileCreateFlags: {NONE: 0, PRIVATE: 1, REPLACE_DESTINATION: 2},
        FileQueryInfoFlags: {NONE: 0, NOFOLLOW_SYMLINKS: 1},
        IOErrorEnum: IO_ERROR_ENUM,
        Cancellable: class extends FakeCancellable {
            constructor() {
                super(log);
            }
        },
        File: {
            new_for_path(path) {
                return new FakeFile(path, fs);
            },
        },
        NetworkMonitor: {
            get_default() {
                if (options.noNetworkMonitor)
                    return null;
                return networkMonitor;
            },
        },
        Subprocess: {
            new(argv, flags) {
                if (options.subprocessThrows)
                    throw new Error('spawn refused');
                return makeSubprocess(argv, flags);
            },
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

    const GLib = {
        PRIORITY_DEFAULT: 0,
        PRIORITY_LOW: 300,
        SOURCE_CONTINUE: true,
        SOURCE_REMOVE: false,
        get_home_dir() {
            return homeDir;
        },
        timeout_add_seconds(priority, intervalSeconds, callback) {
            const id = log.nextSourceId++;
            log.timeouts.push({id, priority, intervalSeconds});
            log.sources.set(id, callback);
            return id;
        },
        Source: {
            remove(id) {
                log.removedSources.push(id);
                return log.sources.delete(id);
            },
        },
        Bytes: {
            new(data) {
                return {data};
            },
        },
    };

    const GObject = {
        registerClass(klass) {
            return klass;
        },
    };

    const Main = {
        notify(title, body) {
            log.notifications.push({title, body});
        },
        sessionMode: {allowSettings: options.allowSettings ?? true},
    };

    const stubs = {
        Gio,
        GLib,
        GObject,
        Main,
        PopupMenu: {
            PopupMenuSection: FakePopupMenuSection,
            PopupSeparatorMenuItem: FakePopupSeparatorMenuItem,
        },
        QuickMenuToggle: FakeQuickMenuToggle,
        SystemIndicator: FakeSystemIndicator,
        gettext: text => text,
    };

    return {stubs, log, settings, paths: {homeDir, stateDir, configPath}};
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
    const {stubs, log, settings, paths} = makeStubs(options);
    globalThis.__stStubs = stubs;
    globalThis.logError = (...args) => log.errors.push(args.map(String));

    const module = await loadToggleModule();
    const extension = makeExtensionObject(settings, options);
    const indicator = new module.ServiceIndicator(extension.object);
    return {indicator, log, paths, extensionLog: extension.log};
}

function snapshotWidgets(indicator) {
    return {
        indicatorVisible: indicator._indicator.visible,
        checked: indicator._toggle.checked,
        subtitle: indicator._toggle.subtitle,
        webGuiSensitive: indicator._toggle.webGuiItem.sensitive,
    };
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
            webGuiItemIsSectionAction:
                indicator._toggle.webGuiItem === toggle._itemsSection.actions[0],
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

    // updateStatus is the single writer of indicator visibility, the toggle's
    // checked/subtitle pair, and the Web GUI item's sensitivity.
    async 'update-status'(options) {
        const {indicator} = await build(options);
        indicator.updateStatus(true);
        const active = snapshotWidgets(indicator);
        indicator.updateStatus(false);
        const inactive = snapshotWidgets(indicator);
        return {active, inactive};
    },

    // checkStatus' own probe: which command it runs and what it concludes.
    async 'status-probe'(options) {
        const {indicator, log} = await build(options);
        await indicator.checkStatus();
        await settle();
        return {
            statusArgv: log.statusArgv,
            statusSubprocesses: log.statusSubprocesses,
            widgets: snapshotWidgets(indicator),
            errors: log.errors,
        };
    },

    // The background poll: how often it runs, and whether destroy() takes the
    // source with it.
    async 'poll-lifecycle'(options) {
        const {indicator, log} = await build(options);
        const installed = log.timeouts.map(t => ({
            intervalSeconds: t.intervalSeconds,
            priority: t.priority,
        }));
        const liveBefore = [...log.sources.keys()];
        indicator.destroy();
        await settle();
        return {
            installed,
            liveBefore,
            removedSources: log.removedSources,
            liveAfter: [...log.sources.keys()],
            monitorDisconnects: log.networkMonitor.disconnected.length,
        };
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
            statusSubprocesses: log.statusSubprocesses,
            createdDirs: log.fs.created,
            pausedForMetered: indicator._pausedForMetered,
            checked: indicator._toggle.checked,
            errors: log.errors,
        };
    },

    // A metered network arrives while sharing is on, then leaves again. Both
    // transitions must run the same verb set as the manual toggle.
    async 'metered-transition'(options) {
        const {indicator, log} = await build(options);
        indicator._toggle.checked = options.checked ?? true;
        indicator._pausedForMetered = options.pausedForMetered ?? false;

        log.setMetered(true);
        await settle();
        const paused = {
            systemctl: [...log.systemctl],
            notifications: [...log.notifications],
            pausedForMetered: indicator._pausedForMetered,
        };

        log.systemctl.length = 0;
        log.notifications.length = 0;

        log.setMetered(false);
        await settle();
        const resumed = {
            systemctl: [...log.systemctl],
            notifications: [...log.notifications],
            pausedForMetered: indicator._pausedForMetered,
        };

        return {paused, resumed, errors: log.errors};
    },

    // Turning the toggle on over a metered link: the request is refused now and
    // remembered, so the next unmetered transition honours it.
    async 'metered-click-then-unmeter'(options) {
        const {indicator, log} = await build({...options, metered: true});
        indicator._toggle.checked = true;
        await indicator._toggle._handlers.get('clicked')();
        await settle();
        const refused = {
            systemctl: [...log.systemctl],
            notifications: [...log.notifications],
            checked: indicator._toggle.checked,
            pausedForMetered: indicator._pausedForMetered,
        };

        log.systemctl.length = 0;
        log.notifications.length = 0;

        log.setMetered(false);
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
            writes: log.fs.writes,
            errors: log.errors,
        };
    },

    // disable() destroys the quick settings items before the indicator, so a
    // status callback arriving afterwards must not reach updateStatus().
    // Cancelling does not drop that callback — GIO delivers it either with
    // G_IO_ERROR_CANCELLED (options.release 'cancelled') or, when the child had
    // already answered, successfully (options.release 'ok'). Both land after
    // destroy(); both must leave the widgets alone.
    async 'destroy-during-status'(options) {
        const {indicator, log} = await build({...options, deferStatus: true});
        const pending = indicator.checkStatus();
        await settle();
        const before = snapshotWidgets(indicator);

        indicator.destroy();
        const forceExitsAfterCancel = log.forceExits.map(argv => argv[0]);

        log.releaseStatus(options.release ?? 'cancelled');
        await pending;
        await settle();

        return {
            before,
            after: snapshotWidgets(indicator),
            forceExitsAfterCancel,
            leakedCancellableHandlers: log.cancellableHandlers,
            errors: log.errors,
        };
    },

    // destroy() lands while `syncthing generate` is still running. Neither the
    // rest of the seeding nor the rest of the click may resume afterwards.
    async 'destroy-during-seed'(options) {
        const {indicator, log} = await build({
            configExists: false,
            syncDirExists: false,
            deferGenerate: true,
            ...options,
        });
        indicator._toggle.checked = true;
        const pending = indicator._toggle._handlers.get('clicked')();
        await settle();
        const generateStarted = log.generate.length;

        indicator.destroy();
        log.releaseGenerate();
        await pending;
        await settle();

        return {
            generateStarted,
            systemctl: log.systemctl,
            writtenPaths: log.fs.writes.map(write => write.path),
            forceExits: log.forceExits.map(argv => argv[0]),
            widgets: snapshotWidgets(indicator),
            errors: log.errors,
        };
    },

    // destroy() lands while the start verb itself is still outstanding: the
    // status refresh chained behind it must never spawn.
    async 'destroy-during-systemctl'(options) {
        const {indicator, log} = await build({...options, deferSystemctl: true});
        indicator._toggle.checked = true;
        const pending = indicator._toggle._handlers.get('clicked')();
        await settle();
        const started = [...log.systemctl];

        indicator.destroy();
        log.releaseSystemctl();
        await pending;
        await settle();

        return {
            started,
            systemctl: log.systemctl,
            statusSubprocesses: log.statusSubprocesses,
            forceExits: log.forceExits.map(argv => argv[0]),
            widgets: snapshotWidgets(indicator),
            errors: log.errors,
        };
    },

    // The same window later on the click path: destroy() lands while the
    // status refresh behind the start verb is still outstanding.
    async 'destroy-during-click'(options) {
        const {indicator, log} = await build({...options, deferStatus: true});
        indicator._toggle.checked = true;
        const pending = indicator._toggle._handlers.get('clicked')();
        await settle();

        indicator.destroy();
        log.releaseStatus('cancelled');
        await pending;
        await settle();

        return {
            widgets: snapshotWidgets(indicator),
            systemctl: log.systemctl,
            forceExits: log.forceExits.map(argv => argv[0]),
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
