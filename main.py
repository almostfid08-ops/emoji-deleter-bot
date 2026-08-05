import os
import logging
from asyncio import create_task
from aiohttp import web
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

# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# يعتمد على التوكن المسجل في بيئة التشغيل أو التوكن الخاص بك تلقائيًا
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")


# -------------------------------------------------------------------
# 1. خادم ويب مصغر لمنع توقف Render والاستجابة لـ UptimeRobot
# -------------------------------------------------------------------

async def handle_ping(request):
    return web.Response(text="Bot is active!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


# -------------------------------------------------------------------
# 2. إدارة المجموعات وتتبع المشرفين
# -------------------------------------------------------------------

async def track_bot_admin_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
# 3. معالجة التوجيه (Forward) والتحقق من صلاحيات القناة وإنشاء الرابط
# -------------------------------------------------------------------

async def process_channel_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    تستقبل الرسالة الموجهة، تستخرج ID القناة، تتحقق من صلاحيات الإدارة،
    وتولد رابط دعوة أو تستخدم المعرف العام دون أي تعليق للبوت.
    """
    if not context.user_data.get("awaiting_channel_forward"):
        return

    msg = update.message
    keyboard_cancel = [[InlineKeyboardButton("🔙 إلغاء والعودة", callback_data="main_menu")]]

    # 1. التحقق من أن الرسالة موجهة بالفعل من قناة
    forward_chat = msg.forward_from_chat
    if not forward_chat or forward_chat.type != "channel":
        await msg.reply_text(
            "⚠️ **الرسالة ليست موجهة من قناة!**\n\n"
            "يرجى القيام بـ **توجيه (Forward)** أي رسالة من القناة المطلوبة مباشرة إلى هنا.",
            reply_markup=InlineKeyboardMarkup(keyboard_cancel),
            parse_mode="Markdown"
        )
        return

    channel_id = forward_chat.id
    channel_title = forward_chat.title

    try:
        # 2. التحقق من أن البوت مشرف في القناة
        bot_member = await context.bot.get_chat_member(channel_id, context.bot.id)
        if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
            await msg.reply_text(
                f"❌ **البوت ليس مشرفًا في القناة:** ({channel_title})\n\n"
                "يرجى رفع البوت كـ **مشرف (Administrator)** داخل القناة أولاً مع صلاحية إضافة المشتركين/إنشاء الرابط، ثم قم بتوجيه الرسالة مرة أخرى.",
                reply_markup=InlineKeyboardMarkup(keyboard_cancel),
                parse_mode="Markdown"
            )
            return

        # 3. إنشاء رابط القناة (رابط عام إن وجد، أو رابط دعوة خاص إن كانت القناة خاصة)
        if forward_chat.username:
            invite_link = f"https://t.me/{forward_chat.username}"
        else:
            # توليد رابط دعوة صالح للقنوات الخاصة
            invite_link = await context.bot.export_chat_invite_link(channel_id)

        # 4. حفظ بيانات القناة المؤكدة
        context.bot_data["forced_channel_id"] = channel_id
        context.bot_data["forced_channel_title"] = channel_title
        context.bot_data["forced_channel_link"] = invite_link

        # إنهاء حالة الانتظار
        context.user_data["awaiting_channel_forward"] = False

        keyboard = [
            [InlineKeyboardButton("📢 معاينة زر الاشتراك", callback_data="preview_sub_button")],
            [InlineKeyboardButton("🔙 الصفحة الرئيسية", callback_data="main_menu")]
        ]

        await msg.reply_text(
            f"✅ **تم ربط القناة بنجاح!**\n\n"
            f"📌 **القناة:** {channel_title}\n"
            f"🆔 **المعرف الرقمي:** `{channel_id}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    except TelegramError as e:
        logger.error(f"خطأ أثناء التعامل مع القناة: {e}")
        await msg.reply_text(
            "❌ **تعذر إنشاء رابط القناة أو الحصول على الصلاحيات!**\n\n"
            "تأكد من إعطاء البوت جميع صلاحيات المشرف الكافية داخل القناة ثم أعد المحاولة.",
            reply_markup=InlineKeyboardMarkup(keyboard_cancel),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"خطأ غير متوقع: {e}")
        context.user_data["awaiting_channel_forward"] = False
        await msg.reply_text(
            "⚠️ حدث خطأ غير متوقع. يرجى إعادة المحاولة من القائمة الرئيسية عبر /start",
            reply_markup=InlineKeyboardMarkup(keyboard_cancel)
        )


# -------------------------------------------------------------------
# 4. بناء زر الاشتراك الاحترافي (Inline Button فقط)
# -------------------------------------------------------------------

async def send_forced_subscribe_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    تنشئ زر شفاف احترافي يحتوي على الرابط المباشر دون إظهار أي نص للرابط.
    """
    channel_link = context.bot_data.get("forced_channel_link", "https://t.me/")
    channel_title = context.bot_data.get("forced_channel_title", "القناة الرسمية")

    # زر مخصص ينقل المستخدم مباشرة للقناة بدون ظهور الرابط كنص
    keyboard = [
        [InlineKeyboardButton(f"📢 اشترك في القناة ({channel_title})", url=channel_link)],
        [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_subscription")]
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ **تنبيه: يجب عليك الاشتراك في القناة أولاً لاستخدام البوت.**\n\n"
             "اضغط على الزر أدناه للانضمام، ثم اضغط على **تحقق من الاشتراك**:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# -------------------------------------------------------------------
# 5. لوحة التحكم والأزرار التفاعلية
# -------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_channel_forward"] = False

    keyboard = [
        [InlineKeyboardButton("🎯 اختيار المجموعة المستهدفة", callback_data="select_target_group")],
        [InlineKeyboardButton("🔗 ضبط قناة الاشتراك الإجباري", callback_data="set_channel_forward")]
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
    query = update.callback_query
    await query.answer()

    admin_groups = await get_valid_admin_groups(context)

    if not admin_groups:
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
        await query.edit_message_text(
            text="⚠️ **لم يتم العثور على أي مجموعة.**\n\n"
                 "تأكد من إضافة البوت إلى المجموعة وتعيينه كـ **مشرف** أولاً.",
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
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "select_target_group":
        await show_target_groups(update, context)

    elif data == "set_channel_forward":
        # تفعيل انتظار توجيه الرسالة
        context.user_data["awaiting_channel_forward"] = True
        keyboard = [[InlineKeyboardButton("🔙 إلغاء", callback_data="main_menu")]]
        await query.edit_message_text(
            text="📢 **قم الآن بتوجيه (Forward) أي رسالة من القناة المطلوبة إلى هذا الشات مباشرة:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "preview_sub_button":
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
        context.user_data["awaiting_channel_forward"] = False
        await start(update, context)


# -------------------------------------------------------------------
# 6. تشغيل التطبيق
# -------------------------------------------------------------------

async def post_init(application: Application):
    create_task(start_web_server())

def main():
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    application.add_handler(ChatMemberHandler(track_bot_admin_groups, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # التقاط أي رسالة موجهة أو نصية معالجتها في الدالة المخصصة
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, process_channel_forward))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
