// Execute the shipped toggle.js unchanged below its GNOME imports. The fake
// daemon is a stateful REST boundary, not a replacement for provisioning or
// transition logic. Deployment/API interoperability is verified on real hosts.
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadGnomeModule} from './gnome_module_loader.mjs';

const SOURCE = join(dirname(fileURLToPath(import.meta.url)), '..', 'extensions',
    'syncthing-toggle', 'toggle.js');
const SECRET = 'private-test-api-key';
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

class Menu {
    constructor() { this.items = []; this.actions = []; this._settingsActions = {}; }
    setHeader(icon, title) { this.header = {icon, title}; }
    addMenuItem(item) { this.items.push(item); }
    addAction(label, callback) {
        const action = {label, callback, visible: true, sensitive: true,
            setSensitive(value) { this.sensitive = value; }};
        this.actions.push(action);
        return action;
    }
}

class Toggle {
    constructor(params) {
        Object.assign(this, params);
        this.checked = false;
        this.menu = new Menu();
        this.handlers = new Map();
        this.nextId = 0;
    }
    connect(signal, callback) { const id = ++this.nextId; this.handlers.set(id, {signal, callback}); return id; }
    disconnect(id) { this.handlers.delete(id); }
    emit(signal) { return [...this.handlers.values()].find(value => value.signal === signal)?.callback(); }
    set(properties) { Object.assign(this, properties); }
    destroy() { this.destroyed = true; }
}

