"""Stop future weekly admission points with an immutable operator retirement.

Run migrations first. This records the cutoff now; no backdating, history rewrite
or automatic resumption is supported. Coordinate family registry retirement
separately. Replacing an active family's release does not retire the family.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crucible.config import load_settings
from crucible.db import Database
from crucible.scoring_awards import AdmissionCreditError, retire_scheme_credit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("family-id", "reviewer-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--reason-file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args())
    path = args.pop("reason_file")
    try:
        if path.stat().st_size > 40000:
            raise AdmissionCreditError("Reason file is too large.")
        reason = path.read_text(encoding="utf-8")
        settings = load_settings()
        db = Database(settings.database_url)
        try:
            result = retire_scheme_credit(db, settings, reason=reason, **args)
        finally:
            db.engine.dispose()
    except (AdmissionCreditError, OSError, UnicodeError) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
