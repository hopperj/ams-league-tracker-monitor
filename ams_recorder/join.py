"""Read the current lobby connection and launch AMS2 through Steam."""
import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import __version__
from .transport import Transport, canonical_server


class JoinError(ValueError):
    pass


def connection(value):
    if not isinstance(value, dict):
        raise JoinError('The dedicated server connection is unavailable. Try again shortly.')
    address = value.get('address')
    if (not isinstance(address, str) or not re.fullmatch(r'[A-Za-z0-9.-]+:[1-9][0-9]{0,4}', address)
            or int(address.rsplit(':', 1)[1]) > 65535 or type(value.get('password')) is not bool):
        raise JoinError('The webapp returned invalid dedicated server connection details.')
    name = value.get('name')
    return dict(name=name[:200] if isinstance(name, str) and name else 'Dedicated server',
                address=address, password=value['password'])


def current_server(server):
    # Validate the configured origin, but never send the recorder's upload credential.
    origin = Transport(canonical_server(server), '').server
    request = Request(origin + '/api/public/me', headers={
        'Accept': 'application/json', 'Cache-Control': 'no-cache',
        'User-Agent': 'AMSLeagueRecorder/' + __version__})
    with urlopen(request, timeout=5) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise JoinError('The webapp returned invalid dedicated server connection details.')
    value = json.loads(raw)
    return connection(value.get('server_join') if isinstance(value, dict) else None)


def steam_url(join, password=''):
    join = connection(join)
    parameters = '-connect ' + join['address']
    if join['password']:
        if not password:
            raise JoinError('Enter the lobby password to join this server.')
        literal = password.replace('\\', '\\\\').replace('"', '\\"')
        parameters += ' -password "' + literal + '"'
    return 'steam://run/1066890//' + quote(parameters, safe='')
