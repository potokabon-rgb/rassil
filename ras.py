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
logger = logging.getLogger("UltraFlexibleRassilBot")

# Конфигурация из ТЗ
BOT_TOKEN = "8954398769:AAFn2uMSdK_YBMZwIHboSdwfcj43Z0zXHDk"
API_ID = 30774866
API_HASH = "fd176053cf8817de383edb515f74cb59"
SESSION_NAME = "rassilbot"
DB_NAME = "ultra_broadcast.db"

bot = Bot(token=BOT_TOKEN)
router = Router()
userbot = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Глобальный флаг для возможности экстренной остановки рассылки
ACTIVE_BROADCAST = {"is_running": False}


# --- БАЗА ДАННЫХ И НАСТРОЙКИ ---
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
    # Дефолтные ультра-настройки
    default_settings = {
        "delay_min": "7",  # Мин. задержка между сообщениями (сек)
        "delay_max": "15",  # Макс. задержка между сообщениями (сек)
        "batch_size": "20",  # Размер пачки сообщений перед большим перерывом
        "batch_pause": "60",  # Большой перерыв после пачки (сек)
        "parse_mode": "html",  # html, markdown или off
        "typing_action": "1",  # 1 - имитировать "печатает...", 0 - выкл
        "skip_duplicates": "1",  # 1 - пропускать дубликаты/ошибочные
    }
    for k, v in default_settings.items():
      await db.execute(
          "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
      )
    await db.commit()


# --- FSM СОСТОЯНИЯ ---
class BroadcastStates(StatesGroup):
  waiting_for_message = State()
  waiting_for_targets = State()


class SettingsStates(StatesGroup):
  waiting_for_value = State()


class BlacklistStates(StatesGroup):
  waiting_for_add = State()
  waiting_for_remove = State()


# --- РАНДОМИЗАЦИЯ (СПЛАЙСИНГ) ---
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
              KeyboardButton(text="🛑 Стоп рассылка"),
          ],
          [
              KeyboardButton(text="⚙️ Гибкие настройки"),
              KeyboardButton(text="🚫 Черный список"),
          ],
      ],
      resize_keyboard=True,
  )


async def get_settings_keyboard():
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT key, value FROM settings") as cursor:
      st = {row[0]: row[1] for row in (await cursor.fetchall())}

  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=f"⏱ Пауза мин: {st.get('delay_min')}с",
                  callback_data="set_delay_min",
              ),
              InlineKeyboardButton(
                  text=f"⏱ Пауза макс: {st.get('delay_max')}с",
                  callback_data="set_delay_max",
              ),
          ],
          [
              InlineKeyboardButton(
                  text=f"📦 Размер пачки: {st.get('batch_size')} шт.",
                  callback_data="set_batch_size",
              ),
              InlineKeyboardButton(
                  text=f"☕️ Пауза пачки: {st.get('batch_pause')}с",
                  callback_data="set_batch_pause",
              ),
          ],
          [
              InlineKeyboardButton(
                  text=(
                      "📝 Режим текста:"
                      f" {st.get('parse_mode').upper()}"
                  ),
                  callback_data="toggle_parse_mode",
              ),
              InlineKeyboardButton(
                  text=(
                      "✍️ Имитация ввода:"
                      f" {'Вкл ✅' if st.get('typing_action') == '1' else 'Выкл ❌'}"
                  ),
                  callback_data="toggle_typing",
              ),
          ],
      ]
  )
  return kb


# --- ОБРАБОТЧИКИ ---
@router.message(CommandStart())
async def cmd_start(message: Message):
  await message.answer(
      f"Привет, {html.bold(message.from_user.full_name)}!\n"
      "🔥 Ультрагибкая система рассылок через Userbot готова к работе.\n"
      "Настраивай параметры в меню ниже:",
      reply_markup=main_menu_kb(),
  )


@router.message(F.text == "⚙️ Гибкие настройки")
async def show_settings(message: Message):
  kb = await get_settings_keyboard()
  await message.answer(
      "⚙️ <b>Панель ультрагибкой настройки рассылки:</b>\n\n"
      "• <b>Паузы:</b> рандомный интервал между отправкой сообщений.\n"
      "• <b>Пачки:</b> бот делает большой перерыв после отправки N сообщений (для защиты от FloodWait).\n"
      "• <b>Имитация ввода:</b> бот перед отправкой показывает статус «печатает...».\n"
      "• <b>Рандомизация:</b> текст `{Привет|Здорово}` меняется индивидуально для каждого получателя.",
      reply_markup=kb,
  )


