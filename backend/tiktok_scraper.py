"""
TikTok Scraper Module
Uses RapidAPI TikTok Scraper to extract slideshow images and audio
"""
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

from logging_config import get_logger, get_request_logger

logger = get_logger('scraper')

RAPIDAPI_HOST = 'tiktok-scraper7.p.rapidapi.com'


class TikTokScraperError(Exception):
    """Custom exception for TikTok scraping errors"""
    pass


def _validate_image(file_path: str) -> bool:
    """
    Validate that a file is a complete, valid image.

    Args:
        file_path: Path to the image file

    Returns:
        True if valid image, False otherwise
    """
    try:
        with Image.open(file_path) as img:
            # Force load the image data to detect truncation
            img.load()
            # Basic sanity check - image should have reasonable dimensions
            if img.width < 10 or img.height < 10:
                return False
            return True
    except Exception:
        return False


def _get_headers() -> dict:
    """Get RapidAPI headers"""
    api_key = os.getenv('RAPIDAPI_KEY')  # Read dynamically for hot-reload support
    if not api_key:
        raise TikTokScraperError('RAPIDAPI_KEY environment variable not set')
    return {
        'x-rapidapi-key': api_key,
        'x-rapidapi-host': RAPIDAPI_HOST
    }


def extract_video_data(tiktok_url: str, request_id: str = None) -> dict:
    """
    Extract full video/slideshow data from TikTok URL

    Args:
        tiktok_url: Full TikTok video/slideshow URL
        request_id: Optional request ID for logging

    Returns:
        dict with video data including images for slideshows
    """
    log = get_request_logger('scraper', request_id) if request_id else logger
    url = f'https://{RAPIDAPI_HOST}/'
    params = {'url': tiktok_url, 'hd': '1'}

    log.debug(f"RapidAPI request: {tiktok_url[:60]}...")
    start_time = time.time()

    try:
        response = requests.get(url, headers=_get_headers(), params=params, timeout=30)
        elapsed = time.time() - start_time
        log.debug(f"RapidAPI response: status={response.status_code}, time={elapsed:.2f}s")

        response.raise_for_status()
        data = response.json()

        if data.get('code') != 0:
            log.error(f"RapidAPI error: {data.get('msg', 'Unknown error')}")
            raise TikTokScraperError(f"API error: {data.get('msg', 'Unknown error')}")

        video_data = data.get('data', {})
        log.debug(f"Video data keys: {list(video_data.keys())}")
        return video_data

    except requests.exceptions.RequestException as e:
        log.error(f"RapidAPI request failed: {str(e)}")
        raise TikTokScraperError(f'Request failed: {str(e)}')


def extract_slideshow_images(tiktok_url: str) -> list[str]:
    """
    Extract slideshow image URLs from a TikTok slideshow

    Args:
        tiktok_url: TikTok slideshow URL

    Returns:
        List of image URLs
    """
    data = extract_video_data(tiktok_url)

    # Check if it's a slideshow (has images array)
    images = data.get('images', [])
    if images:
        # Return image URLs from the images array
        return [img if isinstance(img, str) else img.get('url', '') for img in images]

    # Alternative: check for image_post_info
    image_post = data.get('image_post_info', {})
    if image_post:
        image_list = image_post.get('images', [])
        return [img.get('display_image', {}).get('url_list', [''])[0] for img in image_list]

    # If no images found, it might be a video not a slideshow
    raise TikTokScraperError('No slideshow images found. This might be a video, not a photo slideshow.')


def extract_audio_url(tiktok_url: str) -> str:
    """
    Extract audio/music URL from TikTok video/slideshow

    Args:
        tiktok_url: TikTok URL

    Returns:
        Audio URL
    """
    data = extract_video_data(tiktok_url)

    # Music can be a direct URL string or a dict
    music = data.get('music')
    if music:
        if isinstance(music, str):
            return music
        elif isinstance(music, dict):
            play_url = music.get('play_url') or music.get('play_url_music')
            if play_url:
                return play_url

    # Alternative: check for audio in different structure
    music_info = data.get('music_info')
    if music_info and isinstance(music_info, dict):
        audio_url = music_info.get('play_url')
        if audio_url:
            return audio_url

    raise TikTokScraperError('Could not extract audio URL')


