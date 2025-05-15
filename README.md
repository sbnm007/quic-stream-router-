# QUIC Stream Router

A HTTP/3 (QUIC) stream multiplexing proxy for routing different streams to microservices with Wireshark decryption support.

## Overview

This project implements a QUIC proxy server that routes HTTP/3 streams to different backend microservices based on path. It demonstrates:

1. **HTTP/3 Stream Multiplexing** - Multiple streams on a single QUIC connection
2. **Microservice Routing** - Directing streams to appropriate backend services
3. **TLS Key Logging** - Enabling Wireshark decryption of QUIC traffic

The system includes a QUIC proxy server that forwards requests to three microservices (video, text, and control), and a test client that sends sample data to all services.

## Components

- **Proxy Server** (`proxy/stream_router.py`): HTTP/3 proxy that routes streams based on path
- **Microservices**:
  - Video Service (`services/video_service/app.py`): Handles video streams on port 5001
  - Text Service (`services/text_service/app.py`): Handles text streams on port 5002
  - Control Service (`services/control_service/app.py`): Handles control commands on port 5003
- **Test Client** (`client/quic_stream_client.py`): Sends test data over multiple streams

## Setup

### Requirements

- Python 3.8+ (tested with Python 3.12)
- aioquic 0.9.20
- Flask
- Requests

### Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/quic-stream-router.git
   cd quic-stream-router
   ```

2. Create and activate a virtual environment:
   ```
   python3 -m venv q
   source q/bin/activate
   ```

3. Install dependencies:
   ```
   pip install aioquic==0.9.20 flask requests
   ```

## Usage

### Starting the Services

1. Start the QUIC proxy server:
   ```
   source q/bin/activate
   python3 proxy/stream_router.py
   ```

2. Start the microservices (in separate terminals):
   ```
   # Video Service
   source q/bin/activate
   cd services/video_service
   python3 app.py

   # Text Service
   source q/bin/activate
   cd services/text_service
   python3 app.py

   # Control Service
   source q/bin/activate
   cd services/control_service
   python3 app.py
   ```

3. Run the test client:
   ```
   source q/bin/activate
   cd client
   python3 quic_stream_client.py
   ```

### Decrypting QUIC Traffic in Wireshark

The system logs TLS session keys to enable Wireshark decryption of QUIC traffic:

1. Capture the QUIC traffic (UDP port 4433)
2. In Wireshark, go to Edit > Preferences > Protocols > TLS
3. Set "(Pre)-Master-Secret log filename" to the log file location:
   - Client: `/path/to/quic-stream-router/client/quic_sslkeylog.log`
   - Server: `/path/to/quic-stream-router/quic_sslkeylog.log`
4. Apply a filter: `quic` or `udp.port == 4433`
5. You should now see decrypted HTTP/3 frames and application data

## Architecture

```
┌─────────────┐     QUIC/HTTP3     ┌──────────────┐     HTTP     ┌──────────────┐
│ QUIC Client ├────────────────────┤ QUIC Proxy   ├─────────────►│ Video Service│
│             │                    │              │              └──────────────┘
│ Stream 0    │                    │ Stream Router│
│ Stream 4    │                    │              │     HTTP     ┌──────────────┐
│ Stream 8    │                    │ Port 4433    ├─────────────►│ Text Service │
└─────────────┘                    │              │              └──────────────┘
                                   │              │
                                   │              │     HTTP     ┌──────────────┐
                                   │              ├─────────────►│Control Service│
                                   └──────────────┘              └──────────────┘
```

## TLS Key Logging

The system implements TLS key logging for QUIC traffic decryption:

- Keys are logged to `quic_sslkeylog.log` in NSS key log format
- Both client and server write to separate key log files
- These keys can be used with Wireshark to decrypt the QUIC traffic

## Troubleshooting

- **Empty keylog file**: Ensure the file is opened with `buffering=1` (line buffering) and properly closed
- **Connection errors**: Check that all services are running and ports are available
- **Wireshark not decrypting**: Verify the key log file path and that traffic was captured after key logging was enabled

## License

MIT
