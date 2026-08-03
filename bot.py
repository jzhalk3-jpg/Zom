import sqlite3
import asyncio
import re
import logging
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# --- الإعدادات الأساسية ---
BOT_TOKEN = "8969957914:AAF33nKExvFFry5ImvGirDU4oYraLMX3tHc"
API_ID = 39289901
API_HASH = "a5dcef068387dd95705046f910d6cd48"

ADMIN_ID = 5064913080

# متغير عام للتحكم في إيقاف البوت (للمشرف فقط)
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
    balance INTEGER DEFAULT 0
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
    cursor.execute("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass

running_states = {}  # لحفظ حالة الانضمام

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

# ========== دالة الخلفية للانضمام (معدلة) ==========
async def background_join_task(user_id, context, active_acc, delay_time, rest_time_minutes, folder_id, folder_name):
    try:
        join_counter = 0
        local_db = sqlite3.connect("bot_final.db")
        local_cursor = local_db.cursor()

        # جلب جميع الروابط المعلقة في المجلد مع معرفاتها
        local_cursor.execute("SELECT id, link FROM links WHERE folder_id=? AND status='pending'", (folder_id,))
        all_links = local_cursor.fetchall()
        total_links = len(all_links)
        
        if not all_links:
            await context.bot.send_message(chat_id=user_id, text="⚠️ لا توجد روابط معلقة في هذا المجلد.")
            return

        for index, (lid, link) in enumerate(all_links, 1):
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

                # تحديث حالة الرابط
                local_cursor.execute("UPDATE links SET status=? WHERE id=?", ('completed' if status == "SUCCESS" else 'failed', lid))
                if user_id != ADMIN_ID:
                    local_cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id=?", (user_id,))
                local_db.commit()

                join_counter += 1
                
                # حساب الروابط المتبقية
                remaining_links = total_links - index

                local_cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                rem_bal = local_cursor.fetchone()[0]
                bal_str = "المشرف (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{rem_bal} نقطة"

                # إرسال رسالة محسنة تحتوي على معلومات المجلد والرابط
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📁 **المجلد:** {folder_name}\n"
                         f"🔗 **الرابط رقم:** {index} من {total_links}\n"
                         f"📊 **المتبقي:** {remaining_links} رابط\n"
                         f"📱 **الرقم:** {active_acc[1]}\n"
                         f"🔗 **الرابط:** {link}\n"
                         f"📌 **النتيجة:** {msg}\n"
                         f"🎯 **نقاطك:** {bal_str}",
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

    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
    db.commit()

    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    balance = cursor.fetchone()[0]

    context.user_data.clear()

    keyboard = [
        [KeyboardButton("📱 تسجيل الدخول الجديد"), KeyboardButton("🔗 إرسال روابط")],
        [KeyboardButton("🚀 بدء الانضمام"), KeyboardButton("🛑 إيقاف الانضمام")],
        [KeyboardButton("📱 أرقامي المسجلة"), KeyboardButton("🗑️ حذف رقم مسجل")],
        [KeyboardButton("⏱️ تحديد الوقت"), KeyboardButton("💤 استراحة كل 5 روابط")],
        [KeyboardButton("📊 حالة النظام"), KeyboardButton("🗑️ مسح الروابط")],
        [KeyboardButton("🎯 شحن نقاطك"), KeyboardButton("📁 مجلدات الروابط")],
        [KeyboardButton("📂 سحب روابطي")]
    ]

    # أزرار المشرف
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("⏹️ إيقاف البوت"), KeyboardButton("▶️ تشغيل البوت")])
        keyboard.append([KeyboardButton("👑 لوحة المطور"), KeyboardButton("🔋 شحن نقاط لمعلم")])
        keyboard.append([KeyboardButton("📢 إذاعة رسالة عامة")])
        keyboard.append([KeyboardButton("📂 سحب روابط المستخدمين"), KeyboardButton("🗑️ حذف أرشيف الروابط")])

    balance_display = "المشرف العام (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{balance} نقطة"

    paused_msg = " ⚠️ البوت متوقف حالياً (للمشرف فقط)" if BOT_PAUSED else ""

    await update.message.reply_text(
        f"🙋‍♂️ أهلاً بك يا {name} في بوت الانضمام التلقائي!{paused_msg}\n\n"
        f"💳 معرفك: `{user_id}`\n"
        f"🎯 رصيدك: {balance_display}\n\n"
        f"📋 تكلفة الرابط = 1 نقطة.\n"
        f"اختر من الأزرار:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )

# ========== معالجة سحب روابط المستخدم (للمشرف) ==========
async def fetch_user_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    reply += "\n👇 **للمشرف فقط:** أرسل معرف المستخدم (User ID) الذي تريد سحب روابطه."
    
    await update.message.reply_text(reply, parse_mode="Markdown")
    context.user_data['action'] = 'admin_fetch_user_links'

# ========== معالجة سحب روابط المستخدم (بعد إرسال المعرف) ==========
async def handle_fetch_user_links(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid):
    try:
        # جلب جميع روابط المستخدم بجميع حالاتها
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
        
        # تجميع الروابط حسب المجلد
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
        
        # إرسال الروابط مع أزرار قابلة للضغط
        msg = f"📂 **روابط المستخدم `{target_uid}`**\n"
        msg += f"📊 **إجمالي الروابط:** {total_links}\n\n"
        
        # إرسال كل مجلد على حدة
        for folder_id, folder_data in folders.items():
            msg += f"📁 **{folder_data['name']}**\n"
            for link, status in folder_data['links']:
                status_icon = "✅" if status == 'completed' else ("❌" if status == 'failed' else "⏳")
                # جعل الرابط قابل للضغط والنقر
                formatted_link = link if ("http://" in link or "https://" in link) else f"https://t.me/{link}"
                msg += f"{status_icon} {formatted_link}\n"
            msg += "\n"
        
        # تقسيم الرسالة إذا كانت طويلة
        if len(msg) > 4000:
            # تقسيم إلى أجزاء
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

    # ------ اختيار مجلد للانضمام ------
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

    # ------ عرض روابط المجلد ------
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

    # ------ حذف مجلد ------
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

# ========== دالة البداية المباشرة (للمجلد الواحد) ==========
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

# ========== دالة معالجة الرسائل الرئيسية ==========
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_PAUSED
    user_id = update.effective_user.id
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    action = context.user_data.get('action')

    # ---- التحقق من حالة إيقاف البوت ----
    if BOT_PAUSED and user_id != ADMIN_ID:
        await update.message.reply_text("⛔ البوت متوقف حالياً، يرجى التواصل مع المشرف.")
        return

    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
    db.commit()

    if text == "/start":
        return await start(update, context)

    # ========== أزرار المشرف لإيقاف/تشغيل البوت ==========
    if text == "⏹️ إيقاف البوت" and user_id == ADMIN_ID:
        BOT_PAUSED = True
        await update.message.reply_text("✅ تم إيقاف البوت. لن يستجيب لأي أوامر من المستخدمين العاديين.")
        return

    if text == "▶️ تشغيل البوت" and user_id == ADMIN_ID:
        BOT_PAUSED = False
        await update.message.reply_text("✅ تم تشغيل البوت. جميع المستخدمين يمكنهم استخدام البوت الآن.")
        return

    # ========== زر شحن النقاط ==========
    if text == "🎯 شحن نقاطك":
        await update.message.reply_text(
            f"لشحن نقاطك يرجى التواصل على @Ra11_8h\n\nمعرفك: `{user_id}`",
            parse_mode="Markdown"
        )
        return

    # ========== زر مجلدات الروابط ==========
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

    # ========== زر حفظ الروابط وإنهاء الإرسال ==========
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

    # ========== زر إرسال روابط ==========
    if text == "🔗 إرسال روابط":
        await update.message.reply_text(
            "📥 أرسل الروابط تباعاً، ثم اضغط على زر (📥 حفظ الروابط وإنهاء الإرسال).",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📥 حفظ الروابط وإنهاء الإرسال")]], resize_keyboard=True)
        )
        context.user_data['action'] = 'add_links'
        context.user_data['temp_links_list'] = []
        return

    # ========== زر بدء الانضمام ==========
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

    # ========== زر إيقاف الانضمام ==========
    if text == "🛑 إيقاف الانضمام":
        running_states[user_id] = False
        await update.message.reply_text("⏳ جاري الإيقاف...")
        return

    # ========== زر حالة النظام (معدل) ==========
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

        # جلب إحصائيات الروابط
        cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND status='pending'", (user_id,))
        pending_links = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND status='completed'", (user_id,))
        completed_links = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM links WHERE user_id=? AND status='failed'", (user_id,))
        failed_links = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM folders WHERE user_id=?", (user_id,))
        total_folders = cursor.fetchone()[0]

        is_running = "🔥 يعمل" if running_states.get(user_id) else "⚪ متوقف"
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        bal = cursor.fetchone()[0]
        bal_str = "مفتوحة" if user_id == ADMIN_ID else f"{bal} نقطة"

        await update.message.reply_text(
            f"📋 **حالة النظام التفصيلية**\n\n"
            f"• **حالة البوت:** {is_running}\n"
            f"• **الرقم النشط:** {active_phone}\n"
            f"• **الوقت بين الروابط:** {delay} ثانية\n"
            f"• **استراحة كل 5 روابط:** {rest} دقائق\n"
            f"• **المجلد المختار:** {folder_name}\n"
            f"• **رصيدك:** {bal_str}\n\n"
            f"📊 **إحصائيات الروابط:**\n"
            f"• 📁 **عدد المجلدات:** {total_folders}\n"
            f"• ⏳ **روابط معلقة:** {pending_links}\n"
            f"• ✅ **روابط منضم لها:** {completed_links}\n"
            f"• ❌ **روابط فاشلة:** {failed_links}"
        )
        return

    # ========== زر مسح الروابط ==========
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

    # ========== زر سحب روابطي ==========
    if text == "📂 سحب روابطي":
        await fetch_user_links(update, context)
        return

    # ========== زر تسجيل الدخول ==========
    if text == "📱 تسجيل الدخول الجديد":
        await update.message.reply_text("أرسل رقم الهاتف مع رمز الدولة (مثال: +966500000000):")
        context.user_data['action'] = 'login_phone'
        return

    # ========== زر أرقامي المسجلة ==========
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

    # ========== زر حذف رقم مسجل ==========
    if text == "🗑️ حذف رقم مسجل":
        cursor.execute("SELECT phone FROM accounts WHERE user_id=?", (user_id,))
        accounts = cursor.fetchall()
        if not accounts:
            await update.message
