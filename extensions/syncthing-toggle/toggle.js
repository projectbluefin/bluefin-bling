import Gio from 'gi://Gio'
import GLib from 'gi://GLib'
import GObject from 'gi://GObject'
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js'
import {
	QuickMenuToggle,
	SystemIndicator,
} from 'resource:///org/gnome/shell/ui/quickSettings.js'
import * as Main from 'resource:///org/gnome/shell/ui/main.js'
import { gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js'

// `systemctl --user is-active` answers straight from the manager and prints one
// word. `status` additionally pages in the journal, which costs roughly 27ms a
// call — unaffordable for a background poll in a feature whose whole point is
// saving battery and data.
const ACTIVE_STATE = 'active'

// The background poll only has to notice state that changed behind our back: a
// manual `systemctl`, a crash, a resume. Every path the user can actually see —
// clicking the toggle, an automatic metered transition, enable() — refreshes
// immediately, so this can be slow.
const POLL_INTERVAL_SECONDS = 60

const XML_ENTITIES = {
	'&': '&amp;',
	'<': '&lt;',
	'>': '&gt;',
	'"': '&quot;',
	"'": '&apos;',
}

// $HOME is whatever the account says it is. An unescaped &, " or < in the path
// produces a config.xml that Syncthing refuses to parse.
function escapeXmlAttribute(value) {
	return String(value).replace(/[&<>"']/g, character => XML_ENTITIES[character])
}

// A cancellable aborts the local stream read only; the child process keeps
// running unless it is killed. See docs/skills/gnome-shell-extension-dev.md.
function connectForceExit(proc, cancellable) {
	if (!cancellable)
		return () => {}

	const handlerId = cancellable.connect(() => {
		try {
			proc.force_exit()
		} catch {}
	})
	return () => cancellable.disconnect(handlerId)
}

function waitCheckAsync(argv, cancellable) {
	return new Promise((resolve, reject) => {
		const proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
		const release = connectForceExit(proc, cancellable)
		proc.wait_check_async(cancellable, (source, res) => {
			release()
			try {
				source.wait_check_finish(res)
				resolve()
			} catch (e) {
				reject(e)
			}
		})
	})
}

function communicateUtf8Async(argv, cancellable) {
	return new Promise((resolve, reject) => {
		const proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE)
		const release = connectForceExit(proc, cancellable)
		proc.communicate_utf8_async(null, cancellable, (source, res) => {
			release()
			try {
				const [, stdout] = source.communicate_utf8_finish(res)
				resolve(stdout)
			} catch (e) {
				reject(e)
			}
		})
	})
}

// query_exists(null) blocks the compositor on NFS, autofs or a spun-down disk.
function queryExistsAsync(file, cancellable) {
	return new Promise(resolve => {
		file.query_info_async(
			'standard::type',
			Gio.FileQueryInfoFlags.NONE,
			GLib.PRIORITY_DEFAULT,
			cancellable,
			(source, res) => {
				try {
					source.query_info_finish(res)
					resolve(true)
				} catch {
					resolve(false)
				}
			}
		)
	})
}

// GIO ships no make_directory_with_parents_async, and the synchronous form
// blocks. Walk up to the first ancestor that exists, then create downwards.
async function makeDirectoryWithParentsAsync(file, cancellable) {
	const missing = []
	for (let dir = file; dir; dir = dir.get_parent()) {
		if (await queryExistsAsync(dir, cancellable))
			break
		missing.unshift(dir)
	}

	for (const dir of missing) {
		await new Promise((resolve, reject) => {
			dir.make_directory_async(GLib.PRIORITY_DEFAULT, cancellable, (source, res) => {
				try {
					source.make_directory_finish(res)
					resolve()
				} catch (e) {
					// Another writer won the race; the directory is there.
					if (e?.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.EXISTS))
						resolve()
					else
						reject(e)
				}
			})
		})
	}
}

function loadContentsAsync(file, cancellable) {
	return new Promise((resolve, reject) => {
		file.load_contents_async(cancellable, (source, res) => {
			try {
				const [, contents] = source.load_contents_finish(res)
				resolve(new TextDecoder('utf-8').decode(contents))
			} catch (e) {
				reject(e)
			}
		})
	})
}

function replaceContentsAsync(file, text, cancellable) {
	return new Promise((resolve, reject) => {
		// replace_contents_bytes_async, not replace_contents_async: GJS cannot
		// keep a plain byte array alive across the call.
		//
		// PRIVATE, never REPLACE_DESTINATION. config.xml carries <apikey>,
		// which is full control of the local REST API; REPLACE_DESTINATION
		// creates a fresh inode, so a 0600 file comes back 0644 under the
		// default umask and a symlinked config is replaced rather than followed.
		file.replace_contents_bytes_async(
			GLib.Bytes.new(new TextEncoder().encode(text)),
			null,
			false,
			Gio.FileCreateFlags.PRIVATE,
			cancellable,
			(source, res) => {
				try {
					source.replace_contents_finish(res)
					resolve()
				} catch (e) {
					reject(e)
				}
			}
		)
	})
}

const ServiceToggle = GObject.registerClass(
	class ServiceToggle extends QuickMenuToggle {
		constructor(extensionObject, syncthingIcon) {
			super({
				title: _('Sync Folder'),
				gicon: syncthingIcon,
				toggleMode: true,
				subtitle: 'Loading',
			})
			// Add a header with an icon, title and optional subtitle. This is
			// recommended for consistency with other quick settings menus.
			this.menu.setHeader(syncthingIcon, _('Sync Folder'))

			this._settings = extensionObject.getSettings()

			// Add a section of items to the menu
			this._itemsSection = new PopupMenu.PopupMenuSection()
			this.webGuiItem = this._itemsSection.addAction(_('Open Web GUI'), () => {
				// 127.0.0.1, not localhost: the seeded config binds the GUI to
				// the IPv4 loopback, and localhost can resolve to ::1 first.
				const webGuiUrl = 'http://127.0.0.1:' + this._settings.get_int('port')
				try {
					Gio.app_info_launch_default_for_uri(webGuiUrl, null)
				} catch (e) {
					logError(e, 'Failed to open URL')
				}
			})

			this.menu.addMenuItem(this._itemsSection)

			// Add an entry-point for more settings
			this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem())
			const settingsItem = this.menu.addAction('Extension Settings', () =>
				extensionObject.openPreferences()
			)

			// Ensure the settings are unavailable when the screen is locked
			settingsItem.visible = Main.sessionMode.allowSettings
			this.menu._settingsActions[extensionObject.uuid] = settingsItem
		}
	}
)

