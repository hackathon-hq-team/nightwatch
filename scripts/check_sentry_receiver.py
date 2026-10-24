"""Probe signing and reachability without creating an incident or making model calls."""
import argparse
import hashlib
import hmac
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Receiver base URL (localhost or HTTPS tunnel)')
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        parser.error('Expected a base URL without credentials, path, query or fragment')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname == '127.0.0.1'):
        parser.error('Use HTTPS, or HTTP on 127.0.0.1')
    secret = dotenv_values(Path(__file__).resolve().parents[1] / '.env').get('SENTRY_WEBHOOK_SECRET')
    if not secret:
        raise SystemExit('Configure SENTRY_WEBHOOK_SECRET first')
    body = b'{"action":"resolved","data":{}}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    endpoint = args.url.rstrip('/') + '/webhooks/sentry'
    for digest, expected in ((signature, 200), ('0' * 64, 401)):
        request = Request(endpoint, data=body, headers={
            'Content-Type': 'application/json', 'Sentry-Hook-Resource': 'event_alert',
            'Sentry-Hook-Signature': digest,
        })
        try:
            with urlopen(request, timeout=15) as response:
                status, data = response.status, json.load(response)
        except HTTPError as error:
            status, data = error.code, {}
        if status != expected or (expected == 200 and data.get('ignored') is not True):
            raise SystemExit(f'Receiver probe failed: expected {expected}, got {status}')
    print('HTTPS/signing checks passed: valid signature accepted, invalid signature rejected; no incident created.')


if __name__ == '__main__':
    main()
