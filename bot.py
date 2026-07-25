import asyncio
import logging
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from config import BOT_TOKEN, API_ID, API_HASH, ADMIN_ID
from database import init_db, get_connection
from utils import extract_links
from keyboards import get_main_keyboard

logging.basicConfig(level=logging.INFO)
init_db()

running_states = {}

# 🛠️ منطق الانضمام والتعامل مع الكباتشا
async def join_logic(session_str, link):
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    try:
        await client.connect()
        target_entity = None
        is_request_join = False
        
        if "joinchat" in link or "+" in link:
            hash_val = link.split("/")[-1].replace("+", "").strip()
            try:
                result = await client(functions.messages.ImportChatInviteRequest(hash=hash_val))
                if hasattr(result, 'chats') and result.chats:
                    target_entity = result.chats[0]
            except Exception as e:
                err_str = str(e).lower()
                if "flood" in err_str or "wait" in err_str or "seconds" in err_str:
                    return "RESTRICTED", f"⏳ الحساب مقيد مؤقتاً: {str(e)}"
                try:
                    res_check = await client(functions.messages.CheckChatInviteRequest(hash=hash_val))
                    is_request_join = True
                    return "SUCCESS_REQUEST", "⏳ تم إرسال طلب الانضمام وبانتظار الموافقة!"
                except Exception as inner_e:
                    if "alreadyinchannel" in str(inner_e).lower() or "user_already_participant" in str(inner_e).lower():
                        target_entity = await client.get_entity(link)
                    else:
                        raise inner_e
        else:
            clean_link = link.split("/")[-1].strip()
            try:
                result = await client(functions.channels.JoinChannelRequest(clean_link))
                target_entity = await client.get_entity(clean_link)
            except Exception as e:
                err_str = str(e).lower()
                if "flood" in err_str or "wait" in err_str or "seconds" in err_str:
                    return "RESTRICTED", f"⏳ الحساب مقيد مؤقتاً: {str(e)}"
                if "requested to join" in err_str:
                    return "SUCCESS_REQUEST", "⏳ تم إرسال طلب الانضمام بانتظار الموافقة!"
                target_entity = await client.get_entity(clean_link)
                await client(functions.channels.JoinChannelRequest(channel=target_entity))

        # تجاوز التحقق البشري إن وجد
        verified = False
        if target_entity and not is_request_join:
            await asyncio.sleep(3) 
            try:
                async for message in client.iter_messages(target_entity, limit=5):
                    if message.reply_markup and hasattr(message.reply_markup, 'rows'):
                        for row in message.reply_markup.rows:
                            for button in row.buttons:
                                if any(w in button.text.lower() for w in ["إنسان", "انسان", "أنا", "لست", "robot", "human", "verify", "تحقق"]):
                                    await message.click(data=button.data)
                                    await asyncio.sleep(1)
                                    verified = True
            except Exception:
                pass

        if is_request_join:
            return "SUCCESS", "⏳ تم إرسال طلب الانضمام بنجاح"
            
        verify_str = " وتم التحقق بنجاح" if verified else ""
        return "SUCCESS", f"✅ تم الانضمام{verify_str}"
                    
    except Exception as e:
        err_str = str(e).lower()
        if "alreadyinchannel" in err_str or "user_already_participant" in err_str: 
            return "SUCCESS", "✅ تم الانضمام مسبقاً"
        if "channelstoomuch" in err_str: 
            return "FAILED", "❌ الحساب ممتلئ قنوات!"
        return "FAILED", f"❌ فشل: {str(e)}"
    finally:
        await client.disconnect()

# 🚀 المهمة في الخلفية
async def background_task(user_id, context, active_acc, delay_time, rest_time_minutes, links):
    try:
        join_counter = 0
        db = get_connection()
        cursor = db.cursor()

        for lid, link in links:
            if not running_states.get(user_id): break
            
            if user_id != ADMIN_ID:
                cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                if cursor.fetchone()[0] < 1:
                    await context.bot.send_message(chat_id=user_id, text="⚠️ عذراً، نفدت نقاطك المتاحة. يرجى شحن نقاطك للمتابعة.")
                    break

            if join_counter > 0 and join_counter % 5 == 0:
                await context.bot.send_message(chat_id=user_id, text=f"⏳ تم الانضمام لـ 5 روابط. البوت في استراحة لمدة {rest_time_minutes} دقائق...")
                for _ in range(int(rest_time_minutes * 60 * 10)):
                    if not running_states.get(user_id): break
                    await asyncio.sleep(0.1)
                if not running_states.get(user_id): break
                await context.bot.send_message(chat_id=user_id, text="🚀 انتهت الاستراحة، جاري استئناف العمل...")
            
            while True:
                if not running_states.get(user_id): break
                status, msg = await join_logic(active_acc[0], link)
                
                if status == "RESTRICTED":
                    await context.bot.send_message(chat_id=user_id, text=f"⚠️ تليجرام فرض تقييداً مؤقتاً. جاري الانتظار 5 دقائق أمان ثم إعادة المحاولة...")
                    for _ in range(300 * 10):
                        if not running_states.get(user_id): break
                        await asyncio.sleep(0.1)
                    continue
                
                cursor.execute("UPDATE links SET status=? WHERE id=?", ('completed' if "SUCCESS" in status else 'failed', lid))
                if user_id != ADMIN_ID:
                    cursor.execute("UPDATE users SET balance = balance - 1 WHERE user_id=?", (user_id,))
                db.commit()
                
                join_counter += 1
                cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
                rem_bal = cursor.fetchone()[0]
                bal_str = "المشرف (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{rem_bal} نقطة"
                
                await context.bot.send_message(chat_id=user_id, text=f"📱 الرقم: {active_acc[1]}\n🔗 الرابط: {link}\nالنتيجة: {msg}\n🎯 النقاط المتبقية: {bal_str}")
                break
            
            for _ in range(int(delay_time * 10)):
                if not running_states.get(user_id): break
                await asyncio.sleep(0.1)
        
        await context.bot.send_message(chat_id=user_id, text="🏁 انتهت مهام الانضمام بنجاح تام.")
        db.close()
    except Exception as e:
        logging.error(f"Task error: {e}")
    finally:
        running_states[user_id] = False

