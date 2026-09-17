/* Stub of `gi://Gio`.
 *
 * Subprocess callbacks are delivered on a later turn of the event loop, like
 * the real async calls, and honour the cancellable they were given.
 */

import { harness } from '../harness.mjs'

class Cancellable {
	constructor() {
		this.cancelled = false
		this._handlers = new Map()
		this._nextId = 1
	}

	connect(callback) {
		const id = this._nextId++
		this._handlers.set(id, callback)
		return id
	}

	disconnect(id) {
		this._handlers.delete(id)
	}

	get handlerCount() {
		return this._handlers.size
	}

	cancel() {
		this.cancelled = true
		// Real Gio invokes connected handlers on cancellation. That is the only
		// hook that can terminate an already-spawned child, so the stub must
		// fire them rather than merely flipping a flag.
		for (const callback of [...this._handlers.values()]) callback()
	}

	is_cancelled() {
		return this.cancelled
	}
}

class Subprocess {
	constructor(argv, flags) {
		this.argv = argv
		this.flags = flags
	}

	static new(argv, flags) {
		const proc = new Subprocess(argv, flags)
		harness.subprocesses.push({ argv, flags, cancellable: undefined, forcedExit: false })
		proc._record = harness.subprocesses[harness.subprocesses.length - 1]
		return proc
	}

	force_exit() {
		// Records that the child was actually terminated, not merely that the
		// wait was abandoned.
		this._record.forcedExit = true
	}

	wait_check_async(cancellable, callback) {
		this._record.cancellable = cancellable
		setTimeout(() => callback(this, { cancellable }), 0)
	}

	wait_check_finish(res) {
		if (res.cancellable && res.cancellable.is_cancelled())
			throw new Error('Operation was cancelled')
		if (harness.failSubprocess)
			throw new Error('Unit failed')
		return true
	}

	communicate_utf8_async(stdinBuf, cancellable, callback) {
		this._record.cancellable = cancellable
		setTimeout(() => callback(this, { cancellable }), 0)
	}

	communicate_utf8_finish(res) {
		if (res.cancellable && res.cancellable.is_cancelled())
			throw new Error('Operation was cancelled')
		return [true, harness.statusStdout, '']
	}
}

class NetworkMonitor {
	constructor() {
		this.metered = false
		this.handlers = new Map()
		this.nextHandlerId = 1
	}

	get_network_metered() {
		return this.metered
	}

	connect(signal, callback) {
		const id = this.nextHandlerId++
		this.handlers.set(id, { signal, callback })
		return id
	}

	disconnect(id) {
		if (!this.handlers.has(id))
			throw new Error(`NetworkMonitor.disconnect(): no handler ${id}`)
		this.handlers.delete(id)
	}

	emit(signal) {
		for (const handler of this.handlers.values()) {
			if (handler.signal === signal)
				handler.callback()
		}
	}
}

const networkMonitor = new NetworkMonitor()

class File {
	constructor(path) {
		this.path = path
	}

	static new_for_path(path) {
		harness.filesTouched.push(path)
		return new File(path)
	}

	query_exists() {
		return false
	}

	make_directory_with_parents() {
		harness.directoriesCreated.push(this.path)
		return true
	}

	load_contents() {
		throw new Error('no such file')
	}

	replace_contents() {
		harness.filesWritten.push(this.path)
		return true
	}
}

const Gio = {
	Cancellable,
	Subprocess,
	SubprocessFlags: { NONE: 0, STDOUT_PIPE: 1 << 0, STDERR_PIPE: 1 << 1 },
	File,
	FileCreateFlags: { NONE: 0, REPLACE_DESTINATION: 1 },
	NetworkMonitor: {
		get_default: () => networkMonitor,
	},
	icon_new_for_string: name => ({ name }),
	app_info_launch_default_for_uri: uri => {
		harness.urisLaunched.push(uri)
		return true
	},
}

export default Gio
export { networkMonitor }
