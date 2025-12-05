# app.py
from flask import Flask, request, session
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI
import json
import os
import re
import logging

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
    # remove ```json or ``` or ```yaml fences at start
    text = re.sub(r"^```(?:json|yaml|txt|js)?\s*", "", text, flags=re.IGNORECASE)
    # remove closing ```
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_text_from_response(resp) -> str:
    """
    Try to extract textual output from various shapes the Responses API might return.
    This is defensive: different SDK versions may return different structures.
    """
    # If the SDK object has an 'output_text' attribute (some SDKs provide it)
    try:
        if hasattr(resp, "output_text") and resp.output_text:
            logger.debug("Using resp.output_text")
            return resp.output_text
    except Exception:
        pass

    # Try the typical nested structure resp.output -> list -> content -> list -> {type:text, text:...}
    try:
        if hasattr(resp, "output") and isinstance(resp.output, list) and resp.output:
            for block in resp.output:
                # block.content may be a list
                content = block.get("content") if isinstance(block, dict) else getattr(block, "content", None)
                if not content:
                    continue
                # content might be list of dicts or objects
                for c in content:
                    # c may be dict with 'text' or 'raw' fields or object with .text
                    if isinstance(c, dict):
                        if "text" in c and c["text"]:
                            return c["text"]
                        if "content" in c and isinstance(c["content"], str) and c["content"].strip():
                            return c["content"]
                    else:
                        # try attribute access
                        text = getattr(c, "text", None) or getattr(c, "content", None)
                        if text:
                            return text
    except Exception:
        logger.exception("Error while inspecting resp.output")

    # Finally try to stringify resp and search for JSON block inside
    try:
        s = str(resp)
        # try to find a JSON object in the string
        m = re.search(r"(\{[\s\S]*\})", s)
        if m:
            return m.group(1)
    except Exception:
        pass

    return ""


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
            model="gpt-4o-mini",
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
    gather = Gather(input="speech", action="/process_order", method="POST", timeout=8)
    gather.say("Welcome to Baguette de Moet Andover. What would you like to order?")
    resp.append(gather)
    resp.say("We did not receive any speech. Goodbye.")
    return str(resp)


@app.route("/process_order", methods=["POST"])
def process_order():
    logger.debug("DEBUG: /process_order hit")
    resp = VoiceResponse()

    speech_text = (request.form.get("SpeechResult") or "").strip()
    logger.debug("DEBUG SpeechResult: %s", speech_text)

    if not speech_text:
        resp.say("Sorry, I did not understand.")
        return str(resp)

    ai_order = ai_parse_order(speech_text)
    logger.debug("DEBUG AI ORDER: %s", ai_order)

    if not ai_order.get("items"):
        resp.say("Sorry, I could not recognise any items from our menu.")
        return str(resp)

    # store in session for later confirmation
    session["order"] = ai_order
    session["speech_text"] = speech_text

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

    confirmation = (request.form.get("SpeechResult") or "").strip().lower()
    logger.debug("DEBUG USER CONFIRMATION: %s", confirmation)

    if confirmation in ["yes", "yeah", "yep", "confirm"]:
        ai_order = session.get("order")
        speech_text = session.get("speech_text")

        if not ai_order:
            resp.say("Sorry, we lost the order information.")
            return str(resp)

        summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in ai_order["items"]])
        total = ai_order["total"]

        msg = f"New Order: {summary}. Total £{total:.2f}. Original speech: {speech_text}"
        send_whatsapp(msg)

        resp.say(f"Thank you! Your order of {summary} totaling £{total:.2f} has been sent to the kitchen.")
    else:
        resp.say("Order cancelled. Thank you for calling Baguette de Moet Andover.")

    return str(resp)


# ---------------------------
# Run the app
# ---------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

    


