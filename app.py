from flask import Flask, request, session
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import OpenAI
import json, base64, os

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# -------------------------
# Menu & config
# -------------------------
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

# Twilio client
client = Client(
    'AC717f3075970887837f943d9717f16558',
    '414da7e86d4e46fee2f9008bc5ba4920'
)



# -------------------------
# Helpers
# -------------------------
def send_whatsapp(text):
    msg = client.messages.create(
        from_=FROM_NUMBER,
        body=text,
        to=TO_NUMBER
    )
    print("WhatsApp sent:", msg.sid)


def ai_parse_order(speech_text):
    """Send order text to OpenAI for parsing."""
    prompt = f"""
You are a restaurant assistant. The menu is: {list(menu.keys())}.
Parse the customer's speech into JSON with fields:

items: [
  {{ "name": string, "quantity": number }}
]
total: number

Rules:
- Ignore items not on the menu.
- "large fries" or "big fries" → "large fries".
- Use these exact menu prices: {json.dumps(menu)}
- If nothing is ordered, return items=[] and total=0.
- Respond ONLY with JSON.

Customer said: "{speech_text}"
"""

    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        data = completion.choices[0].message.content.strip()
        return json.loads(data)

    except Exception as e:
        print("OpenAI error:", e)
        return {"items": [], "total": 0.0}


# -------------------------
# Flask Routes
# -------------------------

@app.route("/voice", methods=["GET", "POST"])
def voice():
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


@app.route("/process_order", methods=["POST"])
def process_order():
    resp = VoiceResponse()
    speech_text = request.form.get("SpeechResult", "")

    if not speech_text:
        resp.say("Sorry, I did not understand.")
        return str(resp)

    # Parse via OpenAI
    ai_order = ai_parse_order(speech_text)

    if not ai_order["items"]:
        resp.say("Sorry, I could not recognise any items from our menu.")
        return str(resp)

    # Save order in session for confirmation call
    session["order"] = ai_order
    session["speech_text"] = speech_text

    # Build summary
    summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in ai_order["items"]])
    total = ai_order["total"]

    gather = Gather(
        input="speech",
        action="/confirm_order",
        method="POST",
        timeout=5
    )
    gather.say(f"You ordered {summary}. Total is £{total:.2f}. Say yes to confirm or no to cancel.")
    resp.append(gather)

    resp.say("No confirmation received. Goodbye.")
    return str(resp)


@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    resp = VoiceResponse()
    confirmation = request.form.get("SpeechResult", "").lower()

    if confirmation in ["yes", "yeah", "yep", "confirm"]:
        # Retrieve saved order
        ai_order = session.get("order")
        speech_text = session.get("speech_text")

        if not ai_order:
            resp.say("Sorry, we lost the order information.")
            return str(resp)

        summary = ", ".join([f"{i['quantity']} x {i['name']}" for i in ai_order["items"]])
        total = ai_order["total"]

        # Send WhatsApp
        msg = f"New Order: {summary}. Total £{total:.2f}. Original speech: {speech_text}"
        send_whatsapp(msg)

        resp.say(f"Thank you! Your order of {summary} totaling £{total:.2f} has been sent to the kitchen.")
    else:
        resp.say("Order cancelled. Thank you for calling Baguette de Moet Andover.")

    return str(resp)


# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
