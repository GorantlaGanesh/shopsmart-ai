from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import certifi
import os

# ================= APP SETUP =================
app = Flask(__name__)
CORS(app)

# ================= MONGODB =================
mongo_uri = os.environ.get("MONGO_URI")

client = MongoClient(
    mongo_uri,
    serverSelectionTimeoutMS=5000,
    tls=True,
    tlsCAFile=certifi.where()
)

db = client["shopsmart"]
products = db["products"]
users = db["users"]

# ================= ROUTES =================

@app.route("/")
def home():
    return {"status": "ShopSmart API running"}

# ---------- SEED PRODUCTS ----------
@app.route("/api/seed")
def seed():
    data = [
        {"id": 1, "name": "iPhone 14", "price": 69999, "rating": 4.6},
        {"id": 2, "name": "Samsung S23", "price": 74999, "rating": 4.5},
        {"id": 3, "name": "Sony Headphones", "price": 12999, "rating": 4.4}
    ]
    products.delete_many({})
    products.insert_many(data)
    return {"inserted": len(data)}

# ---------- GET PRODUCTS ----------
@app.route("/api/products")
def get_products():
    return jsonify(list(products.find({}, {"_id": 0})))

# ---------- SIGNUP ----------
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.json
    name = data.get("name")
    email = data.get("email")
    password = data.get("password")

    if not name or not email or not password:
        return jsonify({"error": "Missing fields"}), 400

    if users.find_one({"email": email}):
        return jsonify({"error": "User already exists"}), 400

    users.insert_one({
        "name": name,
        "email": email,
        "password": generate_password_hash(password)
    })

    return jsonify({"message": "User registered successfully"})

# ---------- LOGIN ----------
@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")

    user = users.find_one({"email": email})
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid credentials"}), 401

    # simple token (frontend demo)
    return jsonify({
        "access_token": "demo-token-123",
        "name": user["name"]
    })

# ================= START =================
if __name__ == "__main__":
    app.run(debug=True)
