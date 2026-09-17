/* Stub of `gi://GLib`. Tracks installed main-loop sources. */

import { harness } from '../harness.mjs'

const GLib = {
	PRIORITY_DEFAULT: 0,
	SOURCE_CONTINUE: true,
	SOURCE_REMOVE: false,

	timeout_add_seconds(priority, seconds, callback) {
		const id = harness.nextSourceId++
		harness.sources.set(id, { priority, seconds, callback })
		return id
	},

	Source: {
		remove(id) {
			if (!harness.sources.has(id))
				throw new Error(`GLib.Source.remove(): source ${id} is not installed`)
			harness.sources.delete(id)
			return true
		},
	},

	get_home_dir() {
		return '/home/harness'
	},

	find_program_in_path() {
		return null
	},
}

export default GLib
