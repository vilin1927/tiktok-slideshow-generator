"""
TikTok Scraper Module
Uses RapidAPI TikTok Scraper to extract slideshow images and audio
"""
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY')
RAPIDAPI_HOST = 'tiktok-scraper7.p.rapidapi.com'


class TikTokScraperError(Exception):
    """Custom exception for TikTok scraping errors"""
    pass


def _get_headers() -> dict:
    """Get RapidAPI headers"""
    if not RAPIDAPI_KEY:
        raise TikTokScraperError('RAPIDAPI_KEY environment variable not set')
    return {
        'x-rapidapi-key': RAPIDAPI_KEY,
        'x-rapidapi-host': RAPIDAPI_HOST
    }


def extract_video_data(tiktok_url: str) -> dict:
    """
    Extract full video/slideshow data from TikTok URL

    Args:
        tiktok_url: Full TikTok video/slideshow URL

    Returns:
        dict with video data including images for slideshows
    """
    url = f'https://{RAPIDAPI_HOST}/'
    params = {'url': tiktok_url, 'hd': '1'}

    try:
        response = requests.get(url, headers=_get_headers(), params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data.get('code') != 0:
            raise TikTokScraperError(f"API error: {data.get('msg', 'Unknown error')}")

        return data.get('data', {})

    except requests.exceptions.RequestException as e:
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


def download_media(url: str, save_path: str, use_proxy: bool = True) -> str:
    """
    Download media file (image or audio) from URL

    Args:
        url: Media URL
        save_path: Path to save the file
        use_proxy: Whether to use proxy for blocked CDNs

    Returns:
        Path to saved file
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.tiktok.com/',
    }

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    # Try direct download first (short timeout)
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=5)
        response.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return save_path
    except requests.exceptions.RequestException:
        if not use_proxy:
            raise TikTokScraperError(f'Download failed and proxy disabled')

    # Try multiple proxies (some fail for certain URLs)
    proxy_urls = [
        f'https://corsproxy.io/?{requests.utils.quote(url, safe="")}',
        f'https://api.allorigins.win/raw?url={requests.utils.quote(url, safe="")}',
        f'https://api.codetabs.com/v1/proxy?quest={requests.utils.quote(url, safe="")}',
    ]

    for proxy_url in proxy_urls:
        try:
            response = requests.get(proxy_url, headers={'User-Agent': headers['User-Agent']}, timeout=15)
            if response.status_code == 200 and len(response.content) > 1000:
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                return save_path
        except requests.exceptions.RequestException:
            continue

    raise TikTokScraperError(f'Download failed - all proxies exhausted')


def scrape_tiktok_slideshow(tiktok_url: str, output_dir: str) -> dict:
    """
    Full scraping pipeline: extract and download all slideshow content

    Args:
        tiktok_url: TikTok slideshow URL
        output_dir: Directory to save downloaded files

    Returns:
        dict with paths to downloaded images and audio
    """
    os.makedirs(output_dir, exist_ok=True)

    # Get video data once (single API call)
    data = extract_video_data(tiktok_url)

    result = {
        'images': [],
        'audio': None,
        'metadata': {
            'title': data.get('title', ''),
            'author': data.get('author', {}).get('nickname', ''),
        }
    }

    # Extract image URLs
    images = data.get('images', [])
    if not images:
        image_post = data.get('image_post_info', {})
        if image_post:
            images = image_post.get('images', [])

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
            download_media(url, path)
            return (idx, path)
        except TikTokScraperError:
            return (idx, None)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(download_task, task) for task in download_tasks]
        downloaded = {}
        for future in as_completed(futures):
            idx, path = future.result()
            if path:
                downloaded[idx] = path

    # Maintain order
    result['images'] = [downloaded[i] for i in sorted(downloaded.keys())]

    # Extract and download audio (in parallel with nothing, but fast)
    music = data.get('music')
    audio_url = None
    if music:
        if isinstance(music, str):
            audio_url = music
        elif isinstance(music, dict):
            audio_url = music.get('play_url') or music.get('play_url_music')
    if audio_url:
        audio_path = os.path.join(output_dir, 'audio.mp3')
        try:
            download_media(audio_url, audio_path)
            result['audio'] = audio_path
        except TikTokScraperError:
            pass

    if not result['images']:
        raise TikTokScraperError('No slideshow images could be downloaded')

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
