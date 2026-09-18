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

function makeStubs(options) {
    const log = {
        notifications: [],
        launchedUris: [],
        iconStrings: [],
        systemctl: [],
        statusSubprocesses: 0,
        errors: [],
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

    // _runSystemctl uses SubprocessFlags.NONE; checkStatus uses STDOUT_PIPE.
    // Splitting on the flag keeps the status command out of these assertions.
    function makeSubprocess(argv, flags) {
        if (flags === Gio.SubprocessFlags.STDOUT_PIPE) {
            log.statusSubprocesses += 1;
            return {
                communicate_utf8_async(_stdin, _cancellable, callback) {
                    queueMicrotask(() => callback(this, 'res'));
                },
                communicate_utf8_finish() {
                    return [true, options.statusStdout ?? '', ''];
                },
            };
        }

        log.systemctl.push(argv);
        return {
            wait_check_async(_cancellable, callback) {
                queueMicrotask(() => callback(this, 'res'));
            },
            wait_check_finish() {
                if (options.systemctlFails)
                    throw new Error('systemctl failed');
                return true;
            },
        };
    }

    const Gio = {
        SubprocessFlags: {NONE: 0, STDOUT_PIPE: 1},
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

    return {stubs, log, settings};
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
