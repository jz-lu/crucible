"""Opt-in, real deployment fixtures; no production configuration/app is loaded.

CRUCIBLE_DEPLOY_FIXTURE=1 authorizes temporary local services and synthetic
PostgreSQL databases/roles inside the disposable Linux VM only.
"""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("CRUCIBLE_DEPLOY_FIXTURE") != "1",
                               reason="Explicit disposable Linux deployment fixture only")


def command(args, *, env=None, content=None):
    return subprocess.run(args, env=env, input=content, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90).stdout


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_caddy_routes_tls_forwarding_limits_and_log_privacy(tmp_path):
    caddy = shutil.which("caddy")
    assert caddy, "Install Caddy in the disposable Linux fixture"
    class Backend(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            body = json.dumps({"forwarded": self.headers.get("X-Forwarded-For"),
                "proto": self.headers.get("X-Forwarded-Proto"), "path": self.path}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200); self.end_headers(); self.wfile.write(str(len(body)).encode())
    backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True); thread.start()
    listen = free_port()
    text = (ROOT/"deploy/Caddyfile.example").read_text().replace(
        "admin unix//run/caddy/admin.sock", "admin off\n\tskip_install_trust\n\tauto_https disable_redirects")
    text = text.replace("beta.example.invalid {", f"https://127.0.0.1:{listen} {{\n\ttls internal")
    text = text.replace("127.0.0.1:8000", f"127.0.0.1:{backend.server_port}")
    config = tmp_path/"Caddyfile"; config.write_text(text)
    env = {**os.environ, "XDG_DATA_HOME": str(tmp_path/"data"), "XDG_CONFIG_HOME": str(tmp_path/"config")}
    command([caddy, "validate", "--config", str(config), "--adapter", "caddyfile"], env=env)
    with (tmp_path/"caddy.log").open("wb") as log:
        proxy = subprocess.Popen([caddy, "run", "--config", str(config), "--adapter", "caddyfile"],
                                 env=env, stdout=log, stderr=log)
        try:
            ca = tmp_path/"data/caddy/pki/authorities/local/root.crt"
            for _ in range(100):
                if ca.exists():
                    try:
                        context = ssl.create_default_context(cafile=str(ca))
                        urllib.request.urlopen(f"https://127.0.0.1:{listen}/health", context=context, timeout=.2).close()
                        break
                    except (OSError, urllib.error.URLError): pass
                time.sleep(.05)
            else: pytest.fail("Local TLS fixture did not start")
            sentinel = "synthetic-oauth-code-DO-NOT-LOG"
            for path in ("/api/notebook/notes", "/api/schemes", "/profiles/example", "/api/auth/github/callback?code="+sentinel):
                request = urllib.request.Request(f"https://127.0.0.1:{listen}"+path,
                    headers={"X-Forwarded-For": "203.0.113.77", "X-Forwarded-Proto": "http"})
                with urllib.request.urlopen(request, context=context, timeout=3) as response:
                    assert json.load(response) == {"forwarded": "127.0.0.1", "proto": "https", "path": path}
                    assert response.headers["Cache-Control"] == "no-store"
                    assert response.headers["Referrer-Policy"] == "no-referrer"
            request = urllib.request.Request(f"https://127.0.0.1:{listen}/api/notebook/import",
                data=b"x"*(3*1024*1024+1), method="POST")
            with pytest.raises((urllib.error.HTTPError, urllib.error.URLError, ConnectionError)):
                urllib.request.urlopen(request, context=context, timeout=5)
            backend.shutdown(); backend.server_close()
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(f"https://127.0.0.1:{listen}/callback?code="+sentinel, context=context, timeout=5)
        finally:
            proxy.terminate(); proxy.wait(timeout=10)
            backend.shutdown(); backend.server_close()
    assert sentinel not in (tmp_path/"caddy.log").read_text()


def test_systemd_units_parse_without_starting_the_app(tmp_path):
    analyze = shutil.which("systemd-analyze"); assert analyze
    units = []
    for name in ("crucible.service", "crucible-backup.service", "crucible-backup.timer"):
        # Interpreter path only is substituted for syntax validation. The
        # actual /opt release and native startup are separate release checks.
        source = (ROOT/"deploy"/name).read_text().replace("/opt/crucible/current/.venv/bin/python", "/usr/bin/python3")
        path = tmp_path/name; path.write_text(source); units.append(str(path))
    command([analyze, "verify", *units])


