# === proxy/stream_router.py ===

import asyncio
import os
import ssl
from aioquic.asyncio import serve, QuicConnectionProtocol
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.quic.configuration import QuicConfiguration
from aioquic.h3.events import HeadersReceived, DataReceived
import requests

# Constants
CERT_DIR = "certs"
SERVICE_PORTS = {
    "/video": 5001,
    "/text": 5002,
    "/control": 5003
}

class H3Protocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http = H3Connection(self._quic)
        self._stream_paths = {}  # Keep track of paths for each stream

    def quic_event_received(self, event):
        for http_event in self._http.handle_event(event):
            if isinstance(http_event, HeadersReceived):
                path = None
                for name, value in http_event.headers:
                    if name == b":path":
                        path = value.decode()
                        break
                if path in SERVICE_PORTS:
                    # Store the path for this stream ID for later use
                    self._stream_paths[http_event.stream_id] = path
                    
                    service_url = f"http://localhost:{SERVICE_PORTS[path]}{path}"
                    print(f"[→] Routed {path} to {service_url}")
                    self._http.send_headers(
                        stream_id=http_event.stream_id,
                        headers=[
                            (b":status", b"200"),
                            (b"content-type", b"text/plain"),
                        ],
                    )
                    self._http.send_data(
                        stream_id=http_event.stream_id,
                        data=b"Request forwarded successfully",
                        end_stream=True
                    )
            elif isinstance(http_event, DataReceived):
                # Get path from stored streams dictionary instead of trying to call get_headers
                path = self._stream_paths.get(http_event.stream_id)
                if path and path in SERVICE_PORTS:
                    service_url = f"http://localhost:{SERVICE_PORTS[path]}{path}"
                    try:
                        response = requests.post(service_url, data=http_event.data)
                        print(f"[→] Routed {path} to {service_url} | Status: {response.status_code}")
                    except Exception as e:
                        print(f"[❌] Error forwarding to {service_url}: {e}")

def load_quic_config():
    """Load QUIC configuration with TLS certificates"""
    config = QuicConfiguration(
        is_client=False,
        alpn_protocols=H3_ALPN,
        max_datagram_frame_size=65536,
    )
    # Create keylog file for Wireshark
    keylog_path = os.path.abspath("quic_sslkeylog.log")
    print(f"[🔑] Writing TLS keys to: {keylog_path}")
    keylog_file = open(keylog_path, "w", buffering=1)  # Line buffered
    config.secrets_log_file = keylog_file
    # Load TLS certificates
    config.load_cert_chain(
        certfile=os.path.join(CERT_DIR, "cert.pem"),
        keyfile=os.path.join(CERT_DIR, "key.pem")
    )
    return config, keylog_file

async def main():
    config, keylog_file = load_quic_config()
    try:
        print(f"[🔐] QUIC Proxy started at 0.0.0.0:4433, waiting for HTTP/3 streams...")
        await serve(
            "0.0.0.0",
            4433,
            configuration=config,
            create_protocol=H3Protocol
        )
        await asyncio.Future()  # Run forever
    finally:
        keylog_file.flush()
        keylog_file.close()
        print("[🔑] TLS key log file closed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
