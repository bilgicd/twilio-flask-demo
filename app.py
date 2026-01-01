from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI
import os
import json
import logging
import re
import string
import jellyfish

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("baguette_app")

# ---------------------------
# Environment
# ---------------------------
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM")
TWILIO_TO = os.getenv("TWILIO_TO")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("BASE_URL")

# ---------------------------
# Clients
# ---------------------------
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------------------
# App
# ---------------------------
app = Flask(__name__)
orders_store = {}

# ---------------------------
# Menu
# ---------------------------
MENU = {
    "tuna baguette": 4.99,
    "chicken baguette": 5.99,
    "fries": 2.50,
    "large fries": 3.00,
    "coke": 1.20,
    "fanta": 1.20
}

# Precompute phonetic keys
PHONETIC_MENU = {
    item: jellyfish.soundex(item)
    for item in MENU
}

# ---------------------------
# Helpers
# ---------------------------
def send_whatsapp(text):
    twilio_client.messages.create(from_=TWILIO_FROM, to=TWILIO_TO, body=text)

def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    replacements = {
        "chips": "fries",
        "cook": "coke",
        "price": "fries",
        "bag get": "baguette",
        "baguete": "baguette",
        "two": "tuna"
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.strip()

def phonetic_match(text: str):
    """
    Match speech text to closest menu item using phonetics.
    """
    text_code = jellyfish.soundex(text)
    best_match = None
    best_score = 0

    for item, item_code in PHONETIC_MENU.items():
        score = jellyfish.jaro_similarity(text_code, item_code)
        if score > best_score:
            best_match = item
            best_score = score

    return best_match if best_score > 0.75 else None

def ai_parse_order(text: str):
    prompt = f"""
You are a voice ordering assistant.

MENU:
{json.dumps(MENU)}

RULES:
- Infer intent even if words are misheard or phonetically similar
- UK English (chips = fries)
- Return JSON only

FORMAT:
{{
  "items": [{{"name": string, "quantity": number}}],
  "confidence": number
}}

Customer said:
"{text}"
"""

    try:
        r = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        logger.exception("GPT failed")
        return {"items": [], "confidence": 0}

# ---------------------------
# Routes
# ---------------------------
@app.route("/voice", methods=["POST", "GET"])
def voice():
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"{BASE_URL}/process",
        timeout=8,
        language="en-GB",
        hints=list(MENU.keys())
    )
    gather.say("Welcome to Baguette de Moet. Please tell me your order.")
    resp.append(gather)
    return str(resp)

@app.route("/process", methods=["POST"])
def process():
    resp = VoiceResponse()
    call_sid = request.form.get("CallSid")
    speech = request.form.get("SpeechResult", "")

    if not speech:
        resp.say("Sorry, I did not hear anything.")
        return str(resp)

    normalized = normalize_text(speech)
    logger.debug(f"Normalized: {normalized}")

    # Try phonetic match first
    phonetic_item = phonetic_match(normalized)

    if phonetic_item:
        orders_store[call_sid] = {
            "items": [{"name": phonetic_item, "quantity": 1}]
        }
    else:
        ai_result = ai_parse_order(normalized)
        if not ai_result["items"]:
            resp.say("I didn't quite get that. Did you want tuna baguette or chicken baguette?")
            return str(resp)
        orders_store[call_sid] = ai_result

    items = orders_store[call_sid]["items"]
    summary = ", ".join(f"{i['quantity']} {i['name']}" for i in items)
    resp.say(f"I heard {summary}. Please say yes to confirm or no to cancel.")

    gather = Gather(
        input="speech",
        action=f"{BASE_URL}/confirm",
        timeout=5,
        language="en-GB"
    )
    resp.append(gather)
    return str(resp)

@app.route("/confirm", methods=["POST"])
def confirm():
    resp = VoiceResponse()
    call_sid = request.form.get("CallSid")
    speech = request.form.get("SpeechResult", "").lower()

    if "yes" in speech:
        items = orders_store.pop(call_sid)["items"]
        total = sum(MENU[i["name"]] * i["quantity"] for i in items)
        send_whatsapp(f"Order: {items} | Total £{total:.2f}")
        resp.say("Thank you. Your order has been placed.")
    else:
        resp.say("Order cancelled.")

    return str(resp)

# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
