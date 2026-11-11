import os
import glob
import asyncio
import subprocess
import threading
import uuid
import telebot
from telebot import types
import yt_dlp
from flask import Flask, request

TOKEN = os.environ.get("TOKEN")
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# One download at a time per chat, so tapping two versions quickly
# cannot mix their files up.
_CHAT_LOCKS = {}
_CHAT_LOCKS_GUARD = threading.Lock()


def chat_lock(chat_id):
    with _CHAT_LOCKS_GUARD:
        return _CHAT_LOCKS.setdefault(chat_id, threading.Lock())

WELCOME = """سلام! به ReelTune خوش اومدی 🎵
آهنگی که شنیدی، همین‌جا بگیر

اسم آهنگ، بخشی از متنش، یا لینک ریلز اینستاگرام رو بفرست.
اگه چند نسخه بود (اصلی، ریمیکس، لایو...)، لیستش رو نشون می‌دم و خودت انتخاب می‌کنی.
برای ریلز، آهنگ کاملش رو پیدا می‌کنم و می‌فرستم."""

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# chat_id -> list of {title, url, source}
RESULTS = {}


def normalize(text):
    return text.translate(PERSIAN_DIGITS).strip()


def search_source(query, limit=5):
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

    add(search_source('ytsearch5:' + base, limit=5))
    if len(base.split()) >= 3:
        add(search_source('ytsearch5:' + base + ' lyrics', limit=3))
    add(search_source('scsearch3:' + base, limit=3))
    return results[:8]


def show_results(chat_id, results, header):
    RESULTS[chat_id] = results
    lines = [header, ""]
    markup = types.InlineKeyboardMarkup()
    for i, item in enumerate(results):
        lines.append(f"{i + 1}. {item['title']} ({item['source']})")
        label = item['title'][:30]
        markup.add(types.InlineKeyboardButton(f"{i + 1}. {label}", callback_data=f"dl:{i}"))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=markup)


def _own_files(uid):
    return glob.glob(f'reeltune-{uid}.*')


def _cleanup(uid):
    for old in _own_files(uid):
        try:
            os.remove(old)
        except OSError:
            pass


def download_audio(query, title_hint=None):
    # A unique file per download: the old shared 'reeltune.*' name meant
    # a second tap could overwrite the file the first tap was about to send,
    # so the user got the previous version instead of the one they tapped.
    uid = uuid.uuid4().hex[:8]
    last_error = None
    for client in (['android', 'web'], ['ios', 'web'], ['web']):
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'reeltune-{uid}.%(ext)s',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {'youtube': {'player_client': client}},
        }
        _cleanup(uid)
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
                return filename, title, False
        except Exception as e:
            print('ReelTune download failed', client, 'for', query, ':', repr(e), flush=True)
            last_error = e
            # yt-dlp sometimes downloads the whole file and then fails on
            # a later step; if a usable file exists, use it instead of
            # throwing it away.
            files = [f for f in _own_files(uid) if os.path.getsize(f) > 50000]
            if files:
                print('ReelTune using downloaded file despite error:', files[0], flush=True)
                return files[0], title_hint or 'ReelTune', False
    # YouTube blocked the download from this server's IP. Try the same
    # song on SoundCloud, which does not do YouTube's bot check.
    if title_hint and ('youtube.com' in query or 'youtu.be' in query):
        clean = title_hint.split('|')[0].split('(')[0].strip()
        print('ReelTune trying SoundCloud fallback for', clean, flush=True)
        sc = search_source('scsearch3:' + clean, limit=3)
        if not sc:
            print('ReelTune SoundCloud fallback: no results for', clean, flush=True)
        else:
            # Pick the closest title match, so "Sare To 2" does not
            # fall back to "Sare To" (version 1).
            # Score only the song name, not the artist: "Shayan Yo - Sare To"
            # must not tie with "Sare To 2". Prefer an exact title match and
            # penalise extra words in the candidate title.
            song = clean.split(' - ')[-1].strip()
            song_words = set(song.lower().split())

            def score(item):
                item_words = set(item['title'].lower().split())
                return len(song_words & item_words) - len(item_words - song_words)
            sc.sort(key=score, reverse=True)
            chosen = sc[0]
            print('ReelTune SoundCloud fallback chose', chosen, flush=True)
            try:
                opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': f'reeltune-{uid}.%(ext)s',
                    'noplaylist': True,
                    'quiet': True,
                    'no_warnings': True,
                }
                _cleanup(uid)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(chosen['url'], download=True)
                    filename = ydl.prepare_filename(info)
                    title = info.get('title', clean)
                    return filename, title, True
            except Exception as e:
                print('ReelTune SoundCloud fallback failed:', repr(e), flush=True)
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


