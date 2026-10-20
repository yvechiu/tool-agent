"""Private control API; never posts phase transitions to Discord."""
import hmac
from aiohttp import web
from utils.phase_store import PhaseConflict


FIELDS = {'event_id', 'experiment_id', 'guild_id', 'channel_id', 'phase_id', 'phase_name', 'sequence'}


def validate_event(data):
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError('expected event_id, experiment_id, guild_id, channel_id, phase_id, phase_name, sequence')
    for key in FIELDS - {'sequence'}:
        limit = 120 if key == 'phase_name' else 128
        if not isinstance(data[key], str) or not data[key].strip() or len(data[key]) > limit:
            raise ValueError(f'invalid {key}')
    for key in ('guild_id', 'channel_id'):
        if not data[key].isascii() or not data[key].isdigit() or not 0 < int(data[key]) < 2**64:
            raise ValueError(f'{key} must be a positive Discord ID string')
        if data[key] != str(int(data[key])):
            raise ValueError(f'{key} must be canonical')
    if type(data['sequence']) is not int or not 1 <= data['sequence'] <= 2**31-1:
        raise ValueError('sequence must be a positive integer')
    return data


def create_control_app(store, token, allowed_guild_id, channel_lock, validate_channel):
    if len(token) < 32:
        raise ValueError('PHASE_SYNC_TOKEN must contain at least 32 characters')
    if not allowed_guild_id or not str(allowed_guild_id).isdigit():
        raise ValueError('DISCORD_GUILD_ID is required for the control API')

    @web.middleware
    async def auth(request, handler):
        provided = request.headers.get('Authorization', '')
        if not hmac.compare_digest(provided.encode(), ('Bearer ' + token).encode()):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[auth], client_max_size=16 * 1024)

    async def transition(request):
        try:
            data = validate_event(await request.json())
        except (ValueError, UnicodeError):
            return web.json_response({'error': 'Invalid transition payload'}, status=400)
        if data['guild_id'] != str(allowed_guild_id):
            return web.json_response({'error': 'Guild is not allowed'}, status=403)
        # Resolve the channel through Discord, not caller-supplied bot identities.
        if not await validate_channel(data['guild_id'], data['channel_id']):
            return web.json_response({'error': 'Channel unavailable or bot not ready'}, status=503)
        async with channel_lock(int(data['guild_id']), int(data['channel_id'])):
            try:
                result = store.transition(data)
            except PhaseConflict as exc:
                return web.json_response({'error': str(exc)}, status=409)
        return web.json_response(result)

    async def current(request):
        guild, channel = request.match_info['guild'], request.match_info['channel']
        if guild != str(allowed_guild_id):
            return web.json_response({'error': 'Guild is not allowed'}, status=403)
        result = store.current(guild, channel)
        if not result:
            return web.json_response({'error': 'No active phase'}, status=404)
        return web.json_response(result)

    app.router.add_post('/internal/v1/phase-transitions', transition)
    app.router.add_get('/internal/v1/channels/{guild}/{channel}/phase', current)
    return app
