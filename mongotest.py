# DB/mongo_client.py
import os
from pymongo import MongoClient

_atlas_client = None
_local_client = None

def get_target_client(threshold_mb=480):
    global _atlas_client, _local_client
    if _atlas_client is None:
        _atlas_client = MongoClient(os.getenv("MONGO_URI_ATLAS"))
        _local_client = MongoClient(os.getenv("MONGO_URI_LOCAL"))

    stats = _atlas_client["mimori"].command("dbStats")
    used_mb = stats["dataSize"] / (1024 * 1024)

    if used_mb >= threshold_mb:
        return _local_client
    return _atlas_client