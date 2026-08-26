import os
import logging
from typing import Any

import aiohttp
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

EMSIFA_BASE = "https://emsifa.github.io/api-wilayah-indonesia/api"
PAGE_SIZE = 8
HTTP_TIMEOUT = 15

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("wa-telegram-bot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diisi.")
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL belum diisi.")
if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY belum diisi.")


async def http_get_json(url: str):
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "WA-Telegram-Bot/1.0",
            },
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"API HTTP {response.status}: {url}")
            return await response.json()


async def get_provinces():
    return await http_get_json(f"{EMSIFA_BASE}/provinces.json")


async def get_regencies(province_id: str):
    return await http_get_json(f"{EMSIFA_BASE}/regencies/{province_id}.json")


async def get_districts(regency_id: str):
    return await http_get_json(f"{EMSIFA_BASE}/districts/{regency_id}.json")


def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


async def supabase_request(
    method: str,
    table: str,
    params: dict | None = None,
    json_data: Any = None,
):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            url,
            headers=supabase_headers(),
            params=params,
            json=json_data,
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"Supabase HTTP {response.status}: {text}")
            if not text:
                return None
            try:
                return await response.json()
            except Exception:
                return text


async def save_user(telegram_id: int, username: str | None, full_name: str):
    data = {
        "telegram_id": telegram_id,
        "username": username,
        "full_name": full_name,
    }
    return await supabase_request(
        "POST",
        "users",
        params={"on_conflict": "telegram_id"},
        json_data=data,
    )


async def save_city(
    telegram_id: int,
    province_id: str,
    province_name: str,
    regency_id: str,
    regency_name: str,
    district_id: str,
    district_name: str,
):
    data = {
        "telegram_id": telegram_id,
        "province_id": province_id,
        "province_name": province_name,
        "city_id": regency_id,
        "city_name": regency_name,
        "district_id": district_id,
        "district_name": district_name,
    }
    return await supabase_request("POST", "user_cities", json_data=data)


async def get_user_cities(telegram_id: int):
    return await supabase_request(
        "GET",
        "user_cities",
        params={
            "telegram_id": f"eq.{telegram_id}",
            "order": "created_at.desc",
        },
    )


def paginate(items: list, page: int):
    total = len(items)
    if total == 0:
        return [], 0
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    return items[start:start + PAGE_SIZE], total_pages


def main_menu(user_id: int):
    keyboard = [
        [
            InlineKeyboardButton("👤 Profil", callback_data="menu_profile"),
            InlineKeyboardButton("📡 Cek Status", callback_data="menu_status"),
        ],
        [
            InlineKeyboardButton("➕ Tambah Kota", callback_data="menu_add_city"),
            InlineKeyboardButton("🏙️ Kota Saya", callback_data="menu_my_city"),
        ],
        [
            InlineKeyboardButton("🔎 Cari Kota Lain", callback_data="menu_search_city"),
            InlineKeyboardButton("🔑 Keyword Lain", callback_data="menu_keyword"),
        ],
        [
            InlineKeyboardButton("📱 Solusi JMO", callback_data="menu_jmo"),
            InlineKeyboardButton("⚙️ Auto Format", callback_data="menu_auto_format"),
        ],
        [
            InlineKeyboardButton("🚫 No Blacklist", callback_data="menu_blacklist"),
            InlineKeyboardButton("❓ Bantuan", callback_data="menu_help"),
        ],
        [InlineKeyboardButton("👨‍💻 Hubungi Admin", callback_data="menu_contact_admin")],
    ]
    if ADMIN_ID and str(user_id) == str(ADMIN_ID):
        keyboard.append(
            [InlineKeyboardButton("🛠️ Panel Admin", callback_data="menu_admin")]
        )
    return InlineKeyboardMarkup(keyboard)


async def show_main_menu(query, user_id: int):
    await query.edit_message_text(
        "🏠 <b>MENU UTAMA</b>\n\nSilakan pilih menu:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(user_id),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        await save_user(user.id, user.username, user.full_name)
    except Exception:
        logger.exception("Gagal menyimpan user ke Supabase.")

    await update.message.reply_text(
        f"👋 Halo <b>{user.first_name}</b>!\n\n"
        "Selamat datang.\n\nSilakan pilih menu:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(user.id),
    )


async def show_provinces(query, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    try:
        provinces = await get_provinces()
        context.user_data["provinces"] = provinces
        items, total_pages = paginate(provinces, page)

        keyboard = [
            [InlineKeyboardButton(x["name"], callback_data=f"province:{x['id']}")]
            for x in items
        ]

        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton("⬅️", callback_data=f"province_page:{page - 1}")
            )
        navigation.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton("➡️", callback_data=f"province_page:{page + 1}")
            )
        keyboard.append(navigation)
        keyboard.append([InlineKeyboardButton("◀️ Kembali", callback_data="back_main")])

        await query.edit_message_text(
            "🌎 <b>PILIH PROVINSI</b>\n\nSilakan pilih provinsi:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        logger.exception("Gagal mengambil provinsi.")
        await query.edit_message_text(
            "❌ Gagal mengambil data provinsi.\n\nSilakan coba lagi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Coba Lagi", callback_data="menu_add_city")],
                [InlineKeyboardButton("◀️ Kembali", callback_data="back_main")],
            ]),
        )


