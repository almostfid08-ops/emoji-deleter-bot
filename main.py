import datetime
import logging
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ---------------------------------------------------------
# 1. قائمة المشرفين المصرح لهم بقائمة الأدمن
# ---------------------------------------------------------
ADMIN_IDS = [123456789]  # ضع ID المطور/المشرفين هنا
DEVELOPER_USERNAME = "@Nabil1r"

# ---------------------------------------------------------
# 2. الهيكل التخزيني للإعدادات والأنظمة (In-Memory Data)
# ---------------------------------------------------------
# بيانات تخصيص المجموعات والنداءات
settings = {
    "target_chat_id": None,               # ID المجموعة المستهدفة للوضع الصامت/النداء
    "sos_keyword": "بوت مراقبة",            # كلمة نداء الاستغاثة
    "sos_limit": 3,                       # عدد المرات المطلوب لتفعيل الصمت
    "sos_mute_duration_minutes": 30,      # مدة الوضع الصامت بالدقائق
    "block_words": True,                  # حظر الكلمات
    "block_links": True,                  # حظر الروابط
    "block_stickers": True,               # حظر الملصقات
    "block_animations": True,             # حظر الملصقات المتحركة/GIFs
}

# عدادات نداء الاستغاثة وسجل المستخدمين
sos_tracker: Dict[int, int] = {}          # {chat_id: count}
user_msg_tracker: Dict[int, int] = {}     # {user_id: count_in_private}


# ---------------------------------------------------------
# 3. معالج رسائل الخاص (غير المشرفين)
# ---------------------------------------------------------
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # إذا كان المستخدم مشرفاً أو مطوراً، تظهر له لوحة الإدارة العادية
    if user.id in ADMIN_IDS:
        await show_admin_panel(update, context)
        return

    # للعملاء والمستخدمين غير المشرفين
    current_count = user_msg_tracker.get(user.id, 0) + 1
    user_msg_tracker[user.id] = current_count

    if current_count >= 2:
        # الرسالة الثانية فما فوق
        warning_msg = (
            "⚠️ **تنبيه مهم:**\n"
            "أنا بوت غير مبرمج لاستقبال الرسائل أو المحادثات الخاصة، "
            "ورسائلكم لا تصل للمطور نهائياً ولن تحظوا برد رسمي هنا.\n\n"
            f"يرجى التواصل مباشرة مع مطور البوت للحصول على الخدمة: {DEVELOPER_USERNAME}\n"
            "شكراً لتفهمكم!"
        )
        await update.message.reply_text(warning_msg, parse_mode="Markdown")
    else:
        # الرسالة الأولى أو عند إرسال /start
        intro_msg = (
            "🤖 **مرحباً بك في بوت مراقبة المجموعات الدراسية!**\n\n"
            "صُمم هذا البوت ليكون كمشرف آلي داخل المجموعات بهدف:\n"
            "• حذف الإيموجيات والرموز غير المرغوب بها للحفاظ على الجو الدراسي.\n"
            "• حذف الروابط المزعجة والإعلانات تلقائياً.\n"
            "• حظر الكلمات والملصقات والوسائط المزعجة.\n"
            "• إدارة الوضع الصامت لحماية المجموعة أثناء التجاوزات.\n\n"
            f"📥 **للحصول على هذه الميزات في مجموعتك:**\n"
            f"يرجى التواصل مع مطور البوت {DEVELOPER_USERNAME} للترخيص وإضافة البوت كمشرف."
        )
        await update.message.reply_text(intro_msg, parse_mode="Markdown")