@router.callback_query(F.data.startswith("set_") | F.data.startswith("toggle_"))
async def process_settings_callback(callback: CallbackQuery, state: FSMContext):
  action = callback.data

  if action == "toggle_parse_mode":
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT value FROM settings WHERE key='parse_mode'"
      ) as cursor:
        cur_mode = (await cursor.fetchone())[0]
      new_mode = "markdown" if cur_mode == "html" else ("off" if cur_mode == "markdown" else "html")
      await db.execute(
          "UPDATE settings SET value = ? WHERE key = 'parse_mode'",
          (new_mode,),
      )
      await db.commit()
    kb = await get_settings_keyboard()
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer(f"Режим изменен на: {new_mode.upper()}")

  elif action == "toggle_typing":
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT value FROM settings WHERE key='typing_action'"
      ) as cursor:
        cur = (await cursor.fetchone())[0]
      new_val = "0" if cur == "1" else "1"
      await db.execute(
          "UPDATE settings SET value = ? WHERE key = 'typing_action'",
          (new_val,),
      )
      await db.commit()
    kb = await get_settings_keyboard()
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer("Статус имитации изменен!")

  else:
    # Запрос числовых параметров через FSM
    setting_key = action.replace("set_", "")
    await state.update_data(setting_key=setting_key)
    await state.set_state(SettingsStates.waiting_for_value)
    param_names = {
        "delay_min": "минимальную задержку (в секундах)",
        "delay_max": "максимальную задержку (в секундах)",
        "batch_size": "размер пачки сообщений (количество)",
        "batch_pause": "паузу после пачки (в секундах)",
    }
    await callback.message.answer(
        f"Введи новое целое число для параметра: <b>{param_names.get(setting_key, setting_key)}</b>"
    )
    await callback.answer()


@router.message(SettingsStates.waiting_for_value)
async def save_new_setting_value(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("Ошибка! Введи корректное целое число.")
    return

  data = await state.get_data()
  key = data.get("setting_key")
  val = message.text

  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute("UPDATE settings SET value = ? WHERE key = ?", (val, key))
    await db.commit()

  await state.clear()
  kb = await get_settings_keyboard()
  await message.answer(
      f"✅ Параметр успешно обновлен на <b>{val}</b>!", reply_markup=main_menu_kb()
  )


# --- ЧЕРНЫЙ СПИСОК ---
@router.message(F.text == "🚫 Черный список")
async def blacklist_menu(message: Message):
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT target FROM blacklist") as cursor:
      rows = await cursor.fetchall()
  bl_list = ", ".join([r[0] for r in rows]) if rows else "Пусто"

  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="➕ Добавить в ЧС", callback_data="bl_add"
              ),
              InlineKeyboardButton(
                  text="➖ Удалить из ЧС", callback_data="bl_remove"
              ),
          ]
      ]
  )
  await message.answer(
      f"🚫 <b>Управление черным списком:</b>\n\nТекущие исключения: {bl_list}",
      reply_markup=kb,
  )


@router.callback_query(F.data == "bl_add")
async def cb_bl_add(callback: CallbackQuery, state: FSMContext):
  await state.set_state(BlacklistStates.waiting_for_add)
  await callback.message.answer(
      "Отправь юзернейм или ID, который нужно добавить в черный список (можно несколько с новой строки):"
  )
  await callback.answer()


@router.message(BlacklistStates.waiting_for_add)
async def process_bl_add(message: Message, state: FSMContext):
  targets = [t.strip() for t in message.text.split("\n") if t.strip()]
  async with aiosqlite.connect(DB_NAME) as db:
    for t in targets:
      await db.execute(
          "INSERT OR IGNORE INTO blacklist (target) VALUES (?)", (t,)
      )
    await db.commit()
  await state.clear()
  await message.answer(
      f"✅ Успешно добавлено в ЧС: {len(targets)} записей.",
      reply_markup=main_menu_kb(),
  )


@router.callback_query(F.data == "bl_remove")
async def cb_bl_remove(callback: CallbackQuery, state: FSMContext):
  await state.set_state(BlacklistStates.waiting_for_remove)
  await callback.message.answer(
      "Отправь юзернейм или ID для удаления из черного списка:"
  )
  await callback.answer()


@router.message(BlacklistStates.waiting_for_remove)
async def process_bl_remove(message: Message, state: FSMContext):
  target = message.text.strip()
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute("DELETE FROM blacklist WHERE target = ?", (target,))
    await db.commit()
  await state.clear()
  await message.answer(
      f"✅ Запись <code>{target}</code> удалена из черного списка.",
      reply_markup=main_menu_kb(),
  )


