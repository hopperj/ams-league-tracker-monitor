import io
import json
import unittest
from urllib.parse import unquote
from unittest.mock import patch

from ams_recorder.join import JoinError, current_server, steam_url


class JoinTest(unittest.TestCase):
    def test_reads_fresh_public_connection_without_upload_credentials(self):
        join = dict(name='Current lobby', address='198.51.100.21:27015', password=False)
        with patch('ams_recorder.join.urlopen', return_value=io.BytesIO(json.dumps(dict(server_join=join)).encode())) as fetch:
            self.assertEqual(current_server('https://ams2.ioresearch.ca:5443/'), join)
        request = fetch.call_args[0][0]
        self.assertEqual(request.full_url, 'https://ams2.ioresearch.ca/api/public/me')
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(request.get_header('Cache-control'), 'no-cache')
        self.assertEqual(fetch.call_args[1]['timeout'], 5)
        self.assertEqual(unquote(steam_url(join)), 'steam://run/1066890//-connect 198.51.100.21:27015')

    def test_private_password_is_quoted_and_public_lobbies_do_not_include_it(self):
        join = dict(name='Private lobby', address='server.example:27015', password=True)
        with self.assertRaises(JoinError):steam_url(join)
        self.assertEqual(unquote(steam_url(join, 'a "quoted" \\ secret')),
                         'steam://run/1066890//-connect server.example:27015 -password "a \\"quoted\\" \\\\ secret"')
        self.assertNotIn('password', unquote(steam_url(dict(join, password=False), 'old password')))

    def test_invalid_or_missing_connections_cannot_become_steam_launch_arguments(self):
        for join in (None, {}, dict(address='198.51.100.21:27015', password='false'),
                     *[dict(address=address, password=False) for address in (
                         '198.51.100.21:0', '198.51.100.21:65536', '-connect example:27015',
                         'server.example:27015 -password something', 'server.example:27015\n-option',
                         'steam://run/another-app', 'server.example:27015/?extra')]):
            with self.subTest(join=join), patch('ams_recorder.join.urlopen', return_value=io.BytesIO(json.dumps(dict(server_join=join)).encode())):
                with self.assertRaises(JoinError):current_server('https://example.invalid')

    def test_failed_and_oversized_responses_do_not_return_a_launch_target(self):
        for payload in (b'not json', b' ' * 65537):
            with self.subTest(size=len(payload)), patch('ams_recorder.join.urlopen', return_value=io.BytesIO(payload)):
                with self.assertRaises(ValueError):current_server('https://example.invalid')
        with patch('ams_recorder.join.urlopen') as fetch:
            with self.assertRaises(ValueError):current_server('http://remote.example')
            with self.assertRaises(ValueError):current_server('https://token@example.invalid')
            fetch.assert_not_called()


if __name__ == '__main__':unittest.main()
