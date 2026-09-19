// Behavior harness for extensions/syncthing-toggle/prefs.js.
//
// prefs.js imports gi:// and resource:///org/gnome/Shell modules that only
// exist inside the gnome-shell prefs process, so plain `import` cannot load it.
// As in syncthing_toggle_harness.mjs, this harness reads the real source,
// rewrites only its import block into bindings taken from globalThis, and
// imports the result as a data: module. Everything below the import block —
// the logic under test — is the byte-for-byte shipped source.
//
// Scope: fillPreferencesWindow's settings/icon-path wiring and _general()'s
// widget tree, row order, GSettings bindings, the port SpinButton adjustment,
// the About button wiring and _about()'s AboutWindow metadata.
//
// The Adw/Gtk stubs implement only what prefs.js actually calls. A rewrite that
// reaches for a different widget API fails here with a TypeError rather than
// silently shipping a preferences dialog that throws on open.
//
// Usage: node syncthing_prefs_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const PREFS_JS = join(HERE, '..', 'extensions', 'syncthing-toggle', 'prefs.js');

const IMPORT_LINE_RE =
    /^\s*import\s+(?:(\*\s+as\s+\w+)|(\{[^}]*\})|(\w+))\s+from\s+['"](?:gi:\/\/|resource:\/\/\/)[^'"]+['"];?\s*$/gm;
// prefs.js writes its named import across several lines, so the single-line
// form above cannot see it. Matching the multi-line shape separately keeps the
// rewrite honest instead of loosening the single-line pattern for everyone.
const MULTILINE_NAMED_IMPORT_RE =
    /^import\s+(\{[^}]*\})\s+from\s+['"](?:gi:\/\/|resource:\/\/\/)[^'"]+['"];?\s*$/gms;

function rewriteGnomeImports(source) {
    const bindNamed = named =>
        // `{ gettext as _ }` is import syntax; destructuring needs `{ gettext: _ }`.
        `const ${named.replace(/\s+as\s+/g, ': ')} = globalThis.__stStubs;`;

    return source
        .replace(MULTILINE_NAMED_IMPORT_RE, (line, named) =>
            line.includes('\n') ? bindNamed(named) : line,
        )
        .replace(IMPORT_LINE_RE, (_line, namespaceImport, namedImport, defaultImport) => {
            if (namespaceImport) {
                const name = namespaceImport.split(/\s+as\s+/)[1];
                return `const ${name} = globalThis.__stStubs.${name};`;
            }
            if (namedImport)
                return bindNamed(namedImport);
            return `const ${defaultImport} = globalThis.__stStubs.${defaultImport};`;
        });
}

function asDataModule(source) {
    return `data:text/javascript;base64,${Buffer.from(source, 'utf8').toString('base64')}`;
}

function loadPrefsModule() {
    const source = readFileSync(PREFS_JS, 'utf8');
    const rewritten = rewriteGnomeImports(source);
    if (/^\s*import\s/m.test(rewritten))
        throw new Error('an import survived the rewrite — harness rewrite is stale');
    if (rewritten === source)
        throw new Error('no gi:// or resource:/// imports found — harness rewrite is stale');
    return import(asDataModule(rewritten));
}

// --- Stubs ------------------------------------------------------------------

// Every widget records the constructor properties it was handed, so a scenario
// can assert on the shipped titles and subtitles without the real Adw types.
class FakeWidget {
    constructor(props = {}) {
        Object.assign(this, props);
        this.props = {...props};
        this.children = [];
        this.suffixes = [];
        this.cssClasses = [];
        this.signals = {};
    }

    add(child) {
        this.children.push(child);
    }

    add_suffix(child) {
        this.suffixes.push(child);
    }

    set_css_classes(classes) {
        this.cssClasses = classes;
    }

    set_header_suffix(child) {
        this.headerSuffix = child;
    }

    connect(signal, handler) {
        (this.signals[signal] ??= []).push(handler);
        return this.signals[signal].length;
    }

    emit(signal) {
        for (const handler of this.signals[signal] ?? [])
            handler();
    }
}

class FakePreferencesPage extends FakeWidget {}
class FakePreferencesGroup extends FakeWidget {}
class FakeEntryRow extends FakeWidget {}
class FakeActionRow extends FakeWidget {}
class FakeSwitchRow extends FakeWidget {}
class FakeButtonContent extends FakeWidget {}
class FakeButton extends FakeWidget {}
class FakeSpinButton extends FakeWidget {}
class FakeAdjustment extends FakeWidget {}

class FakeAboutWindow extends FakeWidget {
    constructor(props) {
        super(props);
        this.shown = false;
        this.applicationIcon = null;
        this.applicationName = null;
        this.version = null;
        this.developerName = null;
        this.issueUrl = null;
        this.website = null;
        this.licenseType = null;
        this.copyright = null;
    }

    set_application_icon(value) {
        this.applicationIcon = value;
    }

