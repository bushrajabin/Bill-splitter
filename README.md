Scan & Split

Scan a receipt with your phone, tick who shared each item, and get exact amounts plus ready-to-send payment messages.

Live demo: https://scan-and-split.vercel.app/


Why

Splitting a restaurant bill usually ends with someone doing math on a phone calculator. Scan & Split does it from a single photo.

How it works

AI reads. Code calculates.

Scan: capture the receipt with your phone camera.
Extract: a vision model (Gemini API) reads the items, taxes and total.
Review: you correct any misread items and tick who shared each one.
Calculate: plain Python computes every share, so the amounts are exact.
Share: copy a payment message or send it on WhatsApp.

AI can misread a receipt, so a human review step sits between extraction and calculation. The money math never goes through the AI.

Features
Phone camera scanning, with a native camera fallback on plain http://
Split by item or split equally
Optional tip, taxes and charges included in the shares
Total check: warns if items + tax do not match the receipt total
Payment messages per person and for the group, with optional UPI ID
Expense mode: summary, Markdown table and CSV download
Results shown on a separate page
Tech stack
Python, Flask
Gemini API (google-genai)
HTML, CSS and vanilla JavaScript
Deployed on Vercel


Project structure
.
├── app.py              # Flask routes: /, /extract, /calculate, /expense, /result.html
├── core.py             # Image prep, Gemini extraction, split math, message builders
├── test_app.py         # Tests
├── requirements.txt
├── .env.example
└── templates/
    ├── index.html      # Scan, review and calculate
    └── result.html     # Results page


    
Run locally
bash
git clone https://github.com/bushrajabin/Bill-splitter.git
cd Bill-splitter

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then add your key
python app.py



Roadmap
UPI payment links
Saved groups
Multiple currencies
Receipt history
