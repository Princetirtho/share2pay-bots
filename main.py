import hashlib
import random
import string
import logging
from datetime import datetime
import sqlite3
import csv
import io
import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler
)

# ---------------- CONFIGURATION ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8879194338:AAG2jzvbLH_EDXuz3pFeuoGWQFmvCZM_w_A")

# Multiple Admin IDs
ADMIN_IDS = [8212595643, 8235339975]

# Conversation States
USERNAME, PASSWORD, TXN_ID, WITHDRAW_PHONE, WITHDRAW_AMOUNT, SUPPORT_MSG, SEARCH_USER, USER_DETAILS, ACTIVATION_METHOD, WITHDRAW_METHOD = range(10)

# Logging Setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ---------------- DATABASE SETUP ----------------
def init_db():
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT,
            referral_code TEXT UNIQUE,
            referred_by INTEGER,
            is_active INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            phone_number TEXT,
            total_referrals INTEGER DEFAULT 0,
            total_withdraw INTEGER DEFAULT 0,
            join_date TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            type TEXT,
            amount INTEGER,
            status TEXT,
            txn_id TEXT,
            phone_number TEXT,
            method TEXT,
            created_at TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_withdraws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            amount INTEGER,
            phone_number TEXT,
            method TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP
        )
    ''')
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'total_withdraw' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN total_withdraw INTEGER DEFAULT 0")
    
    conn.commit()
    conn.close()

init_db()

# Helper Functions
def generate_ref_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user(user_id):
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_all_users():
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, is_active, balance, total_referrals, total_withdraw, join_date FROM users ORDER BY join_date DESC")
    users = cursor.fetchall()
    conn.close()
    return users

def get_user_details(user_id):
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.user_id, u.username, u.is_active, u.balance, u.total_referrals, u.total_withdraw, u.join_date,
               r.username as referred_by_username, u.referral_code
        FROM users u
        LEFT JOIN users r ON u.referred_by = r.user_id
        WHERE u.user_id = ?
    """, (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def search_users(query):
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, is_active, balance, total_referrals, total_withdraw, join_date 
        FROM users 
        WHERE user_id LIKE ? OR username LIKE ?
        ORDER BY join_date DESC
        LIMIT 10
    """, (f'%{query}%', f'%{query}%'))
    users = cursor.fetchall()
    conn.close()
    return users

def export_to_csv():
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    tables = ['users', 'transactions', 'pending_withdraws']
    all_data = {}
    for table in tables:
        cursor.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        all_data[table] = {'columns': columns, 'rows': rows}
    conn.close()
    return all_data

# ---------------- DUMMY HTTP SERVER FOR RENDER ----------------
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# ---------------- REPLY KEYBOARDS ----------------
def get_pending_reply_keyboard():
    keyboard = [
        [KeyboardButton("✅ অ্যাকাউন্ট অ্যাক্টিভেট করুন")],
        [KeyboardButton("❓ কিভাবে করবেন?")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_active_reply_keyboard(user_id):
    keyboard = []
    keyboard.append([KeyboardButton("👤 প্রোফাইল"), KeyboardButton("📤 রেফার")])
    keyboard.append([KeyboardButton("💰 ব্যালেন্স"), KeyboardButton("🆘 সাপোর্ট")])
    keyboard.append([KeyboardButton("💸 উইথড্র"), KeyboardButton("❓ কিভাবে করবেন?")])
    if is_admin(user_id):
        keyboard.append([KeyboardButton("🔐 অ্যাডমিন প্যানেল")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_reply_keyboard():
    keyboard = [
        [KeyboardButton("👥 ইউজার লিস্ট"), KeyboardButton("📊 পরিসংখ্যান")],
        [KeyboardButton("💳 পেন্ডিং পেমেন্ট"), KeyboardButton("📤 পেন্ডিং উইথড্র")],
        [KeyboardButton("🔍 ইউজার খুঁজুন"), KeyboardButton("📋 ইউজার ডিটেইলস")],
        [KeyboardButton("📥 CSV ডাউনলোড"), KeyboardButton("🔄 ডেটাবেস রিস্টোর")],
        [KeyboardButton("🔙 ইউজার মোড")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_payment_method_keyboard():
    keyboard = [
        [KeyboardButton("📱 বিকাশ"), KeyboardButton("💳 নগদ")],
        [KeyboardButton("❌ বাতিল")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ---------------- MAIN START HANDLER ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    if context.args:
        ref_code = context.args[0].replace("ref_", "")
        conn = sqlite3.connect('share2pay.db')
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
        referrer = cursor.fetchone()
        if referrer:
            context.user_data['referred_by'] = referrer[0]
        conn.close()

    if not user:
        await update.message.reply_text(
            "✨ **শেয়ার2পে বটে আপনাকে স্বাগতম!** ✨\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 একটি অ্যাকাউন্ট তৈরি করতে আপনার **ইউজারনেম** লিখুন:\n\n"
            "✅ ইংরেজি অক্ষর ও সংখ্যা ব্যবহার করুন\n"
            "❌ স্পেস বা বিশেষ অক্ষর ব্যবহার করবেন না\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode='Markdown'
        )
        return USERNAME
    
    if user[5] == 1:
        if is_admin(user_id) and context.user_data.get('admin_mode', False):
            await update.message.reply_text(
                "🔐 **অ্যাডমিন প্যানেল**\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 নিচের বাটন থেকে নির্বাচন করুন:\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=get_admin_reply_keyboard(),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "🎉 **স্বাগতম!** 🎉\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 আপনার প্রয়োজনীয় অপশন নির্বাচন করুন:\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=get_active_reply_keyboard(user_id),
                parse_mode='Markdown'
            )
    else:
        await update.message.reply_text(
            "⚠️ **অ্যাকাউন্ট ইনঅ্যাক্টিভ**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 অ্যাকাউন্ট অ্যাক্টিভেট করতে নিচের বাটনে ক্লিক করুন:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=get_pending_reply_keyboard(),
            parse_mode='Markdown'
        )
    return ConversationHandler.END

# ---------------- REGISTRATION HANDLERS ----------------
async def set_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    
    if not username or not re.match(r'^[a-zA-Z0-9_]+$', username):
        await update.message.reply_text(
            "❌ **ভুল ইউজারনেম!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ শুধু ইংরেজি অক্ষর, সংখ্যা ও আন্ডারস্কোর ব্যবহার করুন\n"
            "❌ স্পেস বা বিশেষ অক্ষর ব্যবহার করবেন না\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 আবার ইউজারনেম দিন:"
        )
        return USERNAME
    
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        await update.message.reply_text(
            "❌ **ইউজারনেম নেওয়া আছে!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 এই ইউজারনেমটি ইতিমধ্যে ব্যবহার করা হচ্ছে।\n"
            "📌 অন্য ইউজারনেম দিন:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return USERNAME
    
    context.user_data['username'] = username
    conn.close()
    await update.message.reply_text(
        "🔑 **পাসওয়ার্ড সেট করুন**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 পাসওয়ার্ড দিন (সর্বনিম্ন ৬ অক্ষর):\n"
        "💡 টিপ: বড় ও ছোট অক্ষর, সংখ্যা ব্যবহার করুন\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return PASSWORD

async def set_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if len(password) < 6:
        await update.message.reply_text(
            "❌ **পাসওয়ার্ড ছোট!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 পাসওয়ার্ড অন্তত ৬ অক্ষরের হতে হবে।\n"
            "📝 আবার পাসওয়ার্ড দিন:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return PASSWORD

    user_id = update.effective_user.id
    username = context.user_data.get('username')
    hashed_pwd = hash_password(password)
    ref_code = generate_ref_code()
    referred_by = context.user_data.get('referred_by', None)

    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO users (user_id, username, password, referral_code, referred_by, join_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, username, hashed_pwd, ref_code, referred_by, datetime.now()))
        conn.commit()
        await update.message.reply_text(
            "✅ **অ্যাকাউন্ট তৈরি সফল!** 🎉\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎊 আপনার অ্যাকাউন্ট তৈরি হয়েছে!\n\n"
            "📌 এখন অ্যাকাউন্ট অ্যাক্টিভেট করুন:\n"
            "✅ 'অ্যাকাউন্ট অ্যাক্টিভেট করুন' বাটনে ক্লিক করুন\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=get_pending_reply_keyboard()
        )
    except Exception as e:
        await update.message.reply_text(
            "❌ **সমস্যা হয়েছে!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 আবার /start লিখে চেষ্টা করুন।\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        logging.error(f"Registration error: {e}")
    finally:
        conn.close()
    return ConversationHandler.END

# ---------------- MESSAGE HANDLER ----------------
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ **আপনি নিবন্ধিত নন!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 অনুগ্রহ করে /start লিখে অ্যাকাউন্ট তৈরি করুন।\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return

    # Check if in support conversation - only text messages should go to support
    if context.user_data.get('in_support') and text:
        await receive_support(update, context)
        return

    if is_admin(user_id) and context.user_data.get('admin_mode', False):
        await handle_admin_messages(update, context)
        return

    if text == "❓ কিভাবে করবেন?":
        msg = (
            "📖 **শেয়ার2পে গাইড** 📖\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔹 **অ্যাকাউন্ট অ্যাক্টিভেশন:**\n"
            "• ৩০ টাকা বিকাশ/নগদ পাঠান\n"
            "• 📱 নম্বর: `01572972953`\n"
            "• বিকাশ: ফোন নম্বর অথবা TxnID\n"
            "• নগদ: শুধু ফোন নম্বর\n\n"
            "🔹 **রেফারেল বোনাস:**\n"
            "• প্রতি সফল রেফারেলে ২০ টাকা\n\n"
            "🔹 **উইথড্র:**\n"
            "• সর্বনিম্ন ৬০ টাকা\n"
            "• বিকাশ অথবা নগদে পাবেন\n\n"
            "🔹 **সাপোর্ট:**\n"
            "• যেকোনো সমস্যায় 'সাপোর্ট' বাটন ব্যবহার করুন\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')

    elif text == "👤 প্রোফাইল":
        user_details = get_user_details(user_id)
        if user_details:
            status = "✅ সক্রিয়" if user_details[2] == 1 else "❌ নিষ্ক্রিয়"
            join_date = datetime.strptime(user_details[6], '%Y-%m-%d %H:%M:%S.%f').strftime('%d %b, %Y')
            msg = (
                f"👤 **আপনার প্রোফাইল**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 **ইউজার আইডি:** `{user_details[0]}`\n"
                f"👤 **ইউজারনেম:** {user_details[1]}\n"
                f"⚡ **স্ট্যাটাস:** {status}\n"
                f"💰 **ব্যালেন্স:** {user_details[3]} টাকা\n"
                f"👥 **মোট রেফার:** {user_details[4]} জন\n"
                f"💸 **মোট উইথড্র:** {user_details[5]} টাকা\n"
                f"📅 **জয়েন তারিখ:** {join_date}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            if user_details[7]:
                msg += f"\n👤 **রেফারড বাই:** {user_details[7]}"
            await update.message.reply_text(msg, parse_mode='Markdown')

    elif text == "📤 রেফার":
        if user[5] == 0:
            await update.message.reply_text(
                "⚠️ **অ্যাকাউন্ট নিষ্ক্রিয়!**\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 রেফার লিংক পেতে প্রথমে অ্যাকাউন্ট অ্যাক্টিভেট করুন।\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            return
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user[3]}"
        msg = (
            f"📤 **আপনার রেফারেল লিংক**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"`{ref_link}`\n\n"
            f"🎁 **বোনাস:** প্রতি সফল রেফারেলে **২০ টাকা**!\n"
            f"👥 **মোট রেফার:** {user[8]} জন\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 কপি লিংক", callback_data=f"copy_{ref_link}"),
             InlineKeyboardButton("📤 শেয়ার", switch_inline_query=ref_link)]
        ])
        await update.message.reply_text(msg, reply_markup=keyboard, parse_mode='Markdown')

    elif text == "💰 ব্যালেন্স":
        msg = (
            f"💰 **আপনার ব্যালেন্স**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 **বর্তমান ব্যালেন্স:** {user[6]} টাকা\n"
            f"👥 **রেফার বোনাস:** {user[8] * 20} টাকা\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 **সর্বনিম্ন উইথড্র:** ৬০ টাকা"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
    
    elif text == "🔐 অ্যাডমিন প্যানেল":
        if is_admin(user_id):
            context.user_data['admin_mode'] = True
            await update.message.reply_text(
                "🔐 **অ্যাডমিন প্যানেল**\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 নিচের বাটন থেকে নির্বাচন করুন:\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=get_admin_reply_keyboard()
            )
        else:
            await update.message.reply_text("⛔ **আপনি অ্যাডমিন নন!**")

# ---------------- ADMIN MESSAGE HANDLER ----------------
async def handle_admin_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "👥 ইউজার লিস্ট":
        users = get_all_users()
        if not users:
            await update.message.reply_text("📭 **কোনো ইউজার নেই!**")
            return
        msg = "👥 **ইউজার লিস্ট**\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, user in enumerate(users[:20], 1):
            status = "✅" if user[2] == 1 else "❌"
            join_date = datetime.strptime(user[6], '%Y-%m-%d %H:%M:%S.%f').strftime('%d %b, %Y')
            msg += (
                f"**{i}. ইউজার**\n"
                f"🆔 আইডি: `{user[0]}`\n"
                f"👤 নাম: {user[1]}\n"
                f"⚡ স্ট্যাটাস: {status}\n"
                f"💰 ব্যালেন্স: {user[3]} টাকা\n"
                f"👥 রেফার: {user[4]} জন\n"
                f"💸 উইথড্র: {user[5]} টাকা\n"
                f"📅 জয়েন: {join_date}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            )
        if len(users) > 20:
            msg += f"\n📌 মোট {len(users)} জন। প্রথম ২০ জন দেখানো হয়েছে।"
        await update.message.reply_text(msg, parse_mode='Markdown')

    elif text == "📊 পরিসংখ্যান":
        conn = sqlite3.connect('share2pay.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_active=1")
        active = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(balance) FROM users")
        balance = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM transactions WHERE status='approved'")
        deposits = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM pending_withdraws WHERE status='pending'")
        pending = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(amount) FROM pending_withdraws WHERE status='approved'")
        withdrawn = cursor.fetchone()[0] or 0
        conn.close()
        msg = (
            f"📊 **সার্বিক পরিসংখ্যান**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 **মোট ইউজার:** {total}\n"
            f"✅ **সক্রিয়:** {active}\n"
            f"💰 **মোট ব্যালেন্স:** {balance} টাকা\n"
            f"💵 **মোট ডিপোজিট:** {deposits * 30} টাকা\n"
            f"💸 **মোট উইথড্র:** {withdrawn} টাকা\n"
            f"📌 **পেন্ডিং:** {pending}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')

    elif text == "💳 পেন্ডিং পেমেন্ট":
        conn = sqlite3.connect('share2pay.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, username, txn_id, phone_number, method, created_at FROM transactions WHERE status='pending'")
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("📌 **কোনো পেন্ডিং পেমেন্ট নেই!**")
            return
        for row in rows:
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ অ্যাপ্রুভ", callback_data=f"app_pay_{row[0]}_{row[1]}"),
                 InlineKeyboardButton("❌ রিজেক্ট", callback_data=f"rej_pay_{row[0]}_{row[1]}")]
            ])
            
            method_emoji = "📱" if "বিকাশ" in row[5] else "💳"
            method_name = row[5].replace("📱 ", "").replace("💳 ", "")
            
            if row[4]:
                info = f"📱 ফোন নম্বর: `{row[4]}`"
            else:
                info = f"🧾 TxnID: `{row[3]}`"
            
            await update.message.reply_text(
                f"📥 **নতুন পেমেন্ট**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ইউজার আইডি: `{row[1]}`\n"
                f"👤 ইউজারনেম: {row[2]}\n"
                f"{method_emoji} পেমেন্ট মেথড: {method_name}\n"
                f"{info}\n"
                f"💰 পরিমাণ: ৩০ টাকা\n"
                f"📅 সময়: {row[6]}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=btn,
                parse_mode='Markdown'
            )

    elif text == "📤 পেন্ডিং উইথড্র":
        conn = sqlite3.connect('share2pay.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, username, amount, phone_number, method, created_at FROM pending_withdraws WHERE status='pending'")
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("📌 **কোনো পেন্ডিং উইথড্র নেই!**")
            return
        for row in rows:
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ অ্যাপ্রুভ", callback_data=f"app_w_{row[0]}_{row[1]}_{row[3]}"),
                 InlineKeyboardButton("❌ রিজেক্ট", callback_data=f"rej_w_{row[0]}_{row[1]}_{row[3]}")]
            ])
            
            method_emoji = "📱" if "বিকাশ" in row[5] else "💳"
            method_name = row[5].replace("📱 ", "").replace("💳 ", "")
            
            await update.message.reply_text(
                f"📤 **নতুন উইথড্র**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ইউজার আইডি: `{row[1]}`\n"
                f"👤 ইউজারনেম: {row[2]}\n"
                f"{method_emoji} উইথড্র মেথড: {method_name}\n"
                f"📱 নম্বর: `{row[4]}`\n"
                f"💵 পরিমাণ: {row[3]} টাকা\n"
                f"📅 সময়: {row[6]}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=btn,
                parse_mode='Markdown'
            )

    elif text == "🔍 ইউজার খুঁজুন":
        await update.message.reply_text(
            "🔍 **ইউজার খুঁজুন**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 ইউজার আইডি অথবা ইউজারনেম লিখুন:\n"
            "📌 উদাহরণ: `8212595643` অথবা `john_doe`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return SEARCH_USER

    elif text == "📋 ইউজার ডিটেইলস":
        await update.message.reply_text(
            "👤 **ইউজার ডিটেইলস**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 ইউজার আইডি লিখুন:\n"
            "📌 উদাহরণ: `8212595643`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return USER_DETAILS

    elif text == "📥 CSV ডাউনলোড":
        try:
            data = export_to_csv()
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            for table_name, table_data in data.items():
                writer.writerow([f'=== {table_name.upper()} ==='])
                writer.writerow(table_data['columns'])
                for row in table_data['rows']:
                    writer.writerow(row)
                writer.writerow([])
            
            csv_content = output.getvalue()
            output.close()
            
            file_io = io.BytesIO(csv_content.encode('utf-8-sig'))
            file_io.seek(0)
            
            await update.message.reply_document(
                document=file_io,
                filename=f"database_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                caption="📊 **ডাটাবেস ব্যাকআপ**\n\n"
                       "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                       "✅ ইউজার ডেটা\n"
                       "✅ ট্রানজেকশন ডেটা\n"
                       "✅ পেন্ডিং উইথড্র ডেটা\n\n"
                       "📌 ফাইলটি Excel বা Google Sheets এ খুলতে পারবেন।\n"
                       "━━━━━━━━━━━━━━━━━━━━━━━━"
            )
        except Exception as e:
            logging.error(f"CSV export error: {e}")
            await update.message.reply_text(
                "❌ **CSV ফাইল তৈরি করতে সমস্যা হয়েছে!**\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 আবার চেষ্টা করুন।\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━"
            )

    elif text == "🔄 ডেটাবেস রিস্টোর":
        # ইনলাইন কীবোর্ড তৈরি করুন
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ হ্যাঁ, রিস্টোর করো", callback_data="confirm_restore")],
            [InlineKeyboardButton("❌ না, বাতিল করো", callback_data="cancel_restore")]
        ])
        
        await update.message.reply_text(
            "⚠️ **সতর্কতা!**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "আপনি কি **ডেটাবেস রিস্টোর** করতে চান?\n\n"
            "🔥 **যা হবে:**\n"
            "• সব পুরোনো ডেটা **ডিলিট** হবে\n"
            "• CSV ফাইলের ডেটা **যোগ** হবে\n"
            "• ইউজার, ট্রানজেকশন, উইথড্র - সব **রিপ্লেস** হবে\n\n"
            "⚠️ এই কাজ **বাতিল করা যাবে না!**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "✅ নিশ্চিত হলে নিচের বাটন ক্লিক করুন:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )

    elif text == "🔙 ইউজার মোড":
        context.user_data['admin_mode'] = False
        await update.message.reply_text(
            "👤 **ইউজার মোডে ফিরে গেছেন**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 আপনি এখন সাধারণ ইউজার হিসেবে ব্যবহার করতে পারবেন।\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=get_active_reply_keyboard(user_id)
        )

# ---------------- SEARCH & DETAILS ----------------
async def search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    users = search_users(query)
    if not users:
        await update.message.reply_text(
            f"❌ **ইউজার পাওয়া যায়নি!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 '{query}' এর সাথে মেলে এমন কোনো ইউজার নেই।\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return ConversationHandler.END
    
    msg = "🔍 **সার্চ রেজাল্ট**\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, user in enumerate(users, 1):
        status = "✅" if user[2] == 1 else "❌"
        join_date = datetime.strptime(user[6], '%Y-%m-%d %H:%M:%S.%f').strftime('%d %b, %Y')
        msg += (
            f"**{i}. ইউজার**\n"
            f"🆔 আইডি: `{user[0]}`\n"
            f"👤 নাম: {user[1]}\n"
            f"⚡ স্ট্যাটাস: {status}\n"
            f"💰 ব্যালেন্স: {user[3]} টাকা\n"
            f"👥 রেফার: {user[4]} জন\n"
            f"💸 উইথড্র: {user[5]} টাকা\n"
            f"📅 জয়েন: {join_date}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
    await update.message.reply_text(msg, parse_mode='Markdown')
    return ConversationHandler.END

async def user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ **ভুল ইউজার আইডি!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 শুধু সংখ্যা ব্যবহার করুন।\n"
            "📌 উদাহরণ: `8212595643`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return USER_DETAILS
    
    user = get_user_details(user_id)
    if not user:
        await update.message.reply_text(
            f"❌ **ইউজার পাওয়া যায়নি!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ইউজার আইডি `{user_id}` এর কোনো ইউজার নেই।\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return ConversationHandler.END
    
    status = "✅ সক্রিয়" if user[2] == 1 else "❌ নিষ্ক্রিয়"
    join_date = datetime.strptime(user[6], '%Y-%m-%d %H:%M:%S.%f').strftime('%d %b, %Y %I:%M %p')
    
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT type, amount, status, created_at 
        FROM transactions 
        WHERE user_id = ? 
        ORDER BY created_at DESC 
        LIMIT 5
    """, (user_id,))
    transactions = cursor.fetchall()
    conn.close()
    
    msg = (
        f"👤 **ইউজার ডিটেইলস**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **ইউজার আইডি:** `{user[0]}`\n"
        f"👤 **ইউজারনেম:** {user[1]}\n"
        f"⚡ **স্ট্যাটাস:** {status}\n"
        f"💰 **ব্যালেন্স:** {user[3]} টাকা\n"
        f"👥 **মোট রেফার:** {user[4]} জন\n"
        f"💸 **মোট উইথড্র:** {user[5]} টাকা\n"
        f"🔑 **রেফারেল কোড:** `{user[8]}`\n"
        f"📅 **জয়েন তারিখ:** {join_date}\n"
    )
    if user[7]:
        msg += f"👤 **রেফারড বাই:** {user[7]}\n"
    
    msg += f"\n📜 **সর্বশেষ ৫ ট্রানজেকশন:**\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    if transactions:
        for txn in transactions:
            txn_type = "ডিপোজিট" if txn[0] == "deposit" else "উইথড্র"
            status_text = "✅" if txn[2] == "approved" else "⏳" if txn[2] == "pending" else "❌"
            txn_date = datetime.strptime(txn[3], '%Y-%m-%d %H:%M:%S.%f').strftime('%d %b')
            msg += f"• {txn_type}: {txn[1]} টাকা {status_text} ({txn_date})\n"
    else:
        msg += "📭 কোনো ট্রানজেকশন নেই।"
    
    msg += f"\n━━━━━━━━━━━━━━━━━━━━━━━━"
    await update.message.reply_text(msg, parse_mode='Markdown')
    return ConversationHandler.END

# ---------------- ACTIVATION ----------------
async def start_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "💳 **অ্যাকাউন্ট অ্যাক্টিভেশন**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "অ্যাক্টিভ করতে **৩০ টাকা** পাঠান:\n\n"
        "📱 **বিকাশ/নগদ (Personal)**\n"
        "`01572972953`\n\n"
        "💰 পরিমাণ: **৩০ টাকা**\n\n"
        "📌 পেমেন্ট মেথড নির্বাচন করুন:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(msg, reply_markup=get_payment_method_keyboard(), parse_mode='Markdown')
    return ACTIVATION_METHOD

async def activation_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = update.message.text
    if method == "❌ বাতিল":
        await update.message.reply_text(
            "❌ **প্রক্রিয়া বাতিল করা হয়েছে!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    if method not in ["📱 বিকাশ", "💳 নগদ"]:
        await update.message.reply_text(
            "❌ **সঠিক মেথড নির্বাচন করুন!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return ACTIVATION_METHOD
    
    context.user_data['activation_method'] = method
    
    if method == "📱 বিকাশ":
        await update.message.reply_text(
            "📱 **বিকাশ পেমেন্ট**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "আপনার **ফোন নম্বর** অথবা **TxnID** লিখুন:\n\n"
            "📌 উদাহরণ: `01712345678` অথবা `ABC123XYZ`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        await update.message.reply_text(
            "💳 **নগদ পেমেন্ট**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "আপনার **ফোন নম্বর** লিখুন:\n\n"
            "📌 উদাহরণ: `01712345678`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    return TXN_ID

async def receive_txnid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    user = get_user(user_id)
    username = user[1] if user else "Unknown"
    method = context.user_data.get('activation_method', '📱 বিকাশ')
    
    is_phone = user_input.isdigit() and len(user_input) == 11
    phone_number = user_input if is_phone else None
    txn_id = user_input if not is_phone else None
    
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO transactions (user_id, username, type, amount, status, txn_id, phone_number, method, created_at) 
        VALUES (?, ?, 'deposit', 30, 'pending', ?, ?, ?, ?)
    """, (user_id, username, txn_id, phone_number, method, datetime.now()))
    txn_db_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ **তথ্য জমা হয়েছে!** 📤\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "আপনার পেমেন্ট রিকুয়েস্ট অ্যাডমিনের কাছে পাঠানো হয়েছে।\n"
        "⏳ অ্যাডমিন চেক করে অ্যাক্টিভেট করবেন।\n\n"
        "📌 অনুগ্রহ করে অপেক্ষা করুন...\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # Fixed: Corrected bracket syntax
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ অ্যাপ্রুভ", callback_data=f"app_pay_{txn_db_id}_{user_id}"),
         InlineKeyboardButton("❌ রিজেক্ট", callback_data=f"rej_pay_{txn_db_id}_{user_id}")]
    ])
    
    method_emoji = "📱" if "বিকাশ" in method else "💳"
    method_name = method.replace("📱 ", "").replace("💳 ", "")
    
    if phone_number:
        info = f"📱 ফোন নম্বর: `{phone_number}`"
    else:
        info = f"🧾 TxnID: `{txn_id}`"
    
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"📥 **নতুন পেমেন্ট**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ইউজার আইডি: `{user_id}`\n"
                f"👤 ইউজারনেম: {username}\n"
                f"{method_emoji} পেমেন্ট মেথড: {method_name}\n"
                f"{info}\n"
                f"💰 পরিমাণ: ৩০ টাকা\n"
                f"📅 সময়: {datetime.now().strftime('%d %b, %Y %I:%M %p')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            reply_markup=btn,
            parse_mode='Markdown'
        )
    return ConversationHandler.END

# ---------------- ADMIN ACTIONS ----------------
async def handle_admin_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    action, txn_db_id, target_user_id = data[0], int(data[2]), int(data[3])
    
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT username FROM users WHERE user_id = ?", (target_user_id,))
    user_data = cursor.fetchone()
    username = user_data[0] if user_data else "Unknown"
    
    if action == "app":
        cursor.execute("UPDATE transactions SET status='approved' WHERE id=?", (txn_db_id,))
        cursor.execute("UPDATE users SET is_active=1 WHERE user_id=?", (target_user_id,))
        
        cursor.execute("SELECT referred_by FROM users WHERE user_id=?", (target_user_id,))
        ref = cursor.fetchone()
        if ref and ref[0]:
            referrer_id = ref[0]
            cursor.execute("UPDATE users SET balance=balance+20, total_referrals=total_referrals+1 WHERE user_id=?", (referrer_id,))
            try:
                await context.bot.send_message(
                    referrer_id,
                    "🎉 **রেফারেল বোনাস!**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "আপনার রেফারেল অ্যাক্টিভ হয়েছে!\n"
                    "💰 **২০ টাকা** বোনাস যোগ হয়েছে!\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━"
                )
            except Exception:
                pass
        conn.commit()
        
        await query.edit_message_text(
            f"✅ **পেমেন্ট অ্যাপ্রুভ করা হয়েছে**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Txn ID: {txn_db_id}\n"
            f"👤 ইউজার আইডি: `{target_user_id}`\n"
            f"👤 ইউজারনেম: {username}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "🎉 **অ্যাকাউন্ট অ্যাক্টিভেটেড!** ✨\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "আপনার পেমেন্ট অ্যাপ্রুভ হয়েছে!\n"
                    "এখন আপনি আমাদের সকল সেবা পেতে পারেন।\n\n"
                    "📌 মেনু থেকে অপশন নির্বাচন করুন:\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━"
                ),
                reply_markup=get_active_reply_keyboard(target_user_id)
            )
        except Exception:
            pass
    else:
        cursor.execute("UPDATE transactions SET status='rejected' WHERE id=?", (txn_db_id,))
        conn.commit()
        await query.edit_message_text(
            f"❌ **পেমেন্ট রিজেক্ট করা হয়েছে**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Txn ID: {txn_db_id}\n"
            f"👤 ইউজার আইডি: `{target_user_id}`\n"
            f"👤 ইউজারনেম: {username}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            await context.bot.send_message(
                target_user_id,
                "❌ **পেমেন্ট রিজেক্টেড**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "আপনার পেমেন্ট রিকুয়েস্ট বাতিল করা হয়েছে।\n"
                "সঠিক তথ্য দিয়ে আবার চেষ্টা করুন।\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━"
            )
        except Exception:
            pass
    conn.close()

async def handle_admin_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    action, w_id, target_user_id, amount = data[0], int(data[2]), int(data[3]), int(data[4])
    
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT username FROM users WHERE user_id = ?", (target_user_id,))
    user_data = cursor.fetchone()
    username = user_data[0] if user_data else "Unknown"
    
    if action == "app":
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (target_user_id,))
        curr_bal = cursor.fetchone()[0]
        if curr_bal >= amount:
            cursor.execute("UPDATE users SET balance=balance-?, total_withdraw=total_withdraw+? WHERE user_id=?", (amount, amount, target_user_id))
            cursor.execute("UPDATE pending_withdraws SET status='approved' WHERE id=?", (w_id,))
            conn.commit()
            await query.edit_message_text(
                f"✅ **উইথড্র অ্যাপ্রুভ করা হয়েছে**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ID: {w_id}\n"
                f"💵 পরিমাণ: {amount} টাকা\n"
                f"👤 ইউজার আইডি: `{target_user_id}`\n"
                f"👤 ইউজারনেম: {username}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            try:
                await context.bot.send_message(
                    target_user_id,
                    f"✅ **উইথড্র সফল!** 💸\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"আপনার **{amount} টাকা** উইথড্র হয়েছে।\n"
                    f"আপনার নম্বরে টাকা পাঠানো হবে।\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━"
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(
                f"❌ **পর্যাপ্ত ব্যালেন্স নেই!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"ইউজারের ব্যালেন্স: {curr_bal} টাকা\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
    else:
        cursor.execute("UPDATE pending_withdraws SET status='rejected' WHERE id=?", (w_id,))
        conn.commit()
        await query.edit_message_text(
            f"❌ **উইথড্র রিজেক্ট করা হয়েছে**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: {w_id}\n"
            f"👤 ইউজার আইডি: `{target_user_id}`\n"
            f"👤 ইউজারনেম: {username}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            await context.bot.send_message(
                target_user_id,
                "❌ **উইথড্র বাতিল**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "আপনার উইথড্র রিকুয়েস্টটি বাতিল করা হয়েছে।\n"
                "কারণ সম্পর্কে জানতে সাপোর্টে যোগাযোগ করুন।\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━"
            )
        except Exception:
            pass
    conn.close()

# ---------------- WITHDRAW ----------------
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if user[5] == 0:
        await update.message.reply_text(
            "⚠️ **অ্যাকাউন্ট নিষ্ক্রিয়!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "উইথড্র করতে প্রথমে অ্যাকাউন্ট অ্যাক্টিভেট করুন।\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return ConversationHandler.END
    if user[6] < 60:
        await update.message.reply_text(
            f"⚠️ **পর্যাপ্ত ব্যালেন্স নেই!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 আপনার ব্যালেন্স: {user[6]} টাকা\n"
            f"📌 ন্যূনতম উইথড্র: ৬০ টাকা\n\n"
            f"👥 রেফার করে আরও টাকা আয় করুন!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "💸 **উইথড্র প্রক্রিয়া**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "আপনি কোন মেথডে টাকা পেতে চান?\n\n"
        "📌 মেথড নির্বাচন করুন:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=get_payment_method_keyboard()
    )
    return WITHDRAW_METHOD

async def withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = update.message.text
    if method == "❌ বাতিল":
        await update.message.reply_text(
            "❌ **প্রক্রিয়া বাতিল করা হয়েছে!**",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    if method not in ["📱 বিকাশ", "💳 নগদ"]:
        await update.message.reply_text(
            "❌ **সঠিক মেথড নির্বাচন করুন!**"
        )
        return WITHDRAW_METHOD
    
    context.user_data['withdraw_method'] = method
    method_name = method.replace("📱 ", "").replace("💳 ", "")
    await update.message.reply_text(
        f"📱 **{method_name} নম্বর লিখুন:**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"আপনি যে নম্বরে টাকা পেতে চান:\n\n"
        f"📌 উদাহরণ: `01712345678`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return WITHDRAW_PHONE

async def withdraw_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.isdigit() or len(phone) != 11:
        await update.message.reply_text(
            "❌ **ভুল নম্বর!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "সঠিক ১১ ডিজিটের নম্বর দিন:\n"
            "📌 উদাহরণ: `01712345678`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return WITHDRAW_PHONE
    context.user_data['withdraw_phone'] = phone
    await update.message.reply_text(
        "💰 **উইথড্র পরিমাণ**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ন্যূনতম: **৬০ টাকা**\n\n"
        "📝 আপনি কত টাকা তুলতে চান?\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return WITHDRAW_AMOUNT

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ **সঠিক সংখ্যা লিখুন!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "শুধু সংখ্যা ব্যবহার করুন।\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return WITHDRAW_AMOUNT
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if amount < 60:
        await update.message.reply_text(
            f"❌ **ন্যূনতম ৬০ টাকা!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"আপনি {amount} টাকা দিয়েছেন।\n"
            f"ন্যূনতম ৬০ টাকা প্রয়োজন।\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return WITHDRAW_AMOUNT
    
    if amount > user[6]:
        await update.message.reply_text(
            f"❌ **পর্যাপ্ত ব্যালেন্স নেই!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 আপনার ব্যালেন্স: {user[6]} টাকা\n"
            f"💸 আপনি চেয়েছেন: {amount} টাকা\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return WITHDRAW_AMOUNT

    phone = context.user_data.get('withdraw_phone')
    username = user[1]
    method = context.user_data.get('withdraw_method', '📱 বিকাশ')
    method_name = method.replace("📱 ", "").replace("💳 ", "")

    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pending_withdraws (user_id, username, amount, phone_number, method, created_at) 
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, amount, phone, method, datetime.now()))
    w_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ **উইথড্র রিকুয়েস্ট জমা হয়েছে!** 📤\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 পরিমাণ: **{amount} টাকা**\n"
        f"📱 {method_name}: `{phone}`\n\n"
        f"⏳ অ্যাডমিনের অনুমোদনের জন্য অপেক্ষা করুন।\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ অ্যাপ্রুভ", callback_data=f"app_w_{w_id}_{user_id}_{amount}"),
         InlineKeyboardButton("❌ রিজেক্ট", callback_data=f"rej_w_{w_id}_{user_id}_{amount}")]
    ])
    
    method_emoji = "📱" if "বিকাশ" in method else "💳"
    
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"📤 **নতুন উইথড্র**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ইউজার আইডি: `{user_id}`\n"
                f"👤 ইউজারনেম: {username}\n"
                f"{method_emoji} উইথড্র মেথড: {method_name}\n"
                f"📱 নম্বর: `{phone}`\n"
                f"💵 পরিমাণ: {amount} টাকা\n"
                f"📅 সময়: {datetime.now().strftime('%d %b, %Y %I:%M %p')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            reply_markup=btn,
            parse_mode='Markdown'
        )
    return ConversationHandler.END

# ---------------- SUPPORT ----------------
async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **সাপোর্ট সেন্টার**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "আপনার সমস্যা বিস্তারিত লিখুন:\n\n"
        "📌 আমরা ২৪ ঘন্টার মধ্যে রিপ্লাই দেব।\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    context.user_data['in_support'] = True
    return SUPPORT_MSG

async def receive_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    # Only process if it's text message and not a command/button
    if not msg or msg.startswith('/'):
        return
    
    # Remove support mode
    context.user_data['in_support'] = False
    
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"📩 **নতুন সাপোর্ট মেসেজ**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 ইউজার আইডি: `{user_id}`\n"
                f"👤 ইউজারনেম: {user[1] if user else 'N/A'}\n"
                f"💬 বার্তা:\n{msg}\n"
                f"📅 সময়: {datetime.now().strftime('%d %b, %Y %I:%M %p')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            parse_mode='Markdown'
        )
    
    await update.message.reply_text(
        "✅ **আপনার বার্তা পাঠানো হয়েছে!** 📨\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "আমরা খুব শীঘ্রই আপনার সাথে যোগাযোগ করব।\n\n"
        "📌 ধৈর্য ধরার জন্য ধন্যবাদ!\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return ConversationHandler.END

# ---------------- DATABASE RESTORE ----------------
async def handle_restore_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CSV ফাইল দিয়ে ডেটাবেস রিপ্লেস করে"""
    
    if not context.user_data.get('restore_mode'):
        return
    
    if not update.message.document:
        await update.message.reply_text("❌ ফাইল আপলোড করুন!")
        return
    
    file = update.message.document
    
    if not file.file_name.endswith('.csv'):
        await update.message.reply_text("❌ শুধু CSV ফাইল সাপোর্টেড!")
        context.user_data['restore_mode'] = False
        return
    
    await update.message.reply_text("⏳ ডেটাবেস রিপ্লেস করা হচ্ছে... দয়া করে অপেক্ষা করুন।")
    
    try:
        # ফাইল ডাউনলোড
        file_obj = await context.bot.get_file(file.file_id)
        file_content = await file_obj.download_as_bytearray()
        csv_data = file_content.decode('utf-8-sig')
        
        conn = sqlite3.connect('share2pay.db')
        cursor = conn.cursor()
        
        # 1. সব টেবিল খালি করুন
        cursor.execute("DELETE FROM users")
        cursor.execute("DELETE FROM transactions")
        cursor.execute("DELETE FROM pending_withdraws")
        
        # 2. অটো-ইনক্রিমেন্ট রিসেট করুন
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='transactions'")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='pending_withdraws'")
        
        stats = {
            'users': 0,
            'transactions': 0,
            'withdraws': 0,
            'errors': 0
        }
        
        lines = csv_data.splitlines()
        current_table = None
        columns = []
        
        for line in lines:
            if not line.strip():
                continue
            
            # টেবিল চিহ্নিত করুন
            if line.startswith('===') and '===' in line:
                table_name = line.replace('=', '').strip().lower()
                if 'users' in table_name:
                    current_table = 'users'
                elif 'transactions' in table_name:
                    current_table = 'transactions'
                elif 'pending_withdraws' in table_name:
                    current_table = 'pending_withdraws'
                else:
                    current_table = None
                columns = []
                continue
            
            # কলাম সেট করুন
            if current_table and not columns:
                columns = [col.strip() for col in line.split(',')]
                continue
            
            # ডেটা প্রসেস করুন
            if current_table and columns:
                values = [v.strip() for v in line.split(',')]
                
                if len(values) < len(columns):
                    continue
                
                try:
                    if current_table == 'users':
                        cursor.execute("""
                            INSERT INTO users 
                            (user_id, username, password, referral_code, referred_by, 
                             is_active, balance, phone_number, total_referrals, total_withdraw, join_date)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            int(values[0]) if values[0] else None,
                            values[1] if len(values) > 1 else None,
                            values[2] if len(values) > 2 else None,
                            values[3] if len(values) > 3 else None,
                            int(values[4]) if len(values) > 4 and values[4] else None,
                            int(values[5]) if len(values) > 5 else 0,
                            int(values[6]) if len(values) > 6 else 0,
                            values[7] if len(values) > 7 else None,
                            int(values[8]) if len(values) > 8 else 0,
                            int(values[9]) if len(values) > 9 else 0,
                            values[10] if len(values) > 10 else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        ))
                        stats['users'] += 1
                    
                    elif current_table == 'transactions':
                        cursor.execute("""
                            INSERT INTO transactions 
                            (id, user_id, username, type, amount, status, txn_id, phone_number, method, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            int(values[0]) if values[0] else None,
                            int(values[1]) if len(values) > 1 and values[1] else None,
                            values[2] if len(values) > 2 else None,
                            values[3] if len(values) > 3 else None,
                            int(values[4]) if len(values) > 4 else 0,
                            values[5] if len(values) > 5 else 'pending',
                            values[6] if len(values) > 6 else None,
                            values[7] if len(values) > 7 else None,
                            values[8] if len(values) > 8 else None,
                            values[9] if len(values) > 9 else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        ))
                        stats['transactions'] += 1
                    
                    elif current_table == 'pending_withdraws':
                        cursor.execute("""
                            INSERT INTO pending_withdraws 
                            (id, user_id, username, amount, phone_number, method, status, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            int(values[0]) if values[0] else None,
                            int(values[1]) if len(values) > 1 and values[1] else None,
                            values[2] if len(values) > 2 else None,
                            int(values[3]) if len(values) > 3 else 0,
                            values[4] if len(values) > 4 else None,
                            values[5] if len(values) > 5 else None,
                            values[6] if len(values) > 6 else 'pending',
                            values[7] if len(values) > 7 else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        ))
                        stats['withdraws'] += 1
                
                except Exception as e:
                    stats['errors'] += 1
                    logging.error(f"Row error: {e}")
                    continue
        
        conn.commit()
        conn.close()
        
        # রিপোর্ট
        await update.message.reply_text(
            f"✅ **ডেটাবেস রিপ্লেস সম্পূর্ণ!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 ইউজার যোগ হয়েছে: {stats['users']} জন\n"
            f"💳 ট্রানজেকশন যোগ হয়েছে: {stats['transactions']} টি\n"
            f"📤 উইথড্র যোগ হয়েছে: {stats['withdraws']} টি\n"
            f"⚠️ এরর: {stats['errors']} টি\n\n"
            f"📌 আগের সব ডেটা ডিলিট করে CSV এর ডেটা যোগ করা হয়েছে!",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ **রিস্টোর ব্যর্থ!**\n\n"
            f"সমস্যা: `{str(e)}`\n\n"
            f"📌 আবার চেষ্টা করুন।",
            parse_mode='Markdown'
        )
        logging.error(f"Restore error: {e}")
    
    context.user_data['restore_mode'] = False

# ---------------- RESTORE CALLBACKS ----------------
async def confirm_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """রিস্টোর কনফার্ম করলে"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📂 **CSV ফাইল আপলোড করুন**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "আপনার ব্যাকআপ CSV ফাইলটি আপলোড করুন।\n\n"
        "✅ ফাইলটি অবশ্যই .csv এক্সটেনশনের হতে হবে।\n"
        "⚠️ সব পুরোনো ডেটা ডিলিট হবে!\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode='Markdown'
    )
    context.user_data['restore_mode'] = True

async def cancel_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """রিস্টোর বাতিল করলে"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "❌ **ডেটাবেস রিস্টোর বাতিল করা হয়েছে!**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 ডেটাবেস আগের মতোই আছে। কোনো পরিবর্তন হয়নি।\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode='Markdown'
    )
    context.user_data['restore_mode'] = False

# ---------------- OTHER ----------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ **আপনি অ্যাডমিন নন!**")
        return
    context.user_data['admin_mode'] = True
    await update.message.reply_text(
        "🔐 **অ্যাডমিন প্যানেল**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 নিচের বাটন থেকে নির্বাচন করুন:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=get_admin_reply_keyboard()
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['in_support'] = False
    await update.message.reply_text(
        "❌ **প্রক্রিয়া বাতিল করা হয়েছে!**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return ConversationHandler.END

async def copy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    link = query.data.replace('copy_', '')
    await query.message.reply_text(f"`{link}`", parse_mode='Markdown')

# ---------------- MAIN ----------------
def main():
    # ডামি HTTP সার্ভার চালু করার জন্য থ্রেড (Render-এর জন্য প্রয়োজন)
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registration
    reg_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_username)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Activation
    activation_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^✅ অ্যাকাউন্ট অ্যাক্টিভেট করুন$'), start_activation)],
        states={
            ACTIVATION_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, activation_method)],
            TXN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txnid)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Withdraw
    withdraw_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^💸 উইথড্র$'), start_withdraw)],
        states={
            WITHDRAW_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_method)],
            WITHDRAW_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_phone)],
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Support
    support_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🆘 সাপোর্ট$'), start_support)],
        states={
            SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_support)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Admin Search
    admin_search_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔍 ইউজার খুঁজুন$'), handle_admin_messages)],
        states={
            SEARCH_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_user)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Admin Details
    admin_details_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📋 ইউজার ডিটেইলস$'), handle_admin_messages)],
        states={
            USER_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_details)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Add handlers
    app.add_handler(reg_handler)
    app.add_handler(activation_handler)
    app.add_handler(withdraw_handler)
    app.add_handler(support_handler)
    app.add_handler(admin_search_handler)
    app.add_handler(admin_details_handler)

    app.add_handler(CommandHandler('admin', admin_command))
    app.add_handler(CallbackQueryHandler(copy_link, pattern='^copy_'))
    app.add_handler(CallbackQueryHandler(handle_admin_payment, pattern='^(app_pay|rej_pay)_'))
    app.add_handler(CallbackQueryHandler(handle_admin_withdraw, pattern='^(app_w|rej_w)_'))
    
    # ডেটাবেস রিস্টোর কলব্যাক হ্যান্ডলার
    app.add_handler(CallbackQueryHandler(confirm_restore, pattern='^confirm_restore$'))
    app.add_handler(CallbackQueryHandler(cancel_restore, pattern='^cancel_restore$'))
    
    # ডেটাবেস রিস্টোর ফাইল হ্যান্ডলার
    app.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.COMMAND, 
        handle_restore_file
    ))
    
    # Main message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    print("🤖 Share2Pay Bot is running...")
    print(f"👥 Admin IDs: {ADMIN_IDS}")
    app.run_polling()

if __name__ == '__main__':
    main()
