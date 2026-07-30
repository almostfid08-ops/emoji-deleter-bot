import os
import logging
import asyncio
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

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    user = update.message.from_user
    
    # اسم المستخدم
    first_name = user.first_name if user and user.first_name else "المستخدم"

    for emoji in FORBIDDEN_EMOJIS:
        if emoji in text:
            try:
                # 1. حذف الرسالة المخالفة
                await update.message.delete()
                print(f"تم حذف رسالة تحتوي على: {emoji} من المستخدم: {first_name}")

                # 2. إرسال تنبيه بسيط
                warning_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"عذراً يا {first_name}، يمنع استخدام هذا الإيموجي في المجموعة!"
                )

                # 3. حذف التنبيه بعد 5 ثوانٍ
                await asyncio.sleep(5)
                await warning_msg.delete()

                break
            except Exception as e:
                print(f"خطأ أثناء العملية: {e}")

def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_message))

    print("البوت يعمل الآن ويراقب الرسائل...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
