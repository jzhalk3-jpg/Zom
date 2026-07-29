import sqlite3
import asyncio
import re
import logging
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- الإعدادات الأساسية ---
BOT_TOKEN = "8969957914:AAF33nKExvFFry5ImvGirDU4oYraLMX3tHc"
API_ID = 39289901
API_HASH = "a5dcef068387dd95705046f910d6cd48"

# 👑 المطور والمالك الوحيد للنظام
ADMIN_ID = 5064913080

logging.basicConfig(level=logging.INFO)

# --- إعداد قاعدة البيانات الدائمة (SQLite) ---
db = sqlite3.connect("bot_final.db", check_same_thread=False)
cursor = db.cursor()
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
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT, 
    user_id INTEGER, 
    link TEXT, 
    status TEXT DEFAULT 'pending'
)
""")
db.commit()

# ترقية قاعدة البيانات تلقائياً في حال كانت الخانات ناقصة أو قديمة
try:
    cursor.execute("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass

running_states = {}

def extract_links(text):
    pattern = r"(?:https?://)?(?:t\.me/|telegram\.me/)([a-zA-Z0-9_]+|joinchat/[a-zA-Z0-9_-]+|\+[a-zA-Z0-9_-]+)"
    return re.findall(pattern, text)

# 🛠️ منطق الانضمام الأساسي للتلغرام
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
                    return "RESTRICTED", f"⏳ الحساب مقيد مؤقتاً وبحاجة للانتظار: {str(e)}"
                
                if "request" in err_str or "ordered to wait" in err_str or "channelstoomuch" not in err_str:
                    try:
                        await client(functions.messages.CheckChatInviteRequest(hash=hash_val))
                        return "SUCCESS", "⏳ تم إرسال طلب الانضمام وبانتظار موافقة الإدارة!"
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
                return "SUCCESS", "✅ تم الانضمام بنجاح (رابط عام)"
            except Exception as e:
                err_str = str(e).lower()
                if "flood" in err_str or "wait" in err_str or "seconds" in err_str or "requests" in err_str:
                    return "RESTRICTED", f"⏳ الحساب مقيد مؤقتاً وبحاجة للانتظار: {str(e)}"
                
                if "requested to join" in err_str or "user_already_participant" in err_str:
                    return "SUCCESS", "⏳ تم إرسال طلب الانضمام بنجاح وبانتظار موافقة الإدارة!"
                
                try:
                    channel = await client.get_entity(clean_link)
                    await client(functions.channels.JoinChannelRequest(channel=channel))
                    return "SUCCESS", "✅ تم الانضمام بنجاح"
                except Exception as inner_e:
                    inner_err_str = str(inner_e).lower()
                    if "flood" in inner_err_str or "wait" in inner_err_str or "seconds" in inner_err_str or "requests" in inner_err_str:
                        return "RESTRICTED", f"⏳ الحساب مقيد مؤقتاً وبحاجة للانتظار: {str(inner_e)}"
                    if "requested to join" in inner_err_str:
                        return "SUCCESS", "⏳ تم إرسال طلب الانضمام بنجاح وبانتظار موافقة الإدارة!"
                    if "alreadyinchannel" in inner_err_str or "user_already_participant" in inner_err_str: 
                        return "🟢 عضو بالفعل"
                    raise e
                    
    except Exception as e:
        err_str = str(e).lower()
        if "alreadyinchannel" in err_str or "user_already_participant" in err_str: 
            return "🟢 عضو بالفعل"
        if "channelstoomuch" in err_str: 
            return "FAILED", "❌ الحساب ممتلئ قنوات!"
        return "FAILED", f"❌ فشل: {str(e)}"
    finally:
        await client.disconnect()

# 🚀 دالة الخلفية المنفصلة مع الخصم التلقائي (نقطة واحدة لكل رابط)
async def background_join_task(user_id, context, active_acc, delay_time, rest_time_minutes, links):
    try:
        join_counter = 0
        local_db = sqlite3.connect("bot_final.db")
        local_cursor = local_db.cursor()

        for lid, link in links:
            if not running_states.get(user_id): break
            
            # 1. تحقق من النقاط قبل معالجة هذا الرابط (استثناء المشرف والمالك)
            if user_id != ADMIN_ID:
                local_cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                current_bal = local_cursor.fetchone()[0]
                if current_bal < 1:
                    await context.bot.send_message(chat_id=user_id, text="⚠️ توقف النظام! لقد نفدت نقاطك المتاحة، يرجى شحن نقاطك للمتابعة.")
                    break

            # 💤 الاستراحة الدورية (لكل 5 روابط)
            if join_counter > 0 and join_counter % 5 == 0:
                await context.bot.send_message(chat_id=user_id, text=f"⏳ تم الانضمام لـ 5 روابط بنجاح. البوت يدخل الآن في استراحة لمدة {rest_time_minutes} دقائق...")
                for _ in range(int(rest_time_minutes * 60 * 10)):
                    if not running_states.get(user_id): break
                    await asyncio.sleep(0.1)
                if not running_states.get(user_id): break
                await context.bot.send_message(chat_id=user_id, text="🚀 انتهت الاستراحة المحددة، جاري استئناف العمل...")
            
            while True:
                if not running_states.get(user_id): break
                
                status, msg = await join_logic(active_acc[0], link)
                
                if status == "RESTRICTED":
                    await context.bot.send_message(chat_id=user_id, text=f"⚠️ تفاجأنا بطلب انتظار من تليجرام لحسابك.\n⏳ الحساب مقيد حالياً. سأدخل في استراحة أمان لمدة 5 دقائق كاملة، ثم سأعيد المحاولة تلقائياً على نفس الرابط دون توقف: {link}")
                    
                    for _ in range(300 * 10):
                        if not running_states.get(user_id): break
                        await asyncio.sleep(0.1)
                        
                    if not running_states.get(user_id): break
                    await context.bot.send_message(chat_id=user_id, text="🔄 انتهت الـ 5 دقائق، جاري إعادة محاولة الانضمام للرابط الحالي الآن...")
                    continue
                
                # تحديث حالة الرابط
                local_cursor.execute("UPDATE links SET status=? WHERE id=?", ('completed' if status == "SUCCESS" else 'failed', lid))
                
                # 🎯 خصم نقطة واحدة من رصيد المستخدم لكل محاولة انضمام ناجحة
                if user_id != ADMIN_ID:
                    local_cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id=?", (user_id,))
                local_db.commit()
                
                join_counter += 1
                
                # جلب النقاط المتبقية لعرضها في الرسالة
                local_cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                rem_bal = local_cursor.fetchone()[0]
                bal_str = "المشرف (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{rem_bal} نقطة"
                
                await context.bot.send_message(chat_id=user_id, text=f"📱 الرقم: {active_acc[1]}\n🔗 الرابط: {link}\nالنتيجة: {msg}\n🎯 نقاطك المتبقية: {bal_str}")
                break
            
            for _ in range(int(delay_time * 10)):
                if not running_states.get(user_id): break
                await asyncio.sleep(0.1)
        
        if not running_states.get(user_id):
            await context.bot.send_message(chat_id=user_id, text="🛑 تم إيقاف عملية الانضمام فوراً بطلبك.")
        else:
            await context.bot.send_message(chat_id=user_id, text="🏁 انتهت معالجة كافة الروابط للحساب الحالي بنجاح تام.")
            
        local_db.close()
    except Exception as task_err:
        logging.error(f"Error in background task for user {user_id}: {task_err}")
    finally:
        running_states[user_id] = False

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
        [KeyboardButton("🎯 شحن نقاطك")]  # الزر موجود لجميع المستخدمين
    ]
    
    # أزرار لوحة تحكم المطور والمالك (بما فيها الأزرار الجديدة)
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("👑 لوحة المطور"), KeyboardButton("🔋 شحن نقاط لمعلم")])
        keyboard.append([KeyboardButton("📢 إذاعة رسالة عامة")])
        keyboard.append([KeyboardButton("📂 سحب روابط المستخدمين"), KeyboardButton("🗑️ حذف أرشيف الروابط")])
        
    balance_display = "المشرف العام (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{balance} نقطة"
    
    await update.message.reply_text(
        f"🙋‍♂️ أهلاً بك يا {name} في بوت الانضمام التلقائي!\n\n"
        f"💳 **معرف حسابك الرقمي:** `{user_id}`\n"
        f"🎯 **رصيدك الحالي:** {balance_display}\n\n"
        f"📋 (تكلفة الانضمام للرابط الواحد هي نقطة واحدة فقط).\n"
        f"الرجاء اختيار ما تريده من الأزرار المتاحة:", 
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not update.message or not update.message.text:
        return
        
    text = update.message.text.strip()
    action = context.user_data.get('action')
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
    db.commit()
    
    if text == "/start":
        return await start(update, context)

    # 📥 إنهاء إرسال الروابط وحفظها
    if text == "📥 حفظ الروابط وإنهاء الإرسال" and action == 'add_links':
        temp_links = context.user_data.get('temp_links_list', [])
        if not temp_links:
            await update.message.reply_text("⚠️ لم تقم بإرسال أي روابط صالحة لحفظها.")
        else:
            for l in temp_links:
                cursor.execute("INSERT INTO links (user_id, link) VALUES (?, ?)", (user_id, l))
            db.commit()
            await update.message.reply_text(f"🏁 انتهى الإرسال بنجاح!\n✅ تم حفظ إجمالي عدد: ({len(temp_links)}) رابط داخل قائمة الانتظار الخاصة بك بنجاح تام.")
        
        context.user_data.clear()
        return await start(update, context)

    # 🎯 عرض معلومات شحن النقاط لجميع المستخدمين (تم التعديل حسب الطلب)
    if text == "🎯 شحن نقاطك":
        await update.message.reply_text(
            f"لشحن نقاطك يرجى التواصل على اليوزر التالي @Ra11_8h\n\n"
            f"معرف حسابك: `{user_id}`",
            parse_mode="Markdown"
        )
        return

    # 🔋 ميزة شحن النقاط للمستخدمين (خاصة بالمالك فقط)
    if text == "🔋 شحن نقاط لمعلم" and user_id == ADMIN_ID:
        await update.message.reply_text("الرجاء إرسال معرف حساب المستخدم المراد شحن نقاطه (الـ User ID الرقمي):")
        context.user_data['action'] = 'admin_charge_id'
        return

    # 📢 زر بدء إذاعة رسالة جديدة (خاص بالمالك فقط)
    if text == "📢 إذاعة رسالة عامة" and user_id == ADMIN_ID:
        await update.message.reply_text(
            "📢 **وضع الإذاعة نشط الآن!**\n\n"
            "أرسل الآن الرسالة التي تريد إرسالها لجميع مستخدمي البوت (يمكنك إرسال نص، تنسيقات، أو روابط):\n\n"
            "*(أو أرسل /cancel إلغاء الإذاعة)*"
        )
        context.user_data['action'] = 'admin_broadcast'
        return

    # 📂 1. زر سحب روابط المستخدمين (خاص بالمالك فقط)
    if text == "📂 سحب روابط المستخدمين" and user_id == ADMIN_ID:
        cursor.execute("""
            SELECT users.user_id, COUNT(links.id) 
            FROM users 
            LEFT JOIN links ON users.user_id = links.user_id 
            GROUP BY users.user_id
        """)
        users_data = cursor.fetchall()
        
        if not users_data:
            return await update.message.reply_text("⚠️ لا توجد أي حسابات مسجلة أو روابط في قاعدة البيانات حتى الآن.")
            
        report = "📂 **قائمة الحسابات المسجلة وإجمالي روابطهم:**\n\n"
        for u_id, count in users_data:
            report += f"• المستخدم: `{u_id}` | عدد الروابط: ({count} رابط)\n"
            
        report += "\n👇 **الرجاء إرسال (معرف المستخدم - User ID) الخاص بالحساب الذي تريد سحب وعرض روابطه الآن:**"
        await update.message.reply_text(report, parse_mode="Markdown")
        context.user_data['action'] = 'admin_fetch_user_links'
        return

    # 🗑️ 2. زر حذف أرشيف الروابط بالكامل (خاص بالمالك فقط)
    if text == "🗑️ حذف أرشيف الروابط" and user_id == ADMIN_ID:
        cursor.execute("DELETE FROM links")
        db.commit()
        await update.message.reply_text("🗑️ **تم بنجاح!** تم حذف وتفريغ أرشيف جميع روابط المستخدمين المحفوظة في النظام بالكامل.")
        return

    # 👑 لوحة المطور الإدارية
    if text == "👑 لوحة المطور" and user_id == ADMIN_ID:
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM accounts")
        total_accounts = cursor.fetchone()[0]
        cursor.execute("""
            SELECT users.user_id, users.balance, COUNT(accounts.id) 
            FROM users 
            LEFT JOIN accounts ON users.user_id = accounts.user_id 
            GROUP BY users.user_id
        """)
        details = cursor.fetchall()
        
        admin_reply = f"👑 **لوحة تحكم المطور الإدارية** 👑\n\n"
        admin_reply += f"👥 إجمالي المستخدمين للبوت: {total_users}\n"
        admin_reply += f"📱 إجمالي الأرقام المسجلة بالنظام: {total_accounts}\n\n"
        admin_reply += "📋 تفاصيل المستخدمين والنقاط والأرقام:\n"
        for u_id, bal, count in details:
            admin_reply += f"• المستخدم (`{u_id}`): نقاطه ({bal} نقطة) | سجل ({count}) أرقام.\n"
        await update.message.reply_text(admin_reply, parse_mode="Markdown")
        return

    elif text == "🔗 إرسال روابط":
        await update.message.reply_text(
            "📥 وضع الإرسال المفتوح والصامت نشط الآن!\n"
            "قم بنسخ ولصق كافة الرسائل والروابط التي لديك هنا تباعاً وبأي عدد تريد.\n\n"
            "📥 عند انتهائك تماماً من إرسال كل شيء، اضغط على زر **(📥 حفظ الروابط وإنهاء الإرسال)** بالأسفل ليظهر لك التقرير النهائي.",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📥 حفظ الروابط وإنهاء الإرسال")]], resize_keyboard=True)
        )
        context.user_data['action'] = 'add_links'
        context.user_data['temp_links_list'] = []
        return

    elif text == "📱 تسجيل الدخول الجديد":
        await update.message.reply_text("الرجاء إرسال رقم الهاتف الجديد مع رمز الدولة (مثال: +966500000000):")
        context.user_data['action'] = 'login_phone'
        return
    
    elif text == "📱 أرقامي المسجلة":
        cursor.execute("SELECT phone, is_active FROM accounts WHERE user_id=?", (user_id,))
        accounts = cursor.fetchall()
        if not accounts:
            return await update.message.reply_text("❌ ليس لديك أي أرقام مسجلة حالياً. اضغط على (تسجيل الدخول الجديد).")
        
        reply = "📱 الحسابات المرتبطة بك:\n\n"
        for phone, is_active in accounts:
            status = "🟢 [نشط ومستخدم حالياً]" if is_active == 1 else "⚪ [غير نشط]"
            reply += f"• {phone} {status}\n"
        reply += "\n🔄 للتبديل وتفعيل أي رقم, أرسل رقم الهاتف المراد تفعيله مباشرة كاملاً من القائمة:"
        context.user_data['action'] = 'switch_account'
        await update.message.reply_text(reply)
        return

    elif text == "🗑️ حذف رقم مسجل":
        cursor.execute("SELECT phone FROM accounts WHERE user_id=?", (user_id,))
        accounts = cursor.fetchall()
        if not accounts:
            return await update.message.reply_text("❌ لا توجد أرقام مسجلة لحذفها.")
        
        reply = "🗑️ اختر الرقم المراد حذفه نهائياً من النظام وأرسله كاملاً:\n\n"
        for (phone,) in accounts:
            reply += f"• {phone}\n"
        context.user_data['action'] = 'delete_account'
        await update.message.reply_text(reply)
        return

    elif text == "⏱️ تحديد الوقت":
        cursor.execute("SELECT delay FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        current_delay = row[0] if row else 10
        await update.message.reply_text(f"⏱️ الوقت الحالي هو: {current_delay} ثانية.\nأرسل الوقت الجديد بالثواني مباشرة:")
        context.user_data['action'] = 'set_delay'
        return

    elif text == "💤 استراحة كل 5 روابط":
        await update.message.reply_text("كم الوقت الي تحب فيه البوت يسترح بعد الانضمام ل 5 روابط (قم بكتابة عدد الدقائق بالرقم مباشرة)؟")
        context.user_data['action'] = 'set_rest_time'
        return
        
    elif text == "🚀 بدء الانضمام":
        if running_states.get(user_id) == True:
            return await update.message.reply_text("⚠️ النظام قيد العمل بالفعل حالياً الخاص بك!")

        cursor.execute("SELECT id, link FROM links WHERE user_id=? AND status='pending'", (user_id,))
        links = cursor.fetchall()
        if not links: 
            return await update.message.reply_text("⚠️ لا توجد روابط جديدة في الانتظار للعمل عليها.")
            
        # 🧾 التحقق من رصيد النقاط الكافي للمستخدمين
        if user_id != ADMIN_ID:
            cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
            current_balance = cursor.fetchone()[0]
            required_cost = len(links)
            
            if current_balance < required_cost:
                return await update.message.reply_text(
                    f"❌ عذراً! رصيدك من النقاط لا يكفي لتشغيل هذه العملية.\n\n"
                    f"📦 عدد الروابط المطلوب تشغيلها: {len(links)} رابط.\n"
                    f"🎯 النقاط المطلوبة: {required_cost} نقطة\n"
                    f"💳 رصيدك الحالي: {current_balance} نقطة\n\n"
                    f"يرجى التواصل مع المالك @Ra11_8h لشحن رصيدك."
                )

        cursor.execute("SELECT session, phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
        active_acc = cursor.fetchone()
        if not active_acc:
            return await update.message.reply_text("❌ لا يوجد حساب نشط حالياً! يرجى اختيار وتفعيل حساب من قائمة (📱 أرقامي المسجلة) أولاً.")
        
        cursor.execute("SELECT delay, rest_time FROM users WHERE user_id=?", (user_id,))
        user_conf = cursor.fetchone()
        delay_time = user_conf[0] if user_conf else 10
        rest_time_minutes = user_conf[1] if (user_conf and len(user_conf) > 1 and user_conf[1] is not None) else 5
        
        running_states[user_id] = True
        await update.message.reply_text(f"🚀 تم التحقق من النقاط وإطلاق مهمتك بنجاح لـ {len(links)} رابط في الخلفية بالتوازي!")
        
        asyncio.create_task(background_join_task(user_id, context, active_acc, delay_time, rest_time_minutes, links))
        return

    elif text == "🛑 إيقاف الانضمام":
        running_states[user_id] = False
        await update.message.reply_text("⏳ جاري إيقاف عملية الانضمام الخاصة بك فوراً...")
        return

    elif text == "🗑️ مسح الروابط":
        cursor.execute("DELETE FROM links WHERE user_id=?", (user_id,))
        db.commit()
        await update.message.reply_text("🗑️ تم تفريغ ومسح قائمة الروابط.")
        return
        
    elif text == "📊 حالة النظام":
        cursor.execute("SELECT count(*) FROM links WHERE user_id=? AND status='pending'", (user_id,))
        pending = cursor.fetchone()[0]
        cursor.execute("SELECT phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
        active_p = cursor.fetchone()
        active_p = active_p[0] if active_p else "لا يوجد"
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        user_bal = cursor.fetchone()[0]
        
        bal_str = "المشرف (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{user_bal} نقطة"
        is_running = "🔥 يعمل حالياً في الخلفية" if running_states.get(user_id) == True else "⚪ متوقف حالياً"
        
        await update.message.reply_text(
            f"📋 **حالة النظام الحالية الخاصة بك:**\n\n"
            f" وضع العمل: {is_running}\n"
            f"🎯 رصيد نقاطك: {bal_str}\n"
            f"📱 الرقم النشط حالياً: {active_p}\n"
            f"⏳ روابط في الانتظار: {pending}"
        )
        return

    # --- إدارة الخطوات الداخلية والمدخلات الفردية ---
    if action == 'add_links':
        found = extract_links(text)
        if found:
            if 'temp_links_list' not in context.user_data:
                context.user_data['temp_links_list'] = []
            context.user_data['temp_links_list'].extend(found)
        return

    # استقبال آيدي المستخدم وسحب روابطه للمالك
    elif action == 'admin_fetch_user_links' and user_id == ADMIN_ID:
        try:
            target_uid = int(text)
            cursor.execute("SELECT link, status FROM links WHERE user_id=?", (target_uid,))
            user_links = cursor.fetchall()
            
            if not user_links:
                await update.message.reply_text(f"⚠️ لا توجد أي روابط مسجلة للمستخدم ذو المعرف: `{target_uid}`", parse_mode="Markdown")
            else:
                msg_chunk = f"📂 **روابط المستخدم (`{target_uid}`) المسجلة ({len(user_links)} رابط):**\n\n"
                for idx, (lnk, stat) in enumerate(user_links, 1):
                    status_icon = "✅" if stat == 'completed' else ("❌" if stat == 'failed' else "⏳")
                    line = f"{idx}. {status_icon} https://t.me/{lnk if not lnk.startswith('joinchat') and not lnk.startswith('+') else ''}\n"
                    # للتأكد من إرسال رابط قابل للضغط والنقر بشكل صحيح:
                    formatted_link = lnk if ("http://" in lnk or "https://" in lnk) else f"https://t.me/{lnk}"
                    line = f"{idx}. {status_icon} {formatted_link}\n"
                    
                    if len(msg_chunk) + len(line) > 3900:
                        await update.message.reply_text(msg_chunk, parse_mode="Markdown", disable_web_page_preview=True)
                        msg_chunk = ""
                    msg_chunk += line
                
                if msg_chunk:
                    await update.message.reply_text(msg_chunk, parse_mode="Markdown", disable_web_page_preview=True)
                    
        except ValueError:
            await update.message.reply_text("❌ يرجى إدخال آيدي رقمي صحيح للمستخدم.")
        context.user_data.clear()
        return

    # تنفيذ الإذاعة لجميع مستخدمي البوت
    elif action == 'admin_broadcast' and user_id == ADMIN_ID:
        if text == "/cancel":
            context.user_data.clear()
            return await update.message.reply_text("❌ تم إلغاء عملية الإذاعة.")
            
        cursor.execute("SELECT user_id FROM users")
        all_users = cursor.fetchall()
        
        success_count = 0
        fail_count = 0
        
        await update.message.reply_text(f"🚀 جاري إرسال الرسالة إلى ({len(all_users)}) مستخدم، يرجى الانتظار...")
        
        for (u_id,) in all_users:
            try:
                await context.bot.send_message(chat_id=u_id, text=text)
                success_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                fail_count += 1
                
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ **تمت الإذاعة بنجاح!**\n\n"
            f"📤 وصلت إلى: {success_count} مستخدم\n"
            f"❌ فشل الوصول إليهم (حظروا البوت): {fail_count} مستخدم"
        )
        return

    # خطوة المالك الأولى: استقبال آيدي الشخص المراد شحن نقاطه
    elif action == 'admin_charge_id' and user_id == ADMIN_ID:
        try:
            target_user_id = int(text)
            context.user_data['target_charge_id'] = target_user_id
            await update.message.reply_text(f"🔋 تم تحديد المستخدم: `{target_user_id}`\nالآن أرسل عدد النقاط المراد إضافتها (رقم صحيح مثل: 50 أو 100):", parse_mode="Markdown")
            context.user_data['action'] = 'admin_charge_amount'
        except ValueError:
            await update.message.reply_text("❌ عذراً، يجب إدخال آيدي رقمي صحيح.")
            context.user_data.clear()
        return

    # خطوة المالك الثانية: شحن عدد النقاط المحدد في قاعدة البيانات
    elif action == 'admin_charge_amount' and user_id == ADMIN_ID:
        try:
            amount = int(text)
            target_id = context.user_data.get('target_charge_id')
            
            cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (target_id,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target_id))
            db.commit()
            
            cursor.execute("SELECT balance FROM users WHERE user_id=?", (target_id,))
            new_total = cursor.fetchone()[0]
            
            await update.message.reply_text(f"✅ تم إضافة {amount} نقطة بنجاح للحساب `{target_id}`.\n🎯 إجمالي نقاطه الحالية أصبح: {new_total} نقطة", parse_mode="Markdown")
            
            try:
                await context.bot.send_message(chat_id=target_id, text=f"🎉 بشرى سارة! قام مالك المنصة بشحن رصيدك بـ: {amount} نقطة 🎯\n💰 رصيدك الكلي الحالي هو: {new_total} نقطة")
            except Exception:
                pass
                
        except ValueError:
            await update.message.reply_text("❌ عذراً، يجب إرسال رقم صحيح للنقاط (مثال: 50 أو 100).")
        context.user_data.clear()
        return

    elif action == 'switch_account':
        cursor.execute("SELECT id FROM accounts WHERE user_id=? AND phone=?", (user_id, text))
        acc = cursor.fetchone()
        if acc:
            cursor.execute("UPDATE accounts SET is_active=0 WHERE user_id=?", (user_id,))
            cursor.execute("UPDATE accounts SET is_active=1 WHERE user_id=? AND phone=?", (user_id, text))
            db.commit()
            await update.message.reply_text(f"✅ تم تغيير الحساب النشط بنجاح إلى: {text}")
        else:
            await update.message.reply_text("❌ هذا الرقم غير موجود في قائمتك.")
        context.user_data.clear()
        return

    elif action == 'delete_account':
        cursor.execute("SELECT id, is_active FROM accounts WHERE user_id=? AND phone=?", (user_id, text))
        acc = cursor.fetchone()
        if acc:
            cursor.execute("DELETE FROM accounts WHERE user_id=? AND phone=?", (user_id, text))
            if acc[1] == 1:
                cursor.execute("UPDATE accounts SET is_active=1 WHERE id=(SELECT id FROM accounts WHERE user_id=? LIMIT 1)", (user_id,))
            db.commit()
            await update.message.reply_text(f"🗑️ تم حذف الرقم {text} نهائياً.")
        else:
            await update.message.reply_text("❌ لم يتم العثور على هذا الرقم.")
        context.user_data.clear()
        return

    elif action == 'set_delay':
        try:
            new_delay = int(text)
            if new_delay < 1: raise ValueError
            cursor.execute("UPDATE users SET delay=? WHERE user_id=?", (new_delay, user_id))
            db.commit()
            await update.message.reply_text(f"✅ تم تحديث الوقت إلى: {new_delay} ثانية.")
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح.")
        context.user_data.clear()
        return

    elif action == 'set_rest_time':
        try:
            new_rest = int(text)
            if new_rest < 0: raise ValueError
            cursor.execute("UPDATE users SET rest_time=? WHERE user_id=?", (new_rest, user_id))
            db.commit()
            await update.message.reply_text("تم حفظ وقت الاستراحة بنجاح.")
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم دقائق صحيح.")
        context.user_data.clear()
        return

    elif action == 'login_phone':
        context.user_data['temp_phone'] = text
        await update.message.reply_text("⏳ جاري إرسال كود التحقق للحساب...\nأرسل الكود فور وصوله:")
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
            
    elif action == 'login_otp':
        phone = context.user_data.get('temp_phone')
        phone_code_hash = context.user_data.get('phone_code_hash')
        client = context.user_data.get('client_obj')
        try:
            await client.sign_in(phone, text, phone_code_hash=phone_code_hash)
            session_str = client.session.save()
            
            cursor.execute("UPDATE accounts SET is_active=0 WHERE user_id=?", (user_id,))
            cursor.execute("INSERT INTO accounts (user_id, session, phone, is_active) VALUES (?, ?, ?, 1)", (user_id, session_str, phone))
            db.commit()
            await update.message.reply_text(f"🎉 ممتاز! تم إضافة الرقم {phone} وتفعيله بنجاح.")
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في الكود: {str(e)}")
        finally:
            if client: await client.disconnect()
            context.user_data.clear()

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    print("🚀 تم تشغيل البوت المطور بالكامل مع أزرار سحب الأرشيف والحذف...")
    app.run_polling()
