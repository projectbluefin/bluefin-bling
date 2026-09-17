/* Behavioural harness for the syncthing-toggle extension.
 *
 * Loads the real extension.js and toggle.js with stubbed GJS/gnome-shell
 * modules, drives them through the actual enable()/disable() lifecycle plus a
 * set of scenarios, and prints the observed results as JSON for
 * tests/test_syncthing_toggle.py to assert on.
 *
 * Usage: node driver.mjs /path/to/toggle.js
 */

import nodeModule from 'node:module'
import { pathToFileURL } from 'node:url'
import { resolve } from './loader.mjs'

// registerHooks() is the current API; register() is the fallback for older
// node releases, where the named export does not exist at all.
if (typeof nodeModule.registerHooks === 'function')
	nodeModule.registerHooks({ resolve })
else
	nodeModule.register('./loader.mjs', import.meta.url)

const { harness } = await import('./harness.mjs')
const { networkMonitor } = await import('./stubs/gio.mjs')

const togglePath = process.argv[2]
if (!togglePath) {
	console.error('usage: node driver.mjs <path to toggle.js>')
	process.exit(2)
}

const toggleUrl = pathToFileURL(togglePath)
const { default: SyncthingToggleExtension } = await import(
	new URL('./extension.js', toggleUrl).href
)

const RUNNING = 'Loaded: loaded\n   Active: active (running) since Mon'
const STOPPED = 'Loaded: loaded\n   Active: inactive (dead)'

/* Let queued subprocess callbacks and their promise chains settle. */
async function flush(turns = 40) {
	for (let i = 0; i < turns; i++)
		await new Promise(resolve => setTimeout(resolve, 0))
}

/* Enable the extension exactly the way GNOME Shell does. */
function enable(statusStdout) {
	harness.reset()
	harness.statusStdout = statusStdout
	networkMonitor.metered = false
	networkMonitor.handlers.clear()
	const extension = new SyncthingToggleExtension()
	extension.enable()
	return extension
}

function timerEntry() {
	const [id] = [...harness.sources.keys()]
	// An extension with no poll timer must produce a failing report, not a
	// harness crash.
	if (id === undefined)
		return { id: null, seconds: 0, callback: () => null }
	return { id, ...harness.sources.get(id) }
}

function statusCallCount() {
	return harness.systemctlVerbs().filter(verb => verb === 'status').length
}

const results = {}

/* --- enable(): one initial refresh, reflecting state nobody clicked ------- */
{
	// The unit is already running before the extension ever loads.
	const extension = enable(RUNNING)
	const indicator = extension._indicator
	await flush()

	const timer = timerEntry()
	results.enable = {
		// Two initial refreshes mean two `systemctl status` processes per enable.
		initialStatusCalls: statusCallCount(),
		spawnedAtEnable: harness.systemctlArgv(),
		installedSources: harness.sources.size,
		pollSeconds: timer.seconds,
		toggleState: {
			checked: indicator._toggle.checked,
			subtitle: indicator._toggle.subtitle,
			indicatorVisible: indicator._indicator.visible,
		},
	}

	// The unit dies behind the extension's back; the next tick must notice.
	harness.statusStdout = STOPPED
	harness.subprocesses = []
	const tickReturn = timer.callback()
	await flush()
	results.tick = {
		returnValue: tickReturn,
		statusCalls: statusCallCount(),
		checked: indicator._toggle.checked,
		subtitle: indicator._toggle.subtitle,
		indicatorVisible: indicator._indicator.visible,
	}

	/* --- disable(): teardown ---------------------------------------------- */
	const cancellable = indicator._cancellable
	const staleCallback = timer.callback
	const toggle = indicator._toggle
	extension.disable()

	harness.subprocesses = []
	const staleReturn = staleCallback()
	toggle.emit('clicked')
	await flush()

	// disable() already destroys the items and the indicator; a further
	// destroy() must stay a no-op.
	let secondDestroyError = null
	try {
		indicator.destroy()
	} catch (e) {
		secondDestroyError = String(e.message ?? e)
	}

	results.disable = {
		liveSources: harness.sources.size,
		networkMonitorHandlers: networkMonitor.handlers.size,
		toggleHandlers: toggle.handlerCount(),
		cancellableCancelled: Boolean(cancellable && cancellable.cancelled),
		superDestroyCount: indicator.superDestroyCount,
		staleTimerReturn: staleReturn,
		// Neither a stale tick nor a late click may spawn anything.
		subprocessesAfterDisable: harness.systemctlArgv(),
		secondDestroyError,
	}
}

