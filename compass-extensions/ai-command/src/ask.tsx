import {
	Action,
	ActionPanel,
	Detail,
	getPreferenceValues,
	showToast,
	Toast,
	type LaunchProps,
} from "@vicinae/api";
import { useEffect, useRef, useState } from "react";
import {
	askProvider,
	resolveModel,
	resolveProvider,
	type ProviderPrefs,
} from "./providers.js";

type Arguments = {
	prompt: string;
	provider?: string;
	model?: string;
};

export default function Ask(props: LaunchProps<{ arguments: Arguments }>) {
	const prefs = getPreferenceValues<ProviderPrefs>();
	const provider = resolveProvider(props.arguments.provider, prefs);
	const model = resolveModel(provider, props.arguments.model, prefs);
	const prompt = props.arguments.prompt?.trim() || "";

	const [answer, setAnswer] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);
	const started = useRef(false);

	useEffect(() => {
		if (started.current) return;
		started.current = true;

		if (!prompt) {
			setError("No prompt was provided.");
			setLoading(false);
			return;
		}

		askProvider(
			provider,
			prefs,
			prefs.systemPrompt || "You are a helpful assistant. Answer concisely.",
			prompt,
			model,
		)
			.then((text) => setAnswer(text))
			.catch((cause: unknown) => {
				const message = cause instanceof Error ? cause.message : String(cause);
				setError(message);
				void showToast({
					style: Toast.Style.Failure,
					title: "AI request failed",
					message,
				});
			})
			.finally(() => setLoading(false));
	}, []);

	const markdown = loading
		? `> Thinking with **${provider}** · \`${model}\`…`
		: error
			? `### Request failed\n\n${error}\n\nCheck the source, model, API key and endpoint in the extension preferences.`
			: answer || "The model returned no answer.";

	return (
		<Detail
			markdown={markdown}
			navigationTitle={`${provider} · ${model}`}
			actions={
				<ActionPanel>
					<Action.CopyToClipboard title="Copy answer" content={answer} />
					<Action.CopyToClipboard title="Copy prompt" content={prompt} />
				</ActionPanel>
			}
		/>
	);
}
