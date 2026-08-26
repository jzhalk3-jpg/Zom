import sqlite3
import asyncio
import re
import logging
from datetime import datetime, timedelta
from telethon import TelegramClient, functions, errors
from telethon.sessions import StringSession
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# --- الإعدادات الأساسية ---
BOT_TOKEN = "8969957914:AAF33nKExvFFry5ImvGirDU4oYraLMX3tHc"
API_ID = 39289901
API_HASH = "a5dcef068387dd95705046f910d6cd48"

ADMIN_ID = 5064913080

BOT_PAUSED = False

logging.basicConfig(level=logging.INFO)

db = sqlite3.connect("bot_final.db", check_same_thread=False)
cursor = db.cursor()

# --- جداول قاعدة البيانات ---
cursor.execute("""
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, 
    session TEXT, 
    phone TEXT,
    is_active INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, 
    delay INTEGER DEFAULT 10,
    rest_time INTEGER DEFAULT 5,
    balance INTEGER DEFAULT 0,
    multi_join_permission INTEGER DEFAULT 0,
    is_banned INTEGER DEFAULT 0,
    is_exempted INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    folder_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT, 
    user_id INTEGER, 
    folder_id INTEGER,
    link TEXT, 
    status TEXT DEFAULT 'pending',
    FOREIGN KEY(folder_id) REFERENCES folders(id)
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS multi_join_progress (
    user_id INTEGER,
    account_id INTEGER,
    folder_id INTEGER,
    last_link_id INTEGER,
    PRIMARY KEY (user_id, account_id, folder_id)
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS charge_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    package TEXT,
    amount INTEGER,
    price INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    photo_file_id TEXT
)
""")
# جدول الباقات
cursor.execute("""
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    points INTEGER,
    price INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
# جدول إعدادات البوت
cursor.execute("""
CREATE TABLE IF NOT EXISTS bot_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT
)
""")
db.commit()

# ترقية الجداول القديمة
try:
    cursor.execute("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass
try:
    cursor.execute("ALTER TABLE users ADD COLUMN multi_join_permission INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass
try:
    cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass
try:
    cursor.execute("ALTER TABLE users ADD COLUMN is_exempted INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass

# ========== إعدادات البوت الافتراضية ==========
DEFAULT_SETTINGS = {
    "welcome_text": "🙋‍♂️ أهلاً بك يا {name} في بوت الانضمام التلقائي!{paused_msg}\n\n💳 معرفك: `{user_id}`\n🎯 رصيدك: {balance_display}\n\n📋 تكلفة الرابط = 1 نقطة.\nاختر من الأزرار:",
    "join_button": "🚀 بدء الانضمام",
    "stop_button": "🛑 إيقاف الانضمام",
    "folders_button": "📁 مجلدات الروابط",
    "charge_button": "💳 شحن حسابي",
    "system_status_text": "📋 **حالة النظام**\n\n• الحالة: {is_running}\n• الرقم النشط: {active_phone}\n• عدد الحسابات المسجلة: {total_accounts}\n• صلاحية الانضمام المتعدد: {multi_perm}\n• التوقف الجماعي: {pause_status}\n• مستثنى من التوقف العام: {exempt_status}\n• الوقت بين الروابط: {delay} ثانية\n• استراحة كل 5 روابط: {rest} دقائق\n• المجلد المختار: {folder_name}\n• رصيدك: {bal_str}",
    "charge_info_text": "💳 **اختر الباقة المناسبة:**",
    "admin_join_button": "🔀 انضمام متعدد",
    "admin_stop_multi_button": "🛑 إيقاف الانضمام المتعدد",
    "admin_packages_button": "📦 إدارة الباقات",
    "admin_settings_button": "⚙️ إعدادات البوت"
}

def init_settings():
    for key, value in DEFAULT_SETTINGS.items():
        cursor.execute("INSERT OR IGNORE INTO bot_settings (setting_key, setting_value) VALUES (?, ?)", (key, value))
    db.commit()

init_settings()

def get_setting(key, default=None):
    cursor.execute("SELECT setting_value FROM bot_settings WHERE setting_key=?", (key,))
    row = cursor.fetchone()
    if row:
        return row[0]
    return default or DEFAULT_SETTINGS.get(key, "")

def set_setting(key, value):
    cursor.execute("REPLACE INTO bot_settings (setting_key, setting_value) VALUES (?, ?)", (key, value))
    db.commit()

def reset_settings():
    for key, value in DEFAULT_SETTINGS.items():
        set_setting(key, value)

# ========== دوال الباقات ==========
def get_packages():
    cursor.execute("SELECT id, points, price FROM packages ORDER BY points ASC")
    return cursor.fetchall()

def add_package(points, price):
    cursor.execute("INSERT INTO packages (points, price) VALUES (?, ?)", (points, price))
    db.commit()

def delete_package(package_id):
    cursor.execute("DELETE FROM packages WHERE id=?", (package_id,))
    db.commit()

# ========== بقية المتغيرات والدوال المساعدة (نفس الكود السابق) ==========
running_states = {}
multi_running_states = {}
global_pause = {}
user_charge_state = {}
admin_package_state = {}
admin_settings_state = {}

PAUSE_DURATION_SECONDS = 300

def extract_links(text):
    pattern = r"(?:https?://)?(?:t\.me/|telegram\.me/)([a-zA-Z0-9_]+|joinchat/[a-zA-Z0-9_-]+|\+[a-zA-Z0-9_-]+)"
    return re.findall(pattern, text)

# دوال المجلدات (بدون تغيير)
def create_folder(user_id):
    cursor.execute("SELECT COUNT(*) FROM folders WHERE user_id=?", (user_id,))
    count = cursor.fetchone()[0] + 1
    folder_name = f"المجلد {count}"
    cursor.execute("INSERT INTO folders (user_id, folder_name) VALUES (?, ?)", (user_id, folder_name))
    db.commit()
    return cursor.lastrowid

def get_user_folders(user_id):
    cursor.execute("SELECT id, folder_name FROM folders WHERE user_id=? ORDER BY created_at", (user_id,))
    return cursor.fetchall()

def get_folder_links(folder_id):
    cursor.execute("SELECT id, link, status FROM links WHERE folder_id=? AND status='pending'", (folder_id,))
    return cursor.fetchall()

def delete_folder_and_links(folder_id):
    cursor.execute("DELETE FROM links WHERE folder_id=?", (folder_id,))
    cursor.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    db.commit()

def has_multi_join_permission(user_id):
    if user_id == ADMIN_ID:
        return True
    cursor.execute("SELECT multi_join_permission FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] == 1:
        return True
    return False

def grant_multi_join_permission(user_id):
    cursor.execute("UPDATE users SET multi_join_permission=1 WHERE user_id=?", (user_id,))
    db.commit()

def revoke_multi_join_permission(user_id):
    cursor.execute("UPDATE users SET multi_join_permission=0 WHERE user_id=?", (user_id,))
    db.commit()

def get_last_link_id(user_id, account_id, folder_id):
    cursor.execute("SELECT last_link_id FROM multi_join_progress WHERE user_id=? AND account_id=? AND folder_id=?", (user_id, account_id, folder_id))
    row = cursor.fetchone()
    return row[0] if row else None

def update_progress(user_id, account_id, folder_id, last_link_id):
    cursor.execute("""
        INSERT OR REPLACE INTO multi_join_progress (user_id, account_id, folder_id, last_link_id)
        VALUES (?, ?, ?, ?)
    """, (user_id, account_id, folder_id, last_link_id))
    db.commit()

def clear_progress(user_id, folder_id):
    cursor.execute("DELETE FROM multi_join_progress WHERE user_id=? AND folder_id=?", (user_id, folder_id))
    db.commit()

# دوال الحظر
def is_user_banned(user_id):
    if user_id == ADMIN_ID:
        return False
    cursor.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] == 1:
        return True
    return False

def ban_user(user_id):
    cursor.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,))
    db.commit()

def unban_user(user_id):
    cursor.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
    db.commit()

def is_user_exempted(user_id):
    if user_id == ADMIN_ID:
        return True
    cursor.execute("SELECT is_exempted FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] == 1:
        return True
    return False

def set_user_exempted(user_id, value):
    cursor.execute("UPDATE users SET is_exempted=? WHERE user_id=?", (1 if value else 0, user_id))
    db.commit()

# دوال الشحن
def save_charge_request(user_id, package, points, price, photo_file_id=None):
    cursor.execute("""
        INSERT INTO charge_requests (user_id, package, amount, price, photo_file_id)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, package, points, price, photo_file_id))
    db.commit()
    return cursor.lastrowid

def update_charge_request_status(request_id, status):
    cursor.execute("UPDATE charge_requests SET status=? WHERE id=?", (status, request_id))
    db.commit()

def get_charge_request(request_id):
    cursor.execute("SELECT user_id, amount FROM charge_requests WHERE id=?", (request_id,))
    return cursor.fetchone()

def add_balance(user_id, amount):
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    db.commit()

