"""Isolated points rehearsal on port 8030; never reuses the user's preview.

All award rows are produced by the real API/operator flows in the shared
rehearsal. Only synthetic identities and their authentication are fixture setup.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def main():
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            os.environ.pop(name)
    with tempfile.TemporaryDirectory(prefix="crucible-points-browser-") as tmp:
        os.environ.update(CRUCIBLE_ENV="development", CRUCIBLE_DATA_DIR=tmp)
        from pytest import MonkeyPatch
        from crucible.app import create_app
        from crucible.db import User
        from crucible.security import passwords
        from test_bounties import make_system
        from test_points_workflow import rehearse
        import uvicorn

        with MonkeyPatch.context() as patcher:
            system = make_system("sqlite:///" + tmp + "/points.db", patcher)
            app, *_, now = system
            ui = None
            try:
                evidence = rehearse(system, patcher)
                encoded = passwords.hash("Synthetic points fixture password")
                with app.state.db.transaction(write=True) as session:
                    for actor in system[1:-1]:
                        session.get(User, actor.identifier).password_hash = encoded
                settings = replace(app.state.settings, origins=("http://testserver", "http://127.0.0.1:8030"))
                ui = create_app(settings, clock=lambda: now[0])
                # Test-only evidence artifact; no participant identifiers or keys.
                output = ROOT / ".local" / "points-rehearsal-evidence.json"
                output.parent.mkdir(exist_ok=True)
                output.write_text(json.dumps(evidence, indent=2) + "\n")
                print(f"Points rehearsal: {len(evidence['checkpoints'])} checked stages; solver total 2403. Disposable port 8030.", flush=True)
                uvicorn.run(ui, host="127.0.0.1", port=8030, access_log=False, log_level="warning")
            finally:
                for actor in system[1:-1]:
                    actor.client.close()
                app.state.db.engine.dispose()
                if ui is not None:
                    ui.state.db.engine.dispose()


if __name__ == "__main__":
    main()
