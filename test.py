import msal
import requests
import time

CLIENT_ID = "8745c2eb-76d4-4a85-8325-3098790b1bd3"
TENANT_ID = "5d471751-9675-428d-917b-70f44f9630b0"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = [
    "https://graph.microsoft.com/Chat.Read",
    "https://graph.microsoft.com/User.Read"
]

CHAT_ID = "19:32315b71-9054-4938-82dc-cf9db9ff1d12_356442bb-1f98-4ade-9b5f-58233770c2b1@unq.gbl.spaces"

app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
result = app.acquire_token_interactive(scopes=SCOPES)
if "access_token" not in result:
    print("❌ Failed to acquire token:", result.get("error_description"))
    exit()

access_token = result["access_token"]
headers = {
    "Authorization": f"Bearer {access_token}"
}

def fetch_all_messages(chat_id):
    url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
    messages = []

    while url:
        print(f"📥 Fetching: {url}")
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"❌ Failed to fetch messages: {response.status_code}")
            print(response.json())
            break

        data = response.json()
        messages.extend(data.get("value", []))
        url = data.get("@odata.nextLink")  

        time.sleep(0.5)  
    return messages


all_messages = fetch_all_messages(CHAT_ID)
print(f"\n✅ Total messages fetched: {len(all_messages)}")

for msg in all_messages:
    timestamp = msg.get("createdDateTime", "N/A")
    sender = msg.get("from", {}).get("user", {}).get("displayName", "System/Unknown")
    content = msg.get("body", {}).get("content", "").strip()
    print(f"[{timestamp}] {sender}: {content}\n")
