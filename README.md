# TaskHelper

TaskHelper is a lightweight, user-authorized browser task agent. It turns natural-language instructions into a structured plan, searches/navigates with Async Playwright, extracts requested information using generic DOM semantics, validates that an answer is non-empty, and records the attempt in SQLite. Discord is an optional adapter; the browser/task engine does not depend on Discord.

## Actual entry point

There is one application entry point: `main.py`.

```bash
python3 main.py "Open https://example.com and give the first 10 words"
python3 main.py --recent
python3 main.py --discord
```

`--discord` requires `discord.py` and a real `DISCORD_TOKEN`.

## Project layout

- `main.py` — CLI/Discord entry point.
- `config.py` — validated environment configuration.
- `browser_manager.py` — one reusable Playwright browser/context and cleanup/retry logic.
- `agent.py` — generic search-result collection and relevance scoring.
- `task_detector.py` — natural-language task and YouTube requirement parsing.
- `task_executor.py` — browser execution, extraction, pagination and validation.
- `task_engine.py` — top-level orchestration plus SQLite recording.
- `extractor.py` — word/marker/heading/paragraph/list/table/attribute extraction.
- `video_tasks.py` — dynamic YouTube search/identification/timing/caption helpers.
- `memory.py` — persistent SQLite task history with freshness filtering.
- `reporter.py` — structured result serialization.
- `discord_bot.py` — optional Discord-only interface.
- `test_agent.py` — deterministic unit/integration test suite.
- `tests/fixtures/` — local HTML fixtures.
- `Dockerfile`, `docker-compose.yml`, `render.yaml` — deployment definitions.

## Local setup

Use Python 3.10+ (Python 3.11/3.12 is recommended for third-party compatibility).

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Playwright browser binaries are a **setup/deployment step**, never downloaded on application startup:

```bash
playwright install chromium
```

If Chromium is already installed by the OS, set `PLAYWRIGHT_EXECUTABLE_PATH`, or TaskHelper will try common executables such as `chromium` and `google-chrome`.

Copy `.env.example` to `.env` for local configuration. `.env` is gitignored.

## Configuration

Important environment variables:

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Discord bot token; required only for Discord mode. |
| `DISCORD_COMMAND_PREFIX` | Discord prefix, default `!`. |
| `BROWSER_MODE` | `local` or `remote`. |
| `BROWSER_WS_ENDPOINT` | Remote Playwright WebSocket endpoint. |
| `PLAYWRIGHT_EXECUTABLE_PATH` | Explicit local Chromium executable. |
| `HEADLESS` | `true`/`false`. |
| `VIEWPORT_WIDTH`, `VIEWPORT_HEIGHT` | Browser viewport. |
| `USER_AGENT` | Optional browser UA. |
| `NAVIGATION_TIMEOUT_MS` | Navigation timeout. |
| `ACTION_TIMEOUT_MS` | General Playwright action timeout. |
| `MAX_RETRIES` | Bounded retry count, 1–5. |
| `INTERACTION_DELAY_MS` | Optional delay after navigation. |
| `MAX_SEARCH_RESULTS` | Search candidates inspected/retained. |
| `MAX_PAGES` | Pagination bound, 1–20. |
| `DATABASE_PATH` | SQLite file path. |
| `MEMORY_MAX_AGE_HOURS` | Freshness window for optional historical hints. |
| `DEFAULT_SEARCH_URL` | Search URL template containing `{query}`. |

Never put tokens, passwords, cookies, API keys or auth headers in source files.

## Task interpretation

TaskHelper does not depend on a fixed website selector or fixed search result position. It extracts the query from the task and scores available links by query-token overlap plus target-domain hints. Extraction instructions supported include:

- first X words
- last X words
- words/text between two markers
- text before/after a marker
- content under a heading
- paragraph by marker/index
- list items
- table rows/cells/columns
- common attributes such as href/src/alt/value
- bounded pagination across pages

