import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from utils import parse_cookie_string
from queue_worker import queue, worker

users = {}

async def start(update: Update, context):
    await update.message.reply_text("أرسل الكوكيز")

async def save(update: Update, context):
    user_id = update.effective_user.id
    cookies = parse_cookie_string(update.message.text)

    users[user_id] = {
        "cookies": cookies,
        "headers": {
            "User-Agent": "Mozilla/5.0"
        }
    }

    await update.message.reply_text("✅ تم حفظ حسابك")

async def book(update: Update, context):
    user_id = update.effective_user.id

    if user_id not in users:
        await update.message.reply_text("ارسل الكوكيز أول")
        return

    await queue.put((user_id, users[user_id]))
    await update.message.reply_text("⏳ تم إضافة طلبك")

def main():
    app = ApplicationBuilder().token("YOUR_TOKEN").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("book", book))
    app.add_handler(MessageHandler(filters.TEXT, save))

    app.post_init = lambda app: asyncio.create_task(worker(app.bot))

    app.run_polling()

if __name__ == "__main__":
    main()
