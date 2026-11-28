import {
	Action,
	ActionPanel,
	getPreferenceValues,
	Icon,
	List,
	open,
	runInTerminal,
	showToast,
	Toast,
	type LaunchProps,
} from "@vicinae/api";
import { useEffect, useState } from "react";
import {
	installArgs,
	packageUrl,
	searchIndex,
	type BrewPackage,
} from "./brew.js";
import { loadPackageIndex } from "./index.js";

type Preferences = {
	brewPath: string;
	includeFormulae: boolean;
	includeCasks: boolean;
};

export default function Packages(props: LaunchProps) {
	const prefs = getPreferenceValues<Preferences>();
	// `compass cmd launch -q <text>` arrives as fallbackText; treating it as the
	// initial search makes the command scriptable and testable.
	const [query, setQuery] = useState(props.fallbackText || "");
	const [packages, setPackages] = useState<BrewPackage[]>([]);
	const [isLoading, setIsLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		const term = query.trim();
		if (term.length === 0) {
			setPackages([]);
			setError(null);
			setIsLoading(false);
			return;
		}

		let stale = false;
		setIsLoading(true);
		const timer = setTimeout(() => {
			loadPackageIndex()
				.then((index) => {
					if (stale) return;
					setPackages(
						searchIndex(index, term, {
							includeFormulae: prefs.includeFormulae !== false,
							includeCasks: prefs.includeCasks !== false,
						}),
					);
					setError(null);
				})
				.catch((cause: unknown) => {
					if (stale) return;
					setError(cause instanceof Error ? cause.message : String(cause));
					setPackages([]);
				})
				.finally(() => {
					if (!stale) setIsLoading(false);
				});
		}, 200);

		return () => {
			stale = true;
			clearTimeout(timer);
		};
	}, [query, prefs.includeFormulae, prefs.includeCasks]);

	async function install(pkg: BrewPackage) {
		await showToast({
			style: Toast.Style.Animated,
			title: `Installing ${pkg.name}`,
			message: `${pkg.kind} · follow the progress in the terminal`,
		});
		try {
			await runInTerminal(
				[prefs.brewPath || "brew", ...installArgs(pkg)],
				{ hold: true, title: `brew install ${pkg.name}` },
			);
			await showToast({
				style: Toast.Style.Success,
				title: `Started installing ${pkg.name}`,
			});
		} catch (cause) {
			await showToast({
				style: Toast.Style.Failure,
				title: `Could not install ${pkg.name}`,
				message: cause instanceof Error ? cause.message : String(cause),
			});
		}
	}

	return (
		<List
			isLoading={isLoading}
			searchBarPlaceholder="Search formulae and casks…"
			onSearchTextChange={setQuery}
		>
			{query.trim().length === 0 ? (
				<List.Item
					key="hint"
					title="Type to search Homebrew"
					subtitle="Searches formulae and casks on formulae.brew.sh, then installs with brew install."
					icon="🍺"
				/>
			) : error ? (
				<List.Item
					key="error"
					title="Homebrew index unavailable"
					subtitle={error}
					icon="⚠️"
				/>
			) : packages.length === 0 && !isLoading ? (
				<List.Item
					key="empty"
					title="No packages found"
					subtitle={`Nothing matched “${query.trim()}”.`}
					icon="📭"
				/>
			) : (
				<List.Section title="Results">
					{packages.map((pkg) => (
						<List.Item
							key={`${pkg.kind}:${pkg.name}`}
							title={pkg.name}
							subtitle={
								pkg.description ||
								(pkg.kind === "cask" ? "Desktop app" : "Command-line formula")
							}
							icon={pkg.kind === "cask" ? "📦" : "⚙️"}
							keywords={[pkg.kind, "brew", "homebrew"]}
							actions={
								<ActionPanel>
									<Action
										title={`Install ${pkg.name}`}
										icon={Icon.Box}
										onAction={() => install(pkg)}
									/>
									<Action
										title="Open on formulae.brew.sh"
										icon={Icon.Terminal}
										onAction={() => open(packageUrl(pkg))}
									/>
									<Action.CopyToClipboard
										title="Copy install command"
										content={`brew install ${pkg.kind === "cask" ? "--cask " : ""}${pkg.name}`}
									/>
								</ActionPanel>
							}
						/>
					))}
				</List.Section>
			)}
		</List>
	);
}
