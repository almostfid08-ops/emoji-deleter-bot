import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# قائمة الإيموجيات الممنوعة
FORBIDDEN_EMOJIS = ["😂", "🤣", "💩"]

# جلب التوكين من متغيرات البيئة لضمان الأمان
BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    
    for emoji in FORBIDDEN_EMOJIS:
        if emoji in text:
            try:
                await update.message.delete()
                print(f"تم حذف رسالة تحتوي على: {emoji}")
                break
            except Exception as e:
                print(f"خطأ أثناء الحذف: {e}")

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        exit(1)
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_message))
    print("البوت يعمل الآن...")
    app.run_polling()
