import os
import time
import asyncio
import sqlite3
import glob
import shutil
import logging
import datetime
import re
from concurrent.futures import ThreadPoolExecutor

from pyrogram import Client, filters, enums
from pyrogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                            ReplyKeyboardMarkup, KeyboardButton, Message,
                            InputMediaPhoto, InputMediaVideo, CallbackQuery,
                            ReplyKeyboardRemove)
from pyrogram.errors import FloodWait, UserNotParticipant, RPCError
from yt_dlp import YoutubeDL

# تلاش برای ایمپورت keep_alive
try:
    from keep_alive import keep_alive
except ImportError:
    keep_alive = None

# ==========================================
# ⚙️ تنظیمات (CONFIGURATION)
# ==========================================
API_ID = 27534153
API_HASH = "6fda758efb46c2c3d4bab7063524a57e"
BOT_TOKEN = "8576127403:AAE-vyj272dQY38qYFsUNfwzbm2uY1tDpbc"
OWNER_ID = 1281887809
BOT_USERNAME = "@Download_insta_Grad_bot"

DB_NAME = "bot_ultimate.db"
DOWNLOAD_DIR = "downloads"
DEFAULT_PREM_TEXT = "💎 تعرفه اشتراک:\n1 ماهه: 50 هزار تومان\nبرای خرید به پشتیبانی پیام دهید."

FREE_DOWNLOAD_LIMIT = 4  # تغییر از 10 به 4

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InstaBot")

# استیت ادمین
admin_states = {}

# استیت انتخاب کیفیت
quality_states = {}


