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

const statusPattern = /(\(running\))/

// The service can be started, stopped or restarted by anything on the system
// (a login-time `systemctl --user enable`, a terminal, another session), so the
// toggle re-reads the real unit state on a timer instead of trusting its own
// last click.
const statusPollSeconds = 5

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
			this._itemsSection.addAction(_('Open Web GUI'), () => {
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

				// Refuse to start syncing over a metered connection, and say so
				// before anything is spawned.
				if (isEnabled && this._isNetworkMetered()) {
					Main.notify(
						_('Sync Folder Sharing Paused'),
						_('Metered network connection detected. Syncing is paused.')
					)
					this._toggle.checked = false
					return
				}

				// systemctl can refuse (missing unit, masked, failing
				// ExecStart). Announcing the new state before the call means
				// claiming "sharing enabled" for a service that never came up,
				// and persisting it with `enable` would make that stick at the
				// next login.
				const started = await this._runSystemctl(
					isEnabled ? 'start' : 'stop',
					serviceName
				)
				await this.checkStatus()
				// Cancelling the cancellable does not drop a pending async
				// callback, so the awaits above still resolve after destroy().
				// Never announce anything on behalf of a torn-down extension.
				if (this._destroyed || !started)
					return

				Main.notify(
					isEnabled
						? _('Sync Folder Sharing Enabled')
						: _('Sync Folder Sharing Disabled'),
					isEnabled
						? _('Your files are sharing with your other devices.')
						: _('File sharing is paused.')
				)

				// Persist the choice across logins as well, unless the user
				// asked for start/stop only. Not `enable --now`: it is far
				// slower and confuses the status read.
				if (!this._settings.get_boolean('start-stop-only'))
					await this._runSystemctl(isEnabled ? 'enable' : 'disable', serviceName)
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

		async _onNetworkMeteredChanged() {
			if (this._destroyed)
				return

			if (!this._isNetworkMetered() || !this._toggle.checked)
				return

			const serviceName = this._validatedServiceName()
			if (!serviceName)
				return

			// Same rule as the click path: announce the pause only once the
			// unit is actually down. A failed stop that still said "paused"
			// would leave the user believing they are off mobile data while
			// Syncthing keeps replicating over it.
			const stopped = await this._runSystemctl('stop', serviceName)
			await this.checkStatus()
			if (this._destroyed || !stopped)
				return

			Main.notify(
				_('Sync Folder Sharing Paused'),
				_('Metered connection detected. Pausing file sharing to save data.')
			)
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

		// Resolves true when systemctl exited 0, false otherwise: the caller
		// decides whether the user-visible state may change.
		async _runSystemctl(verb, serviceName) {
			if (this._destroyed)
				return false
			try {
				const proc = Gio.Subprocess.new(
					['systemctl', '--user', verb, serviceName],
					Gio.SubprocessFlags.NONE
				)
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
				logError(e, `Failed to run systemctl ${verb}`)
				return false
			}
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
					['systemctl', '--user', 'status', serviceName],
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

				const status = stdout ? statusPattern.exec(stdout)?.[1] : null
				this.updateStatus(status == '(running)')
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
