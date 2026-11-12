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
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

from maxapi import Bot as MaxBot
from maxapi.enums.format import Format
from maxapi.types import InputMedia

from dotenv import load_dotenv

load_dotenv()

MAX_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = int(os.getenv("MAX_CHANNEL_ID", 0))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = int(os.getenv("TG_CHANNEL_ID", 0))

# ── Настройки ─────────────────────────────────────────────
TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")

MAX_TEXT_LIMIT = 3900
TMP_DIR = "tmp_media"

# Жёсткий лимит MAX на вложение — отсекаем с запасом 5 МБ,
# чтобы файлы 95–100 МБ не улетали в MAX и не падали там с ошибкой
MAX_ATTACHMENT_LIMIT_MB = 95
DOWNLOAD_SAFE_LIMIT_MB = MAX_ATTACHMENT_LIMIT_MB - 5

# Сколько вложений разрешено прикреплять к одному сообщению MAX.
# Если MAX ответит ошибкой — уменьшите это число.
MAX_ATTACHMENTS_PER_MESSAGE = 10

# Окно ожидания, за которое должны прийти все элементы альбома
ALBUM_WAIT_SECONDS = 2.0

logging.basicConfig(
    # filename='app.log',
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s",
)
log = logging.getLogger("tg2max")

tg_bot = Bot(TG_TOKEN)
dp = Dispatcher()

mx = None
tg_client = TelegramClient("session", TG_API_ID, TG_API_HASH)
Path(TMP_DIR).mkdir(exist_ok=True)


def chunk(text: str) -> list[str]:
    """Нарезка текста без разрыва HTML-тегов и по границам слов/строк."""
    if not text:
        return ["(без текста)"]

    parts = []
    while len(text) > MAX_TEXT_LIMIT:
        piece = text[:MAX_TEXT_LIMIT]

        # не рвём посреди открывающего тега: если "<" не закрыт — режем до него
        last_open = piece.rfind("<")
        if last_open != -1 and ">" not in piece[last_open:]:
            piece = piece[:last_open]

        # режем по границе строки/пробела, если она не слишком далеко от лимита
        cut = max(piece.rfind("\n"), piece.rfind(" "))
        if cut > MAX_TEXT_LIMIT * 0.7:
            piece = piece[:cut]

        parts.append(piece)
        text = text[len(piece):].lstrip()

    parts.append(text)
    parts = [p for p in parts if p.strip()]
    return parts or ["(без текста)"]


def _normalize_media_paths(media_paths) -> list[str]:
    if not media_paths:
        return []
    if isinstance(media_paths, str):
        return [media_paths]
    return list(media_paths)


async def send_to_max(text: str, media_paths: str | list[str] | None = None):
    global mx

    if mx is None:
        log.error("[SEND ERROR] MaxBot ещё не инициализирован!")
        return

    media_paths = _normalize_media_paths(media_paths)
    parts = chunk(text)
    for i, part in enumerate(parts):
        attachments = None
        # вложения отправляем только с первой частью текста
        if media_paths and i == 0:
            valid = []
            for p in media_paths:
                if not os.path.exists(p):
                    continue
                size_mb = os.path.getsize(p) / (1024 * 1024)
                if size_mb > MAX_ATTACHMENT_LIMIT_MB:
                    log.warning(
                        f"[SKIP ATTACH] Файл ({size_mb:.1f} МБ) превышает лимит "
                        f"MAX ({MAX_ATTACHMENT_LIMIT_MB} МБ). Пропускаем."
                    )
                else:
                    valid.append(p)
            if valid:
                attachments = [InputMedia(p) for p in valid[:MAX_ATTACHMENTS_PER_MESSAGE]]
                if len(valid) > MAX_ATTACHMENTS_PER_MESSAGE:
                    log.warning(
                        f"[SKIP ATTACH] {len(valid) - MAX_ATTACHMENTS_PER_MESSAGE} "
                        "вложений не влезли в лимит на сообщение MAX."
                    )

        try:
            await mx.send_message(
                chat_id=MAX_CHAT_ID,
                text=part,
                format=Format.HTML,
                attachments=attachments,
            )
            log.info(f"[SEND] Часть {i + 1}/{len(parts)} отправлена в MAX.")
        except Exception as e:
            log.error(f"[SEND ERROR] Ошибка отправки в MAX: {e}")
            # аварийный ретрай: та же часть без разметки
            try:
                plain = re.sub(r"<[^>]+>", "", part)
                await mx.send_message(chat_id=MAX_CHAT_ID, text=plain)
                log.info("[SEND] Ретрай без HTML прошёл.")
            except Exception as e2:
                log.error(f"[SEND ERROR] Ретрай тоже не прошёл: {e2}")

        if i < len(parts) - 1:
            await asyncio.sleep(0.6)


def caption_html(message: Message) -> str:
    caption = message.caption or ""
    entities = message.caption_entities or []
    return html_decoration.unparse(caption, entities=entities)


