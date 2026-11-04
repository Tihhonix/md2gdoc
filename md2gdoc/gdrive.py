"""Google OAuth + upload of HTML to Drive with conversion to a Google Doc."""

from __future__ import annotations

import io
import os
from pathlib import Path

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"

SCOPES = {
    # Access only to files this app created. Enough for create / --update of
    # our own docs. Usually NOT enough to put a doc into an existing folder.
    "file": ["https://www.googleapis.com/auth/drive.file"],
    # Full Drive access: needed for --folder-id pointing at a folder you made
    # in the Drive UI, or --update of a doc not created by this tool.
    "full": ["https://www.googleapis.com/auth/drive"],
}


class AuthError(RuntimeError):
    pass


def config_dir() -> Path:
    base = os.environ.get("MD2GDOC_CONFIG_DIR")
    if base:
        return Path(base).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "md2gdoc"


def get_credentials(scope: str = "file", client_secrets: Path | None = None, open_browser: bool = True):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = SCOPES[scope]
    cfg = config_dir()
    token_path = cfg / f"token-{scope}.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(token_path, creds)
            return creds
        except RefreshError:
            creds = None  # revoked or expired (7 days for apps in "Testing" mode)

    secrets = client_secrets or cfg / "credentials.json"
    if not secrets.exists():
        raise AuthError(
            f"OAuth client file not found: {secrets}\n"
            "Create an OAuth client (type 'Desktop app') in Google Cloud Console, "
            "enable the Google Drive API, download the JSON and either put it at the path "
            "above or pass --credentials PATH. See README.md."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes)
    creds = flow.run_local_server(port=0, open_browser=open_browser)
    _save_token(token_path, creds)
    return creds


def _save_token(path: Path, creds) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def upload_html(
    creds,
    html: str,
    title: str,
    *,
    folder_id: str | None = None,
    update_id: str | None = None,
) -> dict:
    """Create (or overwrite) a Google Doc from HTML. Returns {id, name, webViewLink}."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    media = MediaIoBaseUpload(
        io.BytesIO(html.encode("utf-8")), mimetype="text/html", resumable=True, chunksize=8 * 1024 * 1024
    )
    fields = "id,name,webViewLink"

    if update_id:
        request = service.files().update(
            fileId=update_id, body={"name": title}, media_body=media, fields=fields, supportsAllDrives=True
        )
    else:
        body = {"name": title, "mimeType": GOOGLE_DOC_MIME}
        if folder_id:
            body["parents"] = [folder_id]
        request = service.files().create(body=body, media_body=media, fields=fields, supportsAllDrives=True)

    response = None
    while response is None:
        _, response = request.next_chunk(num_retries=3)
    return response
