import os
import json
import hmac
import hashlib
import logging
import requests
from datetime import datetime
from typing import Dict, List, Optional, Any
from flask import Flask, request, Response, jsonify
import re
from dataclasses import dataclass, asdict
from urllib.parse import urlparse, parse_qs

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('instagram_webhook.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============== CONFIGURATION ==============
class Config:
    """Configuration from environment variables"""
    APP_SECRET = os.environ.get('FACEBOOK_APP_SECRET')
    VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')
    PAGE_ACCESS_TOKEN = os.environ.get('PAGE_ACCESS_TOKEN')
    INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get('INSTAGRAM_BUSINESS_ACCOUNT_ID')
    PAGE_ID = os.environ.get('PAGE_ID')

    GRAPH_API_VERSION = os.environ.get('GRAPH_API_VERSION', 'v23.0')
    GRAPH_API_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

    ENABLE_SIGNATURE_VERIFICATION = os.environ.get('ENABLE_SIGNATURE_VERIFICATION', 'true').lower() == 'true'
    DEBUG_MODE = os.environ.get('DEBUG_MODE', 'true').lower() == 'true'

config = Config()

# ============== MEDIA DOWNLOAD FUNCTIONS ==============

def download_instagram_media_from_cdn(cdn_url: str, sender_id: str, message_id: str) -> Optional[Dict[str, str]]:
    """
    Download media (image/video) from Instagram messaging CDN URL
    Returns dict with file_path, media_type, and file_size
    """
    try:
        # Create downloads directory if it doesn't exist
        downloads_dir = os.path.join(os.getcwd(), 'downloads')
        os.makedirs(downloads_dir, exist_ok=True)

        logger.info(f"📥 Starting download from CDN: {cdn_url[:100]}...")

        # Extract asset_id from URL for filename
        parsed = urlparse(cdn_url)
        query_params = parse_qs(parsed.query)
        asset_id = query_params.get('asset_id', ['unknown'])[0]

        # Create unique filename with sender and message info
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_filename = f"media_{sender_id}_{message_id}_{asset_id}_{timestamp}"

        # Make request with proper headers to mimic browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.facebook.com/',
        }

        logger.info(f"   🌐 Making request with headers...")
        response = requests.get(cdn_url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()

        # Determine file extension from Content-Type
        content_type = response.headers.get('content-type', '').lower()
        logger.info(f"   📊 Content-Type: {content_type}")

        if 'image/jpeg' in content_type or 'image/jpg' in content_type:
            file_extension = '.jpg'
            media_type = 'image'
        elif 'image/png' in content_type:
            file_extension = '.png'
            media_type = 'image'
        elif 'image/gif' in content_type:
            file_extension = '.gif'
            media_type = 'image'
        elif 'video/mp4' in content_type:
            file_extension = '.mp4'
            media_type = 'video'
        elif 'video/' in content_type:
            file_extension = '.mp4'
            media_type = 'video'
        else:
            # Default fallback
            file_extension = '.bin'
            media_type = 'unknown'
            logger.warning(f"   ⚠️ Unknown content type: {content_type}, using .bin")

        output_filename = os.path.join(downloads_dir, base_filename + file_extension)

        # Download file in chunks
        logger.info(f"   💾 Downloading to: {output_filename}")
        downloaded_size = 0
        with open(output_filename, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file.write(chunk)
                    downloaded_size += len(chunk)

        file_size = os.path.getsize(output_filename)
        logger.info(f"   ✅ Download complete!")
        logger.info(f"   📦 File: {output_filename}")
        logger.info(f"   📏 Size: {file_size:,} bytes ({file_size / 1024:.2f} KB)")
        logger.info(f"   🎬 Type: {media_type}")

        return {
            'file_path': output_filename,
            'media_type': media_type,
            'file_size': file_size,
            'content_type': content_type
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"   ❌ Download failed (Request Error): {e}")
        return None
    except Exception as e:
        logger.error(f"   ❌ Unexpected download error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

# ============== DATA MODELS ==============
@dataclass
class ProductData:
    """Extracted data from shared posts"""
    timestamp: str
    sender_id: str
    message_id: str
    post_type: str
    shop_urls: List[str]
    raw_webhook_data: Dict
    downloaded_media_path: Optional[str] = None
    media_type: Optional[str] = None
    cdn_url: Optional[str] = None

# ============== UTILITY FUNCTIONS ==============

def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify webhook signature from Instagram/Facebook"""
    if not config.ENABLE_SIGNATURE_VERIFICATION:
        logger.info("Signature verification is disabled")
        return True

    if not signature or not config.APP_SECRET:
        logger.warning("Missing signature or app secret")
        return False

    try:
        if signature.startswith('sha256='):
            signature = signature[7:]

        expected_sig = hmac.new(
            config.APP_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(expected_sig, signature)
        logger.info(f"Signature verification result: {is_valid}")
        return is_valid

    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False

def process_instagram_message(event: Dict) -> ProductData:
    """Process Instagram message and extract data with media download"""

    logger.info("📨 PROCESSING INSTAGRAM MESSAGE")

    # Initialize product data
    product_data = ProductData(
        timestamp=datetime.now().isoformat(),
        sender_id=event.get('sender', {}).get('id', 'unknown'),
        message_id=event.get('message', {}).get('mid', 'unknown'),
        post_type='unknown',
        shop_urls=[],
        raw_webhook_data=event
    )

    message = event.get('message', {})
    attachments = message.get('attachments', [])

    # Process attachments and extract CDN URLs
    for attachment in attachments:
        attachment_type = attachment.get('type')
        payload = attachment.get('payload', {})

        logger.info(f"🔍 Processing attachment type: {attachment_type}")

        # Set post type based on attachment type
        product_data.post_type = attachment_type

        # Extract CDN URL from payload
        # All Instagram media types (ig_reel, share, image, video, story_mention, etc.)
        # have CDN URLs in payload.url
        cdn_url = payload.get('url', '')

        # Log additional metadata for reels
        if attachment_type == 'ig_reel':
            reel_id = payload.get('reel_video_id', '')
            title = payload.get('title', '')
            logger.info(f"📹 Instagram Reel detected - ID: {reel_id}")
            if title:
                logger.info(f"📝 Title: {title[:100]}...")

        # Download media if CDN URL is present
        if cdn_url and 'lookaside.fbsbx.com' in cdn_url:
            logger.info(f"🎯 Found CDN URL: {cdn_url[:100]}...")
            product_data.cdn_url = cdn_url

            # Download the media
            download_result = download_instagram_media_from_cdn(
                cdn_url,
                product_data.sender_id,
                product_data.message_id
            )

            if download_result:
                product_data.downloaded_media_path = download_result['file_path']
                product_data.media_type = download_result['media_type']
                logger.info(f"✅ Media downloaded successfully: {download_result['file_path']}")
            else:
                logger.error("❌ Media download failed")
        else:
            logger.warning(f"⚠️ No CDN URL found for attachment type: {attachment_type}")
    return product_data

def send_acknowledgment(recipient_id: str, product_data: ProductData):
    """Send acknowledgment message back to user"""
    logger.info(f"💬 SENDING ACKNOWLEDGMENT TO: {recipient_id}")

    if not config.PAGE_ACCESS_TOKEN:
        logger.error("❌ Cannot send message - PAGE_ACCESS_TOKEN missing")
        return

    # Prepare message
    if product_data.downloaded_media_path:
        message_text = (
            f"Looking at your product..."
        )
    else:
        message_text = "👋 Received your message. Processing..."

    # Send message
    url = f"{config.GRAPH_API_URL}/me/messages"
    payload = {
        'recipient': {'id': recipient_id},
        'message': {'text': message_text}
    }

    headers = {'Content-Type': 'application/json'}
    params = {'access_token': config.PAGE_ACCESS_TOKEN}

    try:
        response = requests.post(url, json=payload, params=params, headers=headers, timeout=10)

        if response.status_code == 200:
            logger.info(f"✅ Message sent successfully")
        else:
            logger.error(f"❌ Message send failed: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"❌ Message send error: {e}")

# ============== WEBHOOK ENDPOINTS ==============

@app.route('/', methods=['GET'])
def home():
    """Home endpoint for health check"""
    return jsonify({
        'status': '✅ Running',
        'service': 'Instagram Media Downloader',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Webhook verification endpoint"""
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    logger.info(f"Webhook verification: mode={mode}, token_provided={bool(token)}")

    if mode == 'subscribe' and token == config.VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully")
        return Response(challenge, status=200, mimetype='text/plain')

    logger.error("❌ Webhook verification failed")
    return Response('Forbidden', status=403)

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Main webhook handler for Instagram messages"""

    # Verify signature
    raw_data = request.get_data()
    signature = request.headers.get('X-Hub-Signature-256', '')

    if config.ENABLE_SIGNATURE_VERIFICATION:
        if not verify_webhook_signature(raw_data, signature):
            logger.error("❌ Invalid webhook signature")
            return Response('Unauthorized', status=401)

    try:
        data = request.get_json()

        if config.DEBUG_MODE:
            logger.info("🔍 Full Webhook Data:")
            logger.info(json.dumps(data, indent=2))

        # Process webhook
        webhook_object = data.get('object')

        if webhook_object in ['instagram', 'page']:
            entries = data.get('entry', [])

            for entry in entries:
                if 'messaging' in entry:
                    for event in entry['messaging']:
                        sender_id = event.get('sender', {}).get('id')

                        # Skip our own messages
                        if sender_id == config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
                            continue

                        if 'message' in event:
                            message = event.get('message', {})

                            # Skip echo and read receipts
                            if message.get('is_echo') or 'read' in event:
                                continue

                            logger.info(f"🔍 Processing message from: {sender_id}")

                            # Process message and download media
                            product_data = process_instagram_message(event)

                            # Log results
                            logger.info("=" * 50)
                            logger.info("🔍 PROCESSING RESULTS:")
                            logger.info(f"   👤 Sender: {product_data.sender_id}")
                            logger.info(f"   🎥 Type: {product_data.post_type}")
                            logger.info(f"   💾 Downloaded: {product_data.downloaded_media_path}")
                            logger.info(f"   🎥 Media Type: {product_data.media_type}")
                            logger.info("=" * 50)

                            # Send acknowledgment
                            send_acknowledgment(sender_id, product_data)

        return Response('EVENT_RECEIVED', status=200)

    except Exception as e:
        logger.error(f"❌ Webhook processing error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return Response('EVENT_RECEIVED', status=200)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'configs_set': {
            'page_access_token': bool(config.PAGE_ACCESS_TOKEN),
            'app_secret': bool(config.APP_SECRET),
            'page_id': bool(config.PAGE_ID),
            'instagram_business_id': bool(config.INSTAGRAM_BUSINESS_ACCOUNT_ID)
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting Instagram Media Downloader on port {port}")
    logger.info(f"🔍 Debug Mode: {config.DEBUG_MODE}")
    logger.info(f"🔍 Signature Verification: {config.ENABLE_SIGNATURE_VERIFICATION}")
    app.run(host='0.0.0.0', port=port, debug=config.DEBUG_MODE)

