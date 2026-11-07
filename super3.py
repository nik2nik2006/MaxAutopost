import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration

from telethon import TelegramClient
from telethon.tl.types import Message as TelethonMessage

# aiohttp больше не нужен для создания сессии, maxapi использует свой
# import aiohttp 

from maxapi import Bot as MaxBot
from maxapi.enums.format import Format
from maxapi.types import InputMedia

#  Добавление переменных среды
from dotenv import load_dotenv

load_dotenv()

MAX_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = int(os.getenv("MAX_CHANNEL_ID", 0))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = int(os.getenv("TG_CHANNEL_ID", 0))

# ── Настройки ─────────────────────────────────────────────
TG_API_ID = os.getenv("TG_API_ID")  # api_id для Telethon (my.telegram.org)
TG_API_HASH = os.getenv("TG_API_HASH")  # api_hash для Telethon

MAX_TEXT_LIMIT = 3900
TMP_DIR = "tmp_media"
# Увеличили лимит скачивания: теперь бот будет пытаться качать файлы до 100 МБ
DOWNLOAD_SAFE_LIMIT_MB = 100 

# Лимит отправки оставляем жёстким (это ограничение самого MAX, его нельзя обойти кодом)
MAX_ATTACHMENT_LIMIT_MB = 95

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s"
)
log = logging.getLogger("tg2max")

tg_bot = Bot(TG_TOKEN)
dp = Dispatcher()

mx = None
tg_client = TelegramClient("session", TG_API_ID, TG_API_HASH)
Path(TMP_DIR).mkdir(exist_ok=True)


def chunk(text: str) -> list[str]:
    if not text:
        return ["(без текста)"]
    return [text[i:i + MAX_TEXT_LIMIT] for i in range(0, len(text), MAX_TEXT_LIMIT)]


async def send_to_max(text: str, media_path: str | None = None):
    global mx
    
    if mx is None:
        log.error("[SEND ERROR] MaxBot ещё не инициализирован!")
        return

    parts = chunk(text)
    for i, part in enumerate(parts):
        attachments = None
        if media_path and os.path.exists(media_path):
            size_mb = os.path.getsize(media_path) / (1024 * 1024)
            if size_mb > 100:
                log.warning(f"Файл {size_mb:.1f} МБ слишком большой для MAX. Вложение пропускаем.")
                attachments = None
            else:
                attachments = [InputMedia(media_path)]
        
        try:
            await mx.send_message(
                chat_id=MAX_CHAT_ID,
                text=part,
                format=Format.HTML,
                attachments=attachments,
            )
            log.info(f"[SEND] Часть {i+1}/{len(parts)} успешно отправлена в MAX.")
        except Exception as e:
            log.error(f"[SEND ERROR] Ошибка отправки в MAX: {e}")
        
        if i < len(parts) - 1:
            await asyncio.sleep(0.6)


def caption_html(message: Message) -> str:
    caption = message.caption or ""
    entities = message.caption_entities or []
    return html_decoration.unparse(caption, entities=entities)