export var ServiceIndicator = GObject.registerClass(
	class ServiceIndicator extends SystemIndicator {
		constructor(extensionObject) {
			super()
			this._extensionObject = extensionObject
			this._settings = extensionObject.getSettings()
			this._destroyed = false
			this._timer = null
			this._networkMonitor = null
			this._meteredSignalId = 0
			// True only while the metered handler is holding sharing off. It is
			// the permission to resume: a network change must never restart
			// sharing the user turned off by hand.
			this._pausedForMetered = false
			this._cancellable = new Gio.Cancellable()

			let iconName =
				this._settings.get_string('icon-name').trim() ||
				extensionObject.path + '/icons/syncthing-symbolic.svg'
			const syncthingIcon = Gio.icon_new_for_string(iconName)

			this._indicator = this._addIndicator()
			this._indicator.gicon = syncthingIcon
			this._toggle = new ServiceToggle(extensionObject, syncthingIcon)
			this.quickSettingsItems.push(this._toggle)

			this._toggle.connect('clicked', async () => {
				const isEnabled = this._toggle.checked

				const serviceName = this._validatedServiceName()
				if (!serviceName)
					return

				// An explicit click overrides whatever the metered handler
				// decided, in both directions.
				this._pausedForMetered = false

				if (isEnabled) {
					// Seed initial configuration and ensure ~/Sync exists.
					await this._ensureSyncFolderConfig()
					if (this._destroyed)
						return

					if (this._isNetworkMetered()) {
						Main.notify(
							_('Sync Folder Sharing Paused'),
							_('Metered network connection detected. Syncing is paused.')
						)
						// Remember the request so returning to an unmetered
						// network honours it instead of dropping it silently.
						this._pausedForMetered = true
						this._toggle.checked = false
						return
					}
				}

				const title = isEnabled
					? _('Sync Folder Sharing Enabled')
					: _('Sync Folder Sharing Disabled')
				const body = isEnabled
					? _('Your files are sharing with your other devices.')
					: _('File sharing is paused.')

				Main.notify(title, body)

				await this._applySharing(isEnabled, serviceName)
			})

			this._timer = GLib.timeout_add_seconds(
				GLib.PRIORITY_LOW,
				POLL_INTERVAL_SECONDS,
				() => {
					if (this._destroyed) {
						this._timer = null
						return GLib.SOURCE_REMOVE
					}
					this.checkStatus()
					return GLib.SOURCE_CONTINUE
				}
			)

			// Listen for network metering changes
			try {
				this._networkMonitor = Gio.NetworkMonitor.get_default()
				if (this._networkMonitor) {
					this._meteredSignalId = this._networkMonitor.connect(
						'notify::network-metered',
						() => this._onNetworkMeteredChanged()
					)
				}
			} catch (e) {
				logError(e, 'Error connecting network monitor')
			}
		}

		_isNetworkMetered() {
			try {
				return this._networkMonitor ? this._networkMonitor.get_network_metered() : false
			} catch (e) {
				return false
			}
		}

		// The one place that decides which systemctl verbs a sharing change
		// runs. The metered handler goes through it too, so an automatic pause
		// and a manual toggle-off leave the unit in exactly the same state —
		// otherwise a metered pause left the unit enabled and it came back at
		// the next login, on the same metered link.
		async _applySharing(active, serviceName) {
			await this._runSystemctl(active ? 'start' : 'stop', serviceName)
			if (this._destroyed)
				return

			await this.checkStatus()
			if (this._destroyed)
				return

			// if the appropriate setting is enabled (default, also enable or disable the service)
			// not using enable --now because it's way slower and bugs the status.
			if (!this._settings.get_boolean('start-stop-only'))
				await this._runSystemctl(active ? 'enable' : 'disable', serviceName)
		}

		async _onNetworkMeteredChanged() {
			if (this._destroyed)
				return

			const isMetered = this._isNetworkMetered()
			if (isMetered) {
				// Nothing to pause, or an earlier transition already paused it.
				if (!this._toggle.checked || this._pausedForMetered)
					return
			} else if (!this._pausedForMetered) {
				// Sharing is off because the user said so. Leave it off.
				return
			}

			const serviceName = this._validatedServiceName()
			if (!serviceName)
				return

			Main.notify(
				isMetered
					? _('Sync Folder Sharing Paused')
					: _('Sync Folder Sharing Resumed'),
				isMetered
					? _('Metered connection detected. Pausing file sharing to save data.')
					: _('Unmetered connection detected. Resuming file sharing.')
			)

			this._pausedForMetered = isMetered
			await this._applySharing(!isMetered, serviceName)
		}

		async _ensureSyncFolderConfig() {
			const homeDir = GLib.get_home_dir()
			const syncDir = `${homeDir}/Sync`
			const stateDir = `${homeDir}/.local/state/syncthing`
			const port = this._settings.get_int('port') || 8384

			try {
				await makeDirectoryWithParentsAsync(
					Gio.File.new_for_path(syncDir),
					this._cancellable
				)
			} catch (e) {
				logError(e, 'Failed to create sync directory')
			}
			if (this._destroyed)
				return

			const configFile = Gio.File.new_for_path(`${stateDir}/config.xml`)
			const alreadyConfigured = await queryExistsAsync(configFile, this._cancellable)
			if (this._destroyed || alreadyConfigured)
				return

			try {
				await makeDirectoryWithParentsAsync(
					Gio.File.new_for_path(stateDir),
					this._cancellable
				)
			} catch (e) {
				logError(e, 'Failed to create state directory')
				return
			}
			if (this._destroyed)
				return

			// Plain 'syncthing': Gio.Subprocess resolves it from PATH inside the
			// child, where GLib.find_program_in_path() would stat every PATH
			// entry on the compositor thread.
			try {
				await waitCheckAsync(
					['syncthing', 'generate', `--home=${stateDir}`, '--no-port-probing'],
					this._cancellable
				)
			} catch (e) {
				logError(e, 'Failed to generate initial syncthing config')
				return
			}
			if (this._destroyed)
				return

			const generated = await queryExistsAsync(configFile, this._cancellable)
			if (this._destroyed || !generated)
				return

			try {
				let xml = await loadContentsAsync(configFile, this._cancellable)
				if (this._destroyed)
					return

				// Bind the GUI to loopback on the configured port.
				xml = xml.replace(
					/<address>127\.0\.0\.1:\d+<\/address>/,
					`<address>127.0.0.1:${port}</address>`
				)

				// Seed the default ~/Sync folder. The <defaults> device
				// template is deliberately left alone: flipping auto-accept
				// there hands every device paired later blanket authority to
				// create folders under $HOME with no prompt, and it outlives
				// the extension in config.xml. The seeded folder below is what
				// the feature actually needs.
				if (!xml.includes('id="sync"')) {
					// Extract local device ID
					const devMatch = xml.match(/<device id="([^"]+)"/)
					const myDevId = devMatch ? devMatch[1] : ''
					const folderXml = `    <folder id="sync" label="Sync Folder" path="${escapeXmlAttribute(syncDir)}" type="sendreceive" rescanIntervalS="3600" fsWatcherEnabled="true" fsWatcherDelayS="10" ignorePerms="false" autoNormalize="true">
        <filesystemType>basic</filesystemType>
        <device id="${myDevId}" introducedBy=""></device>
        <minDiskFree unit="%">1</minDiskFree>
        <versioning>
            <cleanupIntervalS>3600</cleanupIntervalS>
        </versioning>
        <markerName>.stfolder</markerName>
    </folder>\n</configuration>`
					xml = xml.replace('</configuration>', folderXml)
				}

				await replaceContentsAsync(configFile, xml, this._cancellable)
			} catch (e) {
				logError(e, 'Failed to configure syncthing config')
			}
		}

		// The service-name setting is free text (dconf-writable by any
		// same-uid process). Never interpolate it into a command line:
		// validate unit-name syntax and pass it as a discrete argv entry.
		_validatedServiceName() {
			const name = this._settings.get_string('service-name')
			// Leading character excludes '-' so the value can never be parsed as
			// a systemctl option (e.g. '--system.service'); matches systemd's
			// unit-name rules.
			if (/^[a-zA-Z0-9_][a-zA-Z0-9_.:@-]*\.service$/.test(name))
				return name
			console.error(`[SyncthingToggle] Rejecting invalid service-name: ${name}`)
			return null
		}

		async _runSystemctl(verb, serviceName) {
			try {
				await waitCheckAsync(
					['systemctl', '--user', verb, serviceName],
					this._cancellable
				)
			} catch (e) {
				logError(e, `Failed to run systemctl ${verb}`)
			}
		}

		async checkStatus() {
			if (this._destroyed)
				return

			try {
				const serviceName = this._validatedServiceName()
				if (!serviceName) {
					this.updateStatus(false)
					return
				}

				const stdout = await communicateUtf8Async(
					['systemctl', '--user', 'is-active', serviceName],
					this._cancellable
				)
				// Cancelling does not drop this callback — it fires with
				// G_IO_ERROR_CANCELLED, by which point disable() has already
				// finalized the St widgets updateStatus() writes to.
				if (this._destroyed)
					return

				this.updateStatus(stdout?.trim() === ACTIVE_STATE)
			} catch (err) {
				if (this._destroyed)
					return
				this.updateStatus(false)
				logError(err, 'Err checking status')
			}
		}

		updateStatus(isActive) {
			this._indicator.visible = isActive
			let status = isActive ? 'Running' : 'Stopped'
			this._toggle.set({ checked: isActive, subtitle: status })
			this._toggle.webGuiItem.setSensitive(isActive)
		}

		destroy() {
			this._destroyed = true
			if (this._cancellable) {
				this._cancellable.cancel()
				this._cancellable = null
			}
			if (this._timer) {
				GLib.Source.remove(this._timer)
				this._timer = null
			}
			if (this._networkMonitor && this._meteredSignalId) {
				this._networkMonitor.disconnect(this._meteredSignalId)
				this._meteredSignalId = 0
				this._networkMonitor = null
			}
			if (super.destroy)
				super.destroy()
		}
	}
)
