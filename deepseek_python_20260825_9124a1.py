# -*- coding: utf-8 -*-
import os, json, requests, re, logging
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Setup logging
logging.basicConfig(level=logging.INFO)
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
RAILWAY_URL = os.getenv("RAILWAY_URL", "https://infinix-bali.up.railway.app")

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
        return remote
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {"user_info": {}, "langganan": {}, "langganan_cari": {}, "blacklist": [], "pending_hapus_kota": []}

def save_db():
    tmp = json.loads(json.dumps(db, default=str))
    save_db_to_supabase(tmp)
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2, ensure_ascii=False)
    except:
        pass

db = load_db()

flask_app = Flask(__name__)

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
    sub_cari = db.get("langganan_cari", {}).get(str(uid))
    
    txt = "📊 STATUS LANGGANAN\n\n"
    if subs:
        txt += "🎁 Paket TAMBAH KOTA:\n"
        for s in subs:
            txt += f"  🏙️ {s.get('kota', '-')} : Exp {s.get('expire', '-')}\n"
    else:
        txt += "❌ Paket TAMBAH KOTA: Belum ada\n"
    txt += f"\n🏙️ Total Wilayah: {jml} kota\n"
    if sub_cari:
        txt += f"\n🔎 Paket CARI DATA: Aktif"
    else:
        txt += f"\n🔎 Paket CARI DATA: Belum ada"
    return txt

# ========== APPLICATION TELEGRAM ==========
application = Application.builder().token(TOKEN).build()

async def start(update, context):
    uid = update.effective_user.id
    if str(uid) not in db["user_info"]:
        db["user_info"][str(uid)] = {"nama": update.effective_user.full_name, "username": update.effective_user.username or "-", "kotas": [], "custom_keywords": []}
        save_db()
    await update.message.reply_text("👋 SELAMAT DATANG!\nPilih menu di bawah:", reply_markup=kb_main(uid))

