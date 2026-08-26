import os
import asyncio
import requests
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify
from telegram.ext import ApplicationBuilder, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

app = Flask(__name__)

# ============================================================
# KONFIGURASI ENVIRONMENT
# ============================================================
ID_INSTANCE = os.getenv("ID_INSTANCE")
API_TOKEN_INSTANCE = os.getenv("API_TOKEN_INSTANCE")
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_NUMBERS = [
    x.strip()
    for x in os.getenv("ADMIN_NUMBERS", "").split(",")
    if x.strip()
]
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")

if not ID_INSTANCE or not API_TOKEN_INSTANCE:
    print("⚠️ ID_INSTANCE / API_TOKEN_INSTANCE belum diset.")

if not ADMIN_PASSWORD:
    print("⚠️ ADMIN_PASSWORD belum diset.")

if not SUPABASE_DB_URL:
    raise RuntimeError("SUPABASE_DB_URL belum diset.")

BASE_URL = f"https://api.green-api.com/waInstance{ID_INSTANCE}"

engine = create_engine(
    SUPABASE_DB_URL,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,
    pool_pre_ping=True,
)

# ============================================================
# STATE
# ============================================================
user_states = {}


def default_state():
    return {"step": "menu", "temp_data": {}}


# ============================================================
# DATABASE
# ============================================================
def get_db():
    try:
        return engine.connect()
    except OperationalError as e:
        print(f"❌ DB Error: {e}")
        return None


def get_user(chat_id):
    conn = get_db()
    if not conn:
        return None

    try:
        result = conn.execute(
            text("SELECT * FROM users WHERE chat_id = :cid"),
            {"cid": chat_id},
        ).fetchone()

        if not result:
            return None

        r = dict(result._mapping)

        if r.get("expired_at") and r.get("paket_aktif") != "UNLIMITED":
            exp_time = r["expired_at"]

            if isinstance(exp_time, str):
                exp_time = datetime.fromisoformat(
                    exp_time.replace("Z", "+00:00")
                )

            if exp_time.tzinfo is None:
                exp_time = exp_time.replace(tzinfo=timezone.utc)

            if datetime.now(timezone.utc) > exp_time:
                update_user_field(
                    chat_id,
                    {
                        "quota": 0,
                        "provinsi": None,
                        "kota": None,
                        "kecamatan_list": [],
                        "search_status": "LOCKED",
                    },
                )

                r.update(
                    {
                        "quota": 0,
                        "provinsi": None,
                        "kota": None,
                        "kecamatan_list": [],
                        "search_status": "LOCKED",
                    }
                )

        return r

    finally:
        conn.close()


def update_user_field(chat_id, fields: dict):
    if not fields:
        return False

    conn = get_db()
    if not conn:
        return False

    try:
        set_clause = ", ".join(
            [f"{k} = :{k}" for k in fields.keys()]
        )

        columns = ", ".join(fields.keys())
        values = ", ".join([f":{k}" for k in fields.keys()])

        query = f"""
            INSERT INTO users (chat_id, {columns})
            VALUES (:chat_id, {values})
            ON CONFLICT (chat_id)
            DO UPDATE SET {set_clause}, updated_at = NOW()
        """

        conn.execute(
            text(query),
            {"chat_id": chat_id, **fields},
        )
        conn.commit()
        return True

    except Exception as e:
        conn.rollback()
        print(f"❌ update_user_field: {e}")
        return False

    finally:
        conn.close()


