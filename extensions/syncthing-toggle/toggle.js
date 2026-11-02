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

const statusPollSeconds = 60
const readinessSeconds = 30
const commandSeconds = 30
// systemctl waits for the unit job. The first start pulls the image inside that
// job, so these must exceed TimeoutStartSec/TimeoutStopSec in syncthing.container.in.
const unitStartSeconds = 960
const unitStopSeconds = 90

// disable() cannot await rollback. Keep its completion outside the destroyed
// indicator so a successor cannot inspect or start the same temporary unit.
let nativeCleanupBarrier = Promise.resolve()

async function runCommand(argv, parent, allowFailure, seconds) {
	const cancellable = new Gio.Cancellable()
	let timer = 0
	let parentSignal = 0
	let cancelSignal = 0
	let proc = null
	try {
		parentSignal = parent.connect(() => cancellable.cancel())
		timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, seconds * 1000, () => {
			timer = 0
			cancellable.cancel()
			return GLib.SOURCE_REMOVE
		})
		if (cancellable.is_cancelled())
			throw new Error('Operation cancelled')
		proc = Gio.Subprocess.new(argv,
			Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
		cancelSignal = cancellable.connect(() => {
			try { proc.force_exit() } catch {}
		})
		const stdout = await new Promise((resolve, reject) => {
			proc.communicate_utf8_async(null, cancellable, (source, result) => {
				try {
					const [, output] = source.communicate_utf8_finish(result)
					if (!allowFailure && !source.get_successful())
						throw new Error('Command failed')
					resolve(output)
				} catch {
					reject(new Error('Command failed'))
				}
			})
		})
		if (cancellable.is_cancelled())
			throw new Error('Command cancelled')
		return stdout
	} catch {
		throw new Error(_('The file sharing service command failed. Check the user service journal.'))
	} finally {
		// Cancelling communicate() abandons the read, not the child. This owner
		// retains its deadline and reaps before releasing the completion barrier.
		if (proc && cancellable.is_cancelled()) {
			await new Promise(resolve => {
				proc.wait_async(null, (source, result) => {
					try { source.wait_finish(result) } catch {}
					resolve()
				})
			})
		}
		if (cancelSignal)
			cancellable.disconnect(cancelSignal)
		if (parentSignal)
			parent.disconnect(parentSignal)
		if (timer)
			GLib.Source.remove(timer)
	}
}

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
			this._sharingSettingsCancellable = null
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
			this._clickedSignalId = this._toggle.connect('clicked', () =>
				this._requestSharing(this._toggle.checked)
			)
			// disable() destroys the item before the indicator; its signals are already gone.
			this._toggleDestroySignalId = this._toggle.connect('destroy', () => {
				this._clickedSignalId = 0
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

		async _exec(argv, cancellable, allowFailure = false, seconds = commandSeconds) {
			const generation = this._generation
			this._assertCurrent(generation, cancellable)
			const stdout = await runCommand(argv, cancellable, allowFailure, seconds)
			this._assertCurrent(generation, cancellable)
			return stdout
		}

		_runSystemctl(verb, serviceName, cancellable) {
			const seconds = {
				start: unitStartSeconds,
				stop: unitStopSeconds,
				restart: unitStopSeconds + unitStartSeconds,
			}[verb] ?? commandSeconds
			return this._exec(['systemctl', '--user', verb, ...(serviceName ? [serviceName] : [])],
				cancellable, false, seconds)
		}

		async _helper(action, serviceName, cancellable, enabled = undefined, waitForNative = false) {
			const generation = this._generation
			const request = { action, serviceName, port: this._settings.get_int('port') }
			if (enabled !== undefined)
				request.enabled = enabled
			if (waitForNative)
				request.waitForNative = true
			const output = await this._exec(
				['gjs', '-m', this._extensionPath + '/service.js', JSON.stringify(request)], cancellable, true)
			this._assertCurrent(generation, cancellable)
			let result
			try { result = JSON.parse(output) } catch {}
			let diagnostic
			if (isObject(result) && Object.keys(result).length === 1) {
				switch (result.error) {
				case 'legacy-inspection-failed':
					diagnostic = _('Unable to inspect existing file sharing. Check the user service journal and try again.')
					break
				case 'legacy-response-invalid':
					diagnostic = _('Existing file sharing returned invalid folder settings. Check the user service journal.')
					break
				case 'legacy-folder-unavailable':
					diagnostic = _('An existing shared folder is unavailable. Restore access to it and try again.')
				}
			}
			if (diagnostic) {
				const error = new Error(diagnostic)
				error.userMessage = diagnostic
				throw error
			}
			if (action === 'image' && isObject(result) && typeof result.image === 'string' &&
				/^ghcr\.io\/syncthing\/syncthing:[^@\s]+@sha256:[a-f0-9]{64}$/.test(result.image))
				return result
			if (action === 'image' || !isObject(result) || !Array.isArray(result.folders) ||
				typeof result.envFile !== 'string' || !result.envFile.startsWith('/') ||
				typeof result.provisioned !== 'boolean' || typeof result.restartRequired !== 'boolean' ||
				typeof result.nativeStartRequired !== 'boolean')
				throw new Error(_('The file sharing container setup returned an invalid response.'))
			return result
		}

		async _ensureImage(serviceName, cancellable) {
			const { image } = await this._helper('image', serviceName, cancellable)
			try {
				// Own Podman directly: killing a synchronous helper would leave its
				// long-running pull behind after disable() or a newer request.
				await this._exec(['/usr/bin/podman', 'pull', '--policy=missing', '--quiet', image],
					cancellable, false, unitStartSeconds)
			} catch {
				const message = _('Unable to download the file sharing image. Check your connection and try again.')
				const error = new Error(message)
				error.userMessage = message
				throw error
			}
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

		async _openSharingSettings() {
			if (this._destroyed)
				return
			const generation = this._generation
			this._sharingSettingsCancellable?.cancel()
			const cancellable = this._newCancellable()
			this._sharingSettingsCancellable = cancellable
			const url = 'http://127.0.0.1:' + this._settings.get_int('port')
			try {
				if (!this._credentials)
					await this._loadCredentials(cancellable)
				this._assertCurrent(generation, cancellable)
				const password = this._credentials.guiPassword
				if (password) {
					const gui = await this._api('GET', 'config/gui', cancellable)
					this._assertCurrent(generation, cancellable)
					if (isObject(gui) && typeof gui.user === 'string' && gui.user &&
						typeof gui.password === 'string' && gui.password &&
						(gui.authMode === undefined || gui.authMode === 'static')) {
						// REST Basic auth still needs CSRF validation. The daemon's
						// password-login endpoint verifies credentials without a key or
						// cookie; only its exact 204 response proves the current login.
						await this._api('POST', 'noauth/auth/password', cancellable,
							{ username: gui.user, password, stayLoggedIn: false }, true)
						this._assertCurrent(generation, cancellable)
						St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, password)
						Main.notify(_('Sync Folder Sharing Settings'),
							_('The password was copied to the clipboard. Username:') + ' ' + gui.user)
					}
				}
			} catch {
				// An existing/changed/directory login or unavailable daemon is not
				// evidence that the independently stored password belongs to it.
			} finally {
				this._cancellables.delete(cancellable)
				if (this._sharingSettingsCancellable === cancellable)
					this._sharingSettingsCancellable = null
			}
			if (this._destroyed || generation !== this._generation || cancellable.is_cancelled())
				return
			try {
				Gio.app_info_launch_default_for_uri(url, null)
			} catch (error) {
				logError(error, 'Failed to open URL')
			}
		}

		async _api(method, path, cancellable, body = undefined, verifyLogin = false) {
			const generation = this._generation
			if (this._destroyed || cancellable.is_cancelled())
				throw new Error('Operation cancelled')
			if (!this._credentials)
				await this._loadCredentials(cancellable)
			this._assertCurrent(generation, cancellable)
			const message = Soup.Message.new(method, this._credentials.url + '/rest/' + path)
			const session = verifyLogin ? new Soup.Session({ proxy_resolver: null }) : this._session
			if (verifyLogin)
				message.set_flags(Soup.MessageFlags.NO_REDIRECT)
			else
				message.get_request_headers().append('X-API-Key', this._credentials.key)
			if (body !== undefined)
				message.set_request_body_from_bytes('application/json',
					GLib.Bytes.new(new TextEncoder().encode(JSON.stringify(body))))
			const scope = this._scope(cancellable, 2000)
			try {
				const bytes = await new Promise((resolve, reject) => {
					session.send_and_read_async(message, GLib.PRIORITY_DEFAULT,
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
				if (verifyLogin) {
					if (status !== 204)
						throw new Error(_('Unable to verify the current file sharing password.'))
					return true
				}
				if (status < 200 || status >= 300) {
					const error = new Error(_('The local file sharing API refused the request.') + ` (HTTP ${status})`)
					error.transient = status >= 500
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
				if (verifyLogin)
					session.abort()
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
			await nativeCleanupBarrier
			this._assertCurrent(generation, cancellable)
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
			this._sharingSettingsCancellable?.cancel()
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
					await this._ensureImage(serviceName, cancellable)
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
						// This command owns its cancellable/deadline independently of
						// the dead indicator, whose destroy() publishes its completion.
						await runCommand(['systemctl', '--user', 'stop', serviceName],
							new Gio.Cancellable(), false, unitStopSeconds)
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
					error.userMessage ?? _('Try again, or open Sharing Settings to review file sharing.'))
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
					try {
						await this._checkPendingInvitations(generation, cancellable)
						this._assertCurrent(generation, cancellable)
					} catch {
						this._assertCurrent(generation, cancellable)
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

		_checkPendingInvitations(generation, cancellable) {
			if (!this._pendingPromise)
				this._pendingPromise = this._readPending(generation, cancellable).finally(() => {
					this._pendingPromise = null
				})
			return this._pendingPromise
		}

		async _readPending(generation, cancellable) {
			const devices = await this._api('GET', 'cluster/pending/devices', cancellable)
			this._assertCurrent(generation, cancellable)
			const folders = await this._api('GET', 'cluster/pending/folders', cancellable)
			this._assertCurrent(generation, cancellable)
			if (!isObject(devices) || !isObject(folders))
				throw new Error('Invalid pending invitations')
			const current = new Set(Object.keys(devices).map(id => JSON.stringify(['device', id])))
			for (const [folderId, folder] of Object.entries(folders)) {
				if (!isObject(folder) || !isObject(folder.offeredBy))
					throw new Error('Invalid pending folder invitation')
				for (const deviceId of Object.keys(folder.offeredBy))
					current.add(JSON.stringify(['folder', folderId, deviceId]))
			}
			const hasNew = [...current].some(key => !this._announcedPending.has(key))
			this._announcedPending = current
			if (hasNew)
				Main.notify(_('Sync Folder Sharing Invitation'),
					_('Open Sharing Settings to review and accept new device or folder invitations.'))
		}

		updateStatus(active, subtitle = undefined) {
			if (this._destroyed)
				return
			this._observedActive = active
			this._toggle.set({ checked: active, subtitle: subtitle ?? (active ? _('Running') : _('Stopped')) })
			this._toggle.webGuiItem.setSensitive(active)
		}

		destroy() {
			if (this._destroyed)
				return
			this._destroyed = true
			++this._generation
			this._pendingRequest = null
			if (this._transition)
				nativeCleanupBarrier = Promise.allSettled([nativeCleanupBarrier, this._transition]).then(() => {})
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
