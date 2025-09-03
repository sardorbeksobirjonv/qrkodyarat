# qr_bot_fixed.py
# Requirements:
# pip install aiogram==3.22.0 qrcode[pil] Pillow aiosqlite
#
# Edit: BOT_TOKEN and ADMINS before running.

import os
import time
import asyncio
import logging
import qrcode
import aiosqlite
from PIL import Image
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ---------------- CONFIG ----------------
BOT_TOKEN = "8159150210:AAEmPokXaK5wNsKAtnHCvlhz5nkB_fjjOjw"       # <-- set your bot token (NEVER share publicly)
ADMINS = [7752032178]                        # <-- replace with your Telegram user_id(s) (integers)
DB_PATH = "qrbot.db"
TMP_DIR = "tmp_qr"
os.makedirs(TMP_DIR, exist_ok=True)
MAX_SIZE = 16000   # max allowed pixel size (be cautious: large sizes require lots of RAM)
# ----------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
# Pass bot instance into Dispatcher and provide storage
dp = Dispatcher(bot=bot, storage=MemoryStorage())

# FSM states for user flow + admin flows
class States(StatesGroup):
    waiting_for_content = State()        # expecting text/file
    waiting_for_color = State()          # expecting color selection
    waiting_for_size = State()           # expecting size selection (or custom size text)
    admin_waiting_broadcast = State()    # admin: waiting for broadcast content
    admin_broadcast_confirm = State()    # admin: confirm broadcast
    admin_waiting_channel = State()      # admin: waiting for channel id to set mandatory

# Quick color keyboard (expandable)
COLOR_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="⚫ Black", callback_data="color:black"),
        InlineKeyboardButton(text="🔴 Red", callback_data="color:red"),
        InlineKeyboardButton(text="🟢 Green", callback_data="color:green")
    ],
    [
        InlineKeyboardButton(text="🔵 Blue", callback_data="color:blue"),
        InlineKeyboardButton(text="🟡 Yellow", callback_data="color:yellow"),
        InlineKeyboardButton(text="🌸 Pink", callback_data="color:pink")
    ],
    [
        InlineKeyboardButton(text="💜 Purple", callback_data="color:purple"),
        InlineKeyboardButton(text="⚪ Gray", callback_data="color:gray"),
        InlineKeyboardButton(text="🤎 Brown", callback_data="color:brown")
    ]
])

# Size keyboard (common sizes + larger options + custom)
SIZE_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="100px", callback_data="size:100"),
        InlineKeyboardButton(text="150px", callback_data="size:150"),
        InlineKeyboardButton(text="200px", callback_data="size:200")
    ],
    [
        InlineKeyboardButton(text="250px", callback_data="size:250"),
        InlineKeyboardButton(text="300px", callback_data="size:300"),
        InlineKeyboardButton(text="350px", callback_data="size:350")
    ],
    [
        InlineKeyboardButton(text="400px", callback_data="size:400"),
        InlineKeyboardButton(text="450px", callback_data="size:450"),
        InlineKeyboardButton(text="500px", callback_data="size:500")
    ],
    [
        InlineKeyboardButton(text="1000px", callback_data="size:1000"),
        InlineKeyboardButton(text="2000px", callback_data="size:2000"),
        InlineKeyboardButton(text="4000px", callback_data="size:4000")
    ],
    [
        InlineKeyboardButton(text="🔧 Custom (type px)", callback_data="size:custom"),
        InlineKeyboardButton(text=f"16K HD ({MAX_SIZE}px) ⚠️", callback_data=f"size:{MAX_SIZE}")
    ]
])

# Admin menu keyboard
def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Users count", callback_data="admin_users_count")],
        [InlineKeyboardButton(text="📜 Logs (recent)", callback_data="admin_logs")],
        [InlineKeyboardButton(text="🔗 Set mandatory channel", callback_data="admin_set_channel")],
        [InlineKeyboardButton(text="❌ Unset mandatory channel", callback_data="admin_unset_channel")],
        [InlineKeyboardButton(text="📢 Broadcast (send ad)", callback_data="admin_broadcast")]
    ])


