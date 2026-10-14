"""Trusted local admission action, after review and active registry integration.

Run migrations first. Use --dry-run to inspect the proposed credit. This command
does not publish proposals, activate adapters, or award existing schemes by default.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crucible.config import load_settings
from crucible.db import Database
from crucible.scoring_awards import AdmissionCreditError, award_scheme_credit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("family-id", "scheme-id", "scheme-version", "proposal-id", "review-id", "reviewer-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True,
                        help="Admission evidence explaining integration and why this is a new family.")
    parser.add_argument("--confirm-new-family", action="store_true", required=True,
                        help="Attest this is a new family, not an alias or another release of an existing one.")
    parser.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args())
    path = args.pop("evidence_file")
    try:
        if path.stat().st_size > 40000:
            raise AdmissionCreditError("Evidence file is too large.")
        evidence = path.read_text(encoding="utf-8")
        settings = load_settings()
        db = Database(settings.database_url)
        try:
            result = award_scheme_credit(db, settings, evidence=evidence, **args)
        finally:
            db.engine.dispose()
    except (AdmissionCreditError, OSError, UnicodeError) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
