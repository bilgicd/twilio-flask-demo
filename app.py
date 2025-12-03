from flask import Flask, request, redirect
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import openai
import json
import base64

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

FROM_NUMBER = 'whatsapp:+14155238886'  # Twilio sandbox WhatsApp number
TO_NUMBER = 'whatsapp:+447425766000'   # Your WhatsApp number

# Twilio client
client = Client('AC717f3075970887837f943d9717f16558', '414da7e86d4e46fee2f9008bc5ba4920')

# OpenAI API key (base64 encoded)
OPENAI_KEY_B64 = "c2stcHJvai1DNXBndFBmaE1IMEtFb0dRMnk3M0dpYUpVQ3FmNE9MODJDcW1GdXdiMGM1NDVZcEMyVllCdXNQODdBWkdzZWYxSXhWUTJmdkpNQVQzQmxia0ZKZ2xYendDQUVtaEVJX29iQk43VkpQanM5TUdYSWRtY3BYcDRKTG5qNjM5aW1xT1lGd0p6Y0tJbUxhdnJrNHl6dHBSSERPbWZoVUE="
openai.api_key = base64.b64decode(OPENAI_KEY_B64).decode()

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
    Returns dict with 'items' and 'total'.
    """
    menu_items = ', '.join(menu.keys())
    prompt = f"""
You are a restaurant assistant. The menu is: {menu_items}.
Parse the following customer order into JSON with "name" and "quantity" for each item.
Ignore items not on the menu. Calculate the total price using these menu prices.
Respond ONLY with JSON. If the customer says "large fries" or "big fries", treat it as "large fries".

Customer said: "{speech_text}"
Menu prices: {json.dumps(menu)}
"""

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    try:
        parsed = json.loads(response['choices'][0]['message']['content'].strip())
        return parsed
    except Exception as e:
        print("Error parsing AI output:", e)
        return {"items": [], "total": 0.0}

# -------------------------
# Flask routes
# -------------------------

@app.route("/voice", methods=["GET", "POST"])
def voice():
    resp = VoiceResponse()
    gather = Gather(
        input='speech',
        action='/process_order',
        method='POST',
        timeout=12  # increased to capture full sentences
    )
    gather.say("Welcome to Baguette de Moet Andover. What would you like to order?")
    resp.append(gather)
    resp.say("Sorry, we did not receive any input. Please call again to place an order.")
    return str(resp)

@app.route("/process_order", methods=["POST"])
def process_order():
    resp = VoiceResponse()
    speech_text = request.form.get('SpeechResult', '')

    if not speech_text:
        resp.say("Sorry, we did not understand your order.")
        return str(resp)

    ai_order = ai_parse_order(speech_text, menu)

    if not ai_order["items"]:
        resp.say("Sorry, we could not find any items from your order in our menu.")
        return str(resp)

    # Create order summary text
    order_summary = ', '.join([f"{item['quantity']} x {item['name']}" for item in ai_order["items"]])
    total = ai_order['total']

    # Ask caller to confirm order
    gather = Gather(
        input='speech',
        action='/confirm_order',
        method='POST',
        timeout=5
    )
    gather.say(f"You ordered {order_summary}. The total is £{total:.2f}. Say yes to confirm or no to cancel.")
    resp.append(gather)
    resp.say("We did not receive a confirmation. Please call again to place an order.")
    return str(resp)

@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    resp = VoiceResponse()
    confirmation = request.form.get('SpeechResult', '').lower()
    
    if confirmation in ['yes', 'yeah', 'yep', 'confirm']:
        # Retrieve last AI order (for simplicity, you could store it in session/db)
        speech_text = request.form.get('SpeechResult', '')
        ai_order = ai_parse_order(speech_text, menu)  # optional, re-parse to get items
        order_summary = ', '.join([f"{item['quantity']} x {item['name']}" for item in ai_order["items"]])
        total = ai_order['total']

        whatsapp_message = f"New Order: {order_summary}. Total: £{total:.2f}. Customer said: {speech_text}"
        send_whatsapp(whatsapp_message)

        resp.say(f"Thank you! Your order of {order_summary} totaling £{total:.2f} has been sent to the kitchen.")
    else:
        resp.say("Order cancelled. Thank you for calling Baguette de Moet Andover.")
    
    return str(resp)

# -------------------------
# Run app
# -------------------------
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
