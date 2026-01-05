"""
Google OAuth Setup Script
Run this once to authenticate and save tokens for Google Drive uploads.

Usage:
    python setup_oauth.py

After running, tokens are saved to .oauth_tokens.json
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

load_dotenv()

# OAuth scopes - need drive.file for uploads
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Token storage path
TOKEN_PATH = Path(__file__).parent / '.oauth_tokens.json'


def get_oauth_config():
    """Get OAuth client config from environment variables"""
    client_id = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
    client_secret = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

    if not client_id or not client_secret:
        print("\nError: Missing OAuth credentials in .env file")
        print("\nPlease add these to your .env file:")
        print("  GOOGLE_OAUTH_CLIENT_ID=your_client_id")
        print("  GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret")
        print("\nTo get these:")
        print("1. Go to https://console.cloud.google.com/apis/credentials")
        print("2. Create OAuth 2.0 Client ID (type: Desktop app)")
        print("3. Copy Client ID and Client Secret")
        return None

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"]
        }
    }


def save_tokens(credentials: Credentials):
    """Save credentials to JSON file"""
    token_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': list(credentials.scopes)
    }

    with open(TOKEN_PATH, 'w') as f:
        json.dump(token_data, f, indent=2)

    print(f"\nTokens saved to: {TOKEN_PATH}")


def main():
    print("=" * 50)
    print("Google Drive OAuth Setup")
    print("=" * 50)

    # Check if already authenticated
    if TOKEN_PATH.exists():
        response = input("\nTokens already exist. Re-authenticate? (y/n): ")
        if response.lower() != 'y':
            print("Keeping existing tokens.")
            return

    # Get OAuth config
    config = get_oauth_config()
    if not config:
        return

    print("\nStarting OAuth flow...")
    print("A browser window will open for Google login.")
    print("Please sign in with the Google account where files should be uploaded.\n")

    try:
        # Run OAuth flow
        flow = InstalledAppFlow.from_client_config(config, SCOPES)
        credentials = flow.run_local_server(port=8080)

        # Save tokens
        save_tokens(credentials)

        print("\n" + "=" * 50)
        print("SUCCESS! OAuth setup complete.")
        print("=" * 50)
        print("\nAll uploads will now go to this Google account's Drive.")
        print("You can restart the Flask server to use the new tokens.")

    except Exception as e:
        print(f"\nError during OAuth flow: {e}")
        print("\nMake sure:")
        print("1. OAuth Client ID is set up as 'Desktop app' type")
        print("2. Google Drive API is enabled in your project")


if __name__ == '__main__':
    main()
