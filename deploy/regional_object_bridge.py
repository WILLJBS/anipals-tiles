"""Loopback-only tile_url bridge. Explicitly opt-in; not wired into production."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import shutil
import threading
import uuid

ROUTE = re.compile(r'/tiles/([a-z0-9-]+)/([0-9a-f]{64})/([012]/(?:[0-9]{3}/)*[0-9]{3}\.gph)')


class Bridge:
    def __init__(self, catalog, cache, fetch):
        self.catalog, self.cache, self.fetch = catalog, cache, fetch
        self.failures = 0
        self.requests = {}
        self.lock = threading.Lock()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, *_):
                pass  # No key, credentials or request metadata in HTTP logs.

            def do_GET(self):
                path, token = self.path, None
                if path.startswith('/requests/'):
                    parts = path.split('/', 3)
                    if len(parts) == 4:
                        token, path = parts[2], '/' + parts[3]
                match = ROUTE.fullmatch(path)
                if match is None:
                    self.send_error(404)
                    return
                if token is not None:
                    with bridge.lock:
                        request = bridge.requests.get(token)
                    if request is None or request['graph'] != match.groups()[:2]:
                        self.send_error(404)
                        return
                try:
                    item = bridge.catalog.lookup(*match.groups())
                except KeyError:
                    self.send_error(404)
                    return
                headers_sent = False
                try:
                    with bridge.cache.open(item, bridge.fetch) as stream:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Length', str(item['size']))
                        self.end_headers()
                        headers_sent = True
                        shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
                except (OSError, ValueError):
                    with bridge.lock:
                        bridge.failures += 1
                        if token in bridge.requests:
                            bridge.requests[token]['failed'] = True
                    self.close_connection = True
                    # Failure stays visible to the native-request supervisor;
                    # callers must not misclassify R2 failure as no-route 404.
                    try:
                        if not headers_sent:
                            self.send_error(503)
                    except OSError:
                        pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def url(self, slug, fingerprint):
        if (slug, fingerprint) not in self.catalog.graphs:
            raise ValueError('unregistered bridge graph')
        return 'http://127.0.0.1:%d/tiles/%s/%s/{tilePath}' % (
            self.server.server_port, slug, fingerprint)

    def begin(self, region):
        token = uuid.uuid4().hex
        fingerprint = region.get('object_fingerprint', region['fingerprint'])
        url = self.url(region['slug'], fingerprint)
        with self.lock:
            self.requests[token] = {'graph': (region['slug'], fingerprint), 'failed': False}
        return token, url.replace('/tiles/', '/requests/' + token + '/tiles/', 1)

    def failed(self, token):
        with self.lock:
            return self.requests[token]['failed']

    def end(self, token):
        with self.lock:
            self.requests.pop(token, None)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
