"""
Google Drive Integration Module
Handles folder creation, file uploads, and sharing via Google Drive API

Supports two auth methods:
1. OAuth tokens (preferred) - from .oauth_tokens.json
2. Service account (fallback) - from service-account.json
"""
import os
import json
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()

# Path to service account credentials (fallback)
CREDENTIALS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '../credentials/service-account.json')

# Path to OAuth tokens
OAUTH_TOKEN_PATH = Path(__file__).parent / '.oauth_tokens.json'

# Scopes required for Drive API
SCOPES = ['https://www.googleapis.com/auth/drive.file']


class GoogleDriveError(Exception):
    """Custom exception for Google Drive errors"""
    pass


def _load_oauth_tokens() -> Optional[Credentials]:
    """Load OAuth tokens from file and refresh if needed"""
    if not OAUTH_TOKEN_PATH.exists():
        return None

    try:
        with open(OAUTH_TOKEN_PATH, 'r') as f:
            token_data = json.load(f)

        credentials = Credentials(
            token=token_data.get('token'),
            refresh_token=token_data.get('refresh_token'),
            token_uri=token_data.get('token_uri'),
            client_id=token_data.get('client_id'),
            client_secret=token_data.get('client_secret'),
            scopes=token_data.get('scopes')
        )

        # Refresh if expired
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            # Save refreshed tokens
            _save_oauth_tokens(credentials)

        return credentials

    except Exception as e:
        print(f'Warning: Failed to load OAuth tokens: {e}')
        return None


def _save_oauth_tokens(credentials: Credentials):
    """Save refreshed OAuth tokens"""
    token_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': list(credentials.scopes) if credentials.scopes else SCOPES
    }

    with open(OAUTH_TOKEN_PATH, 'w') as f:
        json.dump(token_data, f, indent=2)


def _get_service_account_credentials() -> Optional[service_account.Credentials]:
    """Load service account credentials (fallback method)"""
    creds_path = CREDENTIALS_PATH
    if not os.path.isabs(creds_path):
        creds_path = os.path.join(os.path.dirname(__file__), creds_path)

    if not os.path.exists(creds_path):
        return None

    return service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=SCOPES
    )


def _get_service():
    """
    Initialize and return Google Drive service.
    Tries OAuth tokens first, falls back to service account.
    """
    # Try OAuth tokens first (preferred)
    credentials = _load_oauth_tokens()

    if credentials:
        try:
            service = build('drive', 'v3', credentials=credentials)
            return service
        except Exception as e:
            print(f'Warning: OAuth auth failed, trying service account: {e}')

    # Fall back to service account
    credentials = _get_service_account_credentials()

    if credentials:
        try:
            service = build('drive', 'v3', credentials=credentials)
            return service
        except Exception as e:
            raise GoogleDriveError(f'Service account auth failed: {e}')

    # No credentials available
    raise GoogleDriveError(
        'No Google Drive credentials found. Run "python setup_oauth.py" to authenticate.'
    )


def create_folder(folder_name: str, parent_id: Optional[str] = None) -> str:
    """
    Create a folder in Google Drive

    Args:
        folder_name: Name of the folder to create
        parent_id: Optional parent folder ID

    Returns:
        Folder ID
    """
    service = _get_service()

    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }

    if parent_id:
        file_metadata['parents'] = [parent_id]

    try:
        folder = service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()

        return folder.get('id')

    except Exception as e:
        raise GoogleDriveError(f'Failed to create folder: {str(e)}')


def upload_file(file_path: str, folder_id: str, file_name: Optional[str] = None) -> str:
    """
    Upload a file to Google Drive folder

    Args:
        file_path: Local path to the file
        folder_id: Google Drive folder ID
        file_name: Optional custom file name (defaults to original name)

    Returns:
        File ID
    """
    if not os.path.exists(file_path):
        raise GoogleDriveError(f'File not found: {file_path}')

    service = _get_service()

    # Determine file name and MIME type
    if not file_name:
        file_name = os.path.basename(file_path)

    # Get MIME type based on extension
    ext = os.path.splitext(file_path)[1].lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.mp3': 'audio/mpeg',
        '.mp4': 'video/mp4',
        '.wav': 'audio/wav',
        '.m4a': 'audio/mp4'
    }
    mime_type = mime_types.get(ext, 'application/octet-stream')

    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }

    try:
        media = MediaFileUpload(
            file_path,
            mimetype=mime_type,
            resumable=True
        )

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        return file.get('id')

    except Exception as e:
        raise GoogleDriveError(f'Failed to upload file: {str(e)}')


def set_folder_public(folder_id: str) -> bool:
    """
    Make a folder publicly viewable (anyone with link can view)

    Args:
        folder_id: Google Drive folder ID

    Returns:
        True if successful
    """
    service = _get_service()

    permission = {
        'type': 'anyone',
        'role': 'reader'
    }

    try:
        service.permissions().create(
            fileId=folder_id,
            body=permission
        ).execute()

        return True

    except Exception as e:
        raise GoogleDriveError(f'Failed to set permissions: {str(e)}')


def get_folder_link(folder_id: str) -> str:
    """
    Get shareable link for a folder

    Args:
        folder_id: Google Drive folder ID

    Returns:
        Shareable URL
    """
    return f'https://drive.google.com/drive/folders/{folder_id}'


def upload_slideshow_output(
    output_dir: str,
    folder_name: str,
    images: list[str],
    audio_path: Optional[str] = None
) -> dict:
    """
    Upload all slideshow output to Google Drive

    Args:
        output_dir: Local directory containing files
        folder_name: Name for the Google Drive folder
        images: List of image file paths
        audio_path: Optional path to audio file

    Returns:
        dict with folder_id, folder_link, and uploaded file info
    """
    # Create main folder
    folder_id = create_folder(folder_name)

    # Make it public
    set_folder_public(folder_id)

    result = {
        'folder_id': folder_id,
        'folder_link': get_folder_link(folder_id),
        'uploaded_images': [],
        'audio_file': None
    }

    # Upload images
    for img_path in images:
        if os.path.exists(img_path):
            try:
                file_id = upload_file(img_path, folder_id)
                result['uploaded_images'].append({
                    'local_path': img_path,
                    'file_id': file_id
                })
            except GoogleDriveError as e:
                print(f'Warning: Failed to upload {img_path}: {e}')

    # Upload audio if provided
    if audio_path and os.path.exists(audio_path):
        try:
            file_id = upload_file(audio_path, folder_id)
            result['audio_file'] = {
                'local_path': audio_path,
                'file_id': file_id
            }
        except GoogleDriveError as e:
            print(f'Warning: Failed to upload audio: {e}')

    return result


# For testing
if __name__ == '__main__':
    print('Google Drive Integration Module')
    print(f'Credentials path: {CREDENTIALS_PATH}')

    # Check if credentials exist
    creds_path = CREDENTIALS_PATH
    if not os.path.isabs(creds_path):
        creds_path = os.path.join(os.path.dirname(__file__), creds_path)
    print(f'Credentials exist: {os.path.exists(creds_path)}')