# ========== منطق الانضمام (مع 5 دقائق ثابتة) ==========
async def join_logic_with_global_pause(session_str, link, user_id, account_index, context):
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    try:
        await client.connect()
        if global_pause.get(user_id, {}).get("paused", False):
            until = global_pause[user_id].get("until")
            if until and until > datetime.now():
                wait_seconds = (until - datetime.now()).total_seconds() + 5
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏳ توقف مؤقت بسبب تعليق في تيليجرام (5 دقائق)، انتظار {int(wait_seconds)} ثانية..."
                )
                await asyncio.sleep(wait_seconds)
                global_pause[user_id]["paused"] = False
                global_pause[user_id]["until"] = None
                return None, None

        if "joinchat" in link or "+" in link:
            hash_val = link.split("/")[-1].replace("+", "").strip()
            try:
                await client(functions.messages.ImportChatInviteRequest(hash=hash_val))
                return "SUCCESS", "✅ تم الانضمام بنجاح (رابط خاص)"
            except errors.FloodWaitError as e:
                wait_seconds = PAUSE_DURATION_SECONDS
                global_pause[user_id] = {
                    "paused": True,
                    "until": datetime.now() + timedelta(seconds=wait_seconds),
                    "reason": f"FloodWait (توقف 5 دقائق)"
                }
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⛔ تم تعليق جميع الحسابات بسبب طلب انتظار من تيليجرام (توقف {PAUSE_DURATION_SECONDS//60} دقائق). سيتم الاستئناف تلقائياً بعد انتهاء المدة."
                )
                return None, None
            except Exception as e:
                err_str = str(e).lower()
                if "flood" in err_str or "wait" in err_str or "seconds" in err_str:
                    wait_seconds = PAUSE_DURATION_SECONDS
                    global_pause[user_id] = {
                        "paused": True,
                        "until": datetime.now() + timedelta(seconds=wait_seconds),
                        "reason": f"تعليق (توقف {PAUSE_DURATION_SECONDS//60} دقائق)"
                    }
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⛔ تم تعليق جميع الحسابات بسبب تعليق (توقف {PAUSE_DURATION_SECONDS//60} دقائق). سيتم الاستئناف تلقائياً."
                    )
                    return None, None
                if "request" in err_str or "ordered to wait" in err_str:
                    try:
                        await client(functions.messages.CheckChatInviteRequest(hash=hash_val))
                        return "SUCCESS", "⏳ تم إرسال طلب الانضمام"
                    except Exception as inner_e:
                        inner_str = str(inner_e).lower()
                        if "alreadyinchannel" in inner_str or "user_already_participant" in inner_str:
                            return "SUCCESS", "🟢 أنت عضو بالفعل."
                        raise inner_e
                raise e
        else:
            clean_link = link.split("/")[-1].strip()
            try:
                await client(functions.channels.JoinChannelRequest(clean_link))
                return "SUCCESS", "✅ تم الانضمام (رابط عام)"
            except errors.FloodWaitError as e:
                wait_seconds = PAUSE_DURATION_SECONDS
                global_pause[user_id] = {
                    "paused": True,
                    "until": datetime.now() + timedelta(seconds=wait_seconds),
                    "reason": f"FloodWait (توقف {PAUSE_DURATION_SECONDS//60} دقائق)"
                }
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⛔ تم تعليق جميع الحسابات بسبب طلب انتظار من تيليجرام (توقف {PAUSE_DURATION_SECONDS//60} دقائق). سيتم الاستئناف تلقائياً."
                )
                return None, None
            except Exception as e:
                err_str = str(e).lower()
                if "flood" in err_str or "wait" in err_str or "seconds" in err_str:
                    wait_seconds = PAUSE_DURATION_SECONDS
                    global_pause[user_id] = {
                        "paused": True,
                        "until": datetime.now() + timedelta(seconds=wait_seconds),
                        "reason": f"تعليق (توقف {PAUSE_DURATION_SECONDS//60} دقائق)"
                    }
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⛔ تم تعليق جميع الحسابات بسبب تعليق (توقف {PAUSE_DURATION_SECONDS//60} دقائق). سيتم الاستئناف تلقائياً."
                    )
                    return None, None
                if "requested to join" in err_str or "user_already_participant" in err_str:
                    return "SUCCESS", "⏳ طلب انضمام مرسل"
                try:
                    channel = await client.get_entity(clean_link)
                    await client(functions.channels.JoinChannelRequest(channel=channel))
                    return "SUCCESS", "✅ تم الانضمام"
                except Exception as inner_e:
                    inner_err_str = str(inner_e).lower()
                    if "flood" in inner_err_str or "wait" in inner_err_str or "seconds" in inner_err_str:
                        wait_seconds = PAUSE_DURATION_SECONDS
                        global_pause[user_id] = {
                            "paused": True,
                            "until": datetime.now() + timedelta(seconds=wait_seconds),
                            "reason": f"تعليق (توقف {PAUSE_DURATION_SECONDS//60} دقائق)"
                        }
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"⛔ تم تعليق جميع الحسابات بسبب تعليق (توقف {PAUSE_DURATION_SECONDS//60} دقائق). سيتم الاستئناف تلقائياً."
                        )
                        return None, None
                    if "requested to join" in inner_err_str:
                        return "SUCCESS", "⏳ طلب انضمام مرسل"
                    if "alreadyinchannel" in inner_err_str or "user_already_participant" in inner_err_str:
                        return "SUCCESS", "🟢 عضو بالفعل"
                    raise e
    except Exception as e:
        err_str = str(e).lower()
        if "alreadyinchannel" in err_str or "user_already_participant" in err_str:
            return "SUCCESS", "🟢 عضو بالفعل"
        if "channelstoomuch" in err_str:
            return "FAILED", "❌ الحساب ممتلئ قنوات!"
        return "FAILED", f"❌ فشل: {str(e)}"
    finally:
        await client.disconnect()

# دوال الانضمام الخلفية (معدلة لدعم الروابط القابلة للضغط)
async def background_join_task(user_id, context, active_acc, delay_time, rest_time_minutes, folder_id, folder_name):
    try:
        join_counter = 0
        local_db = sqlite3.connect("bot_final.db")
        local_cursor = local_db.cursor()

        local_cursor.execute("SELECT id, link FROM links WHERE folder_id=? AND status='pending'", (folder_id,))
        links = local_cursor.fetchall()
        if not links:
            await context.bot.send_message(chat_id=user_id, text="⚠️ لا توجد روابط معلقة في هذا المجلد.")
            return

        for lid, link in links:
            if not running_states.get(user_id):
                break

            if user_id != ADMIN_ID:
                local_cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                current_bal = local_cursor.fetchone()[0]
                if current_bal < 1:
                    await context.bot.send_message(chat_id=user_id, text="⚠️ نفدت نقاطك، يرجى شحنها.")
                    break

            if join_counter > 0 and join_counter % 5 == 0:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏳ استراحة لمدة {rest_time_minutes} دقائق بعد 5 روابط..."
                )
                for _ in range(int(rest_time_minutes * 60 * 10)):
                    if not running_states.get(user_id):
                        break
                    await asyncio.sleep(0.1)
                if not running_states.get(user_id):
                    break
                await context.bot.send_message(chat_id=user_id, text="🚀 استئناف العمل...")

            while True:
                if not running_states.get(user_id):
                    break

                status, msg = await join_logic_with_global_pause(active_acc[0], link, user_id, 0, context)
                if status is None and msg is None:
                    continue

                local_cursor.execute("UPDATE links SET status=? WHERE id=?", ('completed' if status == "SUCCESS" else 'failed', lid))
                if user_id != ADMIN_ID:
                    local_cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id=?", (user_id,))
                local_db.commit()

                join_counter += 1

                local_cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                rem_bal = local_cursor.fetchone()[0]
                bal_str = "المشرف (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{rem_bal} نقطة"

                formatted_link = f"[{link}](https://t.me/{link})" if not link.startswith("http") else link
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📱 الرقم: {active_acc[1]}\n🔗 الرابط: {formatted_link}\nالنتيجة: {msg}\n🎯 نقاطك: {bal_str}",
                    parse_mode="Markdown"
                )
                break

            for _ in range(int(delay_time * 10)):
                if not running_states.get(user_id):
                    break
                await asyncio.sleep(0.1)

        if not running_states.get(user_id):
            await context.bot.send_message(chat_id=user_id, text="🛑 تم الإيقاف.")
        else:
            await context.bot.send_message(chat_id=user_id, text="🏁 انتهت معالجة المجلد بنجاح.")

        local_db.close()
    except Exception as e:
        logging.error(f"Error in background task: {e}")
    finally:
        running_states[user_id] = False

async def multi_join_task(user_id, context, account_data, folder_id, folder_name, delay_time, rest_time_minutes, account_index, stop_flag):
    session_str, phone, account_id = account_data
    try:
        join_counter = 0
        local_db = sqlite3.connect("bot_final.db")
        local_cursor = local_db.cursor()

        last_link_id = get_last_link_id(user_id, account_id, folder_id)
        if last_link_id:
            local_cursor.execute("""
                SELECT id, link FROM links 
                WHERE folder_id=? AND status='pending' AND id > ?
                ORDER BY id
            """, (folder_id, last_link_id))
        else:
            local_cursor.execute("SELECT id, link FROM links WHERE folder_id=? AND status='pending' ORDER BY id", (folder_id,))
        
        links = local_cursor.fetchall()
        if not links:
            await context.bot.send_message(chat_id=user_id, text=f"⚠️ لا توجد روابط معلقة متبقية للرقم {phone} في هذا المجلد.")
            return

        await context.bot.send_message(chat_id=user_id, text=f"📱 جاري استئناف الانضمام بالرقم: {phone} (من رابط {links[0][0] if links else 'بداية'})...")

        success_count = 0
        fail_count = 0

        for lid, link in links:
            if stop_flag and not stop_flag():
                break
            if not running_states.get(user_id, False) and not stop_flag:
                break

            if global_pause.get(user_id, {}).get("paused", False):
                until = global_pause[user_id].get("until")
                if until and until > datetime.now():
                    wait_seconds = (until - datetime.now()).total_seconds() + 2
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⏳ انتظار رفع التوقف الجماعي ({int(wait_seconds)} ثانية) للرقم {phone}..."
                    )
                    await asyncio.sleep(wait_seconds)
                    global_pause[user_id]["paused"] = False
                    global_pause[user_id]["until"] = None
                else:
                    global_pause[user_id]["paused"] = False
                    global_pause[user_id]["until"] = None

            if user_id != ADMIN_ID:
                local_cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                current_bal = local_cursor.fetchone()[0]
                if current_bal < 1:
                    await context.bot.send_message(chat_id=user_id, text=f"⚠️ نفدت نقاطك (للرقم {phone})، يرجى شحنها.")
                    break

            if join_counter > 0 and join_counter % 5 == 0:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⏳ استراحة لمدة {rest_time_minutes} دقائق بعد 5 روابط (للرقم {phone})..."
                )
                for _ in range(int(rest_time_minutes * 60 * 10)):
                    if stop_flag and not stop_flag():
                        break
                    if not running_states.get(user_id, False) and not stop_flag:
                        break
                    await asyncio.sleep(0.1)
                if stop_flag and not stop_flag():
                    await context.bot.send_message(chat_id=user_id, text=f"🛑 تم إيقاف الانضمام للرقم {phone}.")
                    break
                if not running_states.get(user_id, False) and not stop_flag:
                    break
                await context.bot.send_message(chat_id=user_id, text=f"🚀 استئناف العمل للرقم {phone}...")

            while True:
                if stop_flag and not stop_flag():
                    break
                if not running_states.get(user_id, False) and not stop_flag:
                    break

                status, msg = await join_logic_with_global_pause(session_str, link, user_id, account_index, context)
                
                if status is None and msg is None:
                    continue

                if status == "SUCCESS":
                    local_cursor.execute("UPDATE links SET status='completed' WHERE id=?", (lid,))
                    success_count += 1
                else:
                    local_cursor.execute("UPDATE links SET status='failed' WHERE id=?", (lid,))
                    fail_count += 1

                if user_id != ADMIN_ID:
                    local_cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id=?", (user_id,))
                local_db.commit()

                update_progress(user_id, account_id, folder_id, lid)
                join_counter += 1

                formatted_link = f"[{link}](https://t.me/{link})" if not link.startswith("http") else link
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📱 {phone}: {formatted_link}\nالنتيجة: {msg}",
                    parse_mode="Markdown"
                )
                break

            for _ in range(int(delay_time * 10)):
                if stop_flag and not stop_flag():
                    break
                if not running_states.get(user_id, False) and not stop_flag:
                    break
                await asyncio.sleep(0.1)

        if not stop_flag:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🏁 انتهى الانضمام للرقم {phone}.\n✅ نجاح: {success_count}\n❌ فشل: {fail_count}"
            )
        else:
            await context.bot.send_message(chat_id=user_id, text=f"🛑 تم إيقاف الانضمام للرقم {phone}.")

        local_db.close()
    except Exception as e:
        logging.error(f"Error in multi_join_task for {phone}: {e}")
        await context.bot.send_message(chat_id=user_id, text=f"❌ حدث خطأ في الرقم {phone}: {str(e)[:100]}")

