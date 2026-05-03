import os

# ──────────────────────────────────────────────
#  Target event URL
#  Set via environment variable on Railway/Heroku
# ──────────────────────────────────────────────
TARGET_URL = os.getenv(
    "TARGET_URL",
    "https://webook.com/ar/events/YOUR_EVENT_SLUG"
)

# ──────────────────────────────────────────────
#  Categories that the bot is allowed to book
#  Ordered by preference (first = most preferred)
# ──────────────────────────────────────────────
ALLOWED_CATEGORIES = [
    "C1 RIGHT", "C1 LEFT",
    "C2 RIGHT", "C2 LEFT",
    "C3 RIGHT", "C3 LEFT",
    "C4",
    "C5", "C7", "C8",
    "BRONZE", "BRONZE 1", "BRONZE 2", "Bronze", "Silver",
]

# ──────────────────────────────────────────────
#  Booking limits
# ──────────────────────────────────────────────
MAX_HOLDS = int(os.getenv("MAX_HOLDS", "5"))          # Max tickets per booking
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))       # Retry attempts per booking job

# ──────────────────────────────────────────────
#  Worker concurrency
# ──────────────────────────────────────────────
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "1"))    # Parallel queue workers

# ──────────────────────────────────────────────
#  Debug mode — saves screenshots to /tmp/
# ──────────────────────────────────────────────
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
