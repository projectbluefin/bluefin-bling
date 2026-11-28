export type Provider = "openai" | "anthropic" | "ollama" | "openrouter" | "hearth";

export type ProviderPrefs = {
	defaultProvider?: string;
	defaultModel?: string;
	systemPrompt?: string;
	openaiApiKey?: string;
	openrouterApiKey?: string;
	anthropicApiKey?: string;
	hearthApiKey?: string;
	openaiBaseUrl?: string;
	hearthBaseUrl?: string;
	ollamaBaseUrl?: string;
};

export const PROVIDERS: Provider[] = [
	"openai",
	"openrouter",
	"anthropic",
	"ollama",
	"hearth",
];

const FALLBACK_MODELS: Record<Provider, string> = {
	openai: "gpt-4o-mini",
	openrouter: "openai/gpt-4o-mini",
	anthropic: "claude-3-5-haiku-latest",
	ollama: "llama3.2",
	hearth: "default",
};

function trimTrailingSlash(url: string): string {
	return url.replace(/\/+$/, "");
}

export function resolveProvider(value: string | undefined, prefs: ProviderPrefs): Provider {
	const chosen = (value || prefs.defaultProvider || "openai").toLowerCase();
	return PROVIDERS.includes(chosen as Provider) ? (chosen as Provider) : "openai";
}

export function resolveModel(
	provider: Provider,
	value: string | undefined,
	prefs: ProviderPrefs,
): string {
	// A model name is provider-specific. Only apply the global default when the
	// selected source is the default one; otherwise use that provider's fallback.
	const useDefault = provider === resolveProvider(undefined, prefs);
	return (
		value?.trim() ||
		(useDefault ? prefs.defaultModel?.trim() : "") ||
		FALLBACK_MODELS[provider]
	);
}

function endpoint(provider: Provider, prefs: ProviderPrefs): string {
	switch (provider) {
		case "openai":
			return trimTrailingSlash(prefs.openaiBaseUrl || "https://api.openai.com/v1");
		case "openrouter":
			return "https://openrouter.ai/api/v1";
		case "anthropic":
			return "https://api.anthropic.com";
		case "ollama":
			return trimTrailingSlash(prefs.ollamaBaseUrl || "http://localhost:11434");
		case "hearth":
			return trimTrailingSlash(prefs.hearthBaseUrl || "http://localhost:8080/v1");
	}
}

function apiKey(provider: Provider, prefs: ProviderPrefs): string {
	switch (provider) {
		case "openai":
			return prefs.openaiApiKey || "";
		case "hearth":
			// Never reuse the OpenAI key: the Hearth base URL is user-supplied
			// and may be a third-party or plain-text endpoint.
			return prefs.hearthApiKey || "";
		case "openrouter":
			return prefs.openrouterApiKey || "";
		case "anthropic":
			return prefs.anthropicApiKey || "";
		case "ollama":
			return "";
	}
}

async function readError(response: Response): Promise<string> {
	const body = await response.text().catch(() => "");
	try {
		const parsed = JSON.parse(body) as { error?: { message?: string } | string };
		const error = parsed?.error;
		if (typeof error === "string") return error;
		if (error?.message) return error.message;
	} catch {
		// Not JSON; fall back to the trimmed body below.
	}
	return body.trim() || `HTTP ${response.status}`;
}

async function openAiCompatible(
	provider: Provider,
	prefs: ProviderPrefs,
	system: string,
	prompt: string,
	model: string,
): Promise<string> {
	const headers: Record<string, string> = { "Content-Type": "application/json" };
	const key = apiKey(provider, prefs);
	if (key) headers.Authorization = `Bearer ${key}`;

	const response = await fetch(`${endpoint(provider, prefs)}/chat/completions`, {
		method: "POST",
		headers,
		body: JSON.stringify({
			model,
			stream: false,
			messages: [
				{ role: "system", content: system },
				{ role: "user", content: prompt },
			],
		}),
	});

	if (!response.ok) throw new Error(await readError(response));
	const data = (await response.json()) as {
		choices?: Array<{ message?: { content?: string }; text?: string }>;
	};
	const answer = data?.choices?.[0]?.message?.content ?? data?.choices?.[0]?.text;
	if (!answer) throw new Error("The model returned an empty response.");
	return answer;
}

async function anthropic(
	prefs: ProviderPrefs,
	system: string,
	prompt: string,
	model: string,
): Promise<string> {
	const response = await fetch(`${endpoint("anthropic", prefs)}/v1/messages`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			"x-api-key": apiKey("anthropic", prefs),
			"anthropic-version": "2023-06-01",
		},
		body: JSON.stringify({
			model,
			max_tokens: 4096,
			system,
			messages: [{ role: "user", content: prompt }],
		}),
	});

	if (!response.ok) throw new Error(await readError(response));
	const data = (await response.json()) as {
		content?: Array<{ text?: string }>;
	};
	const answer = data?.content?.map((block) => block.text || "").join("").trim();
	if (!answer) throw new Error("The model returned an empty response.");
	return answer;
}

async function ollama(
	prefs: ProviderPrefs,
	system: string,
	prompt: string,
	model: string,
): Promise<string> {
	const response = await fetch(`${endpoint("ollama", prefs)}/api/chat`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			model,
			stream: false,
			messages: [
				{ role: "system", content: system },
				{ role: "user", content: prompt },
			],
		}),
	});

	if (!response.ok) throw new Error(await readError(response));
	const data = (await response.json()) as { message?: { content?: string } };
	const answer = data?.message?.content;
	if (!answer) throw new Error("The model returned an empty response.");
	return answer;
}

/** Send a prompt to the selected source and return the model's text answer. */
export async function askProvider(
	provider: Provider,
	prefs: ProviderPrefs,
	system: string,
	prompt: string,
	model: string,
): Promise<string> {
	switch (provider) {
		case "anthropic":
			return anthropic(prefs, system, prompt, model);
		case "ollama":
			return ollama(prefs, system, prompt, model);
		default:
			return openAiCompatible(provider, prefs, system, prompt, model);
	}
}
