# app.py
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI
import json
import os
import re
import logging
import string
from metaphone import doublemetaphone
import difflib

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("baguette_app")

logger.debug("DEBUG OPENAI_API_KEY = %s", bool(os.getenv("OPENAI_API_KEY")))
logger.debug("DEBUG TWILIO_ACCOUNT_SID = %s", bool(os.getenv("TWILIO_ACCOUNT_SID")))
logger.debug("DEBUG TWILIO_AUTH_TOKEN = %s", bool(os.getenv("TWILIO_AUTH_TOKEN")))

# ---------------------------
# OpenAI client
# ---------------------------
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
# Flask app and Twilio
# ---------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mysuperlongrandomsecretkey123456789")

FROM_NUMBER = os.getenv("TWILIO_FROM", "whatsapp:+14155238886")
TO_NUMBER = os.getenv("TWILIO_TO", "whatsapp:+447425766000")

twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

# ---------------------------
# In-memory store for orders keyed by CallSid
# ---------------------------
orders_store = {}

# ---------------------------
# Helpers
# ---------------------------
def send_whatsapp(text):
    try:
        msg = twilio_client.messages.create(
            from_=FROM_NUMBER,
            body=text,
            to=TO_NUMBER
        )
        logger.debug("WhatsApp sent: %s", msg.sid)
    except Exception as e:
        logger.exception("Failed to send WhatsApp message: %s", e)


def clean_json(text: str) -> str:
    if not text:
        return text
    text = text.strip()
    text = re.sub(r"^```(?:json|yaml|txt|js)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def normalize_speech(text: str) -> str:
    text = text.lower()
    text = text.replace("bagette", "baguette")
    text = text.replace("chiken", "chicken")
    text = text.replace("big fries", "large fries")
    text = text.replace("coka", "coke")
    text = text.replace("fantaa", "fanta")
    return text.strip()


def closest_menu_item(spoken_text, menu_items):
    """Find the closest menu item using phonetic similarity."""
    spoken_code = doublemetaphone(spoken_text)[0]
    best_match = None
    best_ratio = 0
    for item in menu_items:
        item_code = doublemetaphone(item)[0]
        ratio = difflib.SequenceMatcher(None, spoken_code, item_code).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = item
    return best_match if best_ratio > 0.6 else None  # threshold adjustable


# ---------------------------
# AI parse order
# ---------------------------
def ai_parse_order(speech_text):
    """Send customer speech to OpenAI to extract structured order."""
    logger.debug("DEBUG: ai_parse_order called with: %s", speech_text)

    # First, try phonetic pre-match for menu items
    matched_items = []
    for item in menu.keys():
        if item in speech_text:
            matched_items.append(item)
        else:
            closest = closest_menu_item(speech_text, [item])
            if closest:
                matched_items.append(closest)

    # If nothing matches, fallback to AI parsing
    if not matched_items:
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
- Try to match customer words to the closest menu item using aliases, even if slightly mispronounced or misheard.
- Multiply quantity by menu prices to compute total.
- No explanations. No backticks. JSON only.

Customer said: "{speech_text}"
"""
        try:
            completion = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            ai_text = completion.choices[0].message.content
            cleaned = clean_json(ai_text)
            order_data = json.loads(cleaned)
            return order_data
        except Exception as e:
            logger.exception("OpenAI error: %s", e)
            return {"items": [], "total": 0}

    # Compute total for matched items
    items = []
    total = 0.0
    for item in matched_items:
        items.append({"name": item, "quantity": 1})
        total += menu[item]
    return {"items": items, "total": total}


# ---------------------------
# Voice endpoints
# ---------------------------
@app.route("/voice", methods=["GET", "POST"])
def voice():
    resp = VoiceResponse()
    gather = Gather(input="speech", action="/process_order", method="POST", timeout=4)
    gather.say("Welcome to Baguette de Moet Andover. What would you like to order?")
    resp.append(gather)
    resp.say("We did not receive any speech. Goodbye.")
    return str(resp)


@app.route("/process_order", methods=["POST"])
def process_order():
    resp = VoiceResponse()
    raw_speech_text = (request.form.get("SpeechResult") or "")
    call_sid = request.form.get("CallSid")

    if not raw_speech_text or not call_sid:
        resp.say("Sorry, I did not understand.")
        return str(resp)

    speech_text = normalize_speech(raw_speech_text)
    logger.debug("Normalized speech: %s", speech_text)

    ai_order = ai_parse_order(speech_text)
    logger.debug("AI order: %s", ai_order)

    if not ai_order.get("items"):
        resp.say("Sorry, I could not recognize any items from our menu.")
        return str(resp)

    orders_store[call_sid] = {"order": ai_order, "speech_text": raw_speech_text}
    summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in ai_order["items"]])
    total = ai_order.get("total", 0.0)

    resp.say(
        f"I understood your order as: {summary}. Total is £{total:.2f}. Say yes to confirm or no to cancel.",
        voice="alice"
    )

    gather = Gather(input="speech", action="/confirm_order", method="POST", timeout=5)
    resp.append(gather)
    resp.say("No confirmation received. Goodbye.", voice="alice")
    return str(resp)


@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    resp = VoiceResponse()
    call_sid = request.form.get("CallSid")
    confirmation_raw = (request.form.get("SpeechResult") or "")

    if not call_sid or call_sid not in orders_store:
        resp.say("Sorry, we lost the order information.")
        return str(resp)

    confirmation = confirmation_raw.lower().translate(str.maketrans("", "", string.punctuation)).strip()
    yes_variants = ["yes", "yeah", "yep", "confirm", "sure", "ok", "okay", "affirmative"]
    no_variants = ["no", "nah", "nope", "cancel", "negative"]

    order_data = orders_store[call_sid]["order"]
    speech_text = orders_store[call_sid]["speech_text"]

    if confirmation in yes_variants:
        summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in order_data["items"]])
        total = order_data["total"]
        send_whatsapp(f"New Order: {summary}. Total £{total:.2f}. Original speech: {speech_text}")
        resp.say(f"Thank you! Your order of {summary} totaling £{total:.2f} has been sent to the kitchen.")
    elif confirmation in no_variants:
        resp.say("Order cancelled. Thank you for calling Baguette de Moet Andover.")
    else:
        resp.say("Sorry, I did not understand your response. Order cancelled.")

    del orders_store[call_sid]
    return str(resp)


# ---------------------------
# Run app
# ---------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
