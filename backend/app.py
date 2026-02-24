from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
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

# ================= ROUTES =================

@app.route("/")
def home():
    return {"status": "ShopSmart API running"}

# ---------- SEED (RUN ONCE) ----------
@app.route("/api/seed")
def seed():
    data = [
        {"id": 1, "name": "iPhone 14", "price": 69999},
        {"id": 2, "name": "Samsung S23", "price": 74999},
        {"id": 3, "name": "Sony Headphones", "price": 12999}
    ]
    products.delete_many({})
    products.insert_many(data)
    return {"inserted": len(data)}

# ---------- GET PRODUCTS ----------
@app.route("/api/products")
def get_products():
    return jsonify(list(products.find({}, {"_id": 0})))

# ================= START =================
if __name__ == "__main__":
    app.run()
