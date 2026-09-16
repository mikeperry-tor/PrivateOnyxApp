# Key Stack Patches

This stack carries many patches to Onyx, its overlays, SearXNG, Obscura
Browser and other components.

This document is meant to be an overview of patches that specifically impact
usage and user experience.

The full technical details of these patches are described in the [developer patch documentation](./docs/onyx_patch_info.md).

## Search Engine Patches

We carry several patches to SearXNG and Obscura Browser to improve search engine reliability under agent research workloads.

### SearXNG Anti-Ban Improvements

- Google, Brave, DuckDuckGo, Startpage, and Bing searches use Obscura Browser to submit each provider's search form. Each provider retains its own cookies and stable browser fingerprint between searches, reducing repeated session setup and inconsistent browser signals.
- Searches rotate among available providers and try another provider when an attempt fails or returns no results. Searches to the same provider are serialized and spaced at least three seconds apart, with a one-hour suspension after blocks or rate limits.
- Startpage searches can complete Anubis proof-of-work challenges automatically within the search time limit. Unsupported challenges and CAPTCHAs still cause the provider to be temporarily suspended.
- Bing is an engine of last resort, used only after the other selected providers have failed for that query or are suspended. If its first page has fewer than five valid results, the engine also checks page two and returns up to ten distinct results.
- DuckDuckGo searches use its JavaScript-rendered No-AI search page and wait for results to finish loading. Unexpected or incomplete result pages are reported as failures rather than mistaken for an empty search.
- Onyx's duplicate search retries are disabled so that SearXNG alone decides when to try another provider. Each query tries a provider at most once, avoiding repeated attempts against an already blocked engine.

### Obscura Browser Anti-Ban Improvements

- A browser tab keeps a stable fingerprint across page loads and child frames, helping search providers see a consistent browser throughout a search session.
- Search form submissions preserve the browser's cookies, proxy route, and browser-like connection profile through redirects. This keeps form-based searches consistent with the session that loaded the search page.
- Browser compatibility fixes help modern search scripts run correctly and prevent conflicting modern and legacy scripts from loading together. Frame navigation fixes also ensure that a form or link targets its intended frame.

## Onyx Patches

Onyx has been patched to improve several areas:

### Privacy Improvements

- Onyx telemetry, third-party analytics and error reporting, cloud billing, CAPTCHA, and remote configuration are explicitly disabled.
- A more restrictive browser Content Security Policy now blocks third-party scripts, connections, frames, media, fonts, workers, and remote images from bypassing the stack's selected Tor/VPN/proxy via the user's browser. Additionally, this policy blocks Onyx WebUI queries to a Google favicon service for all sourced URLs in chat and research reports; generic icons are used instead.

### Security Improvements

- Significantly hardened container security and network isolation for all containers.
- The code sandbox is kept up to date with latest Debian security patches.
- The Onyx installation process and the wider stack lifecycle are adapted to additionally support rootless Docker and rootless Podman, including selected-engine image preparation, Compose routing, startup-health handling, and shared-data safeguards when switching between Docker and Podman.
- Optional Slack and Discord bots support chat and search. Slack cannot run code; Discord can use the code tools you enable on Docker. Neither bot uses deep-research mode.

### Performance Improvements

- Stock Onyx discards an entire in-progress chat response when its connection to the inference provider closes or times out. This stack retries from the point of interruption. Each additional continuation requires the model to have produced new answer or reasoning text; two continuation failures cannot run back to back. If recovery fails without progress, the partial response remains visible with a warning instead of the whole chat being lost. (Retry is not attempted during tool calls, because doing so could execute corrupted arguments or produce invalid structured data.)
- Ordinary chat and Deep Research avoid mid-investigation prompt mutations. This vastly improve prompt cache hit rate over stock Onyx. See [scope and cache boundaries](docs/onyx_patch_info.md#investigation-prompt-stability).
- Onyx's idle background CPU workload is reduced by running discovery and housekeeping less often, removing unused monitoring and disabled-feature work, keeping lightweight control processes out of application bootstraps, and keeping optional Slack/Discord bot processes off unless enabled with `ONYX_AGENT_SLACK_BOT` or `ONYX_AGENT_DISCORD_BOT`.
- RAG document re-indexing can skip re-downloading and re-parsing unchanged PDFs from configured trusted local document sources. This reduces repeat indexing work when the files' HTTP metadata is unchanged.

### Open-Weight LLM Compatibility Improvements

- Stock Onyx strips agent reasoning between tool calls for most open-weight LLMs. This causes needless repeated re-thinking and degrades final answer quality. This has been patched.
- Stock Onyx can treat JSON/XML-looking assistant text as a tool call and execute it as a fallback. This stack executes only provider-native structured tool calls and leaves JSON/XML-looking assistant text visible. If that payload appears in chat, the selected model or provider emitted a malformed or non-native tool call; verify that its serving path supports native tool calling.
- Sub-agents are patched to choose whether to call another tool or finish, avoiding a forced-tool compatibility problem with vLLM for open weight models.
- Stock Onyx displays error messages on chats if the browser is backgrounded or loses internet connectivity. We patch it so that recorded chats automatically reload and reattach when a suspended or mobile browser loses the response stream, without resending the prompt. Multi-model chats reconcile after every model finishes. Incognito chats remain non-recoverable and are classified without a recovery reload so their stock teardown behavior is preserved.

### Research Quality Improvements

- Stock Onyx removes query strings (`?`) and fragments (`#`) from web URLs, which can break Hacker News `item?id=...` posts, YouTube `watch?v=...` links, signed links, and other query-addressed pages. This stack preserves complete URLs through search, crawling, citations, and document matching.
- When some pages in a URL retrieval succeed and others fail, the agent receives both the successful content and a report of the failed fetches. This makes gaps in the retrieved evidence visible to the agent.
- URL retrieval explicitly tells the agent to split requests into batches of at most ten distinct URLs. Oversized requests are rejected with guidance instead of silently dropping the extra URLs.
- Stock Onyx strips tool call results upon user follow-up questions, which often makes LLMs think that they hallucinated the previous turn tool results. This has been patched.
- Onyx Agent tool descriptions and prompts have been patched to describe an additional SymPy package, reinforce exact opaque links for Python-generated files, and describe network access in coding environments when it is enabled. Generated-file markdown is normalized to portable same-origin links.
- Stock Onyx caps Deep Research orchestration and research sub-agent responses at roughly 1,000 tokens, which can exhaust the budget on reasoning before any tool calls are produced. This stack removes those caps and Onyx's report-length caps, while provider and model limits still apply.
- The "Deep Research" mode has been patched to provide the research sub-agents with RAG access and all configured tools, rather than the Onyx default of only web search and url retrieval.
- The "Deep Research" mode allows much longer research runs, and has been patched to execute all accepted tool calls when a research agent requests several different tools at once, rather than silently dropping some of them like stock Onyx does.
- The code sub-agent investigation summarization has been enhanced to summarize reasoning steps as well as output.