JavaScript-rendered content is captured after Playwright loads the page. Open shadow roots are flattened into rendered text. Accessible iframes can be traversed by Playwright when a workflow explicitly targets them; inaccessible cross-origin/authenticated frames remain a browser security boundary. BeautifulSoup is used for semantic HTML extraction after the browser snapshot.

## SQLite memory

Each attempt can store:

- original task
- task type
- search query
- answer
- relevant location/section
- extraction rule
- URL
- points/task metadata when supplied
- created/updated timestamps
- success/failure
- error information

Historical rows are records, not truth. `find_similar(..., max_age_seconds=...)` supports a freshness bound. The execution engine still performs a live browser workflow rather than blindly returning an old answer.

## Discord setup

1. Create a Discord application/bot and enable the **Message Content Intent**.
2. Install dependencies with `pip install -r requirements.txt`.
3. Put the bot token in `DISCORD_TOKEN`.
4. Start:

```bash
python3 main.py --discord
```

Use:

```text
!task Search for "example" and give the first 5 words
```

The formatter used for task archiving safely escapes backslashes and double quotes:

```text
!add "Task Description / Search Prompt" "Extracted Answer"
```

Live Discord connectivity is external-service functionality and requires credentials.

## YouTube workflow

A YouTube task can specify its own search query, target title/channel, thumbnail text/hint, timestamp, interval, required viewing duration, reference phrase and requested value. These values are parsed from each task; no particular video, timestamp, query or duration is hard-coded.

For timestamp tasks, TaskHelper can seek an accessible HTML5 video element and read available YouTube caption DOM text. If captions/player controls are unavailable because of consent, sign-in, age restrictions, changed DOM, or another access boundary, the task fails honestly rather than inventing an answer.

Example shape:

```text
Search youtube for "demo" and target video "Example" channel "Maker" thumbnail "red car" from 01:10 to 01:20 and return text at 01:10
```

Real YouTube execution is subject to YouTube availability, consent, rate limits and account/access restrictions.

## Testing

The suite is deterministic and does not require Google, YouTube or Discord. It covers imports, configuration validation, task parsing, extraction modes, command formatting, SQLite memory/freshness, browser configuration, retries, cleanup, JavaScript-rendered fixtures, shadow DOM, and bounded pagination.

Run:

```bash
pytest -q
# or
python3 -m unittest test_agent -v
```

A local Chromium executable is used when available for browser integration tests. If no Chromium exists, those tests skip with an explicit reason.

## Docker

The Docker image uses the Playwright Python base image, which provides browser binaries at **image build/deployment time**. The application itself does not download browsers.

```bash
docker build -t taskhelper .
docker run --rm taskhelper "Open file:///app/tests/fixtures/sample_page.html and give the first 5 words"
```

Compose:

```bash
docker compose up -d
```

SQLite should be mounted to `/app/data` for persistence.

## Render

`render.yaml` defines a Render Docker worker. Set `DISCORD_TOKEN` as a secret and use the persistent disk mounted at `/app/data` if SQLite history must survive restarts.

Render/browser limitations include memory/CPU limits, browser process overhead, external-site rate limits, CAPTCHAs, consent screens, authentication requirements and the fact that local SQLite is not a multi-instance database. For higher scale, use a proper external database and a dedicated browser service.

Validate the YAML before deployment, then deploy through Render. The actual cloud deployment is not part of the local test suite.

## Error handling and logging

The browser layer has bounded retries and explicit launch/navigation errors. Task execution distinguishes extraction, navigation, YouTube and unexpected failures. Shutdown closes pages, context, browser and Playwright. Logs avoid printing configured tokens and redact URL query strings in browser navigation logs.

## Known limitations

- Adaptive interpretation is intentionally dependency-light and rule-based; it is not an LLM and may need new parsing patterns for unusual wording.
- Search-result relevance is lexical rather than semantic.
- Cross-origin iframe restrictions and authenticated content cannot be bypassed.
- YouTube timestamp extraction depends on an accessible player/caption DOM.
- External websites can change their DOM or block automation.
- Discord and real external-site workflows require credentials/network access and must be tested in the target environment.
- Docker/Render deployment must be validated in an environment that actually has Docker/Render access.