def add_quota_and_set_expiry(chat_id, package_name, quota_add):
    conn = get_db()
    if not conn:
        return False

    now = datetime.now(timezone.utc)

    expiry_map = {
        "1_MINGGU": now + timedelta(days=7),
        "1_BULAN": now + timedelta(days=30),
        "2_BULAN": now + timedelta(days=60),
        "6_BULAN": now + timedelta(days=180),
        "UNLIMITED": None,
    }

    new_expiry = expiry_map.get(package_name)

    query = """
        INSERT INTO users
            (chat_id, quota, paket_aktif, expired_at)
        VALUES
            (:cid, :q, :pkg, :exp)
        ON CONFLICT (chat_id)
        DO UPDATE SET
            quota = COALESCE(users.quota, 0) + :q,
            paket_aktif = :pkg,
            expired_at = CASE
                WHEN :pkg = 'UNLIMITED' THEN NULL
                ELSE :exp
            END,
            updated_at = NOW()
    """

    try:
        conn.execute(
            text(query),
            {
                "cid": chat_id,
                "q": quota_add,
                "pkg": package_name,
                "exp": new_expiry,
            },
        )
        conn.commit()
        return True

    except Exception as e:
        conn.rollback()
        print(f"❌ add_quota_and_set_expiry: {e}")
        return False

    finally:
        conn.close()


def decrement_quota(chat_id):
    conn = get_db()
    if not conn:
        return False

    try:
        result = conn.execute(
            text("""
                UPDATE users
                SET quota = GREATEST(COALESCE(quota, 0) - 1, 0),
                    updated_at = NOW()
                WHERE chat_id = :chat_id
                  AND COALESCE(quota, 0) > 0
                RETURNING quota
            """),
            {"chat_id": chat_id},
        ).fetchone()

        conn.commit()
        return bool(result)

    except Exception as e:
        conn.rollback()
        print(f"❌ decrement_quota: {e}")
        return False

    finally:
        conn.close()


def search_keyword(query):
    conn = get_db()
    if not conn:
        return None

    try:
        result = conn.execute(
            text("""
                SELECT response_text
                FROM keywords
                WHERE LOWER(keyword) LIKE :kw
                  AND is_active = TRUE
                LIMIT 1
            """),
            {"kw": f"%{query.lower()}%"},
        ).fetchone()

        return result[0] if result else None

    finally:
        conn.close()


def get_all_group_history():
    conn = get_db()
    if not conn:
        return None

    try:
        results = conn.execute(
            text("""
                SELECT kota, pesan, pengirim, timestamp
                FROM group_history
                ORDER BY timestamp DESC
                LIMIT 50
            """)
        ).fetchall()

        formatted = []

        for r in results:
            row = dict(r._mapping)
            ts = row["timestamp"]

            if isinstance(ts, datetime):
                ts = ts.strftime("%d/%m %H:%M")
            else:
                ts = str(ts)[:16]

            formatted.append(
                f"📍 {row['kota']}\n"
                f"💬 \"{row['pesan']}\"\n"
                f"👤 {row['pengirim']} | 🕐 {ts}"
            )

        return "\n\n".join(formatted) if formatted else None

    finally:
        conn.close()


# ============================================================
# WHATSAPP / TELEGRAM
# ============================================================
def send_wa(chat_id, message):
    try:
        r = requests.post(
            f"{BASE_URL}/sendMessage/{API_TOKEN_INSTANCE}",
            json={
                "chatId": f"{chat_id}@c.us",
                "message": message,
            },
            timeout=10,
        )

        if not r.ok:
            print(f"❌ WA HTTP {r.status_code}: {r.text}")

        return r.ok

    except Exception as e:
        print(f"❌ WA Error: {e}")
        return False


