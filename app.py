# app.py - High-accuracy version using Twilio + OpenAI Whisper + Fuzzy Confirmation Handling

from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI
import json
import os
import logging
import string
import requests
import difflib

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("baguette_app")

# ---------------------------
# Environment variables
# ---------------------------
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM", "whatsapp:+14155238886")
TWILIO_TO = os.getenv("TWILIO_TO", "whatsapp:+447425766000")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "supersecretkey123")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ---------------------------
# Clients
# ---------------------------
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------------------
# Menu
# ---------------------------
menu = {
    "tuna baguette": 4.99,
    "fries": 2.50,
    "large fries": 3.00,
    "coke": 1.20,
    "fanta": 1.20,
    "chicken baguette": 5.99
}

# ---------------------------
# Flask App
# ---------------------------
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# ---------------------------
# In-memory order store
# ---------------------------
orders_store = {}

# ---------------------------
# Helpers
# ---------------------------

def send_whatsapp(text):
    try:
        msg = twilio_client.messages.create(from_=TWILIO_FROM, body=text, to=TWILIO_TO)
        logger.debug(f"WhatsApp sent: {msg.sid}")
    except Exception as e:
        logger.exception(f"Failed to send WhatsApp message: {e}")

def normalize_speech(text: str) -> str:
    text = text.lower()
    text = text.replace("bagette", "baguette")
    text = text.replace("chiken", "chicken")
    text = text.replace("big fries", "large fries")
    text = text.replace("coka", "coke")
    text = text.replace("fantaa", "fanta")
    return text.strip()

def ai_parse_order(speech_text):
    aliases = {
        "tuna baguette": ["tuna baguette", "tuna bagette"],
        "chicken baguette": ["chicken baguette", "chiken baguette"],
        "large fries": ["large fries", "big fries"],
        "fries": ["fries", "small fries"],
        "coke": ["coke", "coca cola", "coka"],
        "fanta": ["fanta", "fantaa"]
    }

    prompt = f"""
You are a restaurant assistant. Extract the order from the customer's message.

MENU ITEMS AND PRICES:
{json.dumps(menu)}

MENU ITEM ALIASES:
{json.dumps(aliases)}

TASK:
Return ONLY valid JSON in this format:
{{
  "items": [
    {{"name": string, "quantity": number}}
  ],
  "total": number
}}

RULES:
- Only include items from the menu.
- Match customer words to the closest menu item using aliases.
- Multiply quantity by menu prices to compute total.
- No explanations. JSON only.

Customer said: "{speech_text}"
"""
    
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        ai_text = completion.choices[0].message.content
        order_data = json.loads(ai_text)
        return order_data
    except Exception as e:
        logger.exception(f"OpenAI error: {e}")
        return {"items": [], "total": 0}

def match_confirmation(text, variants):
    best_match = difflib.get_close_matches(text, variants, n=1, cutoff=0.7)
    return best_match[0] if best_match else None

# ---------------------------
# Voice endpoints
# ---------------------------

@app.route("/voice", methods=["GET", "POST"])
def voice():
    resp = VoiceResponse()
    gather = Gather(input="speech", action="/process_order", method="POST", timeout=4, finish_on_key="#")
    gather.say("Welcome to Baguette de Moet Andover. Please say your order after the beep. Press # when finished.")
    resp.append(gather)
    resp.say("We did not receive any speech. Goodbye.")
    return str(resp)

@app.route("/process_order", methods=["POST"])
def process_order():
    resp = VoiceResponse()
    call_sid = request.form.get("CallSid")
    recording_url = request.form.get("RecordingUrl")

    if not recording_url or not call_sid:
        resp.say("Sorry, we did not receive your order.")
        return str(resp)

    audio = requests.get(recording_url + ".wav").content

    # Transcribe using Whisper
    transcription = openai_client.audio.transcriptions.create(
        model="gpt-4o-mini-transcribe",
        file=("audio.wav", audio, "audio/wav")
    )
    raw_speech_text = transcription.text
    speech_text = normalize_speech(raw_speech_text)
    logger.debug(f"Normalized speech: {speech_text}")

    ai_order = ai_parse_order(speech_text)
    logger.debug(f"AI order: {ai_order}")

    if not ai_order.get("items"):
        resp.say("Sorry, I could not recognize any items from our menu.")
        return str(resp)

    orders_store[call_sid] = {"order": ai_order, "speech_text": raw_speech_text}
    summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in ai_order["items"]])
    total = ai_order.get("total", 0.0)

    resp.say(f"I understood your order as: {summary}. Total is £{total:.2f}. Say yes to confirm or no to cancel.")
    gather = Gather(input="speech", action="/confirm_order", method="POST", timeout=5)
    resp.append(gather)
    resp.say("No confirmation received. Goodbye.")
    return str(resp)

@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    resp = VoiceResponse()
    call_sid = request.form.get("CallSid")
    confirmation_raw = request.form.get("SpeechResult", "")

    if not call_sid or call_sid not in orders_store:
        resp.say("Sorry, we lost the order information.")
        return str(resp)

    # Normalize and remove punctuation
    confirmation_clean = confirmation_raw.lower().translate(str.maketrans("", "", string.punctuation)).strip()

    # Expanded variants for fuzzy matching
    yes_variants = ["yes", "yeah", "yep", "confirm", "sure", "ok", "okay", "affirmative", "correct", "right"]
    no_variants = ["no", "nah", "nope", "cancel", "negative", "wrong"]

    matched = match_confirmation(confirmation_clean, yes_variants + no_variants)

    order_data = orders_store[call_sid]["order"]
    speech_text = orders_store[call_sid]["speech_text"]

    if matched in yes_variants:
        summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in order_data["items"]])
        total = order_data["total"]
        send_whatsapp(f"New Order: {summary}. Total £{total:.2f}. Original speech: {speech_text}")
        resp.say(f"Thank you! Your order of {summary} totaling £{total:.2f} has been sent to the kitchen.")
    elif matched in no_variants:
        resp.say("Order cancelled. Thank you for calling Baguette de Moet Andover.")
    else:
        # Ambiguous response, ask for clarification
        resp.say("Sorry, I did not understand your response. Please say yes or no to confirm your order.")
        gather = Gather(input="speech", action="/confirm_order", method="POST", timeout=5)
        resp.append(gather)
        return str(resp)

    del orders_store[call_sid]
    return str(resp)

# ---------------------------
# Run app
# ---------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
