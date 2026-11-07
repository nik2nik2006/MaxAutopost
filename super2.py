import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration

from telethon import TelegramClient

from maxapi import Bot as MaxBot
from maxapi.enums.format import Format
from maxapi.types import InputMedia

# ── Настройки ─────────────────────────────────────────────
TG_API_ID = 23085740  # api_id для Telethon (my.telegram.org)
TG_API_HASH = "5286184dd7768305eb684972ff230aaf"  # api_hash для Telethon

TG_TOKEN = "9091502525:AAG5RXBRO0rn4NBD_LAmxyjROoBZDV7dO-E"
TG_CHAT_ID = -1004331778776        # чат/канал-источник в Telegram Зеркало МЧС

MAX_TOKEN = "f9LHodD0cOLMAxumEUqTcfFGYKer_iq-6u9_FtwNadopBTx5VbzjSYbg-ydvYSdJXOlbWqCym3Bo3erSGEZr"
MAX_CHAT_ID = -76749423478797             # чат-получатель в МАКС В Белгород

MAX_TEXT_LIMIT = 3900
TMP_DIR = "tmp_media"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("tg2max")

# Инициализация клиентов
tg_bot = Bot(TG_TOKEN)
dp = Dispatcher()
mx = MaxBot(MAX_TOKEN)

# Telethon: нужен только для скачивания файлов без лимита 20 МБ
tg_client = TelegramClient("session", TG_API_ID, TG_API_HASH)

Path(TMP_DIR).mkdir(exist_ok=True)


def chunk(text: str) -> list[str]:
    if not text:
        return ["(без текста)"]
    return [text[i:i + MAX_TEXT_LIMIT] for i in range(0, len(text), MAX_TEXT_LIMIT)]


async def send_to_max(text: str, media_path: str | None = None):
    parts = chunk(text)
    for i, part in enumerate(parts):
        attachments = [InputMedia(media_path)] if (media_path and i == 0) else None
        await mx.send_message(
            chat_id=MAX_CHAT_ID,
            text=part,
            format=Format.HTML,
            attachments=attachments,
        )
        if i < len(parts) - 1:
            await asyncio.sleep(0.6)


def caption_html(message: Message) -> str:
    caption = message.caption or ""
    entities = message.caption_entities or []
    return html_decoration.unparse(caption, entities=entities)


def remove_file_safe(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
            log.debug("Файл удалён: %s", path)
        else:
            log.debug("Файл не найден (уже удалён): %s", path)
    except Exception as e:
        log.error("Ошибка при удалении файла %s: %s", path, e)


async def download_file_via_telethon(file_id: str, destination: str):
    """Скачивает файл любого размера через Telethon (без лимита 20 МБ)."""
    log.info("Скачивание файла через Telethon: %s -> %s", file_id, destination)
    # В Telethon мы не используем file_id напрямую, а ищем сообщение по ID,
    # потому что в aiogram у нас нет доступа к raw-объекту.
    # Поэтому самый простой способ — передать сам объект сообщения из aiogram
    # и скачать медиа через client.download_media(message.media)
    raise RuntimeError("Эта функция вызывается неправильно: используйте download_media_from_message.")


async def download_media_from_message(message):
    """Скачивает медиа из сообщения через Telethon, обходя лимит 20 МБ."""
    path = None
    if message.photo:
        photo = message.photo[-1]
        path = os.path.join(TMP_DIR, f"{photo.file_unique_id}.jpg")
        # Скачиваем через Telethon по file_id
        file_path = await tg_client.download_file(photo.file_id, file=path)
        log.info("Фото скачано: %s", file_path)
    elif message.video:
        video = message.video
        path = os.path.join(TMP_DIR, f"{video.file_unique_id}.mp4")
        file_path = await tg_client.download_file(video.file_id, file=path)
        log.info("Видео скачано: %s", file_path)
    return path


# ── Обработчики Telegram (aiogram) ────────────────────────

@dp.message(F.chat.id == TG_CHAT_ID, F.text)
async def on_text(message: Message):
    await send_to_max(message.html_text)


@dp.message(F.chat.id == TG_CHAT_ID, F.photo)
async def on_photo(message: Message):
    path = None
    try:
        path = await download_media_from_message(message)
        html_caption = caption_html(message)
        await send_to_max(html_caption, media_path=path)
    finally:
        remove_file_safe(path)


@dp.message(F.chat.id == TG_CHAT_ID, F.video)
async def on_video(message: Message):
    path = None
    try:
        path = await download_media_from_message(message)
        html_caption = caption_html(message)
        await send_to_max(html_caption, media_path=path)
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
            path = await download_media_from_message(message)
            html_caption = caption_html(message)
            await send_to_max(html_caption, media_path=path)
        elif message.video:
            path = await download_media_from_message(message)
            html_caption = caption_html(message)
            await send_to_max(html_caption, media_path=path)
        elif message.text:
            await send_to_max(message.html_text)
        elif message.caption:
            html_caption = caption_html(message)
            await send_to_max(html_caption)
    finally:
        remove_file_safe(path)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Бот-мост запущен!\n"
        "Теперь большие видео тоже скачиваются (через Telethon).\n"
        "Сообщения уходят в МАКС с сохранением ссылок.\n"
        "Временные файлы удаляются сразу после отправки."
    )


async def main():
    log.info("Запуск Telethon для скачивания больших файлов...")
    await tg_client.start()
    log.info("Telethon запущен. Запуск aiogram polling...")
    await dp.start_polling(tg_bot)


if __name__ == "__main__":
    asyncio.run(main())