async def show_regencies(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    province_id: str,
    page: int = 0,
):
    try:
        provinces = context.user_data.get("provinces", [])
        province = next(
            (x for x in provinces if str(x["id"]) == str(province_id)),
            None,
        )
        if not province:
            provinces = await get_provinces()
            province = next(
                (x for x in provinces if str(x["id"]) == str(province_id)),
                None,
            )
        if not province:
            raise RuntimeError("Provinsi tidak ditemukan.")

        regencies = await get_regencies(province_id)
        context.user_data["selected_province"] = province
        context.user_data["regencies"] = regencies

        items, total_pages = paginate(regencies, page)
        keyboard = [
            [InlineKeyboardButton(x["name"], callback_data=f"regency:{x['id']}")]
            for x in items
        ]

        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    "⬅️",
                    callback_data=f"regency_page:{province_id}:{page - 1}",
                )
            )
        navigation.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    "➡️",
                    callback_data=f"regency_page:{province_id}:{page + 1}",
                )
            )
        keyboard.append(navigation)
        keyboard.append(
            [InlineKeyboardButton("◀️ Kembali", callback_data="menu_add_city")]
        )

        await query.edit_message_text(
            "🏙️ <b>PILIH KOTA / KABUPATEN</b>\n\n"
            f"Provinsi: <b>{province['name']}</b>\n\nSilakan pilih:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        logger.exception("Gagal mengambil kabupaten/kota.")
        await query.edit_message_text(
            "❌ Gagal mengambil data kota/kabupaten.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Kembali", callback_data="menu_add_city")]
            ]),
        )


async def show_districts(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    regency_id: str,
    page: int = 0,
):
    try:
        regencies = context.user_data.get("regencies", [])
        regency = next(
            (x for x in regencies if str(x["id"]) == str(regency_id)),
            None,
        )
        if not regency:
            raise RuntimeError("Kabupaten/kota tidak ditemukan.")

        districts = await get_districts(regency_id)
        province = context.user_data.get("selected_province")
        context.user_data["selected_regency"] = regency
        context.user_data["districts"] = districts

        items, total_pages = paginate(districts, page)
        keyboard = [
            [InlineKeyboardButton(x["name"], callback_data=f"district:{x['id']}")]
            for x in items
        ]

        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    "⬅️",
                    callback_data=f"district_page:{regency_id}:{page - 1}",
                )
            )
        navigation.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    "➡️",
                    callback_data=f"district_page:{regency_id}:{page + 1}",
                )
            )
        keyboard.append(navigation)
        keyboard.append([
            InlineKeyboardButton(
                "◀️ Kembali",
                callback_data=f"province:{province['id']}" if province else "menu_add_city",
            )
        ])

        await query.edit_message_text(
            "📍 <b>PILIH KECAMATAN</b>\n\n"
            f"Provinsi: <b>{province['name']}</b>\n"
            f"Kota/Kab: <b>{regency['name']}</b>\n\n"
            "Silakan pilih kecamatan:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        logger.exception("Gagal mengambil kecamatan.")
        await query.edit_message_text(
            "❌ Gagal mengambil data kecamatan.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Kembali", callback_data="menu_add_city")]
            ]),
        )


async def confirm_district(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    district_id: str,
):
    districts = context.user_data.get("districts", [])
    district = next(
        (x for x in districts if str(x["id"]) == str(district_id)),
        None,
    )
    province = context.user_data.get("selected_province")
    regency = context.user_data.get("selected_regency")

    if not district or not province or not regency:
        await query.edit_message_text(
            "❌ Data pilihan tidak ditemukan.\nSilakan ulangi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Tambah Kota", callback_data="menu_add_city")]
            ]),
        )
        return

    context.user_data["selected_district"] = district

    text = (
        "✅ <b>KONFIRMASI WILAYAH</b>\n\n"
        f"🌎 Provinsi:\n<b>{province['name']}</b>\n\n"
        f"🏙️ Kota/Kabupaten:\n<b>{regency['name']}</b>\n\n"
        f"📍 Kecamatan:\n<b>{district['name']}</b>\n\n"
        "Simpan wilayah ini?"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Simpan", callback_data="city_save"),
                InlineKeyboardButton("❌ Batal", callback_data="menu_add_city"),
            ]
        ]),
    )


