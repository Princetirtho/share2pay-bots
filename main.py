import random
import hashlib
import string
import logging
import sqlite3
import csv
import io
import os
import re
import asyncio
from datetime import datetime
from threading import Thread
from flask import Flask
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

# ---------------- DUMMY WEB SERVER FOR RENDER ----------------
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Bot is Alive and Running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ---------------- CONFIGURATION ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN environment variable not set")
    exit(1)

# Multiple Admin IDs
ADMIN_IDS_STR = os.environ.get("ADMIN_IDS", "8212595643,8235339975")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]

# Payment Numbers
BKASH_NUMBERS = ["01709780713", "01572972953"]
NAGAD_NUMBERS = ["01922799136"]

# Conversation States
USERNAME, PASSWORD, TXN_ID, WITHDRAW_PHONE, WITHDRAW_AMOUNT, SUPPORT_MSG, SEARCH_USER, USER_DETAILS, ACTIVATION_METHOD, WITHDRAW_METHOD, BROADCAST_TEXT, BROADCAST_IMAGE_WAIT, ADMIN_REPLY = range(13)

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
               r.username as referred_by_username, u.referral_code, u.phone_number
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

def get_all_active_users():
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE is_active=1")
    users = cursor.fetchall()
    conn.close()
    return users

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
        [KeyboardButton("📥 CSV ডাউনলোড"), KeyboardButton("📢 ব্রডকাস্ট")],
        [KeyboardButton("🔙 ইউজার মোড")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_activation_keyboard():
    keyboard = [
        [KeyboardButton("📱 বিকাশ"), KeyboardButton("💳 নগদ")],
        [KeyboardButton("❌ বাতিল")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_withdraw_keyboard():
    keyboard = [
        [KeyboardButton("📱 বিকাশ"), KeyboardButton("💳 নগদ")],
        [KeyboardButton("❌ বাতিল")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_cancel_keyboard():
    keyboard = [[KeyboardButton("❌ বাতিল")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ---------------- START COMMAND (FIXED) ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    # Check for referral
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
        # New user - start registration
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
    
    # Existing user
    if user[5] == 1:  # Active
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
                "🎉 **স্বাগতম ফিরে!** 🎉\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📌 আপনার প্রয়োজনীয় অপশন নির্বাচন করুন:\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=get_active_reply_keyboard(user_id),
                parse_mode='Markdown'
            )
    else:  # Inactive
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

    # Check if in support mode - BUT allow cancel
    if context.user_data.get('in_support'):
        if text == "❌ বাতিল":
            context.user_data['in_support'] = False
            await update.message.reply_text(
                "❌ **সাপোর্ট বাতিল!**",
                reply_markup=get_active_reply_keyboard(user_id)
            )
            return
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
            f"• 📱 বিকাশ: {', '.join(BKASH_NUMBERS)}\n"
            f"• 💳 নগদ: {', '.join(NAGAD_NUMBERS)}\n"
            "• বিকাশ: TxnID অথবা ফোন নম্বর\n"
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
            if user_details[9]:
                msg += f"\n📱 **ফোন:** {user_details[9]}"
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
                caption="📊 **ডাটাবেস ব্যাকআপ**"
            )
        except Exception as e:
            logging.error(f"CSV export error: {e}")
            await update.message.reply_text("❌ **CSV ফাইল তৈরি করতে সমস্যা হয়েছে!**")

    elif text == "📢 ব্রডকাস্ট":
        if not is_admin(user_id):
            return
        await update.message.reply_text(
            "📢 **ব্রডকাস্ট সিস্টেম**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 সব ইউজারকে টেক্সট পাঠান:\n"
            "🖼️ ছবি পাঠাতে চাইলে /broadcast_image\n"
            "❌ 'বাতিল' লিখে বাতিল করুন",
            reply_markup=get_cancel_keyboard()
        )
        return BROADCAST_TEXT

    elif text == "🔙 ইউজার মোড":
        context.user_data['admin_mode'] = False
        await update.message.reply_text(
            "👤 **ইউজার মোডে ফিরে গেছেন**",
            reply_markup=get_active_reply_keyboard(user_id)
        )

# ---------------- SEARCH & DETAILS ----------------
async def search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    users = search_users(query)
    if not users:
        await update.message.reply_text(f"❌ **'{query}' এর কোনো ইউজার পাওয়া যায়নি!**")
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
        await update.message.reply_text("❌ **ভুল ইউজার আইডি! শুধু সংখ্যা লিখুন।**")
        return USER_DETAILS
    
    user = get_user_details(user_id)
    if not user:
        await update.message.reply_text(f"❌ **ইউজার আইডি `{user_id}` পাওয়া যায়নি!**")
        return ConversationHandler.END
    
    status = "✅ সক্রিয়" if user[2] == 1 else "❌ নিষ্ক্রিয়"
    join_date = datetime.strptime(user[6], '%Y-%m-%d %H:%M:%S.%f').strftime('%d %b, %Y %I:%M %p')
    
    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("SELECT type, amount, status, created_at FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 5", (user_id,))
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
    if user[9]:
        msg += f"📱 **ফোন:** {user[9]}\n"
    
    msg += f"\n📜 **সর্বশেষ ৫ ট্রানজেকশন:**\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
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

# ---------------- ACTIVATION (FIXED) ----------------
async def start_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Clear previous data
    context.user_data.clear()
    
    msg = (
        "💳 **অ্যাকাউন্ট অ্যাক্টিভেশন**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "অ্যাক্টিভ করতে **৩০ টাকা** পাঠান:\n\n"
        f"📱 **বিকাশ:** {', '.join(BKASH_NUMBERS)}\n"
        f"💳 **নগদ:** {', '.join(NAGAD_NUMBERS)}\n\n"
        "💰 পরিমাণ: **৩০ টাকা**\n\n"
        "📌 পেমেন্ট মেথড নির্বাচন করুন:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(msg, reply_markup=get_activation_keyboard(), parse_mode='Markdown')
    return ACTIVATION_METHOD

async def activation_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = update.message.text
    
    # Check for cancel
    if method == "❌ বাতিল" or method.lower() == "বাতিল":
        context.user_data.clear()
        await update.message.reply_text(
            "❌ **প্রক্রিয়া বাতিল করা হয়েছে!**",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    if method not in ["📱 বিকাশ", "💳 নগদ"]:
        await update.message.reply_text(
            "❌ **সঠিক মেথড নির্বাচন করুন!**\n"
            "📌 '📱 বিকাশ' অথবা '💳 নগদ' সিলেক্ট করুন",
            reply_markup=get_activation_keyboard()
        )
        return ACTIVATION_METHOD
    
    context.user_data['activation_method'] = method
    
    if method == "📱 বিকাশ":
        await update.message.reply_text(
            "📱 **বিকাশ পেমেন্ট**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🧾 আপনার **TxnID** অথবা **ফোন নম্বর** লিখুন:\n"
            "📌 TxnID: `ABCD123456789`\n"
            "📌 ফোন: `017xxxxxxxx`\n\n"
            "❌ 'বাতিল' লিখে প্রক্রিয়া বাতিল করুন"
        )
    else:  # নগদ
        await update.message.reply_text(
            "💳 **নগদ পেমেন্ট**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 আপনার **ফোন নম্বর** লিখুন:\n"
            "📌 উদাহরণ: `017xxxxxxxx`\n\n"
            "❌ 'বাতিল' লিখে প্রক্রিয়া বাতিল করুন"
        )
    return TXN_ID

async def receive_txnid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    
    # Check for cancel
    if user_input.lower() in ["বাতিল", "cancel"]:
        context.user_data.clear()
        await update.message.reply_text(
            "❌ **প্রক্রিয়া বাতিল করা হয়েছে!**",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    username = user[1] if user else "Unknown"
    method = context.user_data.get('activation_method', '📱 বিকাশ')
    
    # Check if input is phone number (11 digits)
    is_phone = user_input.isdigit() and len(user_input) == 11
    
    # Validate based on method
    if method == "📱 বিকাশ":
        # Both phone number or TxnID allowed
        if is_phone:
            phone_number = user_input
            txn_id = None
        else:
            phone_number = None
            txn_id = user_input
            # TxnID should be at least 5 characters
            if len(txn_id) < 5:
                await update.message.reply_text(
                    "❌ **ভুল TxnID!**\n\n"
                    "TxnID কমপক্ষে ৫ অক্ষরের হতে হবে।\n"
                    "আবার TxnID বা ফোন নম্বর দিন:"
                )
                return TXN_ID
    else:  # নগদ
        # Only phone number allowed
        if not is_phone:
            await update.message.reply_text(
                "❌ **ভুল ইনপুট!**\n\n"
                "💳 নগদ এর জন্য শুধু ১১ ডিজিটের ফোন নম্বর দিন।\n"
                "📌 উদাহরণ: `017xxxxxxxx`\n\n"
                "আবার ফোন নম্বর দিন অথবা 'বাতিল' দিন:"
            )
            return TXN_ID
        phone_number = user_input
        txn_id = None
    
    # Save to database
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
        "✅ **তথ্য জমা হয়েছে!**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ অ্যাডমিন চেক করে আপনার অ্যাকাউন্ট অ্যাক্টিভেট করবেন।\n"
        "📌 সাধারণত ৫-১০ মিনিট সময় লাগে।"
    )
    
    # Notify admins
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ অ্যাপ্রুভ", callback_data=f"app_pay_{txn_db_id}_{user_id}"),
         InlineKeyboardButton("❌ রিজেক্ট", callback_data=f"rej_pay_{txn_db_id}_{user_id}")]
    ])
    
    if method == "📱 বিকাশ":
        if txn_id:
            info = f"🧾 TxnID: `{txn_id}`"
        else:
            info = f"📱 ফোন: `{phone_number}`"
    else:
        info = f"📱 ফোন: `{phone_number}`"
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📥 **নতুন পেমেন্ট**\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                     f"🆔 ইউজার আইডি: `{user_id}`\n"
                     f"👤 ইউজারনেম: {username}\n"
                     f"📱 টেলিগ্রাম: @{update.effective_user.username or 'N/A'}\n"
                     f"🤖 বট: @{(await context.bot.get_me()).username}\n"
                     f"{info}\n"
                     f"💰 পরিমাণ: ৩০ টাকা\n"
                     f"📅 সময়: {datetime.now().strftime('%d %b, %Y %I:%M %p')}\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=btn,
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Admin notification error: {e}")
    
    context.user_data.clear()
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
                await context.bot.send_message(referrer_id, "🎉 **রেফারেল বোনাস!** আপনার রেফারেলে ২০ টাকা যোগ হয়েছে!")
            except Exception:
                pass
        conn.commit()
        await query.edit_message_text(f"✅ **অ্যাপ্রুভড:** `{target_user_id}` ({username})")
        try:
            await context.bot.send_message(
                target_user_id, 
                "🎉 **অ্যাকাউন্ট অ্যাক্টিভেটেড!**\n\nআপনি এখন সব ফিচার ব্যবহার করতে পারবেন।",
                reply_markup=get_active_reply_keyboard(target_user_id)
            )
        except Exception:
            pass
    else:
        cursor.execute("UPDATE transactions SET status='rejected' WHERE id=?", (txn_db_id,))
        conn.commit()
        await query.edit_message_text(f"❌ **রিজেক্টেড:** `{target_user_id}` ({username})")
        try:
            await context.bot.send_message(target_user_id, "❌ **পেমেন্ট রিজেক্টেড!** দয়া করে সঠিক তথ্য দিয়ে আবার চেষ্টা করুন।")
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
            await query.edit_message_text(f"✅ **উইথড্র অ্যাপ্রুভড:** {amount} টাকা (`{target_user_id}`)")
            try:
                await context.bot.send_message(target_user_id, f"✅ **{amount} টাকা উইথড্র সফল হয়েছে!**")
            except Exception:
                pass
        else:
            await query.edit_message_text("❌ **পর্যাপ্ত ব্যালেন্স নেই!**")
    else:
        cursor.execute("UPDATE pending_withdraws SET status='rejected' WHERE id=?", (w_id,))
        conn.commit()
        await query.edit_message_text(f"❌ **উইথড্র রিজেক্টেড:** `{target_user_id}`")
        try:
            await context.bot.send_message(target_user_id, "❌ **উইথড্র রিকুয়েস্ট বাতিল করা হয়েছে।**")
        except Exception:
            pass
    conn.close()

# ---------------- WITHDRAW (FIXED) ----------------
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if user[5] == 0:
        await update.message.reply_text(
            "⚠️ **উইথড্র করতে প্রথমে অ্যাকাউন্ট অ্যাক্টিভেট করুন।**\n"
            "✅ 'অ্যাকাউন্ট অ্যাক্টিভেট করুন' বাটনে ক্লিক করুন।"
        )
        return ConversationHandler.END
    if user[6] < 60:
        await update.message.reply_text(
            f"⚠️ **পর্যাপ্ত ব্যালেন্স নেই!**\n\n"
            f"💰 আপনার ব্যালেন্স: {user[6]} টাকা\n"
            f"📌 সর্বনিম্ন উইথড্র: ৬০ টাকা"
        )
        return ConversationHandler.END
    
    context.user_data.clear()
    await update.message.reply_text(
        "💸 **উইথড্র**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 উইথড্র মেথড নির্বাচন করুন:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=get_withdraw_keyboard()
    )
    return WITHDRAW_METHOD

async def withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = update.message.text
    
    if method == "❌ বাতিল" or method.lower() == "বাতিল":
        context.user_data.clear()
        await update.message.reply_text(
            "❌ **প্রক্রিয়া বাতিল করা হয়েছে!**",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    if method not in ["📱 বিকাশ", "💳 নগদ"]:
        await update.message.reply_text(
            "❌ **সঠিক মেথড নির্বাচন করুন!**",
            reply_markup=get_withdraw_keyboard()
        )
        return WITHDRAW_METHOD
    
    context.user_data['withdraw_method'] = method
    await update.message.reply_text(
        "📱 **ফোন নম্বর লিখুন:**\n"
        "📌 ১১ ডিজিটের নম্বর (যেমন: 017xxxxxxxx)\n"
        "❌ 'বাতিল' লিখে বাতিল করুন"
    )
    return WITHDRAW_PHONE

async def withdraw_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    
    if phone.lower() in ["বাতিল", "cancel"]:
        context.user_data.clear()
        await update.message.reply_text(
            "❌ **প্রক্রিয়া বাতিল!**",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    if not phone.isdigit() or len(phone) != 11:
        await update.message.reply_text(
            "❌ **সঠিক ১১ ডিজিটের নম্বর দিন!**\n"
            "📌 উদাহরণ: `017xxxxxxxx`"
        )
        return WITHDRAW_PHONE
    
    context.user_data['withdraw_phone'] = phone
    await update.message.reply_text(
        "💰 **কত টাকা তুলতে চান?**\n"
        f"📌 সর্বনিম্ন: ৬০ টাকা\n"
        "❌ 'বাতিল' লিখে বাতিল করুন"
    )
    return WITHDRAW_AMOUNT

async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text.lower() in ["বাতিল", "cancel"]:
        context.user_data.clear()
        await update.message.reply_text(
            "❌ **প্রক্রিয়া বাতিল!**",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text(
            "❌ **সঠিক সংখ্যা লিখুন!**\n"
            "📌 উদাহরণ: `100`"
        )
        return WITHDRAW_AMOUNT
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if amount < 60:
        await update.message.reply_text(
            f"❌ **ন্যূনতম ৬০ টাকা হতে হবে!**\n"
            f"📌 আপনি চেয়েছেন: {amount} টাকা"
        )
        return WITHDRAW_AMOUNT
    
    if amount > user[6]:
        await update.message.reply_text(
            f"❌ **পর্যাপ্ত ব্যালেন্স নেই!**\n"
            f"💰 আপনার ব্যালেন্স: {user[6]} টাকা\n"
            f"📌 আপনি চেয়েছেন: {amount} টাকা"
        )
        return WITHDRAW_AMOUNT

    phone = context.user_data.get('withdraw_phone')
    username = user[1]
    method = context.user_data.get('withdraw_method', '📱 বিকাশ')

    conn = sqlite3.connect('share2pay.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pending_withdraws (user_id, username, amount, phone_number, method, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (user_id, username, amount, phone, method, datetime.now()))
    w_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ **উইথড্র রিকুয়েস্ট জমা হয়েছে!**\n\n"
        f"💵 পরিমাণ: {amount} টাকা\n"
        f"⏳ অ্যাডমিন অ্যাপ্রুভ করলে টাকা পাবেন।"
    )

    # Notify admins with full info
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ অ্যাপ্রুভ", callback_data=f"app_w_{w_id}_{user_id}_{amount}"),
         InlineKeyboardButton("❌ রিজেক্ট", callback_data=f"rej_w_{w_id}_{user_id}_{amount}")]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📤 **নতুন উইথড্র**\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                     f"🆔 ইউজার আইডি: `{user_id}`\n"
                     f"👤 ইউজারনেম: {username}\n"
                     f"📱 টেলিগ্রাম: @{update.effective_user.username or 'N/A'}\n"
                     f"🤖 বট: @{(await context.bot.get_me()).username}\n"
                     f"📱 ফোন: `{phone}`\n"
                     f"{'📱' if 'বিকাশ' in method else '💳'} মেথড: {method.replace('📱 ', '').replace('💳 ', '')}\n"
                     f"💵 পরিমাণ: {amount} টাকা\n"
                     f"📅 সময়: {datetime.now().strftime('%d %b, %Y %I:%M %p')}\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=btn,
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Admin withdraw notification error: {e}")
    
    context.user_data.clear()
    return ConversationHandler.END

# ---------------- SUPPORT (FIXED) ----------------
async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data['in_support'] = True
    
    await update.message.reply_text(
        "🆘 **সাপোর্ট সেন্টার**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 আপনার সমস্যা বিস্তারিত লিখুন:\n\n"
        "⚠️ সাপোর্ট মোডে থাকা অবস্থায় শুধু টেক্সট লিখুন।\n"
        "❌ 'বাতিল' লিখে প্রক্রিয়া বাতিল করুন",
        reply_markup=get_cancel_keyboard()
    )
    return SUPPORT_MSG

async def receive_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    
    # "বাতিল" চেক
    if msg in ["❌ বাতিল", "বাতিল", "cancel"]:
        context.user_data.clear()
        await update.message.reply_text(
            "❌ **সাপোর্ট প্রক্রিয়া বাতিল!**",
            reply_markup=get_active_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    
    # অন্য কোনো বাটন চেক
    if msg in ["👤 প্রোফাইল", "📤 রেফার", "💰 ব্যালেন্স", "💸 উইথড্র", "❓ কিভাবে করবেন?", "✅ অ্যাকাউন্ট অ্যাক্টিভেট করুন"]:
        await update.message.reply_text(
            "⚠️ **সাপোর্ট মোডে আছেন!**\n"
            "📝 আপনার সমস্যা লিখুন অথবা 'বাতিল' লিখুন।"
        )
        return SUPPORT_MSG
    
    user_id = update.effective_user.id
    user = get_user(user_id)
    user_info = get_user_details(user_id)
    
    # ইউজারের সব ইনফো
    tg_username = update.effective_user.username or "N/A"
    tg_first_name = update.effective_user.first_name or "N/A"
    bot_username = (await context.bot.get_me()).username
    
    context.user_data['in_support'] = False
    
    # ডিটেইলস সহ মেসেজ
    support_msg = (
        f"📩 **সাপোর্ট টিকেট**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **ইউজার ইনফো:**\n"
        f"├ 🆔 ইউজার আইডি: `{user_id}`\n"
        f"├ 👤 ইউজারনেম: {user[1] if user else 'N/A'}\n"
        f"├ 📱 টেলিগ্রাম: @{tg_username}\n"
        f"├ 📛 নাম: {tg_first_name}\n"
        f"├ 🤖 বট: @{bot_username}\n"
        f"├ 💰 ব্যালেন্স: {user[6] if user else 0} টাকা\n"
        f"├ ⚡ স্ট্যাটাস: {'✅ সক্রিয়' if user and user[5] == 1 else '❌ নিষ্ক্রিয়'}\n"
        f"└ 📅 জয়েন: {user_info[6] if user_info else 'N/A'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 **মেসেজ:**\n"
        f"{msg}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ সময়: {datetime.now().strftime('%d %b, %Y %I:%M %p')}"
    )
    
    # রিপ্লাই বাটন
    reply_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 রিপ্লাই", callback_data=f"reply_{user_id}")]
    ])
    
    # সব অ্যাডমিনকে পাঠান
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=support_msg,
                reply_markup=reply_btn,
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Support message send error: {e}")
    
    # ইউজারকে কনফার্মেশন
    await update.message.reply_text(
        "✅ **সাপোর্ট মেসেজ পাঠানো হয়েছে!**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ অ্যাডমিন রিপ্লাই দিলে আপনি নোটিফিকেশন পাবেন।\n"
        "📌 দ্রুত সাড়া পেতে ধৈর্য ধরুন।",
        reply_markup=get_active_reply_keyboard(user_id)
    )
    return ConversationHandler.END

# ---------------- ADMIN REPLY ----------------
async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = int(query.data.split('_')[1])
    context.user_data['reply_to_user'] = user_id
    
    await query.message.reply_text(
        f"💬 **ইউজারকে রিপ্লাই**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ইউজার আইডি: `{user_id}`\n\n"
        f"📝 আপনার রিপ্লাই লিখুন:\n"
        f"❌ 'বাতিল' লিখে বাতিল করুন",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return ADMIN_REPLY

async def send_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    
    if msg.lower() in ["বাতিল", "cancel"]:
        await update.message.reply_text("❌ **রিপ্লাই বাতিল!**")
        return ConversationHandler.END
    
    user_id = context.user_data.get('reply_to_user')
    if not user_id:
        await update.message.reply_text("❌ **ইউজার আইডি পাওয়া যায়নি!**")
        return ConversationHandler.END
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📩 **অ্যাডমিন থেকে রিপ্লাই**\n"
                 f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                 f"{msg}\n"
                 f"━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text("✅ **রিপ্লাই পাঠানো হয়েছে!**")
    except Exception as e:
        await update.message.reply_text(f"❌ **পাঠাতে সমস্যা!**\n{str(e)}")
    
    return ConversationHandler.END

# ---------------- BROADCAST ----------------
async def broadcast_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    
    if msg.lower() in ["বাতিল", "cancel"]:
        await update.message.reply_text(
            "❌ **ব্রডকাস্ট বাতিল!**",
            reply_markup=get_admin_reply_keyboard()
        )
        return ConversationHandler.END
    
    users = get_all_active_users()
    
    if not users:
        await update.message.reply_text("❌ **কোনো সক্রিয় ইউজার নেই!**")
        return ConversationHandler.END
    
    success = 0
    failed = 0
    status_msg = await update.message.reply_text("⏳ **মেসেজ পাঠানো হচ্ছে...**")
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0],
                text=f"📢 **অ্যাডমিন থেকে বার্তা**\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                     f"{msg}\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    
    await status_msg.edit_text(
        f"✅ **ব্রডকাস্ট সম্পন্ন!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ সফল: {success} জন\n"
        f"❌ ব্যর্থ: {failed} জন"
    )
    return ConversationHandler.END

async def broadcast_image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    await update.message.reply_text(
        "🖼️ **ব্রডকাস্ট ইমেজ**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📸 ছবি পাঠান (ক্যাপশন সহ):\n"
        "❌ 'বাতিল' লিখে বাতিল করুন",
        reply_markup=get_cancel_keyboard()
    )
    return BROADCAST_IMAGE_WAIT

async def handle_broadcast_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check for cancel (if user sends text "বাতিল")
    if update.message.text and update.message.text.lower() in ["বাতিল", "cancel"]:
        await update.message.reply_text(
            "❌ **ব্রডকাস্ট বাতিল!**",
            reply_markup=get_admin_reply_keyboard()
        )
        return ConversationHandler.END
    
    if not update.message.photo:
        await update.message.reply_text("❌ **দয়া করে একটি ছবি পাঠান!**")
        return BROADCAST_IMAGE_WAIT
    
    photo = update.message.photo[-1]
    caption = update.message.caption or ""
    
    users = get_all_active_users()
    
    if not users:
        await update.message.reply_text("❌ **কোনো সক্রিয় ইউজার নেই!**")
        return ConversationHandler.END
    
    success = 0
    failed = 0
    status_msg = await update.message.reply_text("⏳ **ছবি পাঠানো হচ্ছে...**")
    
    for user in users:
        try:
            await context.bot.send_photo(
                chat_id=user[0],
                photo=photo.file_id,
                caption=f"📢 **অ্যাডমিন থেকে বার্তা**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"{caption}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    
    await status_msg.edit_text(
        f"✅ **ব্রডকাস্ট সম্পন্ন!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ সফল: {success} জন\n"
        f"❌ ব্যর্থ: {failed} জন"
    )
    return ConversationHandler.END

# ---------------- OTHER ----------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data['admin_mode'] = True
    await update.message.reply_text(
        "🔐 **অ্যাডমিন প্যানেল**",
        reply_markup=get_admin_reply_keyboard()
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ **প্রক্রিয়া বাতিল করা হয়েছে!**",
        reply_markup=get_active_reply_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

async def copy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    link = query.data.replace('copy_', '')
    await query.message.reply_text(f"`{link}`", parse_mode='Markdown')

# ---------------- MAIN ----------------
def main():
    keep_alive()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ============ START COMMAND (সবার আগে) ============
    app.add_handler(CommandHandler('start', start))

    # ============ REGISTRATION ============
    reg_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, set_username)],
        states={
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_username)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(reg_handler)

    # ============ ACTIVATION (FIXED) ============
    activation_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^✅ অ্যাকাউন্ট অ্যাক্টিভেট করুন$'), start_activation)],
        states={
            ACTIVATION_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, activation_method)],
            TXN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txnid)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(activation_handler)

    # ============ WITHDRAW (FIXED) ============
    withdraw_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^💸 উইথড্র$'), start_withdraw)],
        states={
            WITHDRAW_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_method)],
            WITHDRAW_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_phone)],
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(withdraw_handler)

    # ============ SUPPORT (FIXED) ============
    support_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🆘 সাপোর্ট$'), start_support)],
        states={
            SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_support)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(support_handler)

    # ============ ADMIN SEARCH ============
    admin_search_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔍 ইউজার খুঁজুন$'), handle_admin_messages)],
        states={
            SEARCH_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_user)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(admin_search_handler)

    # ============ ADMIN DETAILS ============
    admin_details_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📋 ইউজার ডিটেইলস$'), handle_admin_messages)],
        states={
            USER_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_details)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(admin_details_handler)

    # ============ ADMIN REPLY ============
    admin_reply_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reply, pattern='^reply_')],
        states={
            ADMIN_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_admin_reply)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(admin_reply_handler)

    # ============ BROADCAST TEXT ============
    broadcast_text_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📢 ব্রডকাস্ট$'), handle_admin_messages)],
        states={
            BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(broadcast_text_handler)

    # ============ BROADCAST IMAGE ============
    broadcast_image_handler = ConversationHandler(
        entry_points=[CommandHandler('broadcast_image', broadcast_image_command)],
        states={
            BROADCAST_IMAGE_WAIT: [MessageHandler(filters.PHOTO | filters.TEXT, handle_broadcast_image)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(broadcast_image_handler)

    # ============ OTHER HANDLERS ============
    app.add_handler(CommandHandler('admin', admin_command))
    app.add_handler(CommandHandler('cancel', cancel))
    app.add_handler(CallbackQueryHandler(copy_link, pattern='^copy_'))
    app.add_handler(CallbackQueryHandler(handle_admin_payment, pattern='^(app_pay|rej_pay)_'))
    app.add_handler(CallbackQueryHandler(handle_admin_withdraw, pattern='^(app_w|rej_w)_'))
    
    # ============ MAIN MESSAGE HANDLER (সবার শেষে) ============
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    print("🤖 Share2Pay Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
