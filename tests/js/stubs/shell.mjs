/* Stub of the three `resource:///org/gnome/shell/...` modules toggle.js imports.
 *
 * One module serves all three: each import site either destructures the names
 * it needs or takes the namespace (`import * as Main`), so a single flat set of
 * exports satisfies popupMenu.js, quickSettings.js and main.js alike.
 */

import { harness } from '../harness.mjs'

class SignalEmitter {
	constructor() {
		this._handlers = new Map()
		this._nextHandlerId = 1
	}

	connect(signal, callback) {
		const id = this._nextHandlerId++
		this._handlers.set(id, { signal, callback })
		return id
	}

	disconnect(id) {
		if (!this._handlers.has(id))
			throw new Error(`disconnect(): no handler ${id}`)
		this._handlers.delete(id)
	}

	emit(signal, ...args) {
		for (const handler of [...this._handlers.values()]) {
			if (handler.signal === signal)
				handler.callback(this, ...args)
		}
	}

	handlerCount() {
		return this._handlers.size
	}
}

export class PopupMenuSection {
	constructor() {
		this.items = []
	}

	addAction(label, callback) {
		const item = { label, callback, visible: true, sensitive: true, setSensitive(v) { this.sensitive = v } }
		this.items.push(item)
		return item
	}
}

export class PopupSeparatorMenuItem {}

class Menu extends PopupMenuSection {
	constructor() {
		super()
		this._settingsActions = {}
		this.header = null
	}

	setHeader(icon, title) {
		this.header = { icon, title }
	}

	addMenuItem(item) {
		this.items.push(item)
	}
}

export class QuickMenuToggle extends SignalEmitter {
	constructor(props = {}) {
		super()
		Object.assign(this, props)
		this.menu = new Menu()
		this.destroyed = false
	}

	set(props) {
		Object.assign(this, props)
	}

	destroy() {
		this.destroyed = true
	}
}

export class SystemIndicator extends SignalEmitter {
	constructor() {
		super()
		this.quickSettingsItems = []
		this.indicators = []
		this.superDestroyCount = 0
	}

	_addIndicator() {
		const indicator = { visible: false, gicon: null }
		this.indicators.push(indicator)
		return indicator
	}

	destroy() {
		this.superDestroyCount++
	}
}

export const sessionMode = { allowSettings: true }

export const panel = {
	statusArea: { quickSettings: { addExternalIndicator() {} } },
}

/* Stand-in for the Extension base class extension.js subclasses, so the tests
 * can drive the real enable()/disable() entry points. */
export class Extension {
	constructor() {
		this.path = '/opt/syncthing-toggle'
		this.uuid = 'syncthing-toggle@test'
	}

	getSettings() {
		return {
			get_string: key => harness.settings[key] ?? '',
			get_int: key => harness.intSettings[key] ?? 0,
			get_boolean: key => harness.boolSettings[key] ?? false,
		}
	}

	openPreferences() {}
}

export function notify(title, body) {
	harness.notifications.push({ title, body })
}

export const gettext = s => s

export { SignalEmitter }
