import os
import json
import asyncio
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from telegram.ext import ApplicationBuilder, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

app = Flask(__name__)

# --- KONFIGURASI ENVIRONMENT ---
ID_INSTANCE = os.getenv("ID_INSTANCE")
API_TOKEN_INSTANCE = os.getenv("API_TOKEN_INSTANCE")
BASE_URL = f"https://api.green-api.com/waInstance{ID_INSTANCE}"
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_NUMBERS = os.getenv("ADMIN_NUMBERS", "").split(",")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")

# --- DATABASE SUPABASE ---
engine = create_engine(SUPABASE_DB_URL, pool_size=5, max_overflow=10, pool_recycle=300)

def get_db():
    try: return engine.connect()
    except OperationalError as e:
        print(f"❌ DB Error: {e}")
        return None

def get_user(chat_id):
    conn = get_db()
    if not conn: return None
    result = conn.execute(text("SELECT * FROM users WHERE chat_id = :cid"), {"cid": chat_id}).fetchone()
    conn.close()
    
    if result:
        r = dict(result._mapping)
        # AUTO EXPIRED CHECK
        if r.get('expired_at') and r.get('paket_aktif') != 'UNLIMITED':
            exp_time = r['expired_at']
            if isinstance(exp_time, str): exp_time = datetime.fromisoformat(exp_time.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) > exp_time:
                update_user_field(chat_id, {'quota': 0, 'provinsi': None, 'kota': None, 'kecamatan_list': [], 'search_status': 'LOCKED'})
                r.update({'quota': 0, 'provinsi': None, 'kota': None, 'kecamatan_list': [], 'search_status': 'LOCKED'})
        return r
    return None

def update_user_field(chat_id, fields: dict):
    conn = get_db()
    if not conn: return
    set_clause = ", ".join([f"{k} = :{k}" for k in fields.keys()])
    query = f"""INSERT INTO users (chat_id, {', '.join(fields.keys())}) 
                VALUES (:chat_id, {', '.join([f':{k}' for k in fields.keys()])})
                ON CONFLICT (chat_id) DO UPDATE SET {set_clause}, updated_at = NOW()"""
    conn.execute(text(query), {"chat_id": chat_id, **fields})
    conn.commit(); conn.close()

def add_quota_and_set_expiry(chat_id, package_name, quota_add):
    conn = get_db()
    if not conn: return False
    now = datetime.now(timezone.utc)
    expiry_map = {
        "1_MINGGU": now + timedelta(days=7), "1_BULAN": now + timedelta(days=30),
        "2_BULAN": now + timedelta(days=60), "6_BULAN": now + timedelta(days=180),
        "UNLIMITED": None
    }
    new_expiry = expiry_map.get(package_name)
    query = """INSERT INTO users (chat_id, quota, paket_aktif, expired_at) 
               VALUES (:cid, :q, :pkg, :exp)
               ON CONFLICT (chat_id) DO UPDATE SET 
                   quota = users.quota + :q, paket_aktif = :pkg,
                   expired_at = CASE WHEN :pkg = 'UNLIMITED' THEN NULL 
                             ELSE COALESCE(users.expired_at, :exp) END,
                   updated_at = NOW()"""
    conn.execute(text(query), {"cid": chat_id, "q": quota_add, "pkg": package_name, "exp": new_expiry})
    conn.commit(); conn.close()
    return True

def search_keyword(query):
    conn = get_db()
    if not conn: return None
    result = conn.execute(text(
        "SELECT response_text FROM keywords WHERE LOWER(keyword) LIKE :kw AND is_active = TRUE LIMIT 1"
    ), {"kw": f"%{query.lower()}%"}).fetchone()
    conn.close()
    return result[0] if result else None

def get_all_group_history():
    """Mengambil SEMUA history pesan grup dari Supabase"""
    conn = get_db()
    if not conn: return []
    results = conn.execute(text(
        "SELECT kota, pesan, pengirim, timestamp FROM group_history ORDER BY timestamp DESC LIMIT 50"
    )).fetchall()
    conn.close()
    
    formatted_list = []
    for r in results:
        row = dict(r._mapping)
        ts = row['timestamp'].strftime('%d/%m %H:%M') if isinstance(row['timestamp'], datetime) else str(row['timestamp'])[:16]
        formatted_list.append(f"📍 {row['kota']}\n💬 \"{row['pesan']}\"\n {row['pengirim']} | 🕐 {ts}")
        
    return "\n\n".join(formatted_list) if formatted_list else None

