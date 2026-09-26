// Behavior harness for extensions/light-style/extension.js.
//
// The extension imports gi:// and resource:///org/gnome/shell modules that only
// exist inside gnome-shell, so plain `import` cannot load it. The harness reads
// the real source, rewrites only its import block into a binding taken from
// globalThis, and imports the result as a data: module. Everything below the
// import block — the logic under test — is the byte-for-byte shipped source.
//
// Usage: node light_style_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

import {loadGnomeModule} from './gnome_module_loader.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const EXTENSION_JS = join(HERE, '..', 'extensions', 'light-style', 'extension.js');

function loadExtensionModule() {
    return loadGnomeModule({
        path: EXTENSION_JS,
        stubsExpression: 'globalThis.__lightStyleStubs',
    });
}

// --- Stubs ------------------------------------------------------------------

const INTERFACE_SCHEMA = 'org.gnome.desktop.interface';

// Stands in for Main.uiGroup: the only thing the extension does to it is toggle
// one style class, so record the class set and every call made against it.
class FakeUiGroup {
    constructor(log) {
        this._classes = new Set();
        this._log = log;
    }

    add_style_class_name(name) {
        this._log.uiGroupCalls.push(['add', name]);
        this._classes.add(name);
    }

    remove_style_class_name(name) {
        this._log.uiGroupCalls.push(['remove', name]);
        this._classes.delete(name);
    }

    classes() {
        return [...this._classes].sort();
    }
}

// Stands in for Gio.Settings. Records the schema it was constructed with, the
// signals connected and disconnected, and lets a scenario change the stored
// value and fire the matching handler the way dconf would.
class FakeSettings {
    constructor(log, initialScheme) {
        this._log = log;
        this._values = {'color-scheme': initialScheme};
        this._handlers = new Map();
        this._nextId = 1;
    }

    get_string(key) {
        this._log.getStringKeys.push(key);
        if (!(key in this._values))
            throw new Error(`unknown key read from ${INTERFACE_SCHEMA}: ${key}`);
        return this._values[key];
    }

    connect(signal, callback) {
        const id = this._nextId++;
        this._handlers.set(id, {signal, callback});
        this._log.connected.push(signal);
        return id;
    }

    disconnect(id) {
        this._log.disconnected.push(id);
        if (!this._handlers.delete(id))
            throw new Error(`disconnect of an unknown handler id: ${id}`);
    }

    // Drive a dconf change the way GSettings would: store the new value, then
    // invoke every handler still connected for that signal.
    changeColorScheme(value) {
        this._values['color-scheme'] = value;
        for (const {signal, callback} of this._handlers.values()) {
            if (signal === 'changed::color-scheme')
                callback();
        }
    }

    handlerCount() {
        return this._handlers.size;
    }
}

function makeStubs(options) {
    const log = {
        settingsConstructed: [],
        getStringKeys: [],
        connected: [],
        disconnected: [],
        uiGroupCalls: [],
        stNotifications: [],
    };

    const settings = new FakeSettings(log, options.colorScheme ?? 'default');

    const uiGroup = new FakeUiGroup(log);

    const stubs = {
        Gio: {
            Settings: class {
                constructor(props) {
                    // Gio.Settings is constructed with a schema_id. Record the
                    // schema the extension asked for and hand back the single
                    // fake so the scenario can drive it.
                    log.settingsConstructed.push(props?.schema_id);
                    return settings;
                }
            },
        },
        St: {
            Settings: {
                get() {
                    return {
                        notify(property) {
                            log.stNotifications.push(property);
                        },
                    };
                },
            },
        },
        Main: {
            uiGroup,
            sessionMode: {
                // A session mode shipping its own colorScheme is exactly the
                // Bluefin/Dakota case the extension's comment calls out.
                colorScheme: options.sessionColorScheme ?? 'default',
            },
        },
        // The shipped class extends Extension; nothing in the code under test
        // calls up into it, so an inert base is faithful enough.
        Extension: class {},
    };

    return {stubs, log, settings, uiGroup};
}

async function buildExtension(options) {
    const {stubs, log, settings, uiGroup} = makeStubs(options);
    globalThis.__lightStyleStubs = stubs;
    const module = await loadExtensionModule();
    const LightStyleExtension = module.default;
    const ext = new LightStyleExtension();
    return {ext, log, settings, uiGroup, sessionMode: stubs.Main.sessionMode};
}

function snapshot({ext, log, settings, uiGroup, sessionMode}) {
    return {
        classes: uiGroup.classes(),
        sessionColorScheme: sessionMode.colorScheme,
        savedColorScheme: ext._savedColorScheme ?? null,
        colorSchemeId: ext._colorSchemeId ?? null,
        interfaceSettingsIsNull: ext._interfaceSettings === null,
        schemasConstructed: log.settingsConstructed,
        keysRead: log.getStringKeys,
        connected: log.connected,
        disconnected: log.disconnected,
        uiGroupCalls: log.uiGroupCalls,
        stNotifications: log.stNotifications,
        liveHandlers: settings.handlerCount(),
    };
}

// --- Scenarios --------------------------------------------------------------

const scenarios = {
    // enable() only: the state GNOME Shell leaves the session in at login.
    async enable(options) {
        const ctx = await buildExtension(options);
        ctx.ext.enable();
        return snapshot(ctx);
    },

    // enable(), then a dconf color-scheme change fired through the connected
    // handler. Proves _sync() is actually wired to the signal.
    async schemeChange(options) {
        const ctx = await buildExtension(options);
        ctx.ext.enable();
        ctx.settings.changeColorScheme(options.nextColorScheme);
        return snapshot(ctx);
    },

    // Full lifecycle: what the session looks like after the extension is
    // disabled, which is the state another theming extension inherits.
    async lifecycle(options) {
        const ctx = await buildExtension(options);
        ctx.ext.enable();
        const afterEnable = snapshot(ctx);
        ctx.ext.disable();
        return {afterEnable, afterDisable: snapshot(ctx)};
    },

    // After disable() the handler must be gone: a later dconf change must not
    // re-enter _sync() with a nulled _interfaceSettings.
    async changeAfterDisable(options) {
        const ctx = await buildExtension(options);
        ctx.ext.enable();
        ctx.ext.disable();
        let threw = false;
        try {
            ctx.settings.changeColorScheme(options.nextColorScheme);
        } catch {
            threw = true;
        }
        return {threw, ...snapshot(ctx)};
    },
};

const [, , scenarioName, optionsJson] = process.argv;
const scenario = scenarios[scenarioName];
if (!scenario) {
    process.stderr.write(`unknown scenario: ${scenarioName}\n`);
    process.exit(2);
}

scenario(optionsJson ? JSON.parse(optionsJson) : {})
    .then(result => process.stdout.write(JSON.stringify(result)))
    .catch(error => {
        process.stderr.write(`${error?.stack ?? error}\n`);
        process.exit(1);
    });