function makeContext(options) {
    const folders = options.folders ?? [
        {id: 'documents', label: 'Documents', path: '/home/test/Documentos', paused: false},
        {id: 'downloads', label: 'Downloads', path: '/home/test/Descargas', paused: true},
        {id: 'pictures', label: 'Pictures', path: '/home/test/Images & Photos', paused: true},
    ];
    const ctx = {
        options, folders: clone(folders), configured: clone(options.existingFolders ?? []),
        originalFolders: clone(options.existingFolders ?? []),
        devices: clone(options.devices ?? [{deviceID: 'APPROVED-PEER', autoAcceptFolders: false}]),
        originalDevices: clone(options.devices ?? [{deviceID: 'APPROVED-PEER', autoAcceptFolders: false}]),
        defaults: {type: 'sendreceive', fsWatcherEnabled: true, rescanIntervalS: 3600,
            devices: [{deviceID: 'TEMPLATE-PEER'}, {deviceID: 'SECOND-TEMPLATE-PEER'}]},
        provisioned: options.provisioned ?? false,
        autostart: options.autostart ?? false,
        unitRunning: options.running ?? false,
        restartRequired: options.restartRequired ?? options.legacy ?? false,
        processCurrent: !(options.legacy || (options.running && options.restartRequired)),
        processKind: options.running ? options.legacy ? 'native' : 'container' : null,
        managedPrepared: !options.legacy,
        legacyCaptured: false, capturedLegacyPaths: [], nativeStarts: 0,
        nativeActivationChecks: options.nativeActivationChecks ?? 0,
        nativeReadinessChecks: options.nativeReadinessChecks ?? 0,
        nativeApiNotReadyChecks: options.nativeApiNotReadyChecks ?? 0,
        identity: options.identity ?? 'LOCAL-DEVICE-ID',
        metered: options.metered ?? false,
        pendingDevices: clone(options.pendingDevices ?? {}),
        pendingFolders: clone(options.pendingFolders ?? {}),
        apiErrors: clone(options.apiErrors ?? {}),
        commands: [], apiRequests: [], notifications: [], events: [], logs: [],
        cancellables: [], timers: new Map(), nextTimer: 0, clock: 0,
        holds: new Set(), held: new Map(), waiters: new Map(),
        activeRequests: 0, killedProcesses: 0, abortCount: 0, authentication: [],
        healthFailures: options.healthFailures ?? 0,
    };
    ctx.waitHeld = key => {
        if (ctx.held.has(key)) return Promise.resolve();
        return new Promise(resolve => ctx.waiters.set(key, resolve));
    };
    ctx.release = key => { ctx.holds.delete(key); ctx.held.get(key)?.(); ctx.held.delete(key); };
    ctx.schedule = (key, cancellable, callback, produce) => {
        ctx.events.push(key);
        let result;
        try { result = produce(); } catch (error) { result = {error}; }
        let done = false;
        let signal = 0;
        const finish = cancelled => {
            if (done) return;
            done = true;
            ctx.held.delete(key);
            if (signal) cancellable.disconnect(signal);
            queueMicrotask(() => callback(cancelled ? {error: new Error('Cancelled')} : result));
        };
        signal = cancellable.connect(() => finish(true));
        if (ctx.holds.has(key)) {
            ctx.holds.delete(key);
            ctx.held.set(key, () => finish(false));
            ctx.waiters.get(key)?.();
            ctx.waiters.delete(key);
        } else {
            setImmediate(() => finish(cancellable.is_cancelled()));
        }
    };

    class Cancellable {
        constructor() { this.cancelled = false; this.handlers = new Map(); this.nextId = 0; ctx.cancellables.push(this); }
        is_cancelled() { return this.cancelled; }
        connect(callback) {
            if (this.cancelled) { callback(); return 0; }
            const id = ++this.nextId; this.handlers.set(id, callback); return id;
        }
        disconnect(id) { this.handlers.delete(id); }
        cancel() {
            if (this.cancelled) return;
            this.cancelled = true;
            for (const callback of [...this.handlers.values()]) callback();
        }
    }

    const helperResponse = (nativeStartRequired = false) => ({
        stateDir: '/home/test/.local/state/syncthing',
        envFile: '/home/test/.local/state/syncthing/container.env',
        folders: nativeStartRequired ? [] : clone(ctx.folders), provisioned: ctx.provisioned,
        restartRequired: ctx.restartRequired, nativeStartRequired,
        documentsAvailable: !nativeStartRequired && ctx.folders.some(folder => folder.id === 'documents'),
    });
    class Subprocess {
        constructor(argv) {
            this.argv = argv;
            this.success = true;
            if (argv[0] === 'gjs') {
                const request = JSON.parse(argv[3]);
                this.key = request.action === 'prepare' && ctx.nativeStarts > 0 && !ctx.legacyCaptured
                    ? 'helper:prepare-native' : 'helper:' + request.action;
                ctx.commands.push({binary: 'gjs', action: request.action, enabled: request.enabled});
                this.run = () => {
                    if (options.fail === this.key) { this.success = false; return {stdout: ''}; }
                    if (options.legacy && !ctx.legacyCaptured && request.action === 'prepare') {
                        if (!ctx.unitRunning || ctx.nativeReadinessChecks > 0) {
                            if (ctx.unitRunning) ctx.nativeReadinessChecks -= 1;
                            return {stdout: JSON.stringify(helperResponse(true))};
                        }
                        if (ctx.nativeApiNotReadyChecks > 0) {
                            if (!request.waitForNative) { this.success = false; return {stdout: ''}; }
                            ctx.nativeApiNotReadyChecks -= 1;
                            return {stdout: JSON.stringify(helperResponse(true))};
                        }
                        if (options.failNativeInspection) { this.success = false; return {stdout: ''}; }
                        ctx.legacyCaptured = true;
                        ctx.capturedLegacyPaths = ctx.configured.map(folder => folder.path);
                        ctx.managedPrepared = true;
                    }
                    if (request.action === 'provisioned') {
                        ctx.provisioned = true;
                        ctx.restartRequired = false;
                    }
                    if (request.action === 'autostart') ctx.autostart = request.enabled;
                    return {stdout: options.malformedHelper ? '{invalid' : JSON.stringify(helperResponse())};
                };
            } else if (argv[0] === 'systemctl') {
                const verb = argv[2];
                this.key = 'systemctl:' + verb;
                if (verb === 'is-active' && ctx.nativeStarts > 0 && ctx.processKind === 'native' && ctx.unitRunning)
                    this.key = 'native:activation';
                ctx.commands.push({binary: 'systemctl', verb, service: argv[3] ?? null});
                this.run = () => {
                    if (options.fail === this.key) { this.success = false; return {stdout: ''}; }
                    // Starting an active unit does not replace its existing process.
                    if (verb === 'restart' || (verb === 'start' && !ctx.unitRunning)) {
                        ctx.unitRunning = true;
                        ctx.processKind = ctx.managedPrepared ? 'container' : 'native';
                        ctx.processCurrent = ctx.processKind === 'container';
                        if (ctx.processKind === 'native') ctx.nativeStarts += 1;
                    }
                    if (verb === 'stop' && !options.stopIneffective) {
                        ctx.unitRunning = false;
                        ctx.processKind = null;
                    }
                    if (verb === 'is-active') {
                        this.success = ctx.unitRunning;
                        if (ctx.unitRunning && ctx.processKind === 'native' && ctx.nativeActivationChecks > 0) {
                            ctx.nativeActivationChecks -= 1;
                            return {stdout: 'activating\n'};
                        }
                        return {stdout: ctx.unitRunning ? 'active\n' : 'inactive\n'};
                    }
                    if (!['start', 'restart', 'stop', 'daemon-reload'].includes(verb)) throw new Error('Unsupported service command');
                    return {stdout: ''};
                };
            } else {
                throw new Error('Unexpected native executable');
            }
        }
        communicate_utf8_async(_stdin, cancellable, callback) {
            ctx.schedule(this.key, cancellable, result => callback(this, result), this.run);
        }
        communicate_utf8_finish(result) { if (result.error) throw result.error; return [true, result.stdout, '']; }
        get_successful() { return this.success; }
        force_exit() { ctx.killedProcesses += 1; }
        wait_async(_cancellable, callback) { setImmediate(() => callback(this, {})); }
        wait_finish() { return true; }
    }

    class Message {
        constructor(method, url) { this.method = method; this.url = url; this.headers = new Map(); this.status = 0; }
        get_request_headers() { return {append: (name, value) => this.headers.set(name, value)}; }
        set_request_body_from_bytes(_type, bytes) { this.body = JSON.parse(new TextDecoder().decode(bytes.get_data())); }
        get_status() { return this.status; }
    }
    const responseFor = message => {
        const path = new URL(message.url).pathname.replace(/^\/rest\//, '');
        const key = message.method + ':' + path;
        ctx.apiRequests.push({method: message.method, path, ...(message.body ? {body: clone(message.body)} : {})});
        const authenticated = message.headers.get('X-API-Key') === SECRET;
        ctx.authentication.push(authenticated);
        if (!authenticated) { message.status = 401; return {text: SECRET}; }
        // A legacy process or old listener still uses its previous credentials
        // until systemd actually replaces it with the prepared configuration.
        if (ctx.unitRunning && !ctx.processCurrent) { message.status = 401; return {text: SECRET}; }
        let error = ctx.apiErrors[key] ?? ctx.apiErrors[path];
        if (Array.isArray(error)) error = error.length > 1 ? error.shift() : error[0];
        if (error?.transport) throw new Error('Network unreachable');
        if (error) { message.status = error.status ?? 200; return {text: error.raw ?? SECRET}; }
        if (path === 'system/status' && ctx.healthFailures > 0) {
            ctx.healthFailures -= 1;
            throw new Error('Container is starting');
        }
        if (!ctx.unitRunning) throw new Error('Connection refused');
        message.status = 200;
        if (message.method === 'GET') {
            if (path === 'system/status') return {text: JSON.stringify({myID: ctx.identity})};
            if (path === 'config/folders') return {text: JSON.stringify(ctx.configured)};
            if (path === 'config/defaults/folder') return {text: JSON.stringify(ctx.defaults)};
            if (path === 'cluster/pending/devices') return {text: JSON.stringify(ctx.pendingDevices)};
            if (path === 'cluster/pending/folders') return {text: JSON.stringify(ctx.pendingFolders)};
        }
        if (message.method === 'POST' && path === 'config/folders') {
            const index = ctx.configured.findIndex(folder => folder.id === message.body.id);
            if (index >= 0) ctx.configured[index] = clone(message.body);
            else ctx.configured.push(clone(message.body));
            return {text: ''};
        }
        throw new Error('Unsupported REST operation');
    };
    class Session {
        send_and_read_async(message, _priority, cancellable, callback) {
            ctx.activeRequests += 1;
            const path = new URL(message.url).pathname.replace(/^\/rest\//, '');
            ctx.schedule('api:' + message.method + ':' + path, cancellable, result => {
                ctx.activeRequests -= 1;
                callback(this, result);
            }, () => responseFor(message));
        }
        send_and_read_finish(result) { if (result.error) throw result.error; return {get_data: () => new TextEncoder().encode(result.text)}; }
        abort() { ctx.abortCount += 1; }
    }
    const networkHandlers = new Map();
    ctx.networkHandlers = networkHandlers;
    ctx.setMetered = async value => {
        ctx.metered = value;
        await Promise.all([...networkHandlers.values()].map(callback => callback()));
    };
    const addTimer = (milliseconds, callback, repeating = false) => {
        const id = ++ctx.nextTimer;
        const fire = () => {
            if (!ctx.timers.has(id)) return;
            ctx.clock += milliseconds * 1000;
            if (callback() === false) ctx.timers.delete(id);
        };
        let handle;
        if (!repeating) {
            if (milliseconds <= 500) {
                if (ctx.holds.has('delay')) {
                    ctx.held.set('delay', fire);
                    ctx.waiters.get('delay')?.();
                    ctx.waiters.delete('delay');
                } else handle = setImmediate(fire);
            } else handle = setTimeout(fire, milliseconds / 100);
        }
        ctx.timers.set(id, {handle, immediate: milliseconds <= 500});
        return id;
    };
    const GLib = {
        PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false, SOURCE_CONTINUE: true,
        get_user_state_dir: () => '/home/test/.local/state',
        get_monotonic_time: () => ctx.clock,
        Bytes: {new: data => ({get_data: () => data})},
        timeout_add: (_priority, milliseconds, callback) => addTimer(milliseconds, callback),
        timeout_add_seconds: (_priority, seconds, callback) => addTimer(seconds * 1000, callback, true),
        Source: {remove(id) {
            const timer = ctx.timers.get(id);
            if (timer?.handle) (timer.immediate ? clearImmediate : clearTimeout)(timer.handle);
            return ctx.timers.delete(id);
        }},
    };
    const stubs = {
        Gio: {
            Cancellable, Subprocess: {new: argv => new Subprocess(argv)},
            SubprocessFlags: {STDOUT_PIPE: 1, STDERR_SILENCE: 2},
            icon_new_for_string: value => value,
            File: {new_for_path: path => ({
                load_contents_async(cancellable, callback) {
                    ctx.schedule('file:credentials', cancellable, result => callback(this, result), () => {
                        if (options.envUnreadable || !ctx.managedPrepared || !path.endsWith('/container.env'))
                            throw new Error('File unavailable');
                        return {text: options.invalidCredentials ? 'STGUIAPIKEY=\nSTGUIADDRESS=other-host:8384\n' :
                            `STGUIAPIKEY=${SECRET}\nSTGUIADDRESS=http://127.0.0.1:${options.port ?? 8384}\n`};
                    });
                },
                load_contents_finish(result) { if (result.error) throw result.error; return [true, new TextEncoder().encode(result.text)]; },
            })},
            NetworkMonitor: {get_default: () => ({
                get_network_metered: () => ctx.metered,
                connect(_signal, callback) { const id = networkHandlers.size + 1; networkHandlers.set(id, callback); return id; },
                disconnect(id) { networkHandlers.delete(id); },
            })},
            app_info_launch_default_for_uri(url) { ctx.launchedUrl = url; },
        },
        GLib, Soup: {Session, Message: {new: (method, url) => new Message(method, url)}},
        GObject: {registerClass: value => value},
        QuickMenuToggle: Toggle,
        SystemIndicator: class {
            constructor() { this.quickSettingsItems = []; this.panelIconCount = 0; this.destroyCount = 0; }
            _addIndicator() { this.panelIconCount += 1; throw new Error('Separate panel icon is forbidden'); }
            destroy() { this.destroyCount += 1; }
        },
        PopupMenu: {PopupMenuSection: Menu, PopupSeparatorMenuItem: class {}},
        Main: {sessionMode: {allowSettings: options.allowSettings ?? true},
            notify(title, body) { ctx.notifications.push({title, body, active: ctx.indicator?._observedActive}); }},
        gettext: value => value,
    };
    globalThis.logError = (error, message) => ctx.logs.push({message, error: String(error)});
    ctx.stubs = stubs;
    return ctx;
}

async function build(options = {}) {
    const ctx = makeContext(options);
    globalThis.__syncthingStubs = ctx.stubs;
    const {ServiceIndicator} = await loadGnomeModule({path: SOURCE, stubsExpression: 'globalThis.__syncthingStubs'});
    ctx.indicator = new ServiceIndicator({path: '/extension', uuid: 'syncthing-toggle@projectbluefin.io',
        getSettings: () => ({
            get_string: key => key === 'service-name' ? options.serviceName ?? 'syncthing.service' : '',
            get_int: () => options.port ?? 8384,
            get_boolean: () => options.startStopOnly ?? false,
        }), openPreferences() { ctx.preferencesOpened = true; }});
    ctx.click = enabled => { ctx.indicator._toggle.checked = enabled; return ctx.indicator._toggle.emit('clicked'); };
    return ctx;
}

function snapshot(ctx) {
    return {
        checked: ctx.indicator._toggle.checked, subtitle: ctx.indicator._toggle.subtitle,
        webGuiSensitive: ctx.indicator._toggle.webGuiItem.sensitive,
        desiredOn: ctx.indicator._desiredOn, paused: ctx.indicator._pausedForMetered,
        running: ctx.unitRunning, autostart: ctx.autostart, provisioned: ctx.provisioned,
        processCurrent: ctx.processCurrent, restartRequired: ctx.restartRequired,
        processKind: ctx.processKind, managedPrepared: ctx.managedPrepared,
        legacyCaptured: ctx.legacyCaptured, capturedLegacyPaths: clone(ctx.capturedLegacyPaths),
        identity: ctx.identity,
        configured: clone(ctx.configured), originalFolders: clone(ctx.originalFolders),
        devices: clone(ctx.devices), originalDevices: clone(ctx.originalDevices),
        commands: clone(ctx.commands), apiRequests: clone(ctx.apiRequests),
        notifications: clone(ctx.notifications), pending: [...ctx.indicator._announcedPending],
        authenticated: ctx.authentication.every(Boolean), requestCount: ctx.authentication.length,
        activeRequests: ctx.activeRequests, killedProcesses: ctx.killedProcesses,
        timerCount: ctx.timers.size, cancellationHandlers: ctx.cancellables.reduce((sum, c) => sum + c.handlers.size, 0),
        networkHandlers: ctx.networkHandlers.size, clickHandlers: ctx.indicator._toggle.handlers.size,
        abortCount: ctx.abortCount, destroyCount: ctx.indicator.destroyCount,
        panelIconCount: ctx.indicator.panelIconCount, quickSettingsCount: ctx.indicator.quickSettingsItems.length,
        secretLeaked: JSON.stringify([ctx.notifications, ctx.logs, ctx.commands, ctx.apiRequests]).includes(SECRET),
    };
}

const scenarios = {
    async initial(options) { const ctx = await build(options); await ctx.click(true); return snapshot(ctx); },
    async firstUse(options) {
        const ctx = await build({...options, running: true});
        await ctx.indicator.checkStatus();
        const before = snapshot(ctx);
        await ctx.click(true);
        return {before, after: snapshot(ctx)};
    },
    async stoppedFirstUse(options) {
        const ctx = await build({...options, running: false, legacy: true});
        await ctx.indicator.checkStatus();
        const before = snapshot(ctx);
        await ctx.click(true);
        return {before, after: snapshot(ctx)};
    },
    async nativeInterrupt(options) {
        const ctx = await build({...options, running: false, legacy: true});
        const gate = options.gate ?? 'native:activation';
        ctx.holds.add(gate);
        const operation = ctx.click(true);
        await ctx.waitHeld(gate);
        const before = snapshot(ctx);
        if (options.interrupt === 'destroy') ctx.indicator.destroy();
        else if (options.interrupt === 'cancel') ctx.indicator._operationCancellable.cancel();
        else await ctx.click(false);
        await operation;
        ctx.release(gate);
        await tick();
        return {before, after: snapshot(ctx)};
    },
    async laterStart(options) {
        const ctx = await build(options);
        await ctx.click(true);
        const documents = ctx.configured.find(folder => folder.id === 'documents');
        documents.label = 'My chosen name'; documents.paused = true;
        documents.devices = [{deviceID: 'USER-APPROVED-PEER'}];
        ctx.configured = ctx.configured.filter(folder => folder.id !== 'pictures');
        await ctx.click(false); await ctx.click(true);
        return snapshot(ctx);
    },
    async rapid(options) {
        const ctx = await build(options);
        const gate = options.gate ?? 'systemctl:start';
        ctx.holds.add(gate);
        const on = ctx.click(true);
        await ctx.waitHeld(gate);
        const off = ctx.click(false);
        await Promise.all([on, off]);
        ctx.release(gate);
        return snapshot(ctx);
    },
    async rapidRestart(options) {
        const ctx = await build({...options, running: true, provisioned: true, autostart: true});
        await ctx.indicator.checkStatus();
        const gate = 'systemctl:stop'; ctx.holds.add(gate);
        const off = ctx.click(false); await ctx.waitHeld(gate);
        const on = ctx.click(true); await Promise.all([off, on]);
        ctx.release(gate); return snapshot(ctx);
    },
    async rapidSequence(options) {
        const ctx = await build(options);
        await Promise.all([ctx.click(true), ctx.click(false), ctx.click(true), ctx.click(false)]);
        return snapshot(ctx);
    },
    async meteredFirst(options) {
        const ctx = await build({...options, metered: true});
        await ctx.click(true);
        const before = snapshot(ctx);
        await ctx.setMetered(false);
        return {before, after: snapshot(ctx)};
    },
    async meteredPause(options) {
        const ctx = await build(options);
        await ctx.click(true);
        await ctx.setMetered(true);
        const paused = snapshot(ctx);
        if (options.manualOff) await ctx.click(false);
        await ctx.setMetered(false);
        return {paused, after: snapshot(ctx)};
    },
    async meteredDuringStart(options) {
        const ctx = await build(options);
        const gate = 'api:GET:system/status'; ctx.holds.add(gate);
        const on = ctx.click(true); await ctx.waitHeld(gate);
        await ctx.setMetered(true); await on;
        ctx.release(gate);
        const paused = snapshot(ctx);
        await ctx.setMetered(false);
        return {paused, after: snapshot(ctx)};
    },
    async startStopOnly(options) {
        const ctx = await build({...options, startStopOnly: true});
        await ctx.click(true); await ctx.click(false); return snapshot(ctx);
    },
    async failedStop(options) {
        const ctx = await build({...options, running: true, provisioned: true, autostart: true,
            fail: options.fail ?? 'systemctl:stop'});
        await ctx.indicator.checkStatus(); await ctx.click(false); return snapshot(ctx);
    },
    async health(options) {
        const ctx = await build({...options, running: true});
        await ctx.indicator.checkStatus(); return snapshot(ctx);
    },
    async stalePoll(options) {
        const ctx = await build({...options, running: true, provisioned: true});
        const gate = 'api:GET:system/status'; ctx.holds.add(gate);
        const poll = ctx.indicator.checkStatus(); await ctx.waitHeld(gate);
        await ctx.click(false); await poll; ctx.release(gate); return snapshot(ctx);
    },
    async pending(options) {
        const ctx = await build({...options, running: true, provisioned: true});
        ctx.pendingDevices = {'NEW-DEVICE': {name: 'New computer'}};
        ctx.pendingFolders = {shared: {offeredBy: {'DEVICE-A': {label: 'Offered'}}}};
        const steps = [];
        const poll = async () => { await ctx.indicator._pollStatusAndPending(); steps.push(snapshot(ctx)); };
        await poll(); await poll();
        ctx.apiErrors['cluster/pending/folders'] = {status: 503}; await poll();
        ctx.apiErrors['cluster/pending/folders'] = {status: 200, raw: '{malformed'}; await poll();
        delete ctx.apiErrors['cluster/pending/folders']; await poll();
        ctx.pendingFolders.shared.offeredBy['DEVICE-B'] = {label: 'Same folder, another device'}; await poll();
        ctx.pendingDevices = {}; ctx.pendingFolders = {}; await poll();
        ctx.pendingFolders = {shared: {offeredBy: {'DEVICE-A': {label: 'Offered again'}}}}; await poll();
        return {steps};
    },
    async overlappingPoll(options) {
        const ctx = await build({...options, running: true, provisioned: true});
        const gate = 'api:GET:cluster/pending/devices'; ctx.holds.add(gate);
        const first = ctx.indicator._pollStatusAndPending(); await ctx.waitHeld(gate);
        const second = ctx.indicator._pollStatusAndPending();
        const third = ctx.indicator._pollStatusAndPending();
        ctx.release(gate); await Promise.all([first, second, third]); return snapshot(ctx);
    },
    async teardown(options) {
        const ctx = await build(options);
        const gate = options.gate ?? 'api:GET:system/status'; ctx.holds.add(gate);
        const operation = ctx.click(true); await ctx.waitHeld(gate);
        const before = snapshot(ctx);
        ctx.indicator.destroy(); ctx.indicator.destroy();
        await operation; await tick(); return {before, after: snapshot(ctx)};
    },
    async teardownDelay(options) {
        const ctx = await build({...options, healthFailures: 1000});
        ctx.holds.add('delay'); const operation = ctx.click(true); await ctx.waitHeld('delay');
        ctx.indicator.destroy(); await operation; return snapshot(ctx);
    },
    async reconcile(options) {
        const ctx = await build({...options, running: true, metered: true, provisioned: true, autostart: true});
        await ctx.indicator.reconcile(); return snapshot(ctx);
    },
    async meteredNativeFirstUse(options) {
        const ctx = await build({...options, running: true, legacy: true, metered: true, autostart: true});
        await ctx.indicator.reconcile();
        const before = snapshot(ctx);
        await ctx.setMetered(false);
        return {before, after: snapshot(ctx)};
    },
    async menu(options) {
        const ctx = await build(options);
        const settings = ctx.indicator._toggle.menu._settingsActions['syncthing-toggle@projectbluefin.io'];
        settings.callback(); ctx.indicator._toggle.webGuiItem.callback();
        return {...snapshot(ctx), settingsVisible: settings.visible, preferencesOpened: ctx.preferencesOpened,
            launchedUrl: ctx.launchedUrl};
    },
};

const [, , name, optionsJson] = process.argv;
if (!scenarios[name]) throw new Error('Unknown scenario: ' + name);
const result = await scenarios[name](optionsJson ? JSON.parse(optionsJson) : {});
process.stdout.write(JSON.stringify(result));
