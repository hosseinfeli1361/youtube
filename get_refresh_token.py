"""یک‌بار روی کامپیوتر خودت اجرا کن تا YT_REFRESH_TOKEN بگیری.
قبلش فایل client_secret.json (از Google Cloud، نوع Desktop app) رو کنار این فایل بذار."""
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json", scopes=["https://www.googleapis.com/auth/youtube.upload"]
)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nYT_CLIENT_ID     =", creds.client_id)
print("YT_CLIENT_SECRET =", creds.client_secret)
print("YT_REFRESH_TOKEN =", creds.refresh_token)
