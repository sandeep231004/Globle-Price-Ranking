"""
Simple CDN Media Downloader
Downloads images/videos from Facebook CDN URLs to local storage
"""

import os
import requests
from datetime import datetime
from urllib.parse import urlparse, parse_qs

def download_from_cdn(cdn_url: str, output_dir: str = "downloads") -> dict:
    """
    Download media from Facebook CDN URL

    Args:
        cdn_url: The Facebook CDN URL (lookaside.fbsbx.com)
        output_dir: Directory to save downloaded files (default: 'downloads')

    Returns:
        dict with download info or None if failed
    """
    try:
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        print("=" * 70)
        print("📥 STARTING DOWNLOAD FROM FACEBOOK CDN")
        print("=" * 70)
        print()

        # Extract asset_id from URL
        parsed = urlparse(cdn_url)
        query_params = parse_qs(parsed.query)
        asset_id = query_params.get('asset_id', ['unknown'])[0]

        print(f"🔗 CDN URL: {cdn_url[:80]}...")
        print(f"🆔 Asset ID: {asset_id}")
        print()

        # Make request with browser-like headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.facebook.com/',
        }

        print("🌐 Making request to CDN...")
        response = requests.get(cdn_url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()

        # Detect media type from Content-Type header
        content_type = response.headers.get('content-type', '').lower()
        print(f"📊 Content-Type: {content_type}")

        # Determine file extension and media type
        if 'image/jpeg' in content_type or 'image/jpg' in content_type:
            extension = '.jpg'
            media_type = 'image'
        elif 'image/png' in content_type:
            extension = '.png'
            media_type = 'image'
        elif 'image/gif' in content_type:
            extension = '.gif'
            media_type = 'image'
        elif 'video/mp4' in content_type:
            extension = '.mp4'
            media_type = 'video'
        elif 'video/' in content_type:
            extension = '.mp4'
            media_type = 'video'
        else:
            extension = '.bin'
            media_type = 'unknown'
            print(f"⚠️  Warning: Unknown content type, saving as .bin")

        # Create filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"cdn_download_{asset_id}_{timestamp}{extension}"
        filepath = os.path.join(output_dir, filename)

        print(f"💾 Downloading to: {filepath}")
        print(f"🎬 Media type: {media_type}")
        print()

        # Download file in chunks with progress
        downloaded = 0
        chunk_size = 8192

        with open(filepath, 'wb') as file:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    file.write(chunk)
                    downloaded += len(chunk)
                    # Show progress every 100KB
                    if downloaded % (100 * 1024) == 0:
                        print(f"   Downloaded: {downloaded / 1024:.2f} KB", end='\r')

        # Get final file size
        file_size = os.path.getsize(filepath)

        print()
        print()
        print("=" * 70)
        print("✅ DOWNLOAD COMPLETE!")
        print("=" * 70)
        print(f"📁 File Path: {filepath}")
        print(f"📏 File Size: {file_size:,} bytes ({file_size / 1024:.2f} KB)")
        print(f"🎬 Media Type: {media_type}")
        print(f"📋 Extension: {extension}")
        print("=" * 70)

        return {
            'success': True,
            'file_path': filepath,
            'file_size': file_size,
            'media_type': media_type,
            'content_type': content_type,
            'filename': filename
        }

    except requests.exceptions.RequestException as e:
        print()
        print("=" * 70)
        print("❌ DOWNLOAD FAILED - REQUEST ERROR")
        print("=" * 70)
        print(f"Error: {e}")
        print()
        print("Possible causes:")
        print("  • URL has expired (CDN URLs are time-limited)")
        print("  • Network connection issue")
        print("  • Invalid URL format")
        print("=" * 70)
        return None

    except Exception as e:
        print()
        print("=" * 70)
        print("❌ DOWNLOAD FAILED - UNEXPECTED ERROR")
        print("=" * 70)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 70)
        return None


def main():
    """
    Main function - Interactive CDN downloader
    """
    print("\n" + "🎥" * 35)
    print("   FACEBOOK CDN MEDIA DOWNLOADER")
    print("🎥" * 35)
    print()
    print("This tool downloads images/videos from Facebook CDN URLs")
    print("to your local machine.")
    print()

    while True:
        print("\n" + "-" * 70)
        print("📋 OPTIONS:")
        print("-" * 70)
        print("1. Download from CDN URL (paste URL)")
        print("2. Use sample URL (from your example)")
        print("3. Exit")
        print("-" * 70)

        choice = input("\nEnter your choice (1-3): ").strip()

        if choice == '1':
            print()
            print("📌 Paste your Facebook CDN URL below:")
            print("   (Should start with: https://lookaside.fbsbx.com/...)")
            print()
            cdn_url = input("URL: ").strip()

            if not cdn_url:
                print("\n❌ Error: Empty URL provided")
                continue

            if 'lookaside.fbsbx.com' not in cdn_url:
                print("\n⚠️  Warning: This doesn't look like a Facebook CDN URL")
                confirm = input("Continue anyway? (y/n): ").strip().lower()
                if confirm != 'y':
                    continue

            print()
            result = download_from_cdn(cdn_url)

            if result:
                print()
                print("🎉 Success! Your file has been downloaded.")

                # Ask if user wants to open the file
                open_file = input("\nOpen the downloaded file? (y/n): ").strip().lower()
                if open_file == 'y':
                    try:
                        os.startfile(result['file_path'])  # Windows
                    except AttributeError:
                        os.system(f"open '{result['file_path']}'")  # macOS
                    except:
                        print(f"Please manually open: {result['file_path']}")

        elif choice == '2':
            # Sample URL from your webhook example
            sample_url = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=18067040134554519&signature=AYfxei3oo1VDlZ6lYGF8gUH24T62lUjIpLENHimlaRutGL0DRfZLXtfQg5qfXpL2V5SOkPHNcXX9sejVClmEx57XG283yW5E85pjRNeJMFj0jnz5I6RALSFsG63isbObX0vC5kBHjHQEg5t0PaOYIJtT00oSiS4S00yLLNPzN0b-vn_MqDcU6DEt7Spq6RtfwrP5NRtpZQ6Qmp7uKaHWVS86egmoevM"

            print()
            print("📌 Using sample URL from your webhook example...")
            print()

            result = download_from_cdn(sample_url)

            if result:
                print()
                print("🎉 Success! Sample file has been downloaded.")

        elif choice == '3':
            print("\n👋 Goodbye!")
            break

        else:
            print("\n❌ Invalid choice. Please enter 1, 2, or 3.")


def quick_download(cdn_url: str):
    """
    Quick download function for command-line usage

    Usage:
        python test_cdn_download.py "your_cdn_url_here"
    """
    result = download_from_cdn(cdn_url)
    return result


if __name__ == "__main__":
    import sys

    # Check if URL provided as command-line argument
    if len(sys.argv) > 1:
        # Direct download mode
        cdn_url = sys.argv[1]
        print("\n🚀 QUICK DOWNLOAD MODE")
        print()

        result = quick_download(cdn_url)

        if result:
            print("\n✅ Download completed successfully!")
            print(f"File saved at: {result['file_path']}")
        else:
            print("\n❌ Download failed. Check error messages above.")
    else:
        # Interactive mode
        main()