async def text_handler(update, context):
    uid = update.effective_user.id
    text = update.message.text.strip()
    
    if "PROFIL" in text:
        user_data = db["user_info"].get(str(uid), {})
        await update.message.reply_text(f"👤 PROFIL\nID: {uid}\nNama: {user_data.get('nama', '-')}", reply_markup=kb_main(uid))
    
    elif "TAMBAH KOTA" in text:
        buttons = []
        row = []
        for p in get_provinces():
            row.append(InlineKeyboardButton(p["nama"], callback_data=f"prov_{p['id']}_{p['nama']}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")])
        await update.message.reply_text("📍 PILIH PROVINSI:", reply_markup=InlineKeyboardMarkup(buttons))
    
    elif "WILAYAH DIPILIH" in text:
        kotas = db["user_info"].get(str(uid), {}).get("kotas", [])
        if not kotas:
            await update.message.reply_text("❌ Belum ada wilayah", reply_markup=kb_main(uid))
        else:
            txt = "🌠 WILAYAH DIPILIH\n\n"
            for i, k in enumerate(kotas, 1):
                txt += f"{i}. {k}\n"
            await update.message.reply_text(txt, reply_markup=kb_main(uid))
    
    elif "HAPUS KOTA" in text:
        kotas = db["user_info"].get(str(uid), {}).get("kotas", [])
        if not kotas:
            await update.message.reply_text("❌ Belum ada wilayah", reply_markup=kb_main(uid))
            return
        buttons = []
        for i, k in enumerate(kotas):
            buttons.append([InlineKeyboardButton(f"🗑️ {k[:40]}", callback_data=f"hapuskota_{i}")])
        buttons.append([InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")])
        await update.message.reply_text("🗑️ Pilih kota yang mau dihapus:", reply_markup=InlineKeyboardMarkup(buttons))
    
    elif "STATUS" in text:
        await update.message.reply_text(get_status_text(uid), reply_markup=kb_main(uid))
    
    elif "TOP UP" in text:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 TAMBAH KOTA", callback_data="topup_tambah")],
            [InlineKeyboardButton("🔍 CARI DATA", callback_data="topup_cari")],
            [InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
        await update.message.reply_text(f"{REKENING_TEXT}\nPilih jenis top up:", reply_markup=kb)
    
    elif "CARI DATA" in text:
        await update.message.reply_text("🔎 CARI DATA\nFitur ini untuk mencari history WA berdasarkan kota.", reply_markup=kb_main(uid))
    
    elif "KEYWORD" in text:
        await update.message.reply_text("🚀 PILIH KEYWORD\nKirim keyword yang ingin dipantau.", reply_markup=kb_main(uid))
    
    elif "BLACKLIST" in text:
        bl = db.get("blacklist", [])
        txt = f"🚫 BLACKLIST\nTotal: {len(bl)} nomor\n\n"
        for n in bl[:20]:
            txt += f"• {n}\n"
        await update.message.reply_text(txt, reply_markup=kb_main(uid))
    
    elif "BANTUAN" in text:
        await update.message.reply_text("❓ BANTUAN\n\n1. TOP UP untuk aktifkan fitur\n2. TAMBAH KOTA untuk filter wilayah\n3. Admin: @Hambali1995", reply_markup=kb_main(uid))
    
    elif "HUBUNGI ADMIN" in text:
        await update.message.reply_text("🧑‍💻 HUBUNGI ADMIN\nTelegram: @Hambali1995", reply_markup=kb_main(uid))
    
    elif "PANEL ADMIN" in text and is_admin(uid):
        await update.message.reply_text("🧭 PANEL ADMIN", reply_markup=kb_main(uid))

async def callback_handler(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    
    if data == "back_main":
        await q.message.delete()
        await context.bot.send_message(uid, "🏠 MENU UTAMA", reply_markup=kb_main(uid))
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
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")])
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
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")])
        await q.message.edit_text(f"🏙️ {kota_nama}\nPilih Kecamatan:", reply_markup=InlineKeyboardMarkup(buttons))
    
    elif data.startswith("kec_"):
        _, kec_id, kec_nama = data.split("_", 2)
        if "kotas" not in db["user_info"].get(str(uid), {}):
            db["user_info"][str(uid)] = {"kotas": []}
        kota_nama = q.message.text.split("\n")[0].replace("🏙️", "").strip()
        entry = f"{kota_nama} | {kec_nama}"
        db["user_info"][str(uid)]["kotas"].append(entry)
        save_db()
        await q.message.edit_text(
            f"✅ {kec_nama} tersimpan!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )
    
    elif data.startswith("hapuskota_"):
        idx = int(data.split("_")[1])
        kotas = db["user_info"].get(str(uid), {}).get("kotas", [])
        if 0 <= idx < len(kotas):
            hapus = kotas.pop(idx)
            save_db()
            await q.message.edit_text(f"✅ Dihapus: {hapus}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")]]))
    
    elif data == "topup_tambah":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 MINGGU - 50K", callback_data="paket_tambah_1minggu")],
            [InlineKeyboardButton("1 BULAN - 150K", callback_data="paket_tambah_1bulan")],
            [InlineKeyboardButton("2 BULAN - 250K", callback_data="paket_tambah_2bulan")],
            [InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
        await q.message.edit_text(f"{REKENING_TEXT}\n\n💳 PILIH PAKET TAMBAH KOTA", reply_markup=kb)
    
    elif data == "topup_cari":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 MINGGU - 15K", callback_data="paket_cari_1minggu")],
            [InlineKeyboardButton("1 BULAN - 50K", callback_data="paket_cari_1bulan")],
            [InlineKeyboardButton("2 BULAN - 100K", callback_data="paket_cari_2bulan")],
            [InlineKeyboardButton("🏠 KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
        await q.message.edit_text(f"{REKENING_TEXT}\n\n🔍 PILIH PAKET CARI DATA", reply_markup=kb)

# ========== WEBHOOK WHATSAPP ==========
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
                    kota = parts[0].upper()
                    kec = parts[1].upper()
                    if kec in text_upper:
                        matched_users.append(int(uid_str))
                        break
        
        for uid in matched_users:
            msg = f"🤖 INFO DARI WA\n\n🎰 GRUP: {group_name}\n👤 {sender_name}\n📞 {sender_number}\n\n💬 {text}"
            try:
                requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": uid, "text": msg})
            except:
                pass
        
        return "ok", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "ok", 200

# ========== TELEGRAM WEBHOOK ==========
@flask_app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            return "ok", 200
        update = Update.de_json(data, application.bot)
        application.process_update(update)
        return "ok", 200
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}")
        return "ok", 200

# ========== MAIN ==========
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(callback_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

@flask_app.route("/")
def index():
    return "Bot Active", 200

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)