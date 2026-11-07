import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration

from maxapi import Bot as MaxBot
from maxapi.enums.format import Format
from maxapi.types import InputMedia

# ── Настройки ─────────────────────────────────────────────
TG_TOKEN = "9091502525:AAG5RXBRO0rn4NBD_LAmxyjROoBZDV7dO-E"
TG_CHAT_ID = -1004331778776        # чат/канал-источник в Telegram Зеркало МЧС

MAX_TOKEN = "f9LHodD0cOLMAxumEUqTcfFGYKer_iq-6u9_FtwNadopBTx5VbzjSYbg-ydvYSdJXOlbWqCym3Bo3erSGEZr"
MAX_CHAT_ID = -76749423478797             # чат-получатель в МАКС В Белгород

MAX_TEXT_LIMIT = 3900  # Безопасный лимит (чуть меньше 4000)
TMP_DIR = "tmp_media"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("tg2max")

tg = Bot(TG_TOKEN)
dp = Dispatcher()
mx = MaxBot(MAX_TOKEN)

Path(TMP_DIR).mkdir(exist_ok=True)


def chunk(text: str) -> list[str]:
    """Разбивает длинный текст на куски не длиннее MAX_TEXT_LIMIT."""
    if not text:
        return ["(без текста)"]
    return [text[i:i + MAX_TEXT_LIMIT] for i in range(0, len(text), MAX_TEXT_LIMIT)]


async def send_to_max(text: str, media_path: str | None = None):
    """Отправляет текст (и опционально медиафайл) в МАКС, нарезая длинные сообщения."""
    parts = chunk(text)
    for i, part in enumerate(parts):
        attachments = [InputMedia(media_path)] if (media_path and i == 0) else None
        await mx.send_message(
            chat_id=MAX_CHAT_ID,
            text=part,
            format=Format.HTML,
            attachments=attachments,
        )
        # Лимит MAX: не более 2 сообщений в секунду в один чат
        if i < len(parts) - 1:
            await asyncio.sleep(0.6)


def caption_html(message: Message) -> str:
    """Преобразует caption с entities в HTML для MAX."""
    caption = message.caption or ""
    entities = message.caption_entities or []
    return html_decoration.unparse(caption, entities=entities)


def remove_file_safe(path: str):
    """Безопасно удаляет файл, логируя результат."""
    try:
        if os.path.exists(path):
            os.remove(path)
            log.debug("Файл удалён: %s", path)
        else:
            log.debug("Файл не найден (уже удалён): %s", path)
    except Exception as e:
        log.error("Ошибка при удалении файла %s: %s", path, e)


# ── Обработчики Telegram ──────────────────────────────────

@dp.message(F.chat.id == TG_CHAT_ID, F.text)
async def on_text(message: Message):
    """Пересылает обычный текст с HTML-разметкой."""
    await send_to_max(message.html_text)


@dp.message(F.chat.id == TG_CHAT_ID, F.photo)
async def on_photo(message: Message):
    """Скачивает фото, пересылает с подписью, удаляет файл."""
    photo = message.photo[-1]  # Берём фото максимального качества
    path = os.path.join(TMP_DIR, f"{photo.file_unique_id}.jpg")
    
    try:
        await message.bot.download(photo, destination=path)
        html_caption = caption_html(message)
        await send_to_max(html_caption, media_path=path)
    finally:
        remove_file_safe(path)


@dp.message(F.chat.id == TG_CHAT_ID, F.video)
async def on_video(message: Message):
    """Скачивает видео, пересылает с подписью (ссылки сохраняются), удаляет файл."""
    video = message.video
    # Используем file_unique_id для уникальности имени файла
    path = os.path.join(TMP_DIR, f"{video.file_unique_id}.mp4")
    
    try:
        await message.bot.download(video, destination=path)
        html_caption = caption_html(message)
        await send_to_max(html_caption, media_path=path)
    finally:
        remove_file_safe(path)


@dp.message(F.chat.id == TG_CHAT_ID)
async def on_other(message: Message):
    """Обрабатывает документы, голосовые и т.д. — отправляем только подпись, если есть."""
    if message.caption:
        html_caption = caption_html(message)
        await send_to_max(html_caption)


@dp.channel_post(F.chat.id == TG_CHAT_ID)
async def on_channel_post(message: Message):
    """Обработка постов из каналов (фото, видео, текст)."""
    if message.photo:
        photo = message.photo[-1]
        path = os.path.join(TMP_DIR, f"{photo.file_unique_id}.jpg")
        try:
            await message.bot.download(photo, destination=path)
            html_caption = caption_html(message)
            await send_to_max(html_caption, media_path=path)
        finally:
            remove_file_safe(path)
            
    elif message.video:
        video = message.video
        path = os.path.join(TMP_DIR, f"{video.file_unique_id}.mp4")
        try:
            await message.bot.download(video, destination=path)
            html_caption = caption_html(message)
            await send_to_max(html_caption, media_path=path)
        finally:
            remove_file_safe(path)
            
    elif message.text:
        await send_to_max(message.html_text)
        
    elif message.caption:
        html_caption = caption_html(message)
        await send_to_max(html_caption)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Бот-мост запущен!\n"
        "Сообщения из чата-источника автоматически уходят в МАКС.\n"
        "Поддерживаются: текст, фото, видео (с сохранением ссылок в подписях).\n"
        "Временные файлы удаляются сразу после отправки."
    )


async def main():
    log.info("Мост Telegram → МАКС запущен")
    await dp.start_polling(tg)


if __name__ == "__main__":
    asyncio.run(main())
