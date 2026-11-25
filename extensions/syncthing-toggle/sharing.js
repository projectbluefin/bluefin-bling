// Pure sharing policy. The caller supplies an authenticated, cancellable API
// function which rejects stale work after every response.
export const presetIds = ['documents', 'music', 'pictures', 'videos', 'templates']

function object(value) {
	return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validFolder(folder) {
	return object(folder) && typeof folder.id === 'string' &&
		typeof folder.paused === 'boolean' && Array.isArray(folder.devices) &&
		folder.devices.every(device => object(device) && typeof device.deviceID === 'string')
}

function validDevice(device) {
	return object(device) && typeof device.deviceID === 'string' &&
		(device.ignoredFolders == null || (Array.isArray(device.ignoredFolders) &&
			device.ignoredFolders.every(folder => object(folder) && typeof folder.id === 'string')))
}

function ignored(device, folderId) {
	return device.ignoredFolders?.some(folder => folder.id === folderId) ?? false
}

export function shortName(value) {
	// St.Label.text is plain text, not markup. Also bound the input itself so
	// long names and line/control characters cannot hide the menu's controls.
	const text = String(value ?? '').replace(/[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/g, ' ')
	const chars = Array.from(text)
	return chars.length > 40 ? chars.slice(0, 39).join('') + '…' : text
}

export async function readSharing(api) {
	const [status, configuredDevices, configuredFolders, connections, pendingDevices, pendingFolders] = await Promise.all([
		api('GET', 'system/status'),
		api('GET', 'config/devices'),
		api('GET', 'config/folders'),
		api('GET', 'system/connections'),
		api('GET', 'cluster/pending/devices'),
		api('GET', 'cluster/pending/folders'),
	])
	if (!object(status) || typeof status.myID !== 'string' ||
		!Array.isArray(configuredDevices) || !configuredDevices.every(validDevice) ||
		!Array.isArray(configuredFolders) || !configuredFolders.every(validFolder) ||
		!object(connections) || !object(connections.connections) ||
		!object(pendingDevices) || !object(pendingFolders))
		throw new Error('Invalid sharing state')
	const folders = configuredFolders.filter(folder => presetIds.includes(folder.id) && !folder.paused)
	const devices = configuredDevices.filter(device => device.deviceID !== status.myID)
	const invitations = Object.keys(pendingDevices).map(id => ({ key: JSON.stringify(['device', id]), matching: false }))
	for (const [folderId, offer] of Object.entries(pendingFolders)) {
		if (!object(offer) || !object(offer.offeredBy))
			throw new Error('Invalid folder invitation')
		for (const deviceId of Object.keys(offer.offeredBy)) {
			const device = devices.find(item => item.deviceID === deviceId)
			if (device && ignored(device, folderId))
				continue
			invitations.push({ key: JSON.stringify(['folder', folderId, deviceId]),
				matching: !!device && folders.some(folder => folder.id === folderId) })
		}
	}
	const peers = await Promise.all(devices.map(async device => {
		const connected = connections.connections[device.deviceID]?.connected === true
		const shares = await Promise.all(folders.map(async folder => {
			const shared = folder.devices.some(item => item.deviceID === device.deviceID)
			const offered = !ignored(device, folder.id) &&
				Object.hasOwn(pendingFolders[folder.id]?.offeredBy ?? {}, device.deviceID)
			let state = shared ? 'shared' : 'notShared'
			if (connected) {
				if (shared) {
					const completion = await api('GET', `db/completion?folder=${encodeURIComponent(folder.id)}&device=${encodeURIComponent(device.deviceID)}`)
					if (!object(completion) || typeof completion.remoteState !== 'string')
						throw new Error('Invalid remote sharing state')
					state = completion.remoteState === 'valid' ? 'sharing' : 'waiting'
				} else if (offered) {
					state = 'offered'
				}
			}
			return { folderId: folder.id, shared, state }
		}))
		return { deviceId: device.deviceID, name: shortName(device.name || device.deviceID), connected, shares }
	}))
	return { peers, invitations }
}

export async function changeSharing(api, folderId, deviceId, share) {
	if (!presetIds.includes(folderId) || typeof deviceId !== 'string' || typeof share !== 'boolean')
		throw new Error('Invalid sharing choice')
	const folderPath = `config/folders/${encodeURIComponent(folderId)}`
	const devicePath = `config/devices/${encodeURIComponent(deviceId)}`
	// Read again at activation: never send a stale menu snapshot back as config.
	const [folder, device, status] = await Promise.all([
		api('GET', folderPath), api('GET', devicePath), api('GET', 'system/status'),
	])
	if (!validFolder(folder) || folder.id !== folderId || folder.paused ||
		!validDevice(device) || device.deviceID !== deviceId ||
		!object(status) || typeof status.myID !== 'string' || status.myID === deviceId)
		throw new Error('The folder or paired device is no longer available')
	const devices = folder.devices.filter(item => item.deviceID !== deviceId)
	if (share)
		devices.push(folder.devices.find(item => item.deviceID === deviceId) ?? { deviceID: deviceId })
	// PATCH only the field we own; all folder settings and other peers survive.
	await api('PATCH', folderPath, { devices })
	// Syncthing discards an ignore while the folder is still shared, so removal
	// MUST precede this write. Its durable ignore survives reconnect and login.
	const latest = await api('GET', devicePath)
	if (!validDevice(latest) || latest.deviceID !== deviceId)
		throw new Error('Invalid paired device')
	const ignoredFolders = (latest.ignoredFolders ?? []).filter(item => item.id !== folderId)
	if (!share)
		ignoredFolders.push({ id: folderId, label: folder.label || folderId, time: new Date().toISOString() })
	await api('PATCH', devicePath, { ignoredFolders })
	const confirmed = await api('GET', folderPath)
	if (!validFolder(confirmed) || confirmed.devices.some(item => item.deviceID === deviceId) !== share)
		throw new Error('Sharing change was not confirmed')
	const confirmedDevice = await api('GET', devicePath)
	if (!validDevice(confirmedDevice) || ignored(confirmedDevice, folderId) === share)
		throw new Error('Invitation choice was not confirmed')
}
