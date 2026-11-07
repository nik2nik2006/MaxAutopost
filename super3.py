import asyncio
import logging
import os
import random
import re
from pathlib import Path
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration

from telethon import TelegramClient, events
# from telethon.tl.types import Message as TelethonMessage
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

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
# api_id для Telethon (my.telegram.org)
api_id = TG_API_ID = os.getenv("TG_API_ID")
api_hash = TG_API_HASH = os.getenv("TG_API_HASH")  # api_hash для Telethon

MAX_TEXT_LIMIT = 3900
TMP_DIR = "tmp_media"
# Увеличили лимит скачивания: теперь бот будет пытаться качать файлы до 100 МБ
DOWNLOAD_SAFE_LIMIT_MB = 100

# Лимит отправки оставляем жёстким (это ограничение самого MAX,
# его нельзя обойти кодом)
MAX_ATTACHMENT_LIMIT_MB = 95

logging.basicConfig(
    filename='app.log',
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
    return [text[i:i + MAX_TEXT_LIMIT] for i in range(0, len(text),
                                                      MAX_TEXT_LIMIT)]


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
                log.warning(f"Файл {size_mb:.1f} МБ слишком большой для MAX. "
                            "Вложение пропускаем.")
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
            log.info(f"[SEND] Часть {i+1}/{len(parts)} успешно"
                     " отправлена в MAX.")
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
            # ДЛЯ ВИДЕО: размер известен заранее, можно отсечь большие
            # файлы ДО скачивания
            size_mb = video.size / (1024 * 1024)
            log.info(f"[SIZE] Размер видео: {size_mb:.2f} МБ")
            if size_mb > DOWNLOAD_SAFE_LIMIT_MB:
                log.warning(
                    f"[SKIP] Видео слишком большое ({size_mb:.1f} МБ >"
                    f" {DOWNLOAD_SAFE_LIMIT_MB} МБ). "
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
                    f"[SKIP] Файл превышает лимит ({final_size:.1f} МБ >"
                    f" {DOWNLOAD_SAFE_LIMIT_MB} МБ). "
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


# async def send_to_max(text: str, media_path: str | None = None):
#     global mx

#     if mx is None:
#         log.error("[SEND ERROR] MaxBot ещё не инициализирован!")
#         return

#     parts = chunk(text)
#     for i, part in enumerate(parts):
#         attachments = None

#         # Проверка: файл существует и его размер <= лимиту отправки MAX
#         if media_path and os.path.exists(media_path):
#             size_mb = os.path.getsize(media_path) / (1024 * 1024)

#             if size_mb > MAX_ATTACHMENT_LIMIT_MB:
#                 log.warning(
#                     f"[SKIP ATTACH] Файл ({size_mb:.1f} МБ) превышает
# лимит отправки MAX "
#                     f"({MAX_ATTACHMENT_LIMIT_MB} МБ). Отправляем только
# текст."
#                 )
#                 attachments = None  # Не прикрепляем файл, отправляем только
# текст
#             else:
#                 attachments = [InputMedia(media_path)]

#         try:
#             await mx.send_message(
#                 chat_id=MAX_CHAT_ID,
#                 text=part,
#                 format=Format.HTML,
#                 attachments=attachments,
#             )
#             log.info(f"[SEND] Часть {i+1}/{len(parts)} успешно отправлена в MAX.")
#         except Exception as e:
#             log.error(f"[SEND ERROR] Ошибка отправки в MAX: {e}")

#         if i < len(parts) - 1:
#             await asyncio.sleep(0.6)

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


# Далее логика пересылки сообщений из канала в канал
main_channels = {
    -1001623943359: -1004255311300,  # первый "ОперШтаб Белгород второй rt"
    -1002615649192: -1004255311300,  # первый "МОй белгород №1 второй rt"
    -1002820362754: -1004255311300,  # первый "Информация БПЛА Белгород"
    # -3000000000000: -4000000000000,
}


# Для хранения групп сообщений (альбомов)
group_buffer = defaultdict(list)
group_timers = {}


def extract_text_and_filenames(messages):
    text_parts = []
    filenames = []

    for msg in messages:
        if msg.message:
            text_parts.append(msg.message)

        if msg.media and isinstance(msg.media, MessageMediaDocument):
            for attr in msg.media.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filenames.append(attr.file_name)

    return " ".join(text_parts + filenames)


async def process_and_forward(messages, source_id):
    dest_id = main_channels.get(source_id)
    if not dest_id:
        return

    # Основной пересыл
    try:
        # await client.forward_messages(dest_id, messages)
        await tg_client.send_message(dest_id, messages[0].text)
        logging.info(f"📦 Основной пересыл: {source_id} → {dest_id}")
    except Exception as e:
        logging.error(f"❌ Ошибка в основной пересылке ({dest_id}): {e}")

    if source_id == -1001623943359:  # если пришло из канала Оперштаб
        word1 = "ракет"
        word2 = "белгород"
        # channel_id = -1004331778776  rt1
        channel_id = -1002337947186  # Канал Пробка в Северном

        pattern1 = rf'\b{re.escape(word1)}[а-яё]*\b'
        pattern2 = rf'\b{re.escape(word2)}[а-яё]*\b'

        has_both = (
            bool(re.search(pattern1, messages[0].text, re.IGNORECASE)) and
            bool(re.search(pattern2, messages[0].text, re.IGNORECASE)) and
            len(messages[0].text) < 300)
        if has_both:
            try:
                await tg_client.send_message(
                    channel_id, f'❗❗❗\n {messages[0].text}'
                    )
                logging.info(
                    f"🔑 Найден ключ {word1} и {word2} → в {channel_id}"
                    )
            except Exception as e:
                logging.error(
                    f"❌ Ошибка пересылки в ключевой канал ({channel_id}): {e}")
        else:
            print("Не все слова найдены")

    # elif source_id == -1002615649192:  # если пришло из МОй белгород №1
    elif source_id == -1002820362754:  # если пришло из канала БПЛА Белгород
        keyword_channels = {
            (
                "автовокзал", "аэропорт", "мегагрин", "Хмельницког",
                "северн", "Гриневк", "строител", "шопин", "Чичерина",
                "Ротонда"
            ): -1002337947186  # Канал Пробка в Северном
            # ): -1004331778776,  # rt1

        }
        # добавил слова исключения
        excludes = ["майский",]
        compiled_keywords = [
            (
                # re.compile(rf'\b({"|".join(words)})\b', flags=re.IGNORECASE),
                re.compile(
                    rf'^(?!.*\b(?:{"|".join(excludes)})\b).*(?:{"|".join(words)}).*',
                    flags=re.IGNORECASE),
                channel
            ) for words, channel in keyword_channels.items()
        ]
        for regex, channel_id in compiled_keywords:
            if regex.search(messages[0].text):
                try:
                    await tg_client.send_message(
                        channel_id,
                        f'<b>❗❗❗БПЛА ОПАСНОСТЬ</b>\n{messages[0].text}',
                        parse_mode='html'
                        )
                    # await client.forward_messages(channel_id, messages)
                    logging.info(
                        f"🔑 Найден ключ {regex.pattern} → в {channel_id}"
                        )

                except Exception as e:
                    logging.error(
                        f"❌ Ошибка пересылки в \
                        ключевой канал ({channel_id}): {e}"
                        )

    await asyncio.sleep(random.uniform(3, 5))


async def flush_album(grouped_id, source_id):
    messages = group_buffer.pop(grouped_id, [])
    group_timers.pop(grouped_id, None)
    if messages:
        await process_and_forward(messages, source_id)


@tg_client.on(events.NewMessage(chats=list(main_channels.keys())))
async def message_handler(event):
    msg = event.message
    grouped_id = getattr(msg, 'grouped_id', None)
    source_id = event.chat_id

    if grouped_id:
        group_buffer[grouped_id].append(msg)

        # Обновляем/создаём таймер на 1.5 сек —
        # после прихода последнего сообщения в альбоме
        if group_timers.get(grouped_id):
            group_timers[grouped_id].cancel()

        group_timers[grouped_id] = asyncio.get_event_loop().call_later(
            1.5, lambda: asyncio.create_task(
                flush_album(grouped_id, source_id))
        )
    else:
        await process_and_forward([msg], source_id)


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
