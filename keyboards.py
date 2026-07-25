from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_keyboard(user_id, admin_id):
    keyboard = [
        [KeyboardButton("🚀 بدء الانضمام"), KeyboardButton("🛑 إيقاف الانضمام")],
        [KeyboardButton("🔗 إرسال روابط"), KeyboardButton("🗑️ مسح الروابط")],
        [KeyboardButton("📱 تسجيل الدخول الجديد"), KeyboardButton("📱 أرقامي المسجلة")],
        [KeyboardButton("🗑️ حذف رقم مسجل"), KeyboardButton("📊 حالة النظام")],
        [KeyboardButton("⏱️ الوقت بين الانضمامات"), KeyboardButton("💤 استراحة كل 5 روابط")],
        [KeyboardButton("💎 شحن النقاط")]
    ]
    
    if user_id == admin_id:
        keyboard.append([KeyboardButton("👑 لوحة المطور"), KeyboardButton("🔋 شحن نقاط لمعلم")])
        keyboard.append([KeyboardButton("📢 إذاعة رسالة عامة")])
        
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
