// Standalone child process: synchronous I/O must never run in GNOME Shell.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

const OWNER = 'bluefin-bling/syncthing-toggle';
const MARKER = '# Managed by bluefin-bling syncthing-toggle; do not replace with an unrelated unit.';
const UID = new Gio.Credentials().get_unix_user();
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', {fatal: true});
const ATTRIBUTES = 'standard::type,standard::is-symlink,unix::uid,unix::mode,unix::nlink';
const NATIVE_UNIT_PATHS = ['/usr/lib/systemd/user/syncthing.service', '/lib/systemd/user/syncthing.service'];

function file(path) {
    return Gio.File.new_for_path(path);
}

function info(path) {
    try {
        return file(path).query_info(ATTRIBUTES, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
    } catch (error) {
        if (error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
            return null;
        throw error;
    }
}

function absolutePath(path) {
    if (typeof path !== 'string' || !GLib.path_is_absolute(path) || /[\x00-\x1f\x7f:]/.test(path))
        throw new Error('Paths must be absolute and contain no control characters or colons');
    return GLib.canonicalize_filename(path, null);
}

// Never follow a credential or configuration symlink, including its ancestors.
function directory(path, privateMode = false) {
    const parent = GLib.path_get_dirname(path);
    if (parent !== path)
        directory(parent);
    let entry = info(path);
    if (!entry) {
        file(path).make_directory(null);
        file(path).set_attribute_uint32('unix::mode', 0o700, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
        entry = info(path);
    }
    if (entry.get_is_symlink() || entry.get_file_type() !== Gio.FileType.DIRECTORY)
        throw new Error('Configuration directory is not a real directory');
    const mode = entry.get_attribute_uint32('unix::mode');
    // Sticky system temporary directories are safe parents for isolated test homes.
    if ((mode & 0o022) && !(mode & 0o1000))
        throw new Error('Configuration directory has unsafe writable ancestors');
    if (privateMode) {
        if (entry.get_attribute_uint32('unix::uid') !== UID)
            throw new Error('Private directory is not owned by the current user');
        file(path).set_attribute_uint32('unix::mode', 0o700, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
    }
}

function ownedFile(path) {
    const entry = info(path);
    if (entry && (entry.get_is_symlink() || entry.get_file_type() !== Gio.FileType.REGULAR ||
        entry.get_attribute_uint32('unix::uid') !== UID || entry.get_attribute_uint32('unix::nlink') !== 1))
        throw new Error('Managed file is not a safe, singly linked file owned by the current user');
    return entry;
}

function readOwned(path) {
    if (!ownedFile(path))
        return null;
    const [ok, contents] = file(path).load_contents(null);
    if (!ok)
        throw new Error('Could not read managed file');
    return decoder.decode(contents);
}

function writePrivate(path, contents) {
    ownedFile(path);
    file(path).replace_contents(encoder.encode(contents), null, false,
        Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    file(path).set_attribute_uint32('unix::mode', 0o600, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
}

function command(argv, allowFailure = false) {
    const process = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
    const [ok, stdout] = process.communicate_utf8(null, null);
    if (!ok || !process.get_successful()) {
        if (allowFailure)
            return null;
        const error = new Error('Required system lookup failed');
        error.exitStatus = ok && process.get_if_exited() ? process.get_exit_status() : null;
        throw error;
    }
    return stdout.replace(/\n$/, '');
}

function userDirectory(type) {
    // The XDG file is shell syntax. Older xdg-user-dir scripts leave its
    // config path unquoted and re-evaluate directory values, losing literal quotes.
    const lookup = 'config="${XDG_CONFIG_HOME:-$HOME/.config}/user-dirs.dirs"\n' +
        '[ -f "$config" ] || exit 0\n' +
        '. "$config"\n' +
        `printf '%s\\n' "\${XDG_${type}_DIR-}"\n`;
    return command(['/usr/bin/sh', '-c', lookup]);
}

function legacyRunning(serviceName) {
    const output = command(['/usr/bin/timeout', '--kill-after=2', '5', '/usr/bin/systemctl',
        '--user', 'show', serviceName, '--property=ActiveState', '--property=MainPID'], true);
    if (output === null)
        return false;
    const properties = Object.fromEntries(output.split('\n').map(line => line.split('=')));
    if (properties.ActiveState !== 'active' || !/^[1-9]\d*$/.test(properties.MainPID ?? ''))
        return false;
    try {
        const processPath = `/proc/${properties.MainPID}`;
        if (info(processPath)?.get_attribute_uint32('unix::uid') !== UID)
            throw new Error('Service process is not owned by the current user');
        // A daemon-reload can change FragmentPath while the old native process still runs.
        return GLib.path_get_basename(GLib.file_read_link(`${processPath}/exe`).replace(/ \(deleted\)$/, '')) === 'syncthing';
    } catch (_) {
        throw new Error('Could not inspect the active service process');
    }
}

function knownNativeUnit(serviceName, activeRequired = false) {
    if (serviceName !== 'syncthing.service')
        return false;
    const output = command(['/usr/bin/timeout', '--kill-after=2', '5', '/usr/bin/systemctl',
        '--user', 'show', serviceName, '--property=LoadState', '--property=FragmentPath',
        '--property=DropInPaths', '--property=ActiveState'], true);
    if (output === null)
        return false;
    const properties = Object.fromEntries(output.split('\n').map(line => line.split('=')));
    const states = activeRequired ? ['active'] : ['inactive', 'failed', 'activating'];
    if (properties.LoadState !== 'loaded' || properties.DropInPaths !== '' ||
        !states.includes(properties.ActiveState) ||
        !NATIVE_UNIT_PATHS.includes(properties.FragmentPath))
        return false;
    const entry = info(properties.FragmentPath);
    if (!entry || entry.get_is_symlink() || entry.get_file_type() !== Gio.FileType.REGULAR ||
        entry.get_attribute_uint32('unix::uid') !== 0 || (entry.get_attribute_uint32('unix::mode') & 0o022))
        return false;
    const resolved = command(['/usr/bin/realpath', '-e', '--', properties.FragmentPath], true);
    if (!NATIVE_UNIT_PATHS.includes(resolved))
        return false;
    // Resolve the packaged /lib alias, but never trust user-writable unit ancestors.
    let parent = GLib.path_get_dirname(resolved);
    while (true) {
        const ancestor = info(parent);
        if (!ancestor || ancestor.get_is_symlink() || ancestor.get_file_type() !== Gio.FileType.DIRECTORY ||
            ancestor.get_attribute_uint32('unix::uid') !== 0 || (ancestor.get_attribute_uint32('unix::mode') & 0o022))
            return false;
        const next = GLib.path_get_dirname(parent);
        if (next === parent)
            break;
        parent = next;
    }
    return true;
}

function legacyFolders(stateDir, image, gid, home, waitForNative = false) {
    const name = `syncthing-folder-inspection-${UID}-${GLib.uuid_string_random()}`;
    try {
        // conmon's timeout and --rm also bound cleanup if this helper is cancelled.
        let output;
        try {
            output = command(['/usr/bin/timeout', '--kill-after=2', '20', '/usr/bin/podman',
                'run', '--rm', '--name', name, '--timeout=20', '--network=host', '--userns=keep-id',
                '--user', `${UID}:${gid}`, '--security-opt=label=disable', '--security-opt=no-new-privileges',
                '--cap-drop=all', '--log-driver=none', '--env=HOME=/var/syncthing',
                '--env=STHOMEDIR=/var/syncthing/config', '--env=STGUIADDRESS=', '--env=STGUIAPIKEY=',
                '--volume', `${stateDir}:/var/syncthing/config:ro`, image,
                'cli', 'config', 'dump-json']);
        } catch (error) {
            // Only a requested, temporary native start may wait for the CLI endpoint.
            // Never retry spawn/image errors or the JSON/path validation below.
            if (waitForNative && error.exitStatus === 1)
                return null;
            throw error;
        }
        // The root dump includes private GUI values; only folder paths leave this helper.
        const configuration = JSON.parse(output);
        const folders = configuration?.folders;
        if (!Array.isArray(folders) || folders.some(folder => !folder || typeof folder.path !== 'string'))
            throw new Error('Invalid legacy folder response');
        const paths = [];
        for (const folder of folders) {
            const path = absolutePath(folder.path);
            const target = existingDirectory(path, home);
            if (target)
                paths.push(target.path);
            else if (!file(path).query_exists(null) ||
                file(path).query_file_type(Gio.FileQueryInfoFlags.NONE, null) !== Gio.FileType.DIRECTORY)
                throw new Error('A legacy folder is unavailable');
            // Home/root and ancestors remain excluded by the mount safety policy.
        }
        return [...new Set(paths)];
    } catch (_) {
        // Neither REST output nor daemon credentials may appear in helper errors.
        throw new Error('Could not safely inspect legacy folders; leave the native service running and retry');
    } finally {
        command(['/usr/bin/timeout', '--kill-after=2', '5', '/usr/bin/podman',
            'rm', '--force', '--time=1', '--ignore', name], true);
    }
}

function randomKey() {
    const stream = file('/dev/urandom').read(null);
    const chunks = [];
    let remaining = 32;
    try {
        while (remaining > 0) {
            const bytes = stream.read_bytes(remaining, null).get_data();
            if (!bytes.length)
                throw new Error('OS random source returned no bytes');
            chunks.push(...bytes);
            remaining -= bytes.length;
        }
    } finally {
        stream.close(null);
    }
    return chunks.map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function apiKey(contents) {
    if (contents === null)
        return randomKey();
    const lines = contents.trimEnd().split('\n');
    const key = lines.find(line => line.startsWith('STGUIAPIKEY='))?.slice(12);
    if (lines.length !== 2 || !/^[a-f0-9]{64}$/.test(key ?? '') ||
        !lines.some(line => /^STGUIADDRESS=(?:http:\/\/)?127\.0\.0\.1:\d+$/.test(line)))
        throw new Error('Existing credential file is not a managed environment file');
    return key;
}

function quote(value) {
    // Quadlet removes these quotes; %% survives systemd's specifier expansion.
    return `"${value.replaceAll('\\', '\\\\').replaceAll('"', '\\"').replaceAll('%', '%%')}"`;
}

function existingDirectory(path, home) {
    if (!path)
        return null;
    const canonical = absolutePath(path);
    // Ordinary data directories may be symlinks; resolve to reject a disguised home/root mount.
    if (!file(canonical).query_exists(null))
        return null;
    if (file(canonical).query_file_type(Gio.FileQueryInfoFlags.NONE, null) !== Gio.FileType.DIRECTORY)
        return null;
    const resolved = command(['/usr/bin/realpath', '-e', '--', canonical]);
    if (resolved === home || resolved === '/' || home.startsWith(`${resolved}/`))
        return null;
    return {path: canonical, resolved};
}

function nativeAutostartLink(serviceName) {
    if (serviceName !== 'syncthing.service')
        return null;
    const path = GLib.build_filenamev([GLib.get_user_config_dir(), 'systemd', 'user',
        'default.target.wants', serviceName]);
    const entry = info(path);
    if (!entry?.get_is_symlink())
        return null;
    const target = GLib.file_read_link(path);
    const resolved = GLib.canonicalize_filename(target, GLib.path_get_dirname(path));
    if (!NATIVE_UNIT_PATHS.includes(resolved))
        return null;
    directory(GLib.path_get_dirname(path));
    if (entry.get_attribute_uint32('unix::uid') !== UID ||
        info(GLib.path_get_dirname(path)).get_attribute_uint32('unix::uid') !== UID)
        throw new Error('Native autostart link is not owned by the current user');
    return {path, target};
}

function removeNativeAutostartLink(serviceName, nativeLink) {
    if (!nativeLink)
        return;
    const current = nativeAutostartLink(serviceName);
    if (!current || current.target !== nativeLink.target)
        throw new Error('Native autostart link changed during setup');
    file(nativeLink.path).delete(null);
}

function prepare(request) {
    if (!['prepare', 'autostart', 'provisioned'].includes(request.action) ||
        !Number.isInteger(request.port) || request.port < 1 || request.port > 65535 ||
        typeof request.serviceName !== 'string' ||
        !/^[a-zA-Z0-9_][a-zA-Z0-9_.:@-]*\.service$/.test(request.serviceName))
        throw new Error('Invalid deployment request');
    if (request.action === 'autostart' && typeof request.enabled !== 'boolean')
        throw new Error('Autostart requires a boolean enabled value');
    if (request.extraPaths !== undefined && !Array.isArray(request.extraPaths))
        throw new Error('extraPaths must be an absolute path array');
    if (request.waitForNative !== undefined && typeof request.waitForNative !== 'boolean')
        throw new Error('waitForNative requires a boolean value');

    const stateDir = absolutePath(GLib.build_filenamev([GLib.get_user_state_dir(), 'syncthing']));
    const quadletDir = absolutePath(GLib.build_filenamev([GLib.get_user_config_dir(), 'containers', 'systemd']));
    directory(stateDir, true);
    directory(quadletDir);
    if (info(quadletDir).get_attribute_uint32('unix::uid') !== UID)
        throw new Error('Quadlet directory is not owned by the current user');
    const envFile = GLib.build_filenamev([stateDir, 'container.env']);
    const metadataFile = GLib.build_filenamev([stateDir, 'desktop.json']);
    const quadletFile = GLib.build_filenamev([quadletDir, `${request.serviceName.slice(0, -8)}.container`]);
    const previousQuadlet = readOwned(quadletFile);
    if (previousQuadlet !== null && previousQuadlet.split('\n')[0] !== MARKER)
        throw new Error('Refusing to overwrite an unrelated Quadlet definition');
    const previousMetadata = readOwned(metadataFile);
    let metadata = previousMetadata === null ? {
        managedBy: OWNER, provisioned: false, autostart: false, extraPaths: [],
    } : JSON.parse(previousMetadata);
    if (metadata.managedBy !== OWNER || typeof metadata.provisioned !== 'boolean' ||
        typeof metadata.autostart !== 'boolean' || !Array.isArray(metadata.extraPaths) ||
        (metadata.legacyPaths !== undefined && !Array.isArray(metadata.legacyPaths)) ||
        (metadata.legacyCaptured !== undefined && typeof metadata.legacyCaptured !== 'boolean') ||
        (metadata.restartRequired !== undefined && typeof metadata.restartRequired !== 'boolean'))
        throw new Error('Existing desktop metadata is not a managed configuration');
    const nativeLink = nativeAutostartLink(request.serviceName);
    if (nativeLink)
        metadata = {...metadata, autostart: true};
    const nativeActive = legacyRunning(request.serviceName);
    const uninspectedLegacy = previousMetadata === null &&
        file(GLib.build_filenamev([stateDir, 'config.xml'])).query_exists(null);
    if (!nativeActive && uninspectedLegacy) {
        const response = {stateDir, envFile, folders: [], provisioned: false,
            documentsAvailable: false, restartRequired: false, nativeStartRequired: false};
        // Off must not fabricate migration metadata that would skip later inspection.
        if (request.action === 'autostart' && request.enabled === false) {
            removeNativeAutostartLink(request.serviceName, nativeLink);
            return response;
        }
        if (request.action !== 'prepare' || !knownNativeUnit(request.serviceName))
            throw new Error('Existing Syncthing state cannot be inspected safely; restore the packaged syncthing.service without overrides, then retry');
        // The caller owns this temporary start; no managed credentials or units exist yet.
        return {...response, nativeStartRequired: true, restartRequired: true};
    }
    const home = command(['/usr/bin/realpath', '-e', '--', GLib.get_home_dir()]);
    const gid = command(['/usr/bin/id', '-g']);
    if (!/^\d+$/.test(gid))
        throw new Error('Could not resolve current user group');
    const templateFile = Gio.File.new_for_uri(import.meta.url).get_parent().get_child('syncthing.container.in');
    const [ok, templateBytes] = templateFile.load_contents(null);
    if (!ok)
        throw new Error('Could not load container template');
    let template = decoder.decode(templateBytes);
    const image = template.match(/^Image=(\S+)$/m)?.[1];
    if (!image)
        throw new Error('Container template has no image');
    let legacyPaths = (metadata.legacyPaths ?? []).map(absolutePath);
    if (nativeActive) {
        const waitForNative = request.action === 'prepare' && request.waitForNative === true &&
            uninspectedLegacy && knownNativeUnit(request.serviceName, true);
        const capturedPaths = legacyFolders(stateDir, image, gid, home, waitForNative);
        if (capturedPaths === null)
            return {stateDir, envFile, folders: [], provisioned: false, documentsAvailable: false,
                restartRequired: true, nativeStartRequired: true};
        legacyPaths = [...new Set([...legacyPaths, ...capturedPaths])];
        metadata = {...metadata, legacyCaptured: true, restartRequired: true};
    }
    // Explicit extras replace the requested list, never the captured native folders.
    const extraPaths = [...new Set([...legacyPaths,
        ...(request.extraPaths ?? metadata.extraPaths).map(absolutePath)])];
    const definitions = [
        ['documents', 'Documents', 'DOCUMENTS'],
        ['desktop', 'Desktop', 'DESKTOP'],
        ['downloads', 'Downloads', 'DOWNLOAD'],
        ['music', 'Music', 'MUSIC'],
        ['pictures', 'Pictures', 'PICTURES'],
        ['videos', 'Videos', 'VIDEOS'],
        ['templates', 'Templates', 'TEMPLATES'],
        ['public-share', 'Public', 'PUBLICSHARE'],
    ];
    const folders = [];
    const mounts = new Map();
    let documentsAvailable = false;
    for (const [id, label, type] of definitions) {
        const target = existingDirectory(userDirectory(type), home);
        if (!target)
            continue;
        if (id === 'documents')
            documentsAvailable = true;
        if (mounts.has(target.resolved))
            continue;
        mounts.set(target.resolved, target.path);
        folders.push({id, label, path: target.path, paused: id !== 'documents'});
    }
    for (const path of extraPaths) {
        const target = existingDirectory(path, home);
        if (!target) {
            if (legacyPaths.includes(path))
                throw new Error('A captured legacy folder is unavailable or unsafe');
            continue;
        }
        // Preserve legacy config paths even when they are symlink aliases of an XDG directory.
        if (!Array.from(mounts.values()).includes(target.path))
            mounts.set(`extra:${target.path}`, target.path);
    }
    const previousEnv = readOwned(envFile);
    const key = apiKey(previousEnv);
    if (request.action === 'autostart')
        metadata = {...metadata, autostart: request.enabled};
    if (request.action === 'provisioned')
        metadata = {...metadata, provisioned: true};
    metadata = {...metadata, extraPaths, legacyPaths};
    const replacements = {
        UID: String(UID), GID: gid,
        HOSTNAME: quote(GLib.get_host_name()),
        ENVFILE: quote(envFile),
        // Volume is a raw colon-separated Quadlet value, NOT a shell argument.
        STATEVOLUME: `${stateDir.replaceAll('%', '%%')}:/var/syncthing/config:rw`,
        FOLDERVOLUMES: [...new Set(mounts.values())].map(path => `Volume=${path.replaceAll('%', '%%')}:${path.replaceAll('%', '%%')}:rw`).join('\n'),
        INSTALL: metadata.autostart ? '\n[Install]\nWantedBy=default.target' : '',
    };
    for (const [name, value] of Object.entries(replacements))
        template = template.replaceAll(`@${name}@`, () => value);
    const environment = `STGUIAPIKEY=${key}\nSTGUIADDRESS=http://127.0.0.1:${request.port}\n`;
    const restartRequired = request.action !== 'provisioned' &&
        (metadata.restartRequired === true || nativeActive ||
        previousQuadlet !== template || previousEnv !== environment);
    metadata = {...metadata, restartRequired};
    // Persist the migration snapshot/pending restart before changing runtime configuration.
    writePrivate(metadataFile, `${JSON.stringify(metadata, null, 2)}\n`);
    writePrivate(envFile, environment);
    writePrivate(quadletFile, template);
    removeNativeAutostartLink(request.serviceName, nativeLink);
    return {stateDir, envFile, folders, provisioned: metadata.provisioned, documentsAvailable,
        restartRequired, nativeStartRequired: false};
}

try {
    if (ARGV.length !== 1)
        throw new Error('Expected one JSON deployment request');
    print(JSON.stringify(prepare(JSON.parse(ARGV[0]))));
} catch (error) {
    // Error details may contain parsed credentials; never echo the request, file contents or stack.
    printerr(`Sync Folder deployment failed: ${error instanceof SyntaxError ? 'Invalid configuration JSON' : error.message}`);
    System.exit(1);
}
