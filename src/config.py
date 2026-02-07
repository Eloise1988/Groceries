import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017").strip()
MONGO_DB = os.getenv("MONGO_DB", "grocery_bot").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "UTC").strip()
SUGGESTION_COUNT = int(os.getenv("SUGGESTION_COUNT", "5").strip())
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1").strip()
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2").strip())

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required. Set it in your environment or .env file.")

ADMIN_CHAT_ID_INT = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
