from bs4 import BeautifulSoup
import re
import base64
import os.path
import datetime

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
    # Use OR (must be capitalized) to check for either sender
    query = 'from:alerts@hdfcbank.net OR from:alerts@hdfcbank.bank.in'
    all_messages=[]
    page_token=None

    while True:
    
     results = service.users().messages().list(
        userId='me',
        q=query, 
        maxResults=500
     ).execute()
     messages = results.get("messages", [])
     all_messages.extend(messages)
     page_token=results.get("nextPageToken")
     if not page_token:
        break

    if not all_messages:
      print("No messages found.")
    else:
       print(f"Successfully retrieved a total of {len(all_messages)} messages!")
    print("messages:")
    
    
    for message in messages:
      
      
      msg_id= message['id']
      full_message= service.users().messages().get(userId= 'me',id=msg_id).execute()
      internal_date_ms = int(full_message['internalDate'])
    
     # 2. Convert to seconds
      timestamp_sec = internal_date_ms / 1000.0
    
     # 3. Create a datetime object
      dt_object = datetime.datetime.fromtimestamp(timestamp_sec)
    
     # 4. Format it to look nice (e.g., "08:35 AM")
      time_string = dt_object.strftime("%I:%M %p")
      day_of_week = dt_object.strftime("%A")
      payload= full_message.get('payload',{})
      parts= payload.get('parts',[])
      encoded_data = None
      
      
      # 1. If it's a simple, single-part email:
      if 'parts' not in payload:
          encoded_data = payload.get('body', {}).get('data')
      
      # 2. If it's a multi-part email, hunt for the text:
      else:
          for part in payload['parts']:
              mime_type = part.get('mimeType')
              
              # If we find text or HTML, grab the data and stop looking!
              if mime_type == 'text/plain' or mime_type == 'text/html':
                  encoded_data = part.get('body', {}).get('data')
                  if encoded_data: 
                      break 
                  
              # 3. Sometimes emails have parts inside parts (nested)
              elif 'parts' in part:
                  for subpart in part['parts']:
                      if subpart.get('mimeType') == 'text/plain' or subpart.get('mimeType') == 'text/html':
                          encoded_data = subpart.get('body', {}).get('data')
                          if encoded_data:
                              break
                              
              # If we found the data in a nested part, break the main loop too
              if encoded_data:
                  break
      if encoded_data:
        decoded_data = base64.urlsafe_b64decode(encoded_data)
        readable_data = decoded_data.decode('utf-8')  
        soup= BeautifulSoup(readable_data, 'lxml')
        raw_txt= soup.get_text().strip()
        clean_txt= re.sub(r'\s+',' ',raw_txt)
        
        amount=None
        merchant=None
        date=None
        txn_type= "Unknown"


        

        #find the txn type  
        if any(k in clean_txt.lower() for k in ["debited","paid to","sent to","transferred to"]):
          txn_type= "debit"
          direction = "To"
        elif any(k in clean_txt.lower() for k in  ["credited","received from", "refund", "payment received"]):
          txn_type= "credit"
          direction= "From"
        else:
          txn_type= "unknown"                        
        


        if txn_type=="debit":
          merchant_match = re.search(r"to\s+(?:VPA\s+)?(.*?)\s+on", clean_txt)
          amount_match = re.search(r"Rs\.?\s*(\d+\.\d{2})", clean_txt)
            
          date_match = re.search(r"on\s+(\d{2}-\d{2}-\d{2})", clean_txt)  
        elif txn_type=="credit":
          merchant_match = re.search(r"by\s+(?:VPA\s+)?(.*?)\s+on", clean_txt)
          amount_match = re.search(r"Rs\.?\s*(\d+\.\d{2})", clean_txt)  
          date_match = re.search(r"on\s+(\d{2}-\d{2}-\d{2})", clean_txt)
        else:
            # If it's unknown/junk, just skip the extraction
            merchant_match = None
            amount_match = None
            date_match = None  

        if merchant_match:
          merchant= merchant_match.group(1).strip()
        if amount_match:
          amount= amount_match.group(1).strip()    
        if date_match:
          date = date_match.group(1).strip()

        if merchant and amount:
          print(f"{date} {day_of_week}   {time_string}: {txn_type.upper()} - {direction} {merchant} - {amount}")    
             
        

        

  except HttpError as error:
    # TODO(developer) - Handle errors from gmail API.
    print(f"An error occurred: {error}")


if __name__ == "__main__":
  main()