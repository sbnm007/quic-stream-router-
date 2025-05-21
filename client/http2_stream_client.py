import asyncio
import h2.connection
import h2.config
import h2.events
import h2.exceptions
import socket
import ssl
import time
import logging
from typing import Optional, Dict, List, Tuple

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('http2_client.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('http2_client')

class HTTP2Client:
    def __init__(self, host: str = 'localhost', port: int = 8080, use_tls: bool = False):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.conn = None
        self.sock = None
        self.response_futures = {}
        
    async def connect(self):
        """Establish connection with the server"""
        logger.info(f"Connecting to {self.host}:{self.port} {'with TLS' if self.use_tls else 'without TLS'}")
        
        # Create socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        logger.debug("TCP connection established")
        
        if self.use_tls:
            # Create SSL context
            context = ssl.create_default_context()
            context.set_alpn_protocols(['h2'])
            self.sock = context.wrap_socket(self.sock, server_hostname=self.host)
            logger.debug("TLS handshake completed")
        
        # Create H2 connection
        config = h2.config.H2Configuration(client_side=True)
        self.conn = h2.connection.H2Connection(config=config)
        self.conn.initiate_connection()
        
        # Send initial connection preface
        preface_data = self.conn.data_to_send()
        logger.debug(f"Sending connection preface: {len(preface_data)} bytes")
        try:
            self.sock.sendall(preface_data)
            logger.debug("Connection preface sent successfully")
        except Exception as e:
            logger.error(f"Failed to send connection preface: {str(e)}")
            raise
        
        # Wait for server's settings frame
        while True:
            try:
                logger.debug("Waiting for server data...")
                data = self.sock.recv(65535)
                if not data:
                    logger.error("Connection closed during setup")
                    raise Exception("Connection closed during setup")
                
                logger.debug(f"Received {len(data)} bytes during setup")
                logger.debug(f"Raw data: {data.hex()}")
                
                try:
                    events = self.conn.receive_data(data)
                    logger.debug(f"Processing {len(events)} events during setup")
                    
                    for event in events:
                        logger.debug(f"Setup event type: {type(event).__name__}")
                        if isinstance(event, h2.events.RemoteSettingsChanged):
                            logger.info(f"Received settings: {event.changed_settings}")
                            # After receiving settings, we can consider the connection established
                            logger.info("HTTP/2 connection established")
                            return self.sock
                        elif isinstance(event, h2.events.ConnectionTerminated):
                            logger.error(f"Connection terminated: {event.error_code}")
                            raise Exception(f"Connection terminated: {event.error_code}")
                        elif isinstance(event, h2.events.WindowUpdated):
                            logger.debug(f"Window updated: stream_id={event.stream_id}, delta={event.delta}")
                        elif isinstance(event, h2.events.SettingsAcknowledged):
                            logger.debug("Settings acknowledged")
                        else:
                            logger.debug(f"Unhandled event type during setup: {type(event).__name__}")
                    
                    # Send any pending data
                    data_to_send = self.conn.data_to_send()
                    if data_to_send:
                        logger.debug(f"Sending {len(data_to_send)} bytes during setup")
                        try:
                            self.sock.sendall(data_to_send)
                            logger.debug("Pending data sent successfully")
                        except Exception as e:
                            logger.error(f"Failed to send pending data: {str(e)}")
                            raise
                
                except h2.exceptions.ProtocolError as e:
                    logger.error(f"Protocol error during setup: {str(e)}")
                    raise
                except Exception as e:
                    logger.error(f"Error processing data: {str(e)}")
                    raise
            
            except Exception as e:
                logger.error(f"Error during setup: {str(e)}")
                raise
        
        return self.sock

    async def _receive_loop(self):
        """Background task to receive and process all HTTP/2 events"""
        logger.info("Starting receive loop")
        while True:
            try:
                data = self.sock.recv(65535)
                if not data:
                    logger.info("Connection closed by server")
                    break
                    
                logger.debug(f"Received {len(data)} bytes")
                events = self.conn.receive_data(data)
                logger.debug(f"Processing {len(events)} events")
                
                for event in events:
                    logger.debug(f"Event type: {type(event).__name__}")
                    if isinstance(event, h2.events.DataReceived):
                        logger.debug(f"Received data on stream {event.stream_id}: {len(event.data)} bytes")
                        if event.stream_id in self.response_futures:
                            future = self.response_futures[event.stream_id]
                            if not future.done():
                                future.set_result(event.data)
                        self.conn.acknowledge_received_data(
                            event.flow_controlled_length,
                            event.stream_id
                        )
                    
                    elif isinstance(event, h2.events.StreamEnded):
                        logger.info(f"Stream {event.stream_id} ended")
                        if event.stream_id in self.response_futures:
                            future = self.response_futures[event.stream_id]
                            if not future.done():
                                future.set_result(b'')
                    
                    elif isinstance(event, h2.events.StreamReset):
                        logger.error(f"Stream {event.stream_id} was reset")
                        if event.stream_id in self.response_futures:
                            future = self.response_futures[event.stream_id]
                            if not future.done():
                                future.set_exception(Exception(f"Stream {event.stream_id} was reset"))
                
                # Send any pending data
                data_to_send = self.conn.data_to_send()
                if data_to_send:
                    logger.debug(f"Sending {len(data_to_send)} bytes")
                    self.sock.sendall(data_to_send)
                    
            except Exception as e:
                logger.error(f"Error in receive loop: {str(e)}")
                break
        
        logger.info("Receive loop ended")

    def send_request(self, path: str, stream_id: int) -> None:
        """Send a request on the specified stream."""
        logger.info(f"Sending request on stream {stream_id} to {path}")
        
        # Define stream data based on path
        if path == "/video":
            data_chunks = [b"This is ", b"video ", b"stream ", b"data..."]
        elif path == "/text":
            data_chunks = [b"This is ", b"text ", b"stream ", b"data..."]
        elif path == "/control":
            data_chunks = [b"This is ", b"control ", b"command."]
        else:
            data_chunks = [b"Unknown path data"]
            
        # Calculate total data length
        total_length = sum(len(chunk) for chunk in data_chunks)
        
        # Send headers
        headers = [
            (':method', 'POST'),
            (':path', path),
            (':scheme', 'http'),
            (':authority', 'localhost:8080'),
            ('content-type', 'application/octet-stream'),
            ('content-length', str(total_length))
        ]
        logger.debug(f"Headers for stream {stream_id}: {headers}")
        self.conn.send_headers(stream_id=stream_id, headers=headers)
        
        # Send data in chunks
        for chunk in data_chunks:
            logger.debug(f"Sending {len(chunk)} bytes on stream {stream_id}")
            self.conn.send_data(stream_id=stream_id, data=chunk)
            
        # End the stream
        self.conn.end_stream(stream_id=stream_id)
        logger.info(f"Request sent on stream {stream_id} to {path}")

async def main():
    # Test without TLS
    logger.info("=== Testing HTTP/2 without TLS ===")
    client = HTTP2Client(port=8080, use_tls=False)
    await client.connect()
    
    # Start receive loop
    receive_task = asyncio.create_task(client._receive_loop())
    
    # Send concurrent requests to different services
    paths = ['/video', '/text', '/control']
    tasks = []
    
    # Create multiple streams for each path
    for path in paths:
        for i in range(3):  # Send 3 concurrent requests per path
            stream_id = client.conn.get_next_available_stream_id()
            client.send_request(path, stream_id)
            tasks.append(stream_id)
            logger.info(f"Request sent on stream {stream_id} to {path}")
    
    # Wait for all responses
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    for i, response in enumerate(responses):
        path = paths[i // 3]  # Calculate path based on task index
        if isinstance(response, Exception):
            logger.error(f"Error for {path}: {str(response)}")
        else:
            logger.info(f"Received response for {path}: {response.decode() if response else 'None'}")
    
    client.sock.close()
    receive_task.cancel()
    
    # Test with TLS
    logger.info("=== Testing HTTP/2 with TLS ===")
    client_tls = HTTP2Client(port=8443, use_tls=True)
    await client_tls.connect()
    
    # Start receive loop
    receive_task_tls = asyncio.create_task(client_tls._receive_loop())
    
    # Send concurrent requests to different services
    tasks_tls = []
    
    # Create multiple streams for each path
    for path in paths:
        for i in range(3):  # Send 3 concurrent requests per path
            stream_id = client_tls.conn.get_next_available_stream_id()
            client_tls.send_request(path, stream_id)
            tasks_tls.append(stream_id)
            logger.info(f"Request sent on stream {stream_id} to {path} (TLS)")
    
    # Wait for all responses
    responses_tls = await asyncio.gather(*tasks_tls, return_exceptions=True)
    for i, response in enumerate(responses_tls):
        path = paths[i // 3]  # Calculate path based on task index
        if isinstance(response, Exception):
            logger.error(f"Error for {path} (TLS): {str(response)}")
        else:
            logger.info(f"Received response for {path} (TLS): {response.decode() if response else 'None'}")
    
    client_tls.sock.close()
    receive_task_tls.cancel()

if __name__ == "__main__":
    asyncio.run(main()) 