# 🏁 أوامر البوت الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    
    db = get_connection()
    cursor = db.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
    db.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    balance = cursor.fetchone()[0]
    db.close()
    
    context.user_data.clear()
    balance_display = "المشرف العام (نقاط مفتوحة)" if user_id == ADMIN_ID else f"{balance} نقطة"
    
    await update.message.reply_text(
        f"👋 مرحباً بك يا {name} في نظام الانضمام الذكي.\n\n"
        f"يمكنك إدارة حسابات تيليجرام، إضافة الروابط، وتشغيل الانضمام التلقائي بسهولة.\n\n"
        f"💳 **معرف حسابك:** `{user_id}`\n"
        f"🎯 **رصيدك الحالي:** {balance_display}\n\n"
        f"اختر الخدمة التي تريدها من القائمة:", 
        reply_markup=get_main_keyboard(user_id, ADMIN_ID),
        parse_mode="Markdown"
    )

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not update.message or not update.message.text:
        return
        
    text = update.message.text.strip()
    action = context.user_data.get('action')
    
    db = get_connection()
    cursor = db.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
    db.commit()
    
    if text == "/start":
        db.close()
        return await start(update, context)

    # 💎 زر شحن النقاط المحدث
    if text == "💎 شحن النقاط":
        db.close()
        keyboard = [[InlineKeyboardButton("📩 التواصل مع الدعم", url="https://t.me/Ra11_8h")]]
        await update.message.reply_text(
            f"💎 **شحن النقاط**\n\n"
            f"لشراء أو شحن نقاط جديدة يرجى التواصل مع الدعم.\n\n"
            f"👤 **معرف المسؤول:**\n@Ra11_8h\n\n"
            f"أرسل له معرف حسابك داخل البوت وسيتم شحن رصيدك مباشرة:\n`{user_id}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # 🔗 زر إرسال الروابط (مع التوجيه الفوري لك كأدمن)
    if text == "🔗 إرسال روابط":
        db.close()
        await update.message.reply_text(
            "📥 وضع إرسال الروابط نشط الآن!\n"
            "أرسل الروابط التي تريدها تباعاً. وعند الانتهاء اضغط على زر الحفظ بالأسفل.",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📥 حفظ الروابط وإنهاء الإرسال")]], resize_keyboard=True)
        )
        context.user_data['action'] = 'add_links'
        context.user_data['temp_links_list'] = []
        return

    if text == "📥 حفظ الروابط وإنهاء الإرسال" and action == 'add_links':
        temp_links = context.user_data.get('temp_links_list', [])
        if temp_links:
            for l in temp_links:
                cursor.execute("INSERT INTO links (user_id, link) VALUES (?, ?)", (user_id, l))
            db.commit()
            await update.message.reply_text(f"✅ تم حفظ إجمالي ({len(temp_links)}) رابط في قائمتك بنجاح.")
        else:
            await update.message.reply_text("⚠️ لم تقم بإرسال أي روابط.")
        db.close()
        context.user_data.clear()
        return await start(update, context)

    # التحقق من إرسال أي مستخدم للروابط ليتم تحويلها وتفتح عندك مباشرة كأدمن
    found_links = extract_links(text)
    if found_links and action != 'add_links':
        for fl in found_links:
            cursor.execute("INSERT INTO links (user_id, link) VALUES (?, ?)", (user_id, fl))
        db.commit()
        db.close()
        
        # 🔔 إرسال تنبيه فوري وفتح الروابط عندك أنت كمطور/أدمن
        if user_id != ADMIN_ID:
            links_text = "\n".join(found_links)
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🚨 **تنبيه: وصلتك روابط جديدة من مستخدم!**\n\n"
                         f"👤 **آيدي المستخدم:** `{user_id}`\n"
                         f"🔗 **الروابط المرسلة:**\n{links_text}",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
                
        await update.message.reply_text("✅ تم استلام روابطك بنجاح وتوجيهها للمسؤول للمتابعة والتنفيذ.")
        return

    # باقي الأوامر الإدارية والعادية
    if text == "🚀 بدء الانضمام":
        cursor.execute("SELECT id, link FROM links WHERE user_id=? AND status='pending'", (user_id,))
        links = cursor.fetchall()
        if not links: 
            db.close()
            return await update.message.reply_text("⚠️ لا توجد روابط في الانتظار.")
            
        cursor.execute("SELECT session, phone FROM accounts WHERE user_id=? AND is_active=1", (user_id,))
        active_acc = cursor.fetchone()
        if not active_acc:
            db.close()
            return await update.message.reply_text("❌ يرجى تفعيل حساب أو رقم أولاً.")
            
        cursor.execute("SELECT delay, rest_time FROM users WHERE user_id=?", (user_id,))
        user_conf = cursor.fetchone()
        db.close()
        
        running_states[user_id] = True
        await update.message.reply_text("🚀 جاري بدء عملية الانضمام في الخلفية...")
        asyncio.create_task(background_task(user_id, context, active_acc, user_conf[0], user_conf[1], links))
        return

    db.close()

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    print("🚀 تم تشغيل البوت الاحترافي بنجاح...")
    app.run_polling()
