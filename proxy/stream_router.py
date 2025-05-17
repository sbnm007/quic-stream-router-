# === proxy/stream_router.py ===

import asyncio
import os
import ssl
import time
import json
import signal
import subprocess
from datetime import datetime
from collections import defaultdict
from aioquic.asyncio import serve, QuicConnectionProtocol
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.quic.configuration import QuicConfiguration
from aioquic.h3.events import HeadersReceived, DataReceived
from aioquic.quic.events import QuicEvent, StreamDataReceived, StreamReset
import requests
from requests.exceptions import RequestException

# Constants
CERT_DIR = "certs"
SERVICE_PORTS = {
    "/video": 5001,
    "/text": 5002,
    "/control": 5003
}

# Global variables for cleanup
tcpdump_process = None
performance_monitor = None

def start_packet_capture():
    """Start tcpdump to capture QUIC packets"""
    global tcpdump_process
    try:
        # Create capture directory if it doesn't exist
        os.makedirs("captures", exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pcap_file = f"captures/quic_capture_{timestamp}.pcap"
        
        # Start tcpdump process
        tcpdump_process = subprocess.Popen(
            ["sudo", "tcpdump", "-i", "lo", "-w", pcap_file, "port", "4433"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"[📦] Started packet capture to {pcap_file}")
        return pcap_file
    except Exception as e:
        print(f"[❌] Failed to start packet capture: {e}")
        return None

def cleanup(signum=None, frame=None):
    """Cleanup function to save metrics and stop packet capture"""
    global tcpdump_process, performance_monitor
    
    print("\n[🛑] Shutting down...")
    
    # Save final metrics
    if performance_monitor:
        performance_monitor.save_metrics()
        print("[📊] Final metrics saved")
    
    # Stop packet capture
    if tcpdump_process:
        tcpdump_process.terminate()
        tcpdump_process.wait()
        print("[📦] Packet capture stopped")
    
    # Exit
    os._exit(0)

class StreamMetrics:
    def __init__(self, stream_id, path):
        self.stream_id = stream_id
        self.path = path
        self.start_time = time.time()
        self.end_time = None
        self.bytes_sent = 0
        self.bytes_received = 0
        self.packets_sent = 0
        self.packets_received = 0
        self.retransmissions = 0
        self.quic_latency = []
        self.http_latency = []
        self.jitter = []
        self.throughput_samples = []
        self.last_byte_time = time.time()
        self.last_byte_count = 0
        self.errors = []

    @property
    def duration(self):
        return (self.end_time or time.time()) - self.start_time

    @property
    def average_throughput(self):
        if self.duration > 0:
            return (self.bytes_sent + self.bytes_received) / self.duration
        return 0

    @property
    def packet_loss_rate(self):
        if self.packets_sent > 0:
            return (self.packets_sent - self.packets_received) / self.packets_sent
        return 0

    @property
    def average_packet_size(self):
        total_packets = self.packets_sent + self.packets_received
        if total_packets > 0:
            return (self.bytes_sent + self.bytes_received) / total_packets
        return 0

    def to_dict(self):
        return {
            'stream_id': self.stream_id,
            'path': self.path,
            'duration': self.duration,
            'bytes_sent': self.bytes_sent,
            'bytes_received': self.bytes_received,
            'packets_sent': self.packets_sent,
            'packets_received': self.packets_received,
            'retransmissions': self.retransmissions,
            'quic_latency': self.quic_latency,
            'http_latency': self.http_latency,
            'jitter': self.jitter,
            'throughput_samples': self.throughput_samples,
            'errors': self.errors,
            'average_throughput': self.average_throughput,
            'packet_loss_rate': self.packet_loss_rate,
            'average_packet_size': self.average_packet_size
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
            'total_packets_sent': 0,
            'total_packets_received': 0,
            'stream_establishment_times': [],
            'errors': []
        }
        self.start_time = time.time()

    def record_stream_start(self, stream_id, path):
        self.stream_metrics[stream_id] = StreamMetrics(stream_id, path)
        self.connection_metrics['streams'].add(stream_id)

    def record_stream_end(self, stream_id):
        if stream_id in self.stream_metrics:
            metrics = self.stream_metrics[stream_id]
            metrics.end_time = time.time()
            
            # Update connection metrics
            self.connection_metrics['total_bytes_sent'] += metrics.bytes_sent
            self.connection_metrics['total_bytes_received'] += metrics.bytes_received
            self.connection_metrics['total_packets_sent'] += metrics.packets_sent
            self.connection_metrics['total_packets_received'] += metrics.packets_received

    def record_latency(self, stream_id, latency, latency_type='quic'):
        if stream_id in self.stream_metrics:
            if latency_type == 'quic':
                self.stream_metrics[stream_id].quic_latency.append(latency)
            else:
                self.stream_metrics[stream_id].http_latency.append(latency)

    def record_bytes(self, stream_id, sent=0, received=0):
        if stream_id in self.stream_metrics:
            metrics = self.stream_metrics[stream_id]
            metrics.bytes_sent += sent
            metrics.bytes_received += received
            
            # Calculate instantaneous throughput
            current_time = time.time()
            if metrics.last_byte_count > 0:
                time_diff = current_time - metrics.last_byte_time
                if time_diff > 0:
                    bytes_diff = (sent + received) - metrics.last_byte_count
                    throughput = bytes_diff / time_diff
                    metrics.throughput_samples.append(throughput)
            
            metrics.last_byte_time = current_time
            metrics.last_byte_count = sent + received

    def record_packets(self, stream_id, sent=0, received=0):
        if stream_id in self.stream_metrics:
            metrics = self.stream_metrics[stream_id]
            metrics.packets_sent += sent
            metrics.packets_received += received

    def record_retransmission(self, stream_id):
        if stream_id in self.stream_metrics:
            self.stream_metrics[stream_id].retransmissions += 1

    def record_error(self, stream_id, error):
        if stream_id in self.stream_metrics:
            self.stream_metrics[stream_id].errors.append(str(error))
        self.connection_metrics['errors'].append(str(error))

    def save_metrics(self, filename='quic_metrics.json'):
        try:
            # Create results directory with timestamp
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            results_dir = os.path.join("results", timestamp)
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
            json_file = os.path.join(results_dir, 'quic_metrics.json')
            with open(json_file, 'w') as f:
                json.dump(metrics, f, indent=2)
            print(f"[📊] Metrics saved to {os.path.abspath(json_file)}")
            
            # Generate human-readable report
            report_file = os.path.join(results_dir, 'quic_performance_report.txt')
            self.generate_report(report_file)
            print(f"[📝] Performance report saved to {os.path.abspath(report_file)}")
            
        except Exception as e:
            print(f"[❌] Error saving metrics: {e}")
            import traceback
            traceback.print_exc()

    def generate_report(self, filename='quic_performance_report.txt'):
        try:
            with open(filename, 'w') as f:
                f.write("QUIC Performance Report\n")
                f.write("======================\n\n")
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
                    
                    # Latency Analysis
                    if metrics.quic_latency:
                        f.write(f"  QUIC Latency (ms):\n")
                        quic_latencies_ms = [lat * 1000 for lat in metrics.quic_latency]
                        f.write(f"    Average: {sum(quic_latencies_ms) / len(quic_latencies_ms):.2f}\n")
                        f.write(f"    Min: {min(quic_latencies_ms):.2f}\n")
                        f.write(f"    Max: {max(quic_latencies_ms):.2f}\n")
                        f.write(f"    Samples: {len(quic_latencies_ms)}\n")
                    
                    if metrics.http_latency:
                        f.write(f"  HTTP Latency (ms):\n")
                        http_latencies_ms = [lat * 1000 for lat in metrics.http_latency]
                        f.write(f"    Average: {sum(http_latencies_ms) / len(http_latencies_ms):.2f}\n")
                        f.write(f"    Min: {min(http_latencies_ms):.2f}\n")
                        f.write(f"    Max: {max(http_latencies_ms):.2f}\n")
                        f.write(f"    Samples: {len(http_latencies_ms)}\n")
                    
                    # Packet Analysis
                    f.write(f"  Packet Statistics:\n")
                    f.write(f"    Sent: {metrics.packets_sent}\n")
                    f.write(f"    Received: {metrics.packets_received}\n")
                    f.write(f"    Retransmissions: {metrics.retransmissions}\n")
                    if metrics.packets_sent > 0:
                        f.write(f"    Packet Loss Rate: {metrics.packet_loss_rate * 100:.2f}%\n")
                        f.write(f"    Average Packet Size: {metrics.average_packet_size:.2f} bytes\n")
                    
                    if metrics.path == '/video' and metrics.jitter:
                        f.write(f"  Jitter: {sum(metrics.jitter) / len(metrics.jitter) * 1000:.2f}ms\n")
                    
                    # Error Analysis
                    if metrics.errors:
                        f.write(f"  Errors:\n")
                        for error in metrics.errors:
                            f.write(f"    - {error}\n")
        except Exception as e:
            print(f"[❌] Error generating report: {e}")
            import traceback
            traceback.print_exc()

class H3Protocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http = H3Connection(self._quic)
        self._stream_paths = {}
        self._performance_monitor = performance_monitor
        self._stream_start_times = {}
        self._connection_start_time = time.time()
        self._connection_established = False
        print("[🔍] H3Protocol initialized")

    def quic_event_received(self, event):
        try:
            # Record connection establishment time
            if not self._connection_established and isinstance(event, StreamDataReceived):
                self._connection_established = True
                connection_time = time.time() - self._connection_start_time
                if self._performance_monitor:
                    self._performance_monitor.connection_metrics['connection_establishment_time'] = connection_time
                print(f"[⏱️] Connection established in {connection_time:.3f}s")

            # Record QUIC-level events for performance monitoring
            if isinstance(event, StreamDataReceived):
                stream_id = event.stream_id
                if stream_id in self._stream_start_times:
                    # Record stream establishment time
                    stream_establishment_time = time.time() - self._stream_start_times[stream_id]
                    if self._performance_monitor:
                        self._performance_monitor.connection_metrics['stream_establishment_times'].append(stream_establishment_time)
                    print(f"[⏱️] Stream {stream_id} established in {stream_establishment_time:.3f}s")
                    
                    # Batch process data to reduce overhead
                    self._performance_monitor.record_bytes(stream_id, received=len(event.data))
                    self._performance_monitor.record_packets(stream_id, received=1)
                    
                    # Update connection metrics
                    if self._performance_monitor:
                        self._performance_monitor.connection_metrics['total_bytes_received'] += len(event.data)
                        self._performance_monitor.connection_metrics['total_packets_received'] += 1
                    
                    # Record QUIC latency after processing
                    quic_latency = time.time() - self._stream_start_times[stream_id]
                    self._performance_monitor.record_latency(stream_id, quic_latency, 'quic')
                    print(f"[⏱️] Recorded QUIC latency for stream {stream_id}: {quic_latency:.3f}s")
            
            # Handle HTTP/3 events
            for http_event in self._http.handle_event(event):
                if isinstance(http_event, HeadersReceived):
                    path = None
                    for name, value in http_event.headers:
                        if name == b":path":
                            path = value.decode()
                            break
                    if path in SERVICE_PORTS:
                        stream_id = http_event.stream_id
                        self._stream_paths[stream_id] = path
                        self._performance_monitor.record_stream_start(stream_id, path)
                        self._stream_start_times[stream_id] = time.time()
                        print(f"[🔄] Started monitoring stream {stream_id} for path {path}")
                        
                        # Update connection metrics
                        if self._performance_monitor:
                            self._performance_monitor.connection_metrics['streams'].add(stream_id)
                        
                        service_url = f"http://localhost:{SERVICE_PORTS[path]}{path}"
                        print(f"[→] Routed {path} to {service_url}")
                        
                        # Optimize response sending
                        headers = [
                            (b":status", b"200"),
                            (b"content-type", b"text/plain"),
                        ]
                        response_data = b"Request forwarded successfully"
                        
                        # Send headers and data in one batch
                        self._http.send_headers(stream_id=stream_id, headers=headers)
                        self._http.send_data(stream_id=stream_id, data=response_data, end_stream=True)
                        
                        # Batch record metrics
                        self._performance_monitor.record_bytes(stream_id, sent=len(response_data))
                        self._performance_monitor.record_packets(stream_id, sent=1)
                        self._performance_monitor.record_stream_end(stream_id)
                        print(f"[✅] Stream {stream_id} completed")
                        
                        # Update connection metrics
                        if self._performance_monitor:
                            self._performance_monitor.connection_metrics['total_bytes_sent'] += len(response_data)
                            self._performance_monitor.connection_metrics['total_packets_sent'] += 1
                
                elif isinstance(http_event, DataReceived):
                    path = self._stream_paths.get(http_event.stream_id)
                    if path and path in SERVICE_PORTS:
                        service_url = f"http://localhost:{SERVICE_PORTS[path]}{path}"
                        try:
                            # Use a session for connection pooling
                            with requests.Session() as session:
                                start_time = time.time()
                                response = session.post(service_url, data=http_event.data)
                                http_latency = time.time() - start_time
                                self._performance_monitor.record_latency(http_event.stream_id, http_latency, 'http')
                                print(f"[⏱️] Recorded HTTP latency for stream {http_event.stream_id}: {http_latency:.3f}s")
                                
                                # Update connection metrics
                                if self._performance_monitor:
                                    self._performance_monitor.connection_metrics['total_bytes_sent'] += len(http_event.data)
                                    self._performance_monitor.connection_metrics['total_packets_sent'] += 1
                                
                                print(f"[→] Routed {path} to {service_url} | Status: {response.status_code} | Latency: {http_latency:.3f}s")
                        except RequestException as e:
                            error_msg = f"Error forwarding to {service_url}: {str(e)}"
                            print(f"[❌] {error_msg}")
                            self._performance_monitor.record_error(http_event.stream_id, error_msg)
        except Exception as e:
            error_msg = f"Error processing QUIC event: {str(e)}"
            print(f"[❌] {error_msg}")
            if self._performance_monitor:
                self._performance_monitor.record_error(None, error_msg)

    def connection_lost(self, exc):
        print("[🔌] Connection lost, saving final metrics...")
        # Update connection metrics before saving
        if self._performance_monitor:
            self._performance_monitor.connection_metrics['end_time'] = time.time()
            self._performance_monitor.connection_metrics['duration'] = (
                self._performance_monitor.connection_metrics['end_time'] - 
                self._performance_monitor.connection_metrics['start_time']
            )
            self._performance_monitor.connection_metrics['streams'] = list(self._performance_monitor.connection_metrics['streams'])
            self._performance_monitor.save_metrics()
            print("[📊] Final metrics saved")
        super().connection_lost(exc)

def load_quic_config():
    """Load QUIC configuration with TLS certificates"""
    config = QuicConfiguration(
        is_client=False,
        alpn_protocols=H3_ALPN,
        max_datagram_frame_size=65536,
        idle_timeout=5.0  # Reduce idle timeout
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
    global performance_monitor
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    # Initialize performance monitor
    performance_monitor = PerformanceMonitor()
    print("[📊] Performance monitor initialized")
    
    # Start packet capture
    pcap_file = start_packet_capture()
    
    # Load QUIC configuration
    config, keylog_file = load_quic_config()
    
    try:
        print(f"[🔐] QUIC Proxy started at 0.0.0.0:4433, waiting for HTTP/3 streams...")
        print(f"[📦] Packet capture: {pcap_file}")
        print(f"[🔑] TLS key log: {keylog_file.name}")
        
        # Start periodic metrics saving
        async def save_metrics_periodically():
            while True:
                await asyncio.sleep(60)  # Save every minute
                if performance_monitor:
                    print("[⏰] Periodic metrics save triggered")
                    performance_monitor.save_metrics()
                    print("[📊] Periodic metrics saved")
        
        # Start metrics saving task
        asyncio.create_task(save_metrics_periodically())
        print("[⏰] Periodic metrics saving task started")
        
        # Start QUIC server
        await serve(
            "0.0.0.0",
            4433,
            configuration=config,
            create_protocol=H3Protocol
        )
        await asyncio.Future()  # Run forever
    finally:
        cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        cleanup()