# ---------------- DATABASE HELPERS ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_at TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT,
            content TEXT,
            color TEXT,
            size INTEGER,
            created_at TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        await db.commit()

async def save_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, joined_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user.id, user.username, user.first_name, user.last_name, datetime.now(timezone.utc).isoformat()))
        await db.commit()

async def add_log(user, action, content="", color="", size=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO logs (user_id, username, action, content, color, size, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user.id, user.username or "", action, (content or "")[:1000], color or "", int(size or 0), datetime.now(timezone.utc).isoformat()))
        await db.commit()

async def get_all_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        row = await cur.fetchone()
        return row[0] if row else 0

async def get_logs(limit=50):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, username, action, content, color, size, created_at FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return rows

async def set_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def get_setting(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

# ---------------- CHANNEL SUBSCRIPTION (MANDATORY) ----------------
async def is_user_member_of_mandatory_channel(user_id: int):
    channel = await get_setting("mandatory_channel")
    if not channel:
        # no mandatory channel set => allow
        return True
    # channel may be '@username' or numeric chat_id like -100...
    try:
        # try numeric id first
        chat_id = channel
        # if channel looks like an @username make sure to send username string
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        logger.exception("get_chat_member failed or channel invalid")
        return False

def make_sub_buttons(channel):
    # returns InlineKeyboardMarkup with a URL to the channel (if username provided) and check button
    buttons = []
    if channel and channel.startswith("@"):
        buttons.append([InlineKeyboardButton(text=f"Open {channel}", url=f"https://t.me/{channel.lstrip('@')}")])
    else:
        # can't build URL for plain chat_id; provide info message instead
        buttons.append([InlineKeyboardButton(text="Open channel in Telegram", callback_data="open_channel_info")])
    buttons.append([InlineKeyboardButton(text="✅ I subscribed / Verify", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery):
    ok = await is_user_member_of_mandatory_channel(call.from_user.id)
    if ok:
        await call.message.answer("✅ Verified — you are a member. Now you can use the bot.")
    else:
        await call.message.answer("❌ You are not yet a member of the mandatory channel.")
    await call.answer()

# ---------------- QR GENERATION ----------------
def generate_qr_file(content: str, color: str = "black", size: int = 300) -> str:
    """
    Create QR image and save to disk; return file path.
    Warning: very large `size` uses much RAM.
    """
    # create qrcode object
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color=color, back_color="white").convert("RGB")

    # resize to requested size
    img = img.resize((size, size), Image.NEAREST)

    filename = f"qr_{int(time.time()*1000)}_{os.getpid()}.png"
    path = os.path.join(TMP_DIR, filename)
    img.save(path, format="PNG")
    return path

# ---------------- HANDLERS ----------------
@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message, state: FSMContext):
    await init_db()
    await save_user(message.from_user)
    await add_log(message.from_user, "start", content="")
    # check mandatory channel
    mandatory = await get_setting("mandatory_channel")
    if mandatory:
        ok = await is_user_member_of_mandatory_channel(message.from_user.id)
        if not ok:
            await message.answer(f"❗ You must join the channel {mandatory} to use this bot.",
                                 reply_markup=make_sub_buttons(mandatory))
            return

    await state.set_state(States.waiting_for_content)
    await message.answer("👋 Send me text, photo, video or a document — I'll convert it to a QR code.\nAfter sending content you'll choose color and size.")

# content accepting: text/photo/video/document
@dp.message(States.waiting_for_content, F.text | F.photo | F.video | F.document)
async def received_content(message: Message, state: FSMContext):
    # ensure DB exists & save user
    await init_db()
    await save_user(message.from_user)

    # mandatory channel check
    if not await is_user_member_of_mandatory_channel(message.from_user.id):
        mandatory = await get_setting("mandatory_channel") or "channel"
        await message.answer(f"❗ You must join {mandatory} to use the bot.", reply_markup=make_sub_buttons(mandatory))
        await add_log(message.from_user, "blocked_not_subscribed", content="")
        return

    # extract content: either text or telegram file link (safe)
    content_value = None
    if message.text:
        content_value = message.text.strip()
    elif message.photo:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        # file.file_path may be None for very new files; handle defensively
        if getattr(file, 'file_path', None):
            content_value = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        else:
            content_value = file_id
    elif message.video:
        file_id = message.video.file_id
        file = await bot.get_file(file_id)
        if getattr(file, 'file_path', None):
            content_value = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        else:
            content_value = file_id
    elif message.document:
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        if getattr(file, 'file_path', None):
            content_value = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        else:
            content_value = file_id

    if not content_value:
        await message.answer("I couldn't read your message. Send text, photo, video or a document.")
        return

    # store in state, ask for color
    await state.update_data(content=content_value)
    await add_log(message.from_user, "content_sent", content=content_value)
    await state.set_state(States.waiting_for_color)
    await message.answer("🎨 Choose QR color:", reply_markup=COLOR_KB)

@dp.callback_query(lambda c: c.data and c.data.startswith("color:"), States.waiting_for_color)
async def color_chosen(call: CallbackQuery, state: FSMContext):
    color = call.data.split(":", 1)[1]
    await state.update_data(color=color)
    await state.set_state(States.waiting_for_size)
    await call.message.edit_text(f"Color selected: {color}\nNow choose size (px):", reply_markup=SIZE_KB)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("size:"), States.waiting_for_size)
