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
// call — unaffordable for a poll in a feature whose point is saving battery.
const activeState = 'active'

// The service can be started, stopped or restarted by anything on the system
// (a login-time `systemctl --user enable`, a terminal, another session), so the
// toggle re-reads the real unit state on a timer instead of trusting its own
// last click. enable(), every click and every metered transition refresh
// immediately, so the timer only has to catch what happened behind our back.
const statusPollSeconds = 60

const xmlEntities = {
	'&': '&amp;',
	'<': '&lt;',
	'>': '&gt;',
	'"': '&quot;',
	"'": '&apos;',
}

// $HOME is whatever the account says it is. An unescaped &, " or < in the path
// produces a config.xml that Syncthing refuses to parse.
function escapeXmlAttribute(value) {
	return String(value).replace(/[&<>"']/g, character => xmlEntities[character])
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
		// PRIVATE, never REPLACE_DESTINATION. config.xml carries <apikey>, which
		// is full control of the local REST API; REPLACE_DESTINATION creates a
		// fresh inode, so a 0600 file comes back 0644 under the default umask
		// and a symlinked config is replaced rather than followed.
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
			// Kept on the instance: updateStatus() greys it out while the daemon
			// is down, so the entry cannot open a URL nothing is listening on.
			this.webGuiItem = this._itemsSection.addAction(_('Open Web GUI'), () => {
				// 127.0.0.1, not localhost: Syncthing's GUI binds IPv4
				// loopback only (dakota ships `<address>127.0.0.1:8384`), and
				// `localhost` can resolve to ::1 first, where nothing listens.
				const webGuiUrl = 'http://127.0.0.1:' + this._settings.get_int('port')
				try {
					Gio.app_info_launch_default_for_uri(webGuiUrl, null)
				} catch (e) {
					logError(e, 'Failed to open URL')
				}
			})

			// this.menu.addMenuItem(this._devicesSection)
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
			this._settings = extensionObject.getSettings()
			this._destroyed = false
			this._timer = null
			this._networkMonitor = null
			this._meteredSignalId = 0
			this._clickedSignalId = 0
			// True only while the metered handler is holding sharing off. It is the
			// permission to resume: a network change must never restart sharing the
			// user turned off by hand.
			this._pausedForMetered = false
			// Every subprocess spawned here is tied to this cancellable so that
			// destroy() can abort in-flight calls instead of letting their
			// callbacks fire against torn-down widgets.
			this._cancellable = new Gio.Cancellable()

			let iconName =
				this._settings.get_string('icon-name').trim() ||
				extensionObject.path + '/icons/syncthing-symbolic.svg'
			const syncthingIcon = Gio.icon_new_for_string(iconName)

			this._indicator = this._addIndicator()
			this._indicator.gicon = syncthingIcon
			this._toggle = new ServiceToggle(extensionObject, syncthingIcon)
			this.quickSettingsItems.push(this._toggle)

			this._clickedSignalId = this._toggle.connect('clicked', async () => {
				const isEnabled = this._toggle.checked

				const serviceName = this._validatedServiceName()
				if (!serviceName)
					return

				// An explicit click is the user overriding whatever the metered
				// handler decided, in both directions.
				this._pausedForMetered = false

				if (isEnabled) {
					// Refuse to start syncing over a metered connection, and say
					// so before anything is spawned.
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

					// Give the daemon a folder to sync before it is asked to
					// start. No-op once config.xml exists, so a config shipped
					// through /etc/skel is never touched.
					await this._ensureSyncFolderConfig()
					if (this._destroyed)
						return
				}

				await this._applySharing(
					isEnabled,
					serviceName,
					isEnabled
						? _('Sync Folder Sharing Enabled')
						: _('Sync Folder Sharing Disabled'),
					isEnabled
						? _('Your files are sharing with your other devices.')
						: _('File sharing is paused.')
				)
			})

			this._timer = GLib.timeout_add_seconds(
				GLib.PRIORITY_DEFAULT,
				statusPollSeconds,
				() => {
					if (this._destroyed)
						return GLib.SOURCE_REMOVE
					this.checkStatus()
					return GLib.SOURCE_CONTINUE
				}
			)

			try {
				this._networkMonitor = Gio.NetworkMonitor.get_default()
				if (this._networkMonitor) {
					this._meteredSignalId = this._networkMonitor.connect(
						'notify::network-metered',
						() => this._onNetworkMeteredChanged()
					)
				}
			} catch (e) {
				console.error(`[SyncthingToggle] Error connecting network monitor: ${e.message}`)
			}
			// The initial refresh — the unit may already be running when the
			// session starts — is done once by enable() in extension.js.
		}

		_isNetworkMetered() {
			try {
				return this._networkMonitor ? this._networkMonitor.get_network_metered() : false
			} catch (e) {
				return false
			}
		}

		// NetworkMonitor only signals *changes*, so a session that comes up on
		// a metered connection never hears about it. A unit left enabled by a
		// previous session is already syncing by the time the extension loads;
		// enable() reconciles both facts once, here.
		async reconcile() {
			await this.checkStatus()
			await this._onNetworkMeteredChanged()
		}

		// The one owner of the systemctl verb sequence. The click path and both
		// metered edges go through it, so an automatic pause and a manual
		// toggle-off leave the unit in the same state — a pause that only stopped
		// the unit left it enabled, and syncing came back at the next login on the
		// same metered link.
		//
		// systemctl can refuse (missing unit, masked, failing ExecStart), so the
		// announcement and the `enable`/`disable` that would persist the choice
		// both wait for the call to have succeeded. Resolves true when the unit
		// actually moved.
		async _applySharing(active, serviceName, title, body) {
			const applied = await this._runSystemctl(active ? 'start' : 'stop', serviceName)
			await this.checkStatus()
			// Cancelling the cancellable does not drop a pending async callback,
			// so the awaits above still resolve after destroy(). Never announce
			// anything on behalf of a torn-down extension.
			if (this._destroyed || !applied)
				return false

			Main.notify(title, body)

			// Persist the choice across logins as well, unless the user asked for
			// start/stop only. Not `enable --now`: it is far slower and confuses
			// the status read.
			if (!this._settings.get_boolean('start-stop-only'))
				await this._runSystemctl(active ? 'enable' : 'disable', serviceName)
			return true
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

			this._pausedForMetered = isMetered
			const applied = await this._applySharing(
				!isMetered,
				serviceName,
				isMetered
					? _('Sync Folder Sharing Paused')
					: _('Sync Folder Sharing Resumed'),
				isMetered
					? _('Metered connection detected. Pausing file sharing to save data.')
					: _('Unmetered connection detected. Resuming file sharing.')
			)
			if (this._destroyed)
				return
			// A transition systemctl refused has not happened: leave the flag
			// where it was so the next edge in that direction retries.
			if (!applied)
				this._pausedForMetered = !isMetered
		}

		// Syncthing needs a folder before it has anything to share. This seeds
		// ~/Sync and an initial config.xml, and returns immediately once a config
		// exists — a config provisioned through /etc/skel is never rewritten.
		//
		// Every call here is asynchronous on purpose: the synchronous GIO forms
		// run on the Shell main loop and freeze the whole compositor on a
		// networked or spun-down home directory.
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
			// child, where GLib.find_program_in_path() would stat every PATH entry
			// on the compositor thread.
			const generated = await this._waitCheck(
				['syncthing', 'generate', `--home=${stateDir}`, '--no-port-probing'],
				'syncthing generate'
			)
			if (this._destroyed || !generated)
				return

			const configWritten = await queryExistsAsync(configFile, this._cancellable)
			if (this._destroyed || !configWritten)
				return

			try {
				let xml = await loadContentsAsync(configFile, this._cancellable)
				if (this._destroyed)
					return

				// Bind the GUI to the loopback address the Web GUI item opens.
				xml = xml.replace(
					/<address>127\.0\.0\.1:\d+<\/address>/,
					`<address>127.0.0.1:${port}</address>`
				)

				// Seed the default ~/Sync folder. The <defaults> device template is
				// deliberately left alone: flipping auto-accept there hands every
				// device paired later blanket authority to create folders under
				// $HOME with no prompt, and it outlives the extension in
				// config.xml. The seeded folder is what the feature actually needs.
				if (!xml.includes('id="sync"')) {
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

		// A Cancellable passed to wait_check_async/communicate_utf8_async only
		// abandons the wait; the systemctl child keeps running. Tie cancellation
		// to force_exit so teardown actually terminates it, and drop the handler
		// once the call completes so a long-lived cancellable does not
		// accumulate one per invocation.
		_terminateOnCancel(proc) {
			const cancellable = this._cancellable
			if (!cancellable)
				return null
			// Keep the object, not just the id: destroy() nulls this._cancellable
			// before an in-flight callback settles, and disconnecting through the
			// field would then silently leak the handler.
			return { cancellable, id: cancellable.connect(() => proc.force_exit()) }
		}

		_releaseCancelHandler(handle) {
			if (handle && handle.id)
				handle.cancellable.disconnect(handle.id)
		}

		// Spawn argv and wait for it. Resolves true when the child exited 0,
		// false otherwise: the caller decides whether the user-visible state may
		// change. Shared by the systemctl verbs and the config seeding so the
		// cancellation bookkeeping exists in exactly one place.
		async _waitCheck(argv, what) {
			if (this._destroyed)
				return false
			try {
				const proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
				const cancelHandler = this._terminateOnCancel(proc)
				try {
					await new Promise((resolve, reject) => {
						proc.wait_check_async(this._cancellable, (proc, res) => {
							try {
								proc.wait_check_finish(res)
								resolve()
							} catch (e) {
								reject(e)
							}
						})
					})
				} finally {
					this._releaseCancelHandler(cancelHandler)
				}
				return true
			} catch (e) {
				logError(e, `Failed to run ${what}`)
				return false
			}
		}

		async _runSystemctl(verb, serviceName) {
			return this._waitCheck(
				['systemctl', '--user', verb, serviceName],
				`systemctl ${verb}`
			)
		}

		async checkStatus() {
			if (this._destroyed)
				return

			const serviceName = this._validatedServiceName()
			if (!serviceName) {
				this.updateStatus(false)
				return
			}
			try {
				const proc = Gio.Subprocess.new(
					['systemctl', '--user', 'is-active', serviceName],
					Gio.SubprocessFlags.STDOUT_PIPE
				)

				const cancelHandler = this._terminateOnCancel(proc)
				let stdout
				try {
					stdout = await new Promise((resolve, _reject) => {
						proc.communicate_utf8_async(null, this._cancellable, (proc, res) => {
							try {
								let [, out] = proc.communicate_utf8_finish(res)
								resolve(out)
							} catch {
								// Cancelled at destroy, or the call failed outright.
								resolve(null)
							}
						})
					})
				} finally {
					this._releaseCancelHandler(cancelHandler)
				}

				// is-active prints one word and exits non-zero for anything but
				// 'active', which communicate_utf8 does not treat as an error.
				this.updateStatus(stdout?.trim() === activeState)
			} catch (err) {
				this.updateStatus(false)
				logError(err, 'Err checking status')
			}
		}

		updateStatus(isActive) {
			// A cancelled subprocess callback can still land after teardown;
			// the widgets are gone by then.
			if (this._destroyed)
				return
			this._indicator.visible = isActive
			let status = isActive ? 'Running' : 'Stopped'
			this._toggle.set({ checked: isActive, subtitle: status })
			this._toggle.webGuiItem.setSensitive(isActive)
		}

		destroy() {
			// GNOME Shell tears items down individually before the indicator,
			// so this can be reached twice; teardown runs once.
			if (this._destroyed)
				return
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
			}
			this._networkMonitor = null
			if (this._toggle && this._clickedSignalId) {
				this._toggle.disconnect(this._clickedSignalId)
				this._clickedSignalId = 0
			}

			super.destroy()
		}
	}
)
