import asyncio
import logging
from engine import BookingEngine

logger = logging.getLogger(__name__)

# Shared queue: items are (user_id, user_data_dict)
queue: asyncio.Queue = asyncio.Queue()

# Track which user_ids are currently being processed
active_users: set[int] = set()


async def worker(bot, worker_id: int = 1):
    """
    Long-running coroutine that drains the booking queue.
    Starts one BookingEngine per queued request and reports results via Telegram.
    """
    logger.info(f"Queue worker #{worker_id} started")

    while True:
        user_id, data = await queue.get()

        if user_id in active_users:
            await _safe_notify(
                bot, user_id,
                "⚠️ طلبك السابق لا يزال قيد المعالجة. انتظر حتى ينتهي."
            )
            queue.task_done()
            continue

        active_users.add(user_id)
        logger.info(f"[worker#{worker_id}] Processing booking for user {user_id}")

        try:
            engine = BookingEngine(user_data=data, bot=bot, user_id=user_id)
            result = await engine.run()

            if result:
                logger.info(f"[worker#{worker_id}] Booking SUCCESS for user {user_id}")
            else:
                logger.info(f"[worker#{worker_id}] Booking FAILED for user {user_id}")

        except Exception as e:
            logger.error(
                f"[worker#{worker_id}] Unhandled exception for user {user_id}: {e}",
                exc_info=True,
            )
            await _safe_notify(
                bot, user_id,
                f"❌ خطأ غير متوقع: {str(e)[:120]}\n\nحاول مرة أخرى لاحقاً."
            )

        finally:
            active_users.discard(user_id)
            queue.task_done()
            logger.info(
                f"[worker#{worker_id}] Done. Queue size: {queue.qsize()}. "
                f"Active users: {len(active_users)}"
            )


def is_user_active(user_id: int) -> bool:
    """Check if a user already has a booking in progress."""
    return user_id in active_users


def queue_size() -> int:
    return queue.qsize()


async def _safe_notify(bot, user_id: int, message: str):
    """Send a Telegram message without raising."""
    try:
        await bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")
