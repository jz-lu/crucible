"""Explicit research/account workflow gate using disposable portable fixtures.

Run from any directory with the project's Python environment. The registry is
restricted only inside this test process; application artifact checks are never
patched. Native generation and PostgreSQL races require their separate gates.
"""
from pathlib import Path
import os
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    "test_protocol_proposals.py", "test_protocol_admission.py",
    "test_research_reviews.py", "test_proposal_retractions.py",
    "test_notebook.py", "test_notebook_access.py", "test_notebook_moderation.py",
    "test_notebook_browse.py", "test_notebook_summary_index.py", "test_notebook_timeline.py",
    "test_notebook_agents.py", "test_notebook_trash.py", "test_notebook_graph.py",
    "test_notebook_numbers.py",
    "test_publication_queue_summary.py",
    "test_note_ratings.py", "test_machine_auth.py",
    "test_researcher_profiles.py", "test_researcher_profiles_migration.py",
    "test_bounties.py", "test_bounties_migration.py",
    "test_admission_weekly_points.py", "test_scoring_awards.py",
    "test_numerical_frontier_points.py", "test_bounty_scoring.py",
)


class RequireExecutedChecks:
    def __init__(self):
        self.skipped = False

    def pytest_runtest_logreport(self, report):
        self.skipped |= report.skipped

    def pytest_collectreport(self, report):
        self.skipped |= report.skipped

    def pytest_sessionfinish(self, session, exitstatus):
        if self.skipped:
            session.exitstatus = 1


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    # Never inherit a developer's server, provider or database configuration.
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            os.environ.pop(name)
    with tempfile.TemporaryDirectory(prefix="crucible-workflow-checks-") as tmp:
        os.environ.update(CRUCIBLE_ENV="development", CRUCIBLE_DATA_DIR=tmp)
        from crucible import registry, lattice_frodo
        registry.ADAPTERS = {key: adapter for key, adapter in registry.ADAPTERS.items()
                             if key[0] in {"teaching-dh-kem", "rsa-kem"}}
        registry.ADAPTERS[(lattice_frodo.SCHEME_ID, lattice_frodo.SCHEME_VERSION)] = lattice_frodo
        registry.CURRENT = {"teaching-dh-kem": "7", "rsa-kem": "4",
                            lattice_frodo.SCHEME_ID: lattice_frodo.SCHEME_VERSION}
        import pytest
        print("Workflow scope: disposable SQLite; portable DH/RSA archives and Frodo. "
              "No hosted writes, native conformance or PostgreSQL race claim.", flush=True)
        return pytest.main(["-q", "-p", "no:cacheprovider", "--tb=short",
                            "--basetemp", str(Path(tmp) / "pytest"),
                            *(str(ROOT / "tests" / name) for name in SUITES)],
                           plugins=[RequireExecutedChecks()])


if __name__ == "__main__":
    raise SystemExit(main())