/* --- disable cancels in-flight work before it can touch dead widgets ------ */
{
	const extension = enable(RUNNING)
	const indicator = extension._indicator
	const subtitleBefore = indicator._toggle.subtitle
	// enable()'s status call is still in flight here.
	const inFlight = harness.subprocesses.length
	const liveCancellable = indicator._cancellable
	extension.disable()
	await flush()

	results.cancellation = {
		inFlightAtDisable: inFlight,
		cancellablePassedToSubprocess:
			harness.subprocesses.length > 0 &&
			harness.subprocesses.every(call => Boolean(call.cancellable)),
		// A cancellable only abandons the wait. The child is terminated only if
		// cancellation is wired to force_exit().
		everySubprocessForcedExit:
			harness.subprocesses.length > 0 &&
			harness.subprocesses.every(call => call.forcedExit === true),
		// Handlers must be released per call, or a long-lived cancellable
		// accumulates one for every status poll.
		cancellableHandlersLeft: liveCancellable ? liveCancellable.handlerCount : -1,
		subtitleBefore,
		subtitleAfter: indicator._toggle.subtitle,
		indicatorVisible: indicator._indicator.visible,
	}
}

/* --- metered connection blocks the start before any systemctl call -------- */
{
	const extension = enable(STOPPED)
	const indicator = extension._indicator
	await flush()

	networkMonitor.metered = true
	harness.subprocesses = []
	harness.notifications = []
	indicator._toggle.checked = true
	indicator._toggle.emit('clicked')
	await flush()

	results.meteredClick = {
		argv: harness.systemctlArgv(),
		checked: indicator._toggle.checked,
		notifications: harness.notifications,
	}
	extension.disable()
}

/* --- unmetered click starts the service, cancellable-wired ---------------- */
{
	const extension = enable(STOPPED)
	const indicator = extension._indicator
	await flush()

	harness.subprocesses = []
	harness.notifications = []
	indicator._toggle.checked = true
	harness.statusStdout = RUNNING
	indicator._toggle.emit('clicked')
	await flush()

	results.unmeteredClick = {
		argv: harness.systemctlArgv(),
		// With the schema's default (start-stop-only false) a click must also
		// persist the choice, and `enable` has to follow the start.
		verbs: harness.systemctlVerbs(),
		allCallsCancellable:
			harness.subprocesses.length > 0 &&
			harness.subprocesses.every(
				call => Boolean(call.cancellable) && call.cancellable === indicator._cancellable
			),
		checked: indicator._toggle.checked,
		notifications: harness.notifications,
		// dakota owns folder provisioning; the extension must create nothing.
		directoriesCreated: harness.directoriesCreated,
		filesWritten: harness.filesWritten,
		filesTouched: harness.filesTouched,
	}

	// ...and turning it back off stops it.
	harness.subprocesses = []
	indicator._toggle.checked = false
	harness.statusStdout = STOPPED
	indicator._toggle.emit('clicked')
	await flush()
	results.unmeteredClick.offArgv = harness.systemctlArgv()
	results.unmeteredClick.offVerbs = harness.systemctlVerbs()
	extension.disable()
}

/* --- going metered while running stops the service ------------------------ */
{
	const extension = enable(RUNNING)
	await flush()

	harness.subprocesses = []
	harness.notifications = []
	networkMonitor.emit('notify::network-metered')
	await flush()
	results.meteredSignal = {
		whileUnmetered: harness.systemctlVerbs(),
	}

	networkMonitor.metered = true
	harness.subprocesses = []
	networkMonitor.emit('notify::network-metered')
	await flush()
	results.meteredSignal.whenMetered = harness.systemctlVerbs()
	results.meteredSignal.notifications = harness.notifications

	// After disable the handler is gone entirely.
	extension.disable()
	harness.subprocesses = []
	networkMonitor.emit('notify::network-metered')
	await flush()
	results.meteredSignal.afterDisable = harness.systemctlVerbs()
}

/* --- start-stop-only keeps the choice out of the unit's enablement -------- */
{
	const extension = enable(STOPPED)
	const indicator = extension._indicator
	await flush()

	harness.boolSettings['start-stop-only'] = true
	harness.subprocesses = []
	indicator._toggle.checked = true
	harness.statusStdout = RUNNING
	indicator._toggle.emit('clicked')
	await flush()

	results.startStopOnly = { verbs: harness.systemctlVerbs() }
	extension.disable()
}

