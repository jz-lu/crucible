"""Full Crucible UI on loopback with disposable accounts, signup links and local mail.

Never reads deployment settings or connects to a hosted database/mail provider.
Registered native workers must already be built: their normal pin checks apply.
"""
import argparse
from pathlib import Path
import secrets
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crucible import beta_access, bounty_allowance
from crucible.app import create_app
from crucible.bounty_models import BountyPolicy
from crucible.config import Settings
from crucible.db import User
from crucible.security import passwords
import uvicorn


class PreviewSettings(Settings):
    @property
    def cookie_name(self):
        return 'crucible_showcase_preview'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8043)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Choose an unprivileged loopback port (1024–65535).')
    origin = f'http://127.0.0.1:{args.port}'
    with tempfile.TemporaryDirectory(prefix='crucible-showcase-') as temporary:
        data = Path(temporary)
        admin_id = str(uuid4())
        settings = PreviewSettings('sqlite:///' + str(data / 'preview.db'), secrets.token_bytes(32),
            origins=(origin,), access_mode='beta', rate_limits=False, access_admin_ids=(admin_id,),
            generation_concurrency=1, generation_per_account=1,
            mail_backend='local', mail_outbox_dir=str(data / 'outbox'),
            mail_from='Crucible preview <preview@example.invalid>')
        app = create_app(settings, initialize=True)
        now = time.time()
        with app.state.db.transaction(write=True) as session:
            user = User(id=admin_id, handle='showcase_preview',
                password_hash=passwords.hash('Local showcase preview'), created_at=now)
            session.add(user)
            session.flush()
            beta_access.set_membership(session, user.id, 'active', now)
            session.add(BountyPolicy(id=bounty_allowance.INITIAL_ID,
                effective_month=bounty_allowance.INITIAL_MONTH,
                configuration=dict(bounty_allowance.INITIAL_CONFIGURATION), created_at=now))
            link = beta_access.create_signup_link(session, settings, 'One-week research preview', now, actor_id=user.id)
            session.flush()
            signup = beta_access.signup_link_data(settings, link, now)
        print('Disposable local preview: ' + origin + '/#home', flush=True)
        print('Preview administrator: showcase_preview / Local showcase preview', flush=True)
        print('Local signup link: ' + signup['url'], flush=True)
        print('Expires (UTC): ' + time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(signup['expires_at'])), flush=True)
        print('Verification emails are written locally, never sent: ' + str(data / 'outbox'), flush=True)
        print('Stopping this process removes its disposable database and emails.', flush=True)
        try:
            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
        finally:
            app.state.db.engine.dispose()


if __name__ == '__main__':
    main()
