from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity
)
import pandas as pd

app = Flask(__name__)
CORS(app)

app.config["JWT_SECRET_KEY"] = "shopsmart_ai_secret_2026!@#"
jwt = JWTManager(app)

df = pd.read_csv("data/products.csv")

USERS = {}
USER_HISTORY = {}

@app.route("/")
def home():
    return {"status": "ShopSmart API running"}

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

@app.route("/api/products")
def products():
    return jsonify(df.to_dict(orient="records"))

@app.route("/api/view/<int:pid>", methods=["POST"])
@jwt_required()
def view(pid):
    user = get_jwt_identity()
    USER_HISTORY.setdefault(user, []).append(pid)
    return {"msg": "viewed"}

@app.route("/api/recommend/user")
@jwt_required()
def recommend_user():
    user = get_jwt_identity()
    seen = set(USER_HISTORY.get(user, []))
    recs = df[~df["product_id"].isin(seen)].head(5)
    return jsonify(recs.to_dict(orient="records"))

if __name__ == "__main__":
    app.run()