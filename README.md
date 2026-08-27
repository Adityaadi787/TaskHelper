# TaskHelper

TaskHelper is an adaptive browser automation and information extraction agent powered by Playwright, BeautifulSoup, and SQLite.

## Features

- **Natural Language Task Engine**: Parses user instructions to perform web searches, navigate direct URLs, and extract structured text, lists, tables, headings, or attributes.
- **Generic Browser Automation**: Uses Playwright (local Chromium or remote WebSocket connection) with automatic retries and popup handling.
- **DOM & Shadow-DOM Extraction**: Flattens rendered visible text including shadow DOMs, extracting relevant content without site-specific hardcoded selectors.
- **Persistent Memory**: Stores task runs, search queries, results, locations, and execution statuses in SQLite (`data/taskhelper.db`).
- **Discord Bot & CLI Interfaces**: Run single tasks via CLI or start a Discord bot service.

## Setup & Installation

### Prerequisites
- Python 3.10+
- Playwright Chromium binaries

### Local Installation
```bash
pip install -r requirements.txt
playwright install chromium
```

### Environment Configuration
Copy `.env.example` to `.env` and set optional configuration parameters:
```env
DISCORD_TOKEN=your_token_here
BROWSER_MODE=local
HEADLESS=true
LOG_LEVEL=INFO
```

## Usage

### Run CLI Task
```bash
python main.py "Search for Python programming language on Wikipedia and give the first 10 words"
```

### View Task History
```bash
python main.py --recent
```

### Run Tests
```bash
python -m unittest test_agent.py
```

## Docker Deployment
Build and run using Docker Compose:
```bash
docker-compose up --build
```
