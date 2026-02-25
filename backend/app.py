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
    key = os.environ.get("ANTHROPIC_API_KEY")
    return jsonify({
        "status": "ShopSmart API running ✓",
        "anthropic_key_set": bool(key),
        "anthropic_key_preview": (key[:12] + "...") if key else None,
        "anthropic_available": ANTHROPIC_AVAILABLE
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
    """Seed all 50 products with images, descriptions and prices into MongoDB."""
    PRODUCTS = [
        {"product_id":1,"name":"iPhone 15 Pro","category":"Electronics","description":"Latest Apple smartphone with titanium body and pro camera system","image_url":"https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRPAYIinm_yjdXKZhPlCbAypxab7cRL9JuzSA&s","price":134999,"rating":0,"review_count":0},
        {"product_id":2,"name":"MacBook Air M2","category":"Electronics","description":"Thin and light laptop with M2 chip and liquid retina display","image_url":"https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTHwKWRGak6aMPRnhKwb41HtXMqhIMb5YyLCw&s","price":114999,"rating":0,"review_count":0},
        {"product_id":3,"name":"Sony WH-1000XM5","category":"Electronics","description":"Industry leading noise canceling wireless headphones","image_url":"https://images.unsplash.com/photo-1505740420928-5e560c06d30e?q=80&w=400","price":29999,"rating":0,"review_count":0},
        {"product_id":4,"name":"Mechanical Keyboard","category":"Electronics","description":"RGB backlit gaming keyboard with blue switches","image_url":"https://images.unsplash.com/photo-1511467687858-23d96c32e4ae?q=80&w=400","price":8999,"rating":0,"review_count":0},
        {"product_id":5,"name":"Samsung Galaxy S23","category":"Electronics","description":"Powerful Android smartphone with great display and zoom camera","image_url":"https://images.unsplash.com/photo-1678911820864-e2c567c655d7?q=80&w=400","price":79999,"rating":0,"review_count":0},
        {"product_id":6,"name":"Gaming Laptop RTX 4080","category":"Electronics","description":"High-end gaming laptop with powerful graphics card","image_url":"https://images.unsplash.com/photo-1603302576837-37561b2e2302?q=80&w=400","price":199999,"rating":0,"review_count":0},
        {"product_id":7,"name":"Wireless Gaming Mouse","category":"Electronics","description":"Ergonomic wireless mouse with high DPI sensor and customizable buttons","image_url":"https://images.unsplash.com/photo-1527864550417-7fd91fc51a46?q=80&w=400","price":4999,"rating":0,"review_count":0},
        {"product_id":8,"name":"Portable Bluetooth Speaker","category":"Electronics","description":"Waterproof portable speaker with deep bass and long battery life","image_url":"https://images.unsplash.com/photo-1608156639585-b3a032ef9689?q=80&w=400","price":3999,"rating":0,"review_count":0},
        {"product_id":9,"name":"Smart Watch Series 9","category":"Electronics","description":"Advanced fitness tracker and smartwatch with heart rate monitoring","image_url":"https://images.unsplash.com/photo-1523275335684-37898b6baf30?q=80&w=400","price":44999,"rating":0,"review_count":0},
        {"product_id":10,"name":"Noise Cancelling Earbuds","category":"Electronics","description":"Compact wireless earbuds with active noise cancellation","image_url":"https://images.unsplash.com/photo-1590658268037-6bf12165a8df?q=80&w=400","price":19999,"rating":0,"review_count":0},
        {"product_id":11,"name":"Nike Air Max","category":"Fashion","description":"Comfortable and stylish running shoes for athletes","image_url":"https://images.unsplash.com/photo-1542291026-7eec264c27ff?q=80&w=400","price":8999,"rating":0,"review_count":0},
        {"product_id":12,"name":"Levi's 501 Jeans","category":"Fashion","description":"Classic straight leg denim jeans for daily wear","image_url":"https://images.unsplash.com/photo-1542272604-787c3835535d?q=80&w=400","price":3499,"rating":0,"review_count":0},
        {"product_id":13,"name":"Adidas Ultraboost","category":"Fashion","description":"Premium running shoes with responsive cushioning","image_url":"https://images.unsplash.com/photo-1587563871167-1ee9c731aefb?q=80&w=400","price":9999,"rating":0,"review_count":0},
        {"product_id":14,"name":"Travel Backpack","category":"Fashion","description":"Durable water-resistant backpack with laptop compartment","image_url":"https://images.unsplash.com/photo-1553062407-98eeb64c6a62?q=80&w=400","price":5999,"rating":0,"review_count":0},
        {"product_id":15,"name":"Running Shorts","category":"Fashion","description":"Lightweight moisture-wicking shorts designed for long distance running","image_url":"https://images.unsplash.com/photo-1591195853828-11db59a44f6b?q=80&w=400","price":1999,"rating":0,"review_count":0},
        {"product_id":16,"name":"Cotton Hoodie","category":"Fashion","description":"Soft premium cotton hoodie with fleece lining for cold weather","image_url":"https://images.unsplash.com/photo-1556821840-3a63f95609a7?q=80&w=400","price":2999,"rating":0,"review_count":0},
        {"product_id":17,"name":"Leather Jacket","category":"Fashion","description":"Classic biker style real leather jacket with zippers","image_url":"https://images.unsplash.com/photo-1551028919-30164a7ed401?q=80&w=400","price":12999,"rating":0,"review_count":0},
        {"product_id":18,"name":"Aviator Sunglasses","category":"Fashion","description":"Classic metal frame aviator sunglasses with UV protection","image_url":"https://images.unsplash.com/photo-1511499767150-a48a237f0083?q=80&w=400","price":3499,"rating":0,"review_count":0},
        {"product_id":19,"name":"Formal Oxford Shoes","category":"Fashion","description":"Elegant brown leather oxford shoes for formal occasions","image_url":"https://images.unsplash.com/photo-1614252235316-06f87530b539?q=80&w=400","price":7999,"rating":0,"review_count":0},
        {"product_id":20,"name":"Summer Floral Dress","category":"Fashion","description":"Lightweight floral print sundress perfect for warm weather","image_url":"https://images.unsplash.com/photo-1572804013309-59a88b7e92f1?q=80&w=400","price":4999,"rating":0,"review_count":0},
        {"product_id":21,"name":"KitchenAid Stand Mixer","category":"Home","description":"Powerful kitchen mixer for baking and food prep","image_url":"https://images.unsplash.com/photo-1594385208974-2e75f9d3bb4a?q=80&w=400","price":35999,"rating":0,"review_count":0},
        {"product_id":22,"name":"Nespresso Machine","category":"Home","description":"Compact espresso machine for quick coffee brewing","image_url":"https://images.unsplash.com/photo-1510591509098-f4fdc6d0ff04?q=80&w=400","price":12999,"rating":0,"review_count":0},
        {"product_id":23,"name":"Yoga Mat Pro","category":"Home","description":"Non-slip eco-friendly yoga mat with extra cushioning","image_url":"https://images.unsplash.com/photo-1592432678899-35492985175a?q=80&w=400","price":2999,"rating":0,"review_count":0},
        {"product_id":24,"name":"Burr Coffee Grinder","category":"Home","description":"Professional grade coffee grinder with adjustable settings","image_url":"https://images.unsplash.com/photo-1580915411954-282cb1b0d780?q=80&w=400","price":8999,"rating":0,"review_count":0},
        {"product_id":25,"name":"LED Desk Lamp","category":"Home","description":"Modern desk lamp with adjustable brightness and color temperature","image_url":"https://images.unsplash.com/photo-1534073828943-f801091bb270?q=80&w=400","price":3499,"rating":0,"review_count":0},
        {"product_id":26,"name":"Insulated Water Bottle","category":"Home","description":"Stainless steel vacuum insulated bottle keeps drinks cold 24h","image_url":"https://images.unsplash.com/photo-1602143307185-84487493375e?q=80&w=400","price":1999,"rating":0,"review_count":0},
        {"product_id":27,"name":"Air Purifier HEPA","category":"Home","description":"Advanced air filtration system that removes 99% of dust","image_url":"https://images.unsplash.com/photo-1585771724684-25271286bb24?q=80&w=400","price":24999,"rating":0,"review_count":0},
        {"product_id":28,"name":"Ergonomic Office Chair","category":"Home","description":"Adjustable office chair with lumbar support and breathable mesh","image_url":"https://images.unsplash.com/photo-1505797149-43b0069ec26b?q=80&w=400","price":29999,"rating":0,"review_count":0},
        {"product_id":29,"name":"Cast Iron Skillet","category":"Home","description":"Durable cast iron skillet for searing and baking","image_url":"https://images.unsplash.com/photo-1584269600464-37b1b58a9fe7?q=80&w=400","price":4999,"rating":0,"review_count":0},
        {"product_id":30,"name":"Robot Vacuum","category":"Home","description":"Smart robot vacuum cleaner with mapping and app control","image_url":"https://images.unsplash.com/photo-1518640467707-6811f4a6ab73?q=80&w=400","price":34999,"rating":0,"review_count":0},
        {"product_id":31,"name":"Matte Lipstick","category":"Beauty","description":"Long-lasting matte lipstick in vibrant red shade","image_url":"https://images.unsplash.com/photo-1586495777744-4413f21062fa?q=80&w=400","price":799,"rating":0,"review_count":0},
        {"product_id":32,"name":"Liquid Foundation","category":"Beauty","description":"Full coverage liquid foundation for all skin types","image_url":"https://images.unsplash.com/photo-1631729371254-42c2892f0e6e?q=80&w=400","price":1299,"rating":0,"review_count":0},
        {"product_id":33,"name":"Volumizing Mascara","category":"Beauty","description":"Black mascara for volume and length without clumping","image_url":"https://images.unsplash.com/photo-1631214524020-7e18db9a8f92?q=80&w=400","price":999,"rating":0,"review_count":0},
        {"product_id":34,"name":"Eau de Parfum","category":"Beauty","description":"Luxury floral scent perfume with notes of jasmine and rose","image_url":"https://images.unsplash.com/photo-1541643600914-78b084683601?q=80&w=400","price":8999,"rating":0,"review_count":0},
        {"product_id":35,"name":"Hydrating Face Cream","category":"Beauty","description":"Daily moisturizer with hyaluronic acid for dry skin","image_url":"https://images.unsplash.com/photo-1620916566398-39f1143ab7be?q=80&w=400","price":1299,"rating":0,"review_count":0},
        {"product_id":36,"name":"Gel Nail Polish","category":"Beauty","description":"UV LED gel nail polish set in pastel colors","image_url":"https://images.unsplash.com/photo-1604654894610-df63bc536371?q=80&w=400","price":1499,"rating":0,"review_count":0},
        {"product_id":37,"name":"Makeup Brush Set","category":"Beauty","description":"Professional 10-piece synthetic makeup brush collection","image_url":"https://images.unsplash.com/photo-1596462502278-27bfdd403348?q=80&w=400","price":2499,"rating":0,"review_count":0},
        {"product_id":38,"name":"Argan Oil Shampoo","category":"Beauty","description":"Sulfate-free shampoo enriched with argan oil for shine","image_url":"https://images.unsplash.com/photo-1535585209827-a15fcdbc4c2d?q=80&w=400","price":699,"rating":0,"review_count":0},
        {"product_id":39,"name":"Vitamin C Serum","category":"Beauty","description":"Brightening facial serum to reduce dark spots","image_url":"https://images.unsplash.com/photo-1620916297397-a4a5402a3c6c?q=80&w=400","price":1099,"rating":0,"review_count":0},
        {"product_id":40,"name":"Eyeshadow Palette","category":"Beauty","description":"Highly pigmented eyeshadow palette with matte and shimmer shades","image_url":"https://images.unsplash.com/photo-1512496015851-a90fb38ba796?q=80&w=400","price":2999,"rating":0,"review_count":0},
        {"product_id":41,"name":"Tennis Racket","category":"Sports","description":"Lightweight graphite tennis racket for intermediate players","image_url":"https://images.unsplash.com/photo-1617083934555-563d6412e92b?q=80&w=400","price":4999,"rating":0,"review_count":0},
        {"product_id":42,"name":"Basketball","category":"Sports","description":"Official size indoor outdoor basketball with superior grip","image_url":"https://images.unsplash.com/photo-1519861531473-9200262188bf?q=80&w=400","price":2999,"rating":0,"review_count":0},
        {"product_id":43,"name":"Dumbbell Set","category":"Sports","description":"Adjustable weight dumbbell set for home workouts","image_url":"https://images.unsplash.com/photo-1638536532686-d610adfc8e5c?q=80&w=400","price":8999,"rating":0,"review_count":0},
        {"product_id":44,"name":"Soccer Ball","category":"Sports","description":"Professional training soccer ball size 5","image_url":"https://images.unsplash.com/photo-1614632537423-1e6c2e7e0aab?q=80&w=400","price":1499,"rating":0,"review_count":0},
        {"product_id":45,"name":"Cycling Helmet","category":"Sports","description":"Aerodynamic safety helmet with ventilation for road cycling","image_url":"https://images.unsplash.com/photo-1558537348-c0f8e747b520?q=80&w=400","price":7999,"rating":0,"review_count":0},
        {"product_id":46,"name":"Boxing Gloves","category":"Sports","description":"Leather boxing gloves with wrist support for sparring","image_url":"https://images.unsplash.com/photo-1599058945522-28d584b6f0ff?q=80&w=400","price":5999,"rating":0,"review_count":0},
        {"product_id":47,"name":"Golf Club Set","category":"Sports","description":"Complete set of golf clubs with bag for beginners","image_url":"https://images.unsplash.com/photo-1535131749006-b7f58c99034b?q=80&w=400","price":49999,"rating":0,"review_count":0},
        {"product_id":48,"name":"Swimming Goggles","category":"Sports","description":"Anti-fog UV protection swimming goggles with adjustable strap","image_url":"https://images.unsplash.com/photo-1600965962102-9d260a71890d?q=80&w=400","price":999,"rating":0,"review_count":0},
        {"product_id":49,"name":"Baseball Bat","category":"Sports","description":"Aluminum alloy baseball bat with cushioned grip","image_url":"https://images.unsplash.com/photo-1593786481097-cf281dd12e9e?q=80&w=400","price":2999,"rating":0,"review_count":0},
        {"product_id":50,"name":"Jump Rope","category":"Sports","description":"Speed jump rope with ball bearings for cardio training","image_url":"https://images.unsplash.com/photo-1599058945522-28d584b6f0ff?q=80&w=400","price":599,"rating":0,"review_count":0},
    ]
    products_col.delete_many({})
    products_col.insert_many(PRODUCTS)
    return jsonify({"message": f"✓ Seeded {len(PRODUCTS)} products", "count": len(PRODUCTS)})


# ================= AI AGENT =================
@app.route("/api/agent", methods=["POST", "OPTIONS"])
def agent():
    if request.method == "OPTIONS": return jsonify({}), 200

    if not GEMINI_AVAILABLE:
        return jsonify({"error": "google-generativeai package not installed"}), 500

    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not set in environment"}), 500

    data     = request.json or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    # Load all products from MongoDB
    products = list(products_col.find({}, {"_id": 0}))

    def search_products(query="", category="", max_price=None, min_price=None, min_rating=None, limit=5):
        results = products
        if query:
            q = query.lower()
            results = [p for p in results if
                q in p.get("name","").lower() or
                q in p.get("description","").lower() or
                q in p.get("category","").lower()]
        if category:
            results = [p for p in results if
                p.get("category","").lower() == category.lower()]
        if max_price:
            results = [p for p in results if (p.get("price") or 0) <= max_price]
        if min_price:
            results = [p for p in results if (p.get("price") or 0) >= min_price]
        if min_rating:
            results = [p for p in results if (p.get("rating") or 0) >= min_rating]
        return results[:limit]

    def compare_products(product_ids):
        return [p for p in products if p.get("product_id") in product_ids]

    def get_product_details(product_id):
        p = next((p for p in products if p.get("product_id") == product_id), None)
        if p:
            reviews = list(reviews_col.find({"product_id": product_id}, {"_id": 0}))
            p["reviews"] = reviews
        return p or {"error": "Not found"}

    # Build full context for Gemini
    products_text = "\n".join([
        f"ID:{p.get('product_id')} | {p.get('name')} | {p.get('category')} | ₹{p.get('price',0):,} | Rating:{p.get('rating',0)} | {p.get('description','')}"
        for p in products
    ])

    # Build conversation for Gemini
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=f"""You are ShopSmart AI — an intelligent shopping assistant for an Indian e-commerce platform.

You have access to {len(products)} products. Here is the full catalogue:

{products_text}

Your job:
- Search and recommend products based on user needs
- Compare products on price, rating, features
- Suggest complete setups within a budget (e.g. home gym under ₹20,000)
- Answer questions about any product
- Always mention product name, price (₹), and why it matches

Rules:
- Only recommend products from the catalogue above
- Always include product IDs in your response like [ID:5] so frontend can show cards
- Be conversational, helpful and concise
- All prices are in Indian Rupees (₹)"""
    )

    # Convert history to Gemini format
    gemini_history = []
    for msg in messages[:-1]:  # all except last
        role = "user" if msg["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [msg["content"]]})

    chat    = model.start_chat(history=gemini_history)
    user_msg = messages[-1]["content"]

    try:
        response = chat.send_message(user_msg)
        reply    = response.text

        # Extract product IDs mentioned in response like [ID:5]
        import re
        mentioned_ids = [int(x) for x in re.findall(r'\[ID:(\d+)\]', reply)]
        # Also extract plain product_id numbers from context
        product_results = [p for p in products if p.get("product_id") in mentioned_ids]

        # Clean up [ID:x] tags from display text
        clean_reply = re.sub(r'\s*\[ID:\d+\]', '', reply)

        return jsonify({"reply": clean_reply, "products": product_results[:6]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= START =================
if __name__ == "__main__":
    app.run(debug=True, port=5000)