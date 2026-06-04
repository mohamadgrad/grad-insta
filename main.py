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

DB_NAME = "bot_ultimate.db"  # نام جدید برای اطمینان از ساختار صحیح
DOWNLOAD_DIR = "downloads"
DEFAULT_PREM_TEXT = "💎 تعرفه اشتراک:\n1 ماهه: 50 هزار تومان\nبرای خرید به پشتیبانی پیام دهید."

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InstaBot")

# استیت ادمین
admin_states = {}


# ==========================================
# 🗄️ دیتابیس (DATABASE ENGINE)
# ==========================================
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # کاربران
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            lang TEXT, 
            dl_count INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0,
            prem_expire INTEGER DEFAULT 0,
            join_date INTEGER,
            is_banned INTEGER DEFAULT 0
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

        conn.commit()


# --- توابع مدیریت دیتابیس ---
def get_user(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.cursor().execute("SELECT * FROM users WHERE user_id=?",
                                     (user_id, )).fetchone()


def add_user(user_id, lang=None):
    if get_user(user_id): return
    with sqlite3.connect(DB_NAME) as conn:
        conn.cursor().execute(
            "INSERT INTO users (user_id, lang, join_date) VALUES (?, ?, ?)",
            (user_id, lang, int(time.time())))


def update_lang(user_id, lang):
    add_user(user_id, lang)  # اطمینان از وجود کاربر
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

    # لیمیت رایگان
    if user[2] < 10: return True
    return False


init_db()

# ==========================================
# 🌍 زبان‌ها (LOCALIZATION)
# ==========================================
LANGS = {
    'fa': {
        'welcome_select': "👋 سلام! خوش آمدید.\nلطفاً زبان خود را انتخاب کنید:",
        'menu': "📥 لینک پست یا ریلز اینستاگرام را ارسال کنید.",
        'wait': "⏳ در حال دانلود... (برای آلبوم‌های حجیم کمی صبر کنید)",
        'uploading': "🚀 دانلود شد! در حال آپلود...",
        'limit':
        "⚠️ محدودیت دانلود رایگان تمام شده.\n💎 برای خرید اشتراک پیام دهید.",
        'error': "❌ خطا در دانلود. لینک نامعتبر یا پرایوت است.",
        'receipt': "✅ رسید دریافت شد. منتظر بررسی باشید.",
        'ticket_sent': "✅ پیام ارسال شد.",
        'ticket_accepted': "👤 **ادمین {}** تیکت شما را پذیرفت.",
        'ticket_closed': "🔒 تیکت بسته شد.",
        'caption': "📥 دانلود شده توسط: {}\n👤 آپلودر: {}",
        'btn_share': "اشتراک‌گذاری 📤",
        'btn_join': "کانال ما 📢",
        'join_alert':
        "⚠️ ابتدا باید عضو کانال زیر شوید:\n{}\nسپس /start بزنید.",
        'banned': "⛔️ مسدود هستید.",
        'btn_account': "حساب من 👤",
        'btn_support': "پشتیبانی 💬",
        'account_info':
        "👤 **حساب شما**\n🆔 شناسه: `{}`\n📦 دانلودها: `{}`\n💎 وضعیت: {}\n\n{}",
        'prem_status': "پرمیوم (تا: {} | {} روز مانده)",
        'free_status': "رایگان",
        'support_header': "📝 پیام خود را ارسال کنید:",
    },
    'en': {
        'welcome_select': "👋 Hello! Welcome.\nPlease select your language:",
        'menu': "📥 Send Instagram link.",
        'wait': "⏳ Downloading... Please wait.",
        'uploading': "🚀 Uploading...",
        'limit': "⚠️ Free limit reached.\n💎 Contact support.",
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
        'account_info':
        "👤 **My Account**\n🆔 ID: `{}`\n📦 Downloads: `{}`\n💎 Status: {}\n\n{}",
        'prem_status': "Premium (Until: {} | {} days left)",
        'free_status': "Free",
        'support_header': "📝 Send your message:",
    }
}


def tr(user_id, key):
    u = get_user(user_id)
    l = u[1] if u and u[1] else 'fa'
    return LANGS.get(l, LANGS['fa']).get(key, "")


def get_main_kb(user_id):
    return ReplyKeyboardMarkup([[
        KeyboardButton(tr(user_id, 'btn_account')),
        KeyboardButton(tr(user_id, 'btn_support'))
    ]],
                               resize_keyboard=True)


# ==========================================
# 🚀 کلاینت
# ==========================================
app = Client(
    "InstaBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    ipv6=False  # پایداری شبکه
)

dl_queue = asyncio.Queue()
executor = ThreadPoolExecutor(max_workers=3)


async def worker():
    print("👷 Worker Started...")
    while True:
        client, message, url = await dl_queue.get()
        try:
            await process_download(client, message, url)
        except Exception as e:
            print(f"Worker Error: {e}")
        finally:
            dl_queue.task_done()
            await asyncio.sleep(1)


# ==========================================
# 📥 دانلودر (Multi-Link Fix)
# ==========================================
def run_ytdlp_sync(url, path):
    ydl_opts = {
        'outtmpl': f'{path}/%(id)s.%(ext)s',
        'cookiefile': 'cookies.txt',
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'writethumbnail': False,  # جلوگیری از دانلود عکس تامنیل جداگانه
        'socket_timeout': 30,
    }
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


async def process_download(client, message, url):
    user_id = message.from_user.id
    msg = await message.reply(tr(user_id, 'wait'))
    folder = f"{DOWNLOAD_DIR}/{user_id}_{int(time.time())}"
    if not os.path.exists(folder): os.makedirs(folder)

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(executor, run_ytdlp_sync, url,
                                          folder)
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

        # گروه‌بندی فایل‌ها برای ارسال آلبوم (Multi-Post Fix)
        # تلگرام نهایتاً 10 فایل را در یک مدیاگروپ قبول می‌کند
        def chunker(seq, size):
            return (seq[pos:pos + size] for pos in range(0, len(seq), size))

        media_chunks = list(chunker(media_files, 10))

        for i, chunk in enumerate(media_chunks):
            media_group = []
            for f in chunk:
                ext = f.split('.')[-1].lower()
                # فقط برای فایل اول کپشن می‌گذاریم
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

        # اگر آلبوم فرستادیم، دکمه را جدا می‌فرستیم چون مدیاگروپ دکمه نمی‌گیرد
        if len(media_files) > 1:
            await client.send_message(user_id, "👇", reply_markup=btns)

        increment_dl(user_id)

    except Exception as e:
        logger.error(f"DL Error: {e}")
        try:
            await msg.edit(tr(user_id, 'error'))
        except:
            pass
    finally:
        if os.path.exists(folder): shutil.rmtree(folder)
        try:
            await msg.delete()
        except:
            pass


# ==========================================
# 🎮 هندلر استارت (فیکس شده)
# ==========================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(c, m):
    user_id = m.from_user.id
    u = get_user(user_id)

    # اگر کاربر در دیتابیس نیست یا زبان ندارد
    if not u or not u[1]:
        # اضافه کردن کاربر به دیتابیس (بدون زبان)
        add_user(user_id)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("فارسی 🇮🇷", callback_data="lang_fa"),
            InlineKeyboardButton("English 🇺🇸", callback_data="lang_en")
        ]])
        return await m.reply(LANGS['fa']['welcome_select'], reply_markup=kb)

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


# ==========================================
# 📡 روتر اصلی (پیام‌ها و دکمه‌ها)
# ==========================================
async def main_router(c, m):
    user_id = m.from_user.id

    # اطمینان از وجود کاربر (گیت‌کیپر)
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

    if text == btn_acc:
        p_txt = get_setting('prem_text')
        if check_access(user_id) and u[3] == 1:
            dt = datetime.datetime.fromtimestamp(u[4]).strftime('%Y-%m-%d')
            rem_days = int((u[4] - time.time()) / 86400)
            if rem_days < 0: rem_days = 0
            st = tr(user_id, 'prem_status').format(dt, rem_days)
        else:
            st = tr(user_id, 'free_status')
        info = tr(user_id, 'account_info').format(user_id, u[2], st, p_txt)
        return await m.reply(info)

    if "instagram.com" in text:
        if check_access(user_id):
            await dl_queue.put((c, m, text))
        else:
            await m.reply(tr(user_id, 'limit'))
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