async def save_selected_city(query, context: ContextTypes.DEFAULT_TYPE):
    user = query.from_user
    province = context.user_data.get("selected_province")
    regency = context.user_data.get("selected_regency")
    district = context.user_data.get("selected_district")

    if not province or not regency or not district:
        await query.edit_message_text(
            "❌ Data wilayah belum lengkap.\n\nSilakan ulangi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Tambah Kota", callback_data="menu_add_city")]
            ]),
        )
        return

    try:
        await save_city(
            telegram_id=user.id,
            province_id=str(province["id"]),
            province_name=province["name"],
            regency_id=str(regency["id"]),
            regency_name=regency["name"],
            district_id=str(district["id"]),
            district_name=district["name"],
        )

        for key in ["selected_province", "selected_regency", "selected_district"]:
            context.user_data.pop(key, None)

        await query.edit_message_text(
            "🎉 <b>BERHASIL!</b>\n\n"
            "Wilayah berhasil disimpan.\n\n"
            f"🌎 {province['name']}\n"
            f"🏙️ {regency['name']}\n"
            f"📍 {district['name']}\n\n"
            "Wilayah ini sekarang masuk ke <b>Kota Saya</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏙️ Kota Saya", callback_data="menu_my_city")],
                [InlineKeyboardButton("➕ Tambah Lagi", callback_data="menu_add_city")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
            ]),
        )
    except Exception:
        logger.exception("Gagal menyimpan kota.")
        await query.edit_message_text(
            "❌ Gagal menyimpan wilayah ke database.\n\n"
            "Periksa konfigurasi Supabase dan tabel database.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Coba Lagi", callback_data="menu_add_city")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
            ]),
        )


async def show_my_cities(query):
    user = query.from_user
    try:
        cities = await get_user_cities(user.id)

        if not cities:
            await query.edit_message_text(
                "🏙️ <b>KOTA SAYA</b>\n\nBelum ada wilayah yang tersimpan.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Tambah Kota", callback_data="menu_add_city")],
                    [InlineKeyboardButton("◀️ Kembali", callback_data="back_main")],
                ]),
            )
            return

        lines = ["🏙️ <b>KOTA SAYA</b>\n"]
        for index, city in enumerate(cities, start=1):
            lines.append(
                f"{index}. 🌎 {city['province_name']}\n"
                f"   🏙️ {city['city_name']}\n"
                f"   📍 {city['district_name']}\n"
            )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Tambah Kota", callback_data="menu_add_city")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
            ]),
        )
    except Exception:
        logger.exception("Gagal mengambil Kota Saya.")
        await query.edit_message_text(
            "❌ Gagal mengambil data Kota Saya.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")]
            ]),
        )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user = query.from_user
    data = query.data

    if data == "noop":
        return

    if data == "back_main":
        await show_main_menu(query, user.id)
        return

    if data == "menu_add_city":
        await show_provinces(query, context, 0)
        return

    if data.startswith("province_page:"):
        await show_provinces(query, context, int(data.split(":")[1]))
        return

    if data.startswith("province:"):
        await show_regencies(query, context, data.split(":")[1], 0)
        return

    if data.startswith("regency_page:"):
        _, province_id, page = data.split(":")
        await show_regencies(query, context, province_id, int(page))
        return

    if data.startswith("regency:"):
        await show_districts(query, context, data.split(":")[1], 0)
        return

    if data.startswith("district_page:"):
        _, regency_id, page = data.split(":")
        await show_districts(query, context, regency_id, int(page))
        return

    if data.startswith("district:"):
        await confirm_district(query, context, data.split(":")[1])
        return

    if data == "city_save":
        await save_selected_city(query, context)
        return

    if data == "menu_my_city":
        await show_my_cities(query)
        return

    if data == "menu_profile":
        await query.edit_message_text(
            "👤 <b>PROFIL</b>\n\n"
            f"Nama: {user.full_name}\n"
            f"Username: @{user.username or '-'}\n"
            f"Telegram ID: <code>{user.id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Kembali", callback_data="back_main")]
            ]),
        )
        return

    if data == "menu_status":
        await query.edit_message_text(
            "📡 <b>CEK STATUS</b>\n\n"
            "🟢 Telegram Bot: Aktif\n"
            "🟢 Emsifa API: Siap digunakan\n"
            "🟡 Evolution API: Belum dipasang\n"
            "🟡 WhatsApp: Belum terhubung\n"
            "🟡 Supabase: Terhubung jika konfigurasi benar",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Kembali", callback_data="back_main")]
            ]),
        )
        return

    feature_names = {
        "menu_search_city": "🔎 CARI KOTA LAIN",
        "menu_keyword": "🔑 KEYWORD LAIN",
        "menu_jmo": "📱 SOLUSI JMO",
        "menu_auto_format": "⚙️ AUTO FORMAT",
        "menu_blacklist": "🚫 NO BLACKLIST",
        "menu_help": "❓ BANTUAN",
        "menu_contact_admin": "👨‍💻 HUBUNGI ADMIN",
        "menu_admin": "🛠️ PANEL ADMIN",
    }

    if data in feature_names:
        await query.edit_message_text(
            f"{feature_names[data]}\n\n"
            "Fitur ini akan kita aktifkan pada tahap berikutnya.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Kembali", callback_data="back_main")]
            ]),
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception:", exc_info=context.error)


def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_error_handler(error_handler)

    logger.info("Bot sedang dijalankan...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
