import asyncio
import logging
import os

from aiogram import Bot as TgBot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.utils.text_decorations import html_decoration

from maxapi import Bot as MaxBot
from maxapi.enums.format import Format
from maxapi.types import InputMedia

# ── Настройки ─────────────────────────────────────────────
TG_TOKEN = "9091502525:AAG5RXBRO0rn4NBD_LAmxyjROoBZDV7dO-E"
TG_CHAT_ID = -1004331778776        # чат/канал-источник в Telegram Зеркало МЧС

MAX_TOKEN = "f9LHodD0cOLMAxumEUqTcfFGYKer_iq-6u9_FtwNadopBTx5VbzjSYbg-ydvYSdJXOlbWqCym3Bo3erSGEZr"
MAX_CHAT_ID = -76749423478797             # чат-получатель в МАКС В Белгород

MAX_TEXT_LIMIT = 3900               # безопасный запас до лимита 4000
TMP_DIR = "tmp_media"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("tg2max")

tg = TgBot(TG_TOKEN)
dp = Dispatcher()
mx = MaxBot(MAX_TOKEN)

os.makedirs(TMP_DIR, exist_ok=True)


def chunk(text: str) -> list[str]:
    if not text:
        return ["(без текста)"]
    return [text[i:i + MAX_TEXT_LIMIT] for i in range(0, len(text), MAX_TEXT_LIMIT)]


async def send_to_max(text: str, photo_path: str | None = None):
    for i, part in enumerate(chunk(text)):
        attachments = [InputMedia(photo_path)] if (photo_path and i == 0) else None
        await mx.send_message(
            chat_id=MAX_CHAT_ID,
            text=part,
            format=Format.HTML,          # <a href>, <b>, <i> останутся кликабельными
            attachments=attachments,
        )
        await asyncio.sleep(0.6)         # лимит MAX: не более 2 сообщений в секунду


def caption_html(message: Message) -> str:
    """HTML для подписи к медиа (у aiogram нет готового caption_html)."""
    return html_decoration.unparse(message.caption or "", entities=message.caption_entities or [])


# ── Обработчики Telegram ──────────────────────────────────
@dp.message(F.chat.id == TG_CHAT_ID, F.text)
async def on_text(message: Message):
    # message.html_text уже содержит готовый HTML: ссылки, жирный, курсив и т.д.
    await send_to_max(message.html_text)


@dp.message(F.chat.id == TG_CHAT_ID, F.photo)
async def on_photo(message: Message):
    photo = message.photo[-1]
    path = os.path.join(TMP_DIR, f"{photo.file_unique_id}.jpg")
    await message.bot.download(photo, destination=path)
    await send_to_max(caption_html(message), photo_path=path)


@dp.message(F.chat.id == TG_CHAT_ID)
async def on_other(message: Message):
    # видео, документы и прочее — пока только текст/подпись
    await send_to_max(caption_html(message))


@dp.channel_post(F.chat.id == TG_CHAT_ID)
async def on_channel_post(message: Message):
    if message.photo:
        photo = message.photo[-1]
        path = os.path.join(TMP_DIR, f"{photo.file_unique_id}.jpg")
        await message.bot.download(photo, destination=path)
        await send_to_max(caption_html(message), photo_path=path)
    else:
        await send_to_max(message.html_text if message.text else caption_html(message))


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Бот-мост запущен. Сообщения из чата-источника уходят в МАКС.")


async def main():
    log.info("Мост запущен")
    await dp.start_polling(tg)


if __name__ == "__main__":
    asyncio.run(main())