async def size_chosen(call: CallbackQuery, state: FSMContext):
    size_val = call.data.split(":", 1)[1]
    if size_val == "custom":
        await call.message.edit_text("Send custom size in pixels (example: 1200). Be careful with huge sizes (memory!).")
        await call.answer()
        return

    size = int(size_val)
    if size < 100 or size > MAX_SIZE:
        await call.message.answer(f"Size must be between 100 and {MAX_SIZE} px.")
        await call.answer()
        return

    data = await state.get_data()
    content = data.get("content")
    color = data.get("color", "black")
    user = call.from_user

    await add_log(user, "generate_qr", content=content, color=color, size=size)

    path = None
    try:
        path = generate_qr_file(content=content, color=color, size=size)
        # send as photo using FSInputFile with filepath
        await call.message.answer_photo(photo=FSInputFile(path), caption=f"✅ Your QR (size={size}px, color={color})")
    except Exception as e:
        logger.exception("Failed to generate QR")
        await call.message.answer(f"Error while generating QR: {e}")
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    await state.clear()
    await call.answer()

@dp.message(States.waiting_for_size, F.text)
async def custom_size_text(message: Message, state: FSMContext):
    txt = message.text.strip()
    if not txt.isdigit():
        await message.answer("Please send a number (pixels).")
        return
    size = int(txt)
    if size < 100 or size > MAX_SIZE:
        await message.answer(f"Size must be between 100 and {MAX_SIZE} px.")
        return

    data = await state.get_data()
    content = data.get("content")
    color = data.get("color", "black")
    await add_log(message.from_user, "generate_qr", content=content, color=color, size=size)

    path = None
    try:
        path = generate_qr_file(content=content, color=color, size=size)
        await message.answer_photo(photo=FSInputFile(path), caption=f"✅ QR ready (size={size}px, color={color})")
    except Exception as e:
        logger.exception("Failed to generate QR")
        await message.answer(f"Error while generating QR: {e}")
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    await state.clear()

# ---------------- ADMIN PANEL ----------------
@dp.message(Command(commands=["admin"]))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMINS:
        await message.answer("❌ You are not an admin.")
        return
    await message.answer("🔧 Admin panel:", reply_markup=admin_menu_kb())

@dp.callback_query(lambda c: c.data and c.data == "admin_users_count")
async def cb_admin_users_count(call: CallbackQuery):
    if call.from_user.id not in ADMINS:
        await call.answer("Not authorized", show_alert=True); return
    cnt = await get_all_users_count()
    await call.message.answer(f"👥 Total users: {cnt}")
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data == "admin_logs")
async def cb_admin_logs(call: CallbackQuery):
    if call.from_user.id not in ADMINS:
        await call.answer("Not authorized", show_alert=True); return
    rows = await get_logs(limit=50)
    if not rows:
        await call.message.answer("No logs yet.")
    else:
        txt = "📜 Recent logs:\n"
        for r in rows:
            txt += f"{r[0]}. user={r[1]} @{r[2]} action={r[3]} size={r[6]} time={r[7]}\n"
        await call.message.answer(txt)
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data == "admin_set_channel")
async def cb_admin_set_channel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        await call.answer("Not authorized", show_alert=True); return
    await call.message.answer("Send channel username (like @mychannel) or chat_id (-100...) to set as mandatory channel.")
    await state.set_state(States.admin_waiting_channel)
    await call.answer()

