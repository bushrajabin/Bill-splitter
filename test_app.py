"""Run: python test_app.py  (no API key / network needed; the model call is mocked)"""
import io
from PIL import Image
import app as appmod
import core

def fake_extract(client, img, mime, model, retries=2):
    return core.normalize({"merchant": "Cafe", "currency": "INR", "category": "food",
        "items": [{"name": "Pizza", "qty": 1, "price": 300}, {"name": "Beer", "qty": 2, "price": 200}],
        "taxes": [{"name": "GST", "amount": 25}], "total": 525})

appmod.get_client = lambda: object()
appmod.core.extract_receipt = fake_extract
c = appmod.app.test_client()

def test_index(): assert b"Receipt Splitter" in c.get("/").data

def test_extract():
    buf = io.BytesIO(); Image.new("RGB", (50, 50)).save(buf, "PNG"); buf.seek(0)
    r = c.post("/extract", data={"receipt": (buf, "r.png")}, content_type="multipart/form-data")
    j = r.get_json(); assert r.status_code == 200 and j["symbol"] == "₹" and len(j["items"]) == 2
    assert c.post("/extract").status_code == 400

def test_calculate():
    p = {"items": [{"name": "Pizza", "price": 300, "who": ["A", "B"]}, {"name": "Beer", "price": 200, "who": ["A"]}],
         "people": ["A", "B", "C"], "payer": "A", "total": 525, "tip_pct": 10, "method": "itemwise",
         "lang": "Hinglish", "upi": "a@upi", "currency": "INR", "taxes": [{"name": "GST", "amount": 25}]}
    j = c.post("/calculate", json=p).get_json()
    assert round(sum(j["shares"].values()), 2) == j["grand"] == 575.0 and j["ok"]
    assert set(j["messages"]) == {"B", "C"} and "a@upi" in j["messages"]["B"]
    assert c.post("/calculate", json={"people": ["A"], "items": []}).status_code == 400

def test_expense():
    j = c.post("/expense", json={"items": [{"name": "Tea", "price": 20}], "total": 21, "merchant": "Stall"}).get_json()
    assert "Stall" in j["summary"] and j["csv"].startswith("merchant,")

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
