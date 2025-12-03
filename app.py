from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from menu import menu
import openai
import json

app = Flask(__name__)

FROM_NUMBER = 'whatsapp:+14155238886'  # Twilio sandbox WhatsApp number
TO_NUMBER = 'whatsapp:+447425766000'   # Your WhatsApp number

client = Client('AC717f3075970887837f943d9717f16558', '414da7e86d4e46fee2f9008bc5ba4920')

# -------------------------
# OpenAI API key
# -------------------------
openai.api_key = "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"

# -------------------------
# Helper functions
# -------------------------
def send_whatsapp(message_text):
    """Send WhatsApp message via Twilio"""
    msg = client.messages.create(
        from_=FROM_NUMBER,
        body=message_text,
        to=TO_NUMBER
    )
    print("WhatsApp sent:", msg.sid)

def ai_parse_order(speech_text, menu):
    """
    Send speech to OpenAI to extract structured order data.
    Returns dict with 'items' (list of {"name","quantity"}) and 'total'.
    """
    menu_items = ', '.join(menu.keys())
    prompt = f"""
You are a restaurant assistant. The menu is: {menu_items}.
Parse the following customer order into JSON with "name" and "quantity" for each item.
Ignore items not on the menu. Calculate the total price using these menu prices.
Respond ONLY with JSON.

Customer said: "{speech_text}"
Menu prices: {json.dumps(menu)}
"""

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    try:
        parsed = json.loads(response['choices'][0]['message']['content'])
        return parsed
    except Exception as e:
        print("Error parsing AI output:", e)
        return {"items": [], "total": 0.0}

# -------------------------
# Flask routes
# -------------------------
@app.route("/voice", methods=["GET", "POST"])
def voice():
    """Initial Twilio voice endpoint"""
    resp = VoiceResponse()

    gather = Gather(
        input='speech',
        action='/process_order',
        method='POST',
        timeout=5
    )
    gather.say("Welcome to Baguette de Moet Andover. What would you like to order?")
    resp.append(gather)
    resp.say("Sorry, we did not receive any input. Please call again to place an order.")
    return str(resp)

@app.route("/process_order", methods=["POST"])
def process_order():
    """Process caller's speech input"""
    resp = VoiceResponse()
    speech_text = request.form.get('SpeechResult', '')

    if not speech_text:
        resp.say("Sorry, we did not understand your order.")
        return str(resp)

    # -------------------------
    # AI parsing
    # -------------------------
    ai_order = ai_parse_order(speech_text, menu)

    if not ai_order["items"]:
        resp.say("Sorry, we could not find any items from your order in our menu.")
        return str(resp)

    # -------------------------
    # Build WhatsApp message
    # -------------------------
    order_summary = ', '.join([f"{item['quantity']} x {item['name']}" for item in ai_order["items"]])
    whatsapp_message = f"New Order: {order_summary}. Total: £{ai_order['total']:.2f}. Customer said: {speech_text}"
    send_whatsapp(whatsapp_message)

    # -------------------------
    # Confirm to caller
    # -------------------------
    resp.say(f"Got it. You ordered {order_summary}. Sending your order to the kitchen. The total is £{ai_order['total']:.2f}.")
    return str(resp)

# -------------------------
# Run app
# -------------------------
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)