def remove_file_safe(path: str | None):
    try:
        if path and os.path.exists(path):
            os.remove(path)
            log.debug(f"Файл удалён: {path}")
    except Exception as e:
        log.error(f"Ошибка удаления файла {path}: {e}")


def album_text(messages) -> str:
    """Текст альбома: raw_text покрывает и текст, и подписи медиа."""
    return "\n".join(m.raw_text for m in messages if m.raw_text)


# ── Скачивание через Telethon ──────────────────────────────
async def download_small_media_via_telethon(aiogram_message: Message):
    msg_id = aiogram_message.message_id
    path = None  # инициализация сразу, чтобы except не падал на NameError
    log.info(f"[DOWNLOAD] Проверка медиа для сообщения id={msg_id}")

    try:
        chat_entity = await tg_client.get_entity(TG_CHAT_ID)
        telethon_msgs = await tg_client.get_messages(chat_entity, ids=[msg_id])

        if not telethon_msgs or len(telethon_msgs) == 0:
            return None

        t_msg = telethon_msgs[0]
        media = None
        ext = ""

        if t_msg.photo:
            media = t_msg.photo
            ext = ".jpg"
            # размер фото заранее неизвестен — проверяем после скачивания
        elif t_msg.video:
            video = t_msg.video
            size_mb = video.size / (1024 * 1024)
            log.info(f"[SIZE] Размер видео: {size_mb:.2f} МБ")
            if size_mb > DOWNLOAD_SAFE_LIMIT_MB:
                log.warning(
                    f"[SKIP] Видео слишком большое ({size_mb:.1f} МБ > "
                    f"{DOWNLOAD_SAFE_LIMIT_MB} МБ). Скачивание отменено."
                )
                return None
            media = video
            ext = ".mp4"
        else:
            return None

        # msg_id в имени файла — чтобы пересланные фото не перезаписывали друг друга
        path = os.path.join(TMP_DIR, f"{msg_id}_{media.id}{ext}")
        log.info(f"[DOWNLOAD] Скачивание медиа в {path}...")
        await tg_client.download_media(media, file=path)

        if path and os.path.exists(path):
            final_size = os.path.getsize(path) / (1024 * 1024)
            log.info(f"[OK] Файл скачан: {final_size:.2f} МБ.")

            if final_size > DOWNLOAD_SAFE_LIMIT_MB:
                log.warning(
                    f"[SKIP] Файл превышает лимит ({final_size:.1f} МБ > "
                    f"{DOWNLOAD_SAFE_LIMIT_MB} МБ). Файл удалён."
                )
                os.remove(path)
                return None

            if final_size == 0:
                log.warning("[SKIP] Файл скачался пустым.")
                os.remove(path)
                return None

            return path

        log.warning("[SKIP] Файл не скачался.")
        return None

    except Exception as e:
        log.exception(f"[ERROR] Ошибка при скачивании медиа: {e}")
        remove_file_safe(path)
        return None


# ── Медиа-группы (альбомы) Telegram → MAX ──────────────────
tg_album_buffer: dict[str, list[Message]] = defaultdict(list)
tg_album_timers: dict[str, object] = {}


async def flush_tg_album(media_group_id: str):
    messages = tg_album_buffer.pop(media_group_id, [])
    tg_album_timers.pop(media_group_id, None)
    if not messages:
        return

    messages.sort(key=lambda m: m.message_id)
    paths: list[str] = []

    try:
        # скачиваем все медиа альбома (фото/видео)
        for msg in messages:
            if msg.photo or msg.video:
                p = await download_small_media_via_telethon(msg)
                if p:
                    paths.append(p)

        # подпись в альбоме может стоять на любом элементе — собираем всё
        captions = [caption_html(m) for m in messages if m.caption]
        text = "\n".join(c for c in captions if c) or "(медиа без подписи)"

        if not paths:
            log.info("[ALBUM] В альбоме нет скачанных медиа — отправляем только текст.")
            await send_to_max(text)
            return

        log.info(f"[ALBUM] Отправка альбома в MAX: {len(paths)} вложений.")
        await send_to_max(text, media_paths=paths)
    finally:
        for p in paths:
            remove_file_safe(p)


@dp.channel_post(F.chat.id == TG_CHAT_ID)
async def on_channel_post(message: Message):
    """TG_CHAT_ID — канал, поэтому обновления приходят только сюда."""
    media_group_id = message.media_group_id

    # элемент альбома: буферизуем и ждём остальные
    if media_group_id:
        tg_album_buffer[media_group_id].append(message)
        log.info(
            f"[ALBUM] Сообщение id={message.message_id} добавлено "
            f"в группу {media_group_id} "
            f"(всего {len(tg_album_buffer[media_group_id])})."
        )

        if tg_album_timers.get(media_group_id):
            tg_album_timers[media_group_id].cancel()

        loop = asyncio.get_running_loop()
        tg_album_timers[media_group_id] = loop.call_later(
            ALBUM_WAIT_SECONDS,
            lambda: asyncio.create_task(flush_tg_album(media_group_id)),
        )
        return

    # одиночное сообщение — как раньше
    path = None
    try:
        if message.photo or message.video:
            path = await download_small_media_via_telethon(message)
            await send_to_max(caption_html(message) or "(медиа без подписи)",
                              media_paths=path)
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
        "Если видео не уходит — запускайте командой: "
        "set AIOHTTP_NO_SSL=1 && python super3.py"
    )


