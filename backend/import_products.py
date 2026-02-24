import pandas as pd
from pymongo import MongoClient
import certifi
import os

# Get MongoDB URI
mongo_uri = os.environ.get("MONGO_URI")

if not mongo_uri:
    raise Exception("MONGO_URI not set")

# Connect to MongoDB Atlas
client = MongoClient(
    mongo_uri,
    tls=True,
    tlsCAFile=certifi.where()
)

db = client["shopsmart"]
collection = db["products"]

# Load CSV (correct path)
csv_path = "data/product.csv"
df = pd.read_csv(csv_path)

# Convert to dict
records = df.to_dict(orient="records")

# OPTIONAL: clear old data
collection.delete_many({})

# Insert data
collection.insert_many(records)

print(f"✅ Imported {len(records)} products into MongoDB")