def download_media(url: str, save_path: str, use_proxy: bool = True, request_id: str = None) -> str:
    """
    Download media file (image or audio) from URL

    Args:
        url: Media URL
        save_path: Path to save the file
        use_proxy: Whether to use proxy for blocked CDNs
        request_id: Optional request ID for logging

    Returns:
        Path to saved file
    """
    log = get_request_logger('scraper', request_id) if request_id else logger
    filename = os.path.basename(save_path)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.tiktok.com/',
    }

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    # Try direct download first (increased timeout for large images)
    try:
        log.debug(f"Direct download: {filename}")
        response = requests.get(url, headers=headers, stream=True, timeout=15)
        response.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        # Validate the downloaded image is complete
        if _validate_image(save_path):
            log.debug(f"Direct download success: {filename}")
            return save_path
        else:
            log.debug(f"Direct download incomplete/corrupt: {filename}")
            os.remove(save_path)  # Remove partial file
            raise requests.exceptions.RequestException("Image validation failed")
    except requests.exceptions.RequestException:
        # Clean up partial file if exists
        if os.path.exists(save_path):
            os.remove(save_path)
        if not use_proxy:
            log.warning(f"Direct download failed, proxy disabled: {filename}")
            raise TikTokScraperError(f'Download failed and proxy disabled')
        log.debug(f"Direct download blocked, trying proxies: {filename}")

    # Try multiple proxies (some fail for certain URLs)
    proxy_urls = [
        f'https://corsproxy.io/?{requests.utils.quote(url, safe="")}',
        f'https://api.allorigins.win/raw?url={requests.utils.quote(url, safe="")}',
        f'https://api.codetabs.com/v1/proxy?quest={requests.utils.quote(url, safe="")}',
    ]

    for i, proxy_url in enumerate(proxy_urls):
        try:
            log.debug(f"Trying proxy {i+1}/3 for: {filename}")
            response = requests.get(proxy_url, headers={'User-Agent': headers['User-Agent']}, timeout=20)
            if response.status_code == 200 and len(response.content) > 1000:
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                # Validate the downloaded image is complete
                if _validate_image(save_path):
                    log.debug(f"Proxy {i+1} success: {filename}")
                    return save_path
                else:
                    log.debug(f"Proxy {i+1} returned incomplete image: {filename}")
                    os.remove(save_path)  # Remove invalid file, try next proxy
        except requests.exceptions.RequestException:
            # Clean up partial file if exists
            if os.path.exists(save_path):
                os.remove(save_path)
            continue

    log.warning(f"All proxies exhausted: {filename}")
    raise TikTokScraperError(f'Download failed - all proxies exhausted')


def scrape_tiktok_slideshow(tiktok_url: str, output_dir: str, request_id: str = None) -> dict:
    """
    Full scraping pipeline: extract and download all slideshow content

    Args:
        tiktok_url: TikTok slideshow URL
        output_dir: Directory to save downloaded files
        request_id: Optional request ID for logging

    Returns:
        dict with paths to downloaded images and audio
    """
    log = get_request_logger('scraper', request_id) if request_id else logger
    start_time = time.time()

    log.info(f"Starting scrape: {tiktok_url[:60]}...")
    os.makedirs(output_dir, exist_ok=True)

    # Get video data once (single API call)
    data = extract_video_data(tiktok_url, request_id)

    result = {
        'images': [],
        'audio': None,
        'metadata': {
            'title': data.get('title', ''),
            'author': data.get('author', {}).get('nickname', ''),
        }
    }

    log.debug(f"Metadata: title='{result['metadata']['title'][:30]}...', author='{result['metadata']['author']}'")

    # Extract image URLs
    images = data.get('images', [])
    if not images:
        image_post = data.get('image_post_info', {})
        if image_post:
            images = image_post.get('images', [])

    log.info(f"Found {len(images)} images to download")

    # Prepare download tasks
    download_tasks = []
    for i, img in enumerate(images):
        if isinstance(img, str):
            img_url = img
        elif isinstance(img, dict):
            img_url = img.get('url') or img.get('display_image', {}).get('url_list', [''])[0]
        else:
            continue
        if img_url:
            save_path = os.path.join(output_dir, f'slide_{i+1}.jpg')
            download_tasks.append((img_url, save_path, i))

    # Download all images in parallel (max 8 concurrent)
    def download_task(task):
        url, path, idx = task
        try:
            download_media(url, path, request_id=request_id)
            return (idx, path)
        except TikTokScraperError:
            return (idx, None)

    log.debug(f"Starting parallel download of {len(download_tasks)} images (8 workers)")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(download_task, task) for task in download_tasks]
        downloaded = {}
        for future in as_completed(futures):
            idx, path = future.result()
            if path:
                downloaded[idx] = path

    # Maintain order
    result['images'] = [downloaded[i] for i in sorted(downloaded.keys())]
    log.info(f"Downloaded {len(result['images'])}/{len(images)} images")

    # Extract and download audio (in parallel with nothing, but fast)
    music = data.get('music')
    audio_url = None
    if music:
        if isinstance(music, str):
            audio_url = music
        elif isinstance(music, dict):
            audio_url = music.get('play_url') or music.get('play_url_music')
    if audio_url:
        log.debug("Downloading audio...")
        audio_path = os.path.join(output_dir, 'audio.mp3')
        try:
            download_media(audio_url, audio_path, request_id=request_id)
            result['audio'] = audio_path
            log.debug("Audio downloaded successfully")
        except TikTokScraperError:
            log.warning("Audio download failed")

    if not result['images']:
        log.error("No slideshow images could be downloaded")
        raise TikTokScraperError('No slideshow images could be downloaded')

    elapsed = time.time() - start_time
    log.info(f"Scrape complete in {elapsed:.1f}s: {len(result['images'])} images, audio={'yes' if result['audio'] else 'no'}")
    return result


# For testing
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        print(f'Testing with URL: {test_url}')
        try:
            data = extract_video_data(test_url)
            print(f'Video data keys: {data.keys()}')
            print(f'Images: {data.get("images", "Not found")}')
            print(f'Music: {data.get("music", "Not found")}')
        except TikTokScraperError as e:
            print(f'Error: {e}')
    else:
        print('Usage: python tiktok_scraper.py <tiktok_url>')
