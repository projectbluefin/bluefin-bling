// Behavior harness for extensions/syncthing-toggle/toggle.js.
//
// toggle.js imports gi:// and resource:///org/gnome/shell modules that only
// exist inside gnome-shell, so plain `import` cannot load it. The harness reads
// the real source, rewrites only its import block into bindings taken from
// globalThis, and imports the result as a data: module. Everything below the
// import block — the logic under test — is the byte-for-byte shipped source.
//
// Scope: ServiceToggle construction, ServiceIndicator icon resolution, the
// 'clicked' orchestration, _runSystemctl argv, and updateStatus. checkStatus
// internals are deliberately NOT asserted here — the harness only distinguishes
// its subprocess from _runSystemctl's by SubprocessFlags so that changes to the
// status command do not break these tests.
//
// Usage: node syncthing_toggle_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const TOGGLE_JS = join(HERE, '..', 'extensions', 'syncthing-toggle', 'toggle.js');
const EXTENSION_JS = join(HERE, '..', 'extensions', 'syncthing-toggle', 'extension.js');

const IMPORT_LINE_RE =
    /^\s*import\s+(?:(\*\s+as\s+\w+)|(\{[^}]*\})|(\w+))\s+from\s+['"](?:gi:\/\/|resource:\/\/\/)[^'"]+['"];?\s*$/gm;

function loadToggleModule() {
    const source = readFileSync(TOGGLE_JS, 'utf8');
    const matches = [...source.matchAll(IMPORT_LINE_RE)];
    if (matches.length === 0)
        throw new Error('no gi:// or resource:/// imports found — harness rewrite is stale');

    const url = asDataModule(rewriteGnomeImports(source));
    return import(url);
}

function rewriteGnomeImports(source) {
    return source.replace(
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
}

function asDataModule(source) {
    return `data:text/javascript;base64,${Buffer.from(source, 'utf8').toString('base64')}`;
}

// extension.js is the entry point GNOME Shell calls, and the only place the
// enable()/disable() lifecycle exists. It needs the same rewrite plus one more:
// its relative `./toggle.js` import cannot resolve from a data: URL, so it is
// bound to the toggle module this harness already loaded.
const TOGGLE_IMPORT_RE =
    /^\s*import\s+(\{[^}]*\})\s+from\s+['"]\.\/toggle\.js['"];?\s*$/gm;

async function loadExtensionModule() {
    globalThis.__stStubs.__toggle = await loadToggleModule();
    const source = readFileSync(EXTENSION_JS, 'utf8');
    if (!TOGGLE_IMPORT_RE.test(source))
        throw new Error("extension.js no longer imports './toggle.js' — harness rewrite is stale");
    const rewritten = rewriteGnomeImports(source).replace(
        TOGGLE_IMPORT_RE,
        (_line, named) => `const ${named} = globalThis.__stStubs.__toggle;`,
    );
    return import(asDataModule(rewritten));
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
        const item = {label, callback, visible: true};
        this.actions.push(item);
        return item;
    }
}

class FakePopupMenuSection {
    constructor() {
        this.kind = 'section';
        this.actions = [];
    }

    addAction(label, callback) {
        const item = {label, callback, visible: true};
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

    // What `systemctl status` reports. A successful start/stop moves it, so the
    // status read the toggle does after every call sees the unit it just acted
    // on rather than a frozen string.
    let unitRunning = options.unitRunning ?? false;
    const statusText = () =>
        options.statusStdout ??
        (unitRunning ? 'Active: active (running) since now' : 'Active: inactive (dead)');

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
            return Object.assign(proc, {
                communicate_utf8_async(_stdin, cancellable, callback) {
                    record.cancellable = cancellable;
                    queueMicrotask(() => callback(this, 'res'));
                },
                communicate_utf8_finish() {
                    if (record.cancellable?.cancelled)
                        throw new Error('Operation was cancelled');
                    return [true, statusText(), ''];
                },
            });
        }

        log.systemctl.push(argv);
        return Object.assign(proc, {
            wait_check_async(cancellable, callback) {
                record.cancellable = cancellable;
                queueMicrotask(() => callback(this, 'res'));
            },
            wait_check_finish() {
                if (record.cancellable?.cancelled)
                    throw new Error('Operation was cancelled');
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
        SOURCE_REMOVE: false,
        SOURCE_CONTINUE: true,
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

    return {stubs, log, settings, networkMonitor};
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
    for (let i = 0; i < 50; i++)
        await Promise.resolve();
}

// --- Scenarios --------------------------------------------------------------

async function build(options) {
    const {stubs, log, settings} = makeStubs(options);
    globalThis.__stStubs = stubs;
    globalThis.logError = (...args) => log.errors.push(args.map(String));

    const module = await loadToggleModule();
    const extension = makeExtensionObject(settings, options);
    const indicator = new module.ServiceIndicator(extension.object);
    return {indicator, log, extensionLog: extension.log};
}

// Drive the real entry point instead of constructing the indicator directly:
// enable()/disable() live in extension.js and are the only place the lifecycle
// — the initial reconcile, the poll source, the teardown — can be observed.
async function buildExtension(options) {
    const {stubs, log, networkMonitor} = makeStubs(options);
    globalThis.__stStubs = stubs;
    globalThis.logError = (...args) => log.errors.push(args.map(String));

    const module = await loadExtensionModule();
    const extension = new module.default();
    extension.enable();
    await settle();
    return {extension, indicator: extension._indicator, log, networkMonitor};
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
        const active = {
            indicatorVisible: indicator._indicator.visible,
            checked: indicator._toggle.checked,
            subtitle: indicator._toggle.subtitle,
        };
        indicator.updateStatus(false);
        const inactive = {
            indicatorVisible: indicator._indicator.visible,
            checked: indicator._toggle.checked,
            subtitle: indicator._toggle.subtitle,
        };
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
