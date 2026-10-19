"""Serve the Unity WebGL build, gzip-compressed for browsers that accept it.

    python tools/serve_game.py DIR [--port 8000] [--bind 127.0.0.1] [--cache DIR]
                               [--mount /download=DIR ...] [--proxy /api=http://127.0.0.1:8010/api ...]

The build ships uncompressed (the .wasm alone is ~190 MB, ~30 MB gzipped).
Compressed copies are made once into --cache (outside the repo) and sent with
`Content-Encoding: gzip`, which the browser undoes transparently. Hashed files
under Build/ are cached by the browser for a day. `--mount PREFIX=DIR` serves
another folder (with a file listing) under PREFIX, e.g. the SDK downloads.
`--proxy PREFIX=URL` forwards requests under PREFIX to URL, so one public
address (a Cloudflare/ngrok tunnel or Tailscale Funnel) can carry the game,
the backend and the LLM gateway.
"""
import argparse
import gzip
import http.client
import os
import shutil
from urllib.parse import urlsplit
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COMPRESS = {".wasm", ".data", ".js", ".html", ".css", ".json"}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
               "trailers", "transfer-encoding", "upgrade", "host", "content-length"}
PROXY_TIMEOUT_S = 60


def compressed(root: Path, cache: Path, path: Path):
    """Path of the gzipped copy of `path` (made or refreshed now), or None."""
    if path.suffix not in COMPRESS or not path.is_file() or root not in path.parents:
        return None
    gz = cache / (str(path.relative_to(root)) + ".gz")
    if not gz.exists() or gz.stat().st_mtime < path.stat().st_mtime:
        gz.parent.mkdir(parents=True, exist_ok=True)
        tmp = gz.with_suffix(".tmp")
        with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        tmp.replace(gz)
    return gz


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".wasm": "application/wasm", ".data": "application/octet-stream"}

    def __init__(self, *args, cache: Path, mounts: tuple = (), proxies: tuple = (), **kwargs):
        self.cache = cache
        self.mounts = mounts
        self.proxies = proxies
        super().__init__(*args, **kwargs)

    def _upstream(self):
        route = self.path.split("?", 1)[0]
        for prefix, target in self.proxies:
            if route == prefix or route.startswith(prefix + "/"):
                return target + self.path[len(prefix):]
        return None

    def _proxy(self, url: str) -> None:
        parts = urlsplit(url)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        try:
            conn = conn_cls(parts.netloc, timeout=PROXY_TIMEOUT_S)
            conn.request(self.command, parts.path + (f"?{parts.query}" if parts.query else ""),
                         body=body, headers=headers)
            resp = conn.getresponse()
            if resp.getheader("Content-Type", "").startswith("text/event-stream"):
                return self._stream(conn, resp)
            data = resp.read()
        except OSError as exc:
            self.send_error(502, f"Upstream unavailable: {type(exc).__name__}")
            return
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() not in HOP_HEADERS:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        conn.close()

    def _stream(self, conn, resp) -> None:
        """Pass Server-Sent Events (the live leaderboard) through as they arrive."""
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() not in HOP_HEADERS:
                self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while chunk := resp.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass  # the browser left, or the backend closed the stream
        finally:
            conn.close()

    def do_GET(self):
        url = self._upstream()
        return self._proxy(url) if url else super().do_GET()

    def do_HEAD(self):
        url = self._upstream()
        return self._proxy(url) if url else super().do_HEAD()

    def do_POST(self):
        url = self._upstream()
        return self._proxy(url) if url else self.send_error(405)

    def do_OPTIONS(self):
        url = self._upstream()
        return self._proxy(url) if url else self.send_error(405)

    def translate_path(self, path):
        route = path.split("?", 1)[0].split("#", 1)[0]
        for prefix, directory in self.mounts:
            if route == prefix or route.startswith(prefix + "/"):
                root, self.directory = self.directory, directory
                try:
                    return super().translate_path(path[len(prefix):] or "/")
                finally:
                    self.directory = root
        return super().translate_path(path)

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path = path / "index.html"
        gz = compressed(Path(self.directory), self.cache, path)
        if gz is None or "gzip" not in self.headers.get("Accept-Encoding", ""):
            return super().send_head()
        f = open(gz, "rb")
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Cache-Control", "public, max-age=86400" if "/Build/" in self.path else "no-cache")
        self.end_headers()
        return f


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--cache", type=Path, default=Path(".runtime/game-gzip"))
    parser.add_argument("--mount", action="append", default=[], metavar="PREFIX=DIR",
                        help="also serve DIR under the URL prefix PREFIX")
    parser.add_argument("--proxy", action="append", default=[], metavar="PREFIX=URL",
                        help="forward requests under PREFIX to URL")
    args = parser.parse_args()
    root, cache = args.directory.resolve(), args.cache.resolve()
    for big in sorted(root.glob("Build/*")):  # compress up front, not on the first visitor
        compressed(root, cache, big)
    mounts = tuple((prefix.rstrip("/"), str(Path(d).resolve()))
                   for prefix, d in (m.split("=", 1) for m in args.mount))
    proxies = tuple((prefix.rstrip("/"), url.rstrip("/"))
                    for prefix, url in (p.split("=", 1) for p in args.proxy))
    handler = partial(Handler, directory=str(root), cache=cache, mounts=mounts, proxies=proxies)
    print(f"Serving {root} on http://{args.bind}:{args.port}", flush=True)
    ThreadingHTTPServer((args.bind, args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
