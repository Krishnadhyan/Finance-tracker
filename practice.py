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

            merchant_match = re.search(r'(?:VPA|by|from|to)\s+([A-Za-z0-9@._\s]+?)(?:\s+on|\s+via|\s+through|$)', clean_txt, re.IGNORECASE)
            party = merchant_match.group(1).strip() if merchant_match else None

            vpa_match = re.search(r'\b[\w\.-]+@[\w\.-]+\b', clean_txt)
            vpa = vpa_match.group(0).strip() if vpa_match else None

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