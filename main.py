import os
import logging
import asyncio
import json
import re
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
INITIAL_ADMINS = [1611988598, 7065061464]
DATA_FILE = "bot_data.json"

WAITING_STATES = {}
TEMP_BROADCAST = {}
RESCUE_TRACK = {}
PRIVATE_MSG_TRACK = {}
SUBSCRIPTION_THANKED = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

DEVELOPER_USERNAME = "@Nabil1r"

NON_ADMIN_INFO_TEXT = (
    "🤖 **مرحباً بك!**\n\n"
    "هذا البوت مُصمَّم ليعمل كـ**مشرف داخل مجموعات الدراسة**، ومهامه:\n\n"
    "• 🚫 حذف الإيموجيات غير المرغوب بها.\n"
    "• 🚫 حذف الكلمات والروابط الممنوعة.\n"
    "• 🚫 حذف الملصقات (الستيكرز) والصور المتحركة GIF الممنوعة.\n"
    "• 🔇 تفعيل وضع صامت في أوقات محددة، أو تلقائياً عند نداء استغاثة من الأعضاء.\n"
    "• 📢 نظام الاشتراك الإجباري في قناة/مجموعة معينة.\n"
    "• 🛡️ الحفاظ على الجو الدراسي الهادئ داخل المجموعة بشكل عام.\n\n"
    f"للحصول على هذه الميزات داخل مجموعتك، يرجى التواصل مع مطور البوت {DEVELOPER_USERNAME} "
    "كي يقوم بإضافتك كمشرف للبوت والسماح لك باستخدام ميزاته."
)

NON_ADMIN_SPAM_TEXT = (
    "⚠️ **تنبيه:**\n"
    "هذا البوت غير مبرمج لاستقبال الرسائل، ورسائلك هذه **لا تصل إلى المطور نهائياً**، "
    "لذلك لن تحصل هنا على أي رد رسمي.\n\n"
    f"في حال احتجت لأي خدمة أو أردت تفعيل البوت داخل مجموعتك، يرجى التواصل مباشرة مع المطور: {DEVELOPER_USERNAME}\n\n"
    "شكراً لتفهمك 🙏"
)


def default_data():
    return {
        "groups": {},
        "users": [],
        "admins": list(INITIAL_ADMINS),
        "emojis": ["😂", "🤣", "💩"],
        "words": [],
        "banned_links": [],
        "block_all_links": False,
        "block_stickers": False,
        "block_animated_stickers": False,
        "silent_mode": {
            "enabled": False,
            "start_time": "22:00",
            "end_time": "07:00",
            "until_timestamp": 0,
            "custom_message": "🔇 المجموعة الآن في الوضع الصامت. الكتابة مقتصرة على المشرفين فقط.",
            "target_group": None
        },
        "rescue_mode": {
            "enabled": False,
            "target_group": None,
            "keyword": "بوت مراقبة",
            "threshold": 3,
            "duration_minutes": 30,
            "message": "⚠️ نداء الاستغاثة يدل على وجود مخالفة أو نزاع داخل المجموعة، الرجاء الانتظار حتى وصول المشرفين."
        },
        "subscription_mode": {
            "enabled": False,
            "target_group": None,
            "channel_id": None,
            "channel_username": None,
            "join_message": (
                "⚠️ **عذراً يا {first_name}**\n\n"
                "يجب عليك الاشتراك في القناة أدناه لتتمكن من الكتابة في هذه المجموعة:\n"
                "{channel_link}\n\n"
                "🔘 اضغط على الزر ثم عد وأرسل رسالتك."
            ),
            "thank_you_message": (
                "✅ شكراً لك يا {first_name} على اشتراكك!\n"
                "يمكنك الآن التفاعل بحرية في المجموعة."
            )
        }
    }


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f2:
                data = json.load(f2)
                for admin in INITIAL_ADMINS:
                    if admin not in data.get("admins", []):
                        data.setdefault("admins", []).append(admin)
                data.setdefault("users", [])
                data.setdefault("groups", {})
                data.setdefault("emojis", ["😂", "🤣", "💩"])
                data.setdefault("words", [])
                data.setdefault("banned_links", [])
                data.setdefault("block_all_links", False)
                data.setdefault("block_stickers", False)
                data.setdefault("block_animated_stickers", False)
                data.setdefault("silent_mode", {})
                data["silent_mode"].setdefault("enabled", False)
                data["silent_mode"].setdefault("start_time", "22:00")
                data["silent_mode"].setdefault("end_time", "07:00")
                data["silent_mode"].setdefault("until_timestamp", 0)
                data["silent_mode"].setdefault(
                    "custom_message",
                    "🔇 المجموعة الآن في الوضع الصامت. الكتابة مقتصرة على المشرفين فقط."
                )
                data["silent_mode"].setdefault("target_group", None)
                data.setdefault("rescue_mode", {})
                data["rescue_mode"].setdefault("enabled", False)
                data["rescue_mode"].setdefault("target_group", None)
                data["rescue_mode"].setdefault("keyword", "بوت مراقبة")
                data["rescue_mode"].setdefault("threshold", 3)
                data["rescue_mode"].setdefault("duration_minutes", 30)
                data["rescue_mode"].setdefault(
                    "message",
                    "⚠️ نداء الاستغاثة يدل على وجود مخالفة أو نزاع داخل المجموعة، الرجاء الانتظار حتى وصول المشرفين."
                )
                data.setdefault("subscription_mode", {})
                data["subscription_mode"].setdefault("enabled", False)
                data["subscription_mode"].setdefault("target_group", None)
                data["subscription_mode"].setdefault("channel_id", None)
                data["subscription_mode"].setdefault("channel_username", None)
                data["subscription_mode"].setdefault(
                    "join_message",
                    "⚠️ **عذراً يا {first_name}**\n\n"
                    "يجب عليك الاشتراك في القناة أدناه لتتمكن من الكتابة في هذه المجموعة:\n"
                    "{channel_link}\n\n"
                    "🔘 اضغط على الزر ثم عد وأرسل رسالتك."
                )
                data["subscription_mode"].setdefault(
                    "thank_you_message",
                    "✅ شكراً لك يا {first_name} على اشتراكك!\n"
                    "يمكنك الآن التفاعل بحرية في المجموعة."
                )
                return data
        except Exception:
            pass
    return default_data()


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f2:
        json.dump(data, f2, ensure_ascii=False, indent=2)


def is_bot_admin(user_id):
    data = load_data()
    return user_id in data.get("admins", [])


async def is_group_admin(context, chat_id, user_id):
    if is_bot_admin(user_id):
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ["creator", "administrator"]
    except Exception:
        return False


def register_group(chat_id, title):
    data = load_data()
    chat_id_str = str(chat_id)
    if chat_id_str not in data["groups"] or data["groups"][chat_id_str] != title:
        data["groups"][chat_id_str] = title
        save_data(data)


def register_user(user_id):
    data = load_data()
    if user_id not in data.get("users", []):
        data.setdefault("users", []).append(user_id)
        save_data(data)


