import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "messages.db"
TG_SESSION = str(DATA_DIR / "user")  # Telethon: data/user.session
AGENT_SESSIONS = DATA_DIR / "agent_sessions.json"

API_ID = int(os.environ.get("TG_API_ID") or 0)
API_HASH = os.environ.get("TG_API_HASH", "")
TARGET_CHAT = os.environ.get("TG_TARGET_CHAT", "").strip()
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
# TG_ALLOWED_IDS: id через запятую; спецзначение "group" дополнительно пускает
# в личку всех участников индексируемой группы (проверка getChatMember).
_raw_ids = os.environ.get("TG_ALLOWED_IDS", "").replace(" ", "").split(",")
ALLOW_GROUP_MEMBERS = "group" in _raw_ids
ALLOWED_IDS = {int(x) for x in _raw_ids if x and x != "group"}
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
SESSION_FRESH_HOURS = float(os.environ.get("SESSION_FRESH_HOURS", "6"))
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
