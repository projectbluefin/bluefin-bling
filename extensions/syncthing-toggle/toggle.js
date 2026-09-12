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
				// Open the URL in the default browser
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

				if (isEnabled) {
					// Seed initial configuration and ensure ~/Sync exists asynchronously
					await this._ensureSyncFolderConfig()
					// Check metered network constraint
					if (this._isNetworkMetered()) {
						Main.notify(
							_('Sync Folder Sharing Paused'),
							_('Metered network connection detected. Syncing is paused.')
						)
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

				await this._runSystemctl(!isEnabled ? 'stop' : 'start', serviceName)
				await this.checkStatus()

				// if the appropriate setting is enabled (default, also enable or disable the service)
				if (!this._settings.get_boolean('start-stop-only'))
					await this._runSystemctl(!isEnabled ? 'disable' : 'enable', serviceName)
			})

			// Set up periodic status polling (every 5 seconds)
			this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
				if (!this._destroyed)
					this.checkStatus()
				return GLib.SOURCE_CONTINUE
			})

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
				console.error(`[SyncthingToggle] Error connecting network monitor: ${e.message}`)
			}
		}

		_isNetworkMetered() {
			try {
				return this._networkMonitor ? this._networkMonitor.get_network_metered() : false
			} catch (e) {
				return false
			}
		}

		async _onNetworkMeteredChanged() {
			if (this._destroyed)
				return

			const isMetered = this._isNetworkMetered()
			if (isMetered && this._toggle.checked) {
				Main.notify(
					_('Sync Folder Sharing Paused'),
					_('Metered connection detected. Pausing file sharing to save data.')
				)
				const serviceName = this._validatedServiceName()
				if (serviceName) {
					await this._runSystemctl('stop', serviceName)
					await this.checkStatus()
				}
			}
		}

		async _ensureSyncFolderConfig() {
			const homeDir = GLib.get_home_dir()
			const syncDir = `${homeDir}/Sync`
			const stateDir = `${homeDir}/.local/state/syncthing`
			const port = this._settings.get_int('port') || 8384

			// Ensure ~/Sync directory exists
			const syncDirFile = Gio.File.new_for_path(syncDir)
			if (!syncDirFile.query_exists(null)) {
				try {
					syncDirFile.make_directory_with_parents(null)
				} catch (e) {
					console.error(`[SyncthingToggle] Failed to create sync directory: ${e.message}`)
				}
			}

			const configFile = Gio.File.new_for_path(`${stateDir}/config.xml`)
			if (configFile.query_exists(null))
				return

			const stateDirFile = Gio.File.new_for_path(stateDir)
			if (!stateDirFile.query_exists(null)) {
				try {
					stateDirFile.make_directory_with_parents(null)
				} catch (e) {
					console.error(`[SyncthingToggle] Failed to create state directory: ${e.message}`)
				}
			}

			// Locate syncthing binary
			const syncthingBin = GLib.find_program_in_path('syncthing') || '/usr/bin/syncthing'

			// Generate initial syncthing config offline asynchronously
			try {
				const proc = Gio.Subprocess.new(
					[syncthingBin, 'generate', `--home=${stateDir}`, '--no-port-probing'],
					Gio.SubprocessFlags.NONE
				)
				await new Promise((resolve, reject) => {
					proc.wait_check_async(this._cancellable, (source, res) => {
						try {
							source.wait_check_finish(res)
							resolve()
						} catch (e) {
							reject(e)
						}
					})
				})
			} catch (e) {
				console.error(`[SyncthingToggle] Failed to generate initial config: ${e.message}`)
				return
			}
			if (!configFile.query_exists(null))
				return

			try {
				const [, contents] = configFile.load_contents(null)
				let xml = new TextDecoder('utf-8').decode(contents)

				// Bind GUI to loopback
				xml = xml.replace(
					/<address>127\.0\.0\.1:\d+<\/address>/,
					`<address>127.0.0.1:${port}</address>`
				)

				// Extract local device ID
				const devMatch = xml.match(/<device id="([^"]+)"/)
				const myDevId = devMatch ? devMatch[1] : ''

				// Enable autoAcceptFolders in default device template for zero-toil folder invitations
				xml = xml.replace(
					/(<device id=""[^>]*>[\s\S]*?<autoAcceptFolders>)false(<\/autoAcceptFolders>)/,
					'$1true$2'
				)

				// Seed default ~/Sync folder
				if (!xml.includes('id="sync"')) {
					const folderXml = `    <folder id="sync" label="Sync Folder" path="${syncDir}" type="sendreceive" rescanIntervalS="3600" fsWatcherEnabled="true" fsWatcherDelayS="10" ignorePerms="false" autoNormalize="true">
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

				configFile.replace_contents(
					new TextEncoder().encode(xml),
					null,
					false,
					Gio.FileCreateFlags.REPLACE_DESTINATION,
					null
				)
			} catch (e) {
				console.error(`[SyncthingToggle] Failed to configure syncthing config: ${e.message}`)
			}
		}

		// The service-name setting is free text (dconf-writable by any
		// same-uid process). Never interpolate it into a command line:
		// validate unit-name syntax and pass it as a discrete argv entry.
		_validatedServiceName() {
			const name = this._settings.get_string('service-name')
			if (/^[a-zA-Z0-9_.:@-]+\.service$/.test(name))
				return name
			console.error(`[SyncthingToggle] Rejecting invalid service-name: ${name}`)
			return null
		}

		async _runSystemctl(verb, serviceName) {
			try {
				const proc = Gio.Subprocess.new(
					['systemctl', '--user', verb, serviceName],
					Gio.SubprocessFlags.NONE
				)
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
			} catch (e) {
				logError(e, `Failed to run systemctl ${verb}`)
			}
		}

		async checkStatus() {
			if (this._destroyed)
				return

			try {
				const proc = Gio.Subprocess.new(
					[
						'systemctl',
						'--user',
						'status',
						this._settings.get_string('service-name'),
					],
					Gio.SubprocessFlags.STDOUT_PIPE
				)

				const stdout = await new Promise((resolve, _reject) => {
					proc.communicate_utf8_async(null, this._cancellable, (proc, res) => {
						try {
							let [, stdout] = proc.communicate_utf8_finish(res)
							resolve(stdout)
						} catch {
							resolve(null)
						}
					})
				})

				if (stdout) {
					const status = statusPattern.exec(stdout)?.[1]
					this.updateStatus(status == '(running)')
				} else {
					this.updateStatus(false)
				}
			} catch (err) {
				this.updateStatus(false)
				logError('Err checking status:', err)
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
