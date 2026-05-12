"""
One-time script to authorise the app with Google and save a token.json.

Run from the project root:
    uv run python scripts/get_token.py

A browser window will open asking you to sign in with Google and grant access
to Gmail (read-only) and Google Drive (read-only). After you approve, the script
saves token.json to the project root. The FastAPI app uses that file for all
subsequent API calls — you won't need to run this again unless you revoke access.
"""

from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

ROOT = Path(__file__).parent.parent
CREDENTIALS_FILE = ROOT / "credentials.json"
TOKEN_FILE = ROOT / "token.json"


def main():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"credentials.json not found at {CREDENTIALS_FILE}. "
            "Download it from Google Cloud Console → APIs & Services → Credentials."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json())
    print(f"\ntoken.json saved to {TOKEN_FILE}")
    print("You can now start the server and use mode='api'.")


if __name__ == "__main__":
    main()