# ==========================================
# 🗄️ دیتابیس (DATABASE ENGINE)
# ==========================================
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # کاربران - با فیلدهای جدید referral
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            lang TEXT, 
            dl_count INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0,
            prem_expire INTEGER DEFAULT 0,
            join_date INTEGER,
            is_banned INTEGER DEFAULT 0,
            last_reset INTEGER DEFAULT 0,
            ref_by INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            ref_step INTEGER DEFAULT 0
        )''')

        # ادمین‌ها
        c.execute('''CREATE TABLE IF NOT EXISTS admins (
            admin_id INTEGER PRIMARY KEY,
            name TEXT,
            added_by INTEGER,
            permissions TEXT DEFAULT 'all'
        )''')

        # تیکت‌ها
        c.execute('''CREATE TABLE IF NOT EXISTS active_tickets (
            user_id INTEGER PRIMARY KEY,
            claimed_by INTEGER,
            start_time INTEGER
        )''')

        # تنظیمات
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        # دیتای اولیه
        c.execute(
            "INSERT OR IGNORE INTO admins (admin_id, name, added_by, permissions) VALUES (?, ?, ?, ?)",
            (OWNER_ID, "Owner", 0, "all"))
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('lock_channel', 'off')"
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('prem_text', ?)",
            (DEFAULT_PREM_TEXT, ))
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('ref_required', '4')"
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('ref_days', '7')"
        )

        # اضافه کردن ستون‌های جدید اگر وجود ندارند (برای دیتابیس‌های قدیمی)
        try:
            c.execute("ALTER TABLE users ADD COLUMN last_reset INTEGER DEFAULT 0")
        except:
            pass
        try:
            c.execute("ALTER TABLE users ADD COLUMN ref_by INTEGER DEFAULT 0")
        except:
            pass
        try:
            c.execute("ALTER TABLE users ADD COLUMN ref_count INTEGER DEFAULT 0")
        except:
            pass
        try:
            c.execute("ALTER TABLE users ADD COLUMN ref_step INTEGER DEFAULT 0")
        except:
            pass

        conn.commit()


# --- توابع مدیریت دیتابیس ---
def get_user(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.cursor().execute("SELECT * FROM users WHERE user_id=?",
                                     (user_id, )).fetchone()


def add_user(user_id, lang=None, ref_by=0):
    if get_user(user_id):
        # اگر کاربر وجود دارد ولی ref_by ندارد و ref_by داده شده
        if ref_by:
            with sqlite3.connect(DB_NAME) as conn:
                conn.cursor().execute(
                    "UPDATE users SET ref_by=? WHERE user_id=? AND (ref_by IS NULL OR ref_by=0)",
                    (ref_by, user_id))
        return
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute(
            "INSERT INTO users (user_id, lang, join_date, ref_by) VALUES (?, ?, ?, ?)",
            (user_id, lang, int(time.time()), ref_by))


def update_lang(user_id, lang):
    add_user(user_id, lang)
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute("UPDATE users SET lang=? WHERE user_id=?",
                              (lang, user_id))


def increment_dl(user_id):
    add_user(user_id)
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute(
            "UPDATE users SET dl_count = dl_count + 1 WHERE user_id=?",
            (user_id, ))


def set_premium_db(user_id, days):
    add_user(user_id)
    expire = int(time.time()) + (int(days) * 86400)
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute(
            "UPDATE users SET is_premium=1, prem_expire=? WHERE user_id=?",
            (expire, user_id))
    return expire


def ban_user_db(user_id, status=1):
    add_user(user_id)
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute("UPDATE users SET is_banned=? WHERE user_id=?",
                              (status, user_id))


# --- مدیریت ادمین ---
def add_admin_db(admin_id, name, added_by, permissions):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute(
            "INSERT OR REPLACE INTO admins VALUES (?, ?, ?, ?)",
            (admin_id, name, added_by, permissions))


def remove_admin_db(admin_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute("DELETE FROM admins WHERE admin_id=?",
                              (admin_id, ))


def get_all_admins():
    with sqlite3.connect(DB_NAME) as conn:
        return conn.cursor().execute("SELECT * FROM admins").fetchall()


def get_admin_info(admin_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.cursor().execute("SELECT * FROM admins WHERE admin_id=?",
                                     (admin_id, )).fetchone()


def check_perm(admin_id, perm):
    if admin_id == OWNER_ID: return True
    info = get_admin_info(admin_id)
    if not info: return False
    perms = info[3]
    if perms == 'all': return True
    return perm in perms.split(',')


# --- تیکتینگ ---
def claim_ticket(user_id, admin_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT claimed_by FROM active_tickets WHERE user_id=?",
                (user_id, ))
            res = cur.fetchone()
            if res: return False, res[0]
            cur.execute("INSERT INTO active_tickets VALUES (?, ?, ?)",
                        (user_id, admin_id, int(time.time())))
            return True, admin_id
    except:
        return False, 0


def get_ticket_owner(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute(
            "SELECT claimed_by FROM active_tickets WHERE user_id=?",
            (user_id, )).fetchone()
        return res[0] if res else None


def close_ticket(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute("DELETE FROM active_tickets WHERE user_id=?",
                              (user_id, ))


# --- تنظیمات ---
def get_setting(key):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.cursor().execute("SELECT value FROM settings WHERE key=?",
                                    (key, )).fetchone()
        return res[0] if res else ""


def set_setting(key, value):
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute("INSERT OR REPLACE INTO settings VALUES (?, ?)",
                              (str(key), str(value)))


def get_stats_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        u = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        d = c.execute("SELECT SUM(dl_count) FROM users").fetchone()[0] or 0
        p = c.execute(
            "SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
    return u, d, p


def check_access(user_id):
    user = get_user(user_id)
    if not user: return False
    # بن بودن (ستون 6)
    if user[6] == 1: return False

    # پرمیوم
    if user[3] == 1:
        if user[4] > int(time.time()): return True
        else:
            with sqlite3.connect(DB_NAME) as conn:
                conn.cursor().execute(
                    "UPDATE users SET is_premium=0 WHERE user_id=?",
                    (user_id, ))

    # سیستم ریست 24 ساعته
    now = int(time.time())
    last_reset = user[7] if len(user) > 7 and user[7] else 0
    dl_count = user[2]

    # اگر 24 ساعت از آخرین ریست گذشته، ریست کن
    if last_reset and (now - last_reset) >= 86400:
        with sqlite3.connect(DB_NAME) as conn:
            conn.cursor().execute(
                "UPDATE users SET dl_count=0, last_reset=? WHERE user_id=?",
                (now, user_id))
        dl_count = 0

    # اگر اولین بار است که چک می‌شود، last_reset را ست کن
    if not last_reset:
        with sqlite3.connect(DB_NAME) as conn:
            conn.cursor().execute(
                "UPDATE users SET last_reset=? WHERE user_id=?",
                (now, user_id))

    # لیمیت رایگان
    if dl_count < FREE_DOWNLOAD_LIMIT: return True
    return False


def get_remaining_downloads(user_id):
    """تعداد دانلودهای باقی‌مانده در 24 ساعت جاری"""
    user = get_user(user_id)
    if not user: return FREE_DOWNLOAD_LIMIT
    if user[3] == 1 and user[4] > int(time.time()):
        return -1  # نامحدود برای پرمیوم
    now = int(time.time())
    last_reset = user[7] if len(user) > 7 and user[7] else 0
    dl_count = user[2]

    if last_reset and (now - last_reset) >= 86400:
        return FREE_DOWNLOAD_LIMIT
    if not last_reset:
        return FREE_DOWNLOAD_LIMIT
    remaining = FREE_DOWNLOAD_LIMIT - dl_count
    return max(0, remaining)


init_db()

# ==========================================
# 🌍 زبان‌ها (LOCALIZATION)
# ==========================================
LANGS = {
    'fa': {
        'welcome_select': "👋 سلام! خوش آمدید.\nلطفاً زبان خود را انتخاب کنید:",
        'menu': "📥 لینک پست یا ریلز اینستاگرام را ارسال کنید.",
        'wait': "⏳ در حال دریافت اطلاعات کیفیت‌ها...",
        'downloading': "⏳ در حال دانلود... (برای آلبوم‌های حجیم کمی صبر کنید)",
        'uploading': "🚀 دانلود شد! در حال آپلود...",
        'limit': "⚠️ محدودیت دانلود رایگان تمام شده.\n💎 برای خرید اشتراک پیام دهید.",
        'limit_reached': "⚠️ شما به محدودیت {0} دانلود رایگان در ۲۴ ساعت رسیده‌اید!\n\nبرای ادامه می‌توانید:\n1️⃣ اشتراک پرمیوم خریداری کنید\n2️⃣ دوستان خود را دعوت کنید و پرمیوم رایگان بگیرید",
        'error': "❌ خطا در دانلود. لینک نامعتبر یا پرایوت است.",
        'receipt': "✅ رسید دریافت شد. منتظر بررسی باشید.",
        'ticket_sent': "✅ پیام ارسال شد.",
        'ticket_accepted': "👤 **ادمین {}** تیکت شما را پذیرفت.",
        'ticket_closed': "🔒 تیکت بسته شد.",
        'caption': "📥 دانلود شده توسط: {}\n👤 آپلودر: {}",
        'btn_share': "اشتراک‌گذاری 📤",
        'btn_join': "کانال ما 📢",
        'join_alert': "⚠️ ابتدا باید عضو کانال زیر شوید:\n{}\nسپس /start بزنید.",
        'banned': "⛔️ مسدود هستید.",
        'btn_account': "حساب من 👤",
        'btn_support': "پشتیبانی 💬",
        'btn_lang': "🌐 تغییر زبان",
        'btn_buy_premium': "💎 خرید پرمیوم",
        'btn_invite': "👥 دعوت دوستان",
        'account_info': "👤 **حساب شما**\n🆔 شناسه: `{}`\n📦 دانلودها: `{}`\n💎 وضعیت: {}\n📊 دانلود باقی‌مانده: {}\n\n{}",
        'prem_status': "پرمیوم (تا: {} | {} روز مانده)",
        'free_status': "رایگان",
        'support_header': "📝 پیام خود را ارسال کنید:",
        'select_quality': "📥 **کیفیت مورد نظر را انتخاب کنید:**\n\n{0}",
        'quality_btn': "{0} - {1}",
        'quality_low': "کیفیت پایین",
        'quality_medium': "کیفیت متوسط",
        'quality_high': "کیفیت بالا",
        'quality_very_high': "کیفیت بسیار بالا",
        'quality_unknown': "نامشخص",
        'cookie_error': "❌ **خطای احراز هویت اینستاگرام!**\nکوکی‌های اینستاگرام منقضی شده یا نامعتبر هستند.\nلطفاً به ادمین اطلاع دهید تا کوکی‌ها را به‌روزرسانی کند.",
        'retrying': "🔄 تلاش مجدد... ({0}/{1})",
        'download_failed': "❌ دانلود پس از {0} تلاش ناموفق بود.\nلطفاً دوباره تلاش کنید.",
        'premium_info': "💎 **خرید اشتراک پرمیوم**\n\nبا خرید اشتراک پرمیوم می‌توانید:\n✅ دانلود نامحدود\n✅ بدون محدودیت روزانه\n✅ اولویت در دانلود\n\n{0}\n\nبرای خرید به پشتیبانی پیام دهید.",
        'invite_info': "👥 **سیستم دعوت دوستان**\n\nبا دعوت {0} دوست، {1} روز پرمیوم رایگان دریافت کنید!\n\n**شرایط:**\nهر دوست باید:\n1️⃣ لینک دعوت شما را کلیک کند\n2️⃣ ربات را استارت کند (/start)\n3️⃣ یک لینک اینستاگرام ارسال کند و دانلود موفق داشته باشد\n\nپس از انجام تمام مراحل توسط {0} دوست، به صورت خودکار پرمیوم رایگان دریافت می‌کنید.\n\n🔗 **لینک دعوت شما:**\n`{2}`\n\n👥 تعداد دوستان دعوت شده: {3}\n✅ تعداد دوستان موفق: {4}",
        'your_ref_link': "🔗 لینک دعوت شما:\n`{0}`",
        'ref_success': "🎉 تبریک! شما {0} دوست موفق دعوت کرده‌اید و {1} روز پرمیوم رایگان دریافت کردید!",
        'ref_progress': "📊 **پیشرفت دعوت شما:**\n\nتعداد دوستان مورد نیاز: {0}\nتعداد دوستان موفق: {1}\n\n{2}",
        'ref_joined': "👤 کاربر {0} با لینک دعوت شما وارد شد!",
        'ref_downloaded': "✅ کاربر {0} اولین دانلود خود را انجام داد! یک قدم به پرمیوم رایگان نزدیک‌تر شدید.",
        'remaining_dl': "📊 دانلود باقی‌مانده در ۲۴ ساعت: {0}",
        'no_premium': "رایگان",
        'unlimited': "نامحدود (پرمیوم)",
    },
    'en': {
        'welcome_select': "👋 Hello! Welcome.\nPlease select your language:",
        'menu': "📥 Send Instagram link.",
        'wait': "⏳ Fetching quality options...",
        'downloading': "⏳ Downloading... Please wait.",
        'uploading': "🚀 Uploading...",
        'limit': "⚠️ Free limit reached.\n💎 Contact support.",
        'limit_reached': "⚠️ You have reached the limit of {0} free downloads in 24 hours!\n\nTo continue you can:\n1️⃣ Buy premium subscription\n2️⃣ Invite friends and get free premium",
        'error': "❌ Download Error.",
        'receipt': "✅ Receipt received.",
        'ticket_sent': "✅ Message sent.",
        'ticket_accepted': "👤 **Admin {}** accepted your ticket.",
        'ticket_closed': "🔒 Ticket closed.",
        'caption': "📥 Downloaded by: {}\n👤 Uploader: {}",
        'btn_share': "Share 📤",
        'btn_join': "Channel 📢",
        'join_alert': "⚠️ Join channel first:\n{}",
        'banned': "⛔️ You are banned.",
        'btn_account': "My Account 👤",
        'btn_support': "Support 💬",
        'btn_lang': "🌐 Change Language",
        'btn_buy_premium': "💎 Buy Premium",
        'btn_invite': "👥 Invite Friends",
        'account_info': "👤 **My Account**\n🆔 ID: `{}`\n📦 Downloads: `{}`\n💎 Status: {}\n📊 Remaining Downloads: {}\n\n{}",
        'prem_status': "Premium (Until: {} | {} days left)",
        'free_status': "Free",
        'support_header': "📝 Send your message:",
        'select_quality': "📥 **Select quality:**\n\n{0}",
        'quality_btn': "{0} - {1}",
        'quality_low': "Low Quality",
        'quality_medium': "Medium Quality",
        'quality_high': "High Quality",
        'quality_very_high': "Very High Quality",
        'quality_unknown': "Unknown",
        'cookie_error': "❌ **Instagram Authentication Error!**\nInstagram cookies are expired or invalid.\nPlease contact admin to update cookies.",
        'retrying': "🔄 Retrying... ({0}/{1})",
        'download_failed': "❌ Download failed after {0} attempts.\nPlease try again.",
        'premium_info': "💎 **Buy Premium Subscription**\n\nWith premium you get:\n✅ Unlimited downloads\n✅ No daily limit\n✅ Download priority\n\n{0}\n\nContact support to purchase.",
        'invite_info': "👥 **Invite Friends System**\n\nInvite {0} friends and get {1} days of free premium!\n\n**Requirements:**\nEach friend must:\n1️⃣ Click your invite link\n2️⃣ Start the bot (/start)\n3️⃣ Send an Instagram link and successfully download\n\nAfter all {0} friends complete these steps, you'll automatically get free premium.\n\n🔗 **Your invite link:**\n`{2}`\n\n👥 Total invited: {3}\n✅ Successful: {4}",
        'your_ref_link': "🔗 Your invite link:\n`{0}`",
        'ref_success': "🎉 Congratulations! You've invited {0} successful friends and received {1} days of free premium!",
        'ref_progress': "📊 **Your referral progress:**\n\nRequired friends: {0}\nSuccessful friends: {1}\n\n{2}",
        'ref_joined': "👤 User {0} joined via your invite link!",
        'ref_downloaded': "✅ User {0} completed their first download! One step closer to free premium.",
        'remaining_dl': "📊 Remaining downloads in 24h: {0}",
        'no_premium': "Free",
        'unlimited': "Unlimited (Premium)",
    }
}


def tr(user_id, key):
    u = get_user(user_id)
    l = u[1] if u and u[1] else 'fa'
    return LANGS.get(l, LANGS['fa']).get(key, "")


def get_main_kb(user_id):
    return ReplyKeyboardMarkup([
        [KeyboardButton(tr(user_id, 'btn_account')),
         KeyboardButton(tr(user_id, 'btn_support'))],
        [KeyboardButton(tr(user_id, 'btn_lang')),
         KeyboardButton(tr(user_id, 'btn_invite'))]
    ],
                               resize_keyboard=True)


# ==========================================
# 🚀 کلاینت
# ==========================================
app = Client(
    "InstaBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    ipv6=False
)

dl_queue = asyncio.Queue()
executor = ThreadPoolExecutor(max_workers=3)


async def worker():
    print("👷 Worker Started...")
    while True:
        client, message, url, quality = await dl_queue.get()
        try:
            await process_download(client, message, url, quality)
        except Exception as e:
            print(f"Worker Error: {e}")
        finally:
            dl_queue.task_done()
            await asyncio.sleep(1)


# ==========================================
# 📥 دانلودر (با انتخاب کیفیت و retry)
# ==========================================
def get_available_qualities(url):
    """دریافت کیفیت‌های موجود با سایز فایل"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'cookiefile': 'cookies.txt',
        'socket_timeout': 30,
    }
    qualities = []
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None, "no_info"
            
            # بررسی کوکی
            if info.get('extractor', '') == 'instagram' and not info.get('entries') and not info.get('formats'):
                return None, "cookie_error"
            
            formats = info.get('formats', [])
            if not formats:
                # اگر فرمتی نبود، شاید یک ویدیوی ساده است
                return None, "no_formats"
            
            seen_qualities = set()
            for f in formats:
                height = f.get('height', 0)
                ext = f.get('ext', 'mp4')
                filesize = f.get('filesize', 0) or f.get('filesize_approx', 0)
                
                if not height or ext not in ['mp4', 'webm']:
                    continue
                
                # گروه‌بندی کیفیت‌ها
                if height <= 360:
                    q_label = "low"
                elif height <= 480:
                    q_label = "medium"
                elif height <= 720:
                    q_label = "high"
                else:
                    q_label = "very_high"
                
                if q_label not in seen_qualities:
                    seen_qualities.add(q_label)
                    size_mb = round(filesize / (1024 * 1024), 1) if filesize else 0
                    qualities.append({
                        'label': q_label,
                        'height': height,
                        'size': size_mb,
                        'format_id': f['format_id'],
                        'ext': ext
                    })
            
            # مرتب‌سازی بر اساس کیفیت
            quality_order = {'low': 0, 'medium': 1, 'high': 2, 'very_high': 3}
            qualities.sort(key=lambda x: quality_order.get(x['label'], 0))
            
            return qualities, info
    except Exception as e:
        error_str = str(e).lower()
        if 'cookie' in error_str or 'login' in error_str or 'auth' in error_str:
            return None, "cookie_error"
        return None, f"error: {str(e)}"


def run_ytdlp_sync(url, path, format_id=None):
    ydl_opts = {
        'outtmpl': f'{path}/%(id)s.%(ext)s',
        'cookiefile': 'cookies.txt',
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'writethumbnail': False,
        'socket_timeout': 30,
    }
    if format_id:
        ydl_opts['format'] = f'{format_id}+bestaudio/best'
    else:
        ydl_opts['format'] = 'best'
    
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


async def process_download(client, message, url, quality=None):
    user_id = message.from_user.id
    msg = await message.reply(tr(user_id, 'downloading'))
    folder = f"{DOWNLOAD_DIR}/{user_id}_{int(time.time())}"
    if not os.path.exists(folder):
        os.makedirs(folder)

    max_retries = 3
    last_error = None

    try:
        success = False
        for attempt in range(1, max_retries + 1):
            try:
                loop = asyncio.get_event_loop()
                
                format_id = quality.get('format_id') if quality else None
                info = await loop.run_in_executor(executor, run_ytdlp_sync, url, folder, format_id)
                
                uploader = info.get('uploader', 'Instagram') if info else 'Instagram'

                # پیدا کردن همه فایل‌ها
                files = glob.glob(os.path.join(folder, "*"))
                # فیلتر کردن فایل‌های مدیا
                media_files = [
                    f for f in files if f.split('.')[-1].lower() in
                    ['mp4', 'jpg', 'jpeg', 'png', 'webp']
                ]

                if not media_files:
                    await msg.edit(tr(user_id, 'error'))
                    return

                await msg.edit(tr(user_id, 'uploading'))
                caption = tr(user_id, 'caption').format(BOT_USERNAME, uploader)
                btns = InlineKeyboardMarkup([[
                    InlineKeyboardButton(tr(user_id, 'btn_share'),
                                         url=f"https://t.me/share/url?url={url}")
                ]])

                # گروه‌بندی فایل‌ها برای ارسال آلبوم
                def chunker(seq, size):
                    return (seq[pos:pos + size] for pos in range(0, len(seq), size))

                media_chunks = list(chunker(media_files, 10))

                for i, chunk in enumerate(media_chunks):
                    media_group = []
                    for f in chunk:
                        ext = f.split('.')[-1].lower()
                        cap = caption if (i == 0 and f == chunk[0]) else ""

                        if ext == 'mp4':
                            media_group.append(InputMediaVideo(f, caption=cap))
                        else:
                            media_group.append(InputMediaPhoto(f, caption=cap))

                    if len(media_group) > 1:
                        await client.send_media_group(user_id, media_group)
                    elif len(media_group) == 1:
                        f = media_group[0].media
                        if isinstance(media_group[0], InputMediaVideo):
                            await client.send_video(user_id,
                                                    f,
                                                    caption=media_group[0].caption,
                                                    reply_markup=btns)
                        else:
                            await client.send_photo(user_id,
                                                    f,
                                                    caption=media_group[0].caption,
                                                    reply_markup=btns)

                if len(media_files) > 1:
                    await client.send_message(user_id, "👇", reply_markup=btns)

                # افزایش تعداد دانلود
                increment_dl(user_id)
                
                # بررسی referral step
                u = get_user(user_id)
                if u and len(u) > 10 and u[10] == 1:  # ref_step == 1 (started via referral)
                    with sqlite3.connect(DB_NAME) as conn:
                        conn.cursor().execute(
                            "UPDATE users SET ref_step=2 WHERE user_id=?",  # completed
                            (user_id, ))
                    # اطلاع به دعوت‌کننده
                    ref_by = u[8] if len(u) > 8 else 0
                    if ref_by:
                        # افزایش ref_count دعوت‌کننده
                        conn = sqlite3.connect(DB_NAME)
                        conn.cursor().execute(
                            "UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?",
                            (ref_by, ))
                        conn.commit()
                        
                        # بررسی آیا دعوت‌کننده به تعداد کافی رسیده
                        inviter = get_user(ref_by)
                        ref_required = int(get_setting('ref_required') or '4')
                        ref_days = int(get_setting('ref_days') or '7')
                        if inviter and len(inviter) > 9 and inviter[9] >= ref_required:
                            # اعطای پرمیوم
                            set_premium_db(ref_by, ref_days)
                            try:
                                await client.send_message(
                                    ref_by,
                                    tr(ref_by, 'ref_success').format(ref_required, ref_days))
                            except:
                                pass
                        
                        try:
                            await client.send_message(
                                ref_by,
                                tr(ref_by, 'ref_downloaded').format(user_id))
                        except:
                            pass
                        conn.close()

                # پاک کردن state کیفیت
                if user_id in quality_states:
                    del quality_states[user_id]

                success = True
                return  # موفقیت

            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                logger.error(f"DL Error (attempt {attempt}/{max_retries}): {e}")
                
                # اگر خطای کوکی بود، بدون retry
                if 'cookie' in error_str or 'login' in error_str or 'auth' in error_str:
                    try:
                        await msg.edit(tr(user_id, 'cookie_error'))
                    except:
                        pass
                    return
                
                if attempt < max_retries:
                    try:
                        await msg.edit(tr(user_id, 'retrying').format(attempt, max_retries))
                    except:
                        pass
                    await asyncio.sleep(2)
                else:
                    try:
                        await msg.edit(tr(user_id, 'download_failed').format(max_retries))
                    except:
                        pass
        
        if not success:
            try:
                await msg.edit(tr(user_id, 'error'))
            except:
                pass
    finally:
        if os.path.exists(folder):
            shutil.rmtree(folder)
        try:
            await msg.delete()
        except:
            pass


# ==========================================
# 🎮 هندلر استارت (با referral)
# ==========================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    user_id = m.from_user.id
    u = get_user(user_id)
    
    # بررسی referral
    ref_by = 0
    if m.text and len(m.text.split()) > 1:
        arg = m.text.split()[1]
        if arg.startswith('ref_'):
            try:
                ref_by = int(arg.split('_')[1])
                if ref_by == user_id:
                    ref_by = 0  # نمی‌تواند خودش را دعوت کند
            except:
                ref_by = 0

    # اگر کاربر در دیتابیس نیست یا زبان ندارد
    if not u or not u[1]:
        add_user(user_id, ref_by=ref_by)
        
        # اگر با referral آمده، ref_step = 1
        if ref_by:
            with sqlite3.connect(DB_NAME) as conn:
                conn.cursor().execute(
                    "UPDATE users SET ref_step=1 WHERE user_id=?",
                    (user_id, ))
            try:
                await c.send_message(ref_by, tr(ref_by, 'ref_joined').format(user_id))
            except:
                pass
        
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("فارسی 🇮🇷", callback_data="lang_fa"),
            InlineKeyboardButton("English 🇺🇸", callback_data="lang_en")
        ]])
        return await m.reply(LANGS['fa']['welcome_select'], reply_markup=kb)
    
    # اگر کاربر با referral آمده ولی قبلاً ثبت‌نام کرده
    if ref_by and (not u[8] or u[8] == 0):
        with sqlite3.connect(DB_NAME) as conn:
            conn.cursor().execute(
                "UPDATE users SET ref_by=?, ref_step=1 WHERE user_id=?",
                (ref_by, user_id))
        try:
            await c.send_message(ref_by, tr(ref_by, 'ref_joined').format(user_id))
        except:
            pass

    # اگر کاربر زبان دارد، چک کردن جوین اجباری
    chn = get_setting('lock_channel')
    if chn and chn != 'off' and user_id != OWNER_ID:
        try:
            await c.get_chat_member(chn, user_id)
        except UserNotParticipant:
            return await m.reply(tr(user_id, 'join_alert').format(chn))
        except:
            pass

    return await m.reply(tr(user_id, 'menu'),
                         reply_markup=get_main_kb(user_id))


# ==========================================
# 👮‍♂️ پنل ادمین
# ==========================================
@app.on_message(filters.command("admin") & filters.private)
async def admin_panel_handler(c, m):
    user_id = m.from_user.id
    info = get_admin_info(user_id)
    if not info: return

    if user_id in admin_states: del admin_states[user_id]

    perms = info[3]
    btns = []

    btns.append(
        [InlineKeyboardButton("📊 آمار ربات", callback_data="adm_stats")])

    if check_perm(user_id, 'add_admin'):
        btns.append([
            InlineKeyboardButton("➕ افزودن ادمین", callback_data="adm_add"),
            InlineKeyboardButton("🗑 حذف ادمین", callback_data="adm_list")
        ])

    row3 = []
    if check_perm(user_id, 'settings'):
        row3.append(
            InlineKeyboardButton("📢 کانال جوین", callback_data="adm_channel"))
        row3.append(
            InlineKeyboardButton("💎 متن تعرفه", callback_data="adm_text"))
    if row3: btns.append(row3)

    row4 = []
    if check_perm(user_id, 'prem'):
        row4.append(
            InlineKeyboardButton("🎁 اعطای پرمیوم", callback_data="adm_prem"))
    if check_perm(user_id, 'bc'):
        row4.append(
            InlineKeyboardButton("📤 ارسال همگانی", callback_data="adm_bc"))
    if row4: btns.append(row4)

    if check_perm(user_id, 'ban'):
        btns.append([
            InlineKeyboardButton("⛔️ مسدود کردن کاربر",
                                 callback_data="adm_ban")
        ])

    # دکمه‌های جدید تنظیمات referral
    if check_perm(user_id, 'settings'):
        btns.append([
            InlineKeyboardButton("👥 تنظیمات دعوت", callback_data="adm_ref_settings")
        ])

    btns.append([InlineKeyboardButton("❌ بستن", callback_data="adm_close")])

    await m.reply(f"👤 **پنل مدیریت**\nسلام {info[1]}",
                  reply_markup=InlineKeyboardMarkup(btns))


@app.on_callback_query(filters.regex("^adm_"))
async def admin_callbacks(c, cb):
    uid = cb.from_user.id
    if not get_admin_info(uid): return
    data = cb.data

    if data == "adm_close":
        await cb.message.delete()
    elif data == "adm_stats":
        u, d, p = get_stats_db()
        await cb.answer(
            f"📊 آمار:\n👥 کاربران: {u}\n📥 دانلودها: {d}\n💎 پرمیوم: {p}",
            show_alert=True)
    elif data == "adm_add":
        if not check_perm(uid, 'add_admin'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'add_admin_name'}
        await cb.message.reply("👤 نام ادمین جدید:")
    elif data == "adm_list":
        if not check_perm(uid, 'add_admin'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admins = get_all_admins()
        btns = []
        for adm in admins:
            aid, aname, aperm = adm
            if aid != OWNER_ID:
                btns.append([
                    InlineKeyboardButton(f"❌ حذف: {aname}",
                                         callback_data=f"del_adm_{aid}")
                ])
            else:
                btns.append([
                    InlineKeyboardButton(f"👑 مالک: {aname}",
                                         callback_data="ignore")
                ])
        btns.append(
            [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")])
        await cb.message.edit_text("📋 لیست ادمین‌ها:",
                                   reply_markup=InlineKeyboardMarkup(btns))
    elif data == "adm_back":
        await admin_panel_handler(c, cb.message)
    elif data == "adm_channel":
        if not check_perm(uid, 'settings'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'set_channel'}
        await cb.message.reply("📢 آیدی کانال با @ (یا off):")
    elif data == "adm_text":
        if not check_perm(uid, 'settings'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'set_prem_text'}
        await cb.message.reply("💎 متن جدید تعرفه:")
    elif data == "adm_prem":
        if not check_perm(uid, 'prem'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'prem_id'}
        await cb.message.reply("🎁 آیدی عددی کاربر:")
    elif data == "adm_ban":
        if not check_perm(uid, 'ban'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'ban_id'}
        await cb.message.reply("⛔️ آیدی کاربر جهت مسدودسازی:")
    elif data == "adm_bc":
        if not check_perm(uid, 'bc'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'broadcast'}
        await cb.message.reply("📢 پیام همگانی را بفرستید:")
    elif data == "adm_ref_settings":
        if not check_perm(uid, 'settings'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        ref_required = get_setting('ref_required') or '4'
        ref_days = get_setting('ref_days') or '7'
        btns = [
            [InlineKeyboardButton(f"👥 تعداد دوستان مورد نیاز: {ref_required}",
                                  callback_data="adm_ref_required")],
            [InlineKeyboardButton(f"📅 روز پرمیوم جایزه: {ref_days}",
                                  callback_data="adm_ref_days")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_back")]
        ]
        await cb.message.edit_text(
            "👥 **تنظیمات سیستم دعوت دوستان**\n\n"
            "از اینجا می‌توانید تعداد دوستان مورد نیاز و روزهای پرمیوم جایزه را تنظیم کنید.",
            reply_markup=InlineKeyboardMarkup(btns))
    elif data == "adm_ref_required":
        if not check_perm(uid, 'settings'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'set_ref_required'}
        await cb.message.reply("👥 تعداد دوستان مورد نیاز برای دریافت پرمیوم رایگان را وارد کنید:")
    elif data == "adm_ref_days":
        if not check_perm(uid, 'settings'):
            return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
        admin_states[uid] = {'step': 'set_ref_days'}
        await cb.message.reply("📅 تعداد روزهای پرمیوم جایزه را وارد کنید:")


@app.on_callback_query(filters.regex("^del_adm_"))
async def delete_admin_cb(c, cb):
    uid = cb.from_user.id
    if not check_perm(uid, 'add_admin'):
        return await cb.answer("❌ دسترسی ندارید.", show_alert=True)
    target_id = int(cb.data.split("_")[2])
    remove_admin_db(target_id)
    await cb.answer("✅ حذف شد.", show_alert=True)
    await cb.message.delete()


@app.on_callback_query(filters.regex("^perm_"))
async def permission_cb(c, cb):
    uid = cb.from_user.id
    data_parts = cb.data.split("_")
    p_type = data_parts[1]
    target_id = int(data_parts[2])

    state = admin_states.get(uid)
    if not state or 'temp_name' not in state:
        return await cb.answer("❌ منقضی شد.", show_alert=True)

    name = state['temp_name']
    perms = 'all' if p_type == 'full' else 'ban,prem'

    add_admin_db(target_id, name, uid, perms)
    del admin_states[uid]
    await cb.message.edit_text(f"✅ ادمین **{name}** اضافه شد.")


# ==========================================
# 🧠 ورودی متنی ادمین
# ==========================================
@app.on_message(filters.private & filters.text
                & ~filters.command(["start", "admin"]))
async def admin_input_handler(c, m):
    user_id = m.from_user.id
    state = admin_states.get(user_id)

    # اگر استیت ندارد، یعنی ادمین کاری نمی‌کند -> برود روتر اصلی
    if not state:
        await main_router(c, m)
        return

    step = state['step']
    text = m.text

    if step == 'add_admin_name':
        admin_states[user_id] = {'step': 'add_admin_id', 'temp_name': text}
        await m.reply("✅ آیدی عددی را بفرستید:")

    elif step == 'add_admin_id':
        if not text.isdigit(): return await m.reply("❌ فقط عدد.")
        target_id = text
        admin_states[user_id]['step'] = 'wait_perm'
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👑 دسترسی کامل",
                                     callback_data=f"perm_full_{target_id}")
            ],
            [
                InlineKeyboardButton("💬 محدود (بن/پرمیوم)",
                                     callback_data=f"perm_support_{target_id}")
            ]
        ])
        await m.reply("🔑 سطح دسترسی:", reply_markup=kb)

    elif step == 'set_channel':
        val = "off" if text.lower() == 'off' else text
        set_setting('lock_channel', val)
        del admin_states[user_id]
        await m.reply("✅ انجام شد.")

    elif step == 'set_prem_text':
        set_setting('prem_text', text)
        del admin_states[user_id]
        await m.reply("✅ انجام شد.")

    elif step == 'prem_id':
        if text.isdigit():
            admin_states[user_id] = {'step': 'prem_days', 'target': int(text)}
            await m.reply("📅 تعداد روز؟")
    elif step == 'prem_days':
        if text.isdigit():
            tid = state['target']
            days = int(text)
            expire_ts = set_premium_db(tid, days)
            del admin_states[user_id]
            date_str = datetime.datetime.fromtimestamp(expire_ts).strftime(
                '%Y-%m-%d')
            await m.reply(f"✅ انجام شد.")
            try:
                await c.send_message(tid,
                                     f"🎉 حساب شما تا {date_str} پرمیوم شد.")
            except:
                pass

    elif step == 'ban_id':
        if text.isdigit():
            ban_user_db(int(text), 1)
            del admin_states[user_id]
            await m.reply(f"⛔️ مسدود شد.")

    elif step == 'broadcast':
        msg = await m.reply("⏳ در حال ارسال...")
        with sqlite3.connect(DB_NAME) as conn:
            users = [
                row[0]
                for row in conn.cursor().execute("SELECT user_id FROM users")
            ]
        count = 0
        for uid in users:
            try:
                await m.copy(uid)
                count += 1
                await asyncio.sleep(0.05)
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except:
                pass
        await msg.edit(f"✅ ارسال به {count} نفر.")
        del admin_states[user_id]

    elif step == 'set_ref_required':
        if text.isdigit() and int(text) > 0:
            set_setting('ref_required', text)
            del admin_states[user_id]
            await m.reply(f"✅ تعداد دوستان مورد نیاز به {text} تنظیم شد.")
        else:
            await m.reply("❌ لطفاً یک عدد معتبر وارد کنید.")

    elif step == 'set_ref_days':
        if text.isdigit() and int(text) > 0:
            set_setting('ref_days', text)
            del admin_states[user_id]
            await m.reply(f"✅ تعداد روزهای پرمیوم جایزه به {text} تنظیم شد.")
        else:
            await m.reply("❌ لطفاً یک عدد معتبر وارد کنید.")


# ==========================================
# 📡 روتر اصلی (پیام‌ها و دکمه‌ها)
# ==========================================
async def main_router(c, m):
    user_id = m.from_user.id

    # اطمینان از وجود کاربر
    add_user(user_id)
    u = get_user(user_id)

    # اگر بن شده
    if u[6] == 1:
        return await m.reply(tr(user_id, 'banned'))

    # اگر زبان ندارد -> استارت اجباری
    if not u[1]:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("فارسی 🇮🇷", callback_data="lang_fa"),
            InlineKeyboardButton("English 🇺🇸", callback_data="lang_en")
        ]])
        return await m.reply(LANGS['fa']['welcome_select'], reply_markup=kb)

    text = m.text or ""

    btn_acc = tr(user_id, 'btn_account')
    btn_sup = tr(user_id, 'btn_support')
    btn_lang = tr(user_id, 'btn_lang')
    btn_invite = tr(user_id, 'btn_invite')

    if text == btn_acc:
        p_txt = get_setting('prem_text')
        remaining = get_remaining_downloads(user_id)
        if remaining == -1:
            remaining_text = tr(user_id, 'unlimited')
        else:
            remaining_text = str(remaining)
        
        if check_access(user_id) and u[3] == 1:
            dt = datetime.datetime.fromtimestamp(u[4]).strftime('%Y-%m-%d')
            rem_days = int((u[4] - time.time()) / 86400)
            if rem_days < 0: rem_days = 0
            st = tr(user_id, 'prem_status').format(dt, rem_days)
        else:
            st = tr(user_id, 'free_status')
        info = tr(user_id, 'account_info').format(user_id, u[2], st, remaining_text, p_txt)
        return await m.reply(info)

    if text == btn_lang:
        # تغییر زبان
        current_lang = u[1] if u and u[1] else 'fa'
        new_lang = 'en' if current_lang == 'fa' else 'fa'
        update_lang(user_id, new_lang)
        lang_name = "English 🇺🇸" if new_lang == 'en' else "فارسی 🇮🇷"
        await m.reply(f"✅ {lang_name}\n{tr(user_id, 'menu')}",
                      reply_markup=get_main_kb(user_id))
        return

    if text == btn_invite:
        ref_required = int(get_setting('ref_required') or '4')
        ref_days = int(get_setting('ref_days') or '7')
        ref_link = f"https://t.me/{BOT_USERNAME.replace('@', '')}?start=ref_{user_id}"
        ref_count = u[9] if len(u) > 9 else 0  # ref_count
        total_invited = u[8] if len(u) > 8 else 0  # ref_by count (approximate)
        
        # محاسبه تعداد کل دعوت‌ها (کسانی که با لینک این کاربر آمده‌اند)
        with sqlite3.connect(DB_NAME) as conn:
            total_invited = conn.cursor().execute(
                "SELECT COUNT(*) FROM users WHERE ref_by=?", (user_id,)
            ).fetchone()[0] or 0
        
        msg = tr(user_id, 'invite_info').format(
            ref_required, ref_days, ref_link, total_invited, ref_count)
        
        # نوار پیشرفت
        progress_bar = ""
        if ref_count > 0:
            filled = min(ref_count, ref_required)
            empty = ref_required - filled
            progress_bar = "🟩" * filled + "⬜" * empty
        
        if progress_bar:
            msg += f"\n\n{progress_bar}"
        
        return await m.reply(msg)

    # تشخیص تمام انواع لینک‌های اینستاگرام
    instagram_patterns = [
        "instagram.com", "instagr.am", "instagr.am/",
        "instagram.com/p/", "instagram.com/reel/", "instagram.com/reels/",
        "instagram.com/stories/", "instagram.com/tv/", "instagram.com/s/",
        "instagram.com/p/", "instagram.com/explore/",
        "dl.instagram.com", "instagram.f"
    ]
    is_instagram_link = any(p in text.lower() for p in instagram_patterns)
    
    if is_instagram_link:
        if check_access(user_id):
            # ابتدا کیفیت‌ها را دریافت کن
            await m.reply(tr(user_id, 'wait'))
            qualities, info = await asyncio.get_event_loop().run_in_executor(
                executor, get_available_qualities, text)
            
            if qualities is None:
                if info == "cookie_error":
                    return await m.reply(tr(user_id, 'cookie_error'))
                elif info == "no_formats":
                    # اگر فرمتی نبود، با کیفیت پیش‌فرض دانلود کن
                    await dl_queue.put((c, m, text, None))
                    return
                else:
                    return await m.reply(tr(user_id, 'error'))
            
            if not qualities:
                # اگر کیفیتی پیدا نشد، با پیش‌فرض دانلود کن
                await dl_queue.put((c, m, text, None))
                return
            
            # ساخت دکمه‌های انتخاب کیفیت
            quality_btns = []
            quality_labels = {
                'low': tr(user_id, 'quality_low'),
                'medium': tr(user_id, 'quality_medium'),
                'high': tr(user_id, 'quality_high'),
                'very_high': tr(user_id, 'quality_very_high')
            }
            
            for q in qualities:
                label = quality_labels.get(q['label'], tr(user_id, 'quality_unknown'))
                size_str = f"{q['size']}MB" if q['size'] else "?"
                btn_text = tr(user_id, 'quality_btn').format(label, size_str)
                quality_btns.append([
                    InlineKeyboardButton(
                        btn_text,
                        callback_data=f"qlty_{q['label']}_{q['format_id']}_{user_id}")
                ])
            
            # ذخیره url در state
            quality_states[user_id] = {'url': text}
            
            await m.reply(
                tr(user_id, 'select_quality').format(f"🔗 {text}"),
                reply_markup=InlineKeyboardMarkup(quality_btns))
        else:
            # محدودیت دانلود
            remaining = get_remaining_downloads(user_id)
            if remaining <= 0:
                ref_required = int(get_setting('ref_required') or '4')
                ref_days = int(get_setting('ref_days') or '7')
                ref_link = f"https://t.me/{BOT_USERNAME.replace('@', '')}?start=ref_{user_id}"
                
                btns = InlineKeyboardMarkup([
                    [InlineKeyboardButton(tr(user_id, 'btn_buy_premium'), callback_data="buy_premium")],
                    [InlineKeyboardButton(tr(user_id, 'btn_invite'), callback_data=f"show_invite_{user_id}")]
                ])
                await m.reply(
                    tr(user_id, 'limit_reached').format(FREE_DOWNLOAD_LIMIT),
                    reply_markup=btns)
            else:
                await m.reply(tr(user_id, 'remaining_dl').format(remaining))
        return

    if text == btn_sup:
        return await m.reply(tr(user_id, 'support_header'))

    # تیکتینگ برای پیام‌های غیر دستوری
    if user_id != OWNER_ID:
        owner_id = get_ticket_owner(user_id)
        if owner_id:
            try:
                await c.send_message(owner_id, f"👤 پیام از: `{user_id}`")
                await m.copy(owner_id)
            except:
                close_ticket(user_id)
        else:
            admins = get_all_admins()
            now = datetime.datetime.now().strftime("%H:%M")
            caption = f"📨 **تیکت جدید**\n👤 کاربر: `{user_id}`\n⏰ ساعت: {now}\n\nبرای پاسخ دکمه زیر را بزنید:"
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ قبول تیکت",
                                     callback_data=f"claim_{user_id}")
            ]])
            for adm in admins:
                try:
                    await c.send_message(adm[0], caption, reply_markup=markup)
                    await m.copy(adm[0])
                except:
                    pass
            await m.reply(tr(user_id, 'ticket_sent'))


# ==========================================
# 📞 کالبک‌های کیفیت
# ==========================================
@app.on_callback_query(filters.regex("^qlty_"))
async def quality_callback(c, cb):
    data = cb.data.split("_")
    q_label = data[1]
    q_format_id = data[2]
    user_id = int(data[3])
    
    if cb.from_user.id != user_id:
        return await cb.answer("❌ این دکمه برای شما نیست!", show_alert=True)
    
    state = quality_states.get(user_id)
    if not state or 'url' not in state:
        return await cb.answer("❌ منقضی شد. دوباره لینک را بفرستید.", show_alert=True)
    
    url = state['url']
    quality = {'label': q_label, 'format_id': q_format_id}
    
    await cb.message.delete()
    await cb.answer(f"✅ {q_label} selected", show_alert=False)
    
    # ارسال به صف دانلود
    await dl_queue.put((c, cb.message, url, quality))


# ==========================================
# 📞 کالبک‌های خرید و دعوت
# ==========================================
@app.on_callback_query(filters.regex("^buy_premium$"))
async def buy_premium_callback(c, cb):
    user_id = cb.from_user.id
    p_txt = get_setting('prem_text')
    await cb.message.edit_text(
        tr(user_id, 'premium_info').format(p_txt))
    await cb.answer()


@app.on_callback_query(filters.regex("^show_invite_"))
async def show_invite_callback(c, cb):
    user_id = int(cb.data.split("_")[2])
    if cb.from_user.id != user_id:
        return await cb.answer("❌ این دکمه برای شما نیست!", show_alert=True)
    
    ref_required = int(get_setting('ref_required') or '4')
    ref_days = int(get_setting('ref_days') or '7')
    ref_link = f"https://t.me/{BOT_USERNAME.replace('@', '')}?start=ref_{user_id}"
    
    u = get_user(user_id)
    ref_count = u[9] if u and len(u) > 9 else 0
    
    with sqlite3.connect(DB_NAME) as conn:
        total_invited = conn.cursor().execute(
            "SELECT COUNT(*) FROM users WHERE ref_by=?", (user_id,)
        ).fetchone()[0] or 0
    
    msg = tr(user_id, 'invite_info').format(
        ref_required, ref_days, ref_link, total_invited, ref_count)
    
    # نوار پیشرفت
    if ref_count > 0:
        filled = min(ref_count, ref_required)
        empty = ref_required - filled
        progress_bar = "🟩" * filled + "⬜" * empty
        msg += f"\n\n{progress_bar}"
    
    await cb.message.edit_text(msg)
    await cb.answer()


# ==========================================
# 📞 کالبک‌های زبان
# ==========================================
@app.on_callback_query(filters.regex("^lang_"))
async def lang_callback(c, cb):
    lang = cb.data.split("_")[1]
    user_id = cb.from_user.id
    update_lang(user_id, lang)
    await cb.message.delete()
    msg = LANGS[lang]['menu']
    await c.send_message(user_id,
                         f"✅ Language set.\n{msg}",
                         reply_markup=get_main_kb(user_id))


# ==========================================
# 📞 کالبک‌های تیکت
# ==========================================
@app.on_callback_query(filters.regex("^claim_"))
async def claim_ticket_cb(c, cb):
    admin_id = cb.from_user.id
    target_user_id = int(cb.data.split("_")[1])
    success, owner_id = claim_ticket(target_user_id, admin_id)
    if success:
        ainfo = get_admin_info(admin_id)
        aname = ainfo[1] if ainfo else "Admin"
        await cb.message.edit_text(f"✅ **توسط {aname} قبول شد.**")
        try:
            await c.send_message(
                target_user_id,
                tr(target_user_id, 'ticket_accepted').format(aname))
        except:
            pass
        await cb.answer("تیکت باز شد.", show_alert=True)
    else:
        await cb.answer("❌ قبلاً گرفته شده.", show_alert=True)
        await cb.message.delete()


@app.on_callback_query(filters.regex("^close_ticket_"))
async def close_ticket_cb(c, cb):
    target_user = int(cb.data.split("_")[2])
    close_ticket(target_user)
    await c.send_message(target_user, tr(target_user, 'ticket_closed'))
    await cb.message.edit_text("✅ مکالمه بسته شد.")


# ==========================================
# 📞 پاسخ ادمین به تیکت
# ==========================================
@app.on_message(filters.private & filters.reply)
async def admin_reply_router(c, m):
    admin_id = m.from_user.id
    if not get_admin_info(admin_id): return

    target_id = None
    orig = m.reply_to_message

    # پیدا کردن آیدی از متن پیام ربات
    if orig.text and "پیام از:" in orig.text:
        try:
            target_id = int(orig.text.split("`")[1])
        except:
            pass
    elif orig.text and "کاربر:" in orig.text:
        try:
            target_id = int(re.search(r"کاربر: `(\d+)`", orig.text).group(1))
        except:
            pass

    if target_id:
        owner = get_ticket_owner(target_id)
        if not owner: claim_ticket(target_id, admin_id)
        elif owner != admin_id: return await m.reply("❌ دست ادمین دیگری است.")

        await c.send_message(target_id, "👤 **پاسخ پشتیبانی:**")
        await m.copy(target_id)
        await m.reply("✅ ارسال شد.")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔒 بستن مکالمه",
                                 callback_data=f"close_ticket_{target_id}")
        ]])
        await c.send_message(admin_id, "مدیریت:", reply_markup=kb)


if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if keep_alive: keep_alive()
    loop = asyncio.get_event_loop()
    loop.create_task(worker())
    print("✅ Bot Started Successfully...")
    app.run()