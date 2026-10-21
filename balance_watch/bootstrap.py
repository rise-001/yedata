"""Create and persist installation credentials before Django initializes."""
import json
import os
import secrets
import tempfile
from pathlib import Path


CREDENTIAL_FIELDS = ('APP_USERNAME', 'APP_PASSWORD', 'DJANGO_SECRET_KEY')


def load_credentials(data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    credentials_path = data_dir / 'bootstrap.json'
    saved = {}
    if credentials_path.exists():
        try:
            saved = json.loads(credentials_path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or any(
                not isinstance(saved.get(field), str) or not saved[field]
                for field in CREDENTIAL_FIELDS
            ):
                raise ValueError('missing or invalid credentials')
        except (ValueError, OSError) as exc:
            raise RuntimeError(
                f'Cannot read credentials from {credentials_path}; '
                'restore this file from backup before restarting.'
            ) from exc

    credentials = {
        field: os.environ.get(field) or saved.get(field)
        for field in CREDENTIAL_FIELDS
    }
    credentials['APP_USERNAME'] = credentials['APP_USERNAME'] or 'admin'
    generated_password = not credentials['APP_PASSWORD']
    if generated_password:
        credentials['APP_PASSWORD'] = secrets.token_urlsafe(24)
    if not credentials['DJANGO_SECRET_KEY']:
        credentials['DJANGO_SECRET_KEY'] = secrets.token_urlsafe(48)

    if credentials != saved:
        # Write privately and replace atomically so interruption cannot truncate
        # credentials already saved in the persistent Docker volume.
        fd, temporary_path = tempfile.mkstemp(prefix='.bootstrap-', dir=data_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(credentials, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, credentials_path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    if generated_password:
        print(
            '\n[balance-watch] Initial administrator credentials\n'
            f'  Username: {credentials["APP_USERNAME"]}\n'
            f'  Password: {credentials["APP_PASSWORD"]}\n'
            f'  Saved to: {credentials_path}\n'
            '  This password is only printed when first generated.\n',
            flush=True,
        )
    return credentials
