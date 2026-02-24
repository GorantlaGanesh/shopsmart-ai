from pymongo import MongoClient
import os

# MongoDB connection
mongo_uri = os.environ.get("MONGO_URI")

client = MongoClient(mongo_uri)
db = client["shopsmart"]
products = db["products"]

# Sample products (you can change/add more)
product_data = [
    {
        "product_id": 1,
        "name": "iPhone 14",
        "price": 69999,
        "category": "Mobile",
        "rating": 4.6,
        "image": "https://via.placeholder.com/200"
    },
    {
        "product_id": 2,
        "name": "Samsung Galaxy S23",
        "price": 74999,
        "category": "Mobile",
        "rating": 4.5,
        "image": "https://via.placeholder.com/200"
    },
    {
        "product_id": 3,
        "name": "Sony Headphones",
        "price": 12999,
        "category": "Accessories",
        "rating": 4.4,
        "image": "https://via.placeholder.com/200"
    }
]

# Clear old data (optional)
products.delete_many({})

# Insert new data
products.insert_many(product_data)

print("✅ Products inserted successfully")
