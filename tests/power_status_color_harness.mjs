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
        this._destroyed = false;
        this._handlers = new Map();
        this._nextHandlerId = 0;
    }

    // Mirrors GJS: touching a Shell-destroyed actor throws instead of silently
    // working, which is what the retained-reference guards have to survive.
    _assertAlive() {
        if (this._destroyed)
            throw new Error('Object St.Widget has been already deallocated');
    }

    has_style_class_name(name) {
        this._assertAlive();
        return this._styleClasses.has(name);
    }

    add_style_class_name(name) {
        this._assertAlive();
        this._styleClasses.add(name);
    }

    remove_style_class_name(name) {
        this._assertAlive();
        this._styleClasses.delete(name);
    }

    connect(signal, callback) {
        this._assertAlive();
        const id = ++this._nextHandlerId;
        this._handlers.set(id, {signal, callback});
        return id;
    }

    disconnect(id) {
        this._assertAlive();
        this._handlers.delete(id);
    }

    connectedSignals() {
        return [...this._handlers.values()].map(h => h.signal);
    }

    // Shell destroying the actor: emit 'destroy', then behave as deallocated.
    destroy() {
        const handlers = [...this._handlers.values()].filter(h => h.signal === 'destroy');
        this._handlers.clear();
        this._destroyed = true;
        for (const {callback} of handlers)
            callback(this);
    }

    // Disposal without a 'destroy' emission reaching us (e.g. the actor was
    // never connectable), so only the try/catch guards can save the caller.
    disposeSilently() {
        this._handlers.clear();
        this._destroyed = true;
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
                styledActors: ext._styledActors,
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

    async disableWhenButtonUnresolvable(options) {
        const {ext, button, stubs} = await buildExtension(options);
        ext.enable();
        await new Promise(resolve => setTimeout(resolve, 0));
        const classesAfterEnable = button.classes();
        const childClassesAfterEnable = button.child ? button.child.classes() : null;

        // Simulate button becoming unresolvable before disable()
        stubs.Main.panel.statusArea.quickSettings = null;

        ext.disable();
        return {
            classesAfterEnable,
            childClassesAfterEnable,
            classesAfterDisable: button.classes(),
            childClassesAfterDisable: button.child ? button.child.classes() : null,
            styledActorsAfterDisable: ext._styledActors,
        };
    },

    async actorReplacedCleansOrphan(options) {
        const {ext, button, stubs} = await buildExtension(options);
        ext._enabled = true;
        ext._cancellable = null;
        await ext._checkStatus();
        const firstClasses = button.classes();

        // Simulate Quick Settings rebuilding with a new button actor
        const newButton = new FakeActor({
            styleClasses: [],
            child: new FakeActor({styleClasses: []}),
        });
        stubs.Main.panel.statusArea.quickSettings._system._systemItem.menu.sourceActor = newButton;

        await ext._checkStatus();
        return {
            firstClasses,
            firstClassesAfterReplace: button.classes(),
            firstChildClassesAfterReplace: button.child ? button.child.classes() : null,
            newClasses: newButton.classes(),
            newChildClasses: newButton.child.classes(),
        };
    },

    // Shell destroys the styled actor (emitting 'destroy') and then rebuilds
    // Quick Settings. The retained reference must have been evicted and the
    // recheck must still style the new actor.
    async destroyedActorEvictedOnRebuild(options) {
        const {ext, button, stubs} = await buildExtension(options);
        ext._enabled = true;
        ext._cancellable = null;
        await ext._checkStatus();
        const trackedAfterFirstCheck = ext._styledActors.size;
        const destroySignalsConnected = button.connectedSignals();

        button.destroy();
        const trackedAfterDestroy = ext._styledActors.size;

        const newButton = new FakeActor({styleClasses: []});
        stubs.Main.panel.statusArea.quickSettings._system._systemItem.menu.sourceActor = newButton;

        let threw = false;
        try {
            await ext._checkStatus();
        } catch {
            threw = true;
        }

        return {
            trackedAfterFirstCheck,
            destroySignalsConnected,
            trackedAfterDestroy,
            threw,
            newClasses: newButton.classes(),
            trackedAfterRecheck: ext._styledActors.size,
        };
    },

    // Same rebuild, but the actor was disposed without us seeing 'destroy'.
    // _applyStyle has to survive the throwing retained reference.
    async silentlyDisposedActorDoesNotBreakRestyle(options) {
        const {ext, button, stubs} = await buildExtension(options);
        ext._enabled = true;
        ext._cancellable = null;
        await ext._checkStatus();

        button.disposeSilently();

        const newButton = new FakeActor({styleClasses: []});
        stubs.Main.panel.statusArea.quickSettings._system._systemItem.menu.sourceActor = newButton;

        let threw = false;
        try {
            await ext._checkStatus();
        } catch {
            threw = true;
        }

        return {threw, newClasses: newButton.classes(), trackedAfterRecheck: ext._styledActors.size};
    },

    // disable() must complete (and null out the retained set) even when the
    // styled actor throws on every style mutation.
    async disableWithSilentlyDisposedActor(options) {
        const {ext, button, stubs} = await buildExtension(options);
        ext.enable();
        await new Promise(resolve => setTimeout(resolve, 0));
        const trackedAfterEnable = ext._styledActors.size;

        button.disposeSilently();
        stubs.Main.panel.statusArea.quickSettings = null;

        let threw = false;
        try {
            ext.disable();
        } catch {
            threw = true;
        }

        return {
            trackedAfterEnable,
            threw,
            enabled: ext._enabled,
            styledActorsAfterDisable: ext._styledActors,
            cancellableCancelled: ext._cancellable === null,
        };
    },

    // A screen lock/unlock (disable() then enable()) while a check is awaiting
    // its probes must retire that run: it may not write styles beside the new
    // session's check, and it may not clear the new run's in-flight guard.
    async staleRunAfterReenable(options) {
        const {ext} = await buildExtension(options);
        ext._enabled = true;
        ext._cancellable = null;
        ext._generation = 1;
        ext._styledActors = new Map();
        ext._checkingStatus = false;
        ext._statusQueued = false;

        const stale = ext._checkStatus();

        const applied = [];
        const realApply = ext._applyStyle.bind(ext);
        ext._applyStyle = className => {
            applied.push(className);
            realApply(className);
        };

        // Hold the new session's probe open so the stale run is observed while
        // the new run is still in flight.
        let releaseNewRun;
        const gate = new Promise(resolve => {
            releaseNewRun = resolve;
        });
        ext._checkUptimeOverdue = () => gate;

        ext.disable();
        ext.enable(); // starts the new session's check, which now blocks on gate

        await stale;
        const afterStale = {
            applyCalls: applied.length,
            checkingStatus: ext._checkingStatus,
        };

        releaseNewRun(true);
        await new Promise(resolve => setTimeout(resolve, 0));
        const result = {
            applyCallsAfterStale: afterStale.applyCalls,
            checkingStatusAfterStale: afterStale.checkingStatus,
            applyCallsAfterNewRun: applied.length,
            checkingStatus: ext._checkingStatus,
        };
        ext.disable();
        return result;
    },

    async inFlightGuard(options) {
        const {ext, button} = await buildExtension(options);
        ext._enabled = true;
        ext._cancellable = null;

        const first = ext._checkStatus();
        const second = ext._checkStatus();

        await Promise.all([first, second]);
        return {
            classes: button.classes(),
            checkingStatus: ext._checkingStatus,
            statusQueued: ext._statusQueued,
        };
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
