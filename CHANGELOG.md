# Changelog

All notable changes to Synthelion are documented here.

---

## [Unreleased]

---

## [1.4.5] - 2026-07-29

### Fixed
- **Numeric values preserved across compression levels** — ports, amounts, percentages, versions, dates and temperatures were being discarded during semantic, aggressive, statistical and syntactic filtering. Numeric tokens are now always retained regardless of compression level.

---

## [1.2.4] — 2026-07-22

### Added — local privacy/compression proxy
- **`synthelion serve-proxy`** — a new, strictly opt-in (`proxy.enabled` default `False`)
  local HTTP reverse proxy closing the enforcement gap the MCP/hook
  integrations can't: Cursor and Aider have no way to force a model to call a
  privacy tool. Point `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` at the proxy
  instead and masking/compression happen server-side, before the request
  ever reaches the real provider — a risky prompt gets a hard `400` block,
  never forwarded, same posture as Claude Code's `block_on_risk` hook.
- **Schema-agnostic compression**: recursively compresses every string value
  in the request JSON regardless of which field it's under — works for
  Anthropic, OpenAI-compatible (Groq/OpenRouter/Together/Azure OpenAI/
  Mistral/DeepSeek/xAI/local shims), Gemini, and custom routes without
  hardcoding any one provider's exact wire format.
- **Automatic failover**, up to 10 backup providers deep — connection
  refused, DNS failure, TLS error, timeout, `429`, or `5xx` all trigger a
  retry against the next configured upstream, before the client ever sees a
  failure. **Circuit breaker** stops hammering an already-rate-limited
  upstream after N failures in a window, cooling down before retrying it.
- **Firewall-protected**: the same WAF gating the dashboard (SQLi/XSS/path-
  traversal/command-injection inspection, IP allow/block, auto-ban, rate
  limiting) now gates every proxy request too.