def reel_song_name(url):
    """Try Instagram's own music credit for a reel. Returns 'Artist - Track' or None."""
    opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print('ReelTune reel info failed:', repr(e), flush=True)
        return None
    track = info.get('track')
    artist = info.get('artist')
    if track:
        return f"{artist} - {track}" if artist else str(track)
    return None


def recognize_song(filename):
    """Listen to an audio file with Shazam and return 'Artist - Title' or None."""
    try:
        from shazamio import Shazam
    except Exception as e:
        print('ReelTune shazam import failed:', repr(e), flush=True)
        return None
    # Shazam cannot read Instagram's AAC files directly
    # ("aac too complex"), so convert to plain WAV first with ffmpeg.
    wav = f'reeltune-shazam-{uuid.uuid4().hex[:8]}.wav'
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-v', 'error', '-i', filename,
             '-ar', '44100', '-ac', '1', wav],
            timeout=30, check=True)
        filename = wav
    except Exception as e:
        print('ReelTune ffmpeg convert for shazam failed:', repr(e), flush=True)

    async def _run():
        shazam = Shazam()
        return await shazam.recognize(filename)

    try:
        out = asyncio.run(_run())
    except Exception as e:
        print('ReelTune shazam failed:', repr(e), flush=True)
        return None
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    track = (out or {}).get('track') or {}
    title = track.get('title')
    artist = track.get('subtitle')
    if not title:
        return None
    return f"{artist} - {title}" if artist else str(title)


def handle_reel(chat_id, url):
    bot.send_message(chat_id, "دارم آهنگ ریلز رو پیدا می‌کنم... 🎧")
    # 1) Instagram's own music credit, when the reel has one.
    song = reel_song_name(url)
    if song:
        bot.send_message(chat_id, f"آهنگ ریلز: {song}\nحالا کاملش رو پیدا می‌کنم... 🔍")
        results = search_all(song)
        if results:
            show_results(chat_id, results, f"🎵 کامل «{song}» رو پیدا کردم، یکی رو انتخاب کن:")
            return
    # 2) Listen to the reel audio and recognise it (Shazam).
    try:
        filename, _title, _via_sc = download_audio(url)
    except Exception as e:
        print('ReelTune reel download failed:', repr(e), flush=True)
        bot.send_message(chat_id, "نتونستم ریلز رو دانلود کنم. لینک درست رو دوباره بفرست.")
        return
    song = recognize_song(filename)
    if song:
        try:
            os.remove(filename)
        except OSError:
            pass
        bot.send_message(chat_id, f"آهنگ ریلز: {song}\nحالا کاملش رو پیدا می‌کنم... 🔍")
        results = search_all(song)
        if results:
            show_results(chat_id, results, f"🎵 کامل «{song}» رو پیدا کردم، یکی رو انتخاب کن:")
            return
        bot.send_message(chat_id, "اسم آهنگ رو پیدا کردم ولی کاملش رو نتونستم دانلود کنم. دوباره امتحان کن.")
        return
    # 3) Fallback: we could not name the song, send the reel audio itself.
    bot.send_message(chat_id, "اسم آهنگ رو پیدا نکردم، صداش رو از خود ریلز می‌فرستم. اگه اسمش رو بفرستی، کاملش رو پیدا می‌کنم.")
    try:
        send_song(chat_id, filename, "ReelTune")
        os.remove(filename)
    except Exception as e:
        print('ReelTune reel fallback send failed:', repr(e), flush=True)
        bot.send_message(chat_id, "نتونستم ریلز رو بفرستم. لینک درست رو دوباره بفرست.")


@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, WELCOME)


@bot.message_handler(func=lambda m: True)
def handle(message):
    text = (message.text or '').strip()
    if not text:
        return
    if "instagram.com" in text or "instagr.am" in text:
        handle_reel(message.chat.id, text)
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
    show_results(message.chat.id, results, "🎵 این‌ها رو پیدا کردم، یکی رو انتخاب کن:")


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
    with chat_lock(call.message.chat.id):
        try:
            filename, title, via_sc = download_audio(item['url'], title_hint=item['title'])
            if via_sc:
                bot.send_message(call.message.chat.id, "یوتیوب دانلود رو بلاک کرد، نسخه ساندکلاد رو می‌فرستم 🎧")
            send_song(call.message.chat.id, filename, title)
            os.remove(filename)
        except Exception as e:
            print('ReelTune callback download failed for', item, ':', repr(e), flush=True)
            bot.send_message(call.message.chat.id, "نتونستم این نسخه رو دانلود کنم، یوتیوب بلاکش کرد و ساندکلاد هم پیداش نکرد. یه نسخه دیگه رو امتحان کن.")


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
