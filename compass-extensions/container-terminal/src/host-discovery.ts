import {
	buildDiscoveryScript,
	parseDiscoveryOutput,
	type Container,
	type DiscoveryOptions,
} from "./discovery.js";
import { runHostScript } from "./host.js";

/** List containers by running the CLIs on the host through Compass's engine. */
export async function listContainers(
	options: DiscoveryOptions,
): Promise<Container[]> {
	const output = await runHostScript(
		(out) => buildDiscoveryScript(out, options),
		"Container discovery",
	);
	return parseDiscoveryOutput(output, options);
}