# ========== دوال الانضمام المساعدة ==========
async def start_joining_from_callback(update, context, user_id, folder_id, folder_name):
    links = get_folder_links(folder_id)
    if not links:
        await update.effective_message.reply_text("⚠️ لا توجد روابط معلقة في هذا المجلد.")
        return

    if user_id != ADMIN_ID:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        bal = cursor.fetchone()[0]
        if bal < len(links):
            await update.effective_message.reply_text(
                f"❌ رصيدك لا يكفي. تحتاج {len(links)} نقطة، لديك {bal} نقطة.\nتواصل مع @Ra11_8h للشحن."
            )
            return

    cursor.execute("SELECT session, phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
    active_acc = cursor.fetchone()
    if not active_acc:
        await update.effective_message.reply_text("❌ لا يوجد حساب نشط. قم بتسجيل الدخول.")
        return

    cursor.execute("SELECT delay, rest_time FROM users WHERE user_id=?", (user_id,))
    user_conf = cursor.fetchone()
    delay_time = user_conf[0] if user_conf else 10
    rest_time = user_conf[1] if user_conf and user_conf[1] is not None else 5

    running_states[user_id] = True
    await update.effective_message.reply_text(
        f"🚀 بدء الانضمام من المجلد **{folder_name}** ({len(links)} رابط)..."
    )

    asyncio.create_task(
        background_join_task(user_id, context, active_acc, delay_time, rest_time, folder_id, folder_name)
    )

async def start_joining(update, context, user_id, folder_id, folder_name):
    links = get_folder_links(folder_id)
    if not links:
        await update.message.reply_text("⚠️ لا توجد روابط معلقة في هذا المجلد.")
        return

    if user_id != ADMIN_ID:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        bal = cursor.fetchone()[0]
        if bal < len(links):
            await update.message.reply_text(
                f"❌ رصيدك لا يكفي. تحتاج {len(links)} نقطة، لديك {bal} نقطة.\nتواصل مع @Ra11_8h للشحن."
            )
            return

    cursor.execute("SELECT session, phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
    active_acc = cursor.fetchone()
    if not active_acc:
        await update.message.reply_text("❌ لا يوجد حساب نشط.")
        return

    cursor.execute("SELECT delay, rest_time FROM users WHERE user_id=?", (user_id,))
    user_conf = cursor.fetchone()
    delay_time = user_conf[0] if user_conf else 10
    rest_time = user_conf[1] if user_conf and user_conf[1] is not None else 5

    running_states[user_id] = True
    await update.message.reply_text(
        f"🚀 بدء الانضمام من المجلد **{folder_name}** ({len(links)} رابط)..."
    )

    asyncio.create_task(
        background_join_task(user_id, context, active_acc, delay_time, rest_time, folder_id, folder_name)
    )

async def start_multi_joining(update, context, user_id, folder_id, folder_name):
    cursor.execute("SELECT id, session, phone FROM accounts WHERE user_id=?", (user_id,))
    accounts = cursor.fetchall()
    if not accounts:
        await update.effective_message.reply_text("❌ لا توجد حسابات مسجلة.")
        return

    links = get_folder_links(folder_id)
    if not links:
        await update.effective_message.reply_text("⚠️ لا توجد روابط معلقة في هذا المجلد.")
        return

    if user_id != ADMIN_ID:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        bal = cursor.fetchone()[0]
        total_needed = len(links) * len(accounts)
        if bal < total_needed:
            await update.effective_message.reply_text(
                f"❌ رصيدك لا يكفي للانضمام المتعدد. تحتاج {total_needed} نقطة (لـ {len(accounts)} حسابات × {len(links)} رابط)، لديك {bal} نقطة.\nتواصل مع @Ra11_8h للشحن."
            )
            return

    cursor.execute("SELECT delay, rest_time FROM users WHERE user_id=?", (user_id,))
    user_conf = cursor.fetchone()
    delay_time = user_conf[0] if user_conf else 10
    rest_time = user_conf[1] if user_conf and user_conf[1] is not None else 5

    stop_flags = [True] * len(accounts)
    multi_running_states[user_id] = stop_flags

    if user_id in global_pause:
        del global_pause[user_id]

    await update.effective_message.reply_text(
        f"🚀 بدء الانضمام المتعدد من المجلد **{folder_name}** ({len(links)} رابط) باستخدام {len(accounts)} حسابات...\n"
        f"سيتم استئناف العمل من حيث توقف في حالة حدوث أي تعليق."
    )

    tasks = []
    for i, acc in enumerate(accounts):
        account_id, session_str, phone = acc
        def make_stop_flag(index):
            def stop_flag():
                return multi_running_states.get(user_id, [False])[index] if index < len(multi_running_states.get(user_id, [])) else False
            return stop_flag

        stop_flag = make_stop_flag(i)
        task = asyncio.create_task(
            multi_join_task(
                user_id,
                context,
                (session_str, phone, account_id),
                folder_id,
                folder_name,
                delay_time,
                rest_time,
                i,
                stop_flag
            )
        )
        tasks.append(task)

    context.user_data['multi_tasks'] = tasks
    context.user_data['multi_folder'] = folder_name
    context.user_data['multi_folder_id'] = folder_id
    context.user_data['multi_accounts_count'] = len(accounts)

    asyncio.create_task(wait_for_multi_tasks_and_report(update, context, user_id, tasks, len(accounts), len(links), folder_id))

    await update.effective_message.reply_text(
        f"✅ تم تشغيل {len(accounts)} مهمة انضمام متعدد.\n"
        f"يمكنك إيقافها باستخدام زر '🛑 إيقاف الانضمام المتعدد'."
    )

async def wait_for_multi_tasks_and_report(update, context, user_id, tasks, accounts_count, links_count, folder_id):
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except:
        pass

    cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND folder_id=? AND status='completed'", (user_id, folder_id))
    completed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND folder_id=? AND status='failed'", (user_id, folder_id))
    failed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND folder_id=? AND status='pending'", (user_id, folder_id))
    pending = cursor.fetchone()[0]

    if user_id in multi_running_states:
        del multi_running_states[user_id]
    if user_id in global_pause:
        del global_pause[user_id]
    if 'multi_tasks' in context.user_data:
        del context.user_data['multi_tasks']
    if 'multi_folder' in context.user_data:
        del context.user_data['multi_folder']
    if 'multi_folder_id' in context.user_data:
        del context.user_data['multi_folder_id']

    await context.bot.send_message(
        chat_id=user_id,
        text=f"📊 **تقرير الانضمام المتعدد النهائي**\n"
             f"📁 المجلد: {context.user_data.get('multi_folder', 'غير معروف')}\n"
             f"📱 عدد الحسابات المستخدمة: {accounts_count}\n"
             f"🔗 عدد الروابط في المجلد: {links_count}\n"
             f"✅ تم الانضمام بنجاح: {completed}\n"
             f"❌ فشل الانضمام: {failed}\n"
             f"⏳ روابط معلقة لم تعالج: {pending}\n"
             f"🏁 انتهت جميع المهام."
    )

async def stop_multi_joining(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in multi_running_states:
        await update.message.reply_text("⚠️ لا توجد عملية انضمام متعدد نشطة حالياً.")
        return

    for i in range(len(multi_running_states[user_id])):
        multi_running_states[user_id][i] = False

    tasks = context.user_data.get('multi_tasks', [])
    for task in tasks:
        if not task.done():
            task.cancel()

    if user_id in global_pause:
        del global_pause[user_id]

    multi_running_states.pop(user_id, None)
    context.user_data.pop('multi_tasks', None)
    await update.message.reply_text("🛑 تم إيقاف جميع عمليات الانضمام المتعدد.")

# ========== دوال الشحن والإدارة ==========
async def handle_charge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text("⛔ تم حظرك من استخدام هذا البوت.")
        return

    packages = get_packages()
    if not packages:
        await update.message.reply_text("⚠️ لا توجد باقات متاحة حالياً. يرجى التواصل مع الإدارة.")
        return

    keyboard = []
    for pkg in packages:
        pkg_id, points, price = pkg
        keyboard.append([InlineKeyboardButton(f"{points} نقطة بـ {price} ريال", callback_data=f"charge_{pkg_id}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_charge")])

    await update.message.reply_text(
        get_setting("charge_info_text", "💳 **اختر الباقة المناسبة:**"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def charge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "cancel_charge":
        await query.edit_message_text("❌ تم إلغاء عملية الشحن.")
        return

    if data.startswith("charge_"):
        pkg_id = int(data.split("_")[1])
        cursor.execute("SELECT points, price FROM packages WHERE id=?", (pkg_id,))
        row = cursor.fetchone()
        if not row:
            await query.edit_message_text("❌ الباقة غير موجودة.")
            return
        points, price = row
        package = f"{points} نقطة بـ {price} ريال"

        user_charge_state[user_id] = {
            'package': package,
            'points': points,
            'price': price
        }

        bank_text = (
            "🏦 **بيانات الحساب البنكي:**\n\n"
            "البنك: الكريمي\n"
            "الاسم: محمد عبدة محمد غالب\n"
            "رقم الحساب: `3097999111`\n\n"
            "يرجى تحويل المبلغ على هذا الحساب، ثم اضغط على **تم التحويل** بعد إتمام الحوالة.\n"
            "لإلغاء العملية اضغط **إلغاء التحويل**."
        )
        keyboard = [
            [InlineKeyboardButton("✅ تم التحويل", callback_data="transfer_done")],
            [InlineKeyboardButton("❌ إلغاء التحويل", callback_data="cancel_transfer")]
        ]
        await query.edit_message_text(
            bank_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def transfer_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if user_id not in user_charge_state:
        await query.edit_message_text("❌ لم يتم العثور على عملية شحن نشطة. ابدأ من جديد.")
        return

    await query.edit_message_text(
        "📸 **يرجى إرسال صورة إشعار الحوالة (لقطة شاشة) الآن.**\n"
        "أرسل الصورة كـ **صورة (Photo)** وليس كملف.\n"
        "سيتم حفظها وإرسالها للمشرف للمراجعة."
    )
    context.user_data['awaiting_charge_photo'] = True

async def cancel_transfer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if user_id in user_charge_state:
        del user_charge_state[user_id]
    await query.edit_message_text("❌ تم إلغاء عملية التحويل.")

async def handle_charge_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_charge_photo'):
        return

    if not update.message.photo:
        await update.message.reply_text("❌ يرجى إرسال صورة (Photo) وليس ملفاً. أعد المحاولة.")
        return

    if user_id not in user_charge_state:
        await update.message.reply_text("❌ لم يتم العثور على عملية شحن نشطة. ابدأ من جديد.")
        context.user_data['awaiting_charge_photo'] = False
        return

    photo_file_id = update.message.photo[-1].file_id
    charge_info = user_charge_state[user_id]
    
    request_id = save_charge_request(
        user_id,
        charge_info['package'],
        charge_info['points'],
        charge_info['price'],
        photo_file_id
    )

    await update.message.reply_text(
        "✅ تم حفظ الصورة وسيتم إيداع الرصيد إلى حسابك بأقرب وقت.\n"
        "نرجو الانتظار حتى يتم تأكيد الحوالة من قبل الإدارة."
    )

    caption = (
        f"📥 **طلب شحن جديد**\n\n"
        f"👤 المستخدم: `{user_id}`\n"
        f"📦 الباقة: {charge_info['package']}\n"
        f"💰 عدد النقاط: {charge_info['points']}\n"
        f"💵 المبلغ: {charge_info['price']} ريال\n"
        f"🆔 رقم الطلب: {request_id}\n\n"
        f"يرجى مراجعة الصورة واتخاذ القرار."
    )
    keyboard = [
        [InlineKeyboardButton("✅ قبول", callback_data=f"approve_charge_{request_id}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"reject_charge_{request_id}")]
    ]
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file_id,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Failed to send photo to admin: {e}")
        await update.message.reply_text("⚠️ حدث خطأ في إرسال الصورة للمشرف، لكن تم حفظها. سيتم مراجعتها قريباً.")

    if user_id in user_charge_state:
        del user_charge_state[user_id]
    context.user_data['awaiting_charge_photo'] = False

async def admin_charge_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    admin_id = update.effective_user.id

    if admin_id != ADMIN_ID:
        await query.edit_message_text("⛔ هذه الخاصية للمشرف فقط.")
        return

    if data.startswith("approve_charge_"):
        request_id = int(data.split("_")[2])
        charge_data = get_charge_request(request_id)
        if not charge_data:
            await query.edit_message_text("❌ الطلب غير موجود.")
            return
        user_id, amount = charge_data
        
        add_balance(user_id, amount)
        update_charge_request_status(request_id, 'completed')
        
        new_caption = (
            f"✅ **تم قبول طلب الشحن**\n\n"
            f"👤 المستخدم: `{user_id}`\n"
            f"💰 عدد النقاط: {amount}\n"
            f"🆔 رقم الطلب: {request_id}\n\n"
            f"تمت إضافة النقاط بنجاح."
        )
        await query.edit_message_caption(
            caption=new_caption,
            parse_mode="Markdown"
        )
        await query.edit_message_reply_markup(reply_markup=None)
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎉 تم شحن رصيدك بنجاح!\nتم إضافة {amount} نقطة إلى حسابك."
            )
        except Exception as e:
            logging.error(f"Failed to notify user {user_id}: {e}")

    elif data.startswith("reject_charge_"):
        request_id = int(data.split("_")[2])
        charge_data = get_charge_request(request_id)
        if not charge_data:
            await query.edit_message_text("❌ الطلب غير موجود.")
            return
        user_id, _ = charge_data
        update_charge_request_status(request_id, 'rejected')
        
        new_caption = (
            f"❌ **تم رفض طلب الشحن**\n\n"
            f"👤 المستخدم: `{user_id}`\n"
            f"🆔 رقم الطلب: {request_id}\n\n"
            f"تم رفض الطلب."
        )
        await query.edit_message_caption(
            caption=new_caption,
            parse_mode="Markdown"
        )
        await query.edit_message_reply_markup(reply_markup=None)
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ لم يتم الشحن بنجاح. يرجى التواصل مع الإدارة."
            )
        except Exception as e:
            logging.error(f"Failed to notify user {user_id}: {e}")

# ========== إدارة الباقات ==========
async def manage_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ هذه الخاصية للمشرف فقط.")
        return

    keyboard = [
        [InlineKeyboardButton("➕ إضافة باقة جديدة", callback_data="add_package")],
        [InlineKeyboardButton("🗑️ حذف باقة", callback_data="delete_package")],
        [InlineKeyboardButton("🔙 العودة", callback_data="back_to_menu")]
    ]
    await update.message.reply_text(
        "📦 **إدارة الباقات**\n\nاختر الإجراء المناسب:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def package_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        await query.edit_message_text("⛔ هذه الخاصية للمشرف فقط.")
        return

    if data == "back_to_menu":
        await query.edit_message_text("🔙 تم العودة.")
        return

    if data == "add_package":
        await query.edit_message_text(
            "📝 **إضافة باقة جديدة**\n\n"
            "أرسل عدد النقاط والسعر مفصولين بمسافة.\n"
            "مثال: `100 500` (100 نقطة بـ 500 ريال)"
        )
        context.user_data['admin_action'] = 'add_package'
        return

    if data == "delete_package":
        packages = get_packages()
        if not packages:
            await query.edit_message_text("⚠️ لا توجد باقات لحذفها.")
            return
        keyboard = []
        for pkg in packages:
            pkg_id, points, price = pkg
            keyboard.append([InlineKeyboardButton(f"{points} نقطة بـ {price} ريال", callback_data=f"del_pkg_{pkg_id}")])
        keyboard.append([InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_delete")])
        await query.edit_message_text(
            "🗑️ **اختر الباقة المراد حذفها:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    if data.startswith("del_pkg_"):
        pkg_id = int(data.split("_")[2])
        delete_package(pkg_id)
        await query.edit_message_text(f"✅ تم حذف الباقة بنجاح.")
        return

    if data == "cancel_delete":
        await query.edit_message_text("❌ تم إلغاء الحذف.")
        return

async def handle_add_package_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    if context.user_data.get('admin_action') != 'add_package':
        return

    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("❌ الصيغة غير صحيحة. أرسل عدد النقاط والسعر مفصولين بمسافة.\nمثال: `100 500`")
        return
    try:
        points = int(parts[0])
        price = int(parts[1])
        if points <= 0 or price <= 0:
            raise ValueError
        add_package(points, price)
        await update.message.reply_text(f"✅ تم إضافة الباقة: {points} نقطة بـ {price} ريال")
        context.user_data.pop('admin_action', None)
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال أرقام صحيحة موجبة.")

# ========== إعدادات البوت (لوحة التحكم) ==========
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ هذه الخاصية للمشرف فقط.")
        return

    keyboard = [
        [InlineKeyboardButton("📝 تعديل رسالة الترحيب", callback_data="settings_welcome")],
        [InlineKeyboardButton("🔘 تعديل أسماء الأزرار", callback_data="settings_buttons")],
        [InlineKeyboardButton("⏱️ تعديل الإعدادات الافتراضية (التأخير/الراحة)", callback_data="settings_defaults")],
        [InlineKeyboardButton("📊 تعديل رسالة حالة النظام", callback_data="settings_status")],
        [InlineKeyboardButton("💳 تعديل رسالة الشحن", callback_data="settings_charge")],
        [InlineKeyboardButton("🔄 إعادة تعيين جميع الإعدادات", callback_data="settings_reset")],
        [InlineKeyboardButton("🔙 عودة", callback_data="settings_back")]
    ]
    await update.message.reply_text(
        "⚙️ **لوحة تحكم إعدادات البوت**\n\nاختر العنصر الذي تريد تعديله:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        await query.edit_message_text("⛔ هذه الخاصية للمشرف فقط.")
        return

    if data == "settings_back":
        await query.edit_message_text("🔙 تم العودة.")
        return

    if data == "settings_reset":
        reset_settings()
        await query.edit_message_text("✅ تم إعادة تعيين جميع الإعدادات إلى الوضع الافتراضي.")
        return

    if data == "settings_welcome":
        current = get_setting("welcome_text")
        await query.edit_message_text(
            f"📝 **تعديل رسالة الترحيب**\n\n"
            f"الرسالة الحالية:\n`{current}`\n\n"
            f"المتغيرات المتاحة:\n"
            f"`{{name}}` - اسم المستخدم\n"
            f"`{{user_id}}` - معرف المستخدم\n"
            f"`{{balance_display}}` - عرض الرصيد\n"
            f"`{{paused_msg}}` - رسالة التوقف\n\n"
            f"أرسل النص الجديد، أو /cancel للإلغاء."
        )
        context.user_data['settings_step'] = 'welcome'
        return

    if data == "settings_buttons":
        buttons = [
            ("join_button", "🚀 بدء الانضمام"),
            ("stop_button", "🛑 إيقاف الانضمام"),
            ("folders_button", "📁 مجلدات الروابط"),
            ("charge_button", "💳 شحن حسابي"),
            ("admin_join_button", "🔀 انضمام متعدد"),
            ("admin_stop_multi_button", "🛑 إيقاف الانضمام المتعدد"),
            ("admin_packages_button", "📦 إدارة الباقات"),
            ("admin_settings_button", "⚙️ إعدادات البوت")
        ]
        keyboard = []
        for key, default in buttons:
            current = get_setting(key, default)
            keyboard.append([InlineKeyboardButton(f"{current} (تعديل)", callback_data=f"set_btn_{key}")])
        keyboard.append([InlineKeyboardButton("🔙 عودة", callback_data="settings_back")])
        await query.edit_message_text(
            "🔘 **تعديل أسماء الأزرار**\nاختر الزر لتغيير نصه:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("set_btn_"):
        key = data.replace("set_btn_", "")
        current = get_setting(key)
        context.user_data['settings_step'] = f'button_{key}'
        await query.edit_message_text(
            f"🔘 **تعديل زر**\n\nالمفتاح: `{key}`\nالنص الحالي: `{current}`\n\nأرسل النص الجديد للزر، أو /cancel للإلغاء."
        )
        return

    if data == "settings_defaults":
        current_delay = get_setting("default_delay", "10")
        current_rest = get_setting("default_rest", "5")
        await query.edit_message_text(
            f"⏱️ **الإعدادات الافتراضية**\n\n"
            f"• التأخير بين الروابط: `{current_delay}` ثانية\n"
            f"• استراحة كل 5 روابط: `{current_rest}` دقائق\n\n"
            f"للتعديل أرسل: `تأخير:قيمة` أو `راحة:قيمة`\n"
            f"مثال: `تأخير:15` أو `راحة:10`"
        )
        context.user_data['settings_step'] = 'defaults'
        return

    if data == "settings_status":
        current = get_setting("system_status_text")
        await query.edit_message_text(
            f"📊 **تعديل رسالة حالة النظام**\n\n"
            f"الرسالة الحالية:\n`{current}`\n\n"
            f"المتغيرات المتاحة:\n"
            f"`{{is_running}}` - حالة التشغيل\n"
            f"`{{active_phone}}` - الرقم النشط\n"
            f"`{{total_accounts}}` - عدد الحسابات\n"
            f"`{{multi_perm}}` - صلاحية المتعدد\n"
            f"`{{pause_status}}` - حالة التوقف\n"
            f"`{{exempt_status}}` - حالة الاستثناء\n"
            f"`{{delay}}` - التأخير\n"
            f"`{{rest}}` - الراحة\n"
            f"`{{folder_name}}` - اسم المجلد\n"
            f"`{{bal_str}}` - الرصيد\n\n"
            f"أرسل النص الجديد، أو /cancel للإلغاء."
        )
        context.user_data['settings_step'] = 'status'
        return

    if data == "settings_charge":
        current = get_setting("charge_info_text")
        await query.edit_message_text(
            f"💳 **تعديل رسالة الشحن**\n\n"
            f"الرسالة الحالية:\n`{current}`\n\n"
            f"أرسل النص الجديد، أو /cancel للإلغاء."
        )
        context.user_data['settings_step'] = 'charge'
        return

async def settings_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    step = context.user_data.get('settings_step')
    if not step:
        return

    text = update.message.text.strip()
    if text == "/cancel":
        context.user_data.pop('settings_step', None)
        await update.message.reply_text("❌ تم إلغاء التعديل.")
        return

    if step == 'welcome':
        set_setting("welcome_text", text)
        await update.message.reply_text("✅ تم تحديث رسالة الترحيب.")
        context.user_data.pop('settings_step', None)

    elif step.startswith('button_'):
        key = step.replace('button_', '')
        set_setting(key, text)
        await update.message.reply_text(f"✅ تم تحديث الزر `{key}` بنجاح.")
        context.user_data.pop('settings_step', None)

    elif step == 'defaults':
        if ':' in text:
            parts = text.split(':', 1)
            key_part = parts[0].strip()
            value = parts[1].strip()
            if key_part in ['تأخير', 'delay']:
                try:
                    val = int(value)
                    if val < 1:
                        raise ValueError
                    set_setting("default_delay", str(val))
                    # تحديث في جدول المستخدمين إذا أردت، لكننا نتركها كقيمة افتراضية.
                    await update.message.reply_text(f"✅ تم تحديث التأخير الافتراضي إلى {val} ثانية.")
                except:
                    await update.message.reply_text("❌ يرجى إدخال رقم صحيح (أكبر من 0).")
            elif key_part in ['راحة', 'rest']:
                try:
                    val = int(value)
                    if val < 0:
                        raise ValueError
                    set_setting("default_rest", str(val))
                    await update.message.reply_text(f"✅ تم تحديث وقت الراحة الافتراضي إلى {val} دقائق.")
                except:
                    await update.message.reply_text("❌ يرجى إدخال رقم صحيح (0 أو أكثر).")
            else:
                await update.message.reply_text("❌ صيغة غير صحيحة. استخدم `تأخير:قيمة` أو `راحة:قيمة`")
        else:
            await update.message.reply_text("❌ يرجى استخدام الصيغة: `تأخير:قيمة` أو `راحة:قيمة`")
        context.user_data.pop('settings_step', None)

    elif step == 'status':
        set_setting("system_status_text", text)
        await update.message.reply_text("✅ تم تحديث رسالة حالة النظام.")
        context.user_data.pop('settings_step', None)

    elif step == 'charge':
        set_setting("charge_info_text", text)
        await update.message.reply_text("✅ تم تحديث رسالة الشحن.")
        context.user_data.pop('settings_step', None)

# ========== دالة البداية الرئيسية (معدلة لاستخدام الإعدادات) ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    if is_user_banned(user_id):
        await update.message.reply_text("⛔ تم حظرك من استخدام هذا البوت.")
        return

    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
    db.commit()

    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    balance = cursor.fetchone()[0]

    context.user_data.clear()

    # بناء الأزرار من الإعدادات
    join_btn = get_setting("join_button", "🚀 بدء الانضمام")
    stop_btn = get_setting("stop_button", "🛑 إيقاف الانضمام")
    folders_btn = get_setting("folders_button", "📁 مجلدات الروابط")
    charge_btn = get_setting("charge_button", "💳 شحن حسابي")

    keyboard = [
        [KeyboardButton("📱 تسجيل الدخول الجديد"), KeyboardButton("🔗 إرسال روابط")],
        [KeyboardButton(join_btn), KeyboardButton(stop_btn)],
        [KeyboardButton("📱 أرقامي المسجلة"), KeyboardButton("🗑️ حذف رقم مسجل")],
        [KeyboardButton("⏱️ تحديد الوقت"), KeyboardButton("💤 استراحة كل 5 روابط")],
        [KeyboardButton("📊 حالة النظام"), KeyboardButton("🗑️ مسح الروابط")],
        [KeyboardButton(folders_btn), KeyboardButton(charge_btn)]
    ]

    if has_multi_join_permission(user_id):
        admin_join_btn = get_setting("admin_join_button", "🔀 انضمام متعدد")
        admin_stop_multi_btn = get_setting("admin_stop_multi_button", "🛑 إيقاف الانضمام المتعدد")
        keyboard.append([KeyboardButton(admin_join_btn), KeyboardButton(admin_stop_multi_btn)])

    if user_id == ADMIN_ID:
        admin_packages_btn = get_setting("admin_packages_button", "📦 إدارة الباقات")
        admin_settings_btn = get_setting("admin_settings_button", "⚙️ إعدادات البوت")
        keyboard.append([KeyboardButton(admin_packages_btn), KeyboardButton(admin_settings_btn)])
        keyboard.append([KeyboardButton("⏹️ إيقاف البوت"), KeyboardButton("▶️ تشغيل البوت")])
        keyboard.append([KeyboardButton("▶️ تشغيل البوت لمستخدم"), KeyboardButton("⏹️ إيقاف البوت لمستخدم")])
        keyboard.append([KeyboardButton("👑 لوحة المطور"), KeyboardButton("🔋 شحن نقاط لمعلم")])
        keyboard.append([KeyboardButton("📢 إذاعة رسالة عامة")])
        keyboard.append([KeyboardButton("➕ منح انضمام متعدد"), KeyboardButton("➖ إلغاء انضمام متعدد")])
        keyboard.append([KeyboardButton("🚫 حظر مستخدم"), KeyboardButton("✅ إلغاء حظر مستخدم")])

    balance_display = "المشرف العام (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{balance} نقطة"
    paused_msg = " ⚠️ البوت متوقف حالياً (للمشرف فقط)" if BOT_PAUSED and not is_user_exempted(user_id) else ""
    if BOT_PAUSED and is_user_exempted(user_id) and user_id != ADMIN_ID:
        paused_msg = " ℹ️ البوت متوقف عام، لكن لديك استثناء وتستطيع استخدامه."

    welcome_text = get_setting("welcome_text").format(
        name=name,
        user_id=user_id,
        balance_display=balance_display,
        paused_msg=paused_msg
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )

# ========== دالة معالجة الرسائل الرئيسية (معدلة لاستخدام الإعدادات) ==========
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_PAUSED
    user_id = update.effective_user.id
    if not update.message or not update.message.text:
        return

    if is_user_banned(user_id):
        await update.message.reply_text("⛔ تم حظرك من استخدام هذا البوت.")
        return

    if BOT_PAUSED and not is_user_exempted(user_id):
        await update.message.reply_text("⛔ البوت متوقف حالياً، يرجى التواصل مع المشرف.")
        return

    text = update.message.text.strip()
    action = context.user_data.get('action')

    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
    db.commit()

    if text == "/start":
        return await start(update, context)

    # ========== أزرار الإعدادات (للمشرف) ==========
    if text == get_setting("admin_settings_button", "⚙️ إعدادات البوت") and user_id == ADMIN_ID:
        await settings_command(update, context)
        return

    # ========== زر شحن حسابي ==========
    if text == get_setting("charge_button", "💳 شحن حسابي"):
        await handle_charge(update, context)
        return

    # ========== أزرار المشرف ==========
    if text == "⏹️ إيقاف البوت" and user_id == ADMIN_ID:
        BOT_PAUSED = True
        await update.message.reply_text("✅ تم إيقاف البوت. لن يستجيب لأي أوامر من المستخدمين العاديين.")
        return

    if text == "▶️ تشغيل البوت" and user_id == ADMIN_ID:
        BOT_PAUSED = False
        await update.message.reply_text("✅ تم تشغيل البوت. جميع المستخدمين يمكنهم استخدام البوت الآن.")
        return

    if text == "▶️ تشغيل البوت لمستخدم" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد تشغيل البوت له (استثناء من التوقف العام):")
        context.user_data['action'] = 'admin_exempt_user'
        return

    if text == "⏹️ إيقاف البوت لمستخدم" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد إلغاء استثناء التوقف له (يعود للتوقف العام):")
        context.user_data['action'] = 'admin_remove_exempt'
        return

    if text == get_setting("admin_join_button", "🔀 انضمام متعدد") and has_multi_join_permission(user_id):
        folders = get_user_folders(user_id)
        if not folders:
            await update.message.reply_text("⚠️ لا توجد مجلدات. أرسل روابط واحفظها أولاً.")
            return
        reply = "📁 **اختر المجلد للانضمام المتعدد:**"
        keyboard = [[InlineKeyboardButton(fname, callback_data=f"multi_join_folder_{fid}")] for fid, fname in folders]
        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
        await update.message.reply_text(reply, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if text == get_setting("admin_stop_multi_button", "🛑 إيقاف الانضمام المتعدد") and has_multi_join_permission(user_id):
        await stop_multi_joining(update, context)
        return

    if text == get_setting("admin_packages_button", "📦 إدارة الباقات") and user_id == ADMIN_ID:
        await manage_packages(update, context)
        return

    if text == "➕ منح انضمام متعدد" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد منحه صلاحية الانضمام المتعدد:")
        context.user_data['action'] = 'admin_grant_multi_join'
        return

    if text == "➖ إلغاء انضمام متعدد" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد إلغاء صلاحية الانضمام المتعدد له:")
        context.user_data['action'] = 'admin_revoke_multi_join'
        return

    if text == "🚫 حظر مستخدم" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد حظره:")
        context.user_data['action'] = 'admin_ban_user'
        return

    if text == "✅ إلغاء حظر مستخدم" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد إلغاء حظره:")
        context.user_data['action'] = 'admin_unban_user'
        return

    if text == "🔋 شحن نقاط لمعلم" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم الذي تريد شحن نقاطه:")
        context.user_data['action'] = 'admin_charge_id'
        return

    # ========== بقية الأزرار (مع الإعدادات) ==========
    if text == get_setting("folders_button", "📁 مجلدات الروابط"):
        folders = get_user_folders(user_id)
        if not folders:
            await update.message.reply_text("📁 لا توجد مجلدات حالياً. أرسل روابط واحفظها لإنشاء مجلد جديد.")
            return
        reply = "📁 **مجلداتك:**\n\n"
        for fid, fname in folders:
            cursor.execute("SELECT COUNT(*) FROM links WHERE folder_id=? AND status='pending'", (fid,))
            count = cursor.fetchone()[0]
            reply += f"• {fname} (عدد الروابط المعلقة: {count})\n"
        reply += "\nاختر مجلداً بالضغط على أحد الأزرار أدناه:"
        keyboard = [[InlineKeyboardButton(fname, callback_data=f"select_folder_{fid}")] for fid, fname in folders]
        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
        await update.message.reply_text(reply, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if text == "📥 حفظ الروابط وإنهاء الإرسال" and action == 'add_links':
        temp_links = context.user_data.get('temp_links_list', [])
        if not temp_links:
            await update.message.reply_text("⚠️ لم ترسل أي روابط.")
        else:
            folder_id = create_folder(user_id)
            cursor.execute("SELECT folder_name FROM folders WHERE id=?", (folder_id,))
            folder_name = cursor.fetchone()[0]
            for link in temp_links:
                cursor.execute("INSERT INTO links (user_id, folder_id, link) VALUES (?, ?, ?)", (user_id, folder_id, link))
            db.commit()
            await update.message.reply_text(
                f"🏁 تم حفظ {len(temp_links)} رابط في مجلد جديد: **{folder_name}**"
            )
        context.user_data.clear()
        return await start(update, context)

    if text == "🔗 إرسال روابط":
        await update.message.reply_text(
            "📥 أرسل الروابط تباعاً، ثم اضغط على زر (📥 حفظ الروابط وإنهاء الإرسال).",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📥 حفظ الروابط وإنهاء الإرسال")]], resize_keyboard=True)
        )
        context.user_data['action'] = 'add_links'
        context.user_data['temp_links_list'] = []
        return

    if text == get_setting("join_button", "🚀 بدء الانضمام"):
        if running_states.get(user_id, False):
            await update.message.reply_text("⚠️ هناك عملية جارية بالفعل.")
            return

        folders = get_user_folders(user_id)
        if not folders:
            await update.message.reply_text("⚠️ لا توجد مجلدات. أرسل روابط واحفظها أولاً.")
            return

        if len(folders) == 1:
            folder_id, folder_name = folders[0]
            await start_joining(update, context, user_id, folder_id, folder_name)
            return

        reply = "📁 **اختر المجلد الذي تريد الانضمام منه:**"
        keyboard = [[InlineKeyboardButton(fname, callback_data=f"join_folder_{fid}")] for fid, fname in folders]
        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
        await update.message.reply_text(reply, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if text == get_setting("stop_button", "🛑 إيقاف الانضمام"):
        running_states[user_id] = False
        await update.message.reply_text("⏳ جاري إيقاف الانضمام العادي...")
        return

    if text == "📊 حالة النظام":
        # استخدام الإعدادات لرسالة حالة النظام
        cursor.execute("SELECT delay, rest_time FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if row:
            delay, rest = row
        else:
            default_delay = int(get_setting("default_delay", "10"))
            default_rest = int(get_setting("default_rest", "5"))
            delay, rest = default_delay, default_rest

        cursor.execute("SELECT phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
        active_phone = cursor.fetchone()
        active_phone = active_phone[0] if active_phone else "لا يوجد"

        selected_folder = context.user_data.get('selected_folder')
        folder_name = "غير محدد"
        if selected_folder:
            cursor.execute("SELECT folder_name FROM folders WHERE id=? AND user_id=?", (selected_folder, user_id))
            res = cursor.fetchone()
            if res:
                folder_name = res[0]

        is_running = "🔥 يعمل" if running_states.get(user_id, False) else "⚪ متوقف"
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        bal = cursor.fetchone()[0]
        bal_str = "مفتوحة" if user_id == ADMIN_ID else f"{bal} نقطة"

        cursor.execute("SELECT COUNT(*) FROM accounts WHERE user_id=?", (user_id,))
        total_accounts = cursor.fetchone()[0]

        multi_perm = "نعم" if has_multi_join_permission(user_id) else "لا"

        pause_status = "لا يوجد"
        if user_id in global_pause and global_pause[user_id].get("paused", False):
            until = global_pause[user_id].get("until")
            if until:
                remaining = int((until - datetime.now()).total_seconds())
                if remaining > 0:
                    pause_status = f"⏳ توقف مؤقت (متبقي {remaining} ثانية)"
                else:
                    pause_status = "⚠️ توقف منتهٍ (جاري الاستئناف)"
            else:
                pause_status = "⏳ توقف مؤقت (غير محدد)"

        exempt_status = "نعم" if is_user_exempted(user_id) else "لا"

        status_text = get_setting("system_status_text").format(
            is_running=is_running,
            active_phone=active_phone,
            total_accounts=total_accounts,
            multi_perm=multi_perm,
            pause_status=pause_status,
            exempt_status=exempt_status,
            delay=delay,
            rest=rest,
            folder_name=folder_name,
            bal_str=bal_str
        )
        await update.message.reply_text(status_text, parse_mode="Markdown")
        return

    if text == "🗑️ مسح الروابط":
        folders = get_user_folders(user_id)
        if not folders:
            await update.message.reply_text("⚠️ لا توجد مجلدات لحذفها.")
            return
        reply = "🗑️ **اختر المجلد المراد حذفه نهائياً:**"
        keyboard = [[InlineKeyboardButton(fname, callback_data=f"delete_folder_{fid}")] for fid, fname in folders]
        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
        await update.message.reply_text(reply, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if text == "📱 تسجيل الدخول الجديد":
        await update.message.reply_text("أرسل رقم الهاتف مع رمز الدولة (مثال: +966500000000):")
        context.user_data['action'] = 'login_phone'
        return

    if text == "📱 أرقامي المسجلة":
        cursor.execute("SELECT phone, is_active FROM accounts WHERE user_id=?", (user_id,))
        accounts = cursor.fetchall()
        if not accounts:
            await update.message.reply_text("❌ لا توجد أرقام مسجلة.")
            return
        reply = "📱 أرقامك:\n\n"
        for phone, active in accounts:
            status = "🟢 نشط" if active else "⚪ غير نشط"
            reply += f"• {phone} {status}\n"
        reply += "\nللتبديل أرسل الرقم الذي تريد تفعيله."
        context.user_data['action'] = 'switch_account'
        await update.message.reply_text(reply)
        return

    if text == "🗑️ حذف رقم مسجل":
        cursor.execute("SELECT phone FROM accounts WHERE user_id=?", (user_id,))
        accounts = cursor.fetchall()
        if not accounts:
            await update.message.reply_text("❌ لا توجد أرقام.")
            return
        reply = "🗑️ اختر رقم للحذف:\n\n"
        for (phone,) in accounts:
            reply += f"• {phone}\n"
        reply += "\nأرسل الرقم كاملاً."
        context.user_data['action'] = 'delete_account'
        await update.message.reply_text(reply)
        return

    if text == "⏱️ تحديد الوقت":
        cursor.execute("SELECT delay FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        current = row[0] if row else int(get_setting("default_delay", "10"))
        await update.message.reply_text(f"⏱️ الوقت الحالي: {current} ثانية.\nأرسل الوقت الجديد (ثواني):")
        context.user_data['action'] = 'set_delay'
        return

    if text == "💤 استراحة كل 5 روابط":
        cursor.execute("SELECT rest_time FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        current = row[0] if row else int(get_setting("default_rest", "5"))
        await update.message.reply_text(f"💤 وقت الاستراحة الحالي: {current} دقائق.\nأرسل الوقت الجديد (دقائق):")
        context.user_data['action'] = 'set_rest_time'
        return

    # ========== أزرار المطور ==========
    if text == "👑 لوحة المطور" and user_id == ADMIN_ID:
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM accounts")
        total_accounts = cursor.fetchone()[0]
        cursor.execute("""
            SELECT users.user_id, users.balance, users.multi_join_permission, users.is_banned, users.is_exempted, COUNT(accounts.id) 
            FROM users 
            LEFT JOIN accounts ON users.user_id = accounts.user_id 
            GROUP BY users.user_id
        """)
        details = cursor.fetchall()
        admin_reply = f"👑 **لوحة المطور**\n👥 المستخدمين: {total_users}\n📱 الأرقام: {total_accounts}\n\n"
        for u_id, bal, perm, banned, exempt, count in details:
            perm_str = "✅" if perm == 1 else "❌"
            banned_str = "🚫" if banned == 1 else "✔️"
            exempt_str = "🔓" if exempt == 1 else "🔒"
            admin_reply += f"• المستخدم `{u_id}`: نقاط {bal} | أرقام {count} | متعدد {perm_str} | محظور {banned_str} | مستثنى {exempt_str}\n"
        await update.message.reply_text(admin_reply, parse_mode="Markdown")
        return

    if text == "📢 إذاعة رسالة عامة" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل الرسالة للإذاعة (أو /cancel):")
        context.user_data['action'] = 'admin_broadcast'
        return

    # ========== معالجة الإدخالات الأخرى ==========
    if action == 'add_links':
        found = extract_links(text)
        if found:
            if 'temp_links_list' not in context.user_data:
                context.user_data['temp_links_list'] = []
            context.user_data['temp_links_list'].extend(found)
        return

    if action == 'set_delay':
        try:
            new_delay = int(text)
            if new_delay < 1:
                raise ValueError
            cursor.execute("UPDATE users SET delay=? WHERE user_id=?", (new_delay, user_id))
            db.commit()
            await update.message.reply_text(f"✅ تم تحديث الوقت إلى: {new_delay} ثانية.")
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح (أكبر من 0).")
        context.user_data.clear()
        return

    if action == 'set_rest_time':
        try:
            new_rest = int(text)
            if new_rest < 0:
                raise ValueError
            cursor.execute("UPDATE users SET rest_time=? WHERE user_id=?", (new_rest, user_id))
            db.commit()
            await update.message.reply_text(f"✅ تم تحديث وقت الاستراحة إلى: {new_rest} دقائق.")
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح (0 أو أكثر).")
        context.user_data.clear()
        return

    # معالجة الإدخالات الخاصة بالإعدادات
    if context.user_data.get('settings_step'):
        await settings_input_handler(update, context)
        return

    if context.user_data.get('admin_action') == 'add_package':
        await handle_add_package_input(update, context)
        return

    # ========== معالجة شحن النقاط للمعلم (بعد إرسال الرقم والعدد) ==========
    if action == 'admin_charge_id' and user_id == ADMIN_ID:
        try:
            target = int(text)
            context.user_data['target_charge_id'] = target
            await update.message.reply_text(f"🔋 المستهدف: `{target}`\nأرسل عدد النقاط:")
            context.user_data['action'] = 'admin_charge_amount'
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
            context.user_data.clear()
        return

    if action == 'admin_charge_amount' and user_id == ADMIN_ID:
        try:
            amount = int(text)
            target = context.user_data.get('target_charge_id')
            if target is None:
                await update.message.reply_text("❌ حدث خطأ، حاول مجدداً.")
                context.user_data.clear()
                return
            cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (target,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target))
            db.commit()
            cursor.execute("SELECT balance FROM users WHERE user_id=?", (target,))
            new_bal = cursor.fetchone()[0]
            await update.message.reply_text(f"✅ تم إضافة {amount} نقطة للمستخدم `{target}`\nرصيده الآن: {new_bal}")
            try:
                await context.bot.send_message(chat_id=target, text=f"🎉 تم شحن {amount} نقطة، رصيدك الآن: {new_bal}")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ أرسل عدد صحيح.")
        context.user_data.clear()
        return

    # ========== معالجة استثناء التوقف ==========
    if action == 'admin_exempt_user' and user_id == ADMIN_ID:
        try:
            target = int(text)
            cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target,))
            set_user_exempted(target, True)
            await update.message.reply_text(f"✅ تم تشغيل البوت للمستخدم `{target}` (مستثنى من التوقف العام).")
            try:
                await context.bot.send_message(chat_id=target, text="✅ تم تفعيل البوت لك رغم التوقف العام، يمكنك استخدامه الآن.")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
        context.user_data.clear()
        return

    if action == 'admin_remove_exempt' and user_id == ADMIN_ID:
        try:
            target = int(text)
            set_user_exempted(target, False)
            await update.message.reply_text(f"✅ تم إلغاء استثناء المستخدم `{target}` (سيتوقف البوت لديه عند التوقف العام).")
            try:
                await context.bot.send_message(chat_id=target, text="⚠️ تم إلغاء استثناء التوقف عن البوت، سيتوقف لديك عند إيقاف البوت العام.")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
        context.user_data.clear()
        return

    # ========== معالجة الحظر ==========
    if action == 'admin_ban_user' and user_id == ADMIN_ID:
        try:
            target = int(text)
            if target == ADMIN_ID:
                await update.message.reply_text("❌ لا يمكن حظر المشرف نفسه.")
                context.user_data.clear()
                return
            cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target,))
            ban_user(target)
            await update.message.reply_text(f"✅ تم حظر المستخدم `{target}` بنجاح.")
            try:
                await context.bot.send_message(chat_id=target, text="⛔ تم حظرك من استخدام هذا البوت.")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
        context.user_data.clear()
        return

    if action == 'admin_unban_user' and user_id == ADMIN_ID:
        try:
            target = int(text)
            unban_user(target)
            await update.message.reply_text(f"✅ تم إلغاء حظر المستخدم `{target}` بنجاح.")
            try:
                await context.bot.send_message(chat_id=target, text="✅ تم إلغاء حظرك، يمكنك استخدام البوت الآن.")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
        context.user_data.clear()
        return

    # ========== معالجة منح وإلغاء صلاحية الانضمام المتعدد ==========
    if action == 'admin_grant_multi_join' and user_id == ADMIN_ID:
        try:
            target = int(text)
            cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target,))
            grant_multi_join_permission(target)
            await update.message.reply_text(f"✅ تم منح صلاحية الانضمام المتعدد للمستخدم `{target}`")
            try:
                await context.bot.send_message(chat_id=target, text="🎉 تم منحك صلاحية استخدام الانضمام المتعدد (أكثر من حساب في نفس الوقت).")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
        context.user_data.clear()
        return

    if action == 'admin_revoke_multi_join' and user_id == ADMIN_ID:
        try:
            target = int(text)
            revoke_multi_join_permission(target)
            await update.message.reply_text(f"✅ تم إلغاء صلاحية الانضمام المتعدد للمستخدم `{target}`")
            try:
                await context.bot.send_message(chat_id=target, text="⚠️ تم إلغاء صلاحية الانضمام المتعدد الخاصة بك.")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
        context.user_data.clear()
        return

    # ========== معالجة الإذاعة ==========
    if action == 'admin_broadcast' and user_id == ADMIN_ID:
        if text == "/cancel":
            context.user_data.clear()
            await update.message.reply_text("❌ تم إلغاء الإذاعة.")
            return
        cursor.execute("SELECT user_id FROM users")
        all_users = cursor.fetchall()
        success = 0
        fail = 0
        await update.message.reply_text(f"🚀 جاري الإرسال إلى {len(all_users)} مستخدم...")
        for (u_id,) in all_users:
            try:
                await context.bot.send_message(chat_id=u_id, text=text)
                success += 1
                await asyncio.sleep(0.05)
            except:
                fail += 1
        context.user_data.clear()
        await update.message.reply_text(f"✅ تم الإرسال: {success} نجاح، {fail} فشل.")
        return

    # ========== معالجة تسجيل الدخول ==========
    if action == 'login_phone':
        context.user_data['temp_phone'] = text
        await update.message.reply_text("⏳ جاري إرسال كود التحقق...\nأرسل الكود فور وصوله:")
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            send_code = await client.send_code_request(text)
            context.user_data['phone_code_hash'] = send_code.phone_code_hash
            context.user_data['client_obj'] = client
            context.user_data['action'] = 'login_otp'
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ: {str(e)}")
            context.user_data.clear()
        return

    if action == 'login_otp':
        phone = context.user_data.get('temp_phone')
        phone_code_hash = context.user_data.get('phone_code_hash')
        client = context.user_data.get('client_obj')
        try:
            await client.sign_in(phone, text, phone_code_hash=phone_code_hash)
            session_str = client.session.save()
            cursor.execute("UPDATE accounts SET is_active=0 WHERE user_id=?", (user_id,))
            cursor.execute("INSERT INTO accounts (user_id, session, phone, is_active) VALUES (?, ?, ?, 1)", (user_id, session_str, phone))
            db.commit()
            await update.message.reply_text(f"🎉 تم إضافة الرقم {phone} وتفعيله.")
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في الكود: {str(e)}")
        finally:
            if client:
                await client.disconnect()
            context.user_data.clear()
        return

    if action == 'switch_account':
        cursor.execute("SELECT id FROM accounts WHERE user_id=? AND phone=?", (user_id, text))
        acc = cursor.fetchone()
        if acc:
            cursor.execute("UPDATE accounts SET is_active=0 WHERE user_id=?", (user_id,))
            cursor.execute("UPDATE accounts SET is_active=1 WHERE user_id=? AND phone=?", (user_id, text))
            db.commit()
            await update.message.reply_text(f"✅ تم تفعيل الرقم: {text}")
        else:
            await update.message.reply_text("❌ هذا الرقم غير موجود في قائمتك.")
        context.user_data.clear()
        return

    if action == 'delete_account':
        cursor.execute("SELECT id, is_active FROM accounts WHERE user_id=? AND phone=?", (user_id, text))
        acc = cursor.fetchone()
        if acc:
            cursor.execute("DELETE FROM accounts WHERE user_id=? AND phone=?", (user_id, text))
            if acc[1] == 1:
                cursor.execute("SELECT id FROM accounts WHERE user_id=? LIMIT 1", (user_id,))
                other = cursor.fetchone()
                if other:
                    cursor.execute("UPDATE accounts SET is_active=1 WHERE id=?", (other[0],))
            db.commit()
            await update.message.reply_text(f"🗑️ تم حذف الرقم {text} نهائياً.")
        else:
            await update.message.reply_text("❌ لم يتم العثور على هذا الرقم.")
        context.user_data.clear()
        return

    await update.message.reply_text("⚠️ زر غير معروف أو حدث خطأ، يرجى استخدام الأزرار المتاحة.")

# ========== معالجة الكولباك الرئيسي ==========
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if is_user_banned(user_id):
        await query.edit_message_text("⛔ تم حظرك من استخدام هذا البوت.")
        return

    # معالجة الإعدادات
    if data.startswith("settings"):
        await settings_callback(update, context)
        return

    # معالجة الباقات
    if data in ["add_package", "delete_package", "back_to_menu"] or data.startswith("del_pkg_") or data == "cancel_delete":
        await package_callback(update, context)
        return

    # معالجة قرارات الشحن
    if data.startswith("approve_charge_") or data.startswith("reject_charge_"):
        await admin_charge_decision(update, context)
        return

    # معالجة الشحن للمستخدمين
    if data.startswith("charge_") or data == "cancel_charge":
        await charge_callback(update, context)
        return
    if data == "transfer_done":
        await transfer_done_callback(update, context)
        return
    if data == "cancel_transfer":
        await cancel_transfer_callback(update, context)
        return

    if data == "cancel":
        await query.edit_message_text("❌ تم الإلغاء.")
        context.user_data.clear()
        return

    # معالجة المجلدات
    if data.startswith("multi_join_folder_"):
        folder_id = int(data.split("_")[3])
        cursor.execute("SELECT folder_name FROM folders WHERE id=? AND user_id=?", (folder_id, user_id))
        res = cursor.fetchone()
        if not res:
            await query.edit_message_text("⚠️ هذا المجلد غير موجود.")
            return
        folder_name = res[0]
        await query.edit_message_text(f"✅ تم اختيار المجلد: **{folder_name}**")
        await start_multi_joining(update, context, user_id, folder_id, folder_name)
        return

    if data.startswith("join_folder_"):
        folder_id = int(data.split("_")[2])
        cursor.execute("SELECT folder_name FROM folders WHERE id=? AND user_id=?", (folder_id, user_id))
        res = cursor.fetchone()
        if not res:
            await query.edit_message_text("⚠️ هذا المجلد غير موجود.")
            return
        folder_name = res[0]
        context.user_data['selected_folder'] = folder_id
        await query.edit_message_text(f"✅ تم اختيار المجلد: **{folder_name}**")
        await start_joining_from_callback(update, context, user_id, folder_id, folder_name)
        return

    if data.startswith("select_folder_"):
        folder_id = int(data.split("_")[2])
        cursor.execute("SELECT folder_name FROM folders WHERE id=? AND user_id=?", (folder_id, user_id))
        res = cursor.fetchone()
        if not res:
            await query.edit_message_text("⚠️ المجلد غير موجود.")
            return
        folder_name = res[0]
        links = get_folder_links(folder_id)
        if not links:
            await query.edit_message_text(f"📁 **{folder_name}**\nلا توجد روابط معلقة.")
            return
        reply = f"📁 **{folder_name}**\nروابط معلقة ({len(links)}):\n\n"
        for idx, (lid, link, status) in enumerate(links, 1):
            reply += f"{idx}. {link}\n"
        await query.edit_message_text(reply, parse_mode="Markdown")
        return

    if data.startswith("delete_folder_"):
        folder_id = int(data.split("_")[2])
        cursor.execute("SELECT folder_name FROM folders WHERE id=? AND user_id=?", (folder_id, user_id))
        res = cursor.fetchone()
        if not res:
            await query.edit_message_text("⚠️ المجلد غير موجود.")
            return
        folder_name = res[0]
        delete_folder_and_links(folder_id)
        await query.edit_message_text(f"🗑️ تم حذف المجلد **{folder_name}** وجميع روابطه.")
        return

# ========== معالجة الصور (لشحن الحساب) ==========
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get('awaiting_charge_photo'):
        await handle_charge_photo(update, context)

# ========== تشغيل البوت ==========
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("🚀 البوت يعمل مع نظام إعدادات البوت المتقدم...")
    app.run_polling()
