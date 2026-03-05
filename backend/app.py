from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import certifi, os, datetime, json
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

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
    key = os.environ.get("GEMINI_API_KEY")
    return jsonify({
        "status": "ShopSmart API running ✓",
        "gemini_key_set": bool(key),
        "gemini_available": GEMINI_AVAILABLE
    })

# ================= PRODUCTS =================
@app.route("/api/products")
def get_products():
    """Return all 50 products from MongoDB."""
    return jsonify(list(products_col.find({}, {"_id": 0}).sort("product_id", 1)))

@app.route("/api/products/<int:pid>")
def get_product(pid):
    """Return single product with reviews and similar products."""
    product = products_col.find_one({"product_id": pid}, {"_id": 0})
    if not product:
        return jsonify({"error": "Product not found"}), 404

    # Attach reviews
    reviews = list(reviews_col.find({"product_id": pid}, {"_id": 0}))
    product["reviews"]      = reviews
    product["review_count"] = len(reviews)

    # Similar products from recommender (falls back to same-category)
    try:
        from recommender import Recommender
        csv_path = os.path.join(os.path.dirname(__file__), "data", "products.csv")
        rec = Recommender(csv_path)
        similar_raw = rec.recommend_by_id(pid, n=4)
        # Enrich similar with prices from MongoDB
        for s in similar_raw:
            spid = s.get("product_id")
            mongo_s = products_col.find_one({"product_id": spid}, {"_id": 0})
            if mongo_s:
                s["price"]     = mongo_s.get("price")
                s["image_url"] = mongo_s.get("image_url", s.get("image_url",""))
        product["similar"] = similar_raw
    except:
        # Fallback: same category products
        product["similar"] = list(products_col.find(
            {"category": product["category"], "product_id": {"$ne": pid}},
            {"_id": 0}
        ).limit(4))

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
    """Seed all 80 products with correct images, ratings and descriptions into MongoDB."""
    PRODUCTS = [
        {"product_id":1,"name":"iPhone 15 Pro","category":"Electronics","description":"Latest Apple smartphone with titanium body and pro camera system","image_url":"https://images.unsplash.com/photo-1695048133142-1a20484d2569?q=80&w=400","price":134999,"rating":4.8,"review_count":612},
        {"product_id":2,"name":"MacBook Air M2","category":"Electronics","description":"Thin and light laptop with M2 chip and liquid retina display","image_url":"https://images.unsplash.com/photo-1611186871525-5f62c7f1a9c4?q=80&w=400","price":114999,"rating":4.7,"review_count":423},
        {"product_id":3,"name":"Sony WH-1000XM5","category":"Electronics","description":"Industry leading noise canceling wireless headphones","image_url":"https://images.unsplash.com/photo-1505740420928-5e560c06d30e?q=80&w=400","price":29999,"rating":4.6,"review_count":847},
        {"product_id":4,"name":"Mechanical Keyboard","category":"Electronics","description":"RGB backlit gaming keyboard with tactile blue switches","image_url":"https://images.unsplash.com/photo-1511467687858-23d96c32e4ae?q=80&w=400","price":8999,"rating":4.3,"review_count":234},
        {"product_id":5,"name":"Samsung Galaxy S23","category":"Electronics","description":"Powerful Android smartphone with great display and zoom camera","image_url":"https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?q=80&w=400","price":79999,"rating":4.5,"review_count":389},
        {"product_id":6,"name":"Gaming Laptop RTX 4080","category":"Electronics","description":"High-end gaming laptop with powerful RTX 4080 graphics card","image_url":"https://images.unsplash.com/photo-1603302576837-37561b2e2302?q=80&w=400","price":199999,"rating":4.4,"review_count":156},
        {"product_id":7,"name":"Wireless Gaming Mouse","category":"Electronics","description":"Ergonomic wireless mouse with high DPI sensor and customizable buttons","image_url":"https://images.unsplash.com/photo-1527864550417-7fd91fc51a46?q=80&w=400","price":4999,"rating":4.2,"review_count":318},
        {"product_id":8,"name":"Portable Bluetooth Speaker","category":"Electronics","description":"Waterproof portable speaker with deep bass and 24h battery life","image_url":"https://images.unsplash.com/photo-1608156639585-b3a032ef9689?q=80&w=400","price":3999,"rating":4.1,"review_count":276},
        {"product_id":9,"name":"Smart Watch Series 9","category":"Electronics","description":"Advanced fitness tracker and smartwatch with heart rate monitoring","image_url":"https://images.unsplash.com/photo-1523275335684-37898b6baf30?q=80&w=400","price":44999,"rating":4.5,"review_count":502},
        {"product_id":10,"name":"Noise Cancelling Earbuds","category":"Electronics","description":"Compact wireless earbuds with active noise cancellation","image_url":"https://images.unsplash.com/photo-1590658268037-6bf12165a8df?q=80&w=400","price":19999,"rating":4.3,"review_count":445},
        {"product_id":11,"name":"Nike Air Max","category":"Fashion","description":"Comfortable and stylish running shoes for everyday athletes","image_url":"https://images.unsplash.com/photo-1542291026-7eec264c27ff?q=80&w=400","price":8999,"rating":4.6,"review_count":721},
        {"product_id":12,"name":"Levi's 501 Jeans","category":"Fashion","description":"Classic straight leg denim jeans for daily wear","image_url":"https://images.unsplash.com/photo-1542272604-787c3835535d?q=80&w=400","price":3499,"rating":4.2,"review_count":563},
        {"product_id":13,"name":"Adidas Ultraboost","category":"Fashion","description":"Premium running shoes with responsive Boost cushioning","image_url":"https://images.unsplash.com/photo-1587563871167-1ee9c731aefb?q=80&w=400","price":9999,"rating":4.5,"review_count":398},
        {"product_id":14,"name":"Travel Backpack","category":"Fashion","description":"Durable water-resistant backpack with padded laptop compartment","image_url":"https://images.unsplash.com/photo-1553062407-98eeb64c6a62?q=80&w=400","price":5999,"rating":4.3,"review_count":287},
        {"product_id":15,"name":"Running Shorts","category":"Fashion","description":"Lightweight moisture-wicking shorts designed for long distance running","image_url":"https://images.unsplash.com/photo-1591195853828-11db59a44f6b?q=80&w=400","price":1999,"rating":3.9,"review_count":142},
        {"product_id":16,"name":"Cotton Hoodie","category":"Fashion","description":"Soft premium cotton hoodie with fleece lining for cold weather","image_url":"https://images.unsplash.com/photo-1556821840-3a63f95609a7?q=80&w=400","price":2999,"rating":4.1,"review_count":209},
        {"product_id":17,"name":"Leather Jacket","category":"Fashion","description":"Classic biker style genuine leather jacket with premium zippers","image_url":"https://images.unsplash.com/photo-1551028919-30164a7ed401?q=80&w=400","price":12999,"rating":4.4,"review_count":176},
        {"product_id":18,"name":"Aviator Sunglasses","category":"Fashion","description":"Classic metal frame aviator sunglasses with UV400 protection","image_url":"https://images.unsplash.com/photo-1511499767150-a48a237f0083?q=80&w=400","price":3499,"rating":4.0,"review_count":193},
        {"product_id":19,"name":"Formal Oxford Shoes","category":"Fashion","description":"Elegant brown leather oxford shoes for formal occasions","image_url":"https://images.unsplash.com/photo-1614252235316-06f87530b539?q=80&w=400","price":7999,"rating":4.3,"review_count":128},
        {"product_id":20,"name":"Summer Floral Dress","category":"Fashion","description":"Lightweight floral print sundress perfect for warm weather","image_url":"https://images.unsplash.com/photo-1572804013309-59a88b7e92f1?q=80&w=400","price":4999,"rating":4.2,"review_count":241},
        {"product_id":21,"name":"KitchenAid Stand Mixer","category":"Home","description":"Powerful 4.8L tilt-head kitchen mixer for baking and food prep","image_url":"https://images.unsplash.com/photo-1594385208974-2e75f9d3bb4a?q=80&w=400","price":35999,"rating":4.7,"review_count":334},
        {"product_id":22,"name":"Nespresso Machine","category":"Home","description":"Compact espresso machine for quick barista-quality coffee brewing","image_url":"https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?q=80&w=400","price":12999,"rating":4.5,"review_count":412},
        {"product_id":23,"name":"Yoga Mat Pro","category":"Home","description":"Non-slip eco-friendly 6mm yoga mat with alignment lines","image_url":"https://images.unsplash.com/photo-1592432678899-35492985175a?q=80&w=400","price":2999,"rating":4.3,"review_count":389},
        {"product_id":24,"name":"Burr Coffee Grinder","category":"Home","description":"Professional grade conical burr grinder with 40 grind settings","image_url":"https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?q=80&w=400","price":8999,"rating":4.4,"review_count":167},
        {"product_id":25,"name":"LED Desk Lamp","category":"Home","description":"Modern desk lamp with adjustable brightness and 3 color temperatures","image_url":"https://images.unsplash.com/photo-1534073828943-f801091bb270?q=80&w=400","price":3499,"rating":4.1,"review_count":223},
        {"product_id":26,"name":"Insulated Water Bottle","category":"Home","description":"Stainless steel vacuum insulated bottle keeps drinks cold 24h hot 12h","image_url":"https://images.unsplash.com/photo-1602143307185-84487493375e?q=80&w=400","price":1999,"rating":4.5,"review_count":534},
        {"product_id":27,"name":"Air Purifier HEPA","category":"Home","description":"Advanced 4-stage HEPA filtration removes 99.97% of dust and allergens","image_url":"https://images.unsplash.com/photo-1585771724684-25271286bb24?q=80&w=400","price":24999,"rating":4.6,"review_count":298},
        {"product_id":28,"name":"Ergonomic Office Chair","category":"Home","description":"Adjustable office chair with lumbar support and breathable mesh back","image_url":"https://images.unsplash.com/photo-1505797149-43b0069ec26b?q=80&w=400","price":29999,"rating":4.3,"review_count":187},
        {"product_id":29,"name":"Cast Iron Skillet","category":"Home","description":"Pre-seasoned 10-inch cast iron skillet for searing, baking and frying","image_url":"https://images.unsplash.com/photo-1584269600464-37b1b58a9fe7?q=80&w=400","price":4999,"rating":4.7,"review_count":456},
        {"product_id":30,"name":"Robot Vacuum","category":"Home","description":"Smart robot vacuum with LiDAR mapping, app control and auto-empty dock","image_url":"https://images.unsplash.com/photo-1518640467707-6811f4a6ab73?q=80&w=400","price":34999,"rating":4.4,"review_count":312},
        {"product_id":31,"name":"Matte Lipstick","category":"Beauty","description":"Long-lasting 16-hour matte lipstick in vibrant red shade","image_url":"https://images.unsplash.com/photo-1586495777744-4413f21062fa?q=80&w=400","price":799,"rating":4.2,"review_count":378},
        {"product_id":32,"name":"Liquid Foundation","category":"Beauty","description":"Full coverage buildable liquid foundation for all skin types and tones","image_url":"https://images.unsplash.com/photo-1631729371254-42c2892f0e6e?q=80&w=400","price":1299,"rating":4.0,"review_count":267},
        {"product_id":33,"name":"Volumizing Mascara","category":"Beauty","description":"Black mascara for dramatic volume and length without clumping","image_url":"https://images.unsplash.com/photo-1631214524020-7e18db9a8f92?q=80&w=400","price":999,"rating":4.1,"review_count":312},
        {"product_id":34,"name":"Eau de Parfum","category":"Beauty","description":"Luxury floral oriental fragrance with notes of jasmine, rose and oud","image_url":"https://images.unsplash.com/photo-1541643600914-78b084683601?q=80&w=400","price":8999,"rating":4.6,"review_count":189},
        {"product_id":35,"name":"Hydrating Face Cream","category":"Beauty","description":"Daily moisturizer with hyaluronic acid and ceramides for dry skin","image_url":"https://images.unsplash.com/photo-1620916566398-39f1143ab7be?q=80&w=400","price":1299,"rating":4.4,"review_count":421},
        {"product_id":36,"name":"Gel Nail Polish","category":"Beauty","description":"UV LED gel nail polish starter kit in 12 pastel colors with base coat","image_url":"https://images.unsplash.com/photo-1604654894610-df63bc536371?q=80&w=400","price":1499,"rating":3.9,"review_count":156},
        {"product_id":37,"name":"Makeup Brush Set","category":"Beauty","description":"Professional 12-piece synthetic vegan makeup brush collection with pouch","image_url":"https://images.unsplash.com/photo-1596462502278-27bfdd403348?q=80&w=400","price":2499,"rating":4.3,"review_count":234},
        {"product_id":38,"name":"Argan Oil Shampoo","category":"Beauty","description":"Sulfate-free shampoo enriched with Moroccan argan oil for shine and volume","image_url":"https://images.unsplash.com/photo-1535585209827-a15fcdbc4c2d?q=80&w=400","price":699,"rating":4.0,"review_count":298},
        {"product_id":39,"name":"Vitamin C Serum","category":"Beauty","description":"20% Vitamin C brightening facial serum to reduce dark spots and even skin tone","image_url":"https://images.unsplash.com/photo-1620916297397-a4a5402a3c6c?q=80&w=400","price":1099,"rating":4.5,"review_count":512},
        {"product_id":40,"name":"Eyeshadow Palette","category":"Beauty","description":"35 highly pigmented eyeshadow shades with matte and shimmer finishes","image_url":"https://images.unsplash.com/photo-1512496015851-a90fb38ba796?q=80&w=400","price":2999,"rating":4.2,"review_count":176},
        {"product_id":41,"name":"Tennis Racket","category":"Sports","description":"Lightweight graphite tennis racket for intermediate to advanced players","image_url":"https://images.unsplash.com/photo-1617083934555-563d6412e92b?q=80&w=400","price":4999,"rating":4.1,"review_count":98},
        {"product_id":42,"name":"Basketball","category":"Sports","description":"Official size 7 indoor outdoor basketball with superior grip and bounce","image_url":"https://images.unsplash.com/photo-1519861531473-9200262188bf?q=80&w=400","price":2999,"rating":4.3,"review_count":167},
        {"product_id":43,"name":"Dumbbell Set","category":"Sports","description":"5-25kg adjustable rubber coated dumbbell set for home workouts","image_url":"https://images.unsplash.com/photo-1638536532686-d610adfc8e5c?q=80&w=400","price":8999,"rating":4.4,"review_count":223},
        {"product_id":44,"name":"Soccer Ball","category":"Sports","description":"Professional FIFA approved training soccer ball size 5","image_url":"https://images.unsplash.com/photo-1614632537423-1e6c2e7e0aab?q=80&w=400","price":1499,"rating":4.0,"review_count":134},
        {"product_id":45,"name":"Cycling Helmet","category":"Sports","description":"Aerodynamic CPSC certified safety helmet with 16 ventilation channels","image_url":"https://images.unsplash.com/photo-1558537348-c0f8e747b520?q=80&w=400","price":7999,"rating":4.5,"review_count":189},
        {"product_id":46,"name":"Boxing Gloves","category":"Sports","description":"Premium leather boxing gloves with multi-layer wrist support for sparring","image_url":"https://images.unsplash.com/photo-1605296867304-46d5465a13f1?q=80&w=400","price":5999,"rating":4.2,"review_count":145},
        {"product_id":47,"name":"Golf Club Set","category":"Sports","description":"Complete 12-piece beginner golf club set with stand bag and head covers","image_url":"https://images.unsplash.com/photo-1535131749006-b7f58c99034b?q=80&w=400","price":49999,"rating":4.3,"review_count":67},
        {"product_id":48,"name":"Swimming Goggles","category":"Sports","description":"Anti-fog UV protection competition swimming goggles with adjustable strap","image_url":"https://images.unsplash.com/photo-1600965962102-9d260a71890d?q=80&w=400","price":999,"rating":4.1,"review_count":212},
        {"product_id":49,"name":"Baseball Bat","category":"Sports","description":"Aerospace-grade aluminum alloy baseball bat with cushioned grip tape","image_url":"https://images.unsplash.com/photo-1593786481097-cf281dd12e9e?q=80&w=400","price":2999,"rating":3.9,"review_count":89},
        {"product_id":50,"name":"Jump Rope","category":"Sports","description":"Speed jump rope with sealed ball bearings for smooth cardio training","image_url":"https://images.unsplash.com/photo-1434596922112-19c563067271?q=80&w=400","price":599,"rating":4.0,"review_count":312},
        {"product_id":51,"name":"Atomic Habits","category":"Books","description":"James Clear's guide to building good habits and breaking bad ones permanently","image_url":"https://images.unsplash.com/photo-1512820790803-83ca734da794?q=80&w=400","price":499,"rating":4.9,"review_count":1243},
        {"product_id":52,"name":"The Psychology of Money","category":"Books","description":"Morgan Housel's timeless lessons on wealth, greed and happiness","image_url":"https://images.unsplash.com/photo-1589829085413-56de8ae18c73?q=80&w=400","price":349,"rating":4.8,"review_count":876},
        {"product_id":53,"name":"Rich Dad Poor Dad","category":"Books","description":"Robert Kiyosaki on financial literacy and the mindset to build wealth","image_url":"https://images.unsplash.com/photo-1544947950-fa07a98d237f?q=80&w=400","price":299,"rating":4.5,"review_count":2134},
        {"product_id":54,"name":"Deep Work","category":"Books","description":"Cal Newport's rules for focused success in a deeply distracted world","image_url":"https://images.unsplash.com/photo-1481627834876-b7833e8f5570?q=80&w=400","price":399,"rating":4.6,"review_count":698},
        {"product_id":55,"name":"Zero to One","category":"Books","description":"Peter Thiel on startups and how to build companies that create new value","image_url":"https://images.unsplash.com/photo-1543002588-bfa74002ed7e?q=80&w=400","price":449,"rating":4.5,"review_count":543},
        {"product_id":56,"name":"Sapiens","category":"Books","description":"Yuval Noah Harari's sweeping history of humankind across 70,000 years","image_url":"https://images.unsplash.com/photo-1495640388908-05fa85288e61?q=80&w=400","price":549,"rating":4.7,"review_count":1567},
        {"product_id":57,"name":"Premium Dark Chocolate Box","category":"Food","description":"Belgian 70% dark chocolate assortment with sea salt and caramel in luxury box","image_url":"https://images.unsplash.com/photo-1548907040-4baa42d10919?q=80&w=400","price":1299,"rating":4.6,"review_count":321},
        {"product_id":58,"name":"Himalayan Pink Salt","category":"Food","description":"Coarse grain unrefined Himalayan pink salt rich in 84 trace minerals","image_url":"https://images.unsplash.com/photo-1531845116688-48819b3b68d9?q=80&w=400","price":349,"rating":4.3,"review_count":187},
        {"product_id":59,"name":"Organic Green Tea Set","category":"Food","description":"Premium Darjeeling first-flush green tea loose leaf in gift tin 100g","image_url":"https://images.unsplash.com/photo-1556679343-c7306c1976bc?q=80&w=400","price":799,"rating":4.5,"review_count":234},
        {"product_id":60,"name":"Gourmet Dry Fruits Mix","category":"Food","description":"Premium roasted almonds, cashews, pistachios and walnuts combo 500g","image_url":"https://images.unsplash.com/photo-1620705565-a3e81b2fe929?q=80&w=400","price":1499,"rating":4.4,"review_count":412},
        {"product_id":61,"name":"Cold Press Olive Oil","category":"Food","description":"Extra virgin cold-pressed olive oil from single-estate Spanish olives 1L","image_url":"https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?q=80&w=400","price":899,"rating":4.5,"review_count":289},
        {"product_id":62,"name":"Artisan Coffee Beans","category":"Food","description":"Single-origin Ethiopian Yirgacheffe light roast whole bean coffee 250g","image_url":"https://images.unsplash.com/photo-1447933601403-0c6688de566e?q=80&w=400","price":699,"rating":4.7,"review_count":356},
        {"product_id":63,"name":"LEGO Architecture Set","category":"Toys & Games","description":"Build iconic world landmarks with this 860-piece LEGO Architecture kit","image_url":"https://images.unsplash.com/photo-1587654780291-39c9404d746b?q=80&w=400","price":5999,"rating":4.8,"review_count":423},
        {"product_id":64,"name":"Chess Set Wooden","category":"Toys & Games","description":"Hand-carved Indian rosewood chess set with folding board and storage","image_url":"https://images.unsplash.com/photo-1529699211952-734e80c4d42b?q=80&w=400","price":2499,"rating":4.5,"review_count":198},
        {"product_id":65,"name":"Monopoly Classic","category":"Toys & Games","description":"The original classic property trading board game for family fun nights","image_url":"https://images.unsplash.com/photo-1611996575749-79a3a250f948?q=80&w=400","price":1299,"rating":4.3,"review_count":567},
        {"product_id":66,"name":"Remote Control Car","category":"Toys & Games","description":"Off-road 4WD brushless RC car 1:16 scale 45+ km/h with 2.4GHz control","image_url":"https://images.unsplash.com/photo-1558618666-fcd25c85cd64?q=80&w=400","price":3499,"rating":4.2,"review_count":234},
        {"product_id":67,"name":"Rubik's Cube Speed","category":"Toys & Games","description":"Professional 3x3 Gan magnetic speed cube used by world champions","image_url":"https://images.unsplash.com/photo-1551038247-3d935bc65ced?q=80&w=400","price":599,"rating":4.6,"review_count":789},
        {"product_id":68,"name":"Carrom Board Premium","category":"Toys & Games","description":"Full size AICF approved tournament carrom board with smooth lacquer surface","image_url":"https://images.unsplash.com/photo-1606503153255-59d5e417b6cd?q=80&w=400","price":4999,"rating":4.4,"review_count":312},
        {"product_id":69,"name":"Car Dash Cam 4K","category":"Automotive","description":"4K UHD front and rear dash camera with night vision and parking mode","image_url":"https://images.unsplash.com/photo-1449965408869-eaa3f722e40d?q=80&w=400","price":7999,"rating":4.3,"review_count":198},
        {"product_id":70,"name":"Tyre Inflator Cordless","category":"Automotive","description":"Digital cordless tyre inflator with auto-shutoff 120W and 6000mAh battery","image_url":"https://images.unsplash.com/photo-1568772585407-9f217294f80c?q=80&w=400","price":3499,"rating":4.2,"review_count":156},
        {"product_id":71,"name":"Car Seat Covers","category":"Automotive","description":"Universal fit premium PU leather 5-seat car seat cover set with airbag cutout","image_url":"https://images.unsplash.com/photo-1503376780353-7e6692767b70?q=80&w=400","price":5999,"rating":4.0,"review_count":134},
        {"product_id":72,"name":"Microfiber Car Wash Kit","category":"Automotive","description":"12-piece ultra-soft microfiber detailing kit for scratch-free car care","image_url":"https://images.unsplash.com/photo-1615906655593-ad0386982a0f?q=80&w=400","price":1499,"rating":4.4,"review_count":267},
        {"product_id":73,"name":"Car Phone Mount","category":"Automotive","description":"360 degree adjustable magnetic wireless charging phone mount for dashboard","image_url":"https://images.unsplash.com/photo-1609840113945-3ee765ced64d?q=80&w=400","price":799,"rating":4.1,"review_count":389},
        {"product_id":74,"name":"Jump Starter Powerbank","category":"Automotive","description":"2000A peak 12V car jump starter with 20000mAh powerbank and smart clamps","image_url":"https://images.unsplash.com/photo-1558618047-f93a35c30aa3?q=80&w=400","price":4999,"rating":4.5,"review_count":223},
        {"product_id":75,"name":"Whey Protein Gold Standard","category":"Health","description":"ON Gold Standard 100% whey isolate protein, double rich chocolate 2lb","image_url":"https://images.unsplash.com/photo-1593095948071-474c5cc2989d?q=80&w=400","price":3499,"rating":4.6,"review_count":847},
        {"product_id":76,"name":"Daily Multivitamin Tablets","category":"Health","description":"Complete daily multivitamin with 25 vitamins and minerals for immunity 60ct","image_url":"https://images.unsplash.com/photo-1550572017-edd951b55104?q=80&w=400","price":699,"rating":4.2,"review_count":512},
        {"product_id":77,"name":"Blood Pressure Monitor","category":"Health","description":"Clinically validated digital upper arm BP monitor with Bluetooth and app sync","image_url":"https://images.unsplash.com/photo-1559757148-5c350d0d3c56?q=80&w=400","price":2499,"rating":4.5,"review_count":334},
        {"product_id":78,"name":"Resistance Bands Set","category":"Health","description":"Set of 5 color-coded latex resistance bands 10-50lbs for strength training","image_url":"https://images.unsplash.com/photo-1598971639058-fab3c3109a5d?q=80&w=400","price":999,"rating":4.3,"review_count":623},
        {"product_id":79,"name":"Omega-3 Fish Oil","category":"Health","description":"Triple-strength omega-3 EPA 600mg DHA 400mg fish oil softgels 60 count","image_url":"https://images.unsplash.com/photo-1607619056574-7b8d3ee536b2?q=80&w=400","price":549,"rating":4.4,"review_count":456},
        {"product_id":80,"name":"Pulse Oximeter","category":"Health","description":"Medical grade fingertip pulse oximeter with OLED display for SpO2 and PR","image_url":"https://images.unsplash.com/photo-1584515933487-779824d29309?q=80&w=400","price":1299,"rating":4.3,"review_count":289},
    ]
    products_col.delete_many({})
    products_col.insert_many(PRODUCTS)
    return jsonify({"message": f"✓ Seeded {len(PRODUCTS)} products across 10 categories", "count": len(PRODUCTS)})


# ================= START =================
if __name__ == "__main__":
    app.run(debug=True, port=5000)
