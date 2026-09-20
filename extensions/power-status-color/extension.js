import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const UPTIME_THRESHOLD_SECONDS = 30 * 24 * 60 * 60; // 30 days
const CHECK_INTERVAL_SECONDS = 300; // 5 minutes

const CLASS_OVERDUE = 'power-status-overdue';
const CLASS_REBOOT = 'power-status-reboot';

const REBOOT_FLAG_FILES = [
    '/run/reboot-required',
    '/var/run/reboot-required',
];

function loadFileAsync(file, cancellable) {
    return new Promise((resolve) => {
        file.load_contents_async(cancellable, (f, res) => {
            try {
                const [ok, contents] = f.load_contents_finish(res);
                resolve(ok ? new TextDecoder().decode(contents) : null);
            } catch {
                resolve(null);
            }
        });
    });
}

function runCommandAsync(argv, cancellable) {
    return new Promise((resolve) => {
        try {
            const proc = new Gio.Subprocess({
                argv,
                flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            });
            proc.init(cancellable);

            let cancelId = 0;
            if (cancellable) {
                cancelId = cancellable.connect(() => {
                    try {
                        proc.force_exit();
                    } catch {}
                });
            }

            proc.communicate_utf8_async(null, cancellable, (p, res) => {
                if (cancelId && cancellable) {
                    cancellable.disconnect(cancelId);
                }
                try {
                    const [ok, stdout] = p.communicate_utf8_finish(res);
                    resolve(ok ? stdout : null);
                } catch {
                    resolve(null);
                }
            });
        } catch {
            resolve(null);
        }
    });
}

export default class PowerStatusColorExtension extends Extension {
    enable() {
        this._enabled = true;
        this._cancellable = new Gio.Cancellable();
        this._styledActors = new Set();
        this._checkingStatus = false;
        this._statusQueued = false;

        // 1. Monitor /run directory for reboot-required flags
        try {
            const runDir = Gio.File.new_for_path('/run');
            this._fileMonitor = runDir.monitor_directory(
                Gio.FileMonitorFlags.NONE,
                this._cancellable
            );
            this._monitorId = this._fileMonitor.connect('changed', (mon, file, otherFile, eventType) => {
                const basename = file?.get_basename();
                if (basename === 'reboot-required') {
                    this._checkStatus();
                }
            });
        } catch (e) {
            console.error(`[PowerStatusColor] Failed to monitor /run: ${e.message}`);
        }

        // 2. Periodic poll every 5 minutes
        this._timeoutId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            CHECK_INTERVAL_SECONDS,
            () => {
                this._checkStatus();
                return GLib.SOURCE_CONTINUE;
            }
        );