/* --- a start that fails must not leave the toggle claiming it worked ------ */
{
	const extension = enable(STOPPED)
	const indicator = extension._indicator
	await flush()

	harness.subprocesses = []
	harness.notifications = []
	harness.errors = []
	harness.failSubprocess = true
	indicator._toggle.checked = true
	// The unit never came up, so status keeps reporting it stopped.
	harness.statusStdout = STOPPED
	indicator._toggle.emit('clicked')
	await flush()

	results.failedStart = {
		checked: indicator._toggle.checked,
		subtitle: indicator._toggle.subtitle,
		indicatorVisible: indicator._indicator.visible,
		verbs: harness.systemctlVerbs(),
		notifications: harness.notifications,
		errors: harness.errors,
	}
	harness.failSubprocess = false
	extension.disable()
}

/* --- a metered pause that cannot stop the unit must not claim it paused --- */
{
	const extension = enable(RUNNING)
	await flush()

	harness.subprocesses = []
	harness.notifications = []
	harness.errors = []
	harness.failSubprocess = true
	networkMonitor.metered = true
	networkMonitor.emit('notify::network-metered')
	await flush()

	results.failedMeteredStop = {
		verbs: harness.systemctlVerbs(),
		notifications: harness.notifications,
		errors: harness.errors,
	}
	harness.failSubprocess = false
	networkMonitor.metered = false
	extension.disable()
}

/* --- logging in on an already-metered connection with the unit running ---- */
{
	// NetworkMonitor emits notify::network-metered only on a *change*, so a
	// session that starts metered never gets one: enable() has to reconcile or
	// a unit left enabled by the previous session keeps syncing on mobile data.
	harness.reset()
	harness.statusStdout = RUNNING
	networkMonitor.metered = true
	networkMonitor.handlers.clear()
	const extension = new SyncthingToggleExtension()
	extension.enable()
	await flush()

	results.meteredAtLogin = {
		verbs: harness.systemctlVerbs(),
		notifications: harness.notifications,
	}
	extension.disable()
	networkMonitor.metered = false
}

/* --- an invalid service name never reaches systemctl, not even on a tick -- */
{
	const extension = enable(RUNNING)
	harness.settings['service-name'] = '--global.service'
	const indicator = extension._indicator
	harness.subprocesses = []
	await flush()

	timerEntry().callback()
	indicator._toggle.checked = true
	indicator._toggle.emit('clicked')
	await flush()

	results.invalidServiceName = {
		argv: harness.systemctlArgv(),
		subtitle: indicator._toggle.subtitle,
		indicatorVisible: indicator._indicator.visible,
	}
	extension.disable()
}

/* --- the menu's Web GUI entry opens the GUI Syncthing actually listens on -- */
{
	const extension = enable(STOPPED)
	const indicator = extension._indicator
	await flush()

	// Walk the menu the way the shell renders it: sections hold the actions.
	const actions = []
	const collect = node => {
		for (const item of node.items ?? []) {
			if (typeof item.callback === 'function')
				actions.push(item)
			else
				collect(item)
		}
	}
	collect(indicator._toggle.menu)

	// Identify the entry by what it does — launch a URI — not by its label.
	harness.subprocesses = []
	const launchers = []
	for (const item of actions) {
		harness.urisLaunched = []
		item.callback()
		if (harness.urisLaunched.length)
			launchers.push({ label: item.label, item, uri: harness.urisLaunched[0] })
	}

	// The port is configurable; the entry must follow the setting rather than
	// a baked-in number, or it opens a dead page on a relocated GUI.
	harness.intSettings.port = 9999
	for (const launcher of launchers) {
		harness.urisLaunched = []
		launcher.item.callback()
		launcher.relocatedUri = harness.urisLaunched[0] ?? null
	}

	results.webGui = {
		labels: actions.map(item => item.label),
		launched: launchers.map(({ label, uri, relocatedUri }) => ({
			label,
			uri,
			relocatedUri,
		})),
		argv: harness.systemctlArgv(),
		filesTouched: harness.filesTouched,
		directoriesCreated: harness.directoriesCreated,
		errors: harness.errors,
	}
	extension.disable()
}

process.stdout.write(JSON.stringify(results, null, 2))
