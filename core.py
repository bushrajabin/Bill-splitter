"""Core logic: image prep, Gemma extraction, validation, split math, message drafting.

No Streamlit imports here, so everything is easy to test (see tests.py).
Design rule: the model ONLY extracts. All arithmetic is done in Python.
"""
import csv
import io
import json
import os
import re

DEFAULT_MODEL = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it")

EXTRACTION_PROMPT = """You are a receipt parser. Read the attached receipt image
(it may be skewed, crumpled, low-light or handwritten). Return ONLY valid JSON,
no markdown fences, no extra text:

{
  "merchant": string or null,
  "date": "YYYY-MM-DD" or null,
  "currency": "INR" or another ISO code,
  "category": one of "food", "groceries", "travel", "shopping", "utilities", "other",
  "items": [{"name": string, "qty": number, "price": number}],
  "subtotal": number or null,
  "taxes": [{"name": string, "amount": number}],
  "total": number,
  "confidence_notes": "anything unclear or illegible, else empty string"
}

Rules:
- price = the LINE TOTAL for that item (qty x unit price), not the unit price.
- Put GST/CGST/SGST/VAT/service charge in "taxes", never in "items".
- Use null for any field that is not visible.
- Never guess illegible numbers; describe them in confidence_notes.
"""

CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "AED": "AED ", "JPY": "¥"}


