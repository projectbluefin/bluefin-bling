import { randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { environment, runInTerminal } from "@vicinae/api";
import { shellQuote } from "./shell.js";

const TIMEOUT_MS = 10_000;
const POLL_MS = 100;

/**
 * Run a shell body on the host, outside the worker's Landlock policy, and
 * return its stdout. The helper terminal exits at once: the script deletes
 * itself and detaches the real work with `setsid -f`. The detached process
 * inherits the engine's full environment and writes its result into the
 * extension's writable support directory.
 */
export async function runHostScript(
	build: (outputPath: string) => string,
	title: string,
): Promise<string> {
	const id = randomUUID();
	const script = join(environment.supportPath, `host-${id}.sh`);
	const output = join(environment.supportPath, `host-${id}.out`);
	const done = join(environment.supportPath, `host-${id}.done`);

	// Bound the host work below the 10s poll deadline. Without this, a slow
	// first podman start could outlive the timeout and recreate the output and
	// done files after `finally` removed them, leaking temporary files.
	// The exit status goes into the done marker so a timeout (124, or 137 when
	// `-k` has to SIGKILL) is reported instead of passing off partial output.
	const body = `timeout -k 1 8 /bin/sh -c ${shellQuote(build(output))}\nprintf '%s' "$?" > ${shellQuote(done)}`;
	await writeFile(
		script,
		`#!/bin/sh\nrm -f "$0"\nsetsid -f /bin/sh -c ${shellQuote(body)} >/dev/null 2>&1\n`,
		{ mode: 0o700 },
	);

	try {
		await runInTerminal([script], { hold: false, title });
		const deadline = Date.now() + TIMEOUT_MS;
		while (Date.now() < deadline) {
			let status: string;
			try {
				status = (await readFile(done, "utf8")).trim();
			} catch {
				// The detached command has not finished yet.
				await new Promise((resolve) => setTimeout(resolve, POLL_MS));
				continue;
			}
			if (status === "") {
				// `> done` creates the file before printf writes the status;
				// an empty marker is not a finished command. Poll again.
				await new Promise((resolve) => setTimeout(resolve, POLL_MS));
				continue;
			}
			if (status === "124" || status === "137") {
				throw new Error(`Timed out waiting for ${title.toLowerCase()}`);
			}
			return await readFile(output, "utf8");
		}
		throw new Error(`Timed out waiting for ${title.toLowerCase()}`);
	} finally {
		await Promise.all(
			[script, output, done].map((path) => rm(path, { force: true })),
		);
	}
}