- **Rolling-history compression** — once a conversation reaches a turn
  threshold, older turns compress at `aggressive` while recent ones stay at
  the configured default, without merging or dropping any message (stays
  valid for every provider's exact schema).
- **CCR (reversible compression)**, opt-in — compressed-away text can be
  cached and retrieved later via the new `synthelion retrieve --token ...`
  CLI command or the `retrieve_compressed_text` MCP tool.
- **Exact-match response cache**, opt-in — identical `(upstream, path, body)`
  within a TTL is served locally instead of re-calling the provider.
  Deliberately not embedding-similarity/semantic caching, consistent with
  Synthelion's zero-ML-models stance.
- **Daily budget cap** (`proxy.daily_budget_usd`) and **output shaping**
  (appends a "be terse" instruction to the system prompt for Anthropic/
  OpenAI-shaped requests), both opt-in.
- **Structured, prompt-free request log** (`synthelion/analytics/proxy_log.py`)
  — timestamp, method, path, upstream, status, duration, blocked, tokens
  before/after. The prompt/response/masked text are never written anywhere.
- **Dashboard "Proxy" page** — Status / Routes / Reliability / Advanced /
  Logs tabs; every proxy setting (all of the above, plus custom routes with
  a "pick a provider" convenience sourced from a live provider list fetched
  on demand) is editable without touching `config.json` by hand.

### Added — `synthelion launch`
- One command starts the proxy and launches an agent already pointed at it
  (`synthelion launch claude|codex|aider`; `cursor` prints the config to
  paste in, since it's an IDE, not a spawnable CLI) — the single-command
  equivalent of running `serve-proxy` in one terminal and setting
  `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` by hand in another.

### Added — cross-agent shared memory
- **`synthelion memory add/list/clear`** and MCP tools `memory_add`/
  `memory_recall` — small, durable, deduplicated (exact-content-hash) notes
  visible to every agent, not just the one that wrote them. Not RAG/semantic
  search over a large corpus — that already exists per-session via the
  vector-store-backed session memory.

### Added — `synthelion learn`
- Deterministic pattern mining over the savings ledger and proxy log — no
  ML, no fabricated insights: only things actually true about recent
  activity (a tool averaging low compression efficiency, repeated privacy
  blocks on the same category, an upstream failing repeatedly). Appends
  findings to `CLAUDE.md`/`AGENTS.md` as plain markdown.

### Added — new agent integrations (`synthelion install --agent ...`)
- **Cursor**: MCP registration + a mandatory privacy Rule (`.mdc`) + a
  best-effort `beforeSubmitPrompt` hook. Cursor's hook is documented as
  informational-only in the current beta (it can't block/rewrite the
  prompt), so this only logs usage to the savings ledger — enforcement
  still depends on the model choosing to call the MCP tool, same limitation
  as any tool-use-only agent.
- **Aider**: an advisory `CONVENTIONS.md`-style file registered via
  `.aider.conf.yml`'s `read:` key. Aider has no MCP client and no pre-send
  hook, so this cannot mask or block anything automatically — it only asks
  the model to warn the user and suggest running `synthelion compress`
  manually; documented plainly rather than overstating what it does.
- **OpenAI Codex CLI**: MCP registration in `config.toml`'s
  `[mcp_servers.synthelion]` + a mandatory privacy instruction merged into
  `~/.codex/AGENTS.md`. An experimental hook (`--experimental-hooks`,
  `[features].codex_hooks` + `hooks.json`) is available but off by default —
  Codex's hook engine is still marked "under development" upstream with an
  unconfirmed payload schema.
- Standalone installer scripts (`install_cursor.py/.ps1/.sh`,
  `install_aider.py/.ps1/.sh`, `install_codex.py/.ps1/.sh`), matching the
  existing `install_claude.py`/`install_opencode.py` pattern, alongside the
  same functionality integrated into `synthelion install --agent
  cursor|aider|codex` (with `--uninstall` support for all three).

### Added — smarter self-upgrade
- **`synthelion upgrade`** now detects how Synthelion was actually installed
  (plain pip, `pip --user`, pipx, `uv tool`, or an editable/git checkout) and
  runs the command that really applies, instead of always shelling out to
  `pip install --upgrade` — which silently no-oped on an editable install
  and wasn't the idiomatic path for pipx/uv-tool installs. New `--check` flag
  reports the latest PyPI version without upgrading.

### Fixed
- **`synthelion version` / `synthelion --version`** — there was no version
  command or flag at all; `synthelion version` errored with "invalid
  choice." Both now work, and print the same `synthelion.__version__`.
- **Dashboard self-restart after upgrade** — clicking "Upgrade now" ran `pip
  install --upgrade` but the *running* dashboard process kept serving the
  old code (Python doesn't hot-reload already-imported modules); a new
  "Restart dashboard now" button re-execs the process in place (same host/
  port) so the upgrade actually takes effect without a manual
  kill-and-relaunch.
- **`pip install -e .` mid-session regression**: reinstalling editable while
  a dashboard process from the old non-editable install was still running
  deleted that process's on-disk HTML/JS/CSS assets out from under it
  (`_serve_file` reads them fresh per request), causing every page —
  including `/login` — to 404. Documented as a standing gotcha; the fix is
  simply to restart the dashboard right after any reinstall.
- **`/proxy` dashboard page 404** — the new Proxy page's sidebar link,
  client-side router entry, and page content were added, but the
  server-side `_PAGE_ROUTES` allow-list was never updated to include it,
  so the page 404'd until this was caught by an actual Playwright
  screenshot pass (not by the API-level curl testing done throughout
  development, which never touched page routing).
- **Duplicate `Server`/`Date` response headers** on both the proxy's live
  forward path and its response-cache replay path — `send_response()`
  already injects fresh ones; forwarding/replaying the upstream's own copies
  on top produced two of each in every response.

### Version
- Bumped `1.2.3` → `1.2.4` in `synthelion/__init__.py` and `pyproject.toml`
  (previously out of sync after the 1.2.4-targeted OpenCode plugin work
  shipped without the version bump).

## [1.2.3] — 2026-07-21

### Added — PrivacyGuard block-on-risk
- **`privacy.block_on_risk`** (default `False`) and **`privacy.block_min_score`**
  (default `61` = High/Critical) — new opt-in config that rejects a prompt
  outright instead of masking-and-continuing once its PII risk score crosses
  the threshold. Configurable from the dashboard's Privacy page or directly
  in `config.json`.
- **Claude Code hook**: when triggered, the `UserPromptSubmit` hook now
  returns `decision: "block"` with the full PII/privacy breakdown + AI Act
  Art.50 notice as the `reason` — the prompt never reaches the model, closing
  the gap where a masked-and-continued hook could only *add* context
  alongside the untouched original prompt. Updated in `install_claude.py`/
  `.ps1`/`.sh` and the local hook install.
- **All other agent entry points get the same guard, not just the Claude
  Code hook**: `RagAgent.prepare_message` (shared by `ClaudeAdapter`,
  `OpenAIAdapter`, `CrewAIAdapter`) now runs the PrivacyGuard pre-pass itself
  and raises `PrivacyBlockedError` on block; the MCP/OpenAI-function
  `compress` tool (used by Claude Code's MCP server, OpenCode, and any other
  MCP/OpenAI function-calling client) got the same pre-pass added — it had
  none before. `synthelion.privacy_analyzer.build_privacy_notice` is the new
  single formatter shared by the CLI, the adapters, and the MCP tool, so the
  PII/AI-Act disclosure is identical everywhere.

---

### Added — CrewAI integration
- **`synthelion.integrations.crewai_adapter.CrewAIAdapter`** — a CrewAI helper
  mirroring `ClaudeAdapter`/`OpenAIAdapter` (`chat`/`store`/`recall`/`status`/
  `reset`). Each `chat()` call compresses the outgoing message through the
  `ContentRouter`, injects recalled decisions from the session DB, and runs a
  one-shot CrewAI Agent + Task + Crew (`crew.kickoff()`), returning a
  `CrewAIResponse` with `content`/`tokens_saved`/`savings_pct`. Delegates all
  memory to the shared `RagAgent`, so savings land in the same ledger as every
  other integration.
- **`synthelion.integrations.crewai_adapter.get_tools()`** — CrewAI-native
  `crewai.tools.BaseTool` wrappers (compress, detect_language, route_content,
  summarize, session_record, session_recall, status, compress_for_context,
  deduplicate, compress_file) so a CrewAI agent/crew can compress messages or
  tool output on the fly, mirroring the LangChain tool wrappers.
- New optional extra **`synthelion[crewai]`** (adds `crewai>=1.15,<2`), also
  folded into `synthelion[all]`.

---

## [1.2.2] — 2026-07-21

A comparative audit against several other prompt/context-compression projects
surfaced techniques Synthelion didn't have yet. Everything below was
reimplemented from scratch in Python, consistent with Synthelion's existing
zero-ML-models, zero-network-call design — nothing here pulls in an embedding
model or an external service. **15 new MCP tools, 41 total.**

### Added — PrivacyGuard: PII detection, masking, prompt-injection guard
A direct Python port of [Caveman.PrivacyGuard](https://github.com/francescopaolopassaro/Caveman.PrivacyGuard)
(C#) — same rule set, same scoring formula, same ~30 checksum validators, not a
thinner reimplementation.
- **`synthelion.privacy_analyzer.PrivacyAnalyzer`** — detects PII across 33
  country/region rule sets (27 EU + UK, Switzerland, China, Russia, Ukraine),
  51 detection rules (email, phone, IBAN, credit cards, national tax/ID
  numbers, GPS coordinates, vehicle plates, JWTs/secrets, ...). Real
  algorithmic checksum validation for ~30 categories (`synthelion.privacy_validators`
  — IBAN, Luhn, Italian CF/P.IVA, French NIR, Polish PESEL, German Steuer-ID,
  UK NINO, Swiss AHV, Chinese ID, Russian INN, and more), not just format
  regexes. Scores 0-100 with a risk level, maps GDPR/EU AI Act/NIS2/PCI-DSS/
  NIST compliance flags, optionally masks detected values.
- **`synthelion.privacy_session.PrivacySession`** — session-based masking with
  recoverable `[PG_n]` placeholders: mask PII before it reaches a model,
  restore the originals client-side once the response comes back.
- **`synthelion.prompt_injection_guard.PromptInjectionGuard`** — heuristic
  screening for prompt-injection/jailbreak attempts (instruction override,
  system-prompt exfiltration, role hijack, delimiter injection, encoded
  payloads, exfiltration coercion) before untrusted text reaches an LLM.
- **`synthelion.ai_transparency_notice`** — localized (en/it/de/fr/es)
  "you're talking to an AI" disclosure message, supporting EU AI Act Art.50
  transparency obligations.
- **New `privacy.*` config section** (`enabled`, `auto_masking`,
  `prompt_injection_guard`, `language`, `ai_transparency_notice`) — a master
  switch plus per-feature toggles, editable from the dashboard's new
  Settings → "Privacy & Security" card. `enabled: true`/`auto_masking: true`
  by default.
- **Integrated into the `compress` CLI command from the start** — the exact
  command the `UserPromptSubmit` Claude Code hook already calls. PII is masked
  *before* NLP compression (the `[PG_n]` placeholders survive compression
  untouched — they match no stopword list in any language), so masking is
  active for every hook-driven prompt the moment Synthelion is installed, not
  an opt-in a user has to discover. `privacy.enabled: false` restores exactly
  the pre-1.2.2 behavior.
- New MCP tools **`analyze_privacy`**, **`restore_privacy_text`**,
  **`check_prompt_injection`**, **`get_ai_transparency_notice`**.
- New dashboard KPI **"PII items masked"** (cumulative, from the same ledger
  `synthelion status`/`gain` already read).
- New dependency: `pyyaml` (parses the copied `privacy_rules.yaml` — the
  hand-rolled mini-parser already in `word_provider.py` is purpose-built for
  the much simpler worddata schema and doesn't handle nested rule documents).

### Added — credential-shape detection before persisting to disk
- **`synthelion.sensitive_guard.find_sensitive(text)`** — regex-based detector
  for AWS access keys, GitHub/Slack tokens, PEM private-key blocks, Bearer
  auth headers, and bulk `.env`-style secret dumps (3+ `KEY=value` lines whose
  key mentions SECRET/TOKEN/PASSWORD/APIKEY). `SessionDB.record_decision()`
  now scans `text` before it reaches any backend (ChromaDB/Qdrant/lexical
  JSONL) and replaces a match with a redaction placeholder instead of
  persisting it verbatim — the only path in Synthelion that writes arbitrary
  agent-supplied text to disk indefinitely. New MCP tool
  **`check_sensitive_content`**.

### Added — terminal noise cleanup and low-signal command collapsing
- **`synthelion.terminal_noise.strip_ansi_noise(text)`** — strips ANSI escape
  codes, isolated braille-spinner-frame lines (npm/yarn/vite/cargo style),
  Unicode/ASCII progress-bar lines, and collapses `\r`-driven in-place
  terminal overwrites to what a real terminal would actually be showing.
  Runs in `ContentRouter` before content-type detection, so detection itself
  isn't thrown off by escape sequences.
- **`synthelion.success_collapse`** — for a small registry of known low-signal
  commands (`git push`/`pull`, `npm install`/`ci`, `yarn install`,
  `pip install`, `docker build`/`push`, `terraform apply`/`plan`) that
  completed with exit code 0, collapses the (often long) output to 1-3
  salient facts ("added 42 packages in 3s, 2 vulnerabilities") instead of
  routing it through generic compression. Returns `None` when nothing
  recognizable is found — never fabricates a summary. `ContentRouter.route()`
  gained optional `command`/`exit_code` parameters (default `None`, fully
  backward-compatible) to opt in.

### Added — tool-list relevance pruning
- **`filter_relevant_tools(query, top_k=10)`** — scores each tool's
  name+description against a task/query using the existing lexical
  `RelevanceFilter` (no new ranking algorithm) and keeps only the `top_k` most
  relevant, in stable original order. For orchestrators that build their own
  per-turn `tools=[...]` array for an LLM — this MCP server's own `tools/list`
  has no per-turn query in the protocol, so this is exposed as a library
  function plus a standalone tool an agent can call explicitly. New MCP tool
  **`list_relevant_tools`**.

### Added — masking of old tool output, with an Artifact Index
- **`synthelion.output_mask`** — `mask_old_outputs(outputs, keep_last=3)`
  replaces all but the most recent tool-call outputs in a chronological list
  with a short placeholder, storing the original verbatim in a hash-keyed,
  TTL'd store (`OutputMaskStore`) for later exact recall — unlike
  `SharedContext`, never rewrites/compresses the content, only decides
  whether a caller sees it or a stand-in. The store also keeps a lightweight
  **Artifact Index**: a catalog of everything masked so far, grouped by tool,
  rendered as a text block meant to be re-injected into context so the model
  knows what was hidden and can ask for it back by hash — returned
  automatically alongside `mask_old_tool_output`'s response, and available
  on demand. New MCP tools **`mask_old_tool_output`**,
  **`expand_masked_output`**, **`get_artifact_index`**.

### Added — diff-on-repeat for identical tool calls
- **`synthelion.repeat_diff.RepeatOutputDiffer`** — when the same tool is
  called again with identical arguments (the same fingerprint `LoopGuard`
  already computes to decide whether to block a retry loop), returns a
  unified diff against the previous call's output instead of the full text
  again — but only when the diff is actually shorter; a separate module from
  `LoopGuard` itself (that one decides *whether* to block, this one decides
  *how* to render a repeat), reusing its fingerprint function so both agree
  on what counts as "the same call". New MCP tool **`diff_tool_output`**.

### Added — chain-depth collapsing for single JSON objects
- `JsonCrusher` used to be a complete no-op on a single JSON object (only
  arrays got structural compression) — a nested config object fell all the
  way through to generic NLP compression. Now collapses chains of
  single-key nested objects into dot-path lines (`{"a":{"b":{"c":"x"}}}` →
  `a.b.c: x`), with a guard that leaves anything shaped like a real
  JSON-Schema object (`type`/`enum`/`minimum`/`maximum`/`pattern` alongside
  `properties`, at any level) untouched. `ContentRouter` now has a
  `ContentType.JSON_OBJECT` branch that tries this before falling back to
  NLP compression.

### Added — adaptive compression scaling by content size
- `ContentRouter` now scales itself automatically for very large input: past
  ~5,000 estimated tokens, NLP compression escalates one level more
  aggressive (capped at `AGGRESSIVE` — never overrides an explicit
  `STATISTICAL`/`SYNTACTIC` choice) and `JsonCrusher`'s row-keep cap tightens
  to 60%; past ~25,000 tokens, two levels more aggressive and the cap
  tightens to 35%. Computed per-call from local values, never mutates shared
  instance state, so concurrent calls of different sizes never interfere.

### Added — advisory command-rewrite suggestions
- **`synthelion.command_rewrite.rewrite_command(command)`** — for a small
  registry of known commands, suggests a less verbose variant with identical
  semantics and exit code (`git log` → `git --no-pager log`, `npm install` →
  adds `--no-fund --no-audit`, `pip install` → adds `--quiet`) — purely
  advisory, Synthelion never executes anything itself anywhere in this
  codebase and this is no exception. Refuses to rewrite any composite/
  non-attestable command (`&&`, `|`, `;`, backticks, `$()`, redirects). New
  MCP tool **`rewrite_command`**.

### Added — response-style guidance (output-side, not context-side)
- **`synthelion.response_style.get_style_guidance(level, language)`** — returns
  a block of verbosity-reduction instructions (no filler openings, no
  restating the question, a structured bug-fix format at `full`/`ultra`) meant
  to be injected into an agent's own system prompt. A different axis from
  every other Synthelion capability: it shapes what the model *generates*,
  not what enters its context — there's no existing text to compress at the
  point a caller would use this. Three escalating levels (`lite`/`full`/
  `ultra`); appends a CJK-specific note when `language` is `zho`/`jpn`/`kor`,
  since common tokenizers spend meaningfully more tokens per character there.
  New MCP tool **`get_response_style_guidance`**.

### Added — file-read freshness tracking (provider cache-breakpoint aware)
- **`synthelion.read_lifecycle.ReadLifecycleTracker`** — tracks file
  read/write tool calls per session and classifies each tracked read as
  `fresh`, `stale` (a write landed after it), or `superseded` (the file's
  been read again since, with no intervening write). `should_mature()` only
  flags a stale/superseded read as safe to collapse into a compact marker
  after `quiesce_turns` turns of silence on that path — a file still being
  actively edited would just get invalidated again next turn, sitting right
  at a provider's KV-cache breakpoint, so maturation deliberately waits for
  the file to settle first. New MCP tools **`track_file_read`**,
  **`track_file_write`**, **`check_read_maturity`**.

### Dashboard
- **Settings**: new "Dashboard" card exposing `dashboard.{host,port,realtime,
  websocket_port}` (previously the only config section not editable from the
  UI) — with an explicit note that a restart is required to take effect,
  since the running server doesn't rebind itself.
- **4 new KPIs on the Overview page** (15 total): Cost saved, Energy saved,
  Tokens processed, and Decisions stored — all from data `ledger.summary()`/
  `storage-status` already computed but never surfaced.
- **Tooltips** on every KPI card (existing and new) — Bootstrap's own
  tooltip component was already vendored and loaded but never initialized.
- **Doctor and Version split into their own pages** (same banner-header style
  as Settings), each with a dedicated sidenav entry, instead of being two
  cards buried at the bottom of Settings.
- **Fixed**: Settings' text-input labels visually overlapped their value
  whenever populated via `loadSettings()` — Material Dashboard's floating
  label only activates on manual blur (`is-filled` class), which JS-set
  `.value` never triggers. Now applied programmatically right after
  populating each field.
- **Fixed**: wide content (the Settings card grid, wide tables) was only
  reachable by Tab-focusing through it — no visible, usable scrollbar.
  `overflow: scroll` (not `auto`) on `html`/`body`/`.main-content`, plus a
  slim, always-visible "modern" scrollbar styled site-wide (Windows' overlay
  scrollbars otherwise hide an `auto` scrollbar until mid-interaction).

## [1.2.1] — 2026-07-20

### Dashboard — full rewrite: login, multi-page admin panel, cluster
- **Login page** (session cookie, not HTTP Basic Auth): default `admin`/`admin`,
  change with `synthelion dashboard-passwd`. Credentials are salted+hashed
  (PBKDF2-HMAC-SHA256), never stored in plaintext; changing the password
  invalidates every session already logged in on that running process.
- UI restyled on [Material Dashboard Free](https://www.creative-tim.com/product/material-dashboard)
  by Creative Tim (MIT, vendored locally — no CDN); login/dashboard structure
  mapped from the template's `sign-up.html`/`dashboard.html`, own SVG icons
  (the template's bundled Nucleo icon font turned out to be corrupted
  upstream — verified in three independently-fetched copies, so it was
  dropped rather than shipped broken).
- **Split into real, separately-routed pages** (Overview, Charts, Sessions,
  Recent requests, Decisions, Settings, Profile, Notifications, Cluster) —
  client-side routed with `history.pushState`, not one long scrolling page.
- **Notifications**: real, never-fabricated health signals (default password
  still set, a configured backend's Python package isn't installed) — bell
  icon dropdown + dedicated page.
- **Profile**: change username/password from the UI; account info; the same
  notifications feed.
- **Settings**: default compression level, default project-wiki depth (new,
  see below), session-store/vector-store backend selection, live storage
  counts, a **Doctor** panel (`synthelion doctor`'s checks, one click), and
  **Version** (PyPI check only on click — never automatic — with an
  "Upgrade now" button).
- **Sessions/Decisions cleanup**: per-row delete, and one-click retention
  cleanup (10/20/30 days) — new `SavingsLedger.prune_older_than()` /
  `.delete_session()` and `SessionDB.prune_older_than()`.
- **Cluster page** (new, see below).

### Cluster — master/slave fleet management
A lightweight node-identity layer, independent of (and complementary to) the
existing shared-backend replica model: a node becomes a **master**
(`synthelion cluster init` or the dashboard's "Become master" button),
generating a node ID and a shared token; other nodes **join** with that token
(`synthelion cluster join <url> --token ...`, prompts for standalone-vs-join
if run with no arguments, or the dashboard's "Join a master" form) and appear
in the master's live node table (calls, tokens saved, version, up/stale). A
joining slave auto-registers on startup and heartbeats every 30s
(`SYNTHELION_ROLE=slave` + `SYNTHELION_MASTER_URL=...` env vars are enough —
no manual join step needed in a container). Node-to-node calls authenticate
with the shared token (`Authorization: Bearer`), a separate scheme from the
browser session cookie — rotating the token disconnects every node until they
re-join. One-click `docker-compose.yml` / Kubernetes manifest downloads for
this topology (env-var based, no secret baked into the file). New module
`synthelion/cluster.py`, `synthelion/analytics/cluster_registry.py`; new
`cluster.*` config section with `SYNTHELION_ROLE`/`SYNTHELION_NODE_ID`/
`SYNTHELION_NODE_TOKEN`/`SYNTHELION_MASTER_URL`/`SYNTHELION_SELF_URL` env
overrides.

### Project Wiki — configurable depth (1-4)
`ProjectWiki.generate(..., depth=1-4)` (CLI `synthelion wiki --depth`, MCP
`generate_project_wiki`'s `depth` argument, and a dashboard Settings default):
1 = overview + file tree only, 2 = standard (previous default, unchanged
output), 3 = wider symbol ranking, 4 = adds short code excerpts for the most
symbol-dense files. An explicit `depth` argument always wins over the
configured default, from both the CLI and MCP.

### Cache alignment — prompt rewriting, not just diagnosis
New `CacheAligner.align()` (MCP tool `align_cache_prompt`): rewrites a system
prompt so blocks containing volatile tokens (UUIDs, timestamps, JWTs, hashes)
sink after the stable blocks, instead of only reporting where they are
(`scan()`, existing) — a stable prefix is what actually lets a provider reuse
its KV-cache.

### Loop guard — agent stuck-in-a-retry-loop guardrail
New `LoopGuard` (MCP tools `check_tool_loop`/`reset_tool_loop`): blocks a tool
call that repeats an identical prior call (same tool + arguments) more than
`max_repeats` times in a row. A `PersistentLoopGuard` variant
(`synthelion loop-check`/`loop-reset` CLI commands) persists history to
`~/.synthelion/loop_guard.jsonl` for use as an external shell hook, since a
hook script is a fresh process every invocation and can't share the MCP
tools' in-memory history.

### Configurable defaults
New `compression.default_level` and `wiki.default_depth` config keys (set via
the dashboard Settings page or `~/.synthelion/config.json` directly) — used
by the CLI and MCP tools whenever a caller doesn't pass an explicit
`--level`/`level`/`--depth`/`depth`.

### Fixed
- **`ledger.py` and `session_db.py` computed their default storage directory
  from `Path.home()` at module import time**, not lazily — meaning tests (and
  anything else) that patch `Path.home()` after import had no effect, and
  those modules' default-directory callers were silently reading/writing the
  real `~/.synthelion` regardless. Found via a test asserting an exact record
  count that didn't match; fixed by resolving `Path.home()` at the point of
  use in both modules (same class of bug, and fix, as `loop_guard.py`'s
  `PersistentLoopGuard` default path a few days earlier).
- CLI `--level`/`--depth` defaults are now resolved from config rather than a
  hardcoded literal, fixing `synthelion compress`/`wiki` to actually respect
  a configured default instead of always using `semantic`/`2`.

### Dashboard (earlier 1.2.x work)
- Replaced the "Est. cost saved" KPI with **"CO₂ saved"** (`co2_mg_saved`,
  aggregated in `ledger.summary()` from the same per-token estimate
  `CompressionResult.estimated_co2_saved_mg` already uses, so it stays
  consistent with the per-call figure shown by the Claude Code hook's
  `systemMessage`). `cost_usd_saved` is still returned by the API for
  existing consumers (`synthelion status`/`gain`), just no longer a
  dashboard KPI card.
- Added a **version badge** next to the dashboard title (`/api/version`,
  reading `synthelion.__version__`) — also fixed that version string, which
  had drifted to the stale `"1.1.0"` while `pyproject.toml` had already moved
  to `1.2.0`.

## [1.2.0] — 2026-07-18

### Fixed (found via real multi-language text review: Italian/English/Chinese negation quality pass)
- **Negation particles silently dropped at every compression level** ("non"/"not"/
  "ne...pas"/"no"/"nicht"/"não"/"不" were classified as ordinary function words
  and stripped like any stopword — not a fluency loss, a meaning inversion
  ("non c'era sensibilità" -> "c'era sensibilità"). Added a small closed
  per-language negation-particle set (`_NEGATION_WORDS` in core.py) folded into
  the existing proper-noun "always keep verbatim" pass, covering
  eng/ita/fra/spa/deu/por/zho. For Chinese specifically, negation is a bound
  prefix morpheme ("不是"/"不管" are themselves dictionary words containing
  "不") — added prefix matching for zho so compound negations survive too.
- **Italian "subito" (adverb, "immediately") mis-lemmatised to "subire" (verb,
  "to undergo")** — same class of UD homograph-contamination bug as the
  earlier "torta"->"torcere" fix. The existing POS tagger already correctly
  tags "subito" as `ADV`; `_lemma_or_lower` now skips a lemma substitution
  whenever the surface form is POS-tagged ADV and the lemma differs (adverbs
  don't meaningfully inflect, so a differing "lemma" is a contamination
  signal, not real morphology) — loaded for every compression level, not just
  SYNTACTIC.
- **Chinese had no word segmentation at all.** The shared `\p{L}\p{M}`
  tokenizer matched an entire Han-character sentence as one "token" (Chinese
  has no spaces), so no function word ever matched — compression and language
  detection both silently no-op'd beyond punctuation stripping (confirmed:
  `LanguageDetector.detect()` returned `"eng"` for pure Chinese text). Added
  **`synthelion.cjk_segmenter`** — dictionary-based segmentation via DAG
  construction + dynamic-programming longest-match path, the same core
  algorithm as jieba's non-ML dictionary mode, re-implemented rather than
  taken as a dependency (jieba's optional HMM unknown-word tagger, the actual
  ML part, is not used). Dictionary built from Synthelion's own zho worddata
  (function words + lemma surface forms + proper nouns), so quality tracks
  what Synthelion already ships. Wired into both `core._tokenize` and
  `detector._tokenize_words`.
- **`synthelion/worddata/zho.yaml.br`, `zho.pos.yaml.br`, and `_index.br` had
  real mojibake corruption**, found while investigating why "不" (not) wasn't
  recognised: some Chinese entries were UTF-8 bytes mis-decoded as
  Windows-1252 at some earlier pipeline stage, then re-encoded — reversible,
  deterministic corruption (confirmed: round-tripping through a
  cp1252-encode + WHATWG-lenient-passthrough + utf-8-decode recovers exactly
  the original character, e.g. `0xE5,0x2C6,0xAB` -> `别`). Repaired 618+213
  entries in `zho.yaml.br`, 131 in `zho.pos.yaml.br`, and — a **separate**
  corrupted copy, since non-curated-language detection reads `_index.br`, not
  the per-language file — 10 further languages in `_index.br` (ben, ell, eng,
  heb, hin, jpn, kor, mar, tha, vie) that shared the same corruption pattern.
- **Language-detection data contamination**, found via false-positive
  detection tests: `"john"` (an English first name) was present as a
  "function word" in 16 languages' `_index.br` entries (ita, ben, bul, ell,
  est, eus, fin, fra, heb, hun, isl, msa, pol, swe, tur, vie) — a proper noun
  has no legitimate place in any language's function-word list. This alone
  flipped `"John bought bread and ate cake yesterday."` to detect as Italian.
  Also removed `"ha"`/`"e"` (real Italian words, not French) from French's
  `_index.br` entry, which was tying French against Italian on real Italian
  sentences, and removed `"ate"` from Portuguese's exclusive-marker list
  (collided with English's own "ate", defeating the exclusive-marker
  disambiguation pass that's supposed to resolve exactly this kind of tie).
  All three fixes verified via `LanguageDetector.detect()` on the exact
  sentences that mis-detected before the fix.
- Added `tests/test_negation_and_zh.py` (15 tests) covering all of the above.

### Fixed (found while refreshing the installed Claude Code hook)
- **`install_claude.py`'s Windows `UserPromptSubmit` hook was broken** —
  `_hook_command_windows` called the compress binary as `$p|"C:\...\synthelion.exe" compress --json`,
  which PowerShell rejects (`ExpressionsMustBeFirstInPipeline` — a quoted
  path followed by arguments needs the `&` call operator). The hook had
  silently regressed at some point after being manually patched directly in
  a live `settings.json`; re-running the installer overwrote that working
  fix with the broken generator output. Fixed by adding `& ` before the
  quoted path, matching `install_claude.ps1` (which already had it).
- Also brought `_hook_command_windows`/`_hook_command_unix` back in line with
  `install_claude.ps1`/`install_claude.sh`: they had drifted to injecting only
  an efficiency/energy/CO2 *label*, dropping the actual compressed prompt
  text from `additionalContext` — the whole point of the hook. Restored the
  `'[Synthelion N% saved] ' + compressed_text` format all three installers
  now share.
- **`install_claude.sh`** read the wrong JSON field (`compressed_text`,
  which `synthelion compress --json` has never emitted — the real field is
  `compressed`), so the Unix hook always injected an empty string. Fixed.
- Verified end-to-end: ran `install_claude.py --no-pip`, extracted the
  written hook command from `~/.claude/settings.json`, and executed it
  through real `powershell.exe` with JSON piped over stdin exactly as Claude
  Code invokes it — confirmed valid `hookSpecificOutput` JSON with the
  compressed text for a >200-char prompt, and silent no-op for a short one.

### Changed
- **The `UserPromptSubmit` hook now also returns a top-level `systemMessage`**
  in all three installers (`install_claude.py`, `.ps1`, `.sh`). Previously the
  savings note was injected only into `hookSpecificOutput.additionalContext`,
  which Claude reads but Claude Code never displays — the hook was working
  but invisible. `systemMessage` is shown directly in the terminal, so the
  compression is now visibly confirmed on every prompt it fires for.
- **`systemMessage` and `additionalContext` now carry different content**,
  each tuned for its actual audience: `systemMessage` (user-visible) is a
  short label — `'[Synthelion N% saved - X mWh - Y mg CO2 saved]'` — with no
  compressed text, so the terminal doesn't get cluttered with keyword-bag
  output; `additionalContext` (Claude-only, never rendered) still carries the
  full compressed text, since that's what actually does the token-saving
  work. Re-verified end-to-end via real `powershell.exe` with JSON piped over
  stdin: `systemMessage` and `additionalContext` now differ as intended.

Incremental update porting the algorithmic/correctness work from **Caveman 1.4.1** (the
original C# project — https://github.com/francescopaolopassaro/caveman) to Python. All
new capabilities are additive: existing levels, methods and return signatures keep
working exactly as before unless noted.

### Fixed
- **Critical: `CompressionLevel.AGGRESSIVE` crashed on every non-trivial sentence** —
  `_filter_aggressive` referenced an undefined `group` name (the `_lang_group(iso3)`
  call was missing). `compress()` silently swallowed the exception into
  `error_message`; direct `apply_compression()` calls (e.g. from the MCP server)
  raised outright. Fixed; added regression tests (the existing test for this path
  only checked token counts, which happened to pass even with the exception
  swallowed — now also asserts `error_message is None`).
- **Tokenizer fragmented words in scripts using combining marks** (Kannada, Hindi,
  Tamil, Thai, …): both `core._tokenize` and `detector._WORD_RE` excluded Unicode
  combining marks (category M*), splitting e.g. "दिन" into "द" and "न". Switched
  to the `regex` package (already a declared dependency) with `\p{L}\p{M}` Unicode
  property escapes — a compiled matcher, not a per-character loop, so no
  performance regression.
- **Silent content loss from two overly-broad descriptive-word suffixes**, found via
  a new comprehensibility test suite: the Romance `"are"` suffix matched every
  Italian first-conjugation infinitive verb ("analizzare") in addition to its
  intended "-are" adjectives, silently deleting sentences' main verbs; the English
  `"al"`/`"ical"` suffixes matched domain-specifying adjectives ("financial") as
  often as decorative ones. Both removed from their suffix lists.
- **Historical-treebank contamination in the bundled word data** (Italian, Swedish,
  Romanian, Icelandic): an archaic sense of a word (e.g. Italian "torta" — modern
  "cake" — annotated as a form of "torcere", "to twist", throughout Dante's Divine
  Comedy) could outvote the modern meaning in the lemma map. Word data files
  regenerated from Caveman's corrected pipeline, which now excludes treebanks that
  silently share a modern language's name (`-Old`, `PaHC`, and
  `UD_Romanian-Nonstandard`, found by reading treebank READMEs rather than by
  naming pattern alone).

### Added
- **`CompressionLevel.STATISTICAL`** — TF-IDF word scoring as an alternative to
  curated dictionaries: scores each word by frequency in the prompt vs. how many of
  the prompt's own sentences contain it, grounding "common" words against the
  language's curated function/generic-word lists as the standard-corpus reference.
- **`CompressionLevel.SYNTACTIC`** — rule-based pruning: same content-word filtering
  as `AGGRESSIVE`, but a function word survives when it is grammatical glue directly
  touching a surviving word, so the result reads as a terse but grammatical sentence
  rather than a keyword bag. When POS data is available for the language (see
  below), also elides a leading hedging/matrix clause ("I kindly ask you to…") in
  favour of the sentence's last verb — gated on real POS evidence and restricted to
  never fire when a genuine content noun sits between two candidate verbs, so
  coordinated clauses ("I bought bread and ate cake") are never mistaken for a
  hedge clause and gutted.
- **`FunctionWordProvider.get_pos_tags(iso3)` / `get_pos_tag(word, iso3)`** — a
  Universal POS lookup (NOUN, VERB, ADJ, ADP, DET, …), the most frequent tag
  Universal Dependencies treebanks observed per word form. A frequency-baseline
  tagger, not a model — covers 54 of 55 mappable languages.
- **`FunctionWordProvider.get_generic_words(iso3)`** now derives a generic-word set
  algorithmically for every language without a curated `{iso3}.generic.yaml.br`
  file, instead of falling back to nothing: the most richly inflected verb lemmas
  in that language's own worddata are ranked as the generic set (validated against
  Polish, where the top-ranked lemmas are "być"/be, "mieć"/have, "mówić"/say —
  exactly the target category, derived from data instead of a translated list).
- **`synthelion.simhash`** — a 64-bit Charikar SimHash fingerprint (FNV-1a feature
  hashing, stdlib only) for near-duplicate text detection.
  `LogCompressor.compress(..., fuzzy=True)` uses it to group near-duplicate lines
  (not just exact matches after normalisation) — e.g. templated lines that
  substitute a username or IP address. Off by default.
- **`CodeCompressor.compress(code, skeletonize=True)`** — an additional pass that
  replaces function/method bodies with a placeholder, keeping only signatures. Real
  brace-depth counting (not regex) handles arbitrary nesting and braces inside
  string/char literals correctly; Python bodies are found by indentation. Only leaf
  `def`/method bodies are collapsed, never a `class`/type container. Lossy by
  design, so off by default; the return signature gained a 4th element
  (`functions_skeletonized`).
- **`_bm25_select(..., delta=1.0)`** in the JSON crusher — BM25+ instead of plain
  BM25: a lower-bound term is added to every non-zero term match, so a genuinely
  relevant row that only mentions the query term once no longer scores near zero
  just because it's a long row.
- **`synthelion.nlp.TopicSegmenter`** — TextTiling-style topic segmentation
  (Hearst, 1997): groups sentences into blocks, scores vocabulary similarity
  between adjacent blocks, and cuts where similarity dips sharply relative to the
  surrounding peaks.
  **`TfIdfSummarizer.summarize_topic_aware(...)`** is a new, separate method
  (existing `summarize` is unchanged) that segments first and allocates the
  sentence budget proportionally across topics (largest-remainder rounding), so a
  summary can't let one statistically dense topic dominate and starve the others —
  falls back to `summarize()` when segmentation finds no real topic structure.
- **`synthelion.retriever.Retriever`** — BM25+ ranking over arbitrary text chunks
  (sentences, conversation turns, log lines, …), with `retrieve_with_feedback`
  adding an RM3 pseudo-relevance-feedback pass: an initial BM25 ranking builds a
  relevance model from its own top results' vocabulary, expands the query with
  that model's top terms, and re-ranks — surfacing genuinely relevant chunks that
  don't literally contain the query's words.
- **A comprehensibility test suite** treating "would a reader — human or AI — still
  get the point from the compressed text alone?" as a directly testable property.

#### Cluster / multi-node / AI-provider-scale deployment
- **`synthelion.config`** — JSON configuration with a fixed resolution order
  (`SYNTHELION_CONFIG` env var → `./synthelion.config.json` → `~/.synthelion/config.json`
  → built-in defaults), deep-merged so a partial file only needs to override the keys
  it cares about. Covers `session_store` (local/redis/postgres), `vector_store`
  (chromadb/qdrant/lexical), and `dashboard` (host/port/realtime mechanism) — the
  shape a Kubernetes ConfigMap-mounted file or a per-node override needs.
- **`synthelion configure`** CLI command — writes/updates `~/.synthelion/config.json`
  from flags (`--session-store`, `--redis-url`, `--postgres-dsn`, `--vector-store`,
  `--qdrant-url`, `--dashboard-host/--dashboard-port`, `--realtime`), or `--show` to
  print the effective config without writing anything.
- **`synthelion.analytics.session_store`** — a `SessionStore` abstraction with three
  interchangeable backends behind `create_session_store(config)`:
  `LocalFileSessionStore` (wraps the existing lock-free ledger, single-node),
  `RedisSessionStore` (pipelined hash/set/counter writes, lazy `import redis`),
  and `PostgresSessionStore` (auto-creates its schema on first connect, lazy
  `import psycopg`) — so active-session visibility can span every node behind a
  load balancer instead of being per-process.
- **Qdrant as a third `session_db` vector-store backend** (alongside the existing
  ChromaDB and lexical-fallback paths), selected via `vector_store.backend` in
  config or `SessionDB(backend="qdrant")`. Consistent with Synthelion's "zero ML
  models" design: rather than pulling in an embedding model just for Qdrant, it
  indexes a deterministic hashed bag-of-words vector (`_hash_vector`, FNV-1a
  feature hashing — the same stable hash `simhash` uses, not Python's
  per-process-randomised `hash()`) — enough for Qdrant's ANN index to support
  "find similar decisions" recall across a cluster without adding a heavyweight
  dependency. Falls back to lexical search if the `qdrant-client` package isn't
  installed or the server is unreachable, exactly as the ChromaDB path already did.
- New optional dependency groups in `pyproject.toml`: `qdrant`, `redis`, `postgres`,
  and a `cluster` bundle of all three — none required for the single-node default.

#### Docker / Kubernetes / Docker Swarm deployment
- **`Dockerfile`** — multi-stage build (`pip install ".[cluster]"` in a builder
  stage, non-root runtime user), runs `synthelion serve-dashboard --host
  0.0.0.0` by default, `HEALTHCHECK` against `/api/summary`. Verified
  end-to-end: image builds, container boots, dashboard responds, and a
  session-store write succeeds through a mounted named volume.
- Fixed a **root-owned volume-mount permission bug** found while verifying the
  image: the local session/vector store directory
  (`~/.synthelion`, incl. the ChromaDB `sessions` subdir) didn't exist in the
  image before `USER synthelion` took effect, so Docker seeded any volume
  mounted there as `root:root` — every write then failed with `[Errno 13]
  Permission denied`. Fixed by pre-creating the directory tree with the
  correct ownership as root, before switching to the non-root user and
  declaring the `VOLUME`.
- **`docker-compose.yml`** — single-node quick start (`docker compose up -d`)
  plus a `cluster` profile (`--profile cluster`) that also starts Redis,
  Postgres, and Qdrant containers. Same file deploys to Docker Swarm via
  `docker stack deploy`. Fixed a **replica/port-binding conflict**: `deploy.replicas:
  2` broke plain `docker compose up` (newer Compose honours `deploy.replicas`
  outside Swarm too, and two replicas can't both bind host port 8787) —
  reverted to `replicas: 1` for local use, documented `docker service scale`
  for Swarm's routing-mesh-backed scaling instead. Verified with a live
  `docker compose up -d` run.
- **`synthelion.config.example.json`** — a ready-to-copy config showing the
  Redis + Qdrant cluster-profile wiring, validated against `synthelion.config.load_config`.
- **`k8s/`** — `namespace.yaml`, `configmap.yaml` (mounts
  `synthelion.config.json`), `deployment.yaml` (3 replicas +
  `HorizontalPodAutoscaler`, readiness/liveness probes, non-root
  `securityContext`), `service.yaml` (ClusterIP), `backing-services.yaml`
  (evaluation-grade in-cluster Redis + Qdrant `StatefulSet`s). All manifests
  YAML-parse-validated.
- New "Cluster deployment" section in README.md covering `synthelion
  configure`, Docker/Compose/Swarm, Kubernetes, and the plain-load-balancer
  path.

#### More agents wired into `synthelion install`
- **`synthelion install --agent cursor`** — registers Synthelion in
  `~/.cursor/mcp.json` (same `mcpServers` shape as Claude).
- **`synthelion install --agent windsurf`** — registers Synthelion in
  `~/.codeium/windsurf/mcp_config.json` (same `mcpServers` shape as Claude).
  Both were previously listed in the README as "configure via the app UI" only;
  they're now scriptable like every other supported agent.

## [1.1.0] — 2026-07-11

### Added

#### Web dashboard
- **`synthelion serve-dashboard [--host] [--port]`** — local, read-only web dashboard (stdlib `http.server`, no extra dependencies). Binds to `127.0.0.1:8787` by default.
- Bootstrap 5 and Chart.js **vendored locally** (`synthelion/plugins/dashboard_assets/vendor/`) — no CDN, works fully offline.
- KPIs: calls, tokens saved, avg efficiency, estimated cost saved, active sessions, avg calls/session, tools used, best single call, avg/p95/max latency.
- Charts: tokens saved over time, tokens saved by tool.
- Tables: by content type, recent session-memory decisions, **Sessions** (one row per `synthelion-mcp`/CLI process), **Recent requests** (full per-call feed with latency).
- Auto-start on Claude Code startup via a `SessionStart` hook — checks if the port is already listening (non-blocking) before launching.

#### Concurrency — built for high-volume AI-provider traffic
- **Lock-free append-only ledger** (`analytics/ledger.py`): `savings.json` → `savings.jsonl`, one atomic append per record instead of read-modify-write. Old array-format ledgers are migrated automatically on first read.
- **`analytics/_atomic_append.py`** — new cross-platform atomic line-append primitive. On Windows, Python's `os.open(..., O_APPEND)` is *not* atomic across processes (the CRT does a separate seek-to-EOF + write); an 8-process/1600-write stress test lost ~28% of records with the naive approach. Fixed by opening the file handle with **only** `FILE_APPEND_DATA` access via `CreateFileW` (ctypes) — verified 1600/1600 records with zero loss under concurrent writers.
- **`analytics/session_db.py`** fallback store also converted to lock-free append-only JSONL; reads always re-scan from disk instead of trusting an in-memory cache that could go stale across processes.
- **Non-blocking auto-update claim** (`mcp_server.py`): when many `synthelion-mcp` processes notice a new PyPI release at once, only one may run `pip install --upgrade` — enforced with an atomic `O_CREAT|O_EXCL` claim file (self-expiring after 5 minutes) instead of a blocking lock. Losing processes skip immediately.
- **`asyncio.to_thread`** wraps tool execution in the MCP server's `call_tool` handler — concurrent tool calls no longer serialize on the event loop.
- Every ledger record now carries **`session_id`**, **`pid`**, and **`duration_ms`** — enables per-session and per-request breakdowns without any extra plumbing at call sites.

#### CLI ledger integration
- `synthelion compress`, `synthelion route`, and `synthelion summarize` now write to the same ledger the MCP server and dashboard read (`cli_compress`, `cli_route`, `cli_summarize` tool names) — the Claude Code `UserPromptSubmit` hook's compressions are now visible in `synthelion status` and the dashboard, not just MCP tool calls.
- **`synthelion install --agent opencode [--local]`** — registers Synthelion as an MCP server in OpenCode's config (`~/.config/opencode/opencode.json` global, or `./opencode.json` project-local), using OpenCode's `mcp` schema (`type: "local"`, `command` as an array).

#### Ledger
- **`sessions_summary()`** — groups ledger records by writer process (session_id), returning calls/tokens/tools/first-last-activity per session.
- **`avg_latency_ms` / `p95_latency_ms` / `max_latency_ms`** added to `summary()`.

### Fixed
- README: OpenCode config path corrected from the non-existent `~/.config/opencode/config.json` to the real `~/.config/opencode/opencode.json`, and the MCP block corrected to OpenCode's actual schema (it previously showed the Claude/Gemini `mcpServers` shape, which OpenCode does not use).

---

## [1.0.8] — 2026-06-27

### Added

#### AI-agent context tools (3 new MCP tools)
- **`compress_for_context`** — compresses any content to fit within a token budget (`max_tokens`). Chains routing → NLP compression → TextRank summarization until the budget is met. Returns `fits_budget`, `strategy`, and `budget_exceeded_by` so the caller always knows exactly where it stands. Profile options: `light | balanced | agent | aggressive`.
- **`compress_conversation`** — compresses a conversation history (list of `{role, content}` messages). Keeps the last `keep_last_n` messages verbatim (default: 4), compresses older turns with the content router, and collapses everything into a `[system]` summary block when a `max_tokens` budget is provided and still exceeded.
- **`deduplicate`** — removes near-duplicate texts from a list using bag-of-words cosine similarity. Configurable `threshold` (default: 0.8). Preserves order of first occurrence. Useful when multiple retrieval sources return overlapping content.

#### Analytics & savings tracking
- **`cost_usd_saved`** field in every ledger record — estimated dollar savings at Sonnet 4.6 input pricing ($3.00/MTok). Accumulated and shown in `synthelion status` and `synthelion gain`.
- **`synthelion status`** CLI command — shows total calls, tokens before/after, avg efficiency %, cost saved, breakdown by tool and content type.
- **`synthelion gain [--days N] [--all] [--json]`** — savings history with dollar estimate and efficiency rate.
- **`synthelion bench [--json]`** — benchmark compression on a built-in 7-sample corpus (prose EN/IT, JSON array, git diff, Python code, log, HTML).

#### Cross-session memory (MCP tools)
- **`session_record`** — persist a decision or context note across sessions. Stored in ChromaDB (semantic) or lexical fallback when chromadb is not installed.
- **`session_recall`** — retrieve past decisions by semantic similarity or keyword. Supports `limit`, `since_days` filtering.
- **`session_start` / `session_end`** — track session boundaries and return summaries.
- **`synthelion_status`** — return aggregate savings stats as a structured dict (also available via MCP).

#### CLI
- **`synthelion doctor [--json]`** — health check: verifies MCP package, savings ledger, session DB, `synthelion-mcp` in PATH, and Claude Code MCP config registration.
- **`synthelion install [--agent claude|gemini] [--local]`** — registers the Synthelion MCP server in the global or project-local agent config (`~/.claude.json`, `./.claude/settings.json`, `~/.gemini/settings.json`). Idempotent — safe to re-run after upgrades.

#### Agent integrations
- **`ClaudeAdapter`** (`synthelion.integrations.claude_adapter`) — drop-in wrapper around `anthropic.Anthropic` with auto-compression + RAG memory recall on every `chat()` call.
- **`OpenAIAdapter`** (`synthelion.integrations.openai_adapter`) — equivalent wrapper for `openai.OpenAI`.
- **`RagAgent`** (`synthelion.agent.rag_agent`) — provider-agnostic stateful agent: compresses every message, recalls past decisions via SessionDB, maintains a rolling context window, and tracks token savings in the ledger.
- **`SynthelionMemory`** (LangChain plugin) — drop-in `ConversationBufferMemory` replacement that compresses history and injects RAG recall.

#### MCP server
- **`readOnlyHint: true`** annotation on all non-mutating tools — Claude Code and other MCP clients can safely call these in parallel without write-conflict checks.
- **`~$X.XXXXX`** dollar cost estimate appended to every `synthelion_metrics` field — visible in every tool response.
- **13 MCP tools total**: `compress`, `detect_language`, `route_content`, `summarize`, `compress_batch`, `compress_for_context`, `compress_conversation`, `deduplicate`, `compress_file`, `session_record`, `session_recall`, `session_start`, `session_end`, `synthelion_status`.

#### Tests
- **222 tests total** (`test_synthelion.py`: 110, `test_analytics.py`: 112): analytics suite covers SavingsLedger, SessionDB (fallback + ChromaDB mock), RagAgent, ClaudeAdapter, OpenAIAdapter, LangChain memory, and all new MCP tools.

### Fixed
- **`install_claude.ps1` — here-string parsing**: script now saved with CRLF line endings; Windows PowerShell requires CRLF for here-string delimiters (`@'...'@`)
- **`install_claude.ps1` — smoke test**: Python code no longer passed via `-c "..."` (f-string `:.0f` format and `%` operator caused PS parser errors); uses a temp `.py` file instead
- **`install_claude.ps1` — Python version check**: replaced f-string `f'{sys.version_info.major}.{minor}'` with string concatenation to avoid PowerShell `{...}` interpolation issues
- **`install_claude.ps1` — hook path escaping**: removed unnecessary double-backslash escaping; Windows paths in PS double-quoted strings do not require extra escaping
- **`install_claude.ps1` — hook call operator**: added `&` call operator before the quoted executable path
- **`install_claude.ps1` — hook bracket escaping**: escaped `[` and `]` in `additionalContext` with backtick to prevent PowerShell from parsing them as type literals
- **`cli.py` — `compress --json` output**: `energy_mwh` and `co2_mg` fields now included in JSON output

#### MCP tool — compress_file
- **`compress_file`** — read a file by path and return only the compressed content. Avoids loading the full raw file into LLM context. Accepts `path`, `profile`, `max_tokens`, `encoding`. Delegates to `compress_for_context` so routing → NLP → TextRank chain applies automatically.

#### CLI additions
- **`synthelion upgrade [--dry-run]`** — self-upgrade via `pip install --upgrade synthelion`.
- **`synthelion export [--format csv|jsonl] [-o FILE] [--days N]`** — export savings ledger for analysis in Excel / Grafana / pandas.

#### Hook improvement
- Hook now injects the **compressed text** into `additionalContext` (not just a stats label) — Claude receives the actual compressed version and can reason from it directly.
- PowerShell hook: uses string-concatenation pattern (`$ctx = '[Synthelion '+$pct+'% saved] '+$r.compressed`) — avoids PS type-literal parsing of `[...]`.
- Bash hook: uses `json.dumps` for proper escaping of the compressed string in JSON output.

#### LangChain
- `synthelion_compress_file` StructuredTool added (12 tools total in `get_tools()`).

### Changed
- **`install_claude.ps1`** installer options use PowerShell switch syntax (`-Upgrade`, `-NoHook`, `-NoPip`, `-Uninstall`)
- **`synthelion_metrics`** format extended: `before=N after=M saved=K (XX.X%) ~$0.00015` — dollar cost always included
- **Tools table**: 13 MCP tools total (`compress_file` added)

---

## [1.0.6] — 2026-06-24

### Fixed
- `__version__` was stuck at 1.0.0 — now kept in sync with `pyproject.toml`
- `"dagli"` appeared 3× in the Italian curated function-word list (duplicate entries removed)
- `mcp_server.py` docstring incorrectly said `pip install "synthelion[mcp]"` — `mcp` is now a core dependency
- `_CURATED` private symbol no longer imported directly across modules — exposed via `FunctionWordProvider.get_curated_iso3s()`
- `getattr(args, "json", False)` in CLI replaced with direct `args.json`

### Improved
- **ContentRouter cache** now has a hard max of 512 entries with 25% LRU eviction — prevents unbounded memory growth in long-running processes
- **TextRank** sentence cap added (`_MAX_SENTENCES = 200`) — prevents O(n² × 100) blowup on very long documents
- **`compress_batch()`** uses `ThreadPoolExecutor` for batches > 8 items — parallel execution with up to 4 workers
- **Error logging** added to `compress()` — failures emit a `WARNING` via Python logging instead of being fully silent
- **`py.typed` marker** added — mypy and pyright now recognize Synthelion as a fully typed package
- **`plugins/__init__.py`** now exports `get_tool_definitions`, `get_tool_list`, `execute_tool`, `serve_mcp`

### Added
- **`count_tokens(text, mode="approx")`** public function in `synthelion` namespace — GPT-style estimate (`len // 4`) or whitespace word count
- **`FunctionWordProvider.get_curated_iso3s()`** public class method
- **`CHANGELOG.md`** — this file

### Removed
- `synthelion_mcp.py` root-level script — superseded by `synthelion-mcp` console_script entry point

---

## [1.0.5] — 2026-06-24

### Fixed
- Language detector: Italian/Catalan/Portuguese confusion on short texts — curated languages now preferred when score is within 75% of best YAML-derived score
- Duplicate `get_tool_list()` definition in `mcp_server.py` removed
- 35 new tests added (LangChain plugin, CLI, JsonCrusher BM25, HTML/diff/log edge cases, Romance language disambiguation)

---

## [1.0.4] — 2026-06-24

### Added
- Full code examples in README: compress, detect, content router (JSON/HTML/diff/log/code), summarize (TF-IDF + TextRank), agent memory

---

## [1.0.3] — 2026-06-24

### Added
- Benchmark section in README: before/after examples, token savings tables by content type, cost calculator (GPT-4o), energy estimator

---

## [1.0.2] — 2026-06-24

### Added
- LangChain plugin (`synthelion/plugins/langchain_tools.py`) — 5 `StructuredTool` wrappers for LangGraph/LCEL/ReAct agents
- Universal README covering all integrations equally (MCP, OpenAI, LangChain, Python API, CLI)
- `langchain` optional extra: `pip install "synthelion[langchain]"`

---

## [1.0.1] — 2026-06-24

### Fixed
- `pyproject.toml` license field updated to SPDX format (deprecation warning resolved)

### Changed
- README simplified to focus on end-user setup

---

## [1.0.0] — 2026-06-24

### Added
- Initial public release
- Core NLP compression engine: Light / Semantic / Aggressive levels, 50+ languages
- Content router: JSON array, HTML, git diff, build logs, source code, plain text
- Summarization: TF-IDF + MMR, TextRank + MMR, chat-aware mode
- Agent toolkit: ContextWindow, MemoryExtractor, MemoryStore
- MCP server: Claude Code, OpenCode, Cursor, Windsurf, Continue
- OpenAI function tools: GPT-4, GPT-4o, Codex
- CLI: compress / detect / route / summarize / serve-mcp
- Python API: CompressionService, ContentRouter, LanguageDetector
