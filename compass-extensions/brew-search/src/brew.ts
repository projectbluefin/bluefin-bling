export type PackageKind = "formula" | "cask";

export type BrewPackage = {
	name: string;
	kind: PackageKind;
	description: string;
};

export type IndexEntry = {
	name: string;
	description: string;
};

export type PackageIndex = {
	formulae: IndexEntry[];
	casks: IndexEntry[];
};

export type BrewOptions = {
	includeFormulae: boolean;
	includeCasks: boolean;
};

const MAX_RESULTS = 60;

/**
 * Homebrew's public API uses `name` for formulae and `token` for casks.
 * Keep only what search needs so the cached index stays small.
 */
export function parseIndex(
	payload: unknown,
	kind: PackageKind,
): IndexEntry[] {
	if (!Array.isArray(payload)) return [];
	const nameKey = kind === "cask" ? "token" : "name";
	const entries: IndexEntry[] = [];

	for (const item of payload) {
		if (!item || typeof item !== "object") continue;
		const record = item as Record<string, unknown>;
		const name = record[nameKey];
		if (typeof name !== "string" || name.length === 0) continue;
		const description =
			typeof record.desc === "string"
				? record.desc
				: Array.isArray(record.name) && typeof record.name[0] === "string"
					? record.name[0]
					: "";
		entries.push({ name, description });
	}

	return entries;
}

/** Match name and description, formulae before casks, with a bounded result set. */
export function searchIndex(
	index: PackageIndex,
	query: string,
	options: BrewOptions,
): BrewPackage[] {
	const term = query.trim().toLowerCase();
	if (term.length === 0) return [];

	const find = (entries: IndexEntry[], kind: PackageKind): BrewPackage[] =>
		entries
			.filter(
				(entry) =>
					entry.name.toLowerCase().includes(term) ||
					entry.description.toLowerCase().includes(term),
			)
			.map((entry) => ({ ...entry, kind }));

	const results: BrewPackage[] = [];
	if (options.includeFormulae) results.push(...find(index.formulae, "formula"));
	if (options.includeCasks) results.push(...find(index.casks, "cask"));
	return results.slice(0, MAX_RESULTS);
}

/** Install argv for a searched package. Never interpolates a shell string. */
export function installArgs(pkg: BrewPackage): string[] {
	return pkg.kind === "cask"
		? ["install", "--cask", pkg.name]
		: ["install", pkg.name];
}

export function packageUrl(pkg: BrewPackage): string {
	const section = pkg.kind === "cask" ? "cask" : "formula";
	return `https://formulae.brew.sh/${section}/${pkg.name}`;
}
