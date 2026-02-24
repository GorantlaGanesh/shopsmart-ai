import pandas as pd
from pymongo import MongoClient
import certifi
import os

# MongoDB Atlas connection
mongo_uri = os.environ.get("MONGO_URI")

if not mongo_uri:
    raise Exception("MONGO_URI not set")

client = MongoClient(
    mongo_uri,
    tls=True,
    tlsCAFile=certifi.where()
)

db = client["shopsmart"]
products = db["products"]

# ✅ Correct CSV path
csv_path = "data/product.csv"

# Read CSV
df = pd.read_csv(csv_path)

# Convert rows to dict
records = df.to_dict(orient="records")

# Optional: clear old data
products.delete_many({})

# Insert into MongoDB
products.insert_many(records)

print(f"✅ Inserted {len(records)} products into MongoDB")