def load_backup():
    spec = importlib.util.spec_from_file_location("crucible_backup_fixture", ROOT/"deploy/backup-beta.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def backup_fixture(tmp_path):
    assert os.geteuid() == 0, "Run this opted-in disposable PG fixture as root"
    assert shutil.which("restic") and shutil.which("runuser")
    module = load_backup(); suffix = uuid4().hex[:12]
    role = "crucible_deploy_"+suffix
    database, restored = role+"_source", role+"_restore"
    password = "synthetic-backup-password-"+suffix
    pg = ["runuser", "-u", "postgres", "--", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", "postgres"]
    command(pg, content=f"CREATE ROLE {role} LOGIN PASSWORD '{password}';\nCREATE DATABASE {database} OWNER {role};\nCREATE DATABASE {restored} OWNER {role};\n".encode())
    try:
        tmp_path.chmod(0o700)
        ca = tmp_path/"ca.crt"; shutil.copyfile("/etc/ssl/certs/ssl-cert-snakeoil.pem", ca); ca.chmod(0o600)
        hostname = command(["openssl", "x509", "-in", str(ca), "-noout", "-subject"]).decode().strip().split("CN=")[-1].strip()
        service = tmp_path/"pg-service.conf"
        service.write_text(f"[fixture]\nhost={hostname}\nhostaddr=127.0.0.1\nport=5432\ndbname={database}\nuser={role}\nsslmode=verify-full\nsslrootcert={ca}\n")
        pgpass = tmp_path/"pgpass"; pgpass.write_text(f"*:*:*:{role}:{password}\n")
        key = tmp_path/"restic-password"; key.write_text("synthetic-encryption-key-"+suffix)
        runtime = tmp_path/"runtime.env"; runtime.write_text("CRUCIBLE_SECRET=synthetic-secret-preserve-exactly\n")
        release = tmp_path/"RELEASE.json"; release.write_text(json.dumps({"format": "crucible-host-release-v1", "release_id": "fixture-"+suffix}))
        for path in (service, pgpass, key, runtime, release): path.chmod(0o600)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "RESTIC_REPOSITORY": str(tmp_path/"encrypted-repo"),
            "RESTIC_PASSWORD_FILE": str(key), "RESTIC_CACHE_DIR": str(tmp_path/"cache"),
            "PGSERVICEFILE": str(service), "PGPASSFILE": str(pgpass), "PGSERVICE": "fixture",
            "CRUCIBLE_BACKUP_RUNTIME_ENV": str(runtime), "CRUCIBLE_BACKUP_RELEASE_MANIFEST": str(release),
            "CRUCIBLE_BACKUP_LOCK": str(tmp_path/"lock")}
        command(["restic", "init"], env=env)
        command(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-q"], env=env, content=(
            "CREATE TABLE fixture_notes (id int PRIMARY KEY, body text, evidence jsonb);\n"
            "INSERT INTO fixture_notes VALUES (1, 'synthetic private note λ', '{\"large\":123456789012345678901234567890}');\n").encode())
        yield module, env, runtime, restored, database, service
    finally:
        command(pg, content=f"DROP DATABASE IF EXISTS {database} WITH (FORCE);\nDROP DATABASE IF EXISTS {restored} WITH (FORCE);\nDROP ROLE IF EXISTS {role};\n".encode())


def test_encrypted_backup_restores_exact_database_and_runtime_secret(backup_fixture, tmp_path):
    module, env, runtime, restored, database, _ = backup_fixture
    with pytest.raises(module.BackupError, match="HTTPS S3"):
        module.backup(env)
    output = module.backup(env, require_remote=False)
    receipt = json.loads(command(["restic", "dump", output["completion_snapshot"], "completion.json"], env=env))
    assert command(["restic", "dump", receipt["configuration_snapshot"], str(runtime)], env=env) == runtime.read_bytes()
    dump = command(["restic", "dump", receipt["database_snapshot"], "database.dump"], env=env)
    command(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "--dbname", restored], env=env, content=dump)
    sql = "COPY (SELECT * FROM fixture_notes ORDER BY id) TO STDOUT;\n".encode()
    before = command(["psql", "-X", "-q", "-d", database], env=env, content=sql)
    after = command(["psql", "-X", "-q", "-d", restored], env=env, content=sql)
    assert before == after and "λ".encode() in after
    assert output["restore_tested"] is False
    for path in (tmp_path/"encrypted-repo").rglob("*"):
        if path.is_file(): assert b"synthetic-secret-preserve-exactly" not in path.read_bytes()


def test_failed_dump_cannot_create_a_complete_recovery_set(backup_fixture):
    module, env, _, _, database, service = backup_fixture
    service.write_text(service.read_text().replace("dbname="+database, "dbname=crucible_deploy_missing_database"))
    with pytest.raises(module.BackupError, match="no complete recovery set"):
        module.backup(env, require_remote=False)
    snapshots = json.loads(command(["restic", "snapshots", "--json"], env=env))
    assert snapshots and all("complete" not in item.get("tags", []) and "database" not in item.get("tags", []) for item in snapshots)
