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

# ---------------------------
# Basic logging / debug
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
# Menu config
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
# Flask app and Twilio config
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
orders_store = {}  # key: CallSid, value: {"order": ..., "speech_text": ...}

# ---------------------------
# Helpers
# ---------------------------
def send_whatsapp(text):
    """Send a WhatsApp message via Twilio."""
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
    """Remove common markdown code fences and whitespace, returning raw JSON-like text."""
    if not text:
        return text
    text = text.strip()
    text = re.sub(r"^```(?:json|yaml|txt|js)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ---------------------------
# AI parse order
# ---------------------------
def ai_parse_order(speech_text):
    """
    Send customer speech to OpenAI to extract a structured order.
    Returns a dict: {"items": [...], "total": ...}
    """

    print("DEBUG: ai_parse_order called with:", speech_text)

    prompt = f"""
You are a restaurant assistant. Extract the order from the customer's message.

MENU ITEMS AND PRICES:
{json.dumps(menu)}

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
- Treat "big fries" or "large fries" as "large fries".
- Multiply quantity by menu prices to compute total.
- No explanations. No backticks. JSON only.

Customer said: "{speech_text}"
"""

    try:
        print("DEBUG: Sending prompt to OpenAI...")

        completion = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        # Use attribute access (works on recent SDKs)
        ai_text = completion.choices[0].message.content
        print("DEBUG RAW OPENAI TEXT:", ai_text)

        # Clean formatting / code fences
        cleaned = clean_json(ai_text)

        # Parse JSON
        order_data = json.loads(cleaned)
        return order_data

    except json.JSONDecodeError as e:
        print("JSON parsing error:", e)
        return {"items": [], "total": 0}

    except Exception as e:
        print("OpenAI error:", e)
        return {"items": [], "total": 0}

# ---------------------------
# Voice endpoints
# ---------------------------
@app.route("/voice", methods=["GET", "POST"])
def voice():
    logger.debug("DEBUG: /voice hit")
    resp = VoiceResponse()
    gather = Gather(input="speech", action="/process_order", method="POST", timeout=4)
    gather.say("Welcome to Baguette de Moet Andover. What would you like to order?")
    resp.append(gather)
    resp.say("We did not receive any speech. Goodbye.")
    return str(resp)


@app.route("/process_order", methods=["POST"])
def process_order():
    logger.debug("DEBUG: /process_order hit")
    resp = VoiceResponse()

    speech_text = (request.form.get("SpeechResult") or "").strip()
    call_sid = request.form.get("CallSid")
    logger.debug("DEBUG SpeechResult: %s, CallSid: %s", speech_text, call_sid)

    if not speech_text or not call_sid:
        resp.say("Sorry, I did not understand.")
        return str(resp)

    ai_order = ai_parse_order(speech_text)
    logger.debug("DEBUG AI ORDER: %s", ai_order)

    if not ai_order.get("items"):
        resp.say("Sorry, I could not recognise any items from our menu.")
        return str(resp)

    # store order by CallSid instead of session
    orders_store[call_sid] = {"order": ai_order, "speech_text": speech_text}

    summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in ai_order["items"]])
    total = ai_order.get("total", 0.0)

    resp.say(
        f"You ordered {summary}. Total is £{total:.2f}. Say yes to confirm or no to cancel.",
        voice="alice"
    )

    gather = Gather(input="speech", action="/confirm_order", method="POST", timeout=5)
    resp.append(gather)
    resp.say("No confirmation received. Goodbye.", voice="alice")

    return str(resp)


@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    logger.debug("DEBUG: /confirm_order hit")
    resp = VoiceResponse()

    call_sid = request.form.get("CallSid")
    confirmation_raw = (request.form.get("SpeechResult") or "")
    logger.debug("DEBUG USER CONFIRMATION RAW: %s", confirmation_raw)

    if not call_sid or call_sid not in orders_store:
        resp.say("Sorry, we lost the order information.")
        return str(resp)

    # Normalize confirmation: lowercase, strip punctuation and whitespace
    confirmation = confirmation_raw.strip().lower()
    confirmation = confirmation.translate(str.maketrans('', '', string.punctuation))
    confirmation = confirmation.strip()
    logger.debug("DEBUG USER CONFIRMATION NORMALIZED: %s", confirmation)

    # Define accepted yes/no variants
    yes_variants = ["yes", "yeah", "yep", "confirm", "sure", "ok", "okay", "affirmative"]
    no_variants = ["no", "nah", "nope", "cancel", "negative"]

    order_data = orders_store[call_sid]["order"]
    speech_text = orders_store[call_sid]["speech_text"]

    if confirmation in yes_variants:
        summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in order_data["items"]])
        total = order_data["total"]

        msg = f"New Order: {summary}. Total £{total:.2f}. Original speech: {speech_text}"
        send_whatsapp(msg)

        resp.say(f"Thank you! Your order of {summary} totaling £{total:.2f} has been sent to the kitchen.")
    elif confirmation in no_variants:
        resp.say("Order cancelled. Thank you for calling Baguette de Moet Andover.")
    else:
        resp.say("Sorry, I did not understand your response. Order cancelled.")
        logger.debug("DEBUG: Unrecognized confirmation: %s", confirmation)

    # Clean up stored order
    del orders_store[call_sid]

    return str(resp)


# ---------------------------
# Run the app
# ---------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
