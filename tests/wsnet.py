"""A local TCP listener that records every connection made to it.

Pointing proxies or remote URLs at it turns "made no network call" into a deterministic
check (the connection count stays 0) instead of a wall-clock bound. Each connection is
accepted, counted and closed at once, so a client that does try fails fast instead of hanging.
"""

import socket
import threading


class Tripwire:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self._lock = threading.Lock()
        self._count = 0
        self._closed = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self._lock:
                self._count += 1
            conn.close()

    @property
    def connections(self):
        with self._lock:
            return self._count

    def proxy_env(self):
        return {k: self.url for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy")}

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self._thread.join(timeout=5)