# --- HELPER FUNCTIONS ---
def send_wa(chat_id, text):
    try: requests.post(f"{BASE_URL}/sendMessage/{API_TOKEN_INSTANCE}", json={"chatId": f"{chat_id}@c.us", "message": text}, timeout=10)
    except Exception as e: print(f"WA Error: {e}")

async def send_tg(text, reply_markup=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return False
    try:
        app_tg = ApplicationBuilder().token(TG_BOT_TOKEN).build()
        await app_tg.bot.send_message(chat_id=TG_CHAT_ID, text=text, parse_mode="MarkdownV2", reply_markup=reply_markup)
        return True
    except Exception as e: print(f"TG Error: {e}"); return False

def fetch_api(endpoint):
    try:
        r = requests.get(f"https://kodepos.vercel.app/{endpoint}", timeout=10)
        return r.json() if r.status_code == 200 else []
    except: return []

def is_admin_number(chat_id): return chat_id in ADMIN_NUMBERS

# --- STATE MANAGEMENT ---
user_states = {} 

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data or 'body' not in data: return jsonify({"status": "ignored"}), 200
    
    body = data['body']; chat_id = body.get('chatId', '').split('@')[0]
    msg = body.get('textMessage', '').strip().lower()
    user = get_user(chat_id)
    
    if chat_id not in user_states: user_states[chat_id] = {"step": "menu", "temp_data": {}}
    state = user_states[chat_id]
    
    # === MENU UTAMA ===
    if state["step"] == "menu":
        if msg in ["/admin", "panel", "adminpanel"]:
            if is_admin_number(chat_id):
                state["step"] = "admin_panel"; send_wa(chat_id, _get_admin_panel_menu())
            else:
                state["step"] = "admin_auth"; send_wa(chat_id, "🔐 *VERIFIKASI*\nMasukkan kode akses:")
                
        elif msg == "3": # Tambah Kota
            send_wa(chat_id, _start_package_selection(chat_id, is_admin_number(chat_id), user))
            
        elif msg == "6": # CARI KOTA LAIN - TAMPILKAN SEMUA HISTORY
            search_status = user.get('search_status', 'LOCKED') if user else 'LOCKED'
            
            if search_status == 'APPROVED':
                all_history = get_all_group_history()
                if all_history:
                    send_wa(chat_id, f"📚 *SELURUH HISTORY GRUP*\n\n{all_history}\n\n_Total 50 pesan terbaru_")
                else:
                    send_wa(chat_id, "❌ Belum ada history pesan grup yang tersimpan.")
            elif search_status == 'PENDING':
                send_wa(chat_id, "⏳ *MENUNGGU APPROVAL*\nPembayaran Anda sedang diverifikasi admin.\nMohon tunggu konfirmasi.")
            else: # LOCKED
                send_wa(chat_id, 
                    " *CARI KOTA LAIN - TERKUNCI*\n\n"
                    "Fitur ini memerlukan pembelian paket khusus.\n\n"
                    "💎 *PAKET CARI KOTA:*\n"
                    "1️⃣ 1 Minggu - Rp 15.000\n"
                    "2️⃣ 1 Bulan - Rp 50.000\n"
                    "3️⃣ 2 Bulan - Rp 80.000\n\n"
                    "_Ketik angka paket untuk lanjut ke pembayaran:_")
                state["step"] = "pilih_paket_cari"

        elif msg == "9": # KEYWORD LAIN
            send_wa(chat_id, "🔑 *KEYWORD LAIN*\n\nKetik kata kunci untuk info khusus.\nContoh: JHT, BPJS, ERROR, OTP\n\n_Silakan ketik keyword Anda:_")
            state["step"] = "input_keyword"
            
        elif msg == "sudah bayar" and state["step"] == "konfirmasi_paket":
            pkg = state["temp_data"].get("selected_package")
            if pkg:
                pkg_key_map = {"1 Minggu": "1_MINGGU", "1 Bulan": "1_BULAN", "2 Bulan": "2_BULAN", "6 Bulan": "6_BULAN", "Unlimited": "UNLIMITED"}
                if add_quota_and_set_expiry(chat_id, pkg_key_map[pkg['name']], pkg['quota']):
                    send_wa(chat_id, f"✅ *BERHASIL!*\nPaket: {pkg['name']}\nQuota: +{pkg['quota']}x\nSilakan pilih Provinsi:")
                    state["step"] = "pilih_provinsi"
                else: send_wa(chat_id, "❌ Gagal proses pembayaran.")
            else: send_wa(chat_id, "Tidak ada paket tertunda.")
            
        elif msg.isdigit() and state["step"] == "menu" and not is_admin_number(chat_id):
            packages = {"1": {"name": "1 Minggu", "price": 40000, "quota": 3}, "2": {"name": "1 Bulan", "price": 120000, "quota": 3}, "3": {"name": "2 Bulan", "price": 220000, "quota": 3}, "4": {"name": "6 Bulan", "price": 600000, "quota": 3}, "5": {"name": "Unlimited", "price": 2000000, "quota": 999}}
            if msg in packages:
                state["step"] = "konfirmasi_paket"; state["temp_data"]["selected_package"] = packages[msg]
                send_wa(chat_id, f" *PAKET: {packages[msg]['name']}*\nHarga: Rp {packages[msg]['price']:,}\nTransfer ke:\n🏦 SEABANK: 901040978290 (HAMBALI)\n💰 DANA: 083824101264 (HAMBALI)\nKetik: *SUDAH BAYAR*")
            else: send_wa(chat_id, "Pilihan tidak valid.")
            
        elif msg.isdigit() and state["step"] == "pilih_paket_cari":
            packages_cari = {
                "1": {"name": "1 Minggu", "price": 15000, "key": "1_MINGGU_CARI"},
                "2": {"name": "1 Bulan", "price": 50000, "key": "1_BULAN_CARI"},
                "3": {"name": "2 Bulan", "price": 80000, "key": "2_BULAN_CARI"}
            }
            if msg in packages_cari:
                pkg = packages_cari[msg]
                state["step"] = "upload_bukti_cari"
                state["temp_data"]["selected_cari_pkg"] = pkg
                send_wa(chat_id, 
                    f" *PAKET: {pkg['name']}*\n"
                    f"Harga: Rp {pkg['price']:,}\n\n"
                    f"Silakan transfer dan kirim *BUKTI TRANSFER* (foto/screenshot) ke chat ini.\n"
                    f"🏦 SEABANK: 901040978290 (HAMBALI)\n"
                    f"💰 DANA: 083824101264 (HAMBALI)")
            else:
                send_wa(chat_id, " Pilihan paket tidak valid.")
                
        elif state["step"] == "upload_bukti_cari" and body.get('typeMessage') in ['imageMessage', 'documentMessage']:
            pkg = state["temp_data"].get("selected_cari_pkg")
            if pkg:
                update_user_field(chat_id, {'search_status': 'PENDING', 'search_package': pkg['key']})
                proof_url = body.get('downloadUrl', 'Bukti terlampir')
                approve_btn = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Setuju", callback_data=f"approve_cari_{chat_id}"),
                    InlineKeyboardButton("❌ Tolak", callback_data=f"reject_cari_{chat_id}")
                ]])
                tg_msg = (
                    f"📥 *PERMINTAAN CARI KOTA BARU*\n"
                    f"📱 Dari: \\+{chat_id}\n"
                    f"📦 Paket: {pkg['name']} (Rp {pkg['price']:,})\n"
                    f" Bukti: [Lihat Bukti]({proof_url})\n\n"
                    f"_Klik tombol untuk approve/tolak_"
                )
                asyncio.run(send_tg(tg_msg, approve_btn))
                send_wa(chat_id, "✅ Bukti terkirim! Menunggu approval admin...")
                state["step"] = "menu"
            else:
                send_wa(chat_id, "❌ Sesi pembayaran habis. Ketik 6 untuk ulang.")

        elif msg == "menu": send_wa(chat_id, _get_main_menu_text())
        else:
            asyncio.run(send_tg(f"📨 *Pesan Baru*\n Dari: \\+{chat_id}\n Isi: {body.get('textMessage', '')}"))
            send_wa(chat_id, "✅ Pesan diteruskan ke admin.")

    # === AUTENTIKASI ADMIN ===
    elif state["step"] == "admin_auth":
        if msg == ADMIN_PASSWORD:
            state["step"] = "admin_panel"; send_wa(chat_id, "✅ AKSES DITERIMA"); send_wa(chat_id, _get_admin_panel_menu())
        else: send_wa(chat_id, " Command tidak dikenali."); state["step"] = "menu"

    # === INPUT KEYWORD ===
    elif state["step"] == "input_keyword":
        if msg == "menu":
            state["step"] = "menu"; send_wa(chat_id, _get_main_menu_text())
        else:
            response = search_keyword(msg)
            if response:
                send_wa(chat_id, response)
                asyncio.run(send_tg(f"🔍 *Keyword Search*\n Dari: \\+{chat_id}\n Query: {msg}\n Status: DITEMUKAN ✅"))
            else:
                send_wa(chat_id, f"❌ *Keyword '{msg}' tidak ditemukan.*\nCoba keyword lain atau ketik 'menu'.")
                asyncio.run(send_tg(f" *Keyword Search*\n Dari: \\+{chat_id}\n Query: {msg}\n Status: TIDAK DITEMUKAN ❌"))
            state["step"] = "menu"

    # === FLOW PILIH KOTA (MENU 3) ===
    elif state["step"] == "pilih_provinsi":
        if not is_admin_number(chat_id) and (not user or user['quota'] <= 0):
            send_wa(chat_id, "️ QUOTA HABIS! Beli paket lagi (ketik 3)."); state["step"] = "menu"; return jsonify({"status": "ok"}), 200
        provs = fetch_api("provinsi"); idx = int(msg) - 1 if msg.isdigit() else -1
        if 0 <= idx < len(provs):
            chosen = provs[idx]; state["temp_data"].update({"provinsi": chosen["nama"], "id_provinsi": chosen["id"]}); state["step"] = "pilih_kota"
            kotas = fetch_api(f"kota/{chosen['id']}"); list_text = "\n".join([f"{i+1}. {k['nama']}" for i, k in enumerate(kotas)]) if kotas else "❌ Gagal muat."
            send_wa(chat_id, f"🏢 *PILIH KOTA*\nProv: {chosen['nama']}\n{list_text}\n_Ketik angka kota._")
        else: send_wa(chat_id, "❌ Angka tidak valid.")

    elif state["step"] == "pilih_kota":
        kotas = fetch_api(f"kota/{state['temp_data'].get('id_provinsi')}"); idx = int(msg) - 1 if msg.isdigit() else -1
        if 0 <= idx < len(kotas):
            chosen = kotas[idx]; state["temp_data"].update({"kota": chosen["nama"], "id_kota": chosen["id"], "selected_kec": []}); state["step"] = "pilih_kecamatan"
            kecs = fetch_api(f"kecamatan/{chosen['id']}"); list_text = "\n".join([f"{i+1}. {k['nama']}" for i, k in enumerate(kecs)]) if kecs else "❌ Gagal muat."
            send_wa(chat_id, f"📍 *PILIH KECAMATAN*\nKota: {chosen['nama']}\n{list_text}\n_Ketik angka (bisa >1, pisahkan koma)._")
        else: send_wa(chat_id, "❌ Angka tidak valid.")

    elif state["step"] == "pilih_kecamatan":
        kecs = fetch_api(f"kecamatan/{state['temp_data'].get('id_kota')}")
        indices = [int(x.strip())-1 for x in msg.split(",") if x.strip().isdigit()]
        valid = [kecs[i]["nama"] for i in indices if 0 <= i < len(kecs)]
        if valid:
            update_user_field(chat_id, {"provinsi": state["temp_data"]["provinsi"], "kota": state["temp_data"]["kota"], "kecamatan_list": valid})
            if not is_admin_number(chat_id) and user:
                new_quota = max(0, user['quota'] - 1); update_user_field(chat_id, {"quota": new_quota}); quota_msg = f"\nQuota tersisa: {new_quota}"
            else: quota_msg = "\n(Admin Unlimited)"
            send_wa(chat_id, f"✅ *LOKASI TERSIMPAN!*\n\nProv: {state['temp_data']['provinsi']}\nKota: {state['temp_data']['kota']}\nKec: {', '.join(valid)}{quota_msg}")
            state["step"] = "menu"
        else: send_wa(chat_id, "❌ Tidak ada kecamatan valid.")

    return jsonify({"status": "received"}), 200

