from bs4 import BeautifulSoup
import re
import base64
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
  """Shows basic usage of the Gmail API.
  Lists the user's Gmail labels.
  """
  creds = None
  # The file token.json stores the user's access and refresh tokens, and is
  # created automatically when the authorization flow completes for the first
  # time.
  if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          "credentials.json", SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open("token.json", "w") as token:
      token.write(creds.to_json())

  try:
    # Call the Gmail API
    service = build("gmail", "v1", credentials=creds)
    results = service.users().messages().list(userId= 'me',q='alerts@hdfcbank.net is:unread').execute()
    messages = results.get("messages", [])

    if not messages:
      print("No messages found.")
      return
    print("messages:")
    transcations= []


    for message in messages:
      msg_id= message['id']
      full_message= service.users().messages().get(userId= 'me',id=msg_id).execute()
      payload= full_message.get('payload',{})
      parts= payload.get('parts',[])
      encoded_data= None
      
      if 'parts' in payload:
        encoded_data= payload['parts'][0]['body']['data']
      else:
        encoded_data= payload['body']['data']

      if encoded_data:
        decoded_data = base64.urlsafe_b64decode(encoded_data)
        readable_data = decoded_data.decode('utf-8')  
        soup= BeautifulSoup(readable_data, 'lxml')
        raw_txt= soup.get_text().strip()
        clean_txt= re.sub(r'\s+',' ',raw_txt)
        text_lower= clean_txt.lower()
        
        txn_type= "unknown"
        direction = None
        amount=None
        party=None
        date=None
        vpa= None
        
        txn_type= None

        if any(k in text_lower for k in ["debited","paid to","sent to","transferred to"]):
          txn_type= "debit"
          direction = "To"
        elif any(k in text_lower for k in  ["credited","received from", "refund", "payment received"]):
          txn_type= "credit"
          direction= "From"
        else:
          txn_type= "unknown"    
        

        amount_match= re.search(r"Rs\.?\s?(\d+[.,]?\d{0,2})",clean_txt)
        if amount_match:
          amount= amount_match.group(1)

        date_match= re.search(r'on\s?(\d{2}[-/]\d{2}[-/]\d{2,4})',clean_txt)  
        if date_match:
          date=date_match.group(1)

        merchant_match= re.search( r'(?:VPA|by|from)\s+([A-Za-z0-9@._\s]+?)(?:\s+on|\s+via|\s+through|$)',clean_txt)  
        if merchant_match:
          party= merchant_match.group(1).strip()

        vpa_match = re.search(r'\b[\w\.-]+@[\w\.-]+\b', clean_txt)
        vpa = vpa_match.group(0).strip() if vpa_match else None  
        headers = full_message.get("payload", {}).get("headers", [])
        from_header = next((h["value"] for h in headers if h["name"] == "From"), None)
        source = None
        bank = None
        if from_header:
           source = from_header
           match = re.search(r'@([\w-]+)', from_header)
        if match:
           bank = match.group(1).upper()
        if date:
           txn_id = f"TXN_{date.replace('-', '_')}_{msg_id[:5]}"
        else:
          import uuid
          txn_id = f"TXN_{uuid.uuid4().hex[:8]}"  
        category = None
        sub_category = None
        notes = None   
        # Basic cleanup
        clean_txt = re.sub(r'\s+', ' ', raw_txt)
        clean_txt = re.sub(r'(?i)dear customer', '', clean_txt)
        clean_txt = re.sub(r'(?i)(if you did not authorize|for more details|click here|regards|©)', '', clean_txt)
        clean_txt = re.sub(r'(?i)(hdfc bank|icici bank|axis bank|sbi bank)', '', clean_txt)
        clean_txt = clean_txt.strip()
        sentences = clean_txt.split('.')
        cleaned_summary = '. '.join(sentences[:3]).strip()




        transcation={
          "id": txn_id,
          "date": date,
          "amount": float(amount) if amount else None,
          "type": txn_type,
          "direction": direction,
          "party": party,
          "vpa": vpa,
          "bank": bank,
          "raw_text": cleaned_summary,
          "category": category,
          "sub_category": sub_category,
          "source": source,
          "notes": notes
        }  
        transcations.append(transcation)

    for t in transcations:
        print(t)

    import json
    with open("transactions.json","w") as f:
      json.dump(transcations,f,indent=4)



                                
        

      
        

  except HttpError as error:
    # TODO(developer) - Handle errors from gmail API.
    print(f"An error occurred: {error}")


if __name__ == "__main__":
  main()