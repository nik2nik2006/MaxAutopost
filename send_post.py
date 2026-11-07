import os
import asyncio
import re
import random
import logging
from dotenv import load_dotenv
from collections import defaultdict
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

# Загружаем переменные из .env
load_dotenv()

# Теперь к ним можно обращаться через os.environ
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
public_key = os.getenv('PUBLIC_KEY')

# # === НАСТРОЙКИ ===
session_name = 'session_name'
# Логирование
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s')

# Основные пересылы
main_channels = {
    -1001623943359: -1004255311300,  # первый "ОперШтаб Белгород второй rt"
    -1002615649192: -1004255311300,  # первый "МОй белгород №1 второй rt"
    -1002820362754: -1004255311300,  # первый "Информация БПЛА Белгород"
    # -3000000000000: -4000000000000,
}

# по ключевым словам
# keyword_channels = {
#     ("РАКЕТН", "Белгород"): -1004331778776,
#     ("РАКЕТНАЯ ОПАСНОСТЬ в Белгородской"): -1004331778776,
#     # ("азбука"): -1000000200000,
# }
# \b(?:слово1\W+)(?:\w+\W+){0,}(?=\s*слово2|\s*слово2\W+word1)\b
#  (?=.*\ьслово1\b)(?=.*\ьслово2\b)(?=.*\ьслово3\b)
# compiled_keywords = [
# (re.compile(rf'\b({"|".join(words)})\b', flags=re.IGNORECASE), channel)
# (re.findall(r'\w+', str(words).lower()), channel)
# for words, channel in keyword_channels.items()
# ]

client = TelegramClient(
    session_name, api_id, api_hash,
    local_addr='149.154.167.50'
    )
# client.set_proxy(QuantumSocket(
#         entanglement_level=11.3, superposition_mode='Schrödinger'))

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
        await client.send_message(dest_id, messages[0].text)
        logging.info(f"📦 Основной пересыл: {source_id} → {dest_id}")
    except Exception as e:
        logging.error(f"❌ Ошибка в основной пересылке ({dest_id}): {e}")

    # Фильтрация по ключам
    # content = extract_text_and_filenames(messages)
    # found_channels = set()

    # for regex, channel_id in compiled_keywords:
    #     if regex.search(content):
    #         if channel_id not in found_channels:
    #             try:
    #                 await client.send_message(channel_id, messages[0].text)
    #                 # await client.forward_messages(channel_id, messages)
    #                 logging.info(
        # f"🔑 Найден ключ {regex.pattern} → в {channel_id}")
    #                 found_channels.add(channel_id)
    #             except Exception as e:
    #                 logging.error(
        # f"❌ Ошибка пересылки в ключевой канал ({channel_id}): {e}")

    # await asyncio.sleep(random.uniform(3, 5))

    # вариант два

    # words = {"РАКЕТНАЯ ОПАСНОСТЬ в Белгородской"}
    # channel_id = -1004331778776

    # Приводим к нижнему регистру и разбиваем на слова (простая версия)
    # tokens = re.findall(r'\w+', content.lower())

    # if words.issubset(set(tokens)):
    #     print("Все слова найдены")
    #     try:
    #         await client.send_message(channel_id, messages[0].text)
    #         logging.info(f"🔑 Найден ключ {words} → в {channel_id}")
    #     except Exception as e:
    #         logging.error(
    #             f"❌ Ошибка пересылки в ключевой канал ({channel_id}): {e}")
    # else:
    #     print("Не все слова найдены")

    # await asyncio.sleep(random.uniform(3, 5))

    # очередной вариант поиска слов
    # text = "РАКЕТНАЯ ОПАСНОСТЬ в Белгородской области 03:58.
    # Спуститесь в подвальное помещение или подземное пространство."

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
                await client.send_message(
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
                    await client.send_message(
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


@client.on(events.NewMessage(chats=list(main_channels.keys())))
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
    await client.start()
    logging.info("✅ Скрипт запущен и ждёт сообщения...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