# ============================================================
# WEBHOOK TELEGRAM (HANDLE TOMBOL APPROVE/REJECT)
# ============================================================
@app.route('/tg-webhook', methods=['POST'])
def tg_webhook():
    data = request.get_json()
    
    # Handle Callback Query (Tombol Inline)
    callback = data.get('callback_query', {})
    if callback:
        cb_data = callback.get('data', '')
        if cb_data.startswith("approve_cari_"):
            target_chat = cb_data.replace("approve_cari_", "")
            update_user_field(target_chat, {'search_status': 'APPROVED'})
            send_wa(target_chat, "✅ *PEMBAYARAN DISETUJUI!*\nAnda sekarang bisa menggunakan fitur Cari Kota Lain.\nKetik 6 untuk melihat history.")
            asyncio.run(send_tg(f"✅ Approved: \\+{target_chat}", reply_markup=None))
        elif cb_data.startswith("reject_cari_"):
            target_chat = cb_data.replace("reject_cari_", "")
            update_user_field(target_chat, {'search_status': 'LOCKED'})
            send_wa(target_chat, "❌ *PEMBAYARAN DITOLAK*\nBukti transfer tidak valid. Silakan hubungi admin.")
            asyncio.run(send_tg(f"❌ Rejected: \\+{target_chat}", reply_markup=None))
        return jsonify({"ok": True}), 200

    # Handle Reply Message (Balasan Dua Arah biasa)
    message = data.get('message', {})
    reply_to = message.get('reply_to_message', {})
    if reply_to and reply_to.get('from', {}).get('is_bot'):
        original_text = reply_to.get('text', '')
        wa_number = ""
        for line in original_text.split('\n'):
            if 'Dari:' in line:
                clean = line.replace(' ', '').replace('\\+', '+').strip()
                if clean.startswith('Dari: +'): wa_number = clean.replace('Dari: +', '').strip(); break
        if wa_number:
            send_wa(wa_number, f"🤖 *Balasan Admin:*\n\n{message.get('text', '')}")
            
    return jsonify({"ok": True}), 200

