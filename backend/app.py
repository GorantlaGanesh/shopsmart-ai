from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity
)
from pymongo import MongoClient
import os

# ------------------ APP SETUP ------------------
app = Flask(__name__)
CORS(app)

app.config["JWT_SECRET_KEY"] = "shopsmart_ai_secret_2026!@#"
jwt = JWTManager(app)

# ------------------ MONGODB ------------------
mongo_uri = os.environ.get("MONGO_URI")

if not mongo_uri:
    raise Exception("MONGO_URI not set")

client = MongoClient(mongo_uri)
db = client["shopsmart"]
products_collection = db["products"]

# ------------------ IN-MEMORY DATA ------------------
USERS = {}
USER_HISTORY = {}

# ------------------ ROUTES ------------------

@app.route("/")
def home():
    return {"status": "ShopSmart API running"}

# ✅ PRODUCTS API (MongoDB only)
@app.route("/api/products")
def get_products():
    try:
        products = list(products_collection.find({}, {"_id": 0}))
        return jsonify(products)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# AUTH
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.json
    USERS[data["email"]] = data
    return {"msg": "registered"}

@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json
    user = USERS.get(data["email"])
    if not user or user["password"] != data["password"]:
        return {"msg": "invalid"}, 401
    token = create_access_token(identity=data["email"])
    return {"access_token": token, "name": user["name"]}

# TRACK VIEW
@app.route("/api/view/<int:pid>", methods=["POST"])
@jwt_required()
def view(pid):
    user = get_jwt_identity()
    USER_HISTORY.setdefault(user, []).append(pid)
    return {"msg": "viewed"}

# ------------------ START ------------------
if __name__ == "__main__":
    app.run()