# ---------------------------------------------------------
# 4. لوحة التحكم بالأدمن (Inline Keyboard)
# ---------------------------------------------------------
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👤 إذاعة للمستخدمين (خاص)", callback_data="bc_private")],
        [InlineKeyboardButton("🔊 إدارة الوضع الصامت", callback_data="manage_silent")],
        [InlineKeyboardButton("🎯 تحديد المجموعة المستهدفة", callback_data="set_target_group")],
        [InlineKeyboardButton("📖 دليل أوامر الإشراف", callback_data="help_guide")],
        [InlineKeyboardButton("👤 إضافة مشرف جديد", callback_data="add_admin")],
        [
            InlineKeyboardButton("⛔ الإيموجيات المحظورة", callback_data="block_emojis"),
            InlineKeyboardButton("⛔ الكلمات المحظورة", callback_data="block_words")
        ],
        [
            InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="toggle_links"),
            InlineKeyboardButton("🖼️ حظر الملصقات", callback_data="toggle_stickers")
        ],
        [InlineKeyboardButton("🎬 حظر الصور المتحركة (GIF)", callback_data="toggle_gifs")],
        [InlineKeyboardButton("🚨 إعدادات نداء الاستغاثة", callback_data="config_sos")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "أهلاً بك يا أدمن في لوحة التحكم الإدارية! 🛠️\nاختر من الأزرار أدناه للتحكم بالبوت:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup)


# ---------------------------------------------------------
# 5. معالجة نداء الاستغاثة (SOS Rescue System)
# ---------------------------------------------------------
async def handle_group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    
    if not message or not chat or chat.type not in ["group", "supergroup"]:
        return

    # التحقق مما إذا كانت الرسالة تُرسل أثناء الوضع الصامت ومن شخص مصرح له
    if is_chat_silent(chat.id):
        # السماح بالقنوات والمشرفين المتخفيين
        if message.sender_chat or (message.from_user and is_admin_or_anonymous(message)):
            return  # استثناء المشرفين المتخفيين ورسائل القنوات
        else:
            try:
                await message.delete()  # حذف رسائل باقي الأعضاء أثناء الصمت
            except Exception:
                pass
            return

    # منطق نداء الاستغاثة
    text = message.text or message.caption or ""
    sos_word = settings["sos_keyword"]

    if sos_word.lower() in text.lower():
        current_calls = sos_tracker.get(chat.id, 0) + 1
        sos_tracker[chat.id] = current_calls
        limit = settings["sos_limit"]

        if current_calls == 1:
            reply_text = (
                f"🚨 **تنبيه نداء استغاثة (1/{limit}):**\n"
                f"تم رصد نداء باسم البوت. يدل هذا على وجود تجاوزات أو محتوى غير مسموح.\n"
                f"في حال تكرار النداء {limit} مرات متتالية سيتم تفعيل الوضع الصامت تلقائياً."
            )
            await message.reply_text(reply_text, parse_mode="Markdown")

        elif current_calls == 2:
            reply_text = (
                f"⚠️ **تحذير هام (2/{limit}):**\n"
                f"تكرر النداء! المتبقي نداء واحد لتغليف المجموعة بالوضع الصامت.\n"
                f"📜 **ملاحظة:** في حال كانت النداءات بدون سبب كافي، سيتم حظر الأعضاء المتسببين بالنداء العشوائي."
            )
            await message.reply_text(reply_text, parse_mode="Markdown")

        elif current_calls >= limit:
            # تفعيل الوضع الصامت عند وصول 3/3
            sos_tracker[chat.id] = 0  # إعادة ضبط العداد
            await enable_silent_mode(chat.id, context, is_auto_sos=True)


# ---------------------------------------------------------
# 6. إدارة وتفعيل الوضع الصامت (التقييد المستهدف)
# ---------------------------------------------------------
async def enable_silent_mode(chat_id: int, context: ContextTypes.DEFAULT_TYPE, is_auto_sos: bool = False):
    # إغلاق صلاحيات الإرسال عن الأعضاء
    permissions = ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_other_messages=False
    )
    
    try:
        await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
        
        keyboard = [[InlineKeyboardButton("🔓 إلغاء الوضع الصامت", callback_data=f"unmute_{chat_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg = (
            "🛑 **تم تفعيل الوضع الصامت للمجموعة (3/3)!**\n\n"
            "تم إغلاق الدردشة لحين وصول المشرفين ومعاينة الوضع.\n"
            "يمكن للمشرفين الضغط على الزر أدناه لإلغاء القفل في أي وقت."
        )
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=reply_markup, parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Failed to mute chat {chat_id}: {e}")


async def handle_unmute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = int(query.data.split("_")[1])
    user = query.from_user

    # التأكد أن ضاغط الزر هو مشرف
    member = await context.bot.get_chat_member(chat_id, user.id)
    if member.status in ["administrator", "creator"] or user.id in ADMIN_IDS:
        # إعادة الصلاحيات الكاملة
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_other_messages=True
        )
        await context.bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
        await query.answer("تم إلغاء الوضع الصامت بنجاح!")
        await query.edit_message_text("🟢 **تم فتح المجموعة وإلغاء الوضع الصامت بواسطة المشرف.**")
    else:
        await query.answer("❌ هذا الخيار مخصص للمشرفين فقط!", show_alert=True)


# ---------------------------------------------------------
# 7. الدعم المساعد للتحقق من الصلاحيات والمشرقين المتخفيين
# ---------------------------------------------------------
def is_admin_or_anonymous(message) -> bool:
    # تحقق من المشرف المتخفي أو إرسال الرسائل باسم المجموعة/القناة
    if message.sender_chat or message.is_automatic_forward:
        return True
    return False

def is_chat_silent(chat_id: int) -> bool:
    # للتحقق من كتم المجموعة المستهدفة
    return settings["target_chat_id"] == chat_id


# ---------------------------------------------------------
# 8. ربط المعالجات بالتطبيق الرئيسي
# ---------------------------------------------------------
def main():
    # ضع التوكن الخاص ببرنامجك هنا
    app = ApplicationBuilder().token("YOUR_BOT_TOKEN_HERE").build()

    # معالجة أمر /start والمحادثات الخاصة
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_private_message))
    
    # معالجة نداء الاستغاثة والمجموعات
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_group_messages))
    
    # معالجة أزرار الكولباك
    app.add_handler(CallbackQueryHandler(handle_unmute_callback, pattern=r"^unmute_"))

    app.run_polling()

if __name__ == "__main__":
    main()