def remove_file_safe(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
            log.debug(f"Файл удалён: {path}")
    except Exception as e:
        log.error(f"Ошибка удаления файла {path}: {e}")


async def download_small_media_via_telethon(aiogram_message: Message):
    msg_id = aiogram_message.message_id
    log.info(f"[DOWNLOAD] Проверка медиа для сообщения id={msg_id}")

    try:
        chat_entity = await tg_client.get_entity(TG_CHAT_ID)
        telethon_msgs = await tg_client.get_messages(chat_entity, ids=[msg_id])

        if not telethon_msgs or len(telethon_msgs) == 0:
            return None

        t_msg = telethon_msgs[0]
        path = None
        media = None
        ext = ""

        # Определяем тип медиа
        if t_msg.photo:
            photo = t_msg.photo
            ext = ".jpg"
            media = photo
            # ДЛЯ ФОТО: не пытаемся угадать размер по sizes — это ненадёжно.
            # Сразу скачиваем, потом проверяем размер файла.
        elif t_msg.video:
            video = t_msg.video
            # ДЛЯ ВИДЕО: размер известен заранее, можно отсечь большие файлы ДО скачивания
            size_mb = video.size / (1024 * 1024)
            log.info(f"[SIZE] Размер видео: {size_mb:.2f} МБ")
            if size_mb > DOWNLOAD_SAFE_LIMIT_MB:
                log.warning(
                    f"[SKIP] Видео слишком большое ({size_mb:.1f} МБ > {DOWNLOAD_SAFE_LIMIT_MB} МБ). "
                    "Скачивание отменено."
                )
                return None
            ext = ".mp4"
            media = video
        else:
            return None

        # Скачивание
        path = os.path.join(TMP_DIR, f"{media.id}{ext}")
        log.info(f"[DOWNLOAD] Скачивание медиа в {path}...")
        await tg_client.download_media(media, file=path)

        # ПРОВЕРКА ПОСЛЕ СКАЧИВАНИЯ (для фото и видео)
        if path and os.path.exists(path):
            final_size = os.path.getsize(path) / (1024 * 1024)
            log.info(f"[OK] Файл скачан: {final_size:.2f} МБ.")

            if final_size > DOWNLOAD_SAFE_LIMIT_MB:
                log.warning(
                    f"[SKIP] Файл превышает лимит ({final_size:.1f} МБ > {DOWNLOAD_SAFE_LIMIT_MB} МБ). "
                    "Файл удалён, будет отправлена только подпись."
                )
                os.remove(path)
                return None
            
            if final_size == 0:
                log.warning("[SKIP] Файл скачался пустым.")
                os.remove(path)
                return None

            return path
        else:
            log.warning("[SKIP] Файл не скачался.")
            return None

    except Exception as e:
        log.exception(f"[ERROR] Ошибка при скачивании медиа: {e}")
        # Если что-то пошло не так, удаляем частичный файл, если он есть
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass
        return None




async def send_to_max(text: str, media_path: str | None = None):
    global mx

    if mx is None:
        log.error("[SEND ERROR] MaxBot ещё не инициализирован!")
        return

    parts = chunk(text)
    for i, part in enumerate(parts):
        attachments = None

        # Проверка: файл существует и его размер <= лимиту отправки MAX
        if media_path and os.path.exists(media_path):
            size_mb = os.path.getsize(media_path) / (1024 * 1024)

            if size_mb > MAX_ATTACHMENT_LIMIT_MB:
                log.warning(
                    f"[SKIP ATTACH] Файл ({size_mb:.1f} МБ) превышает лимит отправки MAX "
                    f"({MAX_ATTACHMENT_LIMIT_MB} МБ). Отправляем только текст."
                )
                attachments = None  # Не прикрепляем файл, отправляем только текст
            else:
                attachments = [InputMedia(media_path)]

        try:
            await mx.send_message(
                chat_id=MAX_CHAT_ID,
                text=part,
                format=Format.HTML,
                attachments=attachments,
            )
            log.info(f"[SEND] Часть {i+1}/{len(parts)} успешно отправлена в MAX.")
        except Exception as e:
            log.error(f"[SEND ERROR] Ошибка отправки в MAX: {e}")

        if i < len(parts) - 1:
            await asyncio.sleep(0.6)

@dp.message(F.chat.id == TG_CHAT_ID, F.text)
async def on_text(message: Message):
    await send_to_max(message.html_text)


@dp.message(F.chat.id == TG_CHAT_ID, F.photo)
async def on_photo(message: Message):
    path = None
    try:
        path = await download_small_media_via_telethon(message)
        if path:
            html_caption = caption_html(message)
            await send_to_max(html_caption, media_path=path)
        else:
            if message.caption:
                html_caption = caption_html(message)
                await send_to_max(html_caption)
                log.info("Фото не отправлено, отправлена подпись.")
    finally:
        remove_file_safe(path)


@dp.message(F.chat.id == TG_CHAT_ID, F.video)
async def on_video(message: Message):
    path = None
    try:
        path = await download_small_media_via_telethon(message)
        if path:
            html_caption = caption_html(message)
            await send_to_max(html_caption, media_path=path)
        else:
            if message.caption:
                html_caption = caption_html(message)
                await send_to_max(html_caption)
                log.info("Видео не отправлено, отправлена подпись.")
            else:
                log.info("Видео пропущено.")
    finally:
        remove_file_safe(path)


@dp.message(F.chat.id == TG_CHAT_ID)
async def on_other(message: Message):
    if message.caption:
        html_caption = caption_html(message)
        await send_to_max(html_caption)


@dp.channel_post(F.chat.id == TG_CHAT_ID)
async def on_channel_post(message: Message):
    path = None
    try:
        if message.photo:
            path = await download_small_media_via_telethon(message)
            if path:
                await send_to_max(caption_html(message), media_path=path)
            elif message.caption:
                await send_to_max(caption_html(message))
        elif message.video:
            path = await download_small_media_via_telethon(message)
            if path:
                await send_to_max(caption_html(message), media_path=path)
            elif message.caption:
                await send_to_max(caption_html(message))
        elif message.text:
            await send_to_max(message.html_text)
        elif message.caption:
            await send_to_max(caption_html(message))
    finally:
        remove_file_safe(path)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    status = "OK" if mx is not None else "NOT INITIALIZED"
    await message.answer(
        f"Бот-мост запущен.\n"
        f"Статус MAX-бота: {status}\n"
        f"Файлы > {DOWNLOAD_SAFE_LIMIT_MB} МБ пропускаются.\n"
        "Чтобы видео уходило, запускайте скрипт командой:\nset AIOHTTP_NO_SSL=1 && python super3.py"
    )


async def main():
    global mx
    log.info("Инициализация MaxBot...")
    # ВАЖНО: Никаких session, verify_ssl и т.п. здесь быть не должно!
    mx = MaxBot(MAX_TOKEN)
    log.info("MaxBot инициализирован.")

    log.info("Запуск Telethon...")
    await tg_client.start()
    log.info("Telethon запущен. Запуск aiogram polling...")
    await dp.start_polling(tg_bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановка бота пользователем.")
