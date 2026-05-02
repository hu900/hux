import os

TARGET_URL = os.getenv("TARGET_URL", "https://webook.com/ar/events/YOUR_EVENT_SLUG")

ALLOWED_CATEGORIES = [
    "C5", "C7", "C8", "C4", "C3 RIGHT", "C3 LEFT",
    "C2 RIGHT", "C2 LEFT", "C1 RIGHT", "C1 LEFT",
    "BRONZE", "BRONZE 1", "BRONZE 2"
]

MAX_HOLDS = int(os.getenv("MAX_HOLDS", "5"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "true").lower() == "true"