# --- ПРОЦЕСС РАССЫЛКИ ---
@router.message(F.text == "🛑 Стоп рассылка")
async def stop_broadcast(message: Message):
  if ACTIVE_BROADCAST["is_running"]:
    ACTIVE_BROADCAST["is_running"] = False
    await message.answer(
        "🛑 Сигнал остановки отправлен. Рассылка завершится на текущем шаге.",
        reply_markup=main_menu_kb(),
    )
  else:
    await message.answer(
        "ℹ️ Сейчас нет активных запущенных рассылок.",
        reply_markup=main_menu_kb(),
    )


@router.message(F.text == "🚀 Запустить рассылку")
async def start_broadcast(message: Message, state: FSMContext):
  if ACTIVE_BROADCAST["is_running"]:
    await message.answer("⚠️ Рассылка уже выполняется! Сначала останови её.")
    return
  await state.set_state(BroadcastStates.waiting_for_message)
  await message.answer(
      "📝 Введи текст рассылки.\n"
      "<i>Поддерживается сплайсинг рандомизации (`{Привет|Добрый день}`) и форматирование.</i>"
  )


@router.message(BroadcastStates.waiting_for_message)
async def get_message_text(message: Message, state: FSMContext):
  await state.update_data(text=message.text)
  await state.set_state(BroadcastStates.waiting_for_targets)
  await message.answer(
      "👥 Отправь список получателей (каждый юзернейм или ID с новой строки):"
  )


@router.message(BroadcastStates.waiting_for_targets)
async def execute_ultra_broadcast(message: Message, state: FSMContext):
  data = await state.get_data()
  raw_text = data.get("text")
  targets = [t.strip() for t in message.text.split("\n") if t.strip()]
  await state.clear()

  ACTIVE_BROADCAST["is_running"] = True

  # Загружаем настройки из БД
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT key, value FROM settings") as cursor:
      st = {row[0]: row[1] for row in (await cursor.fetchall())}
    async with db.execute("SELECT target FROM blacklist") as cursor:
      blacklist = {row[0] for row in (await cursor.fetchall())}

  delay_min = int(st.get("delay_min", 7))
  delay_max = int(st.get("delay_max", 15))
  batch_size = int(st.get("batch_size", 20))
  batch_pause = int(st.get("batch_pause", 60))
  parse_mode_val = st.get("parse_mode", "html")
  parse_mode = None if parse_mode_val == "off" else parse_mode_val
  typing_on = st.get("typing_action", "1") == "1"

  status_msg = await message.answer(
      f"🚀 **Ультрарассылка запущена!**\n"
      f"• Всего получателей: {len(targets)}\n"
      f"• Пакетный лимит: каждые {batch_size} шт. пауза {batch_pause}с\n"
      f"• Статус: Выполняется..."
  )

  success = 0
  fail = 0
  skipped = 0
  counter = 0

  for target in targets:
    if not ACTIVE_BROADCAST["is_running"]:
      await status_msg.edit_text("🛑 Рассылка была экстренно остановлена пользователем!")
      break

    if target in blacklist:
      skipped += 1
      continue

    # Рандомизация текста для каждого нового адресата
    current_text = spin_text(raw_text)
    current_delay = random.randint(delay_min, delay_max)

    try:
      # Имитация ввода текста (если включено)
      if typing_on:
        try:
          async with userbot.action(target, "typing"):
            await asyncio.sleep(random.uniform(1.2, 2.5))
        except Exception:
          pass

      await userbot.send_message(target, current_text, parse_mode=parse_mode)
      success += 1
      counter += 1

      # Проверка пачки сообщений
      if counter % batch_size == 0 and counter < len(targets):
        logger.info(
            f"Пачка из {batch_size} сообщений отправлена. Перерыв {batch_pause} секунд..."
        )
        await asyncio.sleep(batch_pause)
      else:
        await asyncio.sleep(current_delay)

    errors.FloodWaitError as e:
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
      f"📊 **Итоги ультрагибкой рассылки:**\n\n"
      f"✅ Успешно доставлено: <b>{success}</b>\n"
      f"❌ Ошибок отправки: <b>{fail}</b>\n"
      f"🚫 Пропущено по ЧС: <b>{skipped}</b>",
      reply_markup=main_menu_kb(),
  )


async def main():
  await init_db()
  await userbot.start()
  logger.info("Ультрагибкий юзербот успешно запущен.")

  dp = Dispatcher()
  dp.include_router(router)

  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except (KeyboardInterrupt, SystemExit):
    logger.info("Бот выключен.")