import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# قائمة الإيموجيات الممنوعة في المجموعة
FORBIDDEN_EMOJIS = ["😂", "🤣", "💩"]

# ضع ايدي حسابك للتواصل والإذاعة (اختياري)
BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- سيرفر الويب لإبقاء Render مستيقظاً ---
async def handle_ping(request):
    return web.Response(text="Bot is awake and running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- 1. وظيفة حذف الإيموجي في المجموعات ---
async def group_emoji_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    user = update.message.from_user
    first_name = user.first_name if user and user.first_name else "المستخدم"

    for emoji in FORBIDDEN_EMOJIS:
        if emoji in text:
            try:
                await update.message.delete()
                warning_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"عذراً يا {first_name}، يمنع استخدام هذا الإيموجي!"
                )
                await asyncio.sleep(5)
                await warning_msg.delete()
                break
            except Exception as e:
                print(f"خطأ أثناء الحذف: {e}")

# --- 2. وظيفة التواصل والإذاعة ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك! هذا البوت يعمل لإدارة المجموعة واستقبال الرسائل.")

async def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return

    await start_web_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # أمر الترحيب في الخاص
    app.add_handler(CommandHandler("start", start_command))

    # مراقبة وحذف الإيموجيات داخل المجموعات فقط
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & (~filters.COMMAND), group_emoji_filter))

    print("البوت المدمج يعمل الآن بنجاح...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
