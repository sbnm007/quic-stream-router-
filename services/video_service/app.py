# === services/video_service/app.py ===

from flask import Flask, request

app = Flask(__name__)

@app.route("/video", methods=["POST"])
def handle_video():
    data = request.get_data()
    print(f"[📹] Video service received: {data.decode()}")
    return "Video received", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
