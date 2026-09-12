// Behavior harness for extensions/power-status-color/extension.js.
//
// The extension imports gi:// and resource:///org/gnome/shell modules that only
// exist inside gnome-shell, so plain `import` cannot load it. The harness reads
// the real source, rewrites only its import block into a binding taken from
// globalThis, and imports the result as a data: module. Everything below the
// import block — the logic under test — is the byte-for-byte shipped source.
//
// Usage: node power_status_color_harness.mjs <scenario> ['<json options>']
// Prints a single JSON object describing the observed result.

import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const EXTENSION_JS = join(HERE, '..', 'extensions', 'power-status-color', 'extension.js');

const IMPORT_LINE_RE = /^\s*import\s+(?:(\*\s+as\s+\w+)|(\{[^}]*\})|(\w+))\s+from\s+['"](?:gi:\/\/|resource:\/\/\/)[^'"]+['"];?\s*$/gm;

function loadExtensionModule() {
    const source = readFileSync(EXTENSION_JS, 'utf8');
    const matches = [...source.matchAll(IMPORT_LINE_RE)];
    if (matches.length === 0)
        throw new Error('no gi:// or resource:/// imports found — harness rewrite is stale');

    const rewritten = source.replace(
        IMPORT_LINE_RE,
        (_line, namespaceImport, namedImport, defaultImport) => {
            if (namespaceImport) {
                const name = namespaceImport.split(/\s+as\s+/)[1];
                return `const ${name} = globalThis.__pscStubs.${name};`;
            }
            if (namedImport)
                return `const ${namedImport} = globalThis.__pscStubs;`;
            return `const ${defaultImport} = globalThis.__pscStubs.${defaultImport};`;
        },
    );
    const url = `data:text/javascript;base64,${Buffer.from(rewritten, 'utf8').toString('base64')}`;
    return import(url);
}

// --- Stubs ------------------------------------------------------------------

class FakeActor {
    constructor({styleClasses = [], iconName = null, accessibleName = null, children = [], child = null} = {}) {
        this._styleClasses = new Set(styleClasses);
        this.icon_name = iconName;
        this.accessible_name = accessibleName;
        this._children = children;
        this.child = child;
    }

    has_style_class_name(name) {
        return this._styleClasses.has(name);
    }

    add_style_class_name(name) {
        this._styleClasses.add(name);
    }

    remove_style_class_name(name) {
        this._styleClasses.delete(name);
    }

    get_children() {
        return this._children;
    }

    classes() {
        return [...this._styleClasses].sort();
    }
}

// Build a FakeActor tree from a plain JSON spec so tests can describe the
// Quick Settings widget hierarchy the fallback search has to walk.
function buildActorTree(spec) {
    return new FakeActor({
        styleClasses: spec.styleClasses ?? [],
        iconName: spec.iconName ?? null,
        accessibleName: spec.accessibleName ?? null,
        children: (spec.children ?? []).map(buildActorTree),
    });
}

function makeStubs(options) {
    const log = {
        timeoutsAdded: [],
        timeoutsRemoved: [],
        monitorCancelled: false,
        monitorDisconnected: false,
        cancellableCancelled: false,
        subprocessArgv: null,
    };

    let monitorChangedCallback = null;
    const fileMonitor = {
        connect(signal, cb) {
            if (signal === 'changed')
                monitorChangedCallback = cb;
            return 77;
        },
        disconnect() {
            log.monitorDisconnected = true;
            monitorChangedCallback = null;
        },
        cancel() {
            log.monitorCancelled = true;
        },
    };

    const Gio = {
        SubprocessFlags: {STDOUT_PIPE: 1, STDERR_SILENCE: 2},
        FileMonitorFlags: {NONE: 0},
        Cancellable: class Cancellable {
            constructor() {
                this._handlers = [];
            }

            connect(cb) {
                this._handlers.push(cb);
                return this._handlers.length;
            }

            disconnect() {}

            cancel() {
                log.cancellableCancelled = true;
            }
        },
        Subprocess: class Subprocess {
            constructor({argv}) {
                log.subprocessArgv = argv;
                if (options.subprocessThrows)
                    throw new Error('spawn failed');
            }

            init() {}

            force_exit() {}

            communicate_utf8_async(_stdin, _cancellable, cb) {
                queueMicrotask(() => cb(this, 'result'));
            }

            communicate_utf8_finish() {
                if (options.bootcThrows)
                    throw new Error('communicate failed');
                if (options.bootcStdout === undefined)
                    return [false, null];
                return [true, options.bootcStdout];
            }
        },
        File: {
            new_for_path(path) {
                return {
                    get_path: () => path,
                    query_exists() {
                        if (options.flagFileThrows)
                            throw new Error('query failed');
                        return (options.existingFlagFiles ?? []).includes(path);
                    },
                    load_contents_async(_cancellable, cb) {
                        queueMicrotask(() => cb(this, 'result'));
                    },
                    load_contents_finish() {
                        if (options.uptimeThrows)
                            throw new Error('load failed');
                        if (options.uptimeContent === undefined)
                            return [false, null];
                        return [true, Buffer.from(options.uptimeContent, 'utf8')];
                    },
                    monitor_directory() {
                        if (options.monitorThrows)
                            throw new Error('monitor failed');
                        return fileMonitor;
                    },
                };
            },
        },
    };

    const GLib = {
        PRIORITY_DEFAULT: 0,
        SOURCE_CONTINUE: true,
        timeout_add_seconds(_priority, interval, cb) {
            log.timeoutsAdded.push({interval, cb});
            return 42;
        },
        Source: {
            remove(id) {
                log.timeoutsRemoved.push(id);
            },
        },
    };

    const button = options.button ?? new FakeActor({
        styleClasses: options.initialClasses ?? [],
        child: options.withChild ? new FakeActor({styleClasses: options.initialClasses ?? []}) : null,
    });

    let quickSettings;
    if (options.quickSettings === null) {
        quickSettings = null;
    } else if (options.fallbackTree) {
        quickSettings = {menu: {_grid: buildActorTree(options.fallbackTree)}};
    } else {
        quickSettings = {_system: {_systemItem: {menu: {sourceActor: button}}}};
    }

    const Main = {panel: {statusArea: {quickSettings}}};

    class Extension {}

    return {
        stubs: {Gio, GLib, Main, Extension},
        log,
        button,
        fireMonitorChange(basename) {
            monitorChangedCallback?.(fileMonitor, {get_basename: () => basename}, null, 0);
        },
        hasMonitorCallback: () => monitorChangedCallback !== null,
    };
}