async def send_tg(message, reply_markup=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram belum dikonfigurasi.")
        return False

    try:
        app_tg = ApplicationBuilder().token(TG_BOT_TOKEN).build()

        await app_tg.bot.send_message(
            chat_id=TG_CHAT_ID,
            text=message,
            parse_mode=None,
            reply_markup=reply_markup,
        )

        return True

    except Exception as e:
        print(f"❌ TG Error: {e}")
        return False


def notify_tg(message, reply_markup=None):
    try:
        return asyncio.run(send_tg(message, reply_markup))
    except Exception as e:
        print(f"❌ Telegram notify error: {e}")
        return False


def fetch_api(endpoint):
    try:
        r = requests.get(
            f"https://kodepos.vercel.app/{endpoint}",
            timeout=10,
        )
        return r.json() if r.status_code == 200 else []

    except Exception as e:
        print(f"❌ API kodepos: {e}")
        return []


def is_admin_number(chat_id):
    return str(chat_id) in ADMIN_NUMBERS


# ============================================================
# MENU
# ============================================================
def _get_main_menu_text():
    return (
        "🤖 BOT SAHABAT JHT v3.0\n\n"
        "1️⃣ Profil\n"
        "2️⃣ Cek Status\n"
        "3️⃣ Tambah Kota\n"
        "4️⃣ Kota Saya\n"
        "5️⃣ Top Up\n"
        "6️⃣ Cari Kota Lain\n"
        "7️⃣ Solusi JMO\n"
        "8️⃣ No Blacklist\n"
        "9️⃣ Keyword Lain\n"
        "🔟 Auto Format\n"
        "1️⃣1️⃣ Bantuan\n"
        "1️⃣2️⃣ Hubungi Admin\n\n"
        "Ketik angka menu."
    )


def _start_package_selection(chat_id, is_admin, user):
    if is_admin:
        return (
            "👑 MODE ADMIN (UNLIMITED)\n\n"
            "Langsung pilih provinsi."
        )

    quota = user["quota"] if user else 0

    return (
        "💎 TAMBAH KOTA - PILIH PAKET\n\n"
        f"Quota Anda: {quota}\n\n"
        "1. 1 Minggu - Rp 40.000 (3x)\n"
        "2. 1 Bulan - Rp 120.000 (3x)\n"
        "3. 2 Bulan - Rp 220.000 (3x)\n"
        "4. 6 Bulan - Rp 600.000 (3x)\n"
        "5. Unlimited - Rp 2.000.000\n\n"
        "Ketik angka paket (1-5)."
    )


def _get_admin_panel_menu():
    return (
        "🔐 PANEL ADMIN\n\n"
        "Menu admin masih menggunakan struktur lama.\n\n"
        "Ketik MENU untuk kembali."
    )


# ============================================================
# WEBHOOK WHATSAPP
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(silent=True)

        if not data or "body" not in data:
            return jsonify({"status": "ignored"}), 200

        body = data["body"]

        chat_id = str(body.get("chatId", "")).split("@")[0]

        if not chat_id:
            return jsonify({"status": "ignored"}), 200

        raw_text = body.get("textMessage", "")
        msg = str(raw_text).strip().lower()

        user = get_user(chat_id)

        if chat_id not in user_states:
            user_states[chat_id] = default_state()

        state = user_states[chat_id]
        step = state["step"]

        # ====================================================
        # MENU UTAMA
        # ====================================================
        if step == "menu":

            if msg == "menu":
                send_wa(chat_id, _get_main_menu_text())

            elif msg in ["/admin", "panel", "adminpanel"]:
                if is_admin_number(chat_id):
                    state["step"] = "admin_panel"
                    send_wa(chat_id, _get_admin_panel_menu())
                else:
                    state["step"] = "admin_auth"
                    send_wa(
                        chat_id,
                        "🔐 VERIFIKASI ADMIN\n\n"
                        "Masukkan kode akses:"
                    )

            elif msg == "3":
                if is_admin_number(chat_id):
                    state["step"] = "pilih_provinsi"
                    provs = fetch_api("provinsi")

                    if not provs:
                        send_wa(chat_id, "❌ Gagal mengambil daftar provinsi.")
                    else:
                        text_list = "\n".join(
                            f"{i+1}. {p['nama']}"
                            for i, p in enumerate(provs)
                        )
                        send_wa(
                            chat_id,
                            f"🏢 PILIH PROVINSI\n\n{text_list}\n\n"
                            "Ketik angka provinsi."
                        )
                else:
                    send_wa(
                        chat_id,
                        _start_package_selection(chat_id, False, user)
                    )
                    state["step"] = "pilih_paket"

            elif msg == "6":
                search_status = (
                    user.get("search_status", "LOCKED")
                    if user else "LOCKED"
                )

                if search_status == "APPROVED":
                    all_history = get_all_group_history()

                    if all_history:
                        send_wa(
                            chat_id,
                            "📚 SELURUH HISTORY GRUP\n\n"
                            f"{all_history}\n\n"
                            "_50 pesan terbaru_"
                        )
                    else:
                        send_wa(
                            chat_id,
                            "❌ Belum ada history pesan grup."
                        )

                elif search_status == "PENDING":
                    send_wa(
                        chat_id,
                        "⏳ MENUNGGU APPROVAL\n\n"
                        "Pembayaran sedang diverifikasi admin."
                    )

                else:
                    send_wa(
                        chat_id,
                        "🔒 CARI KOTA LAIN - TERKUNCI\n\n"
                        "Fitur ini memerlukan paket khusus.\n\n"
                        "1️⃣ 1 Minggu - Rp 15.000\n"
                        "2️⃣ 1 Bulan - Rp 50.000\n"
                        "3️⃣ 2 Bulan - Rp 80.000\n\n"
                        "Ketik angka paket."
                    )
                    state["step"] = "pilih_paket_cari"

            elif msg == "9":
                send_wa(
                    chat_id,
                    "🔑 KEYWORD LAIN\n\n"
                    "Ketik kata kunci.\n"
                    "Contoh: JHT, BPJS, ERROR, OTP"
                )
                state["step"] = "input_keyword"

            elif msg.isdigit():
                packages = {
                    "1": {"name": "1 Minggu", "price": 40000, "quota": 3},
                    "2": {"name": "1 Bulan", "price": 120000, "quota": 3},
                    "3": {"name": "2 Bulan", "price": 220000, "quota": 3},
                    "4": {"name": "6 Bulan", "price": 600000, "quota": 3},
                    "5": {"name": "Unlimited", "price": 2000000, "quota": 999},
                }

                if msg in packages:
                    pkg = packages[msg]

                    state["temp_data"]["selected_package"] = pkg
                    state["step"] = "konfirmasi_paket"

                    send_wa(
                        chat_id,
                        f"💎 PAKET: {pkg['name']}\n"
                        f"Harga: Rp {pkg['price']:,}\n"
                        f"Quota: {pkg['quota']}x\n\n"
                        "Transfer ke:\n"
                        "🏦 SEABANK: 901040978290 (HAMBALI)\n"
                        "💰 DANA: 083824101264 (HAMBALI)\n\n"
                        "Setelah transfer, ketik:\n"
                        "SUDAH BAYAR"
                    )
                else:
                    send_wa(chat_id, "❌ Pilihan menu tidak valid.")

            else:
                notify_tg(
                    f"📨 Pesan Baru\n"
                    f"Dari: +{chat_id}\n"
                    f"Isi: {raw_text}"
                )
                send_wa(
                    chat_id,
                    "✅ Pesan diteruskan ke admin."
                )

        # ====================================================
        # PILIH PAKET TAMBAH KOTA
        # ====================================================
        elif step == "pilih_paket":

            if msg == "menu":
                state["step"] = "menu"
                send_wa(chat_id, _get_main_menu_text())

            elif msg.isdigit():
                packages = {
                    "1": {"name": "1 Minggu", "price": 40000, "quota": 3},
                    "2": {"name": "1 Bulan", "price": 120000, "quota": 3},
                    "3": {"name": "2 Bulan", "price": 220000, "quota": 3},
                    "4": {"name": "6 Bulan", "price": 600000, "quota": 3},
                    "5": {"name": "Unlimited", "price": 2000000, "quota": 999},
                }

                pkg = packages.get(msg)

                if not pkg:
                    send_wa(chat_id, "❌ Pilihan paket tidak valid.")
                    return jsonify({"status": "ok"}), 200

                state["temp_data"]["selected_package"] = pkg
                state["step"] = "konfirmasi_paket"

                send_wa(
                    chat_id,
                    f"💎 PAKET: {pkg['name']}\n"
                    f"Harga: Rp {pkg['price']:,}\n"
                    f"Quota: {pkg['quota']}x\n\n"
                    "Transfer ke:\n"
                    "🏦 SEABANK: 901040978290 (HAMBALI)\n"
                    "💰 DANA: 083824101264 (HAMBALI)\n\n"
                    "Setelah transfer, ketik:\n"
                    "SUDAH BAYAR"
                )

            else:
                send_wa(
                    chat_id,
                    "Ketik angka paket 1-5."
                )

        # ====================================================
        # KONFIRMASI PEMBAYARAN TAMBAH KOTA
        # ====================================================
        elif step == "konfirmasi_paket":

            if msg == "sudah bayar":
                pkg = state["temp_data"].get("selected_package")

                if not pkg:
                    send_wa(
                        chat_id,
                        "❌ Tidak ada paket tertunda."
                    )
                    state["step"] = "menu"
                    return jsonify({"status": "ok"}), 200

                pkg_key_map = {
                    "1 Minggu": "1_MINGGU",
                    "1 Bulan": "1_BULAN",
                    "2 Bulan": "2_BULAN",
                    "6 Bulan": "6_BULAN",
                    "Unlimited": "UNLIMITED",
                }

                pkg_key = pkg_key_map.get(pkg["name"])

                if not pkg_key:
                    send_wa(chat_id, "❌ Paket tidak valid.")
                    state["step"] = "menu"
                    return jsonify({"status": "ok"}), 200

                success = add_quota_and_set_expiry(
                    chat_id,
                    pkg_key,
                    pkg["quota"],
                )

                if success:
                    state["step"] = "pilih_provinsi"

                    provs = fetch_api("provinsi")

                    if not provs:
                        send_wa(
                            chat_id,
                            "✅ Pembayaran tercatat.\n"
                            "❌ Tetapi daftar provinsi gagal dimuat."
                        )
                    else:
                        text_list = "\n".join(
                            f"{i+1}. {p['nama']}"
                            for i, p in enumerate(provs)
                        )

                        send_wa(
                            chat_id,
                            f"✅ BERHASIL!\n"
                            f"Paket: {pkg['name']}\n"
                            f"Quota: +{pkg['quota']}x\n\n"
                            f"🏢 PILIH PROVINSI\n\n{text_list}\n\n"
                            "Ketik angka provinsi."
                        )

                    state["temp_data"].pop("selected_package", None)

                else:
                    send_wa(
                        chat_id,
                        "❌ Gagal memproses pembayaran."
                    )

            else:
                send_wa(
                    chat_id,
                    "Ketik SUDAH BAYAR jika pembayaran sudah dilakukan."
                )

        # ====================================================
        # PILIH PAKET CARI KOTA
        # ====================================================
        elif step == "pilih_paket_cari":

            packages_cari = {
                "1": {
                    "name": "1 Minggu",
                    "price": 15000,
                    "key": "1_MINGGU_CARI",
                },
                "2": {
                    "name": "1 Bulan",
                    "price": 50000,
                    "key": "1_BULAN_CARI",
                },
                "3": {
                    "name": "2 Bulan",
                    "price": 80000,
                    "key": "2_BULAN_CARI",
                },
            }

            pkg = packages_cari.get(msg)

            if not pkg:
                send_wa(
                    chat_id,
                    "❌ Pilihan paket tidak valid.\n"
                    "Pilih 1, 2, atau 3."
                )
                return jsonify({"status": "ok"}), 200

            state["temp_data"]["selected_cari_pkg"] = pkg
            state["step"] = "upload_bukti_cari"

            send_wa(
                chat_id,
                f"💎 PAKET: {pkg['name']}\n"
                f"Harga: Rp {pkg['price']:,}\n\n"
                "Silakan transfer lalu kirim "
                "BUKTI TRANSFER berupa foto/screenshot.\n\n"
                "🏦 SEABANK: 901040978290 (HAMBALI)\n"
                "💰 DANA: 083824101264 (HAMBALI)"
            )

        # ====================================================
        # UPLOAD BUKTI CARI KOTA
        # ====================================================
        elif step == "upload_bukti_cari":

            msg_type = body.get("typeMessage")

            if msg_type in ["imageMessage", "documentMessage"]:

                pkg = state["temp_data"].get("selected_cari_pkg")

                if not pkg:
                    send_wa(
                        chat_id,
                        "❌ Sesi pembayaran sudah habis.\n"
                        "Ketik 6 untuk mulai lagi."
                    )
                    state["step"] = "menu"
                    return jsonify({"status": "ok"}), 200

                update_user_field(
                    chat_id,
                    {
                        "search_status": "PENDING",
                        "search_package": pkg["key"],
                    },
                )

                proof_url = body.get(
                    "downloadUrl",
                    "Bukti terlampir",
                )

                approve_btn = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            "✅ Setuju",
                            callback_data=f"approve_cari_{chat_id}",
                        ),
                        InlineKeyboardButton(
                            "❌ Tolak",
                            callback_data=f"reject_cari_{chat_id}",
                        ),
                    ]]
                )

                tg_msg = (
                    "📥 PERMINTAAN CARI KOTA BARU\n\n"
                    f"📱 Dari: +{chat_id}\n"
                    f"📦 Paket: {pkg['name']} "
                    f"(Rp {pkg['price']:,})\n"
                    f"🔗 Bukti: {proof_url}\n\n"
                    "Klik tombol untuk approve/tolak."
                )

                notify_tg(tg_msg, approve_btn)

                send_wa(
                    chat_id,
                    "✅ Bukti transfer terkirim!\n"
                    "⏳ Menunggu approval admin."
                )

                state["temp_data"].pop(
                    "selected_cari_pkg",
                    None,
                )
                state["step"] = "menu"

            else:
                send_wa(
                    chat_id,
                    "📸 Silakan kirim bukti transfer "
                    "berupa foto atau dokumen."
                )

        # ====================================================
        # AUTENTIKASI ADMIN
        # ====================================================
        elif step == "admin_auth":

            if ADMIN_PASSWORD and msg == ADMIN_PASSWORD.lower():
                state["step"] = "admin_panel"

                send_wa(
                    chat_id,
                    "✅ AKSES DITERIMA"
                )
                send_wa(
                    chat_id,
                    _get_admin_panel_menu()
                )

            else:
                send_wa(
                    chat_id,
                    "❌ Kode akses salah."
                )
                state["step"] = "menu"

        # ====================================================
        # ADMIN PANEL
        # ====================================================
        elif step == "admin_panel":

            if msg == "menu":
                state["step"] = "menu"
                send_wa(
                    chat_id,
                    _get_main_menu_text()
                )
            else:
                send_wa(
                    chat_id,
                    "🔐 PANEL ADMIN\n\n"
                    "Fitur panel dapat ditambahkan di sini.\n"
                    "Ketik MENU untuk kembali."
                )

        # ====================================================
        # KEYWORD
        # ====================================================
        elif step == "input_keyword":

            if msg == "menu":
                state["step"] = "menu"
                send_wa(
                    chat_id,
                    _get_main_menu_text()
                )

            else:
                response = search_keyword(msg)

                if response:
                    send_wa(chat_id, response)

                    notify_tg(
                        "🔍 Keyword Search\n"
                        f"Dari: +{chat_id}\n"
                        f"Query: {msg}\n"
                        "Status: DITEMUKAN"
                    )

                else:
                    send_wa(
                        chat_id,
                        f"❌ Keyword '{msg}' tidak ditemukan.\n"
                        "Coba keyword lain atau ketik menu."
                    )

                    notify_tg(
                        "🔍 Keyword Search\n"
                        f"Dari: +{chat_id}\n"
                        f"Query: {msg}\n"
                        "Status: TIDAK DITEMUKAN"
                    )

                state["step"] = "menu"

        # ====================================================
        # PROVINSI
        # ====================================================
        elif step == "pilih_provinsi":

            if not is_admin_number(chat_id):
                fresh_user = get_user(chat_id)

                if not fresh_user or fresh_user.get("quota", 0) <= 0:
                    send_wa(
                        chat_id,
                        "⚠️ QUOTA HABIS!\n"
                        "Beli paket lagi dengan mengetik 3."
                    )
                    state["step"] = "menu"
                    return jsonify({"status": "ok"}), 200

            provs = fetch_api("provinsi")

            idx = int(msg) - 1 if msg.isdigit() else -1

            if 0 <= idx < len(provs):
                chosen = provs[idx]

                state["temp_data"].update({
                    "provinsi": chosen["nama"],
                    "id_provinsi": chosen["id"],
                })

                state["step"] = "pilih_kota"

                kotas = fetch_api(
                    f"kota/{chosen['id']}"
                )

                if not kotas:
                    send_wa(
                        chat_id,
                        "❌ Gagal memuat daftar kota."
                    )
                    return jsonify({"status": "ok"}), 200

                list_text = "\n".join(
                    f"{i+1}. {k['nama']}"
                    for i, k in enumerate(kotas)
                )

                send_wa(
                    chat_id,
                    f"🏢 PILIH KOTA\n"
                    f"Provinsi: {chosen['nama']}\n\n"
                    f"{list_text}\n\n"
                    "Ketik angka kota."
                )

            else:
                send_wa(
                    chat_id,
                    "❌ Angka provinsi tidak valid."
                )

        # ====================================================
        # KOTA
        # ====================================================
        elif step == "pilih_kota":

            kotas = fetch_api(
                f"kota/{state['temp_data'].get('id_provinsi')}"
            )

            idx = int(msg) - 1 if msg.isdigit() else -1

            if 0 <= idx < len(kotas):
                chosen = kotas[idx]

                state["temp_data"].update({
                    "kota": chosen["nama"],
                    "id_kota": chosen["id"],
                    "selected_kec": [],
                })

                state["step"] = "pilih_kecamatan"

                kecs = fetch_api(
                    f"kecamatan/{chosen['id']}"
                )

                if not kecs:
                    send_wa(
                        chat_id,
                        "❌ Gagal memuat kecamatan."
                    )
                    return jsonify({"status": "ok"}), 200

                list_text = "\n".join(
                    f"{i+1}. {k['nama']}"
                    for i, k in enumerate(kecs)
                )

                send_wa(
                    chat_id,
                    f"📍 PILIH KECAMATAN\n"
                    f"Kota: {chosen['nama']}\n\n"
                    f"{list_text}\n\n"
                    "Ketik angka.\n"
                    "Bisa lebih dari satu, pisahkan koma."
                )

            else:
                send_wa(
                    chat_id,
                    "❌ Angka kota tidak valid."
                )

        # ====================================================
        # KECAMATAN
        # ====================================================
        elif step == "pilih_kecamatan":

            kecs = fetch_api(
                f"kecamatan/{state['temp_data'].get('id_kota')}"
            )

            indices = []

            for x in msg.split(","):
                x = x.strip()

                if x.isdigit():
                    indices.append(int(x) - 1)

            valid = [
                kecs[i]["nama"]
                for i in indices
                if 0 <= i < len(kecs)
            ]

            if valid:

                update_user_field(
                    chat_id,
                    {
                        "provinsi": state["temp_data"]["provinsi"],
                        "kota": state["temp_data"]["kota"],
                        "kecamatan_list": valid,
                    },
                )

                if not is_admin_number(chat_id):
                    if decrement_quota(chat_id):
                        fresh_user = get_user(chat_id)
                        new_quota = (
                            fresh_user.get("quota", 0)
                            if fresh_user else 0
                        )
                        quota_msg = (
                            f"\nQuota tersisa: {new_quota}"
                        )
                    else:
                        send_wa(
                            chat_id,
                            "❌ Quota sudah habis."
                        )
                        state["step"] = "menu"
                        return jsonify({"status": "ok"}), 200
                else:
                    quota_msg = "\n(Admin Unlimited)"

                send_wa(
                    chat_id,
                    "✅ LOKASI TERSIMPAN!\n\n"
                    f"Provinsi: {state['temp_data']['provinsi']}\n"
                    f"Kota: {state['temp_data']['kota']}\n"
                    f"Kecamatan: {', '.join(valid)}"
                    f"{quota_msg}"
                )

                state["step"] = "menu"
                state["temp_data"] = {}

            else:
                send_wa(
                    chat_id,
                    "❌ Tidak ada kecamatan valid."
                )

        # ====================================================
        # FALLBACK
        # ====================================================
        else:
            state["step"] = "menu"
            state["temp_data"] = {}

            send_wa(
                chat_id,
                "🔄 Sesi direset.\n\n"
                + _get_main_menu_text()
            )

        return jsonify({"status": "received"}), 200

    except Exception as e:
        print(f"❌ WEBHOOK ERROR: {e}")

        try:
            if chat_id:
                send_wa(
                    chat_id,
                    "⚠️ Terjadi error pada bot.\n"
                    "Silakan ketik MENU untuk mencoba lagi."
                )
        except Exception:
            pass

        return jsonify({
            "status": "error",
            "message": "internal error",
        }), 200


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================
@app.route("/tg-webhook", methods=["POST"])
def tg_webhook():
    try:
        data = request.get_json(silent=True) or {}

        callback = data.get("callback_query", {})

        if callback:
            cb_data = callback.get("data", "")

            if cb_data.startswith("approve_cari_"):
                target_chat = cb_data.replace(
                    "approve_cari_",
                    "",
                    1,
                )

                update_user_field(
                    target_chat,
                    {"search_status": "APPROVED"},
                )

                send_wa(
                    target_chat,
                    "✅ PEMBAYARAN DISETUJUI!\n\n"
                    "Anda sekarang bisa menggunakan "
                    "fitur Cari Kota Lain.\n\n"
                    "Ketik 6."
                )

                notify_tg(
                    f"✅ Approved: +{target_chat}"
                )

            elif cb_data.startswith("reject_cari_"):
                target_chat = cb_data.replace(
                    "reject_cari_",
                    "",
                    1,
                )

                update_user_field(
                    target_chat,
                    {"search_status": "LOCKED"},
                )

                send_wa(
                    target_chat,
                    "❌ PEMBAYARAN DITOLAK\n\n"
                    "Bukti transfer tidak valid. "
                    "Silakan hubungi admin."
                )

                notify_tg(
                    f"❌ Rejected: +{target_chat}"
                )

            return jsonify({"ok": True}), 200

        message = data.get("message", {})
        reply_to = message.get("reply_to_message", {})

        if reply_to and reply_to.get("from", {}).get("is_bot"):
            original_text = reply_to.get("text", "")
            wa_number = ""

            for line in original_text.split("\n"):
                if "Dari:" in line:
                    clean = (
                        line.replace(" ", "")
                        .replace("+", "+")
                        .strip()
                    )

                    if clean.startswith("Dari:+"):
                        wa_number = clean.replace(
                            "Dari:+",
                            "",
                            1,
                        )
                        break

            if wa_number:
                send_wa(
                    wa_number,
                    "🤖 Balasan Admin:\n\n"
                    + message.get("text", "")
                )

        return jsonify({"ok": True}), 200

    except Exception as e:
        print(f"❌ TG WEBHOOK ERROR: {e}")
        return jsonify({"ok": False}), 200


# ============================================================
# HEALTH CHECK
# ============================================================
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "online",
        "service": "BOT SAHABAT JHT",
    }), 200


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200


# ============================================================
# START SERVER
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    print("======================================")
    print("🤖 BOT SAHABAT JHT STARTING")
    print(f"🌐 PORT: {port}")
    print(f"👑 ADMIN: {len(ADMIN_NUMBERS)} nomor")
    print("======================================")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
