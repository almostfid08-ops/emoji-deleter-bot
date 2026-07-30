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
    
    for emoji in FORBIDDEN_EMOJIS:
        if emoji in text:
            try:
                # 1. حذف الرسالة المخالفة
                await update.message.delete()
                print(f"تم حذف رسالة تحتوي على: {emoji} من المستخدم: {user.first_name}")
                
                # 2. إرسال رسالة توضيحية مع الإشارة للمستخدم (Mention)
                warning_msg = await update.effective_chat.send_message(
                    text=f"عذراً عزيزي {user.mention_html()}، يمنع استخدام هذا الإيموجي في المجموعة!",
                    parse_mode='HTML'
                )
                
                # 3. انتظر 5 ثوانٍ ثم احذف رسالة التنبيه لنظافة المجموعة
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
