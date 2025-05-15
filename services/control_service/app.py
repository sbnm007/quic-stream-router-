from flask import Flask, request

app = Flask(__name__)

@app.route("/control", methods=["POST"])
def handle_control():
    data = request.get_data()
    print(f"[🛠️] Control service received: {data.decode()}")
    return "Control received", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003)

