# === proxy/stream_router.py ===

import asyncio
import os
import ssl
import aiohttp

from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import HeadersReceived, DataReceived

# Map incoming :path to internal service URLs
ROUTE_MAP = {
    "/video": "http://localhost:5001/video",
    "/text": "http://localhost:5002/text",
    "/control": "http://localhost:5003/control"
}

# QUIC HTTP/3 event handler
class H3RequestHandler:
    def __init__(self):
        self.stream_buffers = {}

    async def handle_event(self, event, stream_id, http, session):
        if isinstance(event, HeadersReceived):
            path = dict(event.headers).get(b":path", b"/").decode()
            self.stream_buffers[stream_id] = {"path": path, "data": b""}

        elif isinstance(event, DataReceived):
            buffer = self.stream_buffers.get(stream_id, {})
            buffer["data"] += event.data
            self.stream_buffers[stream_id] = buffer

            if event.stream_ended:
                await self.forward_stream(buffer["path"], buffer["data"], session)

    async def forward_stream(self, path, data, session):
        url = ROUTE_MAP.get(path)
        if url:
            try:
                async with session.post(url, data=data) as resp:
                    print(f"[→] Routed {path} to {url} | Status: {resp.status}")
            except Exception as e:
                print(f"[x] Error routing {path} → {url}: {e}")
        else:
            print(f"[x] No route defined for {path}")


# Subclass QuicConnectionProtocol for QUIC + HTTP/3
class H3Protocol(QuicConnectionProtocol):
    def __init__(self, *args, handler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._http = H3Connection(self._quic)
        self._handler = handler or H3RequestHandler()
        self._session = aiohttp.ClientSession()

    def quic_event_received(self, event):
        for http_event in self._http.handle_event(event):
            asyncio.ensure_future(
                self._handler.handle_event(http_event, event.stream_id, self._http, self._session)
            )

    async def connection_lost(self, exc):
        await self._session.close()
        return await super().connection_lost(exc)


# Load TLS certs
def load_quic_config():
    config = QuicConfiguration(
        is_client=False,
        alpn_protocols=H3_ALPN
    )
    cert_path = os.path.join("certs", "cert.pem")
    key_path = os.path.join("certs", "key.pem")
    config.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return config


# Entrypoint
async def main(host="0.0.0.0", port=4433):
    config = load_quic_config()
    handler = H3RequestHandler()

    print(f"[🔐] QUIC Proxy started at {host}:{port}, waiting for HTTP/3 streams...")

    await serve(
        host,
        port,
        configuration=config,
        create_protocol=lambda *args, **kwargs: H3Protocol(*args, handler=handler, **kwargs)
    )

    await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(main())