// --- Scenarios --------------------------------------------------------------

async function buildExtension(options) {
    const harness = makeStubs(options);
    globalThis.__pscStubs = harness.stubs;
    const module = await loadExtensionModule();
    const ext = new module.default();
    return {ext, ...harness};
}

const scenarios = {
    async checkStatus(options) {
        const {ext, button} = await buildExtension(options);
        ext._enabled = true;
        ext._cancellable = null;
        await ext._checkStatus();
        return {
            classes: button.classes(),
            childClasses: button.child ? button.child.classes() : null,
        };
    },

    async checkStatusWhileDisabled(options) {
        const {ext, button, log} = await buildExtension(options);
        ext._enabled = false;
        ext._cancellable = null;
        await ext._checkStatus();
        return {classes: button.classes(), subprocessArgv: log.subprocessArgv};
    },

    // Disables the extension after _checkStatus has started its async probes but
    // before they resolve, which is what happens when the user turns the
    // extension off mid-poll. The post-await guard has to swallow the result.
    async disabledMidCheck(options) {
        const {ext, button} = await buildExtension(options);
        ext._enabled = true;
        ext._cancellable = null;
        const pending = ext._checkStatus();
        ext._enabled = false;
        await pending;
        return {classes: button.classes()};
    },

    async uptimeOverdue(options) {
        const {ext} = await buildExtension(options);
        ext._cancellable = null;
        return {overdue: await ext._checkUptimeOverdue()};
    },

    async rebootPending(options) {
        const {ext, log} = await buildExtension(options);
        ext._cancellable = null;
        const pending = await ext._checkRebootPending();
        return {pending, subprocessArgv: log.subprocessArgv};
    },

    async findPowerButton(options) {
        const {ext} = await buildExtension(options);
        const btn = ext._findPowerButton();
        return {found: btn !== null, marker: btn?.accessible_name ?? btn?.icon_name ?? null};
    },

    async lifecycle(options) {
        const {ext, log, button, fireMonitorChange, hasMonitorCallback} = await buildExtension(options);
        ext.enable();
        await new Promise(resolve => setTimeout(resolve, 0));
        const afterEnable = {
            classes: button.classes(),
            intervals: log.timeoutsAdded.map(t => t.interval),
            monitorConnected: hasMonitorCallback(),
        };
        ext.disable();
        return {
            afterEnable,
            afterDisable: {
                classes: button.classes(),
                enabled: ext._enabled,
                timeoutId: ext._timeoutId,
                fileMonitor: ext._fileMonitor,
                cancellable: ext._cancellable,
                timeoutsRemoved: log.timeoutsRemoved,
                monitorCancelled: log.monitorCancelled,
                monitorDisconnected: log.monitorDisconnected,
                cancellableCancelled: log.cancellableCancelled,
                monitorStillConnected: hasMonitorCallback(),
            },
            fireAfterDisableThrows: (() => {
                try {
                    fireMonitorChange('reboot-required');
                    return false;
                } catch {
                    return true;
                }
            })(),
        };
    },

    async monitorTrigger(options) {
        const {ext, button, fireMonitorChange} = await buildExtension(options);
        ext.enable();
        await new Promise(resolve => setTimeout(resolve, 0));
        button.remove_style_class_name('power-status-overdue');
        button.remove_style_class_name('power-status-reboot');
        fireMonitorChange(options.changedBasename);
        await new Promise(resolve => setTimeout(resolve, 0));
        const classes = button.classes();
        ext.disable();
        return {classes};
    },

    async timerCallback(options) {
        const {ext, log, button} = await buildExtension(options);
        ext.enable();
        await new Promise(resolve => setTimeout(resolve, 0));
        button.remove_style_class_name('power-status-overdue');
        button.remove_style_class_name('power-status-reboot');
        const returned = log.timeoutsAdded[0].cb();
        await new Promise(resolve => setTimeout(resolve, 0));
        const classes = button.classes();
        ext.disable();
        return {returned, classes};
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
