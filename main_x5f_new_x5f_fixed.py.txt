# -*- coding: utf-8 -*-
import os, json, requests, re, logging, asyncio
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS") or os.getenv("TELEGRAM_ADMIN_ID") or ""
if ADMIN_IDS_STR:
    try:
        ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.replace(";",",").split(",") if x.strip().isdigit()]
    except:
        ADMIN_IDS = []
else:
    ADMIN_IDS = []

PORT = int(os.getenv("PORT", 8080))
# URL publik Railway kamu - WAJIB diisi di Variables
RAILWAY_URL = os.getenv("RAILWAY_URL", "").rstrip("/") or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
if RAILWAY_URL and not RAILWAY_URL.startswith("http"):
    RAILWAY_URL = f"https://{RAILWAY_URL}"

# ========== SUPABASE ==========
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "bot_data")

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def supabase_enabled():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def load_db_from_supabase():
    if not supabase_enabled():
        return None
    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        params = {"select": "id,data_json", "order": "id.asc", "limit": "1"}
        r = requests.get(url, headers=supabase_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        payload = rows[0].get("data_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload if isinstance(payload, dict) else None
    except Exception as e:
        logger.error(f"Supabase LOAD ERROR: {e}")
        return None

def save_db_to_supabase(data):
    if not supabase_enabled():
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        params = {"select": "id", "order": "id.asc", "limit": "1"}
        r = requests.get(url, headers=supabase_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        body = {"data_json": data}
        if rows:
            row_id = rows[0]["id"]
            r = requests.patch(f"{url}?id=eq.{row_id}", headers=supabase_headers(), json=body, timeout=15)
        else:
            r = requests.post(url, headers=supabase_headers(), json=body, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Supabase SAVE ERROR: {e}")
        return False

DB_FILE = "bot_database.json"
def load_db():
    remote = load_db_from_supabase()
    if remote is not None:
        logger.info("DB loaded from Supabase")
        return remote
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Local DB load error: {e}")
    return {"user_info": {}, "langganan": {}, "langganan_cari": {}, "blacklist": [], "pending_hapus_kota": []}

def save_db():
    tmp = json.loads(json.dumps(db, default=str))
    save_db_to_supabase(tmp)
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Local DB save error: {e}")

db = load_db()

flask_app = Flask(__name__)
# Alias untuk gunicorn biar bisa pakai main_new:app atau main_new:flask_app
app = flask_app

# ========== TELEGRAM APPLICATION (Global) ==========
if not TOKEN:
    logger.warning("TOKEN Telegram tidak ditemukan di ENV!")
    application = None
else:
    application = Application.builder().token(TOKEN).build()

# ========== FUNGSI BANTUAN ==========
def is_admin(uid): return uid in ADMIN_IDS

def kb_main(uid):
    keyboard = [
        ["👤 PROFIL", "🌍 TAMBAH KOTA"],
        ["🌠 WILAYAH DIPILIH", "🗑️ HAPUS KOTA"],
        ["📊 STATUS LANGGANAN", "💳 TOP UP"],
        ["🔎 CARI DATA", "🚀 PILIH KEYWORD"],
        ["🚫 BLACKLIST", "❓ BANTUAN"],
        ["🧑‍💻 HUBUNGI ADMIN"],
    ]
    if is_admin(uid):
        keyboard.append(["🧭 PANEL ADMIN"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_provinces():
    return [
        {"id": "11", "nama": "ACEH"}, {"id": "12", "nama": "SUMATERA UTARA"},
        {"id": "13", "nama": "SUMATERA BARAT"}, {"id": "14", "nama": "RIAU"},
        {"id": "15", "nama": "JAMBI"}, {"id": "16", "nama": "SUMATERA SELATAN"},
        {"id": "17", "nama": "BENGKULU"}, {"id": "18", "nama": "LAMPUNG"},
        {"id": "31", "nama": "DKI JAKARTA"}, {"id": "32", "nama": "JAWA BARAT"},
        {"id": "33", "nama": "JAWA TENGAH"}, {"id": "34", "nama": "DI YOGYAKARTA"},
        {"id": "35", "nama": "JAWA TIMUR"}, {"id": "36", "nama": "BANTEN"},
        {"id": "51", "nama": "BALI"}, {"id": "52", "nama": "NUSA TENGGARA BARAT"},
        {"id": "53", "nama": "NUSA TENGGARA TIMUR"}, {"id": "61", "nama": "KALIMANTAN BARAT"},
        {"id": "62", "nama": "KALIMANTAN TENGAH"}, {"id": "63", "nama": "KALIMANTAN SELATAN"},
        {"id": "64", "nama": "KALIMANTAN TIMUR"}, {"id": "65", "nama": "KALIMANTAN UTARA"},
        {"id": "71", "nama": "SULAWESI UTARA"}, {"id": "72", "nama": "SULAWESI TENGAH"},
        {"id": "73", "nama": "SULAWESI SELATAN"}, {"id": "74", "nama": "SULAWESI TENGGARA"},
        {"id": "75", "nama": "GORONTALO"}, {"id": "76", "nama": "SULAWESI BARAT"},
        {"id": "81", "nama": "MALUKU"}, {"id": "82", "nama": "MALUKU UTARA"},
        {"id": "91", "nama": "PAPUA"}, {"id": "92", "nama": "PAPUA BARAT"},
    ]

REKENING_TEXT = """
💳 TOP UP SALDO

🏦 SEABANK: 901040978290 - HAMBALI
💰 DANA: 083824101264 - HAMBALI

📸 Kirim foto bukti transfer!
"""

PAKET_TAMBAH = {
    "1minggu": {"nama": "1 MINGGU", "harga": 50000, "hari": 7},
    "1bulan": {"nama": "1 BULAN", "harga": 150000, "hari": 30},
    "2bulan": {"nama": "2 BULAN", "harga": 250000, "hari": 60},
}
PAKET_CARI = {
    "1minggu": {"nama": "1 MINGGU", "harga": 15000, "hari": 7},
    "1bulan": {"nama": "1 BULAN", "harga": 50000, "hari": 30},
    "2bulan": {"nama": "2 BULAN", "harga": 100000, "hari": 60},
}

def get_status_text(uid):
    user_data = db.get("user_info", {}).get(str(uid), {})
    jml = len(user_data.get("kotas", []))
    subs = db.get("langganan", {}).get(str(uid), [])
    return f"👤 ID: {uid}\n🌍 Kota: {jml}\n📦 Langganan: {len(subs)}"

# ========== HANDLER TELEGRAM ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if str(uid) not in db.get("user_info", {}):
        db["user_info"][str(uid)] = {"kotas": []}
        save_db()
    await update.message.reply_text(
        f"Halo {update.effective_user.first_name}! 👋\nBot Aktif di Railway.",
        reply_markup=kb_main(uid)
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    
    if text == "👤 PROFIL":
        await update.message.reply_text(get_status_text(uid), reply_markup=kb_main(uid))
    elif text == "🌍 TAMBAH KOTA":
        buttons = []
        row = []
        for prov in get_provinces():
            row.append(InlineKeyboardButton(prov["nama"], callback_data=f"prov_{prov['id']}_{prov['nama']}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        await update.message.reply_text("🌍 PILIH PROVINSI:", reply_markup=InlineKeyboardMarkup(buttons))
    elif text == "🌠 WILAYAH DIPILIH":
        kotas = db.get("user_info", {}).get(str(uid), {}).get("kotas", [])
        if not kotas:
            await update.message.reply_text("Belum ada kota dipilih.", reply_markup=kb_main(uid))
        else:
            msg = "🌠 WILAYAH DIPILIH:\n\n" + "\n".join([f"{i+1}. {k}" for i, k in enumerate(kotas)])
            await update.message.reply_text(msg, reply_markup=kb_main(uid))
    elif text == "💳 TOP UP":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 TOP UP TAMBAH KOTA", callback_data="topup_tambah")],
            [InlineKeyboardButton("🔍 TOP UP CARI DATA", callback_data="topup_cari")],
            [InlineKeyboardButton("🏠 MENU UTAMA", callback_data="back_main")]
        ])
        await update.message.reply_text(REKENING_TEXT, reply_markup=kb)
    else:
        await update.message.reply_text("Gunakan menu di bawah:", reply_markup=kb_main(uid))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = q.from_user.id

    if data == "back_main":
        await q.message.delete()
        await context.bot.send_message(chat_id=uid, text="🏠 MENU UTAMA", reply_markup=kb_main(uid))
        return

    if data.startswith("prov_"):
        _, prov_id, prov_nama = data.split("_", 2)
        try:
            r = requests.get(f"https://www.emsifa.com/api-wilayah-indonesia/api/regencies/{prov_id}.json", timeout=10)
            kota_list = r.json()
        except:
            kota_list = []
        buttons = []
        row = []
        for k in kota_list:
            row.append(InlineKeyboardButton(k["name"], callback_data=f"kota_{k['id']}_{k['name']}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ KEMBALI", callback_data="back_to_provinsi")])
        buttons.append([InlineKeyboardButton("🏠 MENU UTAMA", callback_data="back_main")])
        await q.message.edit_text(f"📍 {prov_nama}\nPilih Kota/Kabupaten:", reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data.startswith("kota_"):
        _, kota_id, kota_nama = data.split("_", 2)
        try:
            r = requests.get(f"https://www.emsifa.com/api-wilayah-indonesia/api/districts/{kota_id}.json", timeout=10)
            kec_list = r.json()
        except:
            kec_list = []
        buttons = []
        row = []
        for k in kec_list:
            row.append(InlineKeyboardButton(k["name"], callback_data=f"kec_{k['id']}_{k['name']}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ KEMBALI KE KOTA", callback_data=f"back_to_kota_{kota_id}_{kota_nama}")])
        buttons.append([InlineKeyboardButton("🏠 MENU UTAMA", callback_data="back_main")])
        await q.message.edit_text(f"🏙️ {kota_nama}\nPilih Kecamatan:", reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data.startswith("kec_"):
        _, kec_id, kec_nama = data.split("_", 2)
        if str(uid) not in db["user_info"]:
            db["user_info"][str(uid)] = {"kotas": []}
        kota_nama = q.message.text.split("\n")[0].replace("🏙️", "").strip()
        entry = f"{kota_nama} | {kec_nama}"
        if entry not in db["user_info"][str(uid)]["kotas"]:
            db["user_info"][str(uid)]["kotas"].append(entry)
            save_db()
        await q.message.edit_text(f"✅ {kec_nama} tersimpan!", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 MENU UTAMA", callback_data="back_main")]
        ]))

    elif data == "topup_tambah":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 MINGGU - 50K", callback_data="paket_tambah_1minggu")],
            [InlineKeyboardButton("1 BULAN - 150K", callback_data="paket_tambah_1bulan")],
            [InlineKeyboardButton("2 BULAN - 250K", callback_data="paket_tambah_2bulan")],
            [InlineKeyboardButton("🏠 MENU UTAMA", callback_data="back_main")]
        ])
        await q.message.edit_text(f"{REKENING_TEXT}\n\n💳 PILIH PAKET TAMBAH KOTA", reply_markup=kb)

# Register handlers jika application ada
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ========== ROUTES FLASK ==========
@flask_app.route("/")
def index():
    return "Bot Active - Production Ready", 200

@flask_app.route("/health")
def health():
    return {"status": "ok", "bot": bool(TOKEN), "db": "supabase" if supabase_enabled() else "local"}, 200

# Webhook Telegram - INI YANG BIKIN BOT JALAN DI GUNICORN
@flask_app.route(f"/webhook/{TOKEN}", methods=["POST"])
@flask_app.route("/webhook/telegram", methods=["POST"])
def telegram_webhook():
    if not application:
        return "Bot token not set", 500
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return "ok", 200
        
        # Jalankan update di event loop baru
        async def process_update():
            async with application:
                await application.initialize()
                update = Update.de_json(data, application.bot)
                await application.process_update(update)

        asyncio.run(process_update())
        return "ok", 200
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}")
        return "ok", 200

@flask_app.route("/set-webhook", methods=["GET"])
def set_webhook():
    """Panggil URL ini sekali setelah deploy untuk set webhook"""
    if not TOKEN:
        return "TOKEN not set", 500
    if not RAILWAY_URL:
        return "Set RAILWAY_URL env dulu (contoh: https://bot-baru.up.railway.app)", 500
    
    webhook_url = f"{RAILWAY_URL}/webhook/{TOKEN}"
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}", timeout=10)
        return r.json(), 200
    except Exception as e:
        return {"error": str(e)}, 500

@flask_app.route("/whatsapp-webhook", methods=["POST"])
def whatsapp_webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return "ok", 200
        sender_data = data.get("senderData", {})
        message_data = data.get("messageData", {})
        sender_number = sender_data.get("sender", "").split("@")[0]
        group_name = sender_data.get("chatName", "Grup WA")
        sender_name = sender_data.get("senderName", "Pengirim WA")
        text = message_data.get("textMessageData", {}).get("textMessage", "")
        if not text:
            return "ok", 200
        
        text_upper = text.upper()
        matched_users = []
        for uid_str, uinfo in db.get("user_info", {}).items():
            kotas = uinfo.get("kotas", [])
            for k in kotas:
                parts = [p.strip() for p in k.split("|")]
                if len(parts) >= 2:
                    kec = parts[1].upper()
                    if kec in text_upper:
                        matched_users.append(int(uid_str))
                        break
        
        for uid in matched_users:
            msg = f"🤖 INFO DARI WA\n\n🎰 GRUP: {group_name}\n👤 {sender_name}\n📞 {sender_number}\n\n💬 {text}"
            try:
                requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": uid, "text": msg}, timeout=10)
            except:
                pass
        return "ok", 200
    except Exception as e:
        logger.error(f"WA Webhook error: {e}")
        return "ok", 200

# ========== MAIN (Hanya untuk run lokal) ==========
if __name__ == "__main__":
    # Untuk lokal aja, di Railway pakai gunicorn
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)
