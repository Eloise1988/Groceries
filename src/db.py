from motor.motor_asyncio import AsyncIOMotorClient
from .config import MONGO_URI, MONGO_DB

_client = None


def get_client():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI)
    return _client


def get_db():
    client = get_client()
    return client[MONGO_DB]