async def handle_ping(request):
    return web.Response(text="Bot is awake and running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


def parse_button_markup(text):
    keyboard = []
    lines = text.strip().split("\n")
    style_map = {"green": "success", "blue": "primary", "red": "danger"}
    for line in lines:
        row = []
        btn_parts = line.split("|")
        for part in btn_parts:
            part_str = part.strip()
            if not part_str:
                continue
            btn_style = None
            style_match = re.search(r"(?:-\s*style:|\[)(green|blue|red)(?:\])?", part_str, re.IGNORECASE)
            if style_match:
                color_name = style_match.group(1).lower()
                btn_style = style_map.get(color_name)
                part_str = re.sub(r"\s*-\s*style:(green|blue|red)|\s*\[(green|blue|red)\]", "", part_str, flags=re.IGNORECASE).strip()
            if "-" in part_str:
                sub_parts = part_str.split("-", 1)
                title = sub_parts[0].strip()
                url = sub_parts[1].strip()
                if url.startswith("http://") or url.startswith("https://"):
                    btn_kwargs = {"text": title, "url": url}
                    if btn_style:
                        btn_kwargs["style"] = btn_style
                    row.append(InlineKeyboardButton(**btn_kwargs))
        if row:
            keyboard.append(row)
    return InlineKeyboardMarkup(keyboard) if keyboard else None


def get_main_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 إذاعة عامة للمجموعات", callback_data="bc_all"),
         InlineKeyboardButton("🎯 إذاعة مخصصة لمجموعة", callback_data="bc_single_select")],
        [InlineKeyboardButton("👤 إذاعة للمستخدمين (خاص)", callback_data="bc_users")],
        [InlineKeyboardButton("🔇 إدارة الوضع الصامت", callback_data="manage_silent")],
        [InlineKeyboardButton("🆘 نداء الاستغاثة", callback_data="manage_rescue")],
        [InlineKeyboardButton("📢 الاشتراك الإجباري", callback_data="manage_subscription")],
        [InlineKeyboardButton("📖 دليل أوامر الإشراف", callback_data="show_cmd_help")],
        [InlineKeyboardButton("👤 إضافة مشرف جديد", callback_data="add_admin")],
        [InlineKeyboardButton("⛔ الكلمات المحظورة", callback_data="manage_words"),
         InlineKeyboardButton("😀 الإيموجيات المحظورة", callback_data="manage_emojis")],
        [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="manage_links"),
         InlineKeyboardButton("🎭 الملصقات المتحركة", callback_data="manage_stickers")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_silent_keyboard(data):
    silent_info = data.get("silent_mode", {})
    status = "🟢 مفعل" if silent_info.get("enabled") else "🔴 معطل"
    target = silent_info.get("target_group")
    target_name = data.get("groups", {}).get(str(target), "لم يتم التحديد ❌") if target else "لم يتم التحديد ❌"
    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status}", callback_data="toggle_silent")],
        [InlineKeyboardButton(f"🎯 المجموعة المستهدفة: {target_name}", callback_data="silent_select_target")],
        [InlineKeyboardButton("⏱️ وضع مدة مؤقتة جاهزة", callback_data="silent_durations")],
        [InlineKeyboardButton("⏰ ضبط توقيت يومي (من/إلى)", callback_data="set_silent_schedule")],
        [InlineKeyboardButton("✏️ تعديل الرسالة التوضيحية", callback_data="edit_silent_msg")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_durations_keyboard():
    keyboard = [
        [InlineKeyboardButton("5 دقائق", callback_data="dur_5"), InlineKeyboardButton("15 دقيقة", callback_data="dur_15"), InlineKeyboardButton("20 دقيقة", callback_data="dur_20")],
        [InlineKeyboardButton("25 دقيقة", callback_data="dur_25"), InlineKeyboardButton("30 دقيقة", callback_data="dur_30"), InlineKeyboardButton("35 دقيقة", callback_data="dur_35")],
        [InlineKeyboardButton("40 دقيقة", callback_data="dur_40"), InlineKeyboardButton("45 دقيقة", callback_data="dur_45"), InlineKeyboardButton("ساعة واحدة", callback_data="dur_60")],
        [InlineKeyboardButton("ساعة ونصف", callback_data="dur_90"), InlineKeyboardButton("ساعتان", callback_data="dur_120")],
        [InlineKeyboardButton("🔙 رجوع للوضع الصامت", callback_data="manage_silent")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_buttons_decision_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ إضافة أزرار روابط", callback_data="btn_add_yes")],
        [InlineKeyboardButton("🚀 إرسال بدون أزرار", callback_data="btn_add_no")],
        [InlineKeyboardButton("❌ إلغاء الإذاعة", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_back_keyboard(target="main_menu"):
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data=target)]]
    return InlineKeyboardMarkup(keyboard)


def get_groups_selection_keyboard(bot_data, callback_prefix, back_target="main_menu"):
    groups = bot_data.get("groups", {})
    keyboard = []
    for g_id, g_title in groups.items():
        keyboard.append([InlineKeyboardButton(f"👥 {g_title}", callback_data=f"{callback_prefix}{g_id}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_target)])
    return InlineKeyboardMarkup(keyboard)


def get_rescue_keyboard(data):
    r = data.get("rescue_mode", {})
    status = "🟢 مفعل" if r.get("enabled") else "🔴 معطل"
    target = r.get("target_group")
    target_name = data.get("groups", {}).get(str(target), "لم يتم التحديد ❌") if target else "لم يتم التحديد ❌"
    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status}", callback_data="rescue_toggle")],
        [InlineKeyboardButton(f"🎯 المجموعة المستهدفة: {target_name}", callback_data="rescue_select_target")],
        [InlineKeyboardButton(f"🗣️ كلمة النداء: {r.get('keyword')}", callback_data="rescue_set_keyword")],
        [InlineKeyboardButton(f"🔢 عدد النداءات: {r.get('threshold')}", callback_data="rescue_set_threshold")],
        [InlineKeyboardButton(f"⏱️ مدة الصمت: {r.get('duration_minutes')} دقيقة", callback_data="rescue_set_duration")],
        [InlineKeyboardButton("✏️ تعديل رسالة النداء", callback_data="rescue_set_message")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_subscription_keyboard(data):
    sub = data.get("subscription_mode", {})
    status = "🟢 مفعل" if sub.get("enabled") else "🔴 معطل"
    target = sub.get("target_group")
    target_name = data.get("groups", {}).get(str(target), "لم يتم التحديد ❌") if target else "لم يتم التحديد ❌"
    channel = sub.get("channel_username") or sub.get("channel_id") or "لم يتم التحديد ❌"
    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status}", callback_data="sub_toggle")],
        [InlineKeyboardButton(f"🎯 المجموعة المستهدفة: {target_name}", callback_data="sub_select_target_group")],
        [InlineKeyboardButton(f"📢 القناة المطلوبة: {channel}", callback_data="sub_set_channel")],
        [InlineKeyboardButton("✏️ تعديل رسالة الاشتراك", callback_data="sub_set_join_msg")],
        [InlineKeyboardButton("✏️ تعديل رسالة الشكر", callback_data="sub_set_thank_msg")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_links_keyboard(data):
    status = "🟢 مفعل" if data.get("block_all_links") else "🔴 معطل"
    keyboard = [
        [InlineKeyboardButton(f"حظر جميع الروابط: {status}", callback_data="toggle_block_all_links")],
        [InlineKeyboardButton("➕ إضافة رابط/نطاق محظور", callback_data="add_link"),
         InlineKeyboardButton("🗑️ مسح الكل", callback_data="clear_links")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_stickers_keyboard(data):
    status_all = "🟢 مفعل" if data.get("block_stickers") else "🔴 معطل"
    status_anim = "🟢 مفعل" if data.get("block_animated_stickers") else "🔴 معطل"
    keyboard = [
        [InlineKeyboardButton(f"حظر جميع الملصقات: {status_all}", callback_data="toggle_block_stickers")],
        [InlineKeyboardButton(f"حظر الملصقات المتحركة و GIF: {status_anim}", callback_data="toggle_block_animated")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def send_non_admin_response(update):
    user_id = update.effective_user.id
    count = PRIVATE_MSG_TRACK.get(user_id, 0) + 1
    PRIVATE_MSG_TRACK[user_id] = count
    if count <= 1:
        await update.message.reply_text(NON_ADMIN_INFO_TEXT, parse_mode="Markdown")
    else:
        await update.message.reply_text(NON_ADMIN_SPAM_TEXT, parse_mode="Markdown")


async def start_command(update, context):
    user_id = update.effective_user.id
    register_user(user_id)
    if is_bot_admin(user_id):
        WAITING_STATES[user_id] = None
        await update.message.reply_text(
            "أهلاً بك يا أدمن في لوحة التحكم الإدارية! 🛠️\nاختر من الأزرار أدناه للتحكم بالبوت:",
            reply_markup=get_main_admin_keyboard()
        )
    else:
        await send_non_admin_response(update)


async def button_click(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    action = query.data
    await query.answer()

    if action == "cancel_silent":
        chat_id = query.message.chat.id
        if not await is_group_admin(context, chat_id, user_id):
            await context.bot.answer_callback_query(query.id, "❌ هذا الزر مخصص لمشرفي المجموعة فقط.", show_alert=True)
            return
        bot_data = load_data()
        bot_data["silent_mode"]["until_timestamp"] = 0
        save_data(bot_data)
        try:
            await query.message.edit_text("✅ تم إلغاء الوضع الصامت، يمكن للأعضاء الكتابة الآن.")
        except Exception:
            await context.bot.send_message(chat_id, "✅ تم إلغاء الوضع الصامت، يمكن للأعضاء الكتابة الآن.")
        return

    if not is_bot_admin(user_id):
        await query.message.reply_text("❌ عذراً، هذه اللوحة مخصصة للمشرفين فقط.")
        return

    bot_data = load_data()

    if action == "main_menu":
        WAITING_STATES[user_id] = None
        TEMP_BROADCAST.pop(user_id, None)
        await query.message.edit_text("أهلاً بك في لوحة التحكم الإدارية! 🛠️", reply_markup=get_main_admin_keyboard())

    elif action == "show_cmd_help":
        help_text = (
            "📖 **دليل أوامر الإشراف السريعة للمجموعات:**\n\n"
            "يمكنك استخدام هذه الأوامر مباشرة داخل المجموعة (بالرد على الرسالة):\n\n"
            "• `/حظر` : حظر المستخدم نهائياً.\n"
            "• `/حظر 10m` أو `/حظر 2h` : حظر مؤقت.\n"
            "• `/كتم` أو `/كتم 30m` : كتم المستخدم.\n"
            "• `/الغاء_الحظر` : إزالة الحظر عن المستخدم بالرد عليه.\n"
            "• `/الغاء_الكتم` : السماح للمستخدم بالكتابة مجدداً بالرد عليه.\n\n"
            "🆘 **نداء الاستغاثة:** إذا كتب الأعضاء كلمة النداء المحددة عدداً من المرات المتتالية "
            "داخل المجموعة المستهدفة، يتم تفعيل الوضع الصامت تلقائياً.\n\n"
            "📢 **الاشتراك الإجباري:** يمكنك إجبار أعضاء مجموعة معينة على الاشتراك في قناة معينة "
            "قبل السماح لهم بالكتابة."
        )
        await query.message.edit_text(help_text, parse_mode="Markdown", reply_markup=get_back_keyboard())

    elif action == "manage_silent":
        s = bot_data.get("silent_mode", {})
        target = s.get("target_group")
        target_name = bot_data.get("groups", {}).get(str(target), "غير محددة ❌") if target else "غير محددة ❌"
        msg = (
            "🔇 **إعدادات الوضع الصامت:**\n\n"
            f"• المجموعة المستهدفة: {target_name}\n"
            f"• التفعيل التلقائي: {'تفعيل' if s.get('enabled') else 'تعطيل'}\n"
            f"• الجدول اليومي: من `{s.get('start_time')}` إلى `{s.get('end_time')}`\n"
            f"• رسالة التنبيه:\n_{s.get('custom_message')}_"
        )
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=get_silent_keyboard(bot_data))

    elif action == "silent_select_target":
        if not bot_data.get("groups"):
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة بعد.", reply_markup=get_back_keyboard("manage_silent"))
            return
        await query.message.edit_text(
            "🎯 **اختر المجموعة التي سيُطبَّق عليها الوضع الصامت:**",
            parse_mode="Markdown",
            reply_markup=get_groups_selection_keyboard(bot_data, "silent_target_", "manage_silent")
        )

    elif action.startswith("silent_target_"):
        target_id = action.replace("silent_target_", "")
        bot_data["silent_mode"]["target_group"] = target_id
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديد المجموعة المستهدفة للوضع الصامت بنجاح.", reply_markup=get_silent_keyboard(bot_data))

    elif action == "toggle_silent":
        if not bot_data["silent_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً قبل تفعيل الوضع الصامت.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return
        bot_data["silent_mode"]["enabled"] = not bot_data["silent_mode"].get("enabled", False)
        save_data(bot_data)
        await query.message.edit_text("تم تغيير حالة الوضع الصامت بنجاح!", reply_markup=get_silent_keyboard(bot_data))

    elif action == "silent_durations":
        if not bot_data["silent_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return
        await query.message.edit_text("⏱️ **اختر مدة الوضع الصامت المباشر:**", reply_markup=get_durations_keyboard())

    elif action.startswith("dur_"):
        target = bot_data["silent_mode"].get("target_group")
        if not target:
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return
        minutes = int(action.replace("dur_", ""))
        until_ts = int((datetime.now() + timedelta(minutes=minutes)).timestamp())
        bot_data["silent_mode"]["until_timestamp"] = until_ts
        bot_data["silent_mode"]["enabled"] = True
        save_data(bot_data)
        custom_msg = bot_data["silent_mode"].get("custom_message", "")
        try:
            await context.bot.send_message(
                chat_id=int(target),
                text=f"🔇 **تم تفعيل الوضع الصامت لمدة {minutes} دقيقة!**\n\n{custom_msg}",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        await query.message.edit_text(f"✅ تم تفعيل الوضع الصامت لمدة {minutes} دقيقة بنجاح!", reply_markup=get_silent_keyboard(bot_data))

    elif action == "set_silent_schedule":
        if not bot_data["silent_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return
        WAITING_STATES[user_id] = "set_schedule"
        await query.message.edit_text(
            "⏰ **أرسل وقت البدء والانتهاء للوضع الصامت بالشكل التالي:**\n\n"
            "`22:00-07:00`\n"
            "(مع الأخذ بالاعتبار نظام 24 ساعة)",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard("manage_silent")
        )

    elif action == "edit_silent_msg":
        WAITING_STATES[user_id] = "set_silent_msg"
        await query.message.edit_text("✏️ **أرسل الرسالة التوضيحية الجديدة للوضع الصامت:**", reply_markup=get_back_keyboard("manage_silent"))

    elif action == "manage_rescue":
        r = bot_data.get("rescue_mode", {})
        target = r.get("target_group")
        target_name = bot_data.get("groups", {}).get(str(target), "غير محددة ❌") if target else "غير محددة ❌"
        msg = (
            "🆘 **إعدادات نداء الاستغاثة:**\n\n"
            f"• المجموعة المستهدفة: {target_name}\n"
            f"• الحالة: {'مفعل' if r.get('enabled') else 'معطل'}\n"
            f"• كلمة النداء: `{r.get('keyword')}`\n"
            f"• عدد النداءات المطلوبة: {r.get('threshold')}\n"
            f"• مدة الصمت التلقائي: {r.get('duration_minutes')} دقيقة\n"
            f"• رسالة النداء:\n_{r.get('message')}_\n\n"
            "عند تكرار كلمة النداء من الأعضاء بالعدد المحدد، يتم تفعيل الوضع الصامت تلقائياً في المجموعة المستهدفة."
        )
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=get_rescue_keyboard(bot_data))

    elif action == "rescue_select_target":
        if not bot_data.get("groups"):
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة بعد.", reply_markup=get_back_keyboard("manage_rescue"))
            return
        await query.message.edit_text(
            "🎯 **اختر المجموعة التي سيعمل بها نداء الاستغاثة:**",
            parse_mode="Markdown",
            reply_markup=get_groups_selection_keyboard(bot_data, "rescue_target_", "manage_rescue")
        )

    elif action.startswith("rescue_target_"):
        target_id = action.replace("rescue_target_", "")
        bot_data["rescue_mode"]["target_group"] = target_id
        RESCUE_TRACK.pop(int(target_id), None)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديد المجموعة المستهدفة لنداء الاستغاثة بنجاح.", reply_markup=get_rescue_keyboard(bot_data))

    elif action == "rescue_toggle":
        if not bot_data["rescue_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً قبل التفعيل.",
                reply_markup=get_rescue_keyboard(bot_data)
            )
            return
        bot_data["rescue_mode"]["enabled"] = not bot_data["rescue_mode"].get("enabled", False)
        save_data(bot_data)
        await query.message.edit_text("تم تغيير حالة نداء الاستغاثة بنجاح!", reply_markup=get_rescue_keyboard(bot_data))

    elif action == "rescue_set_keyword":
        WAITING_STATES[user_id] = "rescue_set_keyword"
        await query.message.edit_text("🗣️ **أرسل كلمة أو عبارة النداء الجديدة:**", reply_markup=get_back_keyboard("manage_rescue"))

    elif action == "rescue_set_threshold":
        WAITING_STATES[user_id] = "rescue_set_threshold"
        await query.message.edit_text("🔢 **أرسل عدد مرات النداء المطلوبة لتفعيل الوضع الصامت (رقم):**", reply_markup=get_back_keyboard("manage_rescue"))

    elif action == "rescue_set_duration":
        WAITING_STATES[user_id] = "rescue_set_duration"
        await query.message.edit_text("⏱️ **أرسل مدة الوضع الصامت بالدقائق (رقم):**", reply_markup=get_back_keyboard("manage_rescue"))

    elif action == "rescue_set_message":
        WAITING_STATES[user_id] = "rescue_set_message"
        await query.message.edit_text("✏️ **أرسل رسالة النداء الجديدة التي تظهر عند تفعيل الوضع الصامت تلقائياً:**", reply_markup=get_back_keyboard("manage_rescue"))

    elif action == "manage_subscription":
        sub = bot_data.get("subscription_mode", {})
        target = sub.get("target_group")
        target_name = bot_data.get("groups", {}).get(str(target), "غير محددة ❌") if target else "غير محددة ❌"
        channel = sub.get("channel_username") or sub.get("channel_id") or "غير محددة ❌"
        msg = (
            "📢 **إعدادات الاشتراك الإجباري:**\n\n"
            f"• الحالة: {'🟢 مفعل' if sub.get('enabled') else '🔴 معطل'}\n"
            f"• المجموعة المستهدفة: {target_name}\n"
            f"• القناة/المجموعة المطلوبة: `{channel}`\n\n"
            f"• رسالة الاشتراك:\n_{sub.get('join_message')}_\n\n"
            f"• رسالة الشكر:\n_{sub.get('thank_you_message')}_\n\n"
            "💡 **تنبيه:** يجب أن يكون البوت **مشرفاً** في القناة المطلوبة ليستطيع التحقق من الاشتراكات."
        )
        await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=get_subscription_keyboard(bot_data))

    elif action == "sub_toggle":
        sub = bot_data.get("subscription_mode", {})
        if not sub.get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً.",
                reply_markup=get_subscription_keyboard(bot_data)
            )
            return
        if not (sub.get("channel_id") or sub.get("channel_username")):
            await query.message.edit_text(
                "❌ يجب تحديد القناة/المجموعة المطلوبة أولاً.",
                reply_markup=get_subscription_keyboard(bot_data)
            )
            return
        bot_data["subscription_mode"]["enabled"] = not sub.get("enabled", False)
        save_data(bot_data)
        await query.message.edit_text("تم تغيير حالة الاشتراك الإجباري بنجاح!", reply_markup=get_subscription_keyboard(bot_data))

    elif action == "sub_select_target_group":
        if not bot_data.get("groups"):
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة بعد.", reply_markup=get_back_keyboard("manage_subscription"))
            return
        await query.message.edit_text(
            "🎯 **اختر المجموعة التي سيتم تطبيق الاشتراك الإجباري عليها:**",
            parse_mode="Markdown",
            reply_markup=get_groups_selection_keyboard(bot_data, "sub_target_group_", "manage_subscription")
        )

    elif action.startswith("sub_target_group_"):
        target_id = action.replace("sub_target_group_", "")
        bot_data["subscription_mode"]["target_group"] = target_id
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديد المجموعة المستهدفة للاشتراك الإجباري بنجاح.", reply_markup=get_subscription_keyboard(bot_data))

    elif action == "sub_set_channel":
        WAITING_STATES[user_id] = "sub_set_channel"
        guide = (
            "📢 **أرسل معرف القناة أو المجموعة المطلوب الاشتراك فيها:**\n\n"
            "يمكنك إرسالها بأحد الصيغ التالية:\n"
            "• ID رقمي: `-1001234567890`\n"
            "• يوزرنيوم: `@channel_username`\n\n"
            "⚠️ **يجب أن يكون البوت مشرفاً في تلك القناة/المجموعة.**"
        )
        await query.message.edit_text(guide, parse_mode="Markdown", reply_markup=get_back_keyboard("manage_subscription"))

    elif action == "sub_set_join_msg":
        WAITING_STATES[user_id] = "sub_set_join_msg"
        guide = (
            "✏️ **أرسل رسالة الاشتراك الإجباري الجديدة:**\n\n"
            "يمكنك استخدام المتغيرات التالية:\n"
            "• `{first_name}` : اسم العضو\n"
            "• `{channel_link}` : رابط القناة/المجموعة المطلوبة\n\n"
            "مثال:\n"
            "`عذراً يا {first_name}، يجب الاشتراك في {channel_link}`"
        )
        await query.message.edit_text(guide, parse_mode="Markdown", reply_markup=get_back_keyboard("manage_subscription"))

    elif action == "sub_set_thank_msg":
        WAITING_STATES[user_id] = "sub_set_thank_msg"
        guide = (
            "✏️ **أرسل رسالة الشكر الجديدة:**\n\n"
            "يمكنك استخدام المتغير التالي:\n"
            "• `{first_name}` : اسم العضو\n\n"
            "مثال:\n"
            "`شكراً لك يا {first_name} على اشتراكك!`"
        )
        await query.message.edit_text(guide, parse_mode="Markdown", reply_markup=get_back_keyboard("manage_subscription"))

    elif action == "bc_all":
        WAITING_STATES[user_id] = "bc_msg_all"
        await query.message.edit_text("📝 **أرسل الآن منشور الإذاعة العامة للمجموعات:**", reply_markup=get_back_keyboard())

    elif action == "bc_users":
        WAITING_STATES[user_id] = "bc_msg_users"
        await query.message.edit_text("📝 **أرسل الآن منشور الإذاعة الخاص للمستخدمين:**", reply_markup=get_back_keyboard())

    elif action == "bc_single_select":
        groups = bot_data.get("groups", {})
        if not groups:
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة.", reply_markup=get_back_keyboard())
            return
        keyboard = [[InlineKeyboardButton(f"👥 {g_title}", callback_data=f"bc_to_{g_id}")] for g_id, g_title in groups.items()]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
        await query.message.edit_text("🎯 **اختر المجموعة المستهدفة:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action.startswith("bc_to_"):
        target_g_id = action.replace("bc_to_", "")
        WAITING_STATES[user_id] = f"bc_msg_single_{target_g_id}"
        await query.message.edit_text("📝 **أرسل منشور الإذاعة للمجموعة المحددة:**", reply_markup=get_back_keyboard())

    elif action == "btn_add_yes":
        WAITING_STATES[user_id] = "wait_for_btn_format"
        guide = (
            "✏️ **أرسل قائمة أزرار الروابط بهذا التنسيق (مع تحديد الألوان اختيارياً):**\n\n"
            "الزر 1 - http://example1.com - style:green\n"
            "الزر 2 - http://example2.com - style:blue\n"
            "الزر 3 - http://example3.com - style:red\n\n"
            "🎨 **الألوان المتاحة:**\n"
            "• `style:green` : لون أخضر 🟢\n"
            "• `style:blue` : لون أزرق 🔵\n"
            "• `style:red` : لون أحمر 🔴\n\n"
            "💡 يمكنك أيضاً وضع أكثر من زر في نفس السطر بوضع الرمز `|` بينهما."
        )
        await query.message.edit_text(guide, parse_mode="Markdown", reply_markup=get_back_keyboard())

    elif action == "btn_add_no":
        await execute_broadcast(context, user_id, query.message, None)

    elif action == "add_admin":
        WAITING_STATES[user_id] = "add_admin"
        await query.message.edit_text("👤 **أرسل ID المشرف الجديد:**", reply_markup=get_back_keyboard())

    elif action == "manage_words":
        words = ", ".join(bot_data.get("words", [])) or "لا توجد"
        keyboard = [
            [InlineKeyboardButton("➕ إضافة كلمة", callback_data="add_word"), InlineKeyboardButton("🗑️ مسح الكل", callback_data="clear_words")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.message.edit_text(f"⛔ **الكلمات المحظورة:**\n`{words}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "add_word":
        WAITING_STATES[user_id] = "add_word"
        await query.message.edit_text("✏️ **أرسل الكلمة المحظورة:**", reply_markup=get_back_keyboard("manage_words"))

    elif action == "clear_words":
        bot_data["words"] = []
        save_data(bot_data)
        await query.message.edit_text("✅ تم تفريغ الكلمات.", reply_markup=get_back_keyboard("manage_words"))

    elif action == "manage_emojis":
        emojis = " ".join(bot_data.get("emojis", [])) or "لا توجد"
        keyboard = [
            [InlineKeyboardButton("➕ إضافة إيموجي", callback_data="add_emoji"), InlineKeyboardButton("🗑️ مسح الكل", callback_data="clear_emojis")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.message.edit_text(f"😀 **الإيموجيات المحظورة:**\n{emojis}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "add_emoji":
        WAITING_STATES[user_id] = "add_emoji"
        await query.message.edit_text("✏️ **أرسل الإيموجي المحظور:**", reply_markup=get_back_keyboard("manage_emojis"))

    elif action == "clear_emojis":
        bot_data["emojis"] = []
        save_data(bot_data)
        await query.message.edit_text("✅ تم تفريغ الإيموجيات.", reply_markup=get_back_keyboard("manage_emojis"))

    elif action == "manage_links":
        links = ", ".join(bot_data.get("banned_links", [])) or "لا توجد"
        await query.message.edit_text(
            f"🔗 **إعدادات الروابط المحظورة:**\n\nالقائمة الحالية:\n`{links}`",
            parse_mode="Markdown",
            reply_markup=get_links_keyboard(bot_data)
        )

    elif action == "toggle_block_all_links":
        bot_data["block_all_links"] = not bot_data.get("block_all_links", False)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديث إعداد حظر جميع الروابط.", reply_markup=get_links_keyboard(bot_data))

    elif action == "add_link":
        WAITING_STATES[user_id] = "add_link"
        await query.message.edit_text("✏️ **أرسل الرابط أو النطاق المراد حظره (مثال: example.com):**", reply_markup=get_back_keyboard("manage_links"))

    elif action == "clear_links":
        bot_data["banned_links"] = []
        save_data(bot_data)
        await query.message.edit_text("✅ تم تفريغ قائمة الروابط المحظورة.", reply_markup=get_back_keyboard("manage_links"))

    elif action == "manage_stickers":
        await query.message.edit_text(
            "🎭 **إعدادات حظر الملصقات والصور المتحركة:**",
            parse_mode="Markdown",
            reply_markup=get_stickers_keyboard(bot_data)
        )

    elif action == "toggle_block_stickers":
        bot_data["block_stickers"] = not bot_data.get("block_stickers", False)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديث إعداد حظر الملصقات.", reply_markup=get_stickers_keyboard(bot_data))

    elif action == "toggle_block_animated":
        bot_data["block_animated_stickers"] = not bot_data.get("block_animated_stickers", False)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديث إعداد حظر الملصقات المتحركة و GIF.", reply_markup=get_stickers_keyboard(bot_data))


async def execute_broadcast(context, user_id, status_msg, reply_markup):
    data_bc = TEMP_BROADCAST.get(user_id)
    if not data_bc:
        await status_msg.edit_text("❌ حدث خطأ، أعد المحاولة.", reply_markup=get_back_keyboard())
        return
    bc_type = data_bc["type"]
    msg_to_copy = data_bc["msg"]
    bot_data = load_data()
    sent, failed = 0, 0
    await status_msg.edit_text("⏳ جاري الإذاعة...")
    targets = []
    if bc_type == "all":
        targets = list(bot_data.get("groups", {}).keys())
    elif bc_type == "users":
        targets = bot_data.get("users", [])
    elif bc_type.startswith("single_"):
        targets = [bc_type.replace("single_", "")]
    for target_id in targets:
        try:
            await msg_to_copy.copy(chat_id=int(target_id), reply_markup=reply_markup)
            sent += 1
            await asyncio.sleep(0.1)
        except Exception:
            failed += 1
    TEMP_BROADCAST.pop(user_id, None)
    WAITING_STATES[user_id] = None
    await status_msg.edit_text(f"✅ تمت الإذاعة بنجاح!\n- نجاح: {sent}\n- فشل: {failed}", reply_markup=get_back_keyboard())


async def handle_private_message(update, context):
    if not update.message:
        return
    user_id = update.message.from_user.id
    register_user(user_id)
    if not is_bot_admin(user_id):
        await send_non_admin_response(update)
        return
    state = WAITING_STATES.get(user_id)
    if not state:
        await update.message.reply_text("يرجى استخدام الأوامر عبر القائمة من /start", reply_markup=get_main_admin_keyboard())
        return
    bot_data = load_data()

    if state.startswith("bc_msg_"):
        bc_type = state.replace("bc_msg_", "")
        TEMP_BROADCAST[user_id] = {"type": bc_type, "msg": update.message}
        WAITING_STATES[user_id] = "ask_buttons"
        await update.message.reply_text("🔗 **هل ترغب بإضافة أزرار روابط للمنشور؟**", reply_markup=get_buttons_decision_keyboard())

    elif state == "wait_for_btn_format":
        btn_markup = parse_button_markup(update.message.text)
        if not btn_markup:
            await update.message.reply_text("❌ التنسيق غير صحيح! التأكد من إرسال رابط صحيح (https://...)\nحاول مرة أخرى:")
            return
        status_msg = await update.message.reply_text("⏳ جاري تحضير الإذاعة...")
        await execute_broadcast(context, user_id, status_msg, btn_markup)

    elif state == "set_schedule":
        WAITING_STATES[user_id] = None
        txt = update.message.text.strip()
        if re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", txt):
            start, end = txt.split("-")
            bot_data["silent_mode"]["start_time"] = start
            bot_data["silent_mode"]["end_time"] = end
            bot_data["silent_mode"]["enabled"] = True
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم ضبط جدول الوضع الصامت من `{start}` إلى `{end}`!", parse_mode="Markdown", reply_markup=get_back_keyboard("manage_silent"))
        else:
            await update.message.reply_text("❌ صيغة غير صحيحة، اكتبها مثل: `22:00-07:00`", parse_mode="Markdown", reply_markup=get_back_keyboard("manage_silent"))

    elif state == "set_silent_msg":
        WAITING_STATES[user_id] = None
        bot_data["silent_mode"]["custom_message"] = update.message.text
        save_data(bot_data)
        await update.message.reply_text("✅ تم حفظ الوصف الجديد للوضع الصامت!", reply_markup=get_back_keyboard("manage_silent"))

    elif state == "add_admin":
        WAITING_STATES[user_id] = None
        try:
            new_id = int(update.message.text.strip())
            if new_id not in bot_data["admins"]:
                bot_data["admins"].append(new_id)
                save_data(bot_data)
                await update.message.reply_text(f"✅ تم إضافة المشرف `{new_id}`!", parse_mode="Markdown", reply_markup=get_back_keyboard())
            else:
                await update.message.reply_text("⚠️ هذا المستخدم مشرف بالفعل.", reply_markup=get_back_keyboard())
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال أرقام الـ ID فقط.", reply_markup=get_back_keyboard())

    elif state == "add_word":
        WAITING_STATES[user_id] = None
        word = update.message.text.strip().lower()
        if word not in bot_data["words"]:
            bot_data["words"].append(word)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الكلمة `{word}`!", parse_mode="Markdown", reply_markup=get_back_keyboard("manage_words"))

    elif state == "add_emoji":
        WAITING_STATES[user_id] = None
        emoji = update.message.text.strip()
        if emoji not in bot_data["emojis"]:
            bot_data["emojis"].append(emoji)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الإيموجي {emoji}!", reply_markup=get_back_keyboard("manage_emojis"))

    elif state == "add_link":
        WAITING_STATES[user_id] = None
        link = update.message.text.strip().lower()
        if link not in bot_data["banned_links"]:
            bot_data["banned_links"].append(link)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الرابط `{link}` للقائمة المحظورة!", parse_mode="Markdown", reply_markup=get_back_keyboard("manage_links"))

    elif state == "rescue_set_keyword":
        WAITING_STATES[user_id] = None
        keyword = update.message.text.strip()
        bot_data["rescue_mode"]["keyword"] = keyword
        save_data(bot_data)
        await update.message.reply_text(f"✅ تم تحديث كلمة النداء إلى: `{keyword}`", parse_mode="Markdown", reply_markup=get_back_keyboard("manage_rescue"))

    elif state == "rescue_set_threshold":
        WAITING_STATES[user_id] = None
        try:
            threshold = int(update.message.text.strip())
            if threshold < 1:
                raise ValueError
            bot_data["rescue_mode"]["threshold"] = threshold
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم تحديث عدد النداءات إلى: {threshold}", reply_markup=get_back_keyboard("manage_rescue"))
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح أكبر من صفر.", reply_markup=get_back_keyboard("manage_rescue"))

    elif state == "rescue_set_duration":
        WAITING_STATES[user_id] = None
        try:
            duration = int(update.message.text.strip())
            if duration < 1:
                raise ValueError
            bot_data["rescue_mode"]["duration_minutes"] = duration
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم تحديث مدة الصمت التلقائي إلى: {duration} دقيقة", reply_markup=get_back_keyboard("manage_rescue"))
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح أكبر من صفر.", reply_markup=get_back_keyboard("manage_rescue"))

    elif state == "rescue_set_message":
        WAITING_STATES[user_id] = None
        bot_data["rescue_mode"]["message"] = update.message.text
        save_data(bot_data)
        await update.message.reply_text("✅ تم تحديث رسالة النداء!", reply_markup=get_back_keyboard("manage_rescue"))

    elif state == "sub_set_channel":
        WAITING_STATES[user_id] = None
        channel_input = update.message.text.strip()
        channel_id = None
        channel_username = None
        if channel_input.startswith("@"):
            channel_username = channel_input
        elif channel_input.startswith("-") and channel_input[1:].isdigit():
            channel_id = int(channel_input)
        elif channel_input.isdigit():
            channel_id = int("-100" + channel_input)
        else:
            await update.message.reply_text("❌ صيغة غير صحيحة. أرسل ID رقمي أو @username", reply_markup=get_back_keyboard("manage_subscription"))
            return
        try:
            check_id = channel_id if channel_id else channel_username
            await context.bot.get_chat(check_id)
        except Exception:
            await update.message.reply_text(
                "⚠️ لم أتمكن من الوصول إلى القناة/المجموعة.\n"
                "تأكد من:\n"
                "1. أن المعرف صحيح.\n"
                "2. أن البوت مشرف في تلك القناة/المجموعة.",
                reply_markup=get_back_keyboard("manage_subscription")
            )
            return
        bot_data["subscription_mode"]["channel_id"] = channel_id
        bot_data["subscription_mode"]["channel_username"] = channel_username
        save_data(bot_data)
        await update.message.reply_text(f"✅ تم تحديد القناة/المجموعة: `{channel_input}`", parse_mode="Markdown", reply_markup=get_back_keyboard("manage_subscription"))

    elif state == "sub_set_join_msg":
        WAITING_STATES[user_id] = None
        bot_data["subscription_mode"]["join_message"] = update.message.text
        save_data(bot_data)
        await update.message.reply_text("✅ تم تحديث رسالة الاشتراك الإجباري!", reply_markup=get_back_keyboard("manage_subscription"))

    elif state == "sub_set_thank_msg":
        WAITING_STATES[user_id] = None
        bot_data["subscription_mode"]["thank_you_message"] = update.message.text
        save_data(bot_data)
        await update.message.reply_text("✅ تم تحديث رسالة الشكر!", reply_markup=get_back_keyboard("manage_subscription"))


def parse_time(time_str):
    unit = time_str[-1].lower()
    value = int(time_str[:-1])
    if unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    return None


async def admin_actions_handler(update, context):
    if not update.message or update.effective_chat.type == "private":
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not await is_group_admin(context, chat_id, user_id):
        return
    text = update.message.text.strip()
    cmd = text.split()[0].lower()
    target_user_id = None
    target_user_name = "المستخدم"
    if update.message.reply_to_message:
        target_user_id = update.message.reply_to_message.from_user.id
        target_user_name = update.message.reply_to_message.from_user.first_name

    if cmd in ["/حظر", "/ban"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم للقيام بالحظر.")
            return
        duration = None
        if len(context.args) > 0 and not context.args[0].startswith("@"):
            try:
                duration = parse_time(context.args[0])
            except Exception:
                pass
        try:
            until_date = datetime.now() + duration if duration else None
            await context.bot.ban_chat_member(chat_id, target_user_id, until_date=until_date)
            dur_text = f" لمدة {context.args[0]}" if duration else " نهائياً"
            await update.message.reply_text(f"🚫 تم حظر {target_user_name}{dur_text}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الحظر: {e}")

    elif cmd in ["/كتم", "/mute"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم للقيام بالكتم.")
            return
        duration = None
        if len(context.args) > 0 and not context.args[0].startswith("@"):
            try:
                duration = parse_time(context.args[0])
            except Exception:
                pass
        try:
            until_date = datetime.now() + duration if duration else None
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.restrict_chat_member(chat_id, target_user_id, permissions=permissions, until_date=until_date)
            dur_text = f" لمدة {context.args[0]}" if duration else " نهائياً"
            await update.message.reply_text(f"🔇 تم كتم {target_user_name}{dur_text}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الكتم: {e}")

    elif cmd in ["/الغاء_الحظر", "/unban"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم لإلغاء الحظر.")
            return
        try:
            await context.bot.unban_chat_member(chat_id, target_user_id)
            await update.message.reply_text(f"✅ تم إلغاء حظر {target_user_name}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إلغاء الحظر: {e}")

    elif cmd in ["/الغاء_الكتم", "/unmute"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم لإلغاء الكتم.")
            return
        try:
            permissions = ChatPermissions(can_send_messages=True, can_send_other_messages=True)
            await context.bot.restrict_chat_member(chat_id, target_user_id, permissions=permissions)
            await update.message.reply_text(f"🔊 تم إلغاء كتم {target_user_name}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إلغاء الكتم: {e}")


def is_silent_active(bot_data, chat_id):
    s = bot_data.get("silent_mode", {})
    if not s.get("enabled"):
        return False
    target = s.get("target_group")
    if not target or str(target) != str(chat_id):
        return False
    now = datetime.now()
    until_ts = s.get("until_timestamp", 0)
    if until_ts > 0:
        if now.timestamp() < until_ts:
            return True
        else:
            bot_data["silent_mode"]["until_timestamp"] = 0
            save_data(bot_data)
    start_str = s.get("start_time", "22:00")
    end_str = s.get("end_time", "07:00")
    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        start_time = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end_time = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if start_time < end_time:
            return start_time <= now <= end_time
        else:
            return now >= start_time or now <= end_time
    except Exception:
        return False


async def delete_and_warn(msg, context, chat_id, first_name, reason):
    try:
        await msg.delete()
        warning_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"عذراً يا {first_name}، يمنع استخدام ({reason}) في المجموعة!"
        )
        await asyncio.sleep(5)
        await warning_msg.delete()
    except Exception:
        pass


async def trigger_rescue_silent(context, chat_id, bot_data, rescue):
    duration = rescue.get("duration_minutes", 30)
    until_ts = int((datetime.now() + timedelta(minutes=duration)).timestamp())
    bot_data["silent_mode"]["until_timestamp"] = until_ts
    bot_data["silent_mode"]["enabled"] = True
    bot_data["silent_mode"]["target_group"] = str(chat_id)
    save_data(bot_data)
    RESCUE_TRACK[chat_id] = {"count": 0, "callers": []}
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء الوضع الصامت", callback_data="cancel_silent")]])
    threshold = rescue.get("threshold", 3)
    text = (
        f"🚨 **تم تفعيل الوضع الصامت تلقائياً** بسبب تكرار نداء الاستغاثة ({threshold}/{threshold})!\n\n"
        f"{rescue.get('message', '')}\n\n"
        "يمكن لمشرفي المجموعة إلغاء الوضع الصامت بالضغط على الزر أدناه."
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        pass


async def handle_rescue_keyword(update, context, chat_id, bot_data, text):
    rescue = bot_data.get("rescue_mode", {})
    target = rescue.get("target_group")
    if not rescue.get("enabled") or not target or str(target) != str(chat_id):
        return False
    keyword = (rescue.get("keyword") or "").strip()
    if not keyword or keyword not in text:
        return False
    threshold = rescue.get("threshold", 3)
    track = RESCUE_TRACK.setdefault(chat_id, {"count": 0, "callers": []})
    track["count"] += 1
    caller_name = update.effective_user.first_name if update.effective_user else "عضو"
    track["callers"].append(caller_name)
    count = track["count"]
    if count >= threshold:
        await trigger_rescue_silent(context, chat_id, bot_data, rescue)
    elif count == 1:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🆘 تم رصد نداء استغاثة ({count}/{threshold})\nفي حال تكرر النداء سيتم تفعيل الوضع الصامت تلقائياً."
        )
    else:
        callers_text = "، ".join(track["callers"])
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ تحذير: نداء استغاثة ({count}/{threshold})\n"
                f"في حال كان النداء بدون سبب حقيقي، سيتم حظر من قام بالنداء عند تفعيل الوضع الصامت: {callers_text}"
            )
        )
    return True


async def check_subscription(context, user_id, channel_id, channel_username):
    """التحقق مما إذا كان المستخدم مشتركاً في القناة/المجموعة المطلوبة."""
    try:
        check_target = channel_id if channel_id else channel_username
        member = await context.bot.get_chat_member(check_target, user_id)
        return member.status not in ["left", "kicked"]
    except Exception:
        return False


async def group_filter(update, context):
    if not update.message or not update.effective_chat:
        return
    msg = update.message
    chat_id = update.effective_chat.id

    if msg.is_automatic_forward or (msg.sender_chat and msg.sender_chat.id == chat_id):
        return

    user_id = update.effective_user.id if update.effective_user else None
    register_group(chat_id, update.effective_chat.title or "مجموعة")
    bot_data = load_data()

    user_is_admin = False
    if user_id:
        user_is_admin = await is_group_admin(context, chat_id, user_id)

    if is_silent_active(bot_data, chat_id):
        if user_id and not user_is_admin:
            try:
                await msg.delete()
                custom_msg = bot_data["silent_mode"].get("custom_message", "🔇 الوضع الصامت مفعل حالياً!")
                first_name = update.effective_user.first_name if update.effective_user else "العضو"
                warn = await context.bot.send_message(chat_id=chat_id, text=f"عذراً يا {first_name}:\n{custom_msg}")
                await asyncio.sleep(5)
                await warn.delete()
            except Exception:
                pass
            return

    # ========== الاشتراك الإجباري ==========
    sub = bot_data.get("subscription_mode", {})
    if sub.get("enabled") and not user_is_admin:
        sub_target = sub.get("target_group")
        if sub_target and str(sub_target) == str(chat_id):
            channel_id = sub.get("channel_id")
            channel_username = sub.get("channel_username")
            if channel_id or channel_username:
                is_subscribed = await check_subscription(context, user_id, channel_id, channel_username)
                if not is_subscribed:
                    try:
                        await msg.delete()
                        first_name = update.effective_user.first_name if update.effective_user else "العضو"
                        if channel_username:
                            channel_link = f"https://t.me/{channel_username.lstrip('@')}"
                        elif channel_id:
                            channel_link = f"https://t.me/c/{str(channel_id).replace('-100', '')}"
                        else:
                            channel_link = "#"
                        join_msg = sub.get("join_message", "").format(first_name=first_name, channel_link=channel_link)
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔘 اضغط هنا للاشتراك", url=channel_link)]])
                        warn = await context.bot.send_message(chat_id=chat_id, text=join_msg, parse_mode="Markdown", reply_markup=kb)
                        await asyncio.sleep(8)
                        await warn.delete()
                    except Exception:
                        pass
                    return
                else:
                    thank_key = (chat_id, user_id)
                    if not SUBSCRIPTION_THANKED.get(thank_key, False):
                        SUBSCRIPTION_THANKED[thank_key] = True
                        try:
                            first_name = update.effective_user.first_name if update.effective_user else "العضو"
                            thank_msg = sub.get("thank_you_message", "").format(first_name=first_name)
                            thank = await context.bot.send_message(chat_id=chat_id, text=thank_msg, parse_mode="Markdown")
                            await asyncio.sleep(25)
                            await thank.delete()
                        except Exception:
                            pass

    if user_is_admin:
        return

    first_name = update.effective_user.first_name if update.effective_user else "المستخدم"

    if msg.sticker:
        is_animated_or_video = bool(msg.sticker.is_animated or msg.sticker.is_video)
        if bot_data.get("block_animated_stickers") and is_animated_or_video:
            await delete_and_warn(msg, context, chat_id, first_name, "ملصق متحرك")
            return
        if bot_data.get("block_stickers"):
            await delete_and_warn(msg, context, chat_id, first_name, "ملصقات (ستيكرز)")
            return

    if msg.animation and bot_data.get("block_animated_stickers"):
        await delete_and_warn(msg, context, chat_id, first_name, "صورة متحركة GIF")
        return

    text = msg.text or msg.caption
    if not text:
        return

    handled = await handle_rescue_keyword(update, context, chat_id, bot_data, text)
    if handled:
        return

    text_lower = text.lower()
    forbidden_emojis = bot_data.get("emojis", [])
    forbidden_words = bot_data.get("words", [])
    forbidden_links = bot_data.get("banned_links", [])
    block_all_links = bot_data.get("block_all_links", False)

    is_violating = False
    reason = ""

    for emoji in forbidden_emojis:
        if emoji in text:
            is_violating = True
            reason = "إيموجي ممنوع"
            break

    if not is_violating:
        for word in forbidden_words:
            if word in text_lower:
                is_violating = True
                reason = "كلمة محظورة"
                break

    url_regex = re.compile(r"(https?://\S+|t\.me/\S+|www\.\S+)", re.IGNORECASE)
    has_link = bool(url_regex.search(text))

    if not is_violating and block_all_links and has_link:
        is_violating = True
        reason = "رابط ممنوع"

    if not is_violating:
        for link in forbidden_links:
            if link and link in text_lower:
                is_violating = True
                reason = "رابط محظور"
                break

    if is_violating:
        await delete_and_warn(msg, context, chat_id, first_name, reason)


async def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return

    await start_web_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), handle_private_message))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.Regex(r"^/(حظر|كتم|الغاء_الحظر|الغاء_الكتم|ban|mute|unban|unmute)"), admin_actions_handler))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_filter))

    print("البوت يعمل بنجاح...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
