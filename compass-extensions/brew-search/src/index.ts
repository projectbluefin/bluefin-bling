import { Cache } from "@vicinae/api";
import { parseIndex, type PackageIndex } from "./brew.js";

const CACHE_KEY = "formulae.brew.sh-v1";
const DAY_MS = 24 * 60 * 60 * 1000;

// The worker cannot execute Homebrew under /home/linuxbrew (Landlock grants
// execute only under the system trees). It can reach the network, so search
// uses Homebrew's public JSON API and caches the compact index on disk.
const cache = new Cache({
	namespace: "brew-index",
	ttl: DAY_MS,
	capacity: 12 * 1024 * 1024,
});

async function fetchJson(url: string): Promise<unknown> {
	const response = await fetch(url);
	if (!response.ok) {
		throw new Error(`Homebrew index returned HTTP ${response.status}`);
	}
	return response.json();
}

/** Load the formulae/cask index, refreshing it at most once a day. */
export async function loadPackageIndex(): Promise<PackageIndex> {
	const cached = cache.get(CACHE_KEY);
	if (cached) {
		try {
			return JSON.parse(cached) as PackageIndex;
		} catch {
			cache.remove(CACHE_KEY);
		}
	}

	// Fetch sequentially: holding both full JSON trees at once pushes a
	// 160 MiB worker heap too hard. Parse and release each before the next.
	const formulae = parseIndex(
		await fetchJson("https://formulae.brew.sh/api/formula.json"),
		"formula",
	);
	const casks = parseIndex(
		await fetchJson("https://formulae.brew.sh/api/cask.json"),
		"cask",
	);

	const index: PackageIndex = { formulae, casks };
	cache.set(CACHE_KEY, JSON.stringify(index));
	return index;
}