    set_application_name(value) {
        this.applicationName = value;
    }

    set_version(value) {
        this.version = value;
    }

    set_developer_name(value) {
        this.developerName = value;
    }

    set_issue_url(value) {
        this.issueUrl = value;
    }

    set_website(value) {
        this.website = value;
    }

    set_license_type(value) {
        this.licenseType = value;
    }

    set_copyright(value) {
        this.copyright = value;
    }

    show() {
        this.shown = true;
    }
}

// A GSettings stub that records every bind() rather than performing one. The
// binding table is the whole contract of _general(): a key bound to the wrong
// widget property silently stops persisting the preference.
class FakeSettings {
    constructor(schemaId, values, log) {
        this.schemaId = schemaId;
        this._values = values;
        this._log = log;
    }

    get_int(key) {
        this._log.settingsReads.push(key);
        if (!(key in this._values))
            throw new Error(`prefs.js read unknown settings key: ${key}`);
        return this._values[key];
    }

    bind(key, widget, property, flags) {
        this._log.binds.push({key, widget, property, flags});
    }
}

class FakeIconTheme {
    constructor(display, log) {
        this.display = display;
        this._log = log;
    }

    add_search_path(path) {
        this._log.iconSearchPaths.push(path);
    }
}

// this.dir is a Gio.File in the real extension; prefs.js only ever walks to a
// child and asks for its path.
function fakeDir(extensionPath) {
    return {
        get_child(name) {
            return {
                get_path: () => `${extensionPath}/${name}`,
            };
        },
    };
}

class FakePreferencesWindow extends FakeWidget {
    constructor(display) {
        super({});
        this._display = display;
        this.pages = this.children;
    }

    get_display() {
        return this._display;
    }
}

const DEFAULTS = {
    schemaId: 'org.gnome.shell.extensions.syncthing-toggle',
    extensionPath: '/usr/share/gnome-shell/extensions/syncthing-toggle',
    metadata: {
        version: 2,
        url: 'https://github.com/projectbluefin/bluefin-bling',
    },
    settings: {port: 8384},
};

async function build(options = {}) {
    const config = {
        ...DEFAULTS,
        ...options,
        metadata: {...DEFAULTS.metadata, ...(options.metadata ?? {})},
        settings: {...DEFAULTS.settings, ...(options.settings ?? {})},
    };

    const log = {
        binds: [],
        settingsReads: [],
        iconSearchPaths: [],
        translated: [],
        requestedSchemas: [],
        iconThemeDisplays: [],
        aboutWindows: [],
    };

    const display = {id: 'display-0'};

    // The base class gnome-shell provides. getSettings() is what prefs.js calls
    // to reach the schema, and the harness records the id it asked for: the
    // real process throws if it does not match metadata's settings-schema.
    class ExtensionPreferences {
        constructor() {
            this.metadata = config.metadata;
            this.dir = fakeDir(config.extensionPath);
        }

        getSettings(schemaId) {
            log.requestedSchemas.push(schemaId);
            return new FakeSettings(schemaId, config.settings, log);
        }
    }

    globalThis.__stStubs = {
        ExtensionPreferences,
        gettext(text) {
            log.translated.push(text);
            return text;
        },
        Adw: {
            PreferencesPage: FakePreferencesPage,
            PreferencesGroup: FakePreferencesGroup,
            EntryRow: FakeEntryRow,
            ActionRow: FakeActionRow,
            SwitchRow: FakeSwitchRow,
            ButtonContent: FakeButtonContent,
            AboutWindow: class extends FakeAboutWindow {
                constructor(props) {
                    super(props);
                    log.aboutWindows.push(this);
                }
            },
        },
        Gio: {
            SettingsBindFlags: {DEFAULT: 'Gio.SettingsBindFlags.DEFAULT'},
        },
        Gtk: {
            Align: {CENTER: 'Gtk.Align.CENTER'},
            Orientation: {HORIZONTAL: 'Gtk.Orientation.HORIZONTAL'},
            License: {GPL_3_0: 'Gtk.License.GPL_3_0'},
            Adjustment: FakeAdjustment,
            SpinButton: FakeSpinButton,
            Button: FakeButton,
            IconTheme: {
                get_for_display(target) {
                    log.iconThemeDisplays.push(target === display);
                    return new FakeIconTheme(target, log);
                },
            },
        },
    };

    const module = await loadPrefsModule();
    const prefs = new module.default();
    const window = new FakePreferencesWindow(display);
    prefs.fillPreferencesWindow(window);

    return {prefs, window, log, config};
}

// The single group _general() builds. Reached through the page rather than a
// saved reference so that a rewrite which stops adding either one fails here.
function group(window) {
    const [page] = window.children;
    if (!page)
        throw new Error('fillPreferencesWindow added no page to the window');
    const [only] = page.children;
    if (!only)
        throw new Error('_general() added no group to the page');
    return only;
}

