"""Receipt Splitter & Actioner (Flask version)

Run:  python app.py   ->   http://127.0.0.1:5000

Flow: /extract (Gemma reads the photo -> JSON)  ->  user edits table in browser
      -> /calculate (Python does the math + drafts messages)  or  /expense (CSV + summary)
"""
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from google import genai

import core

load_dotenv()  # .env file se GEMINI_API_KEY padhta hai

API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
MODEL = core.DEFAULT_MODEL

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15 MB upload limit

_client = None


def get_client():
    global _client
    if API_KEY == "YOUR_GEMINI_API_KEY":
        raise RuntimeError("API key set nahi hai. app.py mein API_KEY daalo ya GEMINI_API_KEY env var set karo.")
    if _client is None:
        _client = genai.Client(api_key=API_KEY)
    return _client


def symbol_for(currency):
    currency = (currency or "INR").upper()
    return core.CURRENCY_SYMBOLS.get(currency, currency + " ")


def clean_items(raw):
    items = []
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        price = core.to_float(it.get("price"))
        if price is None:
            continue
        items.append({
            "name": str(it.get("name") or "Item").strip() or "Item",
            "qty": core.to_float(it.get("qty"), 1.0) or 1.0,
            "price": price,
            "who": [str(w) for w in (it.get("who") or [])],
        })
    return items


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/extract", methods=["POST"])
def extract():
    file = request.files.get("receipt")
    if not file:
        return jsonify(error="No image uploaded"), 400
    try:
        img, mime = core.prepare_image(file.read())
    except Exception:
        return jsonify(error="Ye image file padh nahi paya. JPG ya PNG try karo."), 400
    try:
        data = core.extract_receipt(get_client(), img, mime, MODEL)
        data["symbol"] = symbol_for(data["currency"])
        return jsonify(data)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=str(e)), 500


@app.route("/calculate", methods=["POST"])
def calculate():
    p = request.get_json(silent=True) or {}
    people = list(dict.fromkeys(str(n).strip() for n in (p.get("people") or []) if str(n).strip()))
    if len(people) < 2:
        return jsonify(error="Kam se kam 2 naam chahiye."), 400
    items = clean_items(p.get("items"))
    if not items:
        return jsonify(error="Table mein koi item nahi hai."), 400

    payer = p.get("payer") if p.get("payer") in people else people[0]
    total = core.to_float(p.get("total"))
    if total is None:
        total = round(sum(i["price"] for i in items), 2)
    tip_pct = core.to_float(p.get("tip_pct"), 0.0) or 0.0
    method = "equal" if p.get("method") == "equal" else "itemwise"
    lang = "Hinglish" if p.get("lang") == "Hinglish" else "English"
    upi = str(p.get("upi") or "").strip()
    merchant = str(p.get("merchant") or "").strip() or None
    symbol = symbol_for(p.get("currency"))
    taxes = [{"amount": core.to_float(t.get("amount"), 0.0)}
             for t in (p.get("taxes") or []) if isinstance(t, dict)]

    res = core.split_bill(items, people, total, tip_pct, method)
    calc, ok = core.check_total(items, taxes, total)

    messages = {
        person: core.payment_message(person, res["shares"][person], res["grand"], merchant, payer, symbol, lang, upi)
        for person in people if person != payer
    }
    group = core.group_message(res["shares"], res["grand"], merchant, payer, symbol, lang, upi)

    return jsonify(
        shares=res["shares"], owned=res["owned"], items_sum=res["items_sum"], extra=res["extra"],
        tip=res["tip"], grand=res["grand"], calc=calc, ok=ok, symbol=symbol, payer=payer,
        group=group, messages=messages,
        markdown=core.to_markdown_table(items, taxes_with_names(p), total, symbol),
    )


def taxes_with_names(p):
    return [{"name": str(t.get("name") or "Tax"), "amount": core.to_float(t.get("amount"), 0.0)}
            for t in (p.get("taxes") or []) if isinstance(t, dict)]


@app.route("/expense", methods=["POST"])
def expense():
    p = request.get_json(silent=True) or {}
    items = clean_items(p.get("items"))
    if not items:
        return jsonify(error="Table mein koi item nahi hai."), 400
    total = core.to_float(p.get("total"))
    if total is None:
        total = round(sum(i["price"] for i in items), 2)
    symbol = symbol_for(p.get("currency"))
    merchant = str(p.get("merchant") or "").strip() or None
    date = str(p.get("date") or "").strip() or None
    category = str(p.get("category") or "").strip() or None
    taxes = taxes_with_names(p)
    calc, ok = core.check_total(items, taxes, total)
    summary = f"{merchant or 'Expense'} ({category or 'other'}) on {date or 'n/a'}: {core.money(total, symbol)}"
    return jsonify(
        summary=summary, calc=calc, ok=ok, symbol=symbol,
        csv=core.to_csv(items, merchant, date, category),
        markdown=core.to_markdown_table(items, taxes, total, symbol),
    )


if __name__ == "__main__":
    app.run(debug=True)
