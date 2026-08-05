import sqlite3
import asyncio
import re
import logging
from datetime import datetime, timedelta
from telethon import TelegramClient, functions, types
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
    subscription_expiry TEXT
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
db.commit()

# ترقية الجداول القديمة
try:
    cursor.execute("ALTER TABLE users ADD COLUMN subscription_expiry TEXT")
    db.commit()
except sqlite3.OperationalError:
    pass

running_states = {}

def extract_links(text):
    pattern = r"(?:https?://)?(?:t\.me/|telegram\.me/)([a-zA-Z0-9_]+|joinchat/[a-zA-Z0-9_-]+|\+[a-zA-Z0-9_-]+)"
    return re.findall(pattern, text)

# ========== دوال مساعدة للمجلدات ==========
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

# ========== دوال الاشتراك ==========
def get_subscription_expiry(user_id):
    cursor.execute("SELECT subscription_expiry FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return row[0]

def is_subscription_active(user_id):
    if user_id == ADMIN_ID:
        return True
    expiry = get_subscription_expiry(user_id)
    if not expiry:
        return False
    try:
        expiry_date = datetime.fromisoformat(expiry)
        return expiry_date > datetime.now()
    except:
        return False

def can_join(user_id):
    if user_id == ADMIN_ID:
        return True, "✅ المشرف لا يخضع للاشتراك"
    if not is_subscription_active(user_id):
        return False, "❌ ليس لديك اشتراك نشط. تواصل مع المشرف للاشتراك."
    return True, "✅ يمكنك الانضمام (اشتراك نشط)"

def add_subscription(user_id, days):
    expiry = (datetime.now() + timedelta(days=days)).isoformat()
    cursor.execute("UPDATE users SET subscription_expiry=? WHERE user_id=?", (expiry, user_id))
    db.commit()

def remove_subscription(user_id):
    cursor.execute("UPDATE users SET subscription_expiry=NULL WHERE user_id=?", (user_id,))
    db.commit()

# ========== منطق الانضمام ==========
async def join_logic(session_str, link):
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    try:
        await client.connect()
        if "joinchat" in link or "+" in link:
            hash_val = link.split("/")[-1].replace("+", "").strip()
            try:
                await client(functions.messages.ImportChatInviteRequest(hash=hash_val))
                return "SUCCESS", "✅ تم الانضمام بنجاح (رابط خاص)"
            except Exception as e:
                err_str = str(e).lower()
                if "flood" in err_str or "wait" in err_str or "seconds" in err_str or "requests" in err_str:
                    return "RESTRICTED", f"⏳ مقيد مؤقتاً: {str(e)}"
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
            except Exception as e:
                err_str = str(e).lower()
                if "flood" in err_str or "wait" in err_str or "seconds" in err_str or "requests" in err_str:
                    return "RESTRICTED", f"⏳ مقيد مؤقتاً: {str(e)}"
                if "requested to join" in err_str or "user_already_participant" in err_str:
                    return "SUCCESS", "⏳ طلب انضمام مرسل"
                try:
                    channel = await client.get_entity(clean_link)
                    await client(functions.channels.JoinChannelRequest(channel=channel))
                    return "SUCCESS", "✅ تم الانضمام"
                except Exception as inner_e:
                    inner_err_str = str(inner_e).lower()
                    if "flood" in inner_err_str or "wait" in inner_err_str or "seconds" in inner_err_str or "requests" in inner_err_str:
                        return "RESTRICTED", f"⏳ مقيد مؤقتاً: {str(inner_e)}"
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

# ========== دالة الخلفية للانضمام ==========
async def background_join_task(user_id, context, active_acc, delay_time, rest_time_minutes, folder_id, folder_name):
    try:
        join_counter = 0
        local_db = sqlite3.connect("bot_final.db")
        local_cursor = local_db.cursor()

        local_cursor.execute("SELECT id, link FROM links WHERE folder_id=? AND status='pending'", (folder_id,))
        all_links = local_cursor.fetchall()
        total_links = len(all_links)
        
        if not all_links:
            await context.bot.send_message(chat_id=user_id, text="⚠️ لا توجد روابط معلقة في هذا المجلد.")
            return

        for index, (lid, link) in enumerate(all_links, 1):
            if not running_states.get(user_id):
                break

            can, msg = can_join(user_id)
            if not can:
                await context.bot.send_message(chat_id=user_id, text=f"⛔ توقف الانضمام: {msg}")
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

                status, msg = await join_logic(active_acc[0], link)

                if status == "RESTRICTED":
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ الحساب مقيد، استراحة 5 دقائق ثم إعادة المحاولة للرابط: {link}"
                    )
                    for _ in range(300 * 10):
                        if not running_states.get(user_id):
                            break
                        await asyncio.sleep(0.1)
                    if not running_states.get(user_id):
                        break
                    await context.bot.send_message(chat_id=user_id, text="🔄 إعادة محاولة الانضمام...")
                    continue

                local_cursor.execute("UPDATE links SET status=? WHERE id=?", ('completed' if status == "SUCCESS" else 'failed', lid))
                local_db.commit()

                join_counter += 1
                remaining_links = total_links - index
                expiry = get_subscription_expiry(user_id)
                expiry_display = expiry if expiry else "غير مفعل"

                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📁 **المجلد:** {folder_name}\n"
                         f"🔗 **الرابط رقم:** {index} من {total_links}\n"
                         f"📊 **المتبقي في المجلد:** {remaining_links} رابط\n"
                         f"📱 **الرقم:** {active_acc[1]}\n"
                         f"🔗 **الرابط:** {link}\n"
                         f"📌 **النتيجة:** {msg}\n"
                         f"📅 **اشتراك ينتهي:** {expiry_display}",
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

