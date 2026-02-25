from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import certifi, os, datetime

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

# ================= SETUP =================
app = Flask(__name__)
CORS(app, origins="*", allow_headers=["Content-Type","Authorization"],
     methods=["GET","POST","PUT","DELETE","OPTIONS"], supports_credentials=False)

SECRET_KEY = os.environ.get("SECRET_KEY", "shopsmart-secret-2024")

@app.after_request
def add_cors(r):
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return r

# ================= MONGODB =================
client      = MongoClient(os.environ.get("MONGO_URI"), serverSelectionTimeoutMS=5000,
                          tls=True, tlsCAFile=certifi.where())
db          = client["shopsmart"]
users_col   = db["users"]
products_col= db["products"]
reviews_col = db["reviews"]
orders_col  = db["orders"]

# ================= HELPERS =================
def make_token(user_id, name, email):
    if JWT_AVAILABLE:
        payload = {
            "sub":   str(user_id),
            "name":  name,
            "email": email,
            "exp":   datetime.datetime.utcnow() + datetime.timedelta(days=7)
        }
        return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    return f"token-{user_id}"

def decode_token(token):
    if JWT_AVAILABLE:
        try:
            return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except:
            return None
    # fallback: token is "token-{id}"
    return None

def get_current_user():
    auth  = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not token: return None
    return decode_token(token)

def recalc_rating(pid):
    """Recalculate and update avg rating in products collection after a new review."""
    reviews = list(reviews_col.find({"product_id": pid}))
    if not reviews:
        return
    avg = round(sum(r["rating"] for r in reviews) / len(reviews), 1)
    products_col.update_one(
        {"product_id": pid},
        {"$set": {"rating": avg, "review_count": len(reviews)}}
    )
    return avg

# ================= STATUS =================
@app.route("/")
@app.route("/api/status")
def status():
    return jsonify({"status": "ShopSmart API running ✓"})

# ================= PRODUCTS =================
@app.route("/api/products")
def get_products():
    return jsonify(list(products_col.find({}, {"_id": 0})))

@app.route("/api/products/<int:pid>")
def get_product(pid):
    product = products_col.find_one({"product_id": pid}, {"_id": 0})
    if not product:
        return jsonify({"error": "Product not found"}), 404
    reviews = list(reviews_col.find({"product_id": pid}, {"_id": 0}))
    product["reviews"]      = reviews
    product["review_count"] = len(reviews)
    return jsonify(product)

# ================= REVIEWS =================
@app.route("/api/products/<int:pid>/reviews", methods=["GET"])
def get_reviews(pid):
    reviews = list(reviews_col.find({"product_id": pid}, {"_id": 0}))
    return jsonify(reviews)

@app.route("/api/products/<int:pid>/reviews", methods=["POST", "OPTIONS"])
def add_review(pid):
    if request.method == "OPTIONS": return jsonify({}), 200
    user = get_current_user()
    if not user: return jsonify({"error": "Login required"}), 401

    data   = request.json or {}
    rating = data.get("rating")
    text   = data.get("text", "").strip()

    if not rating or not (1 <= int(rating) <= 5):
        return jsonify({"error": "Rating between 1 and 5 is required"}), 400

    # Check product exists
    product = products_col.find_one({"product_id": pid})
    if not product:
        return jsonify({"error": "Product not found"}), 404

    # Check user hasn't already reviewed
    existing = reviews_col.find_one({"product_id": pid, "user_email": user.get("email","")})
    if existing:
        return jsonify({"error": "You have already reviewed this product"}), 400

    review = {
        "product_id": pid,
        "user_name":  user.get("name", "Anonymous"),
        "user_email": user.get("email", ""),
        "rating":     int(rating),
        "text":       text,
        "date":       datetime.datetime.utcnow().strftime("%b %d, %Y")
    }
    reviews_col.insert_one(review)

    # Update product's avg rating in MongoDB
    new_avg = recalc_rating(pid)
    return jsonify({"message": "Review added", "new_avg_rating": new_avg})

# ================= RECOMMENDATIONS =================
@app.route("/api/recommend/<int:pid>")
def recommend_by_id(pid):
    try:
        from recommender import Recommender
        import os
        csv_path = os.path.join(os.path.dirname(__file__), "data", "products.csv")
        rec = Recommender(csv_path)
        return jsonify(rec.recommend_by_id(pid, n=6))
    except Exception as e:
        # Fallback: return same-category products from MongoDB
        product = products_col.find_one({"product_id": pid}, {"_id": 0})
        if not product: return jsonify([])
        similar = list(products_col.find(
            {"category": product.get("category"), "product_id": {"$ne": pid}},
            {"_id": 0}
        ).limit(6))
        return jsonify(similar)

