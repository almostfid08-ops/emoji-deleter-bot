import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# قائمة الإيموجيات الممنوعة
FORBIDDEN_EMOJIS = ["😂", "🤣", "💩"]

# جلب التوكين من متغيرات البيئة
BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- سيرفر ويب خفيف لاستجابة UptimeRobot ---
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
    print(f"سيرفر الويب يعمل على المنفذ: {port}")

# --- كود البوت لتفقد الرسائل ---
async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    user = update.message.from_user
    first_name = user.first_name if user and user.first_name else "المستخدم"

    for emoji in FORBIDDEN_EMOJIS:
        if emoji in text:
            try:
                # 1. حذف الرسالة
                await update.message.delete()
                print(f"تم حذف رسالة تحتوي على: {emoji} من المستخدم: {first_name}")

                # 2. إرسال تنبيه
                warning_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"عذراً يا {first_name}، يمنع استخدام هذا الإيموجي في المجموعة!"
                )

                # 3. حذف التنبيه بعد 5 ثوانٍ
                await asyncio.sleep(5)
                await warning_msg.delete()

                break
            except Exception as e:
                print(f"خطأ أثناء الحذف/التنبيه: {e}")

async def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return

    # تشغيل سيرفر الويب والبوت معاً في نفس الدورة (Event Loop)
    await start_web_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_message))

    print("البوت يعمل الآن ويراقب الرسائل...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    # إبقاء التطبيق يعمل
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
