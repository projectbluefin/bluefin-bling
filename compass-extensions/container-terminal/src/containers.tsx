import {
	Action,
	ActionPanel,
	getPreferenceValues,
	Icon,
	List,
	showToast,
	Toast,
} from "@vicinae/api";
import { useEffect, useState } from "react";
import { type Container, type Runtime } from "./discovery.js";
import { listContainers } from "./host-discovery.js";
import { launchInTerminal } from "./terminal.js";
import type { TerminalPrefs } from "./shell.js";

type Preferences = TerminalPrefs & {
	enablePodman: boolean;
	enableDocker: boolean;
	enableDistrobox: boolean;
	includeStopped: boolean;
};

const RUNTIME_LABELS: Record<Runtime, string> = {
	podman: "Podman",
	docker: "Docker",
	distrobox: "Distrobox",
};

const RUNTIME_ICONS: Record<Runtime, string> = {
	podman: "🦭",
	docker: "🐳",
	distrobox: "📦",
};

export default function Containers() {
	const prefs = getPreferenceValues<Preferences>();
	const [containers, setContainers] = useState<Container[]>([]);
	const [isLoading, setIsLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	async function refresh() {
		setIsLoading(true);
		try {
			setContainers(
				await listContainers({
					enablePodman: prefs.enablePodman !== false,
					enableDocker: prefs.enableDocker !== false,
					enableDistrobox: prefs.enableDistrobox !== false,
					includeStopped: prefs.includeStopped === true,
				}),
			);
			setError(null);
		} catch (cause) {
			setError(cause instanceof Error ? cause.message : String(cause));
			setContainers([]);
		} finally {
			setIsLoading(false);
		}
	}

	useEffect(() => {
		void refresh();
	}, []);

	async function open(container: Container) {
		await showToast({
			style: Toast.Style.Animated,
			title: `Opening ${container.name}`,
			message: `${RUNTIME_LABELS[container.runtime]} · ${container.image}`,
		});
		try {
			await launchInTerminal(prefs, container);
			await showToast({
				style: Toast.Style.Success,
				title: `Opened ${container.name} in a terminal`,
			});
		} catch (cause) {
			await showToast({
				style: Toast.Style.Failure,
				title: `Could not open ${container.name}`,
				message: cause instanceof Error ? cause.message : String(cause),
			});
		}
	}

	const runtimes: Runtime[] = ["podman", "docker", "distrobox"];

	return (
		<List
			isLoading={isLoading}
			searchBarPlaceholder="Search containers by name, image or runtime…"
		>
			{containers.length === 0 && !isLoading ? (
				<List.Item
					key="empty"
					title={error ? "Container search failed" : "No containers found"}
					subtitle={
						error ??
						"Enable a runtime in the extension preferences and start a container."
					}
					icon="📭"
				/>
			) : (
				runtimes.map((runtime) => {
					const items = containers.filter((c) => c.runtime === runtime);
					if (items.length === 0) return null;
					return (
						<List.Section key={runtime} title={RUNTIME_LABELS[runtime]}>
							{items.map((container) => (
								<List.Item
									key={`${container.runtime}:${container.name}`}
									title={container.name}
									subtitle={`${container.image} · ${container.status}`}
									icon={RUNTIME_ICONS[container.runtime]}
									keywords={[
										container.runtime,
										container.image,
										container.id,
										container.state,
									]}
									actions={
										<ActionPanel>
											<Action
												title="Open in Terminal"
												icon={Icon.Box}
												onAction={() => open(container)}
											/>
											<Action.CopyToClipboard
												title="Copy Container ID"
												content={container.id}
											/>
											<Action.CopyToClipboard
												title="Copy Container Name"
												content={container.name}
											/>
											<Action
												title="Refresh"
												icon={Icon.Cog}
												onAction={refresh}
											/>
										</ActionPanel>
									}
								/>
							))}
						</List.Section>
					);
				})
			)}
		</List>
	);
}
