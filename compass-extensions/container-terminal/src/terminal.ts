import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { environment, runInTerminal } from "@vicinae/api";
import type { Container } from "./discovery.js";
import {
	expandTemplate,
	shellCommandFor,
	terminalTemplate,
	type TerminalPrefs,
} from "./shell.js";

/**
 * Launch the chosen terminal through Compass's engine rather than spawning a
 * GUI process from the worker. The worker's environment has no DISPLAY,
 * WAYLAND_DISPLAY or XDG_RUNTIME_DIR, so a terminal started there cannot reach
 * the session. The wrapper script is written to the extension's writable
 * support directory and executed by the engine, which has the session
 * environment; the script deletes itself and detaches the chosen terminal
 * with `setsid -f`, so the temporary terminal closes immediately.
 */
export async function launchInTerminal(
	prefs: TerminalPrefs,
	container: Container,
): Promise<void> {
	const command = shellCommandFor(container, prefs.containerShell || "");
	const full = expandTemplate(terminalTemplate(prefs), command).replace(
		/[\r\n]+/g,
		" ",
	);
	const scriptPath = join(environment.supportPath, `open-${randomUUID()}.sh`);

	// Detach the chosen terminal so the helper window closes immediately, and
	// remove the wrapper so launches do not accumulate files in supportPath.
	await writeFile(
		scriptPath,
		`#!/bin/sh\nrm -f "$0"\nsetsid -f ${full} >/dev/null 2>&1\n`,
		{ mode: 0o700 },
	);

	// A failure here means the engine could not start the temporary terminal;
	// it rejects so the caller can show a failure toast instead of a success.
	await runInTerminal([scriptPath], {
		hold: false,
		title: `Container: ${container.name}`,
	});
}
