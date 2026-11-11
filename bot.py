import os
import glob
import telebot
from telebot import types
import yt_dlp
from flask import Flask, request

TOKEN = os.environ.get("TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

WELCOME = """سلام! به ReelTune خوش اومدی 🎵
آهنگی که شنیدی، همین‌جا بگیر

اسم آهنگ، بخشی از متنش، یا لینک ریلز اینستاگرام رو بفرست.
اگه چند نسخه بود (اصلی، ریمیکس، لایو...)، لیستش رو نشون می‌دم و خودت انتخاب می‌کنی."""

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# chat_id -> list of {title, url, source}
RESULTS = {}


def normalize(text):
    return text.translate(PERSIAN_DIGITS).strip()


def search_source(query, limit=5):
    """Search one yt-dlp source, return [{title, url, source}] or []."""
    opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as e:
        print('ReelTune search failed for', query, ':', repr(e), flush=True)
        return []
    entries = info.get('entries') or []
    out = []
    for e in entries[:limit]:
        if not e:
            continue
        title = e.get('title') or 'بدون عنوان'
        url = e.get('webpage_url') or e.get('url') or ''
        if not url:
            continue
        source = 'YouTube'
        if 'soundcloud' in url:
            source = 'SoundCloud'
        out.append({'title': title, 'url': url, 'source': source})
    return out


def search_all(text):
    """Search like Google: name or a piece of lyrics, across sources."""
    base = normalize(text)
    results = []
    seen = set()

    def add(items):
        for item in items:
            key = item['title'].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(item)

    # Main search: YouTube finds songs by name and by lyrics snippets,
    # because titles and descriptions often contain the lyrics.
    add(search_source('ytsearch5:' + base, limit=5))
    # If the text looks like lyrics (a longer phrase), try it with "lyrics" too.
    if len(base.split()) >= 3:
        add(search_source('ytsearch5:' + base + ' lyrics', limit=3))
    # SoundCloud often has remixes and versions YouTube does not.
    add(search_source('scsearch3:' + base, limit=3))
    return results[:8]


def download_audio(query):
    last_error = None
    for client in (['android', 'web'], ['ios', 'web'], ['web']):
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'reeltune.%(ext)s',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {'youtube': {'player_client': client}},
        }
        for old in glob.glob('reeltune.*'):
            try:
                os.remove(old)
            except OSError:
                pass
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=True)
                if 'entries' in info:
                    entries = [e for e in info['entries'] if e]
                    if not entries:
                        raise ValueError('no search results')
                    info = entries[0]
                filename = ydl.prepare_filename(info)
                title = info.get('title', 'ReelTune')
                return filename, title
        except Exception as e:
            print('ReelTune download failed', client, 'for', query, ':', repr(e), flush=True)
            last_error = e
    raise last_error


def send_song(chat_id, filename, title):
    try:
        with open(filename, 'rb') as f:
            bot.send_audio(chat_id, f, title=title, caption="🎵 ReelTune")
        return
    except Exception as e:
        print('ReelTune send_audio failed:', repr(e), flush=True)
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption="🎵 " + title)


@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, WELCOME)


@bot.message_handler(func=lambda m: True)
def handle(message):
    text = (message.text or '').strip()
    if not text:
        return
    if "instagram.com" in text or "instagr.am" in text:
        bot.reply_to(message, "دارم پیداش می‌کنم... 🎧")
        try:
            filename, title = download_audio(text)
            send_song(message.chat.id, filename, title)
            os.remove(filename)
        except Exception as e:
            print('ReelTune handle failed for', repr(text), ':', repr(e), flush=True)
            bot.reply_to(message, "نتونستم پیداش کنم. لینک درست رو دوباره بفرست.")
        return

    bot.reply_to(message, "دارم می‌گردم... 🔍")
    try:
        results = search_all(text)
    except Exception as e:
        print('ReelTune search_all failed:', repr(e), flush=True)
        results = []
    if not results:
        bot.reply_to(message, "نتونستم پیداش کنم. اسم دقیق‌تر، بخشی از متن آهنگ، یا لینک درست رو دوباره بفرست.")
        return

    RESULTS[message.chat.id] = results
    lines = ["🎵 این‌ها رو پیدا کردم، یکی رو انتخاب کن:\n"]
    markup = types.InlineKeyboardMarkup()
    for i, item in enumerate(results):
        lines.append(f"{i + 1}. {item['title']} ({item['source']})")
        label = item['title'][:30]
        markup.add(types.InlineKeyboardButton(f"{i + 1}. {label}", callback_data=f"dl:{i}"))
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith('dl:'))
def on_download(call):
    try:
        idx = int(call.data.split(':')[1])
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "انتخاب نامعتبر است.")
        return
    results = RESULTS.get(call.message.chat.id, [])
    if idx < 0 or idx >= len(results):
        bot.answer_callback_query(call.id, "این مورد دیگه در دسترس نیست، دوباره جستجو کن.")
        return
    item = results[idx]
    bot.answer_callback_query(call.id, "دارم دانلود می‌کنم... 🎧")
    bot.send_message(call.message.chat.id, f"دارم «{item['title']}» رو می‌فرستم... 🎧")
    try:
        filename, title = download_audio(item['url'])
        send_song(call.message.chat.id, filename, title)
        os.remove(filename)
    except Exception as e:
        print('ReelTune callback download failed for', item, ':', repr(e), flush=True)
        bot.send_message(call.message.chat.id, "نتونستم این نسخه رو دانلود کنم. یه نسخه دیگه رو امتحان کن.")


@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200


@app.route('/')
def index():
    return 'ReelTune is running', 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
