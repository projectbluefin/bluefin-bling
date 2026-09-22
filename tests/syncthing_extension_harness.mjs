// Behavior harness for extensions/syncthing-toggle/extension.js.
//
// The shipped entry point imports resource:///org/gnome/shell modules that only
// exist inside gnome-shell, plus its own './toggle.js'. tests/gnome_module_loader.mjs
// owns the first rewrite; the relative import is rewritten here, because a
// data: module has no base URL a relative specifier could resolve against.
//
// Stubbing ./toggle.js is deliberate: ServiceIndicator already has its own
// executed coverage in tests/test_syncthing_toggle_behavior.py. What is
// unverified is the lifecycle contract extension.js keeps around it — who the
// indicator is constructed with, that it is reconciled before the panel sees
// it, and the order teardown destroys things in. Everything below the import
// block is byte-for-byte shipped source.
//
// Usage: node syncthing_extension_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

import {loadGnomeModule} from './gnome_module_loader.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const EXTENSION_JS = join(HERE, '..', 'extensions', 'syncthing-toggle', 'extension.js');

const STUBS = 'globalThis.__syncthingExtensionStubs';

// `import { ServiceIndicator } from './toggle.js'` — matched by shape rather
// than by the exact line, but still required to be present: if the entry point
// stops pulling the indicator from its own module the harness is testing
// something other than what ships, and must say so instead of passing.
const RELATIVE_IMPORT_RE =
    /^\s*import\s+\{([^}]*)\}\s+from\s+['"](\.\/[^'"]+)['"];?\s*$/gm;

function rewriteRelativeImports(source) {
    let matched = 0;
    const rewritten = source.replace(RELATIVE_IMPORT_RE, (_line, names) => {
        matched += 1;
        return `const {${names.replace(/\s+as\s+/g, ': ')}} = ${STUBS};`;
    });
    if (matched === 0) {
        throw new Error(
            'no relative import found in extension.js — harness rewrite is stale',
        );
    }
    return rewritten;
}

function loadExtensionModule() {
    return loadGnomeModule({
        path: EXTENSION_JS,
        stubsExpression: STUBS,
        rewrite: rewriteRelativeImports,
    });
}

// --- Stubs ------------------------------------------------------------------

// Stands in for a quick-settings item owned by the indicator (the ServiceToggle
// the real indicator pushes onto quickSettingsItems). Only destroy() matters to
// the code under test.
class FakeQuickSettingsItem {
    constructor(log, indicatorId, index) {
        this._log = log;
        this._indicatorId = indicatorId;
        this._index = index;
        this.destroyCount = 0;
    }

    destroy() {
        this.destroyCount += 1;
        this._log.events.push(['item-destroy', this._indicatorId, this._index]);
    }
}

// Stands in for toggle.js's ServiceIndicator. It records the lifecycle calls
// extension.js makes and, critically, *which* status probe it was asked for:
// checkStatus() is the cheap one and reconcile() is the one that also copes
// with a unit already running on a metered link, which emits no signal.
class FakeServiceIndicator {
    constructor(log, id, extensionObject, itemCount) {
        this._log = log;
        this.id = id;
        this.extensionObject = extensionObject;
        this.destroyCount = 0;
        this.quickSettingsItems = [];
        for (let index = 0; index < itemCount; index += 1)
            this.quickSettingsItems.push(new FakeQuickSettingsItem(log, id, index));
        log.events.push(['construct', id]);
    }

    reconcile() {
        this._log.events.push(['reconcile', this.id]);
    }

    checkStatus() {
        this._log.events.push(['checkStatus', this.id]);
    }

    destroy() {
        this.destroyCount += 1;
        this._log.events.push(['indicator-destroy', this.id]);
    }
}