# ---------- image ----------
def prepare_image(raw: bytes, max_px: int = 1600):
    """Fix phone-camera rotation, shrink big photos, return (jpeg_bytes, mime)."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


# ---------- parsing ----------
def to_float(v, default=None):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v).replace(",", ""))
    try:
        return float(s)
    except ValueError:
        return default


def parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return json.loads(text[start : end + 1])


def normalize(raw: dict) -> dict:
    items = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        price = to_float(it.get("price"))
        if price is None:
            continue
        items.append({
            "name": str(it.get("name") or "Item").strip(),
            "qty": to_float(it.get("qty"), 1.0) or 1.0,
            "price": price,
        })
    taxes = []
    for t in raw.get("taxes") or []:
        if not isinstance(t, dict):
            continue
        amt = to_float(t.get("amount"))
        if amt is not None:
            taxes.append({"name": str(t.get("name") or "Tax").strip(), "amount": amt})
    total = to_float(raw.get("total"))
    if total is None:
        total = round(sum(i["price"] for i in items) + sum(t["amount"] for t in taxes), 2)
    return {
        "merchant": (raw.get("merchant") or "").strip() or None,
        "date": raw.get("date"),
        "currency": (raw.get("currency") or "INR").upper(),
        "category": raw.get("category") or "other",
        "items": items,
        "subtotal": to_float(raw.get("subtotal")),
        "taxes": taxes,
        "total": total,
        "confidence_notes": (raw.get("confidence_notes") or "").strip(),
    }


def extract_receipt(client, img_bytes: bytes, mime: str, model: str = DEFAULT_MODEL, retries: int = 2) -> dict:
    """One vision call -> normalized dict. Retries if the model returns broken JSON."""
    from google.genai import types

    last_err = None
    for _ in range(retries + 1):
        resp = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=img_bytes, mime_type=mime), EXTRACTION_PROMPT],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        try:
            return normalize(parse_json(resp.text))
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    raise RuntimeError(f"Model did not return valid JSON after {retries + 1} tries: {last_err}")


# ---------- validation ----------
def check_total(items, taxes, total, tolerance: float = 1.0):
    """Returns (calculated_total, matches_receipt_total)."""
    calc = round(sum(i["price"] for i in items) + sum(t["amount"] for t in taxes), 2)
    return calc, abs(calc - total) <= tolerance


# ---------- split math ----------
def _round_to_total(shares: dict, total: float) -> dict:
    """Round to paise and push any leftover paise onto the largest share so sum == total."""
    rounded = {k: round(v, 2) for k, v in shares.items()}
    diff = round(total - sum(rounded.values()), 2)
    if rounded and abs(diff) >= 0.01:
        biggest = max(rounded, key=rounded.get)
        rounded[biggest] = round(rounded[biggest] + diff, 2)
    return rounded


def split_bill(items, people, total, tip_pct: float = 0.0, method: str = "itemwise") -> dict:
    """
    items: [{"name", "price", "who": [names]}]  (empty/unknown 'who' = shared by everyone)
    total: final receipt total (taxes/charges/discounts = total - items_sum, spread proportionally)
    method: "itemwise" or "equal"
    """
    items_sum = round(sum(i["price"] for i in items), 2)
    extra = round(total - items_sum, 2)
    tip = round(items_sum * tip_pct / 100.0, 2)
    grand = round(total + tip, 2)

    if method == "equal" or items_sum == 0:
        shares = {p: grand / len(people) for p in people}
        owned = {p: [] for p in people}
    else:
        base = {p: 0.0 for p in people}
        owned = {p: [] for p in people}
        for it in items:
            who = [w for w in it.get("who", []) if w in base] or list(people)
            for w in who:
                base[w] += it["price"] / len(who)
                owned[w].append(it["name"])
        shares = {p: base[p] + (extra + tip) * (base[p] / items_sum) for p in people}

    return {
        "shares": _round_to_total(shares, grand),
        "owned": owned,
        "items_sum": items_sum,
        "extra": extra,
        "tip": tip,
        "grand": grand,
    }


# ---------- messages ----------
def money(amount: float, symbol: str) -> str:
    return f"{symbol}{amount:,.2f}"


def payment_message(name, share, grand, merchant, payer, symbol="₹", lang="English", upi_id="") -> str:
    where = merchant or "the restaurant"
    upi = f" (UPI: {upi_id})" if upi_id else ""
    if lang == "Hinglish":
        return (f"Hi {name}! 👋 {where} ka bill {money(grand, symbol)} aaya tha. "
                f"Tumhara share {money(share, symbol)} hai. Jab time mile {payer} ko bhej dena{upi}. Thanks! 🙏")
    return (f"Hi {name}! 👋 The bill at {where} came to {money(grand, symbol)}. "
            f"Your share is {money(share, symbol)}. Please send it to {payer}{upi} when you get a chance. Thanks! 🙏")


def group_message(shares: dict, grand, merchant, payer, symbol="₹", lang="English", upi_id="") -> str:
    where = merchant or "the restaurant"
    lines = "\n".join(f"• {p}: {money(a, symbol)}" for p, a in shares.items() if p != payer)
    upi = f"\nUPI: {upi_id}" if upi_id else ""
    if lang == "Hinglish":
        return (f"{where} ka bill {money(grand, symbol)}, {payer} ne pay kiya. Sabka share:\n{lines}\n"
                f"Please {payer} ko bhej do 🙏{upi}")
    return (f"Bill at {where}: {money(grand, symbol)}, paid by {payer}. Shares:\n{lines}\n"
            f"Please send to {payer} 🙏{upi}")


# ---------- output formats ----------
def to_markdown_table(items, taxes, total, symbol="₹") -> str:
    md = "| Item | Qty | Price |\n|---|---:|---:|\n"
    for i in items:
        qty = int(i["qty"]) if float(i["qty"]).is_integer() else i["qty"]
        md += f"| {i['name']} | {qty} | {money(i['price'], symbol)} |\n"
    for t in taxes:
        md += f"| *{t['name']}* | | {money(t['amount'], symbol)} |\n"
    md += f"| **Total** | | **{money(total, symbol)}** |\n"
    return md


def to_csv(items, merchant=None, date=None, category=None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["merchant", "date", "category", "item", "qty", "price"])
    for i in items:
        w.writerow([merchant or "", date or "", category or "", i["name"], i["qty"], i["price"]])
    return buf.getvalue()
