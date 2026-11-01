import Gio from 'gi://Gio'
import GLib from 'gi://GLib'
import GObject from 'gi://GObject'
import Soup from 'gi://Soup?version=3.0'
import St from 'gi://St'
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js'
import {
	QuickMenuToggle,
	SystemIndicator,
} from 'resource:///org/gnome/shell/ui/quickSettings.js'
import * as Main from 'resource:///org/gnome/shell/ui/main.js'
import { gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js'

import { readSharing, changeSharing } from './sharing.js'

const statusPollSeconds = 60
const readinessSeconds = 30
const commandSeconds = 30
// systemctl waits for the unit job. The first start pulls the image inside that
// job, so these must exceed TimeoutStartSec/TimeoutStopSec in syncthing.container.in.
const unitStartSeconds = 960
const unitStopSeconds = 90

function isObject(value) {
	return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function loadContentsAsync(file, cancellable) {
	return new Promise((resolve, reject) => {
		file.load_contents_async(cancellable, (source, result) => {
			try {
				const [, contents] = source.load_contents_finish(result)
				resolve(new TextDecoder().decode(contents))
			} catch {
				reject(new Error(_('Unable to read the private file sharing credentials.')))
			}
		})
	})
}

const ServiceToggle = GObject.registerClass(
	class ServiceToggle extends QuickMenuToggle {
		constructor(extensionObject, syncthingIcon, openSharingSettings) {
			super({
				title: _('Sync Folder'),
				gicon: syncthingIcon,
				toggleMode: true,
				subtitle: _('Loading'),
			})
			this.menu.setHeader(syncthingIcon, _('Sync Folder'))
			this._settings = extensionObject.getSettings()
			const itemsSection = new PopupMenu.PopupMenuSection()
			this.webGuiItem = itemsSection.addAction(_('Sharing Settings'), openSharingSettings)
			this.webGuiItem.setSensitive(false)
			this.menu.addMenuItem(itemsSection)
			this.sharingSection = new PopupMenu.PopupMenuSection()
			this.menu.addMenuItem(this.sharingSection)
			this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem())
			const settingsItem = this.menu.addAction(_('Extension Settings'), () =>
				extensionObject.openPreferences()
			)
			settingsItem.visible = Main.sessionMode.allowSettings
			this.menu._settingsActions[extensionObject.uuid] = settingsItem
		}
	}
)

export var ServiceIndicator = GObject.registerClass(
	class ServiceIndicator extends SystemIndicator {
		constructor(extensionObject) {
			super()
			this._extensionPath = extensionObject.path
			this._settings = extensionObject.getSettings()
			this._destroyed = false
			this._generation = 0
			this._desiredOn = false
			this._observedActive = false
			this._unitActive = false
			this._pausedForMetered = false
			this._persistOnResume = false
			this._pendingRequest = null
			this._transition = null
			this._operationCancellable = null
			this._pollPromise = null
			this._pollCancellable = null
			this._pendingPromise = null
			this._sharingEpoch = 0
			this._renderedEpoch = -1
			this._sharingBusy = false
			this._shareCancellable = null
			this._pendingScope = null
			this._cancellables = new Set()
			this._timeouts = new Set()
			this._announcedPending = new Set()
			this._credentials = null
			this._session = new Soup.Session({ proxy_resolver: null })
			this._networkMonitor = null
			this._meteredSignalId = 0

			const iconName = this._settings.get_string('icon-name').trim() ||
				extensionObject.path + '/icons/syncthing-symbolic.svg'
			this._toggle = new ServiceToggle(extensionObject, Gio.icon_new_for_string(iconName),
				() => this._openSharingSettings())
			this.quickSettingsItems.push(this._toggle)
			this._menuSignalId = this._toggle.menu.connect('open-state-changed', (_menu, open) => {
				if (open)
					this._refreshSharing()
			})
			this._sessionSignalId = Main.sessionMode.connect('updated', () => {
				++this._sharingEpoch
				this._shareCancellable?.cancel()
				this._pendingScope?.cancellable.cancel()
				this._toggle.sharingSection.removeAll()
				this._toggle.sharingSection.actor.visible = this._sharingAllowed()
				if (this._sharingAllowed() && this._toggle.menu.isOpen)
					this._refreshSharing()
			})
			this._toggle.sharingSection.actor.visible = this._sharingAllowed()
			this._clickedSignalId = this._toggle.connect('clicked', () =>
				this._requestSharing(this._toggle.checked)
			)
			// disable() destroys the item before the indicator; its signals are already gone.
			this._toggleDestroySignalId = this._toggle.connect('destroy', () => {
				this._clickedSignalId = 0
				this._menuSignalId = 0
				this._toggleDestroySignalId = 0
			})
			this._timer = GLib.timeout_add_seconds(
				GLib.PRIORITY_DEFAULT,
				statusPollSeconds,
				() => {
					if (this._destroyed)
						return GLib.SOURCE_REMOVE
					this._pollStatusAndPending()
					return GLib.SOURCE_CONTINUE
				}
			)
			try {
				this._networkMonitor = Gio.NetworkMonitor.get_default()
				this._meteredSignalId = this._networkMonitor.connect(
					'notify::network-metered', () => this._onNetworkMeteredChanged()
				)
			} catch {
				this._networkMonitor = null
			}
		}

		_isNetworkMetered() {
			try {
				return this._networkMonitor?.get_network_metered() ?? false
			} catch {
				return false
			}
		}


		_validatedServiceName() {
			const name = this._settings.get_string('service-name')
			if (/^[a-zA-Z0-9_][a-zA-Z0-9_.:@-]*\.service$/.test(name))
				return name
			return null
		}

		_assertCurrent(generation, cancellable) {
			if (this._destroyed || generation !== this._generation || cancellable.is_cancelled())
				throw new Error('Operation cancelled')
		}

		_newCancellable() {
			const cancellable = new Gio.Cancellable()
			this._cancellables.add(cancellable)
			return cancellable
		}

		// A child deadline must not cancel the whole transition: readiness can
		// retry a connection while the container is still starting.
		_scope(parent, milliseconds) {
			const cancellable = this._newCancellable()
			const scope = { cancellable, parent, signal: 0, timer: 0 }
			scope.signal = parent.connect(() => cancellable.cancel())
			scope.timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, milliseconds, () => {
				this._timeouts.delete(scope.timer)
				scope.timer = 0
				cancellable.cancel()
				return GLib.SOURCE_REMOVE
			})
			this._timeouts.add(scope.timer)
			return scope
		}

		_releaseScope(scope) {
			if (scope.signal)
				scope.parent.disconnect(scope.signal)
			if (scope.timer && this._timeouts.delete(scope.timer))
				GLib.Source.remove(scope.timer)
			this._cancellables.delete(scope.cancellable)
		}

		async _exec(argv, cancellable, allowFailure = false, cleanup = false, seconds = commandSeconds) {
			const generation = this._generation
			if ((!cleanup && this._destroyed) || cancellable.is_cancelled())
				throw new Error('Operation cancelled')
			const scope = this._scope(cancellable, seconds * 1000)
			let cancelSignal = 0
			let proc = null
			try {
				proc = Gio.Subprocess.new(argv,
					Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
				cancelSignal = scope.cancellable.connect(() => {
					try { proc.force_exit() } catch {}
				})
				const stdout = await new Promise((resolve, reject) => {
					proc.communicate_utf8_async(null, scope.cancellable, (source, result) => {
						try {
							const [, output] = source.communicate_utf8_finish(result)
							if (!allowFailure && !source.get_successful())
								throw new Error('Command failed')
							resolve(output)
						} catch {
							reject(new Error(_('The file sharing service command failed. Check the user service journal.')))
						}
					})
				})
				if (!cleanup)
					this._assertCurrent(generation, cancellable)
				if (scope.cancellable.is_cancelled())
					throw new Error('Command cancelled')
				return stdout
			} catch {
				throw new Error(_('The file sharing service command failed. Check the user service journal.'))
			} finally {
				// Cancellation abandons communicate(), not the child. Reap the
				// killed helper before a newer request can write desktop metadata.
				if (proc && scope.cancellable.is_cancelled()) {
					await new Promise(resolve => {
						proc.wait_async(null, (source, result) => {
							try { source.wait_finish(result) } catch {}
							resolve()
						})
					})
				}
				if (cancelSignal)
					scope.cancellable.disconnect(cancelSignal)
				this._releaseScope(scope)
			}
		}

		_runSystemctl(verb, serviceName, cancellable) {
			const seconds = {
				start: unitStartSeconds,
				stop: unitStopSeconds,
				restart: unitStopSeconds + unitStartSeconds,
			}[verb] ?? commandSeconds
			return this._exec(['systemctl', '--user', verb, ...(serviceName ? [serviceName] : [])],
				cancellable, false, false, seconds)
		}

		async _helper(action, serviceName, cancellable, enabled = undefined, waitForNative = false) {
			const generation = this._generation
			const request = { action, serviceName, port: this._settings.get_int('port') }
			if (enabled !== undefined)
				request.enabled = enabled
			if (waitForNative)
				request.waitForNative = true
			const output = await this._exec(
				['gjs', '-m', this._extensionPath + '/service.js', JSON.stringify(request)], cancellable)
			this._assertCurrent(generation, cancellable)
			let result
			try { result = JSON.parse(output) } catch {}
			if (!isObject(result) || !Array.isArray(result.folders) ||
				typeof result.envFile !== 'string' || !result.envFile.startsWith('/') ||
				typeof result.provisioned !== 'boolean' || typeof result.restartRequired !== 'boolean' ||
				typeof result.nativeStartRequired !== 'boolean')
				throw new Error(_('The file sharing container setup returned an invalid response.'))
			return result
		}

		async _loadCredentials(cancellable, envFile = undefined) {
			const generation = this._generation
			const path = envFile ?? GLib.get_user_state_dir() + '/syncthing/container.env'
			const contents = await loadContentsAsync(Gio.File.new_for_path(path), cancellable)
			this._assertCurrent(generation, cancellable)
			const values = {}
			for (const line of contents.split('\n')) {
				const separator = line.indexOf('=')
				if (separator > 0)
					values[line.slice(0, separator)] = line.slice(separator + 1).trim()
			}
			const key = values.STGUIAPIKEY
			// Absent only in a credential file written before the password existed;
			// the helper adds one before the next start, so polling must not fail here.
			const guiPassword = values.BLING_GUI_PASSWORD || null
			const port = this._settings.get_int('port')
			if (!key || /\s/.test(key) || (guiPassword !== null && /\s/.test(guiPassword)) ||
				!Number.isInteger(port) || port < 1 || port > 65535)
				throw new Error(_('The private file sharing credentials are invalid.'))
			this._credentials = { key, guiPassword, url: 'http://127.0.0.1:' + port }
		}

		// Syncthing protects its web page only once a user and password are set;
		// without them any local program that can reach loopback gets the CSRF
		// cookie and full REST access. The toggle's own calls use X-API-Key, which
		// bypasses that login, so turning it on does not affect them. Returns
		// whether the daemon was changed (its API listener then restarts itself).
		async _ensureGuiAuth(generation, cancellable) {
			const gui = await this._api('GET', 'config/gui', cancellable)
			this._assertCurrent(generation, cancellable)
			if (!isObject(gui) || typeof gui.user !== 'string' || typeof gui.password !== 'string' ||
				(gui.authMode !== undefined && typeof gui.authMode !== 'string'))
				throw new Error(_('The local file sharing API returned invalid login settings.'))
			if (gui.user && gui.password)
				return false
			// Another directory (LDAP) already answers for logins; a static password would be ignored.
			if (gui.authMode && gui.authMode !== 'static')
				return false
			if (!this._credentials?.guiPassword)
				throw new Error(_('The private file sharing credentials are invalid.'))
			// PATCH merges into the current GUI settings; the daemon stores a bcrypt hash.
			await this._api('PATCH', 'config/gui', cancellable, {
				authMode: 'static',
				user: gui.user || GLib.get_user_name(),
				password: this._credentials.guiPassword,
			})
			this._assertCurrent(generation, cancellable)
			return true
		}

		_openSharingSettings() {
			const url = 'http://127.0.0.1:' + this._settings.get_int('port')
			const password = this._credentials?.guiPassword
			if (password) {
				try {
					St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, password)
					Main.notify(_('Sync Folder Sharing Settings'),
						_('Sign in with your login name. The password was copied to the clipboard.'))
				} catch (error) {
					logError(error, 'Failed to copy the sharing password')
				}
			}
			try {
				Gio.app_info_launch_default_for_uri(url, null)
			} catch (error) {
				logError(error, 'Failed to open URL')
			}
		}

		async _api(method, path, cancellable, body = undefined) {
			const generation = this._generation
			if (this._destroyed || cancellable.is_cancelled())
				throw new Error('Operation cancelled')
			if (!this._credentials)
				await this._loadCredentials(cancellable)
			this._assertCurrent(generation, cancellable)
			const message = Soup.Message.new(method, this._credentials.url + '/rest/' + path)
			message.get_request_headers().append('X-API-Key', this._credentials.key)
			if (body !== undefined)
				message.set_request_body_from_bytes('application/json',
					GLib.Bytes.new(new TextEncoder().encode(JSON.stringify(body))))
			const scope = this._scope(cancellable, 2000)
			try {
				const bytes = await new Promise((resolve, reject) => {
					this._session.send_and_read_async(message, GLib.PRIORITY_DEFAULT,
						scope.cancellable, (session, result) => {
							try { resolve(session.send_and_read_finish(result)) } catch {
								const error = new Error(_('Unable to reach the local file sharing API.'))
								error.transient = true
								reject(error)
							}
						})
				})
				this._assertCurrent(generation, cancellable)
				const status = message.get_status()
				if (status < 200 || status >= 300) {
					const error = new Error(_('The local file sharing API refused the request.') + ` (HTTP ${status})`)
					error.transient = status >= 500
					error.refused = status >= 400 && status < 500
					throw error
				}
				const text = new TextDecoder().decode(bytes.get_data())
				if (method !== 'GET' && !text.trim())
					return null
				try { return JSON.parse(text) } catch {
					throw new Error(_('The local file sharing API returned invalid data.'))
				}
			} finally {
				this._releaseScope(scope)
			}
		}

		async _delay(milliseconds, cancellable) {
			const generation = this._generation
			let timer = 0
			let signal = 0
			try {
				await new Promise((resolve, reject) => {
					timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, milliseconds, () => {
						this._timeouts.delete(timer)
						resolve()
						return GLib.SOURCE_REMOVE
					})
					this._timeouts.add(timer)
					signal = cancellable.connect(() => reject(new Error('Operation cancelled')))
				})
				this._assertCurrent(generation, cancellable)
			} finally {
				if (this._timeouts.delete(timer))
					GLib.Source.remove(timer)
				if (signal)
					cancellable.disconnect(signal)
			}
		}

		async _readServiceState(serviceName, cancellable) {
			const generation = this._generation
			const output = await this._exec(['systemctl', '--user', 'is-active', serviceName], cancellable, true)
			this._assertCurrent(generation, cancellable)
			const state = output?.trim()
			if (['inactive', 'failed', 'active', 'activating', 'deactivating', 'reloading', 'refreshing'].includes(state))
				return state
			throw new Error(_('Unable to determine the file sharing service state.'))
		}

		async _readUnitState(serviceName, cancellable) {
			const generation = this._generation
			const state = await this._readServiceState(serviceName, cancellable)
			this._assertCurrent(generation, cancellable)
			return state !== 'inactive' && state !== 'failed'
		}

		async _health(cancellable) {
			const generation = this._generation
			const status = await this._api('GET', 'system/status', cancellable)
			this._assertCurrent(generation, cancellable)
			if (!isObject(status) || typeof status.myID !== 'string' || !status.myID)
				throw new Error(_('The local file sharing API returned an invalid device identity.'))
			return status
		}

		async _waitUntilReady(serviceName, generation, cancellable) {
			const deadline = GLib.get_monotonic_time() + readinessSeconds * 1000000
			let failure = new Error(_('File sharing did not become ready. Check the user service journal.'))
			do {
				this._assertCurrent(generation, cancellable)
				const active = await this._readUnitState(serviceName, cancellable)
				this._assertCurrent(generation, cancellable)
				if (active) {
					try {
						const status = await this._health(cancellable)
						this._assertCurrent(generation, cancellable)
						return status
					} catch (error) {
						this._assertCurrent(generation, cancellable)
						if (!error.transient)
							throw error
						failure = error
					}
				}
				await this._delay(500, cancellable)
				this._assertCurrent(generation, cancellable)
			} while (GLib.get_monotonic_time() < deadline)
			throw failure
		}

		async _waitForNativePreparation(serviceName, generation, cancellable, waitForNative) {
			const deadline = GLib.get_monotonic_time() + readinessSeconds * 1000000
			const scope = this._scope(cancellable, readinessSeconds * 1000)
			try {
				do {
					this._assertCurrent(generation, scope.cancellable)
					const state = await this._readServiceState(serviceName, scope.cancellable)
					this._assertCurrent(generation, scope.cancellable)
					if (state === 'active') {
						const prepared = await this._helper('prepare', serviceName, scope.cancellable,
							undefined, waitForNative)
						this._assertCurrent(generation, scope.cancellable)
						if (!prepared.nativeStartRequired)
							return prepared
					}
					// Only the helper's explicit readiness handshake may be retried.
					// Unsafe folders, credentials or permanent inspection errors fail immediately.
					await this._delay(500, scope.cancellable)
					this._assertCurrent(generation, scope.cancellable)
				} while (GLib.get_monotonic_time() < deadline)
				throw new Error(_('File sharing did not become ready. Check the user service journal.'))
			} finally {
				this._releaseScope(scope)
			}
		}

		async _stopOwnedNative(serviceName) {
			// Teardown cancels request I/O, but must not leave its temporary native
			// sharing process behind. This bounded rollback outlives that request;
			// the transition queue waits for it before applying a newer intent.
			const cancellable = this._newCancellable()
			try {
				await this._exec(['systemctl', '--user', 'stop', serviceName], cancellable, false, true,
					unitStopSeconds)
			} finally {
				this._cancellables.delete(cancellable)
			}
		}

		async _provision(prepared, status, generation, cancellable) {
			if (prepared.provisioned)
				return
			const existing = await this._api('GET', 'config/folders', cancellable)
			this._assertCurrent(generation, cancellable)
			const defaults = await this._api('GET', 'config/defaults/folder', cancellable)
			this._assertCurrent(generation, cancellable)
			if (!Array.isArray(existing) || !existing.every(isObject) || !isObject(defaults))
				throw new Error(_('The local file sharing API returned invalid folder settings.'))
			for (const folder of prepared.folders) {
				this._assertCurrent(generation, cancellable)
				if (!isObject(folder) || typeof folder.id !== 'string' ||
					typeof folder.path !== 'string' || !folder.path.startsWith('/'))
					throw new Error(_('The file sharing container setup returned invalid folder settings.'))
				// Preserve folders already configured at this path, even under a
				// different ID. Never rewrite labels, sharing consent or paused state.
				if (existing.some(item => item.id === folder.id || item.path === folder.path))
					continue
				const configured = {
					...defaults,
					id: folder.id,
					label: folder.label,
					path: folder.path,
					paused: folder.paused,
					devices: [{ deviceID: status.myID }],
				}
				await this._api('POST', 'config/folders', cancellable, configured)
				this._assertCurrent(generation, cancellable)
			}
		}

		_requestSharing(enabled) {
			if (this._destroyed)
				return Promise.resolve()
			this._desiredOn = enabled
			this._pausedForMetered = enabled && this._isNetworkMetered()
			this._persistOnResume = enabled
			this._rememberHeldRequest()
			return this._enqueueTransition({
				active: enabled && !this._pausedForMetered,
				persist: !this._pausedForMetered,
				paused: this._pausedForMetered,
			})
		}

		_enqueueTransition(request) {
			if (this._destroyed)
				return Promise.resolve()
			request.generation = ++this._generation
			this._pendingRequest = request
			this._operationCancellable?.cancel()
			this._pollCancellable?.cancel()
			if (!this._transition)
				this._transition = this._drainTransitions()
			return this._transition
		}

		async _drainTransitions() {
			while (this._pendingRequest && !this._destroyed) {
				const request = this._pendingRequest
				this._pendingRequest = null
				const cancellable = this._newCancellable()
				this._operationCancellable = cancellable
				try {
					await this._transitionTo(request, cancellable)
				} finally {
					this._cancellables.delete(cancellable)
					this._operationCancellable = null
				}
			}
			this._transition = null
		}

		async _transitionTo(request, cancellable) {
			const { active, generation } = request
			const serviceName = this._validatedServiceName()
			const previouslyActive = this._observedActive
			let previouslyRunning = false
			let activationAttempted = false
			let nativeStartAttempted = false
			let initialized = false
			let documentsAvailable = true
			try {
				this._assertCurrent(generation, cancellable)
				if (!serviceName)
					throw new Error(_('The configured file sharing service name is invalid.'))
				this._toggle.subtitle = active ? _('Starting') : _('Stopping')
				if (active) {
					previouslyRunning = await this._readUnitState(serviceName, cancellable)
					this._assertCurrent(generation, cancellable)
					let prepared = await this._helper('prepare', serviceName, cancellable)
					this._assertCurrent(generation, cancellable)
					if (prepared.nativeStartRequired) {
						nativeStartAttempted = true
						await this._runSystemctl('start', serviceName, cancellable)
						this._assertCurrent(generation, cancellable)
						prepared = await this._waitForNativePreparation(serviceName, generation, cancellable,
							!previouslyRunning)
						this._assertCurrent(generation, cancellable)
					}
					documentsAvailable = prepared.documentsAvailable
					await this._runSystemctl('daemon-reload', null, cancellable)
					this._assertCurrent(generation, cancellable)
					await this._loadCredentials(cancellable, prepared.envFile)
					this._assertCurrent(generation, cancellable)
					activationAttempted = true
					await this._runSystemctl(nativeStartAttempted || (previouslyRunning && prepared.restartRequired) ? 'restart' : 'start',
						serviceName, cancellable)
					this._assertCurrent(generation, cancellable)
					let status = await this._waitUntilReady(serviceName, generation, cancellable)
					this._assertCurrent(generation, cancellable)
					// Lock the web page before any folder is configured on it.
					if (await this._ensureGuiAuth(generation, cancellable)) {
						this._assertCurrent(generation, cancellable)
						status = await this._waitUntilReady(serviceName, generation, cancellable)
					}
					this._assertCurrent(generation, cancellable)
					await this._provision(prepared, status, generation, cancellable)
					this._assertCurrent(generation, cancellable)
					await this._health(cancellable)
					this._assertCurrent(generation, cancellable)
					await this._helper('provisioned', serviceName, cancellable)
					this._assertCurrent(generation, cancellable)
					initialized = true
				} else {
					const running = await this._readUnitState(serviceName, cancellable)
					this._assertCurrent(generation, cancellable)
					if (running) {
						await this._runSystemctl('stop', serviceName, cancellable)
						this._assertCurrent(generation, cancellable)
					}
					const stillRunning = await this._readUnitState(serviceName, cancellable)
					this._assertCurrent(generation, cancellable)
					if (stillRunning)
						throw new Error(_('File sharing is still running. Check the user service journal.'))
				}
				if (request.persist && !this._settings.get_boolean('start-stop-only')) {
					await this._helper('autostart', serviceName, cancellable, active)
					this._assertCurrent(generation, cancellable)
					await this._runSystemctl('daemon-reload', null, cancellable)
					this._assertCurrent(generation, cancellable)
				}
				if (active)
					this._persistOnResume = false
				this._unitActive = active
				this.updateStatus(active, request.paused ? _('Paused on metered network') : undefined)
				if (request.paused) {
					Main.notify(_('Sync Folder Sharing Paused'), _('Sharing will resume on an unmetered connection.'))
				} else if (active) {
					Main.notify(_('Sync Folder Sharing Enabled'), documentsAvailable
						? _('Review folders and choose devices in Sharing Settings.')
						: _('Documents is unavailable. Choose an existing folder in Sharing Settings.'))
				} else {
					Main.notify(_('Sync Folder Sharing Disabled'), _('File sharing is paused.'))
				}
			} catch (error) {
				if (active && nativeStartAttempted && !initialized && !previouslyRunning) {
					try {
						await this._stopOwnedNative(serviceName)
					} catch (rollbackError) {
						logError(rollbackError, 'Sync Folder')
					}
				}
				if (this._destroyed || generation !== this._generation || cancellable.is_cancelled())
					return
				// Roll back only a service this request started. Failed API health
				// does not mean an existing native or managed service was stopped.
				if (serviceName) {
					try {
						if (active && activationAttempted && !nativeStartAttempted && !initialized && !previouslyRunning) {
							await this._runSystemctl('stop', serviceName, cancellable)
							this._assertCurrent(generation, cancellable)
						}
						const unitActive = await this._readUnitState(serviceName, cancellable)
						this._assertCurrent(generation, cancellable)
						this._unitActive = unitActive
						let healthy = false
						if (this._unitActive) {
							await this._health(cancellable)
							this._assertCurrent(generation, cancellable)
							healthy = true
						}
						this.updateStatus(healthy, _('Error'))
					} catch {
						if (this._destroyed || generation !== this._generation || cancellable.is_cancelled())
							return
						this.updateStatus(false, _('Error'))
					}
				} else {
					this.updateStatus(previouslyActive, _('Error'))
				}
				logError(error, 'Sync Folder')
				Main.notify(_('Unable to Change File Sharing'),
					_('Try again, or open Sharing Settings to review file sharing.'))
			}
		}

		async reconcile() {
			const generation = this._generation
			await this.checkStatus()
			if (this._destroyed || generation !== this._generation)
				return
			this._desiredOn = this._unitActive
			if (!this._unitActive && this._validatedServiceName() &&
				!this._settings.get_boolean('start-stop-only') &&
				this._settings.get_boolean('resume-when-unmetered')) {
				this._desiredOn = true
				this._pausedForMetered = true
				this._persistOnResume = true
				this.updateStatus(false, _('Paused on metered network'))
			}
			this._rememberHeldRequest()
			return this._onNetworkMeteredChanged()
		}

		// A request to turn sharing on that a metered link held back has not
		// recorded the login choice yet, so it is kept in GSettings: otherwise a
		// logout before the link unmeters reads it as "off" at the next login.
		_rememberHeldRequest() {
			const held = this._desiredOn && this._pausedForMetered && this._persistOnResume &&
				!this._settings.get_boolean('start-stop-only')
			if (this._settings.get_boolean('resume-when-unmetered') !== held)
				this._settings.set_boolean('resume-when-unmetered', held)
		}

		_onNetworkMeteredChanged() {
			if (this._destroyed)
				return Promise.resolve()
			// No transition can run against a rejected name; recording a pause
			// here would make the next unmetered edge restart a running unit.
			if (!this._validatedServiceName())
				return Promise.resolve()
			const metered = this._isNetworkMetered()
			if (metered) {
				if (!this._desiredOn || this._pausedForMetered)
					return Promise.resolve()
				this._pausedForMetered = true
				this._rememberHeldRequest()
				return this._enqueueTransition({ active: false, persist: false, paused: true })
			}
			if (!this._pausedForMetered || !this._desiredOn)
				return Promise.resolve()
			this._pausedForMetered = false
			this._rememberHeldRequest()
			return this._enqueueTransition({ active: true, persist: this._persistOnResume, paused: false })
		}

		async checkStatus() {
			if (!this._validatedServiceName()) {
				this.updateStatus(false, _('Invalid service name'))
				return false
			}
			return this._poll(false)
		}

		_pollStatusAndPending() {
			return this._poll(true)
		}

		_poll(includePending) {
			if (this._destroyed || this._transition)
				return Promise.resolve(this._observedActive)
			if (this._pollPromise)
				return this._pollPromise
			const generation = this._generation
			const cancellable = this._newCancellable()
			this._pollCancellable = cancellable
			this._pollPromise = this._pollOnce(includePending, generation, cancellable).finally(() => {
				this._cancellables.delete(cancellable)
				this._pollCancellable = null
				this._pollPromise = null
			})
			return this._pollPromise
		}

		async _pollOnce(includePending, generation, cancellable) {
			try {
				const serviceName = this._validatedServiceName()
				if (!serviceName) {
					this.updateStatus(false, _('Invalid service name'))
					return false
				}
				const unitActive = await this._readUnitState(serviceName, cancellable)
				this._assertCurrent(generation, cancellable)
				this._unitActive = unitActive
				if (!unitActive) {
					this.updateStatus(false, this._pausedForMetered ? _('Paused on metered network') : undefined)
					return false
				}
				await this._health(cancellable)
				this._assertCurrent(generation, cancellable)
				this.updateStatus(true)
				if (includePending) {
					const epoch = this._sharingEpoch
					try {
						await this._checkPendingInvitations(generation, cancellable)
						this._assertCurrent(generation, cancellable)
					} catch {
						this._assertCurrent(generation, cancellable)
						// A menu open or sharing change cancels the shared read; that is not a failure.
						if (epoch === this._sharingEpoch)
							this._toggle.subtitle = _('Invitations unavailable')
					}
				}
				return true
			} catch {
				if (!this._destroyed && generation === this._generation && !cancellable.is_cancelled())
					this.updateStatus(false, _('Service unavailable'))
				return false
			}
		}

		_sharingAllowed() {
			return !this._destroyed && Main.sessionMode.allowSettings && !Main.sessionMode.isLocked
		}

		_sharingApi(generation, cancellable, epoch) {
			return async (method, path, body) => {
				const check = () => {
					this._assertCurrent(generation, cancellable)
					if (!this._sharingAllowed() || epoch !== this._sharingEpoch)
						throw new Error('Sharing controls unavailable')
				}
				check()
				const result = await this._api(method, path, cancellable, body)
				check()
				return result
			}
		}

		_checkPendingInvitations(generation, cancellable) {
			if (!this._sharingAllowed() || this._sharingBusy)
				return Promise.resolve()
			if (this._pendingPromise && this._pendingEpoch !== this._sharingEpoch)
				return this._pendingPromise.catch(() => {}).then(() =>
					this._checkPendingInvitations(generation, cancellable))
			if (!this._pendingPromise) {
				this._pendingEpoch = this._sharingEpoch
				const scope = this._scope(cancellable, 4500)
				this._pendingScope = scope
				this._pendingPromise = this._readPending(generation, scope.cancellable).finally(() => {
					this._releaseScope(scope)
					this._pendingScope = null
					this._pendingPromise = null
				})
			}
			return this._pendingPromise
		}

		async _readPending(generation, cancellable) {
			const epoch = this._sharingEpoch
			const state = await readSharing(this._sharingApi(generation, cancellable, epoch))
			this._assertCurrent(generation, cancellable)
			if (!this._sharingAllowed() || this._sharingBusy || epoch !== this._sharingEpoch)
				return
			// Background polls must not rebuild an open menu and collapse its submenus;
			// opening the menu starts a new epoch that always renders.
			if (!this._toggle.menu.isOpen || this._renderedEpoch !== epoch) {
				this._renderSharing(state)
				this._renderedEpoch = epoch
			}
			const current = new Set(state.invitations.map(item => item.key))
			const fresh = state.invitations.filter(item => !this._announcedPending.has(item.key))
			this._announcedPending = current
			// No device names in notifications, including retained lock-screen history.
			if (fresh.some(item => item.matching))
				Main.notify(_('Sync Folder Sharing Invitation'),
					_('Open the Sync Folder menu to accept a folder from a paired device.'))
			if (fresh.some(item => !item.matching))
				Main.notify(_('Sync Folder Sharing Invitation'),
					_('Open Sharing Settings to review and accept new device or folder invitations.'))
		}

		_sharingMessage(text) {
			const section = this._toggle.sharingSection
			section.removeAll()
			section.addMenuItem(new PopupMenu.PopupMenuItem(text, { reactive: false }))
		}

		_renderSharing(state) {
			const section = this._toggle.sharingSection
			section.removeAll()
			if (!this._sharingAllowed())
				return
			if (!state.peers.length) {
				this._sharingMessage(_('No paired devices. Pair in Sharing Settings.'))
				return
			}
			const labels = { documents: _('Documents'), music: _('Music'), pictures: _('Pictures'),
				videos: _('Videos'), templates: _('Templates') }
			const states = { notShared: _('Not shared'), shared: _('Shared with this device'),
				waiting: _('Waiting for the other device'), offered: _('Offered by the other device'),
				sharing: _('Sharing') }
			for (const peer of state.peers) {
				const item = new PopupMenu.PopupSubMenuMenuItem(peer.name + ' — ' +
					(peer.connected ? _('Connected') : _('Not connected')))
				section.addMenuItem(item)
				if (!peer.shares.length)
					item.menu.addMenuItem(new PopupMenu.PopupMenuItem(
						_('Turn on a standard folder in Sharing Settings.'), { reactive: false }))
				for (const folder of peer.shares) {
					const action = folder.shared ? _('Stop sharing') :
						(folder.state === 'offered' ? _('Accept') : _('Share'))
					item.menu.addAction(`${labels[folder.folderId]} — ${states[folder.state]} — ${action}`,
						() => this._changeSharing(folder.folderId, peer.deviceId, !folder.shared))
				}
			}
		}

		async _refreshSharing() {
			if (!this._sharingAllowed() || this._sharingBusy)
				return
			this._sharingMessage(_('Checking sharing…'))
			if (!this._observedActive || this._transition) {
				this._sharingMessage(_('Turn on Sync Folder to share with a device.'))
				return
			}
			const generation = this._generation
			// Opening the menu must read anew, rather than reuse a snapshot that
			// a background poll started before the user opened it.
			const epoch = ++this._sharingEpoch
			this._pendingScope?.cancellable.cancel()
			const cancellable = this._newCancellable()
			try {
				await this._checkPendingInvitations(generation, cancellable)
			} catch {
				if (this._sharingAllowed() && generation === this._generation && epoch === this._sharingEpoch)
					this._sharingMessage(_('Sharing unavailable. Try opening the menu again.'))
			} finally {
				this._cancellables.delete(cancellable)
			}
		}

		async _changeSharing(folderId, deviceId, share) {
			if (!this._sharingAllowed() || this._sharingBusy || !this._observedActive || this._transition)
				return
			this._sharingBusy = true
			++this._sharingEpoch
			this._pendingScope?.cancellable.cancel()
			this._sharingMessage(_('Updating sharing…'))
			const parent = this._newCancellable()
			this._shareCancellable = parent
			// Bound the whole change, including confirmation, not each request.
			const scope = this._scope(parent, 9000)
			const epoch = this._sharingEpoch
			const generation = this._generation
			try {
				await changeSharing(this._sharingApi(generation, scope.cancellable, epoch),
					folderId, deviceId, share)
			} catch (error) {
				if (this._sharingAllowed() && epoch === this._sharingEpoch)
					Main.notify(_('Sync Folder Sharing'), error.refused
						? _('The sharing change was refused. Check the folder in Sharing Settings.')
						: _('The sharing change could not be confirmed. Check the folder before trying again.'))
			} finally {
				this._releaseScope(scope)
				this._cancellables.delete(parent)
				this._shareCancellable = null
				this._sharingBusy = false
				if (this._sharingAllowed())
					this._refreshSharing()
			}
		}

		updateStatus(active, subtitle = undefined) {
			if (this._destroyed)
				return
			this._observedActive = active
			this._toggle.set({ checked: active, subtitle: subtitle ?? (active ? _('Running') : _('Stopped')) })
			this._toggle.webGuiItem.setSensitive(active)
			if (!active)
				this._toggle.sharingSection.removeAll()
		}

		destroy() {
			if (this._destroyed)
				return
			this._destroyed = true
			++this._generation
			this._pendingRequest = null
			for (const cancellable of this._cancellables)
				cancellable.cancel()
			this._cancellables.clear()
			this._session.abort()
			this._credentials = null
			for (const timer of this._timeouts)
				GLib.Source.remove(timer)
			this._timeouts.clear()
			if (this._timer) {
				GLib.Source.remove(this._timer)
				this._timer = null
			}
			if (this._networkMonitor && this._meteredSignalId)
				this._networkMonitor.disconnect(this._meteredSignalId)
			this._meteredSignalId = 0
			this._networkMonitor = null
			if (this._sessionSignalId)
				Main.sessionMode.disconnect(this._sessionSignalId)
			this._sessionSignalId = 0
			if (this._menuSignalId)
				this._toggle.menu.disconnect(this._menuSignalId)
			this._menuSignalId = 0
			if (this._toggleDestroySignalId)
				this._toggle.disconnect(this._toggleDestroySignalId)
			this._toggleDestroySignalId = 0
			if (this._clickedSignalId)
				this._toggle.disconnect(this._clickedSignalId)
			this._clickedSignalId = 0
			this._announcedPending.clear()
			super.destroy()
		}
	}
)
