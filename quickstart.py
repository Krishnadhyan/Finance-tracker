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

# If modifying these scopes, delete the file token.json.
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
    # First try to find text/html or text/plain parts
    for part in parts:
        p_mime = part.get("mimeType", "")
        if p_mime in ["text/plain", "text/html"]:
            text = extract_body_text(part)
            if text:
                return text

    # Fallback to any subpart
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


def parse_transaction(clean_txt, msg_id, internal_date_ms, source_email=""):
    """Extract structured transaction data from raw email text."""
    text_lower = clean_txt.lower()

    # 1. Determine transaction type & direction
    txn_type = "unknown"
    direction = None
    if any(k in text_lower for k in ["debited", "paid to", "sent to", "transferred to", "spent"]):
        txn_type = "debit"
        direction = "To"
    elif any(k in text_lower for k in ["credited", "received from", "refund", "payment received", "deposited"]):
        txn_type = "credit"
        direction = "From"

    # 2. Extract Amount (handles Rs. 1,500 or Rs 1500.50 or INR 500)
    amount = None
    amount_match = re.search(r"(?:Rs\.?|INR)\s*([\d,]+\.?\d*)", clean_txt, re.IGNORECASE)
    if amount_match:
        try:
            raw_amt = amount_match.group(1).replace(",", "")
            amount = float(raw_amt)
        except ValueError:
            amount = None

    # 3. Extract Date
    date = None
    date_match = re.search(r"(?:on|date)\s+(\d{2}[-/]\d{2}[-/]\d{2,4})", clean_txt, re.IGNORECASE)
    if date_match:
        date = date_match.group(1)

    # Fallback date from timestamp if missing
    dt_object = datetime.datetime.fromtimestamp(internal_date_ms / 1000.0)
    if not date:
        date = dt_object.strftime("%d-%m-%y")

    time_string = dt_object.strftime("%I:%M %p")
    day_of_week = dt_object.strftime("%A")

    # 4. Extract VPA & Merchant / Party
    vpa_match = re.search(r'\b[\w\.-]+@[\w\.-]+\b', clean_txt)
    vpa = vpa_match.group(0).strip() if vpa_match else None

    party = None
    if txn_type == "debit":
        merchant_match = re.search(r"to\s+(?:VPA\s+[\w\.-]+@[\w\.-]+\s+)?(?:VPA\s+)?([A-Za-z0-9\.\&\s\-\_]+?)(?:\s+on|\s+via|\s+through|\s+ref|\.|$)", clean_txt, re.IGNORECASE)
        if merchant_match:
            party = merchant_match.group(1).strip()
    elif txn_type == "credit":
        merchant_match = re.search(r"(?:from|by)\s+(?:VPA\s+[\w\.-]+@[\w\.-]+\s+)?(?:VPA\s+)?([A-Za-z0-9\.\&\s\-\_]+?)(?:\s+on|\s+via|\s+through|\s+ref|\.|$)", clean_txt, re.IGNORECASE)
        if merchant_match:
            party = merchant_match.group(1).strip()

    # Clean up party string if it captured unnecessary prefix like "account 4152 to VPA ..."
    if party and "account" in party.lower():
        party_sub = re.search(r"to\s+(?:VPA\s+[\w\.-]+\s+)?(.*)", party, re.IGNORECASE)
        if party_sub:
            party = party_sub.group(1).strip()

    # 5. Extract Bank Name
    bank = None
    if source_email:
        bank_match = re.search(r"@([\w-]+)", source_email)
        if bank_match:
            bank = bank_match.group(1).upper()

    # Clean summary text snippet
    summary = re.sub(r'\s+', ' ', clean_txt).strip()

    # Unique transaction ID using msg_id
    txn_id = f"TXN_{date.replace('/', '_').replace('-', '_')}_{msg_id}"

    return {
        "id": txn_id,
        "date": date,
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

        # Save to transactions.json
        output_file = "transactions.json"
        with open(output_file, "w") as f:
            json.dump(transactions, f, indent=4)
        print(f"\nSaved {len(transactions)} parsed transactions to {output_file}")

    except HttpError as error:
        print(f"An error occurred with Gmail API: {error}")


if __name__ == "__main__":
    main()