import asyncio
import h2.connection
import h2.config
import h2.events
import h2.exceptions
import socket
import ssl
import time
import json
import os
import signal
import requests
import logging
from typing import Dict, Optional, List, Set

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('http2_server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('http2_server')

# Constants
SERVICE_PORTS = {
    "/video": 5001,
    "/text": 5002,
    "/control": 5003
}

class StreamMetrics:
    def __init__(self, stream_id: int, path: str):
        self.stream_id = stream_id
        self.path = path
        self.start_time = time.time()
        self.end_time = None
        self.bytes_sent = 0
        self.bytes_received = 0
        self.http_latency = []
        self.errors = []

    @property
    def duration(self):
        return (self.end_time or time.time()) - self.start_time

    @property
    def average_throughput(self):
        if self.duration > 0:
            return (self.bytes_sent + self.bytes_received) / self.duration
        return 0

    def to_dict(self):
        return {
            'stream_id': self.stream_id,
            'path': self.path,
            'duration': self.duration,
            'bytes_sent': self.bytes_sent,
            'bytes_received': self.bytes_received,
            'http_latency': self.http_latency,
            'errors': self.errors,
            'average_throughput': self.average_throughput
        }

class PerformanceMonitor:
    def __init__(self):
        self.stream_metrics = {}
        self.connection_metrics = {
            'start_time': time.time(),
            'connection_establishment_time': 0,
            'streams': set(),
            'total_bytes_sent': 0,
            'total_bytes_received': 0,
            'stream_establishment_times': [],
            'errors': []
        }
        self.start_time = time.time()

    def record_stream_start(self, stream_id: int, path: str):
        logger.debug(f"Starting stream {stream_id} for path {path}")
        self.stream_metrics[stream_id] = StreamMetrics(stream_id, path)
        self.connection_metrics['streams'].add(stream_id)

    def record_stream_end(self, stream_id: int):
        if stream_id in self.stream_metrics:
            logger.debug(f"Ending stream {stream_id}")
            metrics = self.stream_metrics[stream_id]
            metrics.end_time = time.time()
            
            # Update connection metrics
            self.connection_metrics['total_bytes_sent'] += metrics.bytes_sent
            self.connection_metrics['total_bytes_received'] += metrics.bytes_received

    def record_latency(self, stream_id: int, latency: float):
        if stream_id in self.stream_metrics:
            logger.debug(f"Recording latency {latency:.3f}s for stream {stream_id}")
            self.stream_metrics[stream_id].http_latency.append(latency)

    def record_bytes(self, stream_id: int, sent: int = 0, received: int = 0):
        if stream_id in self.stream_metrics:
            if sent > 0:
                logger.debug(f"Recording {sent} bytes sent for stream {stream_id}")
            if received > 0:
                logger.debug(f"Recording {received} bytes received for stream {stream_id}")
            self.stream_metrics[stream_id].bytes_sent += sent
            self.stream_metrics[stream_id].bytes_received += received

    def record_error(self, stream_id: Optional[int], error: str):
        logger.error(f"Error on stream {stream_id if stream_id is not None else 'connection'}: {error}")
        if stream_id is not None and stream_id in self.stream_metrics:
            self.stream_metrics[stream_id].errors.append(error)
        self.connection_metrics['errors'].append(error)

    def save_metrics(self):
        """Save performance metrics to file"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        results_dir = os.path.join("results", "http2", timestamp)
        os.makedirs(results_dir, exist_ok=True)
        
        # Convert metrics to dictionary format
        metrics = {
            'stream_metrics': {
                stream_id: metrics.to_dict()
                for stream_id, metrics in self.stream_metrics.items()
            },
            'connection_metrics': {
                k: list(v) if isinstance(v, set) else v 
                for k, v in self.connection_metrics.items()
            },
            'uptime': time.time() - self.start_time,
            'timestamp': timestamp
        }
        
        # Save JSON metrics
        json_file = os.path.join(results_dir, 'http2_metrics.json')
        with open(json_file, 'w') as f:
            json.dump(metrics, f, indent=2)
        logger.info(f"Metrics saved to {os.path.abspath(json_file)}")
        
        # Generate report
        report_file = os.path.join(results_dir, 'http2_performance_report.txt')
        self.generate_report(report_file)
        logger.info(f"Performance report saved to {os.path.abspath(report_file)}")

    def generate_report(self, filename: str):
        """Generate a human-readable performance report"""
        with open(filename, 'w') as f:
            f.write("HTTP/2 Performance Report\n")
            f.write("=======================\n\n")
            f.write(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # Connection Establishment Analysis
            f.write("Connection Establishment\n")
            f.write("----------------------\n")
            if self.connection_metrics:
                f.write(f"Connection Setup Time: {self.connection_metrics['connection_establishment_time']*1000:.2f}ms\n")
                if self.connection_metrics['stream_establishment_times']:
                    avg_stream_setup = sum(self.connection_metrics['stream_establishment_times']) / len(self.connection_metrics['stream_establishment_times'])
                    f.write(f"Average Stream Setup Time: {avg_stream_setup*1000:.2f}ms\n")
                    f.write(f"Total Streams Established: {len(self.connection_metrics['stream_establishment_times'])}\n")
            f.write("\n")
            
            # Overall Statistics
            f.write("Overall Statistics\n")
            f.write("-----------------\n")
            total_streams = len(self.stream_metrics)
            total_bytes = sum(metrics.bytes_sent + metrics.bytes_received for metrics in self.stream_metrics.values())
            f.write(f"Total Streams: {total_streams}\n")
            f.write(f"Total Bytes Transferred: {total_bytes}\n")
            f.write(f"Uptime: {time.time() - self.start_time:.2f} seconds\n\n")
            
            # Multiplexing Analysis
            stream_times = [metrics.start_time for metrics in self.stream_metrics.values()]
            stream_times.sort()
            
            concurrent_streams = 1
            max_concurrent = 1
            for i in range(1, len(stream_times)):
                if stream_times[i] - stream_times[i-1] < 0.1:  # Streams within 100ms are considered concurrent
                    concurrent_streams += 1
                    max_concurrent = max(max_concurrent, concurrent_streams)
                else:
                    concurrent_streams = 1
            
            if len(stream_times) > 1:
                avg_time_between = sum(stream_times[i] - stream_times[i-1] for i in range(1, len(stream_times))) / (len(stream_times) - 1)
                stream_rate = len(stream_times) / (time.time() - self.start_time)
            else:
                avg_time_between = 0
                stream_rate = 0
            
            f.write("Multiplexing Analysis\n")
            f.write("--------------------\n")
            f.write(f"Maximum Concurrent Streams: {max_concurrent}\n")
            f.write(f"Average Time Between Streams: {avg_time_between*1000:.2f}ms\n")
            f.write(f"Stream Establishment Rate: {stream_rate:.2f} streams/second\n\n")
            
            # Error Summary
            if self.connection_metrics['errors']:
                f.write("Error Summary\n")
                f.write("-------------\n")
                for error in self.connection_metrics['errors']:
                    f.write(f"- {error}\n")
                f.write("\n")
            
            # Detailed Stream Analysis
            f.write("Detailed Stream Analysis\n")
            f.write("----------------------\n")
            for stream_id, metrics in self.stream_metrics.items():
                f.write(f"\nStream {stream_id} ({metrics.path}):\n")
                f.write(f"  Duration: {metrics.duration:.2f}s\n")
                f.write(f"  Total Bytes: {metrics.bytes_sent + metrics.bytes_received}\n")
                f.write(f"  Average Throughput: {metrics.average_throughput:.2f} bytes/s\n")
                
                if metrics.http_latency:
                    f.write(f"  HTTP Latency (ms):\n")
                    http_latencies_ms = [lat * 1000 for lat in metrics.http_latency]
                    f.write(f"    Average: {sum(http_latencies_ms) / len(http_latencies_ms):.2f}\n")
                    f.write(f"    Min: {min(http_latencies_ms):.2f}\n")
                    f.write(f"    Max: {max(http_latencies_ms):.2f}\n")
                    f.write(f"    Samples: {len(http_latencies_ms)}\n")
                
                if metrics.errors:
                    f.write(f"  Errors:\n")
                    for error in metrics.errors:
                        f.write(f"    - {error}\n")

class HTTP2Server:
    def __init__(self, host: str = 'localhost', port: int = 8080, use_tls: bool = False):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.performance_monitor = PerformanceMonitor()
        self.running = True
        
    def create_ssl_context(self):
        """Create SSL context for TLS connections"""
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(
            certfile=os.path.join("certs", "cert.pem"),
            keyfile=os.path.join("certs", "key.pem")
        )
        context.set_alpn_protocols(['h2'])
        return context

    async def handle_connection(self, sock: socket.socket):
        """Handle a single client connection"""
        start_time = time.time()
        client_address = sock.getpeername()
        logger.info(f"New connection from {client_address}")
        
        # Create H2 connection
        config = h2.config.H2Configuration(client_side=False)
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()
        
        # Send initial connection preface and settings
        preface_and_settings_data = conn.data_to_send()
        logger.debug(f"Sending connection preface and settings: {len(preface_and_settings_data)} bytes")
        try:
            sock.sendall(preface_and_settings_data)
            logger.debug("Connection preface and settings sent successfully")
        except Exception as e:
            logger.error(f"Failed to send connection preface and settings: {str(e)}")
            return
        
        # Wait for client's settings frame (and preface if not already received)
        handshake_complete = False
        while not handshake_complete:
            try:
                logger.debug("Server: Waiting for client data...")
                # Set a timeout for receiving data
                sock.settimeout(1.0)
                try:
                    data = sock.recv(65535)
                    logger.debug(f"Server: Received {len(data)} bytes during setup")
                    logger.debug(f"Server: Raw data: {data.hex()}")
                except socket.timeout:
                    logger.debug("Server: Receive timed out, no data received.")
                    continue # Keep waiting
                except Exception as e:
                    logger.error(f"Server: Error during sock.recv() setup: {str(e)}")
                    return
                    
                if not data:
                    logger.error("Server: Connection closed by client during setup")
                    return
                
                # Process received data
                try:
                    events = conn.receive_data(data)
                    logger.debug(f"Server: Processing {len(events)} events during setup")
                    
                    for event in events:
                        logger.debug(f"Server: Setup event type: {type(event).__name__}")
                        if isinstance(event, h2.events.RemoteSettingsChanged):
                            logger.info(f"Server: Received settings: {event.changed_settings}")
                            # After receiving settings, we can consider the connection established
                            logger.info("Server: HTTP/2 connection established")
                            handshake_complete = True
                            break
                        elif isinstance(event, h2.events.ConnectionTerminated):
                            logger.error(f"Server: Connection terminated during setup: {event.error_code}")
                            return
                        elif isinstance(event, h2.events.WindowUpdated):
                            logger.debug(f"Server: Window updated: stream_id={event.stream_id}, delta={event.delta}")
                        else:
                            logger.debug(f"Server: Unhandled event type during setup: {type(event).__name__}")
                    
                    # Send any pending data (like SETTINGS ACK)
                    data_to_send = conn.data_to_send()
                    if data_to_send:
                        logger.debug(f"Server: Sending {len(data_to_send)} bytes during setup")
                        try:
                            sock.sendall(data_to_send)
                            logger.debug("Server: Pending data sent successfully")
                        except Exception as e:
                            logger.error(f"Server: Failed to send pending data during setup: {str(e)}")
                            return
                        
                except h2.exceptions.ProtocolError as e:
                    logger.error(f"Server: Protocol error during setup: {str(e)}")
                    return
                except Exception as e:
                    logger.error(f"Server: Error processing data during setup: {str(e)}")
                    return
                    
            except Exception as e:
                logger.error(f"Server: Unexpected error during setup loop: {str(e)}")
                return
        
        # Record connection establishment time
        self.performance_monitor.connection_metrics['connection_establishment_time'] = time.time() - start_time
        logger.info(f"Server: Connection established in {self.performance_monitor.connection_metrics['connection_establishment_time']:.3f}s")
        
        # Dictionary to store active streams
        active_streams = {}
        
        # Main connection handling loop
        while self.running:
            try:
                logger.debug("Server: Waiting for data...")
                # Use non-blocking read for the main loop with asyncio
                data = await asyncio.wait_for(asyncio.get_event_loop().sock_recv(sock, 65535), timeout=1.0)
                logger.debug(f"Server: Received {len(data)} bytes")
                logger.debug(f"Server: Raw data: {data.hex()}")
                
                if not data:
                    logger.info("Server: Connection closed by client")
                    break
                
                events = conn.receive_data(data)
                logger.debug(f"Server: Processing {len(events)} events")
                
                for event in events:
                    logger.debug(f"Server: Event type: {type(event).__name__}")
                    
                    if isinstance(event, h2.events.RequestReceived):
                        # Record stream start
                        stream_id = event.stream_id
                        path = next((v for k, v in event.headers if k == b':path'), b'/').decode()
                        logger.info(f"Server: Received request on stream {stream_id} for {path}")
                        logger.debug(f"Server: Headers: {dict(event.headers)}")
                        self.performance_monitor.record_stream_start(stream_id, path)
                        self.performance_monitor.connection_metrics['stream_establishment_times'].append(time.time() - start_time)
                        # Store only path and headers, no data accumulation
                        active_streams[stream_id] = {
                            'path': path,
                            'headers': dict(event.headers)
                        }
                    
                    elif isinstance(event, h2.events.DataReceived):
                        logger.debug(f"Server: Received {len(event.data)} bytes of data on stream {event.stream_id}")
                        if event.stream_id in active_streams:
                            stream_info = active_streams[event.stream_id]
                            path = stream_info['path']
                            # Forward chunk immediately to service
                            if path in SERVICE_PORTS:
                                service_url = f"http://localhost:{SERVICE_PORTS[path]}{path}"
                                try:
                                    request_start = time.time()
                                    logger.debug(f"Server: Forwarding chunk to {service_url}")
                                    # Forward the chunk directly
                                    response = requests.post(service_url, data=event.data)
                                    latency = time.time() - request_start
                                    # Update metrics
                                    self.performance_monitor.record_bytes(event.stream_id, received=len(event.data))
                                    self.performance_monitor.record_latency(event.stream_id, latency)
                                except Exception as e:
                                    error_msg = f"Server: Error forwarding chunk to {service_url}: {str(e)}"
                                    logger.error(error_msg)
                                    self.performance_monitor.record_error(event.stream_id, error_msg)
                        conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                    
                    elif isinstance(event, h2.events.StreamEnded):
                        logger.info(f"Server: Stream {event.stream_id} ended")
                        if event.stream_id in active_streams:
                            # Send final response
                            response_headers = [
                                (':status', '200'),
                                ('content-type', 'text/plain'),
                                ('content-length', '0')
                            ]
                            logger.info(f"Server: Sending final response for stream {event.stream_id}")
                            conn.send_headers(stream_id=event.stream_id, headers=response_headers)
                            conn.send_data(stream_id=event.stream_id, data=b'', end_stream=True)
                            # Update metrics
                            self.performance_monitor.record_stream_end(event.stream_id)
                            # Remove stream from active streams
                            del active_streams[event.stream_id]
                    
                    elif isinstance(event, h2.events.StreamReset):
                        logger.error(f"Server: Stream {event.stream_id} was reset with error code {event.error_code}")
                        if event.stream_id in active_streams:
                            del active_streams[event.stream_id]
                        self.performance_monitor.record_error(event.stream_id, f"Stream reset with error code {event.error_code}")
                    
                    elif isinstance(event, h2.events.ConnectionTerminated):
                        logger.error(f"Server: Connection terminated: {event.error_code}")
                        # This will cause the outer loop to break
                        return
                        
                    elif isinstance(event, h2.events.RemoteSettingsChanged):
                         logger.debug(f"Server: Remote settings changed during main loop: {event.changed_settings}")
                    elif isinstance(event, h2.events.SettingsAcknowledged):
                         logger.debug("Server: Settings acknowledged during main loop")
                    elif isinstance(event, h2.events.WindowUpdated):
                         logger.debug(f"Server: Window updated during main loop: stream_id={event.stream_id}, delta={event.delta}")
                    else:
                        logger.debug(f"Server: Unhandled event type during main loop: {type(event).__name__}")
                
                # Send any pending data (responses, acks, window updates, etc.)
                data_to_send = conn.data_to_send()
                if data_to_send:
                    logger.debug(f"Server: Sending {len(data_to_send)} bytes")
                    try:
                        await asyncio.get_event_loop().sock_sendall(sock, data_to_send)
                        logger.debug("Server: Data sent successfully")
                    except Exception as e:
                        logger.error(f"Server: Failed to send data: {str(e)}")
                        break # Break main loop on send failure
                    
            except asyncio.TimeoutError:
                 # No data received within timeout, continue waiting
                 pass
            except Exception as e:
                error_msg = f"Server: Error handling connection in main loop: {str(e)}"
                logger.error(error_msg)
                self.performance_monitor.record_error(None, error_msg)
                break # Break main loop on unexpected error
        
        # Connection closed or error occurred
        logger.info(f"Server: Connection with {client_address} ending.")
        
        # Clean up any remaining active streams
        for stream_id in list(active_streams.keys()):
            logger.warning(f"Server: Cleaning up stream {stream_id} during connection close.")
            del active_streams[stream_id]
        
        sock.close()
        logger.info(f"Server: Connection with {client_address} closed")

    async def start(self):
        """Start the HTTP/2 server"""
        # Create server socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Make socket non-blocking
        sock.setblocking(False)
        
        sock.bind((self.host, self.port))
        sock.listen(5)
        
        if self.use_tls:
            context = self.create_ssl_context()
            # Wrap socket after it's been made non-blocking
            sock = context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        
        logger.info(f"HTTP/2 Server started on {self.host}:{self.port} {'with TLS' if self.use_tls else 'without TLS'}")
        
        loop = asyncio.get_event_loop()
        
        try:
            while self.running:
                try:
                    client_sock, addr = await loop.sock_accept(sock)
                    logger.info(f"Server: Accepted new connection from {addr}")
                    
                    # Make client socket non-blocking as well
                    client_sock.setblocking(False)
                    
                    # If TLS, perform handshake separately
                    if self.use_tls:
                        try:
                            await loop.sock_handshake(client_sock)
                            logger.debug(f"Server: TLS handshake completed with {addr}")
                        except Exception as e:
                            logger.error(f"Server: TLS handshake failed with {addr}: {str(e)}")
                            client_sock.close()
                            continue # Skip handling this connection
                            
                    asyncio.create_task(self.handle_connection(client_sock))
                    
                except Exception as e:
                    logger.error(f"Server: Error accepting connection: {str(e)}")
                    # Allow server to continue running despite accept errors
                    await asyncio.sleep(0.1) # Prevent tight loop on repeated errors
                    
        except KeyboardInterrupt:
            logger.info("Server: Shutting down...")
            self.running = False
            # Delay briefly to allow ongoing tasks to finish logging
            await asyncio.sleep(1.0)
            self.performance_monitor.save_metrics()
            logger.info("Server: Shutdown complete.")
        finally:
            sock.close()
            logger.info("Server: Listener socket closed.")

def cleanup(signum, frame):
    """Cleanup function for graceful shutdown"""
    logger.info("Shutting down...")
    os._exit(0)

async def main():
    # Set up signal handlers
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    # Start non-TLS server
    logger.info("=== Starting HTTP/2 Server without TLS ===")
    server = HTTP2Server(port=8080, use_tls=False)
    await server.start()
    
    # Start TLS server
    logger.info("=== Starting HTTP/2 Server with TLS ===")
    server_tls = HTTP2Server(port=8443, use_tls=True)
    await server_tls.start()

if __name__ == "__main__":
    asyncio.run(main()) 