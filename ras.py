import asyncio
import logging
import os
import random
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from telethon import TelegramClient, errors

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
)
logger = logging.getLogger("CommandRassilBot")

BOT_TOKEN = "8954398769:AAFn2uMSdK_YBMZwIHboSdwfcj43Z0zXHDk"
API_ID = 30774866
API_HASH = "fd176053cf8817de383edb515f74cb59"
SESSION_NAME = "rassilbot"
DB_NAME = "command_broadcast.db"

bot = Bot(token=BOT_TOKEN)
router = Router()
userbot = TelegramClient(SESSION_NAME, API_ID, API_HASH)

ACTIVE_BROADCAST = {"is_running": False}


# --- БД И НАСТРОЙКИ ---
async def init_db():
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                target TEXT PRIMARY KEY
            )
        """)
    defaults = {
        "delay_min": "7",
        "delay_max": "15",
        "batch_size": "20",
        "batch_pause": "60",
        "parse_mode": "html",
        "typing_action": "1",
    }
    for k, v in defaults.items():
      await db.execute(
          "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
      )
    await db.commit()


# --- FSM СОСТОЯНИЯ ---
class BroadcastStates(StatesGroup):
  waiting_for_message = State()
  waiting_for_targets = State()


class AuthStates(StatesGroup):
  waiting_for_phone = State()
  waiting_for_code = State()
  waiting_for_password = State()


class BlacklistStates(StatesGroup):
  waiting_for_add = State()
  waiting_for_remove = State()


# --- РАНДОМИЗАЦИЯ ТЕКСТА ---
def spin_text(text: str) -> str:
  import re

  while "{" in text and "}" in text:
    match = re.search(r"\{([^{}]+)\}", text)
    if not match:
      break
    options = match.group(1).split("|")
    text = text.replace(match.group(0), random.choice(options), 1)
  return text


# --- КЛАВИАТУРЫ ---
def main_menu_kb():
  return ReplyKeyboardMarkup(
      keyboard=[
          [
              KeyboardButton(text="🚀 Запустить рассылку"),
              KeyboardButton(text="🛑 Стоп"),
          ],
          [
              KeyboardButton(text="📱 Подключить аккаунт"),
              KeyboardButton(text="⚙️ Статус настроек"),
          ],
          [
              KeyboardButton(text="🚫 Черный список"),
              KeyboardButton(text="📋 Список команд"),
          ],
      ],
      resize_keyboard=True,
  )


# --- СТАРТ И МЕНЮ ---
@router.message(CommandStart())
async def cmd_start(message: Message):
  await message.answer(
      f"Привет, {html.bold(message.from_user.full_name)}!\n"
      "🤖 Профессиональная система авто-рассылки активирована.\n"
      "Управляйте настройками через команды в чате или кнопки ниже.",
      reply_markup=main_menu_kb(),
  )


@router.message(F.text == "📋 Список команд")
async def cmd_list_help(message: Message):
  await message.answer(
      "📋 <b>СПИСОК ВСЕХ КОМАНД УПРАВЛЕНИЯ РАССЫЛКОЙ:</b>\n\n"
      "🔹 <code>/set_delay_min [сек]</code> — Установить минимальную паузу\n"
      "🔹 <code>/set_delay_max [сек]</code> — Установить максимальную паузу\n"
      "🔹 <code>/set_batch_size [число]</code> — Размер пачки сообщений\n"
      "🔹 <code>/set_batch_pause [сек]</code> — Пауза после отправки пачки\n"
      "🔹 <code>/set_parse [html/markdown/off]</code> — Режим форматирования\n"
      "🔹 <code>/set_typing [1/0]</code> — Имитация набора текста\n"
      "🔹 <code>/settings</code> — Посмотреть текущие настройки\n"
      "🔹 <code>/connect</code> — Подключить аккаунт (ввод телефона/кода)\n"
      "🔹 <code>/blacklist</code> — Показать черный список\n"
      "🔹 <code>/stop</code> — Экстренно остановить рассылку",
      reply_markup=main_menu_kb(),
  )


# --- АВТОРИЗАЦИЯ АККАУНТА («ПОДКЛЮЧИТЬ») ---
@router.message(F.text == "📱 Подключить аккаунт")
@router.message(Command("connect"))
async def start_auth(message: Message, state: FSMContext):
  if userbot.is_connected():
    try:
      me = await userbot.get_me()
      if me:
        await message.answer(
            f"ℹ️ Аккаунт уже подключен:\n👤 <b>{me.first_name}</b> (@{me.username})"
        )
        return
    except Exception:
      pass

  await state.set_state(AuthStates.waiting_for_phone)
  await message.answer(
      "📱 <b>Авторизация Userbot</b>\n\nВведите номер телефона аккаунта в международном формате (например, <code>+79991234567</code>):"
  )


@router.message(AuthStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
  phone = message.text.strip()
  await state.update_data(phone=phone)
  try:
    if not userbot.is_connected():
      await userbot.connect()
    result = await userbot.send_code_request(phone)
    await state.update_data(phone_code_hash=result.phone_code_hash)
    await state.set_state(AuthStates.waiting_for_code)
    await message.answer(
        "✅ Код подтверждения отправлен в ваш Telegram!\nВведите полученный код цифрами:"
    )
  except Exception as e:
    await state.clear()
    await message.answer(f"❌ Ошибка отправки кода: {e}")


@router.message(AuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
  code = message.text.strip()
  data = await state.get_data()
  phone = data.get("phone")
  phone_code_hash = data.get("phone_code_hash")

  try:
    await userbot.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    await state.clear()
    me = await userbot.get_me()
    await message.answer(
        f"🎉 <b>Успешная авторизация!</b>\nАккаунт <b>{me.first_name}</b> (@{me.username}) успешно подключен.",
        reply_markup=main_menu_kb(),
    )
  except errors.SessionPasswordNeededError:
    await state.set_state(AuthStates.waiting_for_password)
    await message.answer(
        "🔐 На аккаунте включена двухэтапная аутентификация (облачный пароль).\nВведите ваш пароль:"
    )
  except Exception as e:
    await state.clear()
    await message.answer(f"❌ Ошибка входа: {e}")


@router.message(AuthStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
  password = message.text.strip()
  try:
    await userbot.sign_in(password=password)
    await state.clear()
    me = await userbot.get_me()
    await message.answer(
        f"🎉 <b>Успешная авторизация с 2FA!</b>\nАккаунт <b>{me.first_name}</b> подключен.",
        reply_markup=main_menu_kb(),
    )
  except Exception as e:
    await state.clear()
    await message.answer(f"❌ Ошибка ввода пароля: {e}")


# --- КОМАНДЫ НАСТРОЙКИ В ЧАТЕ ---
@router.message(Command("settings"))
@router.message(F.text == "⚙️ Статус настроек")
async def cmd_show_settings(message: Message):
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT key, value FROM settings") as cursor:
      st = {row[0]: row[1] for row in (await cursor.fetchall())}

  await message.answer(
      f"⚙️ <b>Текущие настройки рассылки:</b>\n\n"
      f"⏱ Мин. пауза: <code>{st.get('delay_min')} сек.</code> (`/set_delay_min`)\n"
      f"⏱ Макс. пауза: <code>{st.get('delay_max')} сек.</code> (`/set_delay_max`)\n"
      f"📦 Размер пачки: <code>{st.get('batch_size')} шт.</code> (`/set_batch_size`)\n"
      f"☕️ Пауза пачки: <code>{st.get('batch_pause')} сек.</code> (`/set_batch_pause`)\n"
      f"📝 Формат текста: <code>{st.get('parse_mode').upper()}</code> (`/set_parse`)\n"
      f"✍️ Имитация ввода: <code>{'Вкл' if st.get('typing_action') == '1' else 'Выкл'}</code> (`/set_typing`)",
      reply_markup=main_menu_kb(),
  )


@router.message(Command("set_delay_min"))
async def cmd_set_delay_min(message: Message):
  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.answer(
        "❌ Использование: <code>/set_delay_min 5</code> (укажите секунды)"
    )
    return
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE settings SET value = ? WHERE key = 'delay_min'", (args[1],)
    )
    await db.commit()
  await message.answer(f"✅ Минимальная задержка изменена на {args[1]} сек.")


@router.message(Command("set_delay_max"))
async def cmd_set_delay_max(message: Message):
  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.answer(
        "❌ Использование: <code>/set_delay_max 15</code> (укажите секунды)"
    )
    return
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE settings SET value = ? WHERE key = 'delay_max'", (args[1],)
    )
    await db.commit()
  await message.answer(f"✅ Максимальная задержка изменена на {args[1]} сек.")


@router.message(Command("set_batch_size"))
async def cmd_set_batch_size(message: Message):
  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.answer(
        "❌ Использование: <code>/set_batch_size 25</code> (количество)"
    )
    return
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE settings SET value = ? WHERE key = 'batch_size'", (args[1],)
    )
    await db.commit()
  await message.answer(f"✅ Размер пачки изменен на {args[1]} сообщений.")


@router.message(Command("set_batch_pause"))
async def cmd_set_batch_pause(message: Message):
  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.answer(
        "❌ Использование: <code>/set_batch_pause 60</code> (секунды)"
    )
    return
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE settings SET value = ? WHERE key = 'batch_pause'", (args[1],)
    )
    await db.commit()
  await message.answer(f"✅ Пауза после пачки изменена на {args[1]} сек.")


@router.message(Command("set_parse"))
async def cmd_set_parse(message: Message):
  args = message.text.split()
  if len(args) < 2 or args[1].lower() not in ["html", "markdown", "off"]:
    await message.answer("❌ Использование: <code>/set_parse html</code> (html / markdown / off)")
    return
  mode = args[1].lower()
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE settings SET value = ? WHERE key = 'parse_mode'", (mode,)
    )
    await db.commit()
  await message.answer(f"✅ Режим форматирования изменен на: {mode.upper()}")


@router.message(Command("set_typing"))
async def cmd_set_typing(message: Message):
  args = message.text.split()
  if len(args) < 2 or args[1] not in ["1", "0"]:
    await message.answer("❌ Использование: <code>/set_typing 1</code> (1 - вкл, 0 - выкл)")
    return
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE settings SET value = ? WHERE key = 'typing_action'", (args[1],)
    )
    await db.commit()
  await message.answer(f"✅ Имитация набора текста установлена в статус: {args[1]}")


# --- ЧЕРНЫЙ СПИСОК ---
@router.message(Command("blacklist"))
@router.message(F.text == "🚫 Черный список")
async def show_blacklist(message: Message):
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT target FROM blacklist") as cursor:
      rows = await cursor.fetchall()
  bl = ", ".join([r[0] for r in rows]) if rows else "Пусто"
  await message.answer(
      f"🚫 <b>Черный список исключений:</b>\n{bl}\n\nДобавить: <code>/bl_add [username]</code>\nУдалить: <code>/bl_remove [username]</code>"
  )


@router.message(Command("bl_add"))
async def cmd_bl_add(message: Message):
  args = message.text.split(maxsplit=1)
  if len(args) < 2:
    await message.answer("❌ Использование: <code>/bl_add @username</code>")
    return
  target = args[1].strip()
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "INSERT OR IGNORE INTO blacklist (target) VALUES (?)", (target,)
    )
    await db.commit()
  await message.answer(f"✅ Пользователь <code>{target}</code> добавлен в ЧС.")


@router.message(Command("bl_remove"))
async def cmd_bl_remove(message: Message):
  args = message.text.split(maxsplit=1)
  if len(args) < 2:
    await message.answer("❌ Использование: <code>/bl_remove @username</code>")
    return
  target = args[1].strip()
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute("DELETE FROM blacklist WHERE target = ?", (target,))
    await db.commit()
  await message.answer(f"✅ Пользователь <code>{target}</code> удален из ЧС.")


# --- РАССЫЛКА ---
@router.message(Command("stop"))
@router.message(F.text == "🛑 Стоп")
async def stop_broadcast(message: Message):
  if ACTIVE_BROADCAST["is_running"]:
    ACTIVE_BROADCAST["is_running"] = False
    await message.answer("🛑 Сигнал остановки отправлен.")
  else:
    await message.answer("ℹ️ Нет активных рассылок.")


@router.message(F.text == "🚀 Запустить рассылку")
async def start_broadcast(message: Message, state: FSMContext):
  if ACTIVE_BROADCAST["is_running"]:
    await message.answer("⚠️ Рассылка уже выполняется!")
    return
  if not userbot.is_connected():
    await message.answer("⚠️ Сначала подключите аккаунт кнопкой «📱 Подключить аккаунт» или командой `/connect`!")
    return
  try:
    if not await userbot.is_user_authorized():
      await message.answer("⚠️ Аккаунт не авторизован. Выполните подключение заново.")
      return
  except Exception:
    pass

  await state.set_state(BroadcastStates.waiting_for_message)
  await message.answer("📝 Введите текст рассылки (поддерживается сплайсинг `{Привет|Здорово}`):")


@router.message(BroadcastStates.waiting_for_message)
async def get_text(message: Message, state: FSMContext):
  await state.update_data(text=message.text)
  await state.set_state(BroadcastStates.waiting_for_targets)
  await message.answer("👥 Отправьте список получателей (каждый юзернейм или ID с новой строки):")


@router.message(BroadcastStates.waiting_for_targets)
async def execute_broadcast(message: Message, state: FSMContext):
  data = await state.get_data()
  raw_text = data.get("text")
  targets = [t.strip() for t in message.text.split("\n") if t.strip()]
  await state.clear()

  ACTIVE_BROADCAST["is_running"] = True

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT key, value FROM settings") as cursor:
      st = {row[0]: row[1] for row in (await cursor.fetchall())}
    async with db.execute("SELECT target FROM blacklist") as cursor:
      blacklist = {row[0] for row in (await cursor.fetchall())}

  delay_min = int(st.get("delay_min", 7))
  delay_max = int(st.get("delay_max", 15))
  batch_size = int(st.get("batch_size", 20))
  batch_pause = int(st.get("batch_pause", 60))
  pm_val = st.get("parse_mode", "html")
  parse_mode = None if pm_val == "off" else pm_val
  typing_on = st.get("typing_action", "1") == "1"

  status_msg = await message.answer(
      f"🚀 Рассылка запущена!\nПолучателей: {len(targets)}\nПачка: каждые {batch_size} шт. пауза {batch_pause}с."
  )

  success = 0
  fail = 0
  skipped = 0
  counter = 0

  for target in targets:
    if not ACTIVE_BROADCAST["is_running"]:
      await status_msg.edit_text("🛑 Рассылка остановлена пользователем.")
      break

    if target in blacklist:
      skipped += 1
      continue

    current_text = spin_text(raw_text)
    current_delay = random.randint(delay_min, delay_max)

    try:
      if typing_on:
        try:
          async with userbot.action(target, "typing"):
            await asyncio.sleep(random.uniform(1.0, 2.0))
        except Exception:
          pass

      await userbot.send_message(target, current_text, parse_mode=parse_mode)
      success += 1
      counter += 1

      if counter % batch_size == 0 and counter < len(targets):
        await asyncio.sleep(batch_pause)
      else:
        await asyncio.sleep(current_delay)

    except errors.FloodWaitError as e:
      logger.warning(f"FloodWait! Спим {e.seconds} секунд...")
      await asyncio.sleep(e.seconds)
      try:
        await userbot.send_message(target, current_text, parse_mode=parse_mode)
        success += 1
      except Exception:
        fail += 1
    except Exception as ex:
      logger.error(f"Ошибка отправки {target}: {ex}")
      fail += 1

  ACTIVE_BROADCAST["is_running"] = False
  await status_msg.edit_text(
      f"📊 **Итоги рассылки:**\n\n"
      f"✅ Успешно: <b>{success}</b>\n"
      f"❌ Ошибок: <b>{fail}</b>\n"
      f"🚫 Пропущено по ЧС: <b>{skipped}</b>",
      reply_markup=main_menu_kb(),
  )


async def main():
  await init_db()
  # Пытаемся подключить клиент без блокировки, если сессия уже есть
  try:
    await userbot.connect()
  except Exception:
    pass

  dp = Dispatcher()
  dp.include_router(router)

  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except (KeyboardInterrupt, SystemExit):
    logger.info("Бот остановлен.")