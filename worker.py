import os
import time
import base64
from pymongo import MongoClient
from bson.objectid import ObjectId
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import app

MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.phishing_detector_db
scan_queue_collection = db.scan_queue

print("Background worker started. Waiting for scan jobs...")

def process_queue():
    while True:
        job = scan_queue_collection.find_one_and_update(
            {'status': 'pending'},
            {'$set': {'status': 'processing'}}
        )

        if job:
            try:
                print(f"Processing job {job['_id']}...")
                creds = Credentials(**job['user_credentials'])
                service = build('gmail', 'v1', credentials=creds)
                
                msg = service.users().messages().get(userId='me', id=job['message_id'], format='full').execute()
                payload = msg.get('payload', {})
                headers = payload.get('headers', [])
                
                subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'No Sender')
                body = ""
                total_score = 0
                reasons = []

                parts = [payload]
                if 'parts' in payload: parts.extend(payload['parts'])
                for part in parts:
                    if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', 'ignore')
                    
                    if part.get('filename'):
                        att_id = part.get('body', {}).get('attachmentId')
                        if att_id:
                            attachment = service.users().messages().attachments().get(userId='me', messageId=msg['id'], id=att_id).execute()
                            file_data = base64.urlsafe_b64decode(attachment['data'])
                            malware_score, malware_reasons = app.analyze_attachment(file_data, part.get('filename'))
                            total_score += malware_score
                            reasons.extend(malware_reasons)
                
                phishing_score, phishing_reasons = app.calculate_phishing_score(sender, subject, body)
                total_score += phishing_score
                reasons.extend(phishing_reasons)

                scan_queue_collection.update_one({'_id': job['_id']}, {'$set': {
                    'status': 'completed', 'score': total_score, 'reasons': reasons,
                    'sender': sender, 'subject': subject
                }})
                print(f"Job {job['_id']} completed with score {total_score}.")
            except Exception as e:
                print(f"Error processing job {job['_id']}: {e}")
                scan_queue_collection.update_one({'_id': job['_id']}, {'$set': {'status': 'failed', 'error': str(e)}})
        else:
            time.sleep(5)

if __name__ == '__main__':
    process_queue()
