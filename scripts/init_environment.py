"""Generate local laboratory secrets without printing them or replacing a file."""
import os
import secrets
from pathlib import Path

destination = Path(__file__).resolve().parents[1] / '.env'
values = {key: secrets.token_hex(32) for key in ['DFSHA_JWT_SECRET', 'DFSHA_BLOCK_TOKEN_SECRET', 'DFSHA_INTERNAL_SECRET', 'DFSHA_BOOTSTRAP_PASSWORD', 'DFSHA_DN1_KEY_HEX', 'DFSHA_DN2_KEY_HEX', 'DFSHA_DN3_KEY_HEX', 'DFSHA_DN4_KEY_HEX']}
values['DFSHA_BOOTSTRAP_USER'] = 'demo'
descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
    stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
print('Archivo .env creado; conserva este archivo fuera de GitHub.')