        // 3. Initial check
        this._checkStatus();
    }

    disable() {
        this._enabled = false;
        this._checkingStatus = false;
        this._statusQueued = false;

        if (this._timeoutId) {
            GLib.Source.remove(this._timeoutId);
            this._timeoutId = null;
        }

        if (this._fileMonitor) {
            if (this._monitorId) {
                this._fileMonitor.disconnect(this._monitorId);
                this._monitorId = null;
            }
            this._fileMonitor.cancel();
            this._fileMonitor = null;
        }

        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }

        this._removeStyleClasses();
        this._styledActors = null;
    }

    _findPowerButton() {
        const qs = Main.panel?.statusArea?.quickSettings;
        if (!qs)
            return null;

        // Direct path via SystemItem in GNOME Shell Quick Settings
        if (qs._system?._systemItem?.menu?.sourceActor)
            return qs._system._systemItem.menu.sourceActor;

        // Fallback search across Quick Settings widgets
        const findActor = (actor) => {
            if (!actor)
                return null;

            if (actor.has_style_class_name?.('icon-button') &&
                (actor.icon_name === 'system-shutdown-symbolic' ||
                 actor.accessible_name?.toLowerCase().includes('power off')))
                return actor;

            if (actor.get_children) {
                for (const child of actor.get_children()) {
                    const match = findActor(child);
                    if (match)
                        return match;
                }
            }
            return null;
        };

        const root = qs.menu?._grid || qs.menu?.actor || qs.menu?.box || qs;
        return findActor(root);
    }

    async _checkStatus() {
        if (!this._enabled)
            return;

        if (this._checkingStatus) {
            this._statusQueued = true;
            return;
        }

        this._checkingStatus = true;
        try {
            do {
                this._statusQueued = false;

                const [isOverdue, isRebootPending] = await Promise.all([
                    this._checkUptimeOverdue(),
                    this._checkRebootPending(),
                ]);

                if (!this._enabled)
                    return;

                // Priority: Uptime (30+ days / red) overrides reboot (yellow)
                if (isOverdue) {
                    this._applyStyle(CLASS_OVERDUE);
                } else if (isRebootPending) {
                    this._applyStyle(CLASS_REBOOT);
                } else {
                    this._removeStyleClasses();
                }
            } while (this._statusQueued && this._enabled);
        } finally {
            this._checkingStatus = false;
        }
    }

    async _checkUptimeOverdue() {
        try {
            const uptimeFile = Gio.File.new_for_path('/proc/uptime');
            const content = await loadFileAsync(uptimeFile, this._cancellable);
            if (!content)
                return false;

            const uptimeSec = parseFloat(content.trim().split(/\s+/)[0]);
            return !isNaN(uptimeSec) && uptimeSec >= UPTIME_THRESHOLD_SECONDS;
        } catch {
            return false;
        }
    }

    async _checkRebootPending() {
        // 1. Check flag files
        for (const filePath of REBOOT_FLAG_FILES) {
            try {
                if (Gio.File.new_for_path(filePath).query_exists(null))
                    return true;
            } catch {
                // Ignore query error
            }
        }

        // 2. Pure bootc staged deployment check (bootc documentation convention)
        try {
            const stdout = await runCommandAsync(['bootc', 'status', '--format=json'], this._cancellable);
            if (stdout) {
                const data = JSON.parse(stdout);
                // In bootc schema, status.staged is non-null when an update is queued for next boot
                if (data?.status?.staged != null)
                    return true;
            }
        } catch {
            // Transient error (e.g. sysroot lock held during pull or permissions)
        }

        return false;
    }

    _applyStyle(className) {
        const btn = this._findPowerButton();
        if (!btn)
            return;

        const currentActors = new Set([btn]);
        if (btn.child)
            currentActors.add(btn.child);

        if (!this._styledActors)
            this._styledActors = new Set();

        for (const actor of this._styledActors) {
            if (!currentActors.has(actor)) {
                if (actor.remove_style_class_name) {
                    actor.remove_style_class_name(CLASS_OVERDUE);
                    actor.remove_style_class_name(CLASS_REBOOT);
                }
            }
        }

        const otherClass = className === CLASS_OVERDUE ? CLASS_REBOOT : CLASS_OVERDUE;
        for (const actor of currentActors) {
            if (actor.remove_style_class_name)
                actor.remove_style_class_name(otherClass);
            if (actor.add_style_class_name && !actor.has_style_class_name?.(className))
                actor.add_style_class_name(className);
        }

        this._styledActors = currentActors;
    }

    _removeStyleClasses() {
        const removed = new Set();
        if (this._styledActors) {
            for (const actor of this._styledActors) {
                if (actor.remove_style_class_name) {
                    actor.remove_style_class_name(CLASS_OVERDUE);
                    actor.remove_style_class_name(CLASS_REBOOT);
                }
                removed.add(actor);
            }
            this._styledActors.clear();
        }

        const btn = this._findPowerButton();
        if (btn) {
            if (!removed.has(btn) && btn.remove_style_class_name) {
                btn.remove_style_class_name(CLASS_OVERDUE);
                btn.remove_style_class_name(CLASS_REBOOT);
            }
            if (btn.child && !removed.has(btn.child) && btn.child.remove_style_class_name) {
                btn.child.remove_style_class_name(CLASS_OVERDUE);
                btn.child.remove_style_class_name(CLASS_REBOOT);
            }
        }
    }
}
