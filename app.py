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
def ai_parse_order(speech_text: str):
    """
    Send prompt to OpenAI Responses API and return a dict like:
    {"items": [{"name": "...", "quantity": N}], "total": X}
    Always returns dict with keys 'items' and 'total' (fallback to empty order on error).
    """
    logger.debug("DEBUG: ai_parse_order called with: %s", speech_text)

    prompt = f"""
You are a restaurant assistant. The available menu items (exact names) are:
{json.dumps(list(menu.keys()))}

TASK:
Convert the customer's speech into valid JSON only. The JSON must be a single object with this shape:
{{
  "items": [
    {{"name": "string", "quantity": number}}
  ],
  "total": number
}}

RULES:
- Only include items that exactly match the menu names (use "large fries" for synonyms like "big fries").
- Use the exact menu prices below to compute the total.
- If nothing is found, return items: [] and total: 0.
- Output MUST be valid JSON only. NO explanation, NO markdown fences, NO code fences.

Menu prices: {json.dumps(menu)}

Customer said: "{speech_text}"
"""

    # We'll also provide a JSON schema request to the API to encourage structured output.
    response_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "quantity": {"type": "number"}
                    },
                    "required": ["name", "quantity"]
                }
            },
            "total": {"type": "number"}
        },
        "required": ["items", "total"]
    }

    try:
        logger.debug("DEBUG: Sending prompt to OpenAI Responses API")

        # Use response_schema instead of the old 'response_format' param.
        resp = openai_client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
            response_schema=response_schema,
            temperature=0.0
        )

        logger.debug("DEBUG RAW RESP: %s", resp)

        # Try to extract the textual/JSON output from response objects
        raw_text = extract_text_from_response(resp)
        raw_text = raw_text or ""
        raw_text = clean_json(raw_text)

        logger.debug("DEBUG RAW OPENAI TEXT (cleaned): %s", raw_text[:1000])

        if not raw_text:
            # If response_schema succeeded the SDK might have a 'output' structured field already
            # Sometimes the responses.create may include parsed values under 'output_parsed' or 'output[0].content[0].payload'
            # Attempt to find 'value' content in the structured response
            try:
                # some SDKs put parsed JSON into resp.output[0].content[0].value
                if hasattr(resp, "output") and isinstance(resp.output, list):
                    for block in resp.output:
                        content = block.get("content") if isinstance(block, dict) else getattr(block, "content", None)
                        if not content:
                            continue
                        for c in content:
                            if isinstance(c, dict) and "value" in c:
                                parsed = c["value"]
                                logger.debug("Found structured parsed value in response: %s", parsed)
                                if isinstance(parsed, dict):
                                    return parsed
            except Exception:
                logger.exception("Secondary structured parse attempt failed")

            logger.warning("No text extracted from OpenAI response; returning empty order")
            return {"items": [], "total": 0}

        # Try to parse JSON
        parsed = json.loads(raw_text)
        # Validate shape minimally
        if "items" not in parsed or "total" not in parsed:
            logger.warning("Parsed JSON missing expected keys, returning empty order: %s", parsed)
            return {"items": [], "total": 0}

        # Normalize items (ensure quantities are ints and names are lowercased to match menu keys)
        normalized_items = []
        for it in parsed.get("items", []):
            try:
                name = it.get("name", "").strip().lower()
                if name == "big fries":
                    name = "large fries"
                if name not in menu:
                    logger.debug("Ignoring unknown menu item from AI: %s", name)
                    continue
                qty = int(it.get("quantity", 0))
                if qty <= 0:
                    continue
                normalized_items.append({"name": name, "quantity": qty})
            except Exception:
                logger.exception("Error normalizing item: %s", it)
                continue

        # If AI returned items but we filtered them all out, return empty order
        if not normalized_items:
            return {"items": [], "total": 0}

        # Recalculate total using the canonical menu prices for safety
        total = 0.0
        for it in normalized_items:
            total += menu[it["name"]] * it["quantity"]

        result = {"items": normalized_items, "total": round(total, 2)}
        logger.debug("AI parsed order result: %s", result)
        return result

    except Exception as e:
        logger.exception("OpenAI error while parsing order: %s", e)
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

    


