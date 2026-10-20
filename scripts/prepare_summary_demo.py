"""Local test setup. Run from the repository root, before/after starting the bot."""
import argparse
import os
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv, set_key


def main():
    parser = argparse.ArgumentParser(description='Prepare local phase-summary testing')
    parser.add_argument('--configure', action='store_true', help='Add a phase API token to root .env if missing')
    parser.add_argument('--channel-id', help='Discord text channel ID to use for testing')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = root / '.env'
    if not config.is_file():
        sys.exit('Root .env is missing. Create it first; no credentials were changed.')
    load_dotenv(config)
    token = os.getenv('PHASE_SYNC_TOKEN', '').strip()
    if args.configure:
        if not token:
            set_key(str(config), 'PHASE_SYNC_TOKEN', secrets.token_urlsafe(32))
            print('Phase API token saved to root .env; its value is not displayed.')
        elif len(token) < 32:
            sys.exit('Existing PHASE_SYNC_TOKEN is too short. Replace it in .env with at least 32 random characters.')
        else:
            print('Existing phase API token kept unchanged.')
        print('Restart the Bot before creating the test phase. Whiteboard need not restart.')
        return
    if not args.channel_id or not args.channel_id.isascii() or not args.channel_id.isdigit():
        sys.exit('Provide --channel-id with the numeric Discord channel ID.')
    if not token:
        sys.exit('Run with --configure first, then restart the Bot.')
    guild_id = os.getenv('DISCORD_GUILD_ID', '').strip()
    if not guild_id.isdigit():
        sys.exit('Set DISCORD_GUILD_ID in root .env first.')
    # This utility deliberately contacts only the local control server.
    base = 'http://127.0.0.1:' + os.getenv('PHASE_CONTROL_PORT', '5860')
    headers = {'Authorization':'Bearer '+token}
    try:
        with httpx.Client(base_url=base, headers=headers, timeout=120, trust_env=False) as client:
            status = client.get(f'/internal/v1/channels/{guild_id}/{args.channel_id}/phase')
            if status.status_code == 200:
                print('An active phase already exists; kept unchanged. You can test summary requests now.')
                return
            if status.status_code != 404:
                sys.exit(f'Phase API returned HTTP {status.status_code}. Check the token and guild configuration.')
            # Persist the event before POST so a timeout can be retried with the same ID.
            pending = root / 'data' / f'local-summary-test-{guild_id}-{args.channel_id}.json'
            import json
            if pending.exists():
                event = json.loads(pending.read_text(encoding='utf-8'))
            else:
                event = dict(event_id=str(uuid4()), experiment_id='local-summary-test',
                             guild_id=guild_id, channel_id=args.channel_id, phase_id=str(uuid4()),
                             phase_name='測試階段', sequence=1)
                pending.parent.mkdir(parents=True, exist_ok=True)
                pending.write_text(json.dumps(event, ensure_ascii=False), encoding='utf-8')
            result = client.post('/internal/v1/phase-transitions', json=event)
            if result.status_code != 200:
                sys.exit(f'Phase API returned HTTP {result.status_code}; no success assumed. Confirm Bot is ready and can view the channel.')
            print('Test phase started. Send NEW discussion messages in this channel, then @工具代理 with your instructions.')
    except httpx.RequestError:
        sys.exit('Cannot complete the local API request. Confirm Bot is running; retry the same command after fixing connectivity.')


if __name__ == '__main__':
    main()