# ============================================================
# FUNGSI BANTU MENU
# ============================================================
def _get_main_menu_text():
    return (" *BOT SAHABAT JHT v3.0*\n\n1️⃣ Profil\n2️⃣ Cek Status\n3️⃣ Tambah Kota\n4️⃣ Kota Saya\n5️ Top Up\n6️⃣ Cari Kota Lain\n7️ Solusi JMO\n8️ No Blacklist\n9️⃣ Keyword Lain\n Auto Format\n1️⃣1️ Bantuan\n1️⃣2️ Hubungi Admin\n\n_Ketik angka menu._")

def _start_package_selection(chat_id, is_admin, user):
    if is_admin: return "👑 *MODE ADMIN (UNLIMITED)*\nLangsung pilih provinsi tanpa batas."
    quota = user['quota'] if user else 0
    return (f"💎 *TAMBAH KOTA - PILIH PAKET*\n\nQuota Anda: *{quota}*\n\n 1. 1 Minggu - Rp 40.000 (3x)\n 2. 1 Bulan - Rp 120.000 (3x)\n 3. 2 Bulan - Rp 220.000 (3x)\n 4. 6 Bulan - Rp 600.000 (3x)\n️ 5. Unlimited - Rp 2.000.000\n\n_Ketik angka paket (1-5)._")

def _get_admin_panel_menu():
    return ("🔐 *PANEL ADMIN*\nPilih menu:\n\n✅ AKTIF | ⏳ PENDING\n CEK USER AKTIF | 📊 STATS USER\n➕ TAMBAH SALDO |  KURANGI SALDO\n🚫 TAMBAH BLACKLIS | 🗑️ HAPUS BLACKLIST\n👑 TAMBAH ADMIN | ❌ HAPUS ADMIN\n🗑️ HAPUS USER | 📢 BROADCAST\n LIST BLACKLIST\n\n⬅️ MENU UTAMA")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