function makeStubs(options) {
    const log = {events: [], addCalls: [], panelPathsTouched: []};
    const indicators = [];
    const itemCount = options.itemCount ?? 1;

    const quickSettings = {
        addExternalIndicator(indicator) {
            log.events.push(['addExternalIndicator', indicator?.id ?? null]);
            log.addCalls.push({
                path: 'Main.panel.statusArea.quickSettings.addExternalIndicator',
                indicatorId: indicator?.id ?? null,
            });
        },
        // addExternalIndicator is the QuickSettings API for an indicator the
        // shell does not own. Record the legacy neighbours too, so routing the
        // indicator through one of them is a visible difference rather than an
        // equally green run.
        addIndicator(indicator) {
            log.addCalls.push({
                path: 'Main.panel.statusArea.quickSettings.addIndicator',
                indicatorId: indicator?.id ?? null,
            });
        },
    };

    const stubs = {
        Main: {
            panel: {
                statusArea: {quickSettings},
                addToStatusArea(role, indicator) {
                    log.addCalls.push({
                        path: 'Main.panel.addToStatusArea',
                        indicatorId: indicator?.id ?? null,
                        role,
                    });
                },
            },
            notify(title, body) {
                log.events.push(['notify', title, body]);
            },
        },
        // The base class gnome-shell provides. The real one carries metadata,
        // getSettings() and path; extension.js only ever hands `this` on, so an
        // inert base with those members is faithful for this file.
        Extension: class {
            getSettings() {
                return {};
            }

            get path() {
                return '/stub/extension/path';
            }
        },
        gettext: text => text,
        ServiceIndicator: class {
            constructor(extensionObject) {
                const indicator = new FakeServiceIndicator(
                    log,
                    indicators.length + 1,
                    extensionObject,
                    itemCount,
                );
                indicators.push(indicator);
                return indicator;
            }
        },
    };

    return {stubs, log, indicators};
}

async function buildExtension(options) {
    const {stubs, log, indicators} = makeStubs(options);
    globalThis.__syncthingExtensionStubs = stubs;
    const module = await loadExtensionModule();
    const SyncthingToggleExtension = module.default;
    const ext = new SyncthingToggleExtension();
    return {ext, log, indicators};
}

function snapshot({ext, log, indicators}) {
    return {
        // Copied, not aliased: the lifecycle scenario snapshots twice against
        // one running log, and a shared reference would make the first
        // snapshot report what the second one saw.
        events: log.events.map(event => [...event]),
        addCalls: log.addCalls.map(call => ({...call})),
        indicatorsConstructed: indicators.length,
        // Identity, not shape: the indicator reads settings and its icon path
        // off whatever it is handed, so it has to be the extension itself.
        constructedWithExtension: indicators.map(i => i.extensionObject === ext),
        indicatorDestroyCounts: indicators.map(i => i.destroyCount),
        itemDestroyCounts: indicators.map(i =>
            i.quickSettingsItems.map(item => item.destroyCount),
        ),
        currentIndicatorId: ext._indicator ? ext._indicator.id : null,
        indicatorIsNull: ext._indicator === null,
    };
}

// --- Scenarios --------------------------------------------------------------

const scenarios = {
    // enable() only: the state gnome-shell leaves the session in at login.
    async enable(options) {
        const ctx = await buildExtension(options);
        ctx.ext.enable();
        return snapshot(ctx);
    },

    // Full lifecycle. What survives disable() is what leaks across a lock
    // screen, an extensions-app toggle, or an `unlock-dialog` session change.
    async lifecycle(options) {
        const ctx = await buildExtension(options);
        ctx.ext.enable();
        const afterEnable = snapshot(ctx);
        ctx.ext.disable();
        return {afterEnable, afterDisable: snapshot(ctx)};
    },

    // enable → disable → enable, which is an ordinary lock/unlock cycle. A
    // second enable must build and reconcile a fresh indicator; re-adding the
    // destroyed one puts a dead widget in the panel.
    async reEnable(options) {
        const ctx = await buildExtension(options);
        ctx.ext.enable();
        ctx.ext.disable();
        ctx.ext.enable();
        return snapshot(ctx);
    },

    // Drives the relative-import rewrite directly, so its staleness guard is
    // executed rather than assumed. A source with no relative import must fail
    // loudly: a rewrite that silently matched nothing would leave the real
    // ServiceIndicator in place and quietly stop testing this file.
    async rewriteRelative(options) {
        try {
            return {
                threw: false,
                rewritten: rewriteRelativeImports(options.source ?? ''),
            };
        } catch (error) {
            return {threw: true, message: error.message};
        }
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
