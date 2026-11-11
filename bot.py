import os
import glob
import telebot
import yt_dlp
from flask import Flask, request

TOKEN = os.environ.get("TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

WELCOME = """سلام! به ReelTune خوش اومدی 🎵
آهنگی که شنیدی، همین‌جا بگیر

اسم آهنگ رو بفرست، یا لینک ریلز اینستاگرام رو بفرست."""

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def normalize(text):
    return text.translate(PERSIAN_DIGITS).strip()


def download_audio(query):
    # YouTube sometimes blocks a datacenter IP ("Sign in to confirm you're
    # not a bot"), so retry the same download with different player clients.
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


def find_and_download(text):
    base = normalize(text)
    queries = [
        'ytsearch5:' + base,
        'ytsearch5:' + base + ' آهنگ',
    ]
    last_error = None
    for query in queries:
        try:
            return download_audio(query)
        except Exception as e:
            last_error = e
    raise last_error


def send_song(chat_id, filename, title):
    # Telegram sometimes rejects a webm audio file as audio; sending it as
    # a document still lets the user play it.
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
    bot.reply_to(message, "دارم پیداش می‌کنم... 🎧")
    try:
        if "instagram.com" in text or "instagr.am" in text:
            filename, title = download_audio(text)
        else:
            filename, title = find_and_download(text)
        send_song(message.chat.id, filename, title)
        os.remove(filename)
    except Exception as e:
        print('ReelTune handle failed for', repr(text), ':', repr(e), flush=True)
        bot.reply_to(message, "نتونستم پیداش کنم. اسم دقیق‌تر یا لینک درست رو دوباره بفرست.")


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
