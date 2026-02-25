from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import certifi
import os

# ================= APP SETUP =================
app = Flask(__name__)

# Explicitly allow all origins + methods + headers
# Fixes CORS preflight (OPTIONS) failures with POST requests
CORS(app,
     origins="*",
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     supports_credentials=False
)

# Belt-and-suspenders: manually add CORS headers to every response
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response

# ================= MONGODB =================
mongo_uri = os.environ.get("MONGO_URI")
client = MongoClient(
    mongo_uri,
    serverSelectionTimeoutMS=5000,
    tls=True,
    tlsCAFile=certifi.where()
)
db           = client["shopsmart"]
products_col = db["products"]
users_col    = db["users"]

# ================= ROUTES =================

@app.route("/")
def home():
    return jsonify({"status": "ShopSmart API running ✓"})

@app.route("/api/status")
def status():
    return jsonify({"status": "ShopSmart API running ✓"})

# ---------- SEED PRODUCTS ----------
@app.route("/api/seed")
def seed():
    data = [
        {"id": 1, "name": "iPhone 14",         "price": 69999, "rating": 4.6, "category": "tech"},
        {"id": 2, "name": "Samsung S23",        "price": 74999, "rating": 4.5, "category": "tech"},
        {"id": 3, "name": "Sony Headphones",    "price": 12999, "rating": 4.4, "category": "tech"},
        {"id": 4, "name": "Nike Air Max",       "price": 8999,  "rating": 4.3, "category": "fashion"},
        {"id": 5, "name": "Levi's Jeans",       "price": 3499,  "rating": 4.2, "category": "fashion"},
        {"id": 6, "name": "Vitamin C Serum",    "price": 799,   "rating": 4.5, "category": "beauty"},
        {"id": 7, "name": "Scented Candle Set", "price": 1299,  "rating": 4.4, "category": "home"},
        {"id": 8, "name": "Smart Desk Lamp",    "price": 2499,  "rating": 4.3, "category": "home"},
    ]
    products_col.delete_many({})
    products_col.insert_many(data)
    return jsonify({"message": f"✓ Seeded {len(data)} products", "count": len(data)})

# ---------- GET ALL PRODUCTS ----------
@app.route("/api/products")
def get_products():
    return jsonify(list(products_col.find({}, {"_id": 0})))

# ---------- REGISTER ----------
@app.route("/api/auth/register", methods=["GET", "POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data     = request.json or {}
    name     = data.get("name",     "").strip()
    email    = data.get("email",    "").strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return jsonify({"error": "All fields are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if users_col.find_one({"email": email}):
        return jsonify({"error": "An account with this email already exists"}), 400

    users_col.insert_one({
        "name":     name,
        "email":    email,
        "password": generate_password_hash(password)
    })
    return jsonify({"message": "Account created successfully"})

# ---------- LOGIN ----------
@app.route("/api/auth/login", methods=["GET", "POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data     = request.json or {}
    email    = data.get("email",    "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = users_col.find_one({"email": email})
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid email or password"}), 401

    return jsonify({
        "access_token": "demo-token-123",
        "name":         user["name"],
        "email":        user["email"]
    })

# ================= START =================
if __name__ == "__main__":
    app.run(debug=True, port=5000)
