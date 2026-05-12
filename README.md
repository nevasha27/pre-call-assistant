# Pre-Call Assistant

A voice-activated pre-call intelligence tool for enterprise account executives. Before any sales call, ask it to prep you — it pulls deal context, email history, open questions, and flagged risks, then reads the brief aloud.

Built as an MCP code example demonstrating the performance difference between three data-fetching modes: local files, direct Google API (OAuth), and Anthropic MCP servers.

---

## Features

- **Three brief modes** — Local files, Google OAuth (Gmail + Drive), Anthropic MCP beta
- **Benchmark view** — run all modes side-by-side with response time comparison and bar chart
- **Voice input** — click the mic, speak your query, ElevenLabs transcribes it
- **Voice output** — click Listen on any brief to have it read aloud (browser Web Speech API)
- **Single-mode or all** — toggle between Local / API / MCP / All (Benchmark) from the UI

---

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) — fast Python package manager
- An [Anthropic API key](https://console.anthropic.com)
- An [ElevenLabs API key](https://elevenlabs.io) — for speech-to-text (free tier supported)
- A Google Cloud project with OAuth credentials — for API mode

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/nevasha27/pre-call-assistant.git
cd pre-call-assistant
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...

# ElevenLabs — speech-to-text (transcribe endpoint)
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=pNInz6obpgDQGcFmaJgB   # Adam (free premade voice)

# MCP mode only — Anthropic MCP beta server URLs
GMAIL_MCP_URL=https://gmailmcp.googleapis.com/mcp/v1
GDRIVE_MCP_URL=https://drivemcp.googleapis.com/mcp/v1
```

### 4. Add account context files (Local mode)

Place plain-text files in the `data/` directory named `<account>-<type>.txt`:

```
data/
  clienta-account-brief.txt
  clienta-email-summary.txt
  clienta-meeting-notes.txt
```

Sample files for `clienta` are included. Add your own accounts using the same naming pattern.

### 5. Set up Google OAuth (API mode only)

API mode fetches live context from Gmail and Google Drive using OAuth 2.0.

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services → Credentials**
2. Create an OAuth 2.0 Client ID (Desktop app type)
3. Save the client ID and secret as `credentials.json` in the project root:

```json
{
  "installed": {
    "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "client_secret": "YOUR_CLIENT_SECRET",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://localhost"]
  }
}
```

4. Enable the Gmail API and Google Drive API in your project
5. Add your Google account as a test user under **OAuth consent screen**
6. Run the one-time authorization flow:

```bash
uv run python scripts/get_token.py
```

A browser window opens for sign-in. After approval, `token.json` is saved to the project root and reused automatically.

---

## Running the server

```bash
uv run python main.py
```

Server starts at `http://localhost:8000` with hot-reload enabled.

---

## Using the app

Open `http://localhost:8000` in your browser.

**Mode selector** — choose how context is fetched:

| Mode | Description |
|------|-------------|
| Local | Reads `data/<account>-*.txt` files — no external APIs |
| API | Fetches live Gmail threads and Drive files via Google OAuth |
| MCP | Claude fetches context autonomously via Anthropic MCP servers |
| All (Benchmark) | Runs all configured modes and compares response times |

**Voice input** — click the mic button to start recording, click again to stop. The transcribed text drops into the query field.

**Listen** — after a brief is generated, click ▶ Listen to have it read aloud. Click ⏹ Stop to stop.

---

## API endpoints

### `POST /brief`
Generate a pre-call brief for a given query and mode.

```bash
curl -s -X POST http://localhost:8000/brief \
  -H "Content-Type: application/json" \
  -d '{"query": "prep me for my call with ClientA", "mode": "local"}' \
  | python3 -m json.tool
```

### `POST /benchmark`
Run all configured modes and return a timing comparison.

```bash
curl -s -X POST http://localhost:8000/benchmark \
  -H "Content-Type: application/json" \
  -d '{"query": "prep me for my call with ClientA"}' \
  | python3 -m json.tool
```

### `POST /transcribe`
Upload an audio file and receive the transcribed text (ElevenLabs STT).

```bash
curl -s -X POST http://localhost:8000/transcribe \
  -F "audio=@/path/to/recording.mp3" \
  | python3 -m json.tool
```

### `POST /speak`
Convert text to speech via ElevenLabs TTS. Returns a streaming MP3.

```bash
curl -s -X POST http://localhost:8000/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Deal status: negotiation stage, one eighty K ARR."}' \
  --output brief.mp3 && open brief.mp3
```

---

## Project structure

```
pre-call-assistant/
├── main.py                  # FastAPI app — all endpoints
├── scripts/
│   └── get_token.py         # One-time Google OAuth flow
├── data/
│   ├── clienta-account-brief.txt
│   ├── clienta-email-summary.txt
│   └── clienta-meeting-notes.txt
├── static/
│   └── index.html           # Frontend UI
├── requirements.txt
├── pyproject.toml
└── .env                     # Not committed — add your keys here
```

---

## Notes

- `credentials.json` and `token.json` are excluded from git via `.gitignore`
- MCP mode requires real Google MCP server endpoints — the URLs in `.env` are placeholders pending public availability
- The benchmark `elapsed_seconds` includes Claude's full generation time, not just the API round-trip
