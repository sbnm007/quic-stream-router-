from flask import Flask, request

app = Flask(__name__)

@app.route("/text", methods=["POST"])
def handle_text():
    data = request.get_data()
    print(f"[📜] Text service received: {data.decode()}")
    return "Text received", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)

