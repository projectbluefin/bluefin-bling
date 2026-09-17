/* Module resolve hook: map GJS/gnome-shell specifiers onto the stubs.
 *
 * This lets the tests import the real extensions/syncthing-toggle/toggle.js
 * byte-for-byte instead of rewriting or re-implementing it.
 */

const STUBS = {
	'gi://Gio': './stubs/gio.mjs',
	'gi://GLib': './stubs/glib.mjs',
	'gi://GObject': './stubs/gobject.mjs',
	'resource:///org/gnome/shell/ui/popupMenu.js': './stubs/shell.mjs',
	'resource:///org/gnome/shell/ui/quickSettings.js': './stubs/shell.mjs',
	'resource:///org/gnome/shell/ui/main.js': './stubs/shell.mjs',
	'resource:///org/gnome/shell/extensions/extension.js': './stubs/shell.mjs',
}

// Sync so it satisfies both module.register() and module.registerHooks().
export function resolve(specifier, context, nextResolve) {
	const stub = STUBS[specifier]
	if (stub)
		return { shortCircuit: true, url: new URL(stub, import.meta.url).href }
	if (specifier.startsWith('gi://') || specifier.startsWith('resource:///'))
		throw new Error(`no stub registered for ${specifier}`)
	return nextResolve(specifier, context)
}
