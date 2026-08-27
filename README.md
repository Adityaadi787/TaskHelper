# TaskHelper

TaskHelper is an adaptive, generic browser automation and extraction agent built on Python and Playwright. It converts natural-language browser tasks into structured execution plans, collects content, extracts relevant information using DOM semantics, and stores historical task data in SQLite.

## Features
- **Adaptive Task Parsing**: Rule and regex based parsing without requiring external LLM dependencies.
- **Generic Search & Extraction**: Scores candidates dynamically based on query relevance rather than relying on brittle, site-specific selectors.
- **SQLite Memory**: Persistent memory for task instructions, status, and extracted answers.
- **Multi-interface Support**: Standalone CLI execution, Discord bot interface, or programmatic engine integration.
- **Headless Browser Management**: Supports local Playwright Chromium or remote WebSocket browser connections.

## Local Installation & Usage

### Setup
1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Install Chromium browser binaries:
   ```bash
   playwright install chromium
   ```

### Running CLI Tasks
Execute a single task:
```bash
python main.py "Open file://$PWD/tests/fixtures/sample_page.html and give the first 5 words"
```

View recent tasks stored in SQLite memory:
```bash
python main.py --recent
```

### Running Tests
Run the complete unit test suite:
```bash
python test_agent.py
```

## Docker Deployment

### Run with Docker
Build and run the containerized TaskHelper agent:
```bash
docker build -t taskhelper .
docker run --rm taskhelper "Open file:///app/tests/fixtures/sample_page.html and give the first 5 words"
```

### Run with Docker Compose
```bash
docker-compose up -d
```
