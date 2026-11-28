// Exercises the pure modules behind the Compass extensions. No engine is
// needed: these modules deliberately avoid importing @vicinae/api.
import { parseIndex, searchIndex } from "../compass-extensions/brew-search/src/brew.ts";
import {
	buildDiscoveryScript,
	parseDistrobox,
	parseDiscoveryOutput,
} from "../compass-extensions/container-terminal/src/discovery.ts";
import {
	expandTemplate,
	shellCommandFor,
} from "../compass-extensions/container-terminal/src/shell.ts";
import { resolveModel, resolveProvider } from "../compass-extensions/ai-command/src/providers.ts";

const scenario = process.argv[2];

const scenarios = {
	distrobox() {
		return parseDistrobox(
			[
				"ID                                   | NAME       | STATUS      | IMAGE",
				"------------------------------------- | ---------- | ----------- | -----",
				"3f2a1b9c7d8e                           | fedora-box | Up 2 hours  | quay.io/fedora/fedora-toolbox:41",
				"aabbccddeeff                           | old-box    | Created     | ubuntu:24.04",
			].join("\n"),
		);
	},

	"discovery-output"() {
		const options = {
			enablePodman: true,
			enableDocker: true,
			enableDistrobox: true,
			includeStopped: false,
		};
		const text = [
			"==podman==",
			"abc123\tweb\tnginx\tUp 2 hours\trunning",
			"dead00\told\tnginx\tExited 1 hour ago\texited",
			"==docker==",
			"==distrobox==",
			"ID | NAME | STATUS | IMAGE",
			"3f2a1b9c7d8e | fedora-box | Up 2 hours | quay.io/fedora/fedora-toolbox:41",
		].join("\n");
		return {
			containers: parseDiscoveryOutput(text, options),
			script: buildDiscoveryScript("/tmp/out", options),
		};
	},

	"stopped-container"() {
		const container = {
			id: "abc123",
			name: "web",
			image: "nginx",
			status: "Exited",
			state: "exited",
			runtime: "podman",
		};
		const command = shellCommandFor(container, "/bin/sh");
		return {
			command,
			rendered: expandTemplate("ghostty -e {command}", command),
		};
	},

	"brew-cask-only"() {
		const index = {
			formulae: parseIndex([{ name: "other", desc: "unrelated" }], "formula"),
			casks: parseIndex(
				[{ token: "firefox", name: ["Firefox"], desc: "Web browser" }],
				"cask",
			),
		};
		return {
			index,
			results: searchIndex(index, "firefox", {
				includeFormulae: true,
				includeCasks: true,
			}),
		};
	},

	"model-resolution"() {
		const prefs = {
			defaultProvider: "openai",
			defaultModel: "gpt-4o-mini",
		};
		return {
			openai: resolveModel(
				resolveProvider("openai", prefs),
				undefined,
				prefs,
			),
			anthropic: resolveModel(
				resolveProvider("anthropic", prefs),
				undefined,
				prefs,
			),
			ollama: resolveModel(
				resolveProvider("ollama", prefs),
				undefined,
				prefs,
			),
		};
	},
};

if (!scenarios[scenario]) {
	console.error(`unknown scenario: ${scenario}`);
	process.exit(2);
}

console.log(JSON.stringify(scenarios[scenario]()));
