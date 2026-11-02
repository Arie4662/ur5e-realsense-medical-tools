#!/usr/bin/python3
"""A loopback-only dataset editor, independent of the robot control service."""
import argparse
import hmac
import json
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from annotation_workspace import AnnotationWorkspace

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = (Path(__file__).resolve().parent / 'data' / 'medical_tools_dataset')
DEFAULT_OUTPUT = ROOT/'dataset_annotations'/'medical_tools'


def make_server(workspace, port=8766, token=None):
    token = token or secrets.token_urlsafe(32)
    export_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def allowed_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def respond(self, value, content_type='application/json; charset=utf-8', status=200):
            if not isinstance(value, bytes): value = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(value)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            try: self.wfile.write(value)
            except (BrokenPipeError, ConnectionResetError): pass

        def do_GET(self):
            if not self.allowed_host(): self.send_error(403); return
            route = urlparse(self.path)
            try:
                if route.path == '/':
                    self.respond((ROOT/'annotation_editor.html').read_bytes().replace(b'__TOKEN__', token.encode()), 'text/html; charset=utf-8')
                elif route.path == '/editor.js':
                    self.respond((ROOT/'annotation_editor.js').read_bytes(), 'text/javascript; charset=utf-8')
                elif route.path == '/api/catalog': self.respond(workspace.catalog())
                elif route.path in ('/api/annotation', '/api/image'):
                    image_id = parse_qs(route.query)['id'][0]
                    if route.path.endswith('image'): self.respond(workspace.image(image_id), 'image/jpeg')
                    else: self.respond(workspace.annotation(image_id))
                else: self.send_error(404)
            except (ValueError, OSError, KeyError) as exc:
                self.respond({'error': str(exc)}, status=409)

        def do_POST(self):
            if (not self.allowed_host() or self.headers.get('Origin') != 'http://'+self.headers.get('Host', '')
                    or not hmac.compare_digest(self.headers.get('X-Annotation-Token', ''), token)):
                self.send_error(403); return
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 32768: raise ValueError('請求大小無效')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict): raise ValueError('請求格式無效')
                if self.path == '/api/save': self.respond(workspace.save(data))
                elif self.path == '/api/export':
                    if not export_lock.acquire(blocking=False): raise ValueError('正在匯出，請稍候')
                    try: self.respond(workspace.export(data.get('mode')))
                    finally: export_lock.release()
                else: self.send_error(404)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.respond({'error': str(exc)}, status=409)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    workspace = AnnotationWorkspace(args.source, args.output)
    server = make_server(workspace, args.port)
    print('ANNOTATION_URL=http://127.0.0.1:%d/' % server.server_port, flush=True)
    print('Images: %d; annotations: %s' % (len(workspace.entries), workspace.root), flush=True)
    try: server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt: pass
    finally: server.server_close()
