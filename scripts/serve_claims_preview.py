#!/usr/bin/env python3
"""Prepare and serve the isolated claims/arguments development preview.

This explicit launcher has synthetic persona sign-in. It is never imported by
the application or deployment entrypoint. See docs/claims-local-setup.txt.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MARKER = "crucible-claims-preview-v1"


def preview_environment(environment, data_dir):
    """Refuse host credentials and construct a local configuration explicitly."""
    if (environment.get("DATABASE_URL") or environment.get("CRUCIBLE_SECRET")
            or environment.get("CRUCIBLE_ENV", "development") != "development"):
        raise RuntimeError("Use a development shell without DATABASE_URL or CRUCIBLE_SECRET; no hosted settings are accepted.")
    if environment.get("CRUCIBLE_DATA_DIR"):
        raise RuntimeError("Unset CRUCIBLE_DATA_DIR; this launcher uses its own .local/claims-preview workspace.")
    clean = {key: value for key, value in environment.items()
             if not key.startswith("CRUCIBLE_") and key not in {"DATABASE_URL", "OPENAI_API_KEY"}}
    clean.update(CRUCIBLE_ENV="development", CRUCIBLE_DATA_DIR=str(data_dir),
                 CRUCIBLE_LOCAL_NOTE_CORPUS_ONLY="true", CRUCIBLE_ACCESS_MODE="open",
                 CRUCIBLE_PUBLICATION_SCREENING_MODE="disabled", CRUCIBLE_MAIL_BACKEND="disabled",
                 CRUCIBLE_GITHUB_ENABLED="false")
    return clean


def workspace(root=ROOT):
    """Never adopt an existing notebook or follow a path into another data set."""
    root = root.resolve()
    local = root / ".local"
    directory = local / "claims-preview"
    if local.is_symlink() or directory.is_symlink():
        raise RuntimeError("The preview workspace must not be a symlink.")
    marker = directory / "preview-workspace.json"
    if directory.exists():
        if any(path.is_symlink() for path in directory.rglob("*")):
            raise RuntimeError("The preview workspace contains a symlink; no files were changed.")
        if any(directory.iterdir()) and not marker.is_file():
            raise RuntimeError("The preview directory already contains unmarked data; choose a fresh checkout or move it aside yourself.")
        if marker.exists() and json.loads(marker.read_text()) != {"format": MARKER}:
            raise RuntimeError("The preview directory belongs to a different fixture.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not marker.exists():
        with marker.open("x") as output:
            json.dump({"format": MARKER}, output)
            output.write("\n")
    return directory


def attach_preview_entry(app, data_dir):
    """Add persona entry only to this app instance; normal route policy is intact."""
    from fastapi.responses import JSONResponse, RedirectResponse
    from crucible.db import LoginSession, User
    from crucible.security import token_hash
    from scripts.prepare_note_integration import AUTHOR, REVIEWER

    settings = app.state.settings
    if (settings.production or not settings.local_note_corpus_only
            or settings.database_url != f"sqlite:///{data_dir / 'crucible.db'}"
            or not settings.cookie_name.startswith("crucible_claims_preview_")):
        raise RuntimeError("Synthetic persona entry requires isolated preview settings.")

    @app.middleware("http")
    async def preview_entry(request, call_next):
        # Check the actual peer, not forwarded headers. Disable proxy trust when
        # launching Uvicorn below; this service is never intended for sharing.
        if (request.client is None or request.client.host not in {"127.0.0.1", "::1"}
                or request.headers.get("host") != settings.origins[0].removeprefix("http://")):
            return JSONResponse({"detail": "Loopback preview only."}, status_code=403)
        if request.url.path != "/preview":
            return await call_next(request)
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if (request.method != "GET" or request.headers.get("sec-fetch-site") == "cross-site"
                or request.headers.get("origin", settings.origins[0]) != settings.origins[0]):
            return JSONResponse({"detail": "Open this preview directly on loopback."}, status_code=403, headers=headers)
        persona = request.query_params.get("persona", "author")
        if persona not in {"author", "reviewer"}:
            return JSONResponse({"detail": "Choose author or reviewer."}, status_code=400, headers=headers)
        destination = "/#challenge"
        if request.query_params.get("trial") == "1":
            receipt = data_dir / "statement-trial-receipt.json"
            if not receipt.is_file():
                return JSONResponse({"detail": "Prepare the optional trial first with --prepare --with-trial."}, status_code=503, headers=headers)
            identifier = str(UUID(json.loads(receipt.read_text())["guide_note_id"]))
            destination = "/#notebook?note=" + identifier
        identifier = REVIEWER if persona == "reviewer" else AUTHOR
        with app.state.db.transaction(write=True) as session:
            if session.get(User, identifier) is None:
                return JSONResponse({"detail": "Prepare the fixture first with --prepare."}, status_code=503, headers=headers)
            raw = secrets.token_urlsafe(40)
            session.add(LoginSession(token_hash=token_hash(raw), user_id=identifier,
                                     expires_at=time.time() + 86400))
        response = RedirectResponse(destination, status_code=303, headers=headers)
        response.set_cookie(settings.cookie_name, raw, httponly=True, samesite="strict", max_age=86400)
        return response
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Migrate the isolated database and prepare eight scheme notes")
    parser.add_argument("--with-trial", action="store_true", help="With --prepare, add the optional six-note lifecycle rehearsal")
    parser.add_argument("--refresh-fixture", action="store_true", help="With --prepare, explicitly revise unchanged fixture notes to current source")
    parser.add_argument("--prepare-only", action="store_true", help="With --prepare, finish without starting the server")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose an unprivileged port from 1024 through 65535.")
    if (args.with_trial or args.refresh_fixture or args.prepare_only) and not args.prepare:
        parser.error("--with-trial, --refresh-fixture and --prepare-only require --prepare.")
    os.umask(0o077)
    # Validate inherited settings before even creating the local directory.
    environment = preview_environment(os.environ, ROOT / ".local" / "claims-preview")
    data_dir = workspace()
    os.environ.clear()
    os.environ.update(environment)

    def run(*arguments):
        subprocess.run([sys.executable, *arguments], cwd=ROOT, env=environment, check=True)

    if args.prepare:
        # Real registry validation: no fake workers or weakened artifact pins.
        run("-c", "from crucible.registry import validate_registry; validate_registry()")
        run("-m", "crucible.manage", "migrate")
        options = ["--revise-fixture"] if args.refresh_fixture else []
        run("scripts/prepare_note_integration.py", "--data-dir", str(data_dir), *options)
        if args.with_trial:
            run("scripts/prepare_statement_trial.py", "--data-dir", str(data_dir))
        if args.prepare_only:
            return
    if not (data_dir / "note-integration-receipt.json").is_file():
        raise RuntimeError("Run this launcher with --prepare first; no notebook is imported from another checkout.")

    from crucible.config import Settings, load_settings
    from crucible.app import create_app
    from crucible import notebook
    from scripts.prepare_note_integration import REVIEWER
    import uvicorn

    class PreviewSettings(Settings):
        @property
        def cookie_name(self):
            return f"crucible_claims_preview_{args.port}"

    settings = replace(load_settings(), origins=(f"http://127.0.0.1:{args.port}",),
                       rate_limits=False, reviewer_ids=(REVIEWER,))
    settings = PreviewSettings(**settings.__dict__)
    notebook.MAX_WORKSPACE_BYTES = max(notebook.MAX_WORKSPACE_BYTES, 8 * 1024 * 1024)
    app = attach_preview_entry(create_app(settings), data_dir)
    print(f"Local fixture only: http://127.0.0.1:{args.port}/preview", flush=True)
    print("Synthetic reviewer: add ?persona=reviewer; optional trial: add &trial=1. Stop with Ctrl+C.", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
