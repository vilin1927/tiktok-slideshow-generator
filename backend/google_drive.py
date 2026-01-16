"""
Google Drive Integration Module
Handles folder creation, file uploads, and sharing via Google Drive API

Uses OAuth authentication (user grants access once, token is saved).
"""
import os
import pickle
import time
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()

from logging_config import get_logger, get_request_logger

logger = get_logger('drive')

# OAuth credentials from .env
CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

# Parent folder ID (optional - uploads to root if not set)
PARENT_FOLDER_ID = os.getenv('PARENT_FOLDER_ID')

# Token file path (stores OAuth token for reuse)
TOKEN_PATH = os.path.join(os.path.dirname(__file__), '..', 'credentials', 'oauth_token.pickle')

# Scopes required for Drive API
SCOPES = ['https://www.googleapis.com/auth/drive.file']


class GoogleDriveError(Exception):
    """Custom exception for Google Drive errors"""
    pass


def _get_credentials():
    """Get OAuth credentials, prompting for authorization if needed."""
    creds = None

    # Load existing token if available
    if os.path.exists(TOKEN_PATH):
        logger.debug("Loading OAuth token from file")
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)

    # If no valid credentials, get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("OAuth token expired, refreshing...")
            creds.refresh(Request())
            logger.debug("OAuth token refreshed")
        else:
            if not CLIENT_ID or not CLIENT_SECRET:
                logger.error("Missing OAuth credentials in .env")
                raise GoogleDriveError(
                    'GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set in .env'
                )

            logger.info("No OAuth token found, starting authorization flow...")
            # Create OAuth flow from client secrets
            client_config = {
                "installed": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"]
                }
            }

            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=8080)
            logger.info("OAuth authorization complete")

        # Save credentials for next time
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(creds, token)
        logger.debug("OAuth token saved")

    return creds


def _get_service():
    """Initialize and return Google Drive service using OAuth."""
    credentials = _get_credentials()
    return build('drive', 'v3', credentials=credentials)


def create_folder(folder_name: str, parent_id: Optional[str] = None) -> str:
    """
    Create a folder in Google Drive

    Args:
        folder_name: Name of the folder to create
        parent_id: Optional parent folder ID (defaults to PARENT_FOLDER_ID)

    Returns:
        Folder ID
    """
    service = _get_service()

    # Use PARENT_FOLDER_ID if no parent specified
    actual_parent = parent_id or PARENT_FOLDER_ID

    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }

    if actual_parent:
        file_metadata['parents'] = [actual_parent]

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


def upload_files_parallel(
    file_paths: List[str],
    folder_id: str,
    max_workers: int = 5,
    request_id: str = None
) -> Tuple[int, int]:
    """
    Upload multiple files to Google Drive in parallel.

    Args:
        file_paths: List of local file paths to upload
        folder_id: Google Drive folder ID
        max_workers: Maximum parallel uploads (default: 5)
        request_id: Optional request ID for logging

    Returns:
        Tuple of (successful_uploads, failed_uploads)
    """
    req_logger = get_request_logger('drive', request_id) if request_id else logger

    # Filter to only existing files
    existing_files = [f for f in file_paths if os.path.exists(f)]
    if not existing_files:
        return (0, 0)

    successful = 0
    failed = 0

    def upload_single(file_path: str) -> bool:
        try:
            upload_file(file_path, folder_id)
            return True
        except GoogleDriveError as e:
            req_logger.warning(f"Failed to upload {os.path.basename(file_path)}: {e}")
            return False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(upload_single, f): f for f in existing_files}

        for future in as_completed(futures):
            if future.result():
                successful += 1
            else:
                failed += 1

    req_logger.info(f"Parallel upload complete: {successful}/{len(existing_files)} succeeded")
    return (successful, failed)


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
    audio_path: Optional[str] = None,
    request_id: str = None
) -> dict:
    """
    Upload all slideshow output to Google Drive

    Args:
        output_dir: Local directory containing files
        folder_name: Name for the Google Drive folder
        images: List of image file paths
        audio_path: Optional path to audio file
        request_id: Optional request ID for logging

    Returns:
        dict with folder_id, folder_link, and uploaded file info
    """
    log = get_request_logger('drive', request_id) if request_id else logger
    start_time = time.time()

    log.info(f"Starting upload: {len(images)} images to folder '{folder_name}'")

    # Create main folder
    folder_id = create_folder(folder_name)
    log.debug(f"Created folder: {folder_id}")

    # Make it public
    set_folder_public(folder_id)
    log.debug("Folder set to public")

    result = {
        'folder_id': folder_id,
        'folder_link': get_folder_link(folder_id),
        'uploaded_images': [],
        'audio_file': None
    }

    # Upload images
    for i, img_path in enumerate(images):
        if os.path.exists(img_path):
            try:
                file_size = os.path.getsize(img_path)
                log.debug(f"Uploading image {i+1}/{len(images)}: {os.path.basename(img_path)} ({file_size/1024:.1f}KB)")
                file_id = upload_file(img_path, folder_id)
                result['uploaded_images'].append({
                    'local_path': img_path,
                    'file_id': file_id
                })
            except GoogleDriveError as e:
                log.warning(f"Failed to upload {img_path}: {e}")

    # Upload audio if provided
    if audio_path and os.path.exists(audio_path):
        try:
            log.debug(f"Uploading audio: {os.path.basename(audio_path)}")
            file_id = upload_file(audio_path, folder_id)
            result['audio_file'] = {
                'local_path': audio_path,
                'file_id': file_id
            }
            log.debug("Audio uploaded")
        except GoogleDriveError as e:
            log.warning(f"Failed to upload audio: {e}")

    elapsed = time.time() - start_time
    log.info(f"Upload complete in {elapsed:.1f}s: {len(result['uploaded_images'])} images, audio={'yes' if result['audio_file'] else 'no'}")
    log.info(f"Folder link: {result['folder_link']}")

    return result


# For testing
if __name__ == '__main__':
    print('Google Drive Integration Module (OAuth)')
    print(f'Client ID set: {bool(CLIENT_ID)}')
    print(f'Client Secret set: {bool(CLIENT_SECRET)}')
    print(f'Parent folder ID: {PARENT_FOLDER_ID}')
    print(f'Token exists: {os.path.exists(TOKEN_PATH)}')

    # Test connection
    try:
        service = _get_service()
        print('OAuth connection: OK')
    except GoogleDriveError as e:
        print(f'Connection error: {e}')