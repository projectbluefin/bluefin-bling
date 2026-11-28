import type { Container } from "./discovery.js";

export type TerminalPrefs = {
	terminal?: string;
	customTerminalTemplate?: string;
	containerShell?: string;
};

/** Single-quote a word for POSIX shells, including embedded quotes. */
export function shellQuote(word: string): string {
	return `'${word.replace(/'/g, "'\\''")}'`;
}

/**
 * Build the command that opens a shell in the chosen container. A stopped
 * container is a compound command: it must be handed to the terminal as one
 * argument, otherwise the outer shell splits on `&&` and only the start runs.
 */
export function shellCommandFor(container: Container, shell: string): string {
	const bin = shellQuote(container.runtime);

	if (container.runtime === "distrobox") {
		return `distrobox enter ${shellQuote(container.name)}`;
	}

	const target = shellQuote(container.id);
	const shellBin = shellQuote(shell.trim() || "/bin/sh");
	const exec = `${bin} exec -it ${target} ${shellBin}`;

	return container.state === "running"
		? exec
		: `/bin/sh -c ${shellQuote(`${bin} start ${target} && ${exec}`)}`;
}

/** Resolve the user's terminal preference to a {command} template. */
export function terminalTemplate(prefs: TerminalPrefs): string {
	const chosen = prefs.terminal || "ghostty -e {command}";
	if (chosen === "custom") {
		return prefs.customTerminalTemplate || "ghostty -e {command}";
	}
	return chosen;
}

/** Expand {command} in a terminal template into a complete shell command. */
export function expandTemplate(template: string, command: string): string {
	const rendered = template.includes("{command}")
		? template.split("{command}").join(command)
		: `${template} ${command}`;
	return rendered.trim();
}
