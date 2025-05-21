from flask import Flask, request, Response
import logging

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('control_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('control_service')

app = Flask(__name__)

@app.route('/control', methods=['POST'])
def handle_control():
    # Get the chunked data
    chunk = request.get_data()
    logger.info(f"[🛠️] Control service received chunk: {chunk.decode()}")
    
    # Process the chunk
    # Add your control logic here
    
    return Response(status=200)

if __name__ == '__main__':
    app.run(port=5003, debug=True) 