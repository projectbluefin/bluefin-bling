/* Shared recorder for the GJS stubs.
 *
 * toggle.js is loaded unmodified under node (see loader.mjs); the stubs below
 * stand in for GJS/gnome-shell and record what the extension actually did, so
 * the Python tests can assert behaviour rather than source text.
 */

export const harness = {
	// GLib main-loop sources that are currently installed.
	sources: new Map(),
	nextSourceId: 1,
	// Every Gio.Subprocess.new() argv, with the cancellable it was handed.
	subprocesses: [],
	// Main.notify() calls.
	notifications: [],
	// Text `systemctl --user status` reports.
	statusStdout: 'Active: active (running) since now',
	// Whether the stub subprocess should fail.
	failSubprocess: false,
	settings: {
		'service-name': 'syncthing.service',
		'icon-name': '',
	},
	intSettings: { port: 8384 },
	// The shipped schema defaults start-stop-only to false, so the click path
	// runs `enable`/`disable` as well; scenarios that want the other mode set
	// it explicitly.
	boolSettings: { 'start-stop-only': false },
	errors: [],
	// Any Gio.File path the extension looked at, created, or rewrote.
	filesTouched: [],
	directoriesCreated: [],
	filesWritten: [],
	// URIs handed to Gio.app_info_launch_default_for_uri().
	urisLaunched: [],

	reset() {
		this.sources = new Map()
		this.nextSourceId = 1
		this.subprocesses = []
		this.notifications = []
		this.statusStdout = 'Active: active (running) since now'
		this.failSubprocess = false
		this.settings = { 'service-name': 'syncthing.service', 'icon-name': '' }
		this.intSettings = { port: 8384 }
		this.boolSettings = { 'start-stop-only': false }
		this.errors = []
		this.filesTouched = []
		this.directoriesCreated = []
		this.filesWritten = []
		this.urisLaunched = []
	},

	systemctlArgv() {
		return this.subprocesses.map(call => call.argv)
	},

	systemctlVerbs() {
		return this.subprocesses
			.filter(call => call.argv[0] === 'systemctl')
			.map(call => call.argv[2])
	},
}

globalThis.__harness = harness

// GJS provides these as globals.
globalThis.logError = (e, msg) => {
	harness.errors.push(String(msg ?? e))
}
