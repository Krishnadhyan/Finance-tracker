from bs4 import BeautifulSoup
import re
import base64
import os.path
import datetime
import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from db_supabase import upsert_transactions

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


def parse_transaction(clean_txt, msg_id, internal_date_ms, source_email=""):
    """Extract structured transaction data from raw email text."""
    text_lower = clean_txt.lower()

    txn_type = "unknown"
    direction = None
    if any(k in text_lower for k in ["debited", "paid to", "sent to", "transferred to", "spent"]):
        txn_type = "debit"
        direction = "To"
    elif any(k in text_lower for k in ["credited", "received from", "refund", "payment received", "deposited"]):
        txn_type = "credit"
        direction = "From"

    amount = None
    amount_match = re.search(r"(?:Rs\.?|INR)\s*([\d,]+\.?\d*)", clean_txt, re.IGNORECASE)
    if amount_match:
        try:
            raw_amt = amount_match.group(1).replace(",", "")
            amount = float(raw_amt)
        except ValueError:
            amount = None

    date = None
    date_match = re.search(r"(?:on|date)\s+(\d{2}[-/]\d{2}[-/]\d{2,4})", clean_txt, re.IGNORECASE)
    if date_match:
        date = date_match.group(1)

    dt_object = datetime.datetime.fromtimestamp(internal_date_ms / 1000.0)
    if not date:
        date = dt_object.strftime("%d-%m-%y")

    time_string = dt_object.strftime("%I:%M %p")
    day_of_week = dt_object.strftime("%A")

    party, vpa = extract_party_and_vpa(clean_txt, txn_type)

    bank = None
    if source_email:
        bank_match = re.search(r"@([\w-]+)", source_email)
        if bank_match:
            bank = bank_match.group(1).upper()

    summary = re.sub(r'\s+', ' ', clean_txt).strip()
    txn_id = f"TXN_{date.replace('/', '_').replace('-', '_')}_{msg_id}"

    return {
        "id": txn_id,
        "date": date,
        "timestamp_ms": internal_date_ms,
        "day_of_week": day_of_week,
        "time": time_string,
        "amount": amount,
        "type": txn_type,
        "direction": direction,
        "party": party,
        "vpa": vpa,
        "bank": bank,
        "raw_text": summary[:250],
        "source": source_email
    }


def main():
    """Shows basic usage of the Gmail API.
    Lists user's transaction messages and parses financial details.
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build("gmail", "v1", credentials=creds)
        query = 'from:alerts@hdfcbank.net OR from:alerts@hdfcbank.bank.in'
        all_messages = []
        page_token = None

        print("Fetching messages from Gmail API...")
        while True:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=500,
                pageToken=page_token
            ).execute()
            messages = results.get("messages", [])
            all_messages.extend(messages)
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        if not all_messages:
            print("No messages found matching the query.")
            return

        print(f"Successfully retrieved a total of {len(all_messages)} messages!")

        transactions = []
        for index, message in enumerate(all_messages, start=1):
            msg_id = message['id']
            full_message = service.users().messages().get(userId='me', id=msg_id).execute()
            internal_date_ms = int(full_message.get('internalDate', 0))
            
            headers = full_message.get("payload", {}).get("headers", [])
            source_email = next((h["value"] for h in headers if h["name"] == "From"), "")

            payload = full_message.get('payload', {})
            raw_body = extract_body_text(payload)
            
            if not raw_body:
                continue

            clean_txt = clean_html_to_text(raw_body)
            txn = parse_transaction(clean_txt, msg_id, internal_date_ms, source_email)

            if txn["amount"] is not None:
                print(f"[{index}/{len(all_messages)}] {txn['date']} {txn['day_of_week']} {txn['time']}: {txn['type'].upper()} - {txn['direction']} {txn['party'] or 'Unknown'} - Rs.{txn['amount']}")
                transactions.append(txn)

        output_file = "transactions.json"
        with open(output_file, "w") as f:
            json.dump(transactions, f, indent=4)
        print(f"\nSaved {len(transactions)} parsed transactions to {output_file}")

        # Upsert transactions to Supabase Cloud PostgreSQL
        upsert_transactions(transactions)

    except HttpError as error:
        print(f"An error occurred with Gmail API: {error}")


if __name__ == "__main__":
    main()