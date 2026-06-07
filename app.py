from flask import Flask, request, jsonify, render_template, Response
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
from youtube_transcript_api.proxies import WebshareProxyConfig
import yt_dlp
import re
import os
import tempfile
from google import genai

from deepgram import DeepgramClient, PrerecordedOptions, FileSource
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

DEEPGRAM_API_KEY = "6042e3078f93ae237b8fca62f0e61628645adbaa"

# Replace with your actual deployed domain
SITE_URL = "https://transcriptflow.onrender.com"

# ── PROXY CONFIG (WebShare) ──────────────────────────────────
PROXY_USER = os.getenv('PROXY_USER')
PROXY_PASS = os.getenv('PROXY_PASS')

def get_api():
    """Returns YouTubeTranscriptApi instance, with WebShare proxy if configured."""
    if all([PROXY_USER, PROXY_PASS]):
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=PROXY_USER,
                proxy_password=PROXY_PASS,
            )
        )
    return YouTubeTranscriptApi()

try:
    gemini_client = genai.Client()
except Exception:
    gemini_client = None


def extract_video_id(url):
    pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(pattern, url)
    return match.group(1) if match else None


def get_ai_transcription(video_url):
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "audio.mp3")
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'outtmpl': output_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        for f in os.listdir(tmpdir):
            if f.startswith("audio"):
                output_path = os.path.join(tmpdir, f)
                break

        with open(output_path, "rb") as audio_file:
            audio_data = audio_file.read()

        deepgram = DeepgramClient(DEEPGRAM_API_KEY)
        options = PrerecordedOptions(model="nova-2", smart_format=True, punctuate=True)
        payload: FileSource = {"buffer": audio_data}
        response = deepgram.listen.prerecorded.v("1").transcribe_file(payload, options, timeout=300)
        transcript_text = response.results.channels[0].alternatives[0].transcript

        if not transcript_text or transcript_text.strip() == "":
            raise ValueError("Deepgram returned an empty transcript.")
        return transcript_text


# ── PAGE ROUTES ──────────────────────────────────────────────

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')


# ── SEO: SITEMAP & ROBOTS ─────────────────────────────────────

@app.route('/sitemap.xml')
def sitemap():
    pages = [
        ('/', '1.0', 'daily'),
        ('/about', '0.8', 'monthly'),
        ('/privacy', '0.5', 'monthly'),
        ('/terms', '0.5', 'monthly'),
        ('/contact', '0.6', 'monthly'),
    ]
    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, priority, freq in pages:
        xml_lines.append(f"""  <url>
    <loc>{SITE_URL}{path}</loc>
    <changefreq>{freq}</changefreq>
    <priority>{priority}</priority>
  </url>""")
    xml_lines.append('</urlset>')
    return Response('\n'.join(xml_lines), mimetype='application/xml')


@app.route('/robots.txt')
def robots():
    content = f"""User-agent: *
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')

from flask import send_from_directory
@app.route('/google2b114fd69e444081.html')
def google_verification():
    return send_from_directory('static', 'google2b114fd69e444081.html')
# ── API ROUTES ────────────────────────────────────────────────

@app.route('/get-transcript', methods=['POST'])
def get_transcript():
    data = request.get_json()
    video_url = data.get('url', '')
    use_ai = data.get('use_ai', False)

    video_id = extract_video_id(video_url)
    if not video_id:
        return jsonify({'error': 'Invalid YouTube URL. Please use a standard youtube.com or youtu.be link.'}), 400

    if use_ai:
        try:
            ai_text = get_ai_transcription(video_url)
            return jsonify({'mode': 'Premium AI Transcribed', 'language': 'Auto-Detected', 'transcript': ai_text})
        except Exception as e:
            return jsonify({'error': f'AI Transcription failed: {str(e)}'}), 500

    try:
        api = get_api()

        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(['en', 'es', 'hi', 'fr', 'de', 'ja', 'pt', 'ru', 'ko', 'zh'])
        except NoTranscriptFound:
            transcript = next(iter(transcript_list))

        fetched_data = transcript.fetch()
        full_text = " ".join([entry.text for entry in fetched_data])
        return jsonify({'mode': 'Free Caption Download', 'language': transcript.language, 'transcript': full_text})

    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return jsonify({'status': 'no_captions_found', 'message': 'No standard subtitles found for this video.'}), 200

    except Exception as e:
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500


@app.route('/summarize', methods=['POST'])
def summarize():
    data = request.get_json()
    transcript = data.get('transcript', '')
    style = data.get('style', 'brief')

    if not transcript:
        return jsonify({'error': 'No transcript provided.'}), 400
    if len(transcript) > 100000:
        transcript = transcript[:100000] + "... [truncated]"

    style_prompts = {
        'brief': "Write a concise 3-4 sentence summary of this transcript.",
        'detailed': "Write a detailed summary (2-3 paragraphs) covering the main topics, key points, and conclusions.",
        'bullets': "Summarize this transcript as a clean bullet-point list of the 5-8 most important points. Use • as bullet character."
    }
    prompt = style_prompts.get(style, style_prompts['brief'])

    try:
        client = gemini_client or genai.Client()
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"{prompt}\n\nTranscript:\n{transcript}",
        )
        return jsonify({'summary': response.text})
    except Exception as e:
        return jsonify({'error': f'Summarization failed: {str(e)}'}), 500


@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    transcript = data.get('transcript', '')
    title = data.get('title', 'transcript')
    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
    filename = f"{safe_title[:50]}.txt" if safe_title else "transcript.txt"
    return Response(
        transcript,
        mimetype='text/plain',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'text/plain; charset=utf-8'
        }
    )


@app.route('/contact', methods=['POST'])
def contact_submit():
    data = request.get_json()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    message = data.get('message', '').strip()
    if not name or not email or not message:
        return jsonify({'error': 'All fields are required.'}), 400
    return jsonify({'success': True, 'message': "Thanks! We'll get back to you within 48 hours."})


if __name__ == '__main__':
    app.run(port=5000, debug=True)