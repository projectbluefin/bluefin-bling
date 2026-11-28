export type Runtime = "podman" | "docker" | "distrobox";

export type Container = {
	id: string;
	name: string;
	image: string;
	status: string;
	state: string;
	runtime: Runtime;
};

export type DiscoveryOptions = {
	enablePodman: boolean;
	enableDocker: boolean;
	enableDistrobox: boolean;
	includeStopped: boolean;
};

// Tab separated so image names with spaces cannot shift columns.
const OCI_FORMAT = "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.State}}";

// Local copy so this module stays self-contained for the Node harness, which
// cannot resolve a `.js` value specifier from a TypeScript file.
function quote(word: string): string {
	return `'${word.replace(/'/g, "'\\''")}'`;
}

/**
 * Shell body that writes tagged sections to `outputPath`. It runs on the host
 * through the engine, where the runtime dir, container storage and PATH are
 * available; the worker's Landlock policy does not grant any of those.
 */
export function buildDiscoveryScript(
	outputPath: string,
	options: DiscoveryOptions,
): string {
	const out = quote(outputPath);
	const filter = options.includeStopped ? "" : " --filter status=running";
	const lines: string[] = [`: > ${out}`];

	if (options.enablePodman) {
		lines.push(`printf '==podman==\\n' >> ${out}`);
		lines.push(
			`command -v podman >/dev/null 2>&1 && podman ps${filter} --format ${quote(OCI_FORMAT)} >> ${out} 2>/dev/null || true`,
		);
	}
	if (options.enableDocker) {
		lines.push(`printf '==docker==\\n' >> ${out}`);
		lines.push(
			`command -v docker >/dev/null 2>&1 && docker ps${filter} --format ${quote(OCI_FORMAT)} >> ${out} 2>/dev/null || true`,
		);
	}
	if (options.enableDistrobox) {
		lines.push(`printf '==distrobox==\\n' >> ${out}`);
		lines.push(
			`command -v distrobox >/dev/null 2>&1 && distrobox list --no-color >> ${out} 2>/dev/null || true`,
		);
	}

	return lines.join("\n");
}

function parseOci(text: string, runtime: "podman" | "docker"): Container[] {
	return text
		.split("\n")
		.map((line) => line.trim())
		.filter((line) => line.length > 0)
		.map((line) => line.split("\t"))
		.filter((parts) => parts.length >= 5)
		.map((parts) => ({
			id: parts[0].trim(),
			name: parts[1].trim(),
			image: parts[2].trim(),
			status: parts[3].trim(),
			state: parts[4].trim().toLowerCase(),
			runtime,
		}))
		.filter((container) => container.name.length > 0);
}

/**
 * `distrobox list` prints `ID | NAME | STATUS | IMAGE`. The ID is a hex
 * container ID, not a row number, and the status sits before the image.
 * Parse only data rows so the `ID` header is dropped.
 */
export function parseDistrobox(text: string): Container[] {
	return text
		.split("\n")
		.map((line) => line.trim())
		.filter((line) => line.includes("|"))
		.map((line) => line.split("|").map((part) => part.trim()))
		.filter((parts) => parts.length >= 4 && /^[0-9a-f]+$/i.test(parts[0]))
		.map((parts) => {
			const status = parts[2];
			return {
				id: parts[0],
				name: parts[1],
				image: parts[3],
				status,
				state: /^(up|running)/i.test(status) ? "running" : "exited",
				runtime: "distrobox" as const,
			};
		});
}

/** Split the tagged output and parse each runtime's section. */
export function parseDiscoveryOutput(
	text: string,
	options: DiscoveryOptions,
): Container[] {
	const sections: Record<string, string> = {
		podman: "",
		docker: "",
		distrobox: "",
	};
	let current = "";
	for (const line of text.split("\n")) {
		const marker = /^==(podman|docker|distrobox)==$/.exec(line.trim());
		if (marker) {
			current = marker[1];
			continue;
		}
		if (current) sections[current] += `${line}\n`;
	}

	const found: Container[] = [];
	const seen = new Set<string>();
	const add = (containers: Container[]) => {
		for (const container of containers) {
			if (!options.includeStopped && container.state !== "running") continue;
			const key = `${container.runtime}:${container.name}`;
			if (seen.has(key)) continue;
			seen.add(key);
			found.push(container);
		}
	};

	if (options.enablePodman) add(parseOci(sections.podman, "podman"));
	if (options.enableDocker) add(parseOci(sections.docker, "docker"));
	if (options.enableDistrobox) add(parseDistrobox(sections.distrobox));

	return found.sort(
		(a, b) =>
			a.runtime.localeCompare(b.runtime) || a.name.localeCompare(b.name),
	);
}