# ========== دوال الأزرار ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name

    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    db.commit()

    context.user_data.clear()

    expiry = get_subscription_expiry(user_id)
    expiry_display = expiry if expiry else "لا يوجد اشتراك"
    if expiry:
        try:
            expiry_date = datetime.fromisoformat(expiry)
            if expiry_date <= datetime.now():
                expiry_display = "منتهي (جدد اشتراكك)"
        except:
            expiry_display = "خطأ في التاريخ"

    keyboard = [
        [KeyboardButton("📱 تسجيل الدخول الجديد"), KeyboardButton("🔗 إرسال روابط")],
        [KeyboardButton("🚀 بدء الانضمام"), KeyboardButton("🛑 إيقاف الانضمام")],
        [KeyboardButton("📱 أرقامي المسجلة"), KeyboardButton("🗑️ حذف رقم مسجل")],
        [KeyboardButton("⏱️ تحديد الوقت"), KeyboardButton("💤 استراحة كل 5 روابط")],
        [KeyboardButton("📊 حالة النظام"), KeyboardButton("🗑️ مسح الروابط")],
        [KeyboardButton("📁 مجلدات الروابط")]
    ]

    # أزرار المشرف
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("⏹️ إيقاف البوت"), KeyboardButton("▶️ تشغيل البوت")])
        keyboard.append([KeyboardButton("👑 لوحة المطور"), KeyboardButton("➕ إضافة اشتراك")])
        keyboard.append([KeyboardButton("➖ حذف اشتراك")])
        keyboard.append([KeyboardButton("📢 إذاعة رسالة عامة")])
        keyboard.append([KeyboardButton("📂 سحب روابط المستخدمين"), KeyboardButton("🗑️ حذف أرشيف الروابط")])
        keyboard.append([KeyboardButton("➕ إضافة قناة")])

    paused_msg = " ⚠️ البوت متوقف حالياً (للمشرف فقط)" if BOT_PAUSED else ""

    await update.message.reply_text(
        f"🙋‍♂️ أهلاً بك يا {name} في بوت الانضمام التلقائي!{paused_msg}\n\n"
        f"💳 معرفك: `{user_id}`\n"
        f"📅 **الاشتراك:** {expiry_display}\n\n"
        f"📋 البوت يعمل بنظام الاشتراك.\n"
        f"اختر من الأزرار:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )

# ========== معالجة إضافة قناة ==========
async def handle_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ هذه الخاصية للمشرف فقط.")
        return

    cursor.execute("SELECT session, phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
    acc = cursor.fetchone()
    if not acc:
        await update.message.reply_text("❌ يجب عليك تسجيل الدخول بحساب تيليجرام أولاً (استخدم زر تسجيل الدخول الجديد).")
        return

    await update.message.reply_text(
        "📥 **أرسل رابط القناة** التي تريد استخراج الروابط منها.\n\n"
        "📌 أمثلة على الروابط المقبولة:\n"
        "• `https://t.me/username` (قناة عامة)\n"
        "• `@username` (معرف القناة)\n"
        "• `https://t.me/+abc123` (رابط دعوة خاص)\n"
        "• `https://t.me/joinchat/abc123` (رابط دعوة قديم)\n\n"
        "⚠️ **ملاحظة هامة:** يجب أن يكون البوت (`@userbot`) **مشرفاً (Admin)** في القناة حتى يتمكن من جلب جميع الروابط."
    )
    context.user_data['action'] = 'admin_add_channel'

# ========== معالجة رابط القناة (جلب جميع الروابط) ==========
async def handle_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    text = update.message.text.strip()
    
    # استخراج معرف القناة من أنواع مختلفة من الروابط
    channel_identifier = None
    is_private_invite = False
    invite_hash = None

    # محاولة استخراج المعرف من الرابط
    if text.startswith('@'):
        channel_identifier = text[1:]
    elif 't.me/joinchat/' in text:
        # رابط دعوة قديم
        invite_hash = text.split('t.me/joinchat/')[-1].split('/')[0].split('?')[0]
        is_private_invite = True
    elif 't.me/+' in text:
        # رابط دعوة خاص
        invite_hash = text.split('t.me/+')[-1].split('/')[0].split('?')[0]
        is_private_invite = True
    elif 't.me/' in text:
        parts = text.split('t.me/')
        if len(parts) > 1:
            identifier = parts[1].split('/')[0].split('?')[0]
            if identifier.startswith('+'):
                invite_hash = identifier[1:]
                is_private_invite = True
            else:
                channel_identifier = identifier
    elif text.isdigit():
        channel_identifier = int(text)

    if not channel_identifier and not is_private_invite:
        await update.message.reply_text(
            "❌ لم أستطع التعرف على رابط القناة.\n"
            "تأكد من إرسال الرابط بصيغة:\n"
            "• `https://t.me/username`\n"
            "• `@username`\n"
            "• `https://t.me/+abc123` (للقنوات الخاصة)"
        )
        return

    cursor.execute("SELECT session, phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
    acc = cursor.fetchone()
    if not acc:
        await update.message.reply_text("❌ لا يوجد حساب نشط. سجل الدخول أولاً.")
        return

    session_str, phone = acc
    await update.message.reply_text(f"🔄 جاري الاتصال بالقناة...")

    try:
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()

        # محاولة الحصول على الكيان
        entity = None
        try:
            if is_private_invite and invite_hash:
                # محاولة الانضمام إلى القناة الخاصة عبر رابط الدعوة
                try:
                    updates = await client(functions.messages.ImportChatInviteRequest(hash=invite_hash))
                    if updates.chats:
                        entity = updates.chats[0]
                        await update.message.reply_text(f"✅ تم الانضمام إلى القناة الخاصة بنجاح.")
                    else:
                        # ربما هو عضو بالفعل، نحاول جلب الكيان
                        entity = await client.get_entity(text)
                except Exception as e:
                    # قد يكون عضو بالفعل، نحاول جلب الكيان مباشرة
                    try:
                        entity = await client.get_entity(text)
                    except:
                        await update.message.reply_text(f"❌ لا يمكن الوصول إلى القناة الخاصة: {str(e)[:100]}\nتأكد من أن البوت مشرف في القناة.")
                        await client.disconnect()
                        return
            else:
                entity = await client.get_entity(channel_identifier)
        except Exception as e:
            await update.message.reply_text(f"❌ لا يمكن العثور على القناة: {str(e)[:100]}")
            await client.disconnect()
            return

        if not entity:
            await update.message.reply_text("❌ لم يتم العثور على القناة.")
            await client.disconnect()
            return

        # ========== التحقق من أن البوت أدمن في القناة ==========
        bot_me = await client.get_me()
        bot_username = bot_me.username
        if not bot_username:
            bot_username = f"@{bot_me.first_name or 'bot'}"

        # محاولة جلب قائمة المشرفين للتحقق
        try:
            admins = await client.get_participants(entity, filter=types.ChannelParticipantsAdmins())
            bot_is_admin = False
            for admin in admins:
                if admin.id == bot_me.id:
                    bot_is_admin = True
                    break
            
            if not bot_is_admin:
                await update.message.reply_text(
                    f"❌ **البوت ليس أدمن في القناة!**\n\n"
                    f"يجب إضافة البوت (`@{bot_username}`) كـ **أدمن** في القناة أولاً.\n"
                    f"لا يمكن جلب الروابط بدون صلاحية الأدمن.\n\n"
                    f"بعد إضافة البوت كأدمن، أعد إرسال رابط القناة مرة أخرى."
                )
                await client.disconnect()
                return
        except Exception as e:
            # إذا لم نتمكن من جلب المشرفين، قد يكون البوت ليس أدمن
            await update.message.reply_text(
                f"❌ **التحقق من صلاحيات البوت فشل!**\n\n"
                f"يجب إضافة البوت (`@{bot_username}`) كـ **أدمن** في القناة.\n"
                f"خطأ: {str(e)[:100]}\n\n"
                f"بعد إضافة البوت كأدمن، أعد إرسال رابط القناة مرة أخرى."
            )
            await client.disconnect()
            return

        # ========== جلب جميع الروابط من القناة ==========
        await update.message.reply_text("📥 جاري جلب جميع الرسائل من القناة... قد يستغرق وقتاً طويلاً إذا كانت القناة كبيرة.")

        all_links = []
        offset_id = 0
        limit = 100
        total_messages = 0
        links_found = 0

        while True:
            try:
                messages = await client.get_messages(entity, limit=limit, offset_id=offset_id)
                if not messages:
                    break
                
                for msg in messages:
                    total_messages += 1
                    if total_messages % 100 == 0:
                        await update.message.reply_text(f"🔄 تم جلب {total_messages} رسالة حتى الآن...")

                    if msg.text:
                        found = extract_links(msg.text)
                        if found:
                            all_links.extend(found)
                            links_found += len(found)
                    if msg.caption:
                        found = extract_links(msg.caption)
                        if found:
                            all_links.extend(found)
                            links_found += len(found)
                    
                    offset_id = msg.id
                
                if len(messages) < limit:
                    break
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                await update.message.reply_text(f"⚠️ توقف الجلب مؤقتاً: {str(e)[:100]}")
                break

        await client.disconnect()

        if not all_links:
            await update.message.reply_text(
                f"⚠️ لم يتم العثور على أي روابط في القناة.\n"
                f"📊 عدد الرسائل التي تم فحصها: {total_messages}"
            )
            return

        # إزالة التكرارات
        all_links = list(dict.fromkeys(all_links))
        total_links = len(all_links)

        # إنشاء مجلد جديد للمشرف
        folder_id = create_folder(user_id)
        cursor.execute("SELECT folder_name FROM folders WHERE id=?", (folder_id,))
        folder_name = cursor.fetchone()[0]

        # إضافة الروابط إلى المجلد
        added = 0
        for link in all_links:
            cursor.execute("INSERT INTO links (user_id, folder_id, link) VALUES (?, ?, ?)", (user_id, folder_id, link))
            added += 1
        db.commit()

        await update.message.reply_text(
            f"✅ **تم استخراج وإضافة الروابط بنجاح!**\n\n"
            f"📊 **إحصائيات:**\n"
            f"• عدد الرسائل التي تم فحصها: {total_messages}\n"
            f"• عدد الروابط المستخرجة (قبل إزالة التكرارات): {links_found}\n"
            f"• عدد الروابط الفريدة المضافة: {added}\n"
            f"• 📁 تم حفظها في مجلد جديد: **{folder_name}**\n\n"
            f"يمكنك الآن استخدام زر (بدء الانضمام) لاستخدامها."
        )

    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

# ========== معالجة سحب روابط المستخدمين (للمشرف فقط) ==========
async def fetch_user_links_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ هذه الخاصية للمشرف فقط.")
        return

    cursor.execute("""
        SELECT users.user_id, COUNT(links.id) 
        FROM users 
        LEFT JOIN links ON users.user_id = links.user_id 
        GROUP BY users.user_id
    """)
    data = cursor.fetchall()
    
    if not data:
        await update.message.reply_text("📂 لا توجد أي روابط مسجلة في البوت حالياً.")
        return
    
    reply = "📂 **إحصائيات المستخدمين والروابط:**\n\n"
    for uid, count in data:
        reply += f"• المستخدم: `{uid}` → عدد الروابط: ({count})\n"
    
    reply += "\n👇 أرسل معرف المستخدم (User ID) الذي تريد سحب روابطه."
    
    await update.message.reply_text(reply, parse_mode="Markdown")
    context.user_data['action'] = 'admin_fetch_user_links'

# ========== معالجة سحب روابط المستخدم (بعد إرسال المعرف) ==========
async def handle_fetch_user_links(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid):
    try:
        cursor.execute("""
            SELECT link, status, folder_id 
            FROM links 
            WHERE user_id=?
            ORDER BY folder_id, id
        """, (target_uid,))
        user_links = cursor.fetchall()
        
        if not user_links:
            await update.message.reply_text(f"⚠️ لا توجد أي روابط مسجلة للمستخدم `{target_uid}`", parse_mode="Markdown")
            return
        
        folders = {}
        for link, status, folder_id in user_links:
            if folder_id not in folders:
                cursor.execute("SELECT folder_name FROM folders WHERE id=?", (folder_id,))
                folder_name = cursor.fetchone()
                folders[folder_id] = {
                    'name': folder_name[0] if folder_name else f"مجلد {folder_id}",
                    'links': []
                }
            folders[folder_id]['links'].append((link, status))
        
        total_links = len(user_links)
        msg = f"📂 **روابط المستخدم `{target_uid}`**\n"
        msg += f"📊 **إجمالي الروابط:** {total_links}\n\n"
        
        for folder_id, folder_data in folders.items():
            msg += f"📁 **{folder_data['name']}**\n"
            for link, status in folder_data['links']:
                status_icon = "✅" if status == 'completed' else ("❌" if status == 'failed' else "⏳")
                formatted_link = link if ("http://" in link or "https://" in link) else f"https://t.me/{link}"
                msg += f"{status_icon} {formatted_link}\n"
            msg += "\n"
        
        if len(msg) > 4000:
            parts = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
            for part in parts:
                await update.message.reply_text(part, parse_mode="Markdown", disable_web_page_preview=False)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=False)
            
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

# ========== معالجة الكولباك ==========
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "cancel":
        await query.edit_message_text("❌ تم الإلغاء.")
        context.user_data.clear()
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
            formatted_link = link if ("http://" in link or "https://" in link) else f"https://t.me/{link}"
            reply += f"{idx}. {formatted_link}\n"
        await query.edit_message_text(reply, parse_mode="Markdown", disable_web_page_preview=False)
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

async def start_joining_from_callback(update, context, user_id, folder_id, folder_name):
    links = get_folder_links(folder_id)
    if not links:
        await update.effective_message.reply_text("⚠️ لا توجد روابط معلقة في هذا المجلد.")
        return

    can, msg = can_join(user_id)
    if not can:
        await update.effective_message.reply_text(f"⛔ لا يمكن بدء الانضمام: {msg}")
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

# ========== دالة البداية المباشرة ==========
async def start_joining(update, context, user_id, folder_id, folder_name):
    links = get_folder_links(folder_id)
    if not links:
        await update.message.reply_text("⚠️ لا توجد روابط معلقة في هذا المجلد.")
        return

    can, msg = can_join(user_id)
    if not can:
        await update.message.reply_text(f"⛔ لا يمكن بدء الانضمام: {msg}")
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

# ========== دالة معالجة الرسائل الرئيسية ==========
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_PAUSED
    user_id = update.effective_user.id
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    action = context.user_data.get('action')

    if BOT_PAUSED and user_id != ADMIN_ID:
        await update.message.reply_text("⛔ البوت متوقف حالياً، يرجى التواصل مع المشرف.")
        return

    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    db.commit()

    if text == "/start":
        return await start(update, context)

    # ========== أزرار المشرف ==========
    if text == "⏹️ إيقاف البوت" and user_id == ADMIN_ID:
        BOT_PAUSED = True
        await update.message.reply_text("✅ تم إيقاف البوت.")
        return

    if text == "▶️ تشغيل البوت" and user_id == ADMIN_ID:
        BOT_PAUSED = False
        await update.message.reply_text("✅ تم تشغيل البوت.")
        return

    if text == "➕ إضافة اشتراك" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد إضافة اشتراك له:")
        context.user_data['action'] = 'admin_add_subscription_id'
        return

    if text == "➖ حذف اشتراك" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل معرف المستخدم (User ID) الذي تريد حذف اشتراكه:")
        context.user_data['action'] = 'admin_remove_subscription'
        return

    if text == "📂 سحب روابط المستخدمين" and user_id == ADMIN_ID:
        await fetch_user_links_admin(update, context)
        return

    if text == "➕ إضافة قناة" and user_id == ADMIN_ID:
        await handle_add_channel(update, context)
        return

    # ========== معالجة رابط القناة (عند إرساله) ==========
    if action == 'admin_add_channel' and user_id == ADMIN_ID:
        await handle_channel_link(update, context)
        return

    # ========== باقي الأزرار العامة ==========
    if text == "📁 مجلدات الروابط":
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

    if text == "🚀 بدء الانضمام":
        if running_states.get(user_id) == True:
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

    if text == "🛑 إيقاف الانضمام":
        running_states[user_id] = False
        await update.message.reply_text("⏳ جاري الإيقاف...")
        return

    if text == "📊 حالة النظام":
        cursor.execute("SELECT delay, rest_time FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if row:
            delay, rest = row
        else:
            delay, rest = 10, 5

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

        cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND status='pending'", (user_id,))
        pending_links = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND status='completed'", (user_id,))
        completed_links = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND status='failed'", (user_id,))
        failed_links = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM folders WHERE user_id=?", (user_id,))
        total_folders = cursor.fetchone()[0]

        is_running = "🔥 يعمل" if running_states.get(user_id) else "⚪ متوقف"

        expiry = get_subscription_expiry(user_id)
        expiry_display = expiry if expiry else "لا يوجد"
        if expiry:
            try:
                expiry_date = datetime.fromisoformat(expiry)
                if expiry_date <= datetime.now():
                    expiry_display = "منتهي (جدد)"
                else:
                    expiry_display = expiry
            except:
                expiry_display = "خطأ"

        await update.message.reply_text(
            f"📋 **حالة النظام التفصيلية**\n\n"
            f"• **حالة البوت:** {is_running}\n"
            f"• **الرقم النشط:** {active_phone}\n"
            f"• **الوقت بين الروابط:** {delay} ثانية\n"
            f"• **استراحة كل 5 روابط:** {rest} دقائق\n"
            f"• **المجلد المختار:** {folder_name}\n"
            f"📅 **الاشتراك:** {expiry_display}\n\n"
            f"📊 **إحصائيات الروابط:**\n"
            f"• 📁 **عدد المجلدات:** {total_folders}\n"
            f"• ⏳ **روابط معلقة:** {pending_links}\n"
            f"• ✅ **روابط منضم لها:** {completed_links}\n"
            f"• ❌ **روابط فاشلة:** {failed_links}"
        )
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
        current = row[0] if row else 10
        await update.message.reply_text(f"⏱️ الوقت الحالي: {current} ثانية.\nأرسل الوقت الجديد (ثواني):")
        context.user_data['action'] = 'set_delay'
        return

    if text == "💤 استراحة كل 5 روابط":
        cursor.execute("SELECT rest_time FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        current = row[0] if row else 5
        await update.message.reply_text(f"💤 وقت الاستراحة الحالي: {current} دقائق.\nأرسل الوقت الجديد (دقائق):")
        context.user_data['action'] = 'set_rest_time'
        return

    # ========== أزرار المطور الأخرى ==========
    if text == "👑 لوحة المطور" and user_id == ADMIN_ID:
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM accounts")
        total_accounts = cursor.fetchone()[0]
        cursor.execute("""
            SELECT users.user_id, users.subscription_expiry
            FROM users
        """)
        details = cursor.fetchall()
        admin_reply = f"👑 **لوحة المطور**\n👥 المستخدمين: {total_users}\n📱 الأرقام: {total_accounts}\n\n"
        admin_reply += "📋 **المستخدمون والاشتراكات:**\n"
        for u_id, expiry in details:
            expiry_str = expiry if expiry else "بدون"
            admin_reply += f"• {u_id} → اشتراك: {expiry_str}\n"
        await update.message.reply_text(admin_reply, parse_mode="Markdown")
        return

    if text == "📢 إذاعة رسالة عامة" and user_id == ADMIN_ID:
        await update.message.reply_text("أرسل الرسالة للإذاعة (أو /cancel):")
        context.user_data['action'] = 'admin_broadcast'
        return

    if text == "🗑️ حذف أرشيف الروابط" and user_id == ADMIN_ID:
        cursor.execute("DELETE FROM links")
        cursor.execute("DELETE FROM folders")
        db.commit()
        await update.message.reply_text("🗑️ تم حذف جميع المجلدات والروابط.")
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

    # ========== معالجة إضافة اشتراك ==========
    if action == 'admin_add_subscription_id' and user_id == ADMIN_ID:
        try:
            target_uid = int(text)
            context.user_data['target_subscription_uid'] = target_uid
            await update.message.reply_text(f"🔹 المستهدف: `{target_uid}`\nأرسل عدد الأيام (مثال: 30):")
            context.user_data['action'] = 'admin_add_subscription_days'
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
            context.user_data.clear()
        return

    if action == 'admin_add_subscription_days' and user_id == ADMIN_ID:
        try:
            days = int(text)
            if days <= 0:
                raise ValueError
            target = context.user_data.get('target_subscription_uid')
            if not target:
                await update.message.reply_text("❌ حدث خطأ، حاول مجدداً.")
                context.user_data.clear()
                return
            cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target,))
            add_subscription(target, days)
            await update.message.reply_text(f"✅ تم إضافة اشتراك لمدة {days} يوم للمستخدم `{target}`")
            try:
                await context.bot.send_message(chat_id=target, text=f"🎉 تم تفعيل اشتراكك لمدة {days} يوم. يمكنك الآن الانضمام إلى الروابط.")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال عدد صحيح للأيام.")
        context.user_data.clear()
        return

    # ========== معالجة حذف اشتراك ==========
    if action == 'admin_remove_subscription' and user_id == ADMIN_ID:
        try:
            target_uid = int(text)
            cursor.execute("SELECT subscription_expiry FROM users WHERE user_id=?", (target_uid,))
            row = cursor.fetchone()
            if not row or not row[0]:
                await update.message.reply_text(f"⚠️ المستخدم `{target_uid}` ليس لديه اشتراك نشط.")
                context.user_data.clear()
                return
            remove_subscription(target_uid)
            await update.message.reply_text(f"✅ تم حذف اشتراك المستخدم `{target_uid}`")
            try:
                await context.bot.send_message(chat_id=target_uid, text="⚠️ لقد قمت بعمل خطأ، تم حذف اشتراكك. يرجى التواصل مع المشرف.")
            except:
                pass
        except ValueError:
            await update.message.reply_text("❌ معرف غير صحيح.")
        context.user_data.clear()
        return

    # ========== معالجة سحب روابط المستخدم (للمشرف) ==========
    if action == 'admin_fetch_user_links' and user_id == ADMIN_ID:
        try:
            target_uid = int(text)
            await handle_fetch_user_links(update, context, target_uid)
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال معرف رقمي صحيح.")
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

# ========== تشغيل البوت ==========
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("🚀 البوت يعمل مع ميزة إضافة القناة (يدعم الروابط الخاصة ويشترط أن يكون البوت أدمن)...")
    app.run_polling()
