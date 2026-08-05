import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
)
from telegram.constants import ChatMemberStatus

# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ملاحظة: قم باستبدال TOKEN بـ توكن البوت الخاص بك
TOKEN = "YOUR_BOT_TOKEN_HERE"


# -------------------------------------------------------------------
# 1. إصلاح الخلل: تتبع واسترجاع المجموعات التي يكون فيها البوت مشرفًا
# -------------------------------------------------------------------

async def track_bot_admin_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    مُستمع يتم تفعيله عند تغيير حالة البوت داخل مجموعة (إضافة، إزالة، ترقية لمشرف).
    يتم حفظ المجموعة فقط إذا كان البوت يمتلك صلاحية Administrator.
    """
    result = update.my_chat_member
    if not result:
        return

    chat = result.chat
    new_status = result.new_chat_member.status

    # التحقق من أن المحادثة هي مجموعة أو مجموعة خارقة (Supergroup)
    if chat.type in ["group", "supergroup"]:
        # تهيئة قائمة المجموعات إن لم تكن موجودة
        if "admin_groups" not in context.bot_data:
            context.bot_data["admin_groups"] = {}

        if new_status == ChatMemberStatus.ADMINISTRATOR:
            # إضافة أو تحديث بيانات المجموعة
            context.bot_data["admin_groups"][chat.id] = chat.title
            logger.info(f"تمت إضافة/تحديث المجموعة {chat.title} ({chat.id}) كـ مشرف.")
        else:
            # إزالة المجموعة إذا فقد البوت صلاحيات المشرف أو تم طرده
            if chat.id in context.bot_data["admin_groups"]:
                del context.bot_data["admin_groups"][chat.id]
                logger.info(f"تمت إزالة المجموعة {chat.title} ({chat.id}) لفقدان الصلاحيات.")


async def get_valid_admin_groups(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """
    دالة مساعدة لجلب والتحقق الفعلي من المجموعات التي يملك فيها البوت صلاحيات الإدارة حاليًا.
    """
    valid_groups = {}
    stored_groups = context.bot_data.get("admin_groups", {})

    for chat_id, title in list(stored_groups.items()):
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if bot_member.status == ChatMemberStatus.ADMINISTRATOR:
                valid_groups[chat_id] = title
            else:
                # إزالة المجموعة إذا لم يعد البوت مشرفًا
                del context.bot_data["admin_groups"][chat_id]
        except Exception as e:
            # إزالة المجموعة إذا يتعذر الوصول إليها (مثلاً: تم طرد البوت)
            logger.warning(f"تعذر التحقق من المجموعة {chat_id}: {e}")
            if chat_id in context.bot_data.get("admin_groups", {}):
                del context.bot_data["admin_groups"][chat_id]

    return valid_groups


# -------------------------------------------------------------------
# 2. عرض المجموعات المستهدفة عند الضغط على الزر
# -------------------------------------------------------------------

async def show_target_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    عرض قائمة المجموعات التي يكون فيها البوت مشرفًا لاختيار المجموعة المستهدفة للاشتراك الإجباري.
    """
    query = update.callback_query
    await query.answer()

    # جلب المجموعات الصالحة
    admin_groups = await get_valid_admin_groups(context)

    if not admin_groups:
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
        await query.edit_message_text(
            text="⚠️ **لم يتم العثور على أي مجموعة.**\n\n"
                 "يرجى التأكد من إضافة البوت إلى المجموعة وتعيينه كـ **مشرف** أولاً، "
                 "ثم أعد محاولة فتح هذه القائمة.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # إنشاء أزرار للمجموعات المتاحة
    keyboard = []
    for chat_id, title in admin_groups.items():
        keyboard.append([
            InlineKeyboardButton(f"👥 {title}", callback_data=f"select_group_{chat_id}")
        ])

    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])

    await query.edit_message_text(
        text="🎯 **اختر المجموعة المستهدفة لتفعيل الاشتراك الإجباري:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# -------------------------------------------------------------------
# 3. الأوامر ووظائف واجهة المستخدم
# -------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البدء لمسؤول البوت."""
    keyboard = [
        [InlineKeyboardButton("🎯 اختيار المجموعة المستهدفة", callback_data="select_target_group")]
    ]
    await update.message.reply_text(
        "مرحباً بك في لوحة تحكم ميزة الاشتراك الإجباري.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الضغط على الأزرار."""
    query = update.callback_query
    data = query.data

    if data == "select_target_group":
        await show_target_groups(update, context)

    elif data.startswith("select_group_"):
        selected_chat_id = data.replace("select_group_", "")
        group_title = context.bot_data.get("admin_groups", {}).get(int(selected_chat_id), "المجموعة")
        
        # تخزين المجموعة المختارة في الإعدادات
        context.bot_data["forced_channel_id"] = selected_chat_id

        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="select_target_group")]]
        await query.edit_message_text(
            text=f"✅ تم تحديد **{group_title}** كـ مجموعة مستهدفة للاشتراك الإجباري بنجاح!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🎯 اختيار المجموعة المستهدفة", callback_data="select_target_group")]
        ]
        await query.edit_message_text(
            "مرحباً بك في لوحة تحكم ميزة الاشتراك الإجباري.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# -------------------------------------------------------------------
# 4. تشغيل البوت ورابط المعالجات
# -------------------------------------------------------------------

def main():
    application = Application.builder().token(TOKEN).build()

    # معالج تحديث حالة العضوية (مُصلح المشكلة الأساسي)
    application.add_handler(ChatMemberHandler(track_bot_admin_groups, ChatMemberHandler.MY_CHAT_MEMBER))

    # أوامر الاستجابة والأزرار
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # بدء البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