# ── Пересылка сообщений между каналами ─────────────────────
main_channels = {
    -1001623943359: -1004255311300,  # ОперШтаб Белгород второй rt
    -1002615649192: -1004255311300,  # МОй белгород №1 второй rt
    -1002820362754: -1004255311300,  # Информация БПЛА Белгород
}

group_buffer = defaultdict(list)
group_timers = {}


def extract_text_and_filenames(messages):
    text_parts = []
    filenames = []

    for msg in messages:
        if msg.raw_text:
            text_parts.append(msg.raw_text)

        if msg.media and isinstance(msg.media, MessageMediaDocument):
            for attr in msg.media.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filenames.append(attr.file_name)

    return " ".join(text_parts + filenames)


async def process_and_forward(messages, source_id):
    dest_id = main_channels.get(source_id)
    if not dest_id:
        return

    text = album_text(messages)
    if not text:
        log.warning(f"[SKIP] Пустой текст от {source_id}")

    # Основной пересыл: медиа сохраняем, текст и подпись переносим
    try:
        if getattr(messages[0], "media", None):
            await tg_client.send_file(
                dest_id,
                messages if len(messages) > 1 else messages[0],
                caption=text,
            )
        else:
            await tg_client.send_message(dest_id, text)
        logging.info(f"📦 Основной пересыл: {source_id} → {dest_id}")
    except Exception as e:
        logging.error(f"❌ Ошибка в основной пересылке ({dest_id}): {e}")

    if source_id == -1001623943359:  # Оперштаб Белгород
        word1, word2 = "ракет", "белгород"
        channel_id = -1002337947186  # Канал Пробка в Северном

        pattern1 = rf"\b{re.escape(word1)}[а-яё]*\b"
        pattern2 = rf"\b{re.escape(word2)}[а-яё]*\b"

        has_both = (
            bool(re.search(pattern1, text, re.IGNORECASE))
            and bool(re.search(pattern2, text, re.IGNORECASE))
            and len(text) < 300
        )
        if has_both:
            try:
                await tg_client.send_message(channel_id, f"❗❗❗\n{text}")
                logging.info(f"🔑 Найден ключ {word1} и {word2} → в {channel_id}")
            except Exception as e:
                logging.error(f"❌ Ошибка пересылки в ключевой канал ({channel_id}): {e}")
        else:
            logging.debug("Не все слова найдены")

    elif source_id == -1002820362754:  # Информация БПЛА Белгород
        keyword_channels = {
            (
                "автовокзал", "аэропорт", "мегагрин", "Хмельницког",
                "северн", "Гриневк", "строител", "шопин", "Чичерина",
                "Ротонда",
            ): -1002337947186  # Канал Пробка в Северном
        }
        excludes = ["майский"]
        compiled_keywords = [
            (
                re.compile(
                    rf'^(?!.*\b(?:{"|".join(excludes)})\b).*(?:{"|".join(words)}).*',
                    flags=re.IGNORECASE,
                ),
                channel,
            )
            for words, channel in keyword_channels.items()
        ]
        for regex, channel_id in compiled_keywords:
            if regex.search(text):
                try:
                    await tg_client.send_message(
                        channel_id,
                        f"<b>❗❗❗БПЛА ОПАСНОСТЬ</b>\n{text}",
                        parse_mode="html",
                    )
                    logging.info(f"🔑 Найден ключ {regex.pattern} → в {channel_id}")
                except Exception as e:
                    logging.error(f"❌ Ошибка пересылки в ключевой канал ({channel_id}): {e}")

    await asyncio.sleep(random.uniform(3, 5))


async def flush_album(grouped_id, source_id):
    messages = group_buffer.pop(grouped_id, [])
    group_timers.pop(grouped_id, None)
    if messages:
        messages.sort(key=lambda m: m.id)
        await process_and_forward(messages, source_id)


@tg_client.on(events.NewMessage(chats=list(main_channels.keys())))
async def message_handler(event):
    msg = event.message
    grouped_id = getattr(msg, "grouped_id", None)
    source_id = event.chat_id

    if grouped_id:
        group_buffer[grouped_id].append(msg)

        # обновляем таймер: после прихода последнего сообщения в альбоме
        if group_timers.get(grouped_id):
            group_timers[grouped_id].cancel()

        loop = asyncio.get_running_loop()
        group_timers[grouped_id] = loop.call_later(
            ALBUM_WAIT_SECONDS,
            lambda: asyncio.create_task(flush_album(grouped_id, source_id)),
        )
    else:
        await process_and_forward([msg], source_id)


async def main():
    global mx
    log.info("Инициализация MaxBot...")
    # Никаких session, verify_ssl и т.п. здесь быть не должно
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
