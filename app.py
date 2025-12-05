from flask import Flask, request, session
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI
import json, os, re

# -------------------------------------------------
# DEBUG environment variables
# -------------------------------------------------
print("DEBUG OPENAI_API_KEY =", bool(os.getenv("OPENAI_API_KEY")))
print("DEBUG TWILIO_ACCOUNT_SID =", bool(os.getenv("TWILIO_ACCOUNT_SID")))
print("DEBUG TWILIO_AUTH_TOKEN =", bool(os.getenv("TWILIO_AUTH_TOKEN")))

# -------------------------------------------------
# OpenAI client
# -------------------------------------------------
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -------------------------------------------------
# Menu config
# -------------------------------------------------
menu = {
    "tuna baguette": 4.99,
    "fries": 2.50,
    "large fries": 3.00,
    "coke": 1.20,
    "fanta": 1.20,
    "chicken baguette": 5.99
}

app = Flask(__name__)
app.secret_key = "mysuperlongrandomsecretkey123456789"

FROM_NUMBER = 'whatsapp:+14155238886'
TO_NUMBER = 'whatsapp:+447425766000'

# -------------------------------------------------
# Twilio client
# -------------------------------------------------
client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

# -------------------------------------------------
# Send WhatsApp helper
# -------------------------------------------------
def send_whatsapp(text):
    msg = client.messages.create(
        from_=FROM_NUMBER,
        body=text,
        to=TO_NUMBER
    )
    print("WhatsApp sent:", msg.sid)


# -------------------------------------------------
# Safe JSON cleaner (removes ```json ... ```)
# -------------------------------------------------
def clean_json(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"```$", "", text)
    return text.strip()


# -------------------------------------------------
# AI Parse Order (fully fixed)
# -------------------------------------------------
def ai_parse_order(speech_text):
    print("DEBUG: ai_parse_order called with:", speech_text)

    prompt = f"""
You are a restaurant assistant. The menu items are: {list(menu.keys())}

TASK:
Convert the customer's speech into *valid JSON only*:
{{
  "items": [
    {{"name": string, "quantity": number}}
  ],
  "total": number
}}

RULES:
- Ignore items not in this menu.
- "large fries" or "big fries" = "large fries".
- Use exact menu prices: {json.dumps(menu)}
- If nothing is found: items=[] and total=0.
- MUST output only a JSON object. No backticks. No explanation.

Customer said: "{speech_text}"
"""

    try:
        print("DEBUG: Sending prompt to OpenAI...")

        completion = openai_client.responses.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},   # ⭐ forces pure JSON
            input=prompt
        )

        print("DEBUG RAW COMPLETION:", completion)

        # This is ALWAYS pure JSON now
        ai_json = completion.output[0].content[0].text
        print("DEBUG RAW OPENAI TEXT:", ai_json)

        # Parse JSON safely
        return json.loads(ai_json)

    except Exception as e:
        print("OpenAI error:", e)
        return {"items": [], "total": 0}



# -------------------------------------------------
# Voice route
# -------------------------------------------------
@app.route("/voice", methods=["GET", "POST"])
def voice():
    print("DEBUG: /voice hit")

    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action="/process_order",
        method="POST",
        timeout=8
    )
    gather.say("Welcome to Baguette de Moet Andover. What would you like to order?")
    resp.append(gather)
    resp.say("We did not receive any speech. Goodbye.")
    return str(resp)


# -------------------------------------------------
# Process order route
# -------------------------------------------------
@app.route("/process_order", methods=["POST"])
def process_order():
    print("DEBUG: /process_order hit")
    resp = VoiceResponse()

    speech_text = request.form.get("SpeechResult", "").strip()
    print("DEBUG SpeechResult:", speech_text)

    if not speech_text:
        resp.say("Sorry, I did not understand.")
        return str(resp)

    ai_order = ai_parse_order(speech_text)
    print("DEBUG AI ORDER:", ai_order)

    if not ai_order.get("items"):
        resp.say("Sorry, I could not recognise any items from our menu.")
        return str(resp)

    session["order"] = ai_order
    session["speech_text"] = speech_text

    summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in ai_order["items"]])
    total = ai_order.get("total", 0)

    resp.say(
        f"You ordered {summary}. Total is £{total:.2f}. Say yes to confirm or no to cancel.",
        voice="alice"
    )

    gather = Gather(
        input="speech",
        action="/confirm_order",
        method="POST",
        timeout=5
    )
    resp.append(gather)
    resp.say("No confirmation received. Goodbye.", voice="alice")

    return str(resp)



# -------------------------------------------------
# Confirm order route
# -------------------------------------------------
@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    print("DEBUG: /confirm_order hit")
    resp = VoiceResponse()

    confirmation = request.form.get("SpeechResult", "").lower()
    print("DEBUG USER CONFIRMATION:", confirmation)

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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

    


