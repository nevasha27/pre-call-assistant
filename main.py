import base64
import os
import time
from pathlib import Path
from typing import Literal

import anthropic
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="Pre-Call Assistant")

app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")

GMAIL_MCP_URL = os.getenv("GMAIL_MCP_URL")
GMAIL_MCP_TOKEN = os.getenv("GMAIL_MCP_TOKEN")
GDRIVE_MCP_URL = os.getenv("GDRIVE_MCP_URL")
GDRIVE_MCP_TOKEN = os.getenv("GDRIVE_MCP_TOKEN")

DATA_DIR = Path("data")
TOKEN_FILE = Path("token.json")

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BriefQuery(BaseModel):
    query: str
    mode: Literal["local", "api", "mcp"] = "local"


class BriefResponse(BaseModel):
    summary: str
    mode: str
    elapsed_seconds: float


class SpeakRequest(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# Shared prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a pre-call intelligence assistant for Alex, an enterprise \
account executive at a B2B SaaS company. Alex is about to jump on a sales call and needs \
a sharp, actionable brief in under 15 minutes."""

BRIEF_INSTRUCTIONS = (
    "Produce a pre-call brief with these four sections:\n\n"
    "DEAL STATUS — current stage, deal value, close timeline, key stakeholders\n"
    "RECENT EMAIL CONTEXT — what has been discussed in recent emails, tone and momentum\n"
    "OPEN QUESTIONS — questions or technical asks that were raised but never answered\n"
    "FLAGGED RISKS — competing vendors, unlooped stakeholders, missed follow-ups, "
    "stalled action items\n\n"
    "Be direct and specific. Name people, dates, and dollar amounts."
)

# ---------------------------------------------------------------------------
# Local mode helpers
# ---------------------------------------------------------------------------

def _load_local_context(query: str) -> str:
    known_accounts = {f.name.split("-")[0] for f in DATA_DIR.glob("*.txt")}
    query_lower = query.lower()
    account = next((a for a in known_accounts if a in query_lower), None)
    context_files = sorted(
        DATA_DIR.glob(f"{account}-*.txt") if account else DATA_DIR.glob("*.txt")
    )
    if not context_files:
        raise HTTPException(
            status_code=404,
            detail="No context files found in data/. Add <account>-<type>.txt files.",
        )
    sections = []
    for f in context_files:
        label = f.stem.replace("-", " ").title()
        sections.append(f"=== {label} ===\n{f.read_text().strip()}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# API mode helpers (Google OAuth)
# ---------------------------------------------------------------------------

def _get_google_creds() -> Credentials:
    if not TOKEN_FILE.exists():
        raise HTTPException(
            status_code=503,
            detail="token.json not found. Run: uv run python scripts/get_token.py",
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), GOOGLE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def _fetch_gmail_context(account_keyword: str, creds: Credentials, max_threads: int = 5) -> str:
    service = build("gmail", "v1", credentials=creds)
    results = service.users().threads().list(
        userId="me",
        q=account_keyword,
        maxResults=max_threads,
    ).execute()

    threads = results.get("threads", [])
    if not threads:
        return f"No Gmail threads found matching '{account_keyword}'."

    sections = []
    for thread in threads:
        thread_data = service.users().threads().get(
            userId="me", id=thread["id"], format="full"
        ).execute()
        messages = thread_data.get("messages", [])
        thread_lines = []
        for msg in messages[:4]:  # cap at 4 messages per thread
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            subject = headers.get("Subject", "(no subject)")
            sender = headers.get("From", "unknown")
            date = headers.get("Date", "")

            # extract plain text body
            body = ""
            payload = msg.get("payload", {})
            if payload.get("mimeType") == "text/plain":
                data = payload.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            else:
                for part in payload.get("parts", []):
                    if part.get("mimeType") == "text/plain":
                        data = part.get("body", {}).get("data", "")
                        if data:
                            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                            break

            thread_lines.append(
                f"  From: {sender} | Date: {date}\n"
                f"  Subject: {subject}\n"
                f"  {body[:500].strip()}"
            )
        sections.append("--- Email Thread ---\n" + "\n\n".join(thread_lines))

    return "\n\n".join(sections)


def _fetch_drive_context(account_keyword: str, creds: Credentials, max_files: int = 5) -> str:
    service = build("drive", "v3", credentials=creds)
    results = service.files().list(
        q=f"fullText contains '{account_keyword}' and trashed=false",
        pageSize=max_files,
        fields="files(id, name, mimeType)",
    ).execute()

    files = results.get("files", [])
    if not files:
        return f"No Google Drive files found matching '{account_keyword}'."

    sections = []
    for f in files:
        name = f["name"]
        mime = f.get("mimeType", "")

        # export Google Docs as plain text; skip non-text files
        if mime == "application/vnd.google-apps.document":
            try:
                content = service.files().export(
                    fileId=f["id"], mimeType="text/plain"
                ).execute()
                text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)
                sections.append(f"--- Drive File: {name} ---\n{text[:1500].strip()}")
            except Exception:
                sections.append(f"--- Drive File: {name} --- (could not export)")
        elif mime == "text/plain":
            try:
                content = service.files().get_media(fileId=f["id"]).execute()
                text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)
                sections.append(f"--- Drive File: {name} ---\n{text[:1500].strip()}")
            except Exception:
                sections.append(f"--- Drive File: {name} --- (could not read)")
        else:
            sections.append(f"--- Drive File: {name} ({mime}) --- (binary, skipped)")

    return "\n\n".join(sections)


def _extract_account_keyword(query: str) -> str:
    """Best-effort: match against known data/ prefixes, else return full query."""
    known = {f.name.split("-")[0] for f in DATA_DIR.glob("*.txt")}
    q = query.lower()
    match = next((a for a in known if a in q), None)
    return match or query


# ---------------------------------------------------------------------------
# MCP mode helpers
# ---------------------------------------------------------------------------

def _mcp_servers_configured() -> bool:
    return bool(GMAIL_MCP_URL and GDRIVE_MCP_URL)



# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")


@app.get("/slides")
async def serve_slides():
    return FileResponse("static/slides.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/accounts")
async def list_accounts():
    files = [f.stem for f in sorted(DATA_DIR.glob("*.txt"))]
    return {"accounts": files}


@app.post("/brief", response_model=BriefResponse)
async def generate_brief(req: BriefQuery):
    """
    Generate a pre-call intelligence brief.

    mode="local"  — context from data/<account>-*.txt files
    mode="api"    — context fetched live from Gmail + Google Drive via OAuth
    mode="mcp"    — Claude fetches context via MCP servers (requires MCP config)
    """
    t_start = time.perf_counter()

    if req.mode == "api":
        creds = _get_google_creds()
        keyword = _extract_account_keyword(req.query)
        gmail_context = _fetch_gmail_context(keyword, creds)
        drive_context = _fetch_drive_context(keyword, creds)
        context = f"=== Gmail Threads ===\n{gmail_context}\n\n=== Google Drive Files ===\n{drive_context}"
        user_message = (
            f"{req.query}\n\n"
            f"Here is live context fetched from Gmail and Google Drive:\n\n{context}\n\n"
            f"{BRIEF_INSTRUCTIONS}"
        )
        with anthropic_client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            brief_text = stream.get_final_message().content[0].text

    elif req.mode == "mcp":
        if not _mcp_servers_configured():
            raise HTTPException(
                status_code=503,
                detail="MCP mode requires GMAIL_MCP_URL and GDRIVE_MCP_URL in .env",
            )
        gmail_url = (GMAIL_MCP_URL or "").strip()
        gdrive_url = (GDRIVE_MCP_URL or "").strip()
        mcp_servers = [
            {
                "type": "url",
                "url": gmail_url,
                "name": "composio" if gmail_url == gdrive_url else "gmail",
                "authorization_token": (GMAIL_MCP_TOKEN or "").strip(),
            },
        ]
        if gdrive_url and gdrive_url != gmail_url:
            mcp_servers.append({
                "type": "url",
                "url": gdrive_url,
                "name": "gdrive",
                "authorization_token": (GDRIVE_MCP_TOKEN or "").strip(),
            })
        mcp_user_message = (
            f"{req.query}\n\n"
            "Search Google Drive for files related to the account in this query "
            "(notes, proposals, decks). Then search Gmail for recent email threads "
            f"with contacts from that account. Then:\n\n{BRIEF_INSTRUCTIONS}"
        )
        response = anthropic_client.beta.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            betas=["mcp-client-2025-04-04"],
            mcp_servers=mcp_servers,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": mcp_user_message}],
        )
        brief_text = next(
            (block.text for block in response.content if block.type == "text"), ""
        )

    else:  # local
        context = _load_local_context(req.query)
        user_message = (
            f"{req.query}\n\n"
            f"Here is all available context for this account:\n\n{context}\n\n"
            f"{BRIEF_INSTRUCTIONS}"
        )
        with anthropic_client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            brief_text = stream.get_final_message().content[0].text

    if not brief_text:
        raise HTTPException(status_code=500, detail="Claude returned no text content.")

    elapsed = round(time.perf_counter() - t_start, 2)
    return BriefResponse(summary=brief_text, mode=req.mode, elapsed_seconds=elapsed)


def _elevenlabs_configured() -> None:
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs credentials not configured in .env",
        )


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """
    Accept an audio file upload and return the transcribed text via ElevenLabs STT.
    Supports common formats: mp3, mp4, wav, webm, ogg, flac, m4a.
    """
    _elevenlabs_configured()
    audio_bytes = await audio.read()
    resp = requests.post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        files={"file": (audio.filename or "audio", audio_bytes, audio.content_type or "audio/webm")},
        data={"model_id": "scribe_v1"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs STT error {resp.status_code}: {resp.text}",
        )
    payload = resp.json()
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=500, detail="Transcription returned empty text.")
    return {"text": text}


@app.post("/speak")
async def speak(req: SpeakRequest):
    """
    Convert text to speech via ElevenLabs TTS. Returns a streaming MP3 response.
    Use the /brief endpoint separately to generate the text, then pass it here.
    """
    _elevenlabs_configured()
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": req.text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        stream=True,
        timeout=60,
    )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs TTS error {resp.status_code}: {resp.text}",
        )
    return StreamingResponse(
        resp.iter_content(chunk_size=4096),
        media_type="audio/mpeg",
        headers={"Content-Disposition": "inline; filename=brief.mp3"},
    )


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

class BenchmarkResult(BaseModel):
    query: str
    results: list[dict]


@app.post("/benchmark", response_model=BenchmarkResult)
async def benchmark(req: BriefQuery):
    """
    Run the same query against all available modes and return timing results.
    Skips modes that aren't configured (e.g. mcp without MCP URLs, api without token.json).
    """
    modes_to_run: list[str] = ["local"]
    if TOKEN_FILE.exists():
        modes_to_run.append("api")
    if _mcp_servers_configured() and TOKEN_FILE.exists():
        modes_to_run.append("mcp")

    results = []
    for mode in modes_to_run:
        try:
            response = await generate_brief(BriefQuery(query=req.query, mode=mode))
            results.append({
                "mode": mode,
                "elapsed_seconds": response.elapsed_seconds,
                "status": "ok",
                "summary": response.summary,
                "preview": response.summary[:200].replace("\n", " "),
            })
        except HTTPException as exc:
            results.append({"mode": mode, "elapsed_seconds": None, "status": f"error: {exc.detail}", "preview": ""})
        except Exception as exc:
            results.append({"mode": mode, "elapsed_seconds": None, "status": f"error: {exc}", "preview": ""})

    return BenchmarkResult(query=req.query, results=results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