function describeRow(row) {
    return {
        kind: row.constructor.name.replace(/^Fake/, ''),
        title: row.props.title ?? null,
        subtitle: row.props.subtitle ?? null,
    };
}

const scenarios = {
    // fillPreferencesWindow reaches the schema by name, parks the settings
    // object on the window (toggle.js and the bindings both read it there) and
    // registers the shipped icons directory against the window's own display.
    async 'window-setup'(options) {
        const {prefs, window, log} = await build(options);
        return {
            requestedSchemas: log.requestedSchemas,
            settingsOnWindow: window._settings?.schemaId ?? null,
            windowRetained: prefs._window === window,
            iconSearchPaths: log.iconSearchPaths,
            iconThemeUsedWindowDisplay: log.iconThemeDisplays,
        };
    },

    // The visible structure: one page, one group, and the rows in shipped
    // order. Row order is the dialog's reading order, so a reshuffle is a
    // user-visible change that should have to be stated here.
    async 'widget-tree'(options) {
        const {window} = await build(options);
        const only = group(window);
        return {
            pageCount: window.children.length,
            groupCount: window.children[0].children.length,
            groupTitle: only.props.title ?? null,
            groupDescription: only.props.description ?? null,
            rows: only.children.map(describeRow),
        };
    },

    // Every GSettings key the dialog writes, with the widget property it is
    // bound to. Binding a key to the wrong property makes the control move
    // without the setting ever changing.
    async 'settings-bindings'(options) {
        const {window, log} = await build(options);
        const only = group(window);
        const rows = only.children;
        const portInput = rows[1]?.suffixes[0] ?? null;
        return {
            binds: log.binds.map(bind => ({
                key: bind.key,
                property: bind.property,
                flags: bind.flags,
            })),
            serviceNameBoundToRow: log.binds[0]?.widget === rows[0],
            portBoundToSpinButton: log.binds[1]?.widget === portInput,
            startStopOnlyBoundToRow: log.binds[2]?.widget === rows[2],
            iconNameBoundToRow: log.binds[3]?.widget === rows[3],
        };
    },

    // The port row: the SpinButton lives in the row's suffix, the row activates
    // it, and its adjustment is seeded from the current setting. An adjustment
    // whose upper bound is below the stored port silently clamps it.
    async 'port-adjustment'(options) {
        const {window, log} = await build(options);
        const portRow = group(window).children[1];
        const portInput = portRow?.suffixes[0] ?? null;
        const adjustment = portInput?.props.adjustment ?? null;
        return {
            settingsReads: log.settingsReads,
            suffixCount: portRow?.suffixes.length ?? 0,
            rowActivatesSpinButton: portRow?.activatable_widget === portInput,
            numeric: portInput?.props.numeric ?? null,
            valign: portInput?.props.valign ?? null,
            orientation: portInput?.props.orientation ?? null,
            lower: adjustment?.props.lower ?? null,
            upper: adjustment?.props.upper ?? null,
            stepIncrement: adjustment?.props.step_increment ?? null,
            value: adjustment?.props.value ?? null,
        };
    },

    // The About button is a header suffix on the group, not a row, and its
    // 'clicked' handler is what opens the About window.
    async 'about-button'(options) {
        const {window, log} = await build(options);
        const only = group(window);
        const button = only.headerSuffix ?? null;
        const content = button?.props.child ?? null;
        return {
            buttonIsHeaderSuffix: button !== null,
            buttonIsNotARow: !only.children.includes(button),
            cssClasses: button?.cssClasses ?? null,
            label: content?.props.label ?? null,
            iconName: content?.props.icon_name ?? null,
            marginTop: button?.props.margin_top ?? null,
            marginBottom: button?.props.margin_bottom ?? null,
            aboutWindowsBeforeClick: log.aboutWindows.length,
        };
    },

    // _about() reached the way a user reaches it — by clicking the button —
    // so the handler wiring is covered along with the window's metadata.
    async 'about-window'(options) {
        const {window, log} = await build(options);
        const button = group(window).headerSuffix;
        button.emit('clicked');
        const about = log.aboutWindows.at(-1) ?? null;
        return {
            aboutWindowCount: log.aboutWindows.length,
            transientForPrefsWindow: about?.props.transient_for === window,
            modal: about?.props.modal ?? null,
            applicationIcon: about?.applicationIcon ?? null,
            applicationName: about?.applicationName ?? null,
            version: about?.version ?? null,
            developerName: about?.developerName ?? null,
            issueUrl: about?.issueUrl ?? null,
            website: about?.website ?? null,
            licenseType: about?.licenseType ?? null,
            copyright: about?.copyright ?? null,
            shown: about?.shown ?? null,
        };
    },

    // Which user-visible strings are routed through gettext. The port row's
    // title and subtitle are not, and pinning that keeps the omission visible
    // instead of letting it pass as an untested detail.
    async 'translation'(options) {
        const {log} = await build(options);
        return {translated: log.translated};
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
