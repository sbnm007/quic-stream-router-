import asyncio
import ssl
import os
import sys
from aioquic.asyncio import connect
from aioquic.h3.connection import H3Connection, H3_ALPN
from aioquic.quic.configuration import QuicConfiguration

# Optional patch for Python 3.12's stream issue
if sys.version_info >= (3, 12):
    import asyncio.transports
    asyncio.transports.BaseTransport.is_closing = lambda self: True

# Stream payloads (split into chunks)
STREAM_DATA = {
    "/video": [b"This is ", b"video ", b"stream ", b"data..."],
    "/text": [b"This is ", b"text ", b"stream ", b"data..."],
    "/control": [b"This is ", b"control ", b"command."]
}

async def send_streams(host, port):
    # Set up TLS key logging
    keylog_path = os.path.abspath("quic_sslkeylog.log")
    print(f"[🔑] Writing TLS keys to: {keylog_path}")
    keylog_file = open(keylog_path, "w", buffering=1)  # Line buffered

    # Configure QUIC client
    config = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN)
    config.verify_mode = ssl.CERT_NONE
    config.secrets_log_file = keylog_file
    config.session_ticket_fetcher = None

    try:
        async with connect(host, port, configuration=config) as client:
            quic = client._quic
            h3_conn = H3Connection(quic)

            # Assign stream IDs and send initial headers
            stream_ids = {}
            for path in STREAM_DATA:
                stream_id = quic.get_next_available_stream_id()
                stream_ids[path] = stream_id
                h3_conn.send_headers(
                    stream_id,
                    [
                        (b":method", b"POST"),
                        (b":scheme", b"https"),
                        (b":authority", host.encode()),
                        (b":path", path.encode())
                    ]
                )
                print(f"[✓] Opened stream to {path} with stream ID {stream_id}")

            # Send data chunks interleaved across streams
            max_chunks = max(len(chunks) for chunks in STREAM_DATA.values())
            for i in range(max_chunks):
                for path, chunks in STREAM_DATA.items():
                    if i < len(chunks):
                        stream_id = stream_ids[path]
                        h3_conn.send_data(stream_id, chunks[i], end_stream=(i == len(chunks) - 1))
                        print(f"[→] Sent chunk {i+1}/{len(chunks)} to {path} (stream {stream_id})")
                client.transmit()
                await asyncio.sleep(1)

            await asyncio.sleep(2)
    finally:
        keylog_file.flush()
        keylog_file.close()
        print("[🔑] TLS key log file closed")

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 4433
    asyncio.run(send_streams(host, port))
