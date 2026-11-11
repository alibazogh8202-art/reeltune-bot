import os
import telebot
import yt_dlp
from flask import Flask, request

TOKEN = os.environ.get("TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

WELCOME = """سلام! به ReelTune خوش اومدی 🎵
آهنگی که شنیدی، همین‌جا بگیر

اسم آهنگ رو بفرست، یا لینک ریلز اینستاگرام رو بفرست."""

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, WELCOME)

def download_audio(query):
    opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'reeltune.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=True)
        if 'entries' in info:
            info = info['entries'][0]
        filename = ydl.prepare_filename(info)
        title = info.get('title', 'ReelTune')
        return filename, title

@bot.message_handler(func=lambda m: True)
def handle(message):
    text = message.text.strip()
    bot.reply_to(message, "دارم پیداش می‌کنم... 🎧")
    try:
        if "instagram.com" in text:
            query = text
        else:
            query = "ytsearch1:" + text
        filename, title = download_audio(query)
        with open(filename, 'rb') as f:
            bot.send_audio(message.chat.id, f, title=title, caption="🎵 ReelTune")
        os.remove(filename)
    except Exception:
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
