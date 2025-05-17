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

## Performance Evaluation

The system provides comprehensive performance metrics for QUIC stream multiplexing and routing. Performance data is saved in timestamped directories under `results/` with detailed analysis in both JSON and human-readable formats.

### Metrics Collected

1. **Connection Metrics**
   - Connection establishment time (0-RTT/1-RTT handshake)
   - Total streams established
   - Total bytes transferred
   - Total packets sent/received
   - Stream establishment times
   - Error tracking

2. **Per-Stream Metrics**
   - Duration
   - Bytes sent/received
   - Packets sent/received
   - Retransmissions
   - QUIC latency (protocol processing time)
   - HTTP latency (proxy-to-backend time)
   - Throughput samples
   - Packet loss rate
   - Average packet size
   - Jitter (for video streams)

### Performance Report Structure

The system generates two main output files:

1. **quic_metrics.json**
   - Raw metrics data in JSON format
   - Detailed stream-level statistics
   - Connection-level metrics
   - Timestamp and uptime information

2. **quic_performance_report.txt**
   - Human-readable performance analysis
   - Connection establishment analysis
   - Overall statistics
   - Detailed stream analysis
   - Error summaries

### Key Performance Indicators

1. **Connection Performance**
   - Connection setup time (typically 20-30ms)
   - Stream establishment time (20-70ms per stream)
   - Maximum concurrent streams
   - Stream establishment rate

2. **Stream Performance**
   - QUIC latency: Measures protocol processing time
   - HTTP latency: Measures proxy-to-backend communication
   - Throughput: Bytes per second
   - Packet statistics: Loss rate, retransmissions
   - Stream type analysis (video/text/control)

3. **Error Analysis**
   - Connection errors
   - Stream-level errors
   - Backend service errors

### Performance Monitoring

The system includes real-time performance monitoring:

1. **Periodic Metrics Saving**
   - Metrics saved every 60 seconds
   - Automatic cleanup on shutdown
   - Timestamped results for comparison

2. **Packet Capture**
   - Automatic tcpdump capture
   - Wireshark decryption support
   - Packet-level analysis

### Performance Optimization

The system implements several optimizations:

1. **Stream Processing**
   - Batch processing of stream data
   - Efficient stream demultiplexing
   - Connection pooling for HTTP requests

2. **Resource Management**
   - Reduced idle timeout (5 seconds)
   - Efficient memory usage
   - Proper cleanup of resources

3. **Error Handling**
   - Comprehensive error tracking
   - Graceful error recovery
   - Detailed error reporting

### Performance Analysis Tools

1. **Wireshark Integration**
   - TLS key logging for decryption
   - Packet-level analysis
   - Protocol debugging

2. **Metrics Visualization**
   - JSON format for programmatic analysis
   - Human-readable reports
   - Error summaries

### Best Practices

1. **Testing**
   - Run multiple concurrent streams
   - Monitor packet capture
   - Check error logs

2. **Optimization**
   - Monitor connection establishment time
   - Track stream latency
   - Analyze packet loss

3. **Troubleshooting**
   - Check performance reports
   - Analyze error summaries
   - Monitor resource usage

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
