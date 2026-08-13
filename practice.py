import base64
import datetime
import json
import os.path
import re
from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def extract_body_text(payload):
    """Recursively extract plain text or HTML body from Gmail message payload."""
    if not payload:
        return ""

    body_data = payload.get("body", {}).get("data")
    mime_type = payload.get("mimeType", "")

    if body_data and (mime_type in ["text/plain", "text/html"] or not mime_type):
        try:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
        except Exception:
            pass

    parts = payload.get("parts", [])
    for part in parts:
        p_mime = part.get("mimeType", "")
        if p_mime in ["text/plain", "text/html"]:
            text = extract_body_text(part)
            if text:
                return text

    for part in parts:
        text = extract_body_text(part)
        if text:
            return text

    return ""


def clean_html_to_text(html_or_text):
    """Clean HTML tags and compress whitespace."""
    if not html_or_text:
        return ""
    soup = BeautifulSoup(html_or_text, 'lxml')
    raw_txt = soup.get_text().strip()
    clean_txt = re.sub(r'\s+', ' ', raw_txt)
    return clean_txt


def extract_party_and_vpa(clean_txt, txn_type):
    """Extract vendor/merchant name and VPA accurately across all HDFC alert formats."""
    body = re.split(r"(?:if you did not authorize|please call|sms block|\bref\.?\s*no|call \d+)", clean_txt, flags=re.IGNORECASE)[0]

    vpa = None
    party = None

    vpa_match = re.search(r'\b[\w\.-]+@[\w\.-]+\b', body)
    if vpa_match:
        vpa = vpa_match.group(0).strip()

    sender_match = re.search(r"Sender:\s*([A-Za-z0-9\.\&\s\-\_]+?)(?:\s*\(VPA:\s*[\w\.-]+@[\w\.-]+\)|\s*on|\s*via|\.|$)", body, re.IGNORECASE)
    if sender_match and sender_match.group(1).strip():
        party = sender_match.group(1).strip()
        return party, vpa

    autopay_match = re.search(r"(?:payment for|payment of|for)\s+([A-Za-z0-9\.\&\s\-\_]+?)(?:\s*,\s*set|\s+was|\s+on|\.|$)", body, re.IGNORECASE)
    if any(k in body.lower() for k in ["autopay", "e-mandate", "auto payment"]) and autopay_match:
        party = autopay_match.group(1).strip()
        return party, vpa

    vpa_name_match = re.search(r"(?:towards|to)\s+VPA\s+[\w\.-]+@[\w\.-]+\s+(?:\((.*?)\)|([A-Za-z0-9\.\&\s\-\_]+?))(?:\s+on|\s+via|\s+through|\s+ref|\.|$)", body, re.IGNORECASE)
    if vpa_name_match:
        party = (vpa_name_match.group(1) or vpa_name_match.group(2) or "").strip()
        if party:
            return party, vpa

    towards_match = re.search(r"(?:towards|to)\s+(?:VPA\s+[\w\.-]+@[\w\.-]+\s+)?(?:VPA\s+)?([A-Za-z0-9\.\&\s\-\_]+?)(?:\s+on|\s+via|\s+through|\s+ref|\.|$)", body, re.IGNORECASE)
    if towards_match:
        cand = towards_match.group(1).strip()
        if "account" in cand.lower():
            cand_sub = re.search(r"(?:to|towards)\s+(?:VPA\s+[\w\.-]+|\s+)?(.*)", cand, re.IGNORECASE)
            if cand_sub:
                cand = cand_sub.group(1).strip()
        if cand and cand.lower() not in ["vpa", "account"]:
            party = cand
            return party, vpa

    if txn_type == "credit":
        credit_match = re.search(r"(?:from|by)\s+(?:VPA\s+[\w\.-]+@[\w\.-]+\s+)?(?:VPA\s+)?([A-Za-z0-9\.\&\s\-\_]+?)(?:\s+on|\s+via|\s+through|\s+ref|\.|$)", body, re.IGNORECASE)
        if credit_match:
            cand = credit_match.group(1).strip()
            if cand and cand.lower() not in ["vpa", "account"]:
                party = cand
                return party, vpa

    return party, vpa


def main():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build("gmail", "v1", credentials=creds)
        results = service.users().messages().list(userId='me', q='alerts@hdfcbank.net').execute()
        messages = results.get("messages", [])

        if not messages:
            print("No messages found.")
            return

        transactions = []
        for message in messages:
            msg_id = message['id']
            full_message = service.users().messages().get(userId='me', id=msg_id).execute()
            headers = full_message.get("payload", {}).get("headers", [])
            from_header = next((h["value"] for h in headers if h["name"] == "From"), "")

            payload = full_message.get('payload', {})
            raw_body = extract_body_text(payload)
            if not raw_body:
                continue

            clean_txt = clean_html_to_text(raw_body)
            text_lower = clean_txt.lower()

            txn_type = "unknown"
            direction = None
            if any(k in text_lower for k in ["debited", "paid to", "sent to", "transferred to"]):
                txn_type = "debit"
                direction = "To"
            elif any(k in text_lower for k in ["credited", "received from", "refund", "payment received"]):
                txn_type = "credit"
                direction = "From"

            amount_match = re.search(r"(?:Rs\.?|INR)\s*([\d,]+\.?\d*)", clean_txt, re.IGNORECASE)
            amount = float(amount_match.group(1).replace(",", "")) if amount_match else None

            date_match = re.search(r'on\s?(\d{2}[-/]\d{2}[-/]\d{2,4})', clean_txt, re.IGNORECASE)
            date = date_match.group(1) if date_match else None

            party, vpa = extract_party_and_vpa(clean_txt, txn_type)

            bank = None
            if from_header:
                match = re.search(r'@([\w-]+)', from_header)
                if match:
                    bank = match.group(1).upper()

            txn_id = f"TXN_{date.replace('-', '_') if date else 'NODATE'}_{msg_id}"

            transaction = {
                "id": txn_id,
                "date": date,
                "amount": amount,
                "type": txn_type,
                "direction": direction,
                "party": party,
                "vpa": vpa,
                "bank": bank,
                "raw_text": clean_txt[:200],
                "category": None,
                "sub_category": None,
                "source": from_header,
                "notes": None
            }
            transactions.append(transaction)

        print(f"Processed {len(transactions)} transactions.")
        with open("transactions.json", "w") as f:
            json.dump(transactions, f, indent=4)

    except HttpError as error:
        print(f"An error occurred: {error}")


if __name__ == "__main__":
    main()