@dp.message(States.admin_waiting_channel)
async def admin_save_channel(message: Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        await message.answer("Not authorized"); await state.clear(); return
    ch = message.text.strip()
    await set_setting("mandatory_channel", ch)
    await message.answer(f"Mandatory channel set to: {ch}")
    await state.clear()

@dp.callback_query(lambda c: c.data and c.data == "admin_unset_channel")
async def cb_admin_unset_channel(call: CallbackQuery):
    if call.from_user.id not in ADMINS:
        await call.answer("Not authorized", show_alert=True); return
    await set_setting("mandatory_channel", "")
    await call.message.answer("Mandatory channel unset.")
    await call.answer()

@dp.callback_query(lambda c: c.data and c.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        await call.answer("Not authorized", show_alert=True); return
    await call.message.answer("Send broadcast message (text/photo/video/document).")
    await state.set_state(States.admin_waiting_broadcast)
    await call.answer()

@dp.message(States.admin_waiting_broadcast, F.text | F.photo | F.video | F.document)
async def admin_broadcast_collect(message: Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        await message.answer("Not authorized"); await state.clear(); return
    b = {}
    if message.text:
        b["type"] = "text"; b["text"] = message.text
    elif message.photo:
        b["type"] = "photo"; b["file_id"] = message.photo[-1].file_id
    elif message.video:
        b["type"] = "video"; b["file_id"] = message.video.file_id
    elif message.document:
        b["type"] = "document"; b["file_id"] = message.document.file_id
    await state.update_data(broadcast=b)
    await state.set_state(States.admin_broadcast_confirm)
    await message.answer("Confirm broadcast? (Yes / No)", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Yes, send", callback_data="broadcast_send")],
        [InlineKeyboardButton(text="Cancel", callback_data="broadcast_cancel")]
    ]))

@dp.callback_query(lambda c: c.data in ("broadcast_send","broadcast_cancel"), States.admin_broadcast_confirm)
async def admin_broadcast_confirm(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        await call.answer("Not authorized", show_alert=True); await state.clear(); return
    if call.data == "broadcast_cancel":
        await call.message.answer("Broadcast cancelled.")
        await state.clear()
        await call.answer()
        return

    data = await state.get_data()
    b = data.get("broadcast")
    if not b:
        await call.message.answer("Nothing to send."); await state.clear(); await call.answer(); return

    # Get users
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        user_ids = [r[0] for r in rows]

    sent = 0; failed = 0
    await call.message.answer(f"Broadcast started to {len(user_ids)} users...")
    for uid in user_ids:
        try:
            if b["type"] == "text":
                await bot.send_message(uid, b["text"])
            elif b["type"] == "photo":
                await bot.send_photo(uid, b["file_id"], caption="📢 Broadcast")
            elif b["type"] == "video":
                await bot.send_video(uid, b["file_id"], caption="📢 Broadcast")
            elif b["type"] == "document":
                await bot.send_document(uid, b["file_id"], caption="📢 Broadcast")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            await asyncio.sleep(0.05)

    await call.message.answer(f"Broadcast finished. Sent: {sent}, Failed: {failed}")
    await add_log(call.from_user, "broadcast_sent", content=str(b))
    await state.clear()
    await call.answer()

# Fallback for other texts - encourage /start
@dp.message()
async def fallback(message: Message):
    await message.answer("Send text/photo/video/document to create QR. Use /start to begin or /admin for admin panel.")

# --------------- START BOT ---------------
async def on_startup():
    await init_db()
    logging.info("Bot started")

async def main():
    try:
        await on_startup()
        # start polling (this will run until process stopped)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
