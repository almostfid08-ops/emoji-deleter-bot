import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

# إعداد التسجيل (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "YOUR_BOT_TOKEN_HERE"


# -------------------------------------------------------------------
# 1. إدارة المجموعات والقنوات والتتبع (الحفاظ على الكود السابق)
# -------------------------------------------------------------------

async def track_bot_admin_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مُستمع يتم تفعيله عند تغيير حالة البوت داخل مجموعة أو قناة."""
    result = update.my_chat_member
    if not result:
        return

    chat = result.chat
    new_status = result.new_chat_member.status

    if chat.type in ["group", "supergroup"]:
        if "admin_groups" not in context.bot_data:
            context.bot_data["admin_groups"] = {}

        if new_status == ChatMemberStatus.ADMINISTRATOR:
            context.bot_data["admin_groups"][chat.id] = chat.title
        else:
            if chat.id in context.bot_data["admin_groups"]:
                del context.bot_data["admin_groups"][chat.id]


async def get_valid_admin_groups(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """جلب والتحقق الفعلي من المجموعات التي يملك فيها البوت صلاحيات الإدارة."""
    valid_groups = {}
    stored_groups = context.bot_data.get("admin_groups", {})

    for chat_id, title in list(stored_groups.items()):
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if bot_member.status == ChatMemberStatus.ADMINISTRATOR:
                valid_groups[chat_id] = title
            else:
                del context.bot_data["admin_groups"][chat_id]
        except Exception:
            if chat_id in context.bot_data.get("admin_groups", {}):
                del context.bot_data["admin_groups"][chat_id]

    return valid_groups


# -------------------------------------------------------------------
# 2. إصلاح مشكلة استقبال واستخراج رابط القناة بدون تعليق
# -------------------------------------------------------------------

def extract_channel_username(url_or_text: str) -> str:
    """استخراج المعرف أو الرابط النظيف من النص المكتوب."""
    text = url_or_text.strip()
    # إذا كان الرابط مكتوب بصيغة t.me/username أو https://t.me/username
    match = re.search(r"(?:t\.me/|@)([a-zA-Z0-9_]{5,})", text)
    if match:
        return f"@{match.group(1)}"
    if text.startswith("@"):
        return text
    return text


async def process_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالج استقبال رابط القناة المطلوبة للاشتراك الإجباري.
    مُصلّح بالكامل لمنع تجمّد البوت وتأمين معالجة الأخطاء.
    """
    # التحقق مما إذا كان المسؤول في وضع انتظار إدخال الرابط
    if not context.user_data.get("awaiting_channel_link"):
        return  # تجاهل الرسائل العادية إذا لم يكن البوت ينتظر رابطًا

    user_input = update.message.text.strip()
    channel_identifier = extract_channel_username(user_input)

    try:
        # محاولة جلب معلومات القناة للتحقق من وجودها وصحتها
        chat = await context.bot.get_chat(channel_identifier)

        # حفظ معلومات القناة في context.bot_data
        context.bot_data["forced_channel_id"] = chat.id
        context.bot_data["forced_channel_title"] = chat.title
        context.bot_data["forced_channel_username"] = chat.username
        context.bot_data["forced_channel_link"] = (
            f"https://t.me/{chat.username}" if chat.username else user_input
        )

        # إنهاء حالة الانتظار
        context.user_data["awaiting_channel_link"] = False

        keyboard = [
            [InlineKeyboardButton("📢 معاينة زر الاشتراك", callback_data="preview_sub_button")],
            [InlineKeyboardButton("🔙 الصفحة الرئيسية", callback_data="main_menu")]
        ]

        await update.message.reply_text(
            f"✅ **تم ضبط قناة الاشتراك الإجباري بنجاح!**\n\n"
            f"📌 **القناة:** {chat.title}\n"
            f"🆔 **المعرف/الرابط:** {channel_identifier}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    except TelegramError as e:
        logger.error(f"خطأ أثناء التحقق من القناة: {e}")
        # الاستجابة الفورية عند حدوث خطأ لمنع تجمّد البوت
        keyboard = [[InlineKeyboardButton("🔙 إلغاء والعودة", callback_data="main_menu")]]
        await update.message.reply_text(
            "❌ **تعذر الوصول إلى القناة!**\n\n"
            "يرجى التأكد من:\n"
            "1. صحة الرابط أو المعرف (مثال: `@MyChannel` أو `https://t.me/MyChannel`).\n"
            "2. إضافة البوت كـ **مشرف** داخل القناة لتمكينه من التحقق من المشتركين.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"خطأ غير متوقع: {e}")
        context.user_data["awaiting_channel_link"] = False
        await update.message.reply_text(
            "⚠️ حدث خطأ غير متوقع أثناء معالجة الرابط. يرجى المحاولة مرة أخرى باستخدام /start"
        )


# -------------------------------------------------------------------
# 3. عرض رسالة الاشتراك الإجباري بالزر الاحترافي (Inline Button)
# -------------------------------------------------------------------

async def send_forced_subscribe_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    دالة إرسال طلب الاشتراك للمستخدم بصيغة احترافية تتضمن زر القناة المباشر.
    """
    channel_link = context.bot_data.get("forced_channel_link", "https://t.me/")
    channel_title = context.bot_data.get("forced_channel_title", "القناة الرسمية")

    # إعداد الأزرار الاحترافية (Inline Keyboard)
    keyboard = [
        [InlineKeyboardButton(f"📢 اشترك في {channel_title}", url=channel_link)],
        [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_subscription")]
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ **تنبيه: يجب عليك الاشتراك في القناة أولاً لاستخدام البوت.**\n\n"
             "إضغط على الزر أدناه للانضمام، ثم اضغط على **تحقق من الاشتراك**:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# -------------------------------------------------------------------
# 4. واجهة التحكم والأزرار
# -------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البدء وتنظيف حالة الانتظار."""
    context.user_data["awaiting_channel_link"] = False

    keyboard = [
        [InlineKeyboardButton("🎯 اختيار المجموعة المستهدفة", callback_data="select_target_group")],
        [InlineKeyboardButton("🔗 ضبط قناة الاشتراك الإجباري", callback_data="set_channel_link")]
    ]
    
    if update.message:
        await update.message.reply_text(
            "مرحباً بك في لوحة تحكم ميزة الاشتراك الإجباري.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "مرحباً بك في لوحة تحكم ميزة الاشتراك الإجباري.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_target_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المجموعات التي يكون فيها البوت مشرفًا."""
    query = update.callback_query
    await query.answer()

    admin_groups = await get_valid_admin_groups(context)

    if not admin_groups:
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
        await query.edit_message_text(
            text="⚠️ **لم يتم العثور على أي مجموعة.**\n\n"
                 "يرجى التأكد من إضافة البوت إلى المجموعة وتعيينه كـ **مشرف** أولاً.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    keyboard = []
    for group_id, title in admin_groups.items():
        keyboard.append([
            InlineKeyboardButton(f"👥 {title}", callback_data=f"select_group_{group_id}")
        ])

    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])

    await query.edit_message_text(
        text="🎯 **اختر المجموعة المستهدفة لتفعيل الاشتراك الإجباري:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأزرار التفاعلية."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "select_target_group":
        await show_target_groups(update, context)

    elif data == "set_channel_link":
        # تفعيل حالة انتظار رابط القناة
        context.user_data["awaiting_channel_link"] = True
        keyboard = [[InlineKeyboardButton("🔙 إلغاء", callback_data="main_menu")]]
        await query.edit_message_text(
            text="📢 **أرسل الآن رابط القناة أو معرفها** (مثال: `@MyChannel` أو `https://t.me/MyChannel`):",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "preview_sub_button":
        # معاينة كيفية ظهور الرسالة للجمهور
        await send_forced_subscribe_message(query.message.chat_id, context)

    elif data.startswith("select_group_"):
        selected_chat_id = data.replace("select_group_", "")
        group_title = context.bot_data.get("admin_groups", {}).get(int(selected_chat_id), "المجموعة")
        context.bot_data["target_group_id"] = selected_chat_id

        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="select_target_group")]]
        await query.edit_message_text(
            text=f"✅ تم تحديد **{group_title}** كـ مجموعة مستهدفة بنجاح!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "main_menu":
        context.user_data["awaiting_channel_link"] = False
        await start(update, context)


# -------------------------------------------------------------------
# 5. تشغيل البوت وإضافة المعالجات
# -------------------------------------------------------------------

def main():
    application = Application.builder().token(TOKEN).build()

    # معالج حالة المشرف للمجموعات والقنوات
    application.add_handler(ChatMemberHandler(track_bot_admin_groups, ChatMemberHandler.MY_CHAT_MEMBER))

    # الأوامر والأزرار
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # معالج النصوص لاستقبال رابط القناة مع التأكد من إدراج المصفى الصحيح
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_channel_link))

    # بدء التشغيل
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