@app.route("/api/recommend/cart", methods=["POST", "OPTIONS"])
def recommend_cart():
    if request.method == "OPTIONS": return jsonify({}), 200
    pids = request.json.get("product_ids", [])
    try:
        from recommender import Recommender
        import os
        csv_path = os.path.join(os.path.dirname(__file__), "data", "products.csv")
        rec = Recommender(csv_path)
        return jsonify(rec.recommend_by_cart(pids, n=6))
    except Exception as e:
        # Fallback: return popular products
        products = list(products_col.find(
            {"product_id": {"$nin": pids}}, {"_id": 0}
        ).sort("rating", -1).limit(6))
        return jsonify(products)

@app.route("/api/recommend/search")
def recommend_search():
    q = request.args.get("q", "")
    try:
        from recommender import Recommender
        import os
        csv_path = os.path.join(os.path.dirname(__file__), "data", "products.csv")
        rec = Recommender(csv_path)
        return jsonify(rec.recommend_by_search(q, n=6))
    except:
        results = list(products_col.find(
            {"name": {"$regex": q, "$options": "i"}}, {"_id": 0}
        ).limit(6))
        return jsonify(results)

# ================= ORDERS =================
@app.route("/api/orders", methods=["POST", "OPTIONS"])
def place_order():
    if request.method == "OPTIONS": return jsonify({}), 200
    user = get_current_user()
    if not user: return jsonify({"error": "Login required"}), 401

    data  = request.json or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "No items in order"}), 400

    total = sum(item.get("price", 0) * item.get("qty", 1) for item in items)
    order = {
        "user_email": user.get("email", ""),
        "user_name":  user.get("name", ""),
        "items":      items,
        "total":      total,
        "status":     "confirmed",
        "date":       datetime.datetime.utcnow().strftime("%b %d, %Y %H:%M")
    }
    result = orders_col.insert_one(order)
    return jsonify({"message": "Order placed!", "order_id": str(result.inserted_id), "total": total})

@app.route("/api/orders", methods=["GET"])
def get_orders():
    user = get_current_user()
    if not user: return jsonify({"error": "Login required"}), 401
    orders = list(orders_col.find({"user_email": user.get("email","")}, {"_id": 0}))
    return jsonify(orders)

# ================= AUTH =================
@app.route("/api/auth/register", methods=["POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS": return jsonify({}), 200
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

    result = users_col.insert_one({
        "name":     name,
        "email":    email,
        "password": generate_password_hash(password),
        "joined":   datetime.datetime.utcnow().strftime("%b %Y")
    })
    token = make_token(result.inserted_id, name, email)
    return jsonify({"message": "Account created", "access_token": token, "name": name, "email": email})

@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS": return jsonify({}), 200
    data     = request.json or {}
    email    = data.get("email",    "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = users_col.find_one({"email": email})
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid email or password"}), 401

    token = make_token(user["_id"], user["name"], email)
    return jsonify({"access_token": token, "name": user["name"], "email": email})

# ================= SEED WITH PRICES + RATINGS =================
@app.route("/api/seed")
def seed():
    """Re-seed products with prices and ratings from CSV if available,
       otherwise use the hardcoded list with prices."""
    try:
        from recommender import Recommender
        import os
        csv_path = os.path.join(os.path.dirname(__file__), "data", "products.csv")
        rec = Recommender(csv_path)
        data = rec.get_all_products()
        # Add prices if missing
        PRICES = [69999,129999,24999,8999,59999,149999,4999,3999,34999,19999,
                  8999,3499,9999,5999,1999,2999,12999,3499,7999,4999,
                  35999,12999,2999,8999,3499,1999,24999,29999,4999,34999,
                  799,1299,999,8999,1299,1499,2499,699,1099,2999,
                  4999,2999,8999,1499,7999,5999,49999,999,2999,599]
        for i, p in enumerate(data):
            if not p.get("price"):
                p["price"] = PRICES[i % len(PRICES)]
            if not p.get("review_count"):
                p["review_count"] = 0
        products_col.delete_many({})
        products_col.insert_many(data)
        return jsonify({"message": f"✓ Seeded {len(data)} products from CSV", "count": len(data)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= START =================
if __name__ == "__main__":
    app.run(debug=True, port=5000)