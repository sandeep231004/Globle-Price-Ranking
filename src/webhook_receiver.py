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
from threading import Thread
from pathlib import Path

# Import product pipeline
from pipeline import run_pipeline

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

# ============== MESSAGE DEDUPLICATION ==============
# Track processed message IDs to prevent duplicate processing
processed_messages = set()
MAX_PROCESSED_MESSAGES = 1000  # Keep last 1000 message IDs

def is_message_processed(message_id: str) -> bool:
    """Check if message has already been processed"""
    return message_id in processed_messages

def mark_message_processed(message_id: str):
    """Mark message as processed"""
    processed_messages.add(message_id)
    # Keep set size manageable
    if len(processed_messages) > MAX_PROCESSED_MESSAGES:
        # Remove oldest (first) items
        processed_messages.pop()

# ============== DIRECTORY MANAGEMENT ==============
def ensure_directories():
    """Ensure all required directories exist"""
    directories = [
        'downloads',
        'extracted_frames',
        'extraction_results',
        'pipeline_results'
    ]
    for dir_name in directories:
        Path(dir_name).mkdir(exist_ok=True)
    logger.info("✅ All directories ensured")

# Initialize directories on startup
ensure_directories()

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

        # Store CDN URL (pipeline will handle download)
        if cdn_url and 'lookaside.fbsbx.com' in cdn_url:
            logger.info(f"🎯 Found CDN URL: {cdn_url[:100]}...")
            product_data.cdn_url = cdn_url
        else:
            logger.warning(f"⚠️ No CDN URL found for attachment type: {attachment_type}")
    return product_data

def send_message_to_user(recipient_id: str, message_text: str) -> bool:
    """Send a message to Instagram user"""
    logger.info(f"💬 SENDING MESSAGE TO: {recipient_id}")

    if not config.PAGE_ACCESS_TOKEN:
        logger.error("❌ Cannot send message - PAGE_ACCESS_TOKEN missing")
        return False

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
            return True
        else:
            logger.error(f"❌ Message send failed: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.error(f"❌ Message send error: {e}")
        return False

def send_acknowledgment(recipient_id: str, product_data: ProductData):
    """Send acknowledgment message back to user"""
    if product_data.cdn_url:
        message_text = "🔍 Analyzing your product... I'll send you the purchase links shortly!"
    else:
        message_text = "👋 Received your message. Processing..."

    send_message_to_user(recipient_id, message_text)

def send_product_results(recipient_id: str, product_urls: List[str], product_info: Optional[Dict] = None):
    """Send product URLs back to user"""
    logger.info(f"📤 SENDING PRODUCT RESULTS TO: {recipient_id}")
    logger.info(f"   URLs found: {len(product_urls)}")

    if not product_urls:
        message_text = "Sorry, I couldn't find purchase links for this product. Try sending another product!"
        send_message_to_user(recipient_id, message_text)
        return

    # Get product name if available
    product_name = ""
    if product_info and product_info.get('products'):
        first_product = product_info['products'][0]
        brand = first_product.get('brand', '')
        product = first_product.get('product', '')

        if brand and product:
            product_name = f" for {brand} {product}"
        elif brand:
            product_name = f" for {brand}"
        elif product:
            product_name = f" for {product}"

    # Send simple header
    header = f"Here are purchase links{product_name}:\n\n"

    # Send URLs in one message if possible (Instagram allows ~2000 characters)
    # Otherwise send in batches
    urls_per_message = 10  # Send more URLs per message
    total_messages = (len(product_urls) + urls_per_message - 1) // urls_per_message

    for i in range(0, len(product_urls), urls_per_message):
        batch_urls = product_urls[i:i+urls_per_message]
        batch_number = (i // urls_per_message) + 1

        # Simple message format
        if i == 0 and total_messages == 1:
            # All URLs fit in one message
            message_text = header
        elif i == 0:
            # First batch of multiple
            message_text = header
        else:
            # Subsequent batches
            message_text = f"More links (Part {batch_number}):\n\n"

        # Add URLs without numbering (cleaner look)
        for url in batch_urls:
            message_text += f"{url}\n\n"

        send_message_to_user(recipient_id, message_text.strip())

    logger.info(f"✅ All product URLs sent to {recipient_id} in {total_messages} message(s)")

def process_pipeline_in_background(cdn_url: str, session_id: str, sender_id: str):
    """
    Run product pipeline in background thread
    This prevents webhook timeout and allows async processing

    IMPORTANT: sender_id is passed as parameter to ensure correct user receives results

    Args:
        cdn_url: CDN URL to download (pipeline will handle download)
        session_id: Unique session ID for tracking
        sender_id: Instagram user ID to send results to
    """
    logger.info(f"🚀 Starting pipeline in background for session: {session_id}")
    logger.info(f"   👤 Sender ID (will receive results): {sender_id}")

    try:
        # Run full pipeline (download → extract → search)
        result = run_pipeline(
            cdn_url=cdn_url,
            session_id=session_id,
            sender_id=sender_id,
            save_results=True
        )

        # Verify sender_id from result matches our sender_id
        result_sender_id = result.get('sender_id')
        if result_sender_id != sender_id:
            logger.error(f"⚠️ SENDER ID MISMATCH: Expected {sender_id}, got {result_sender_id}")

        # Check if pipeline succeeded
        if result.get('completed_successfully'):
            product_urls = result.get('product_urls', [])
            product_info = result.get('product_info')

            logger.info(f"✅ Pipeline completed successfully for sender: {sender_id}")
            logger.info(f"   Product URLs found: {len(product_urls)}")
            logger.info(f"   🎯 Sending results to: {sender_id}")

            # Send results to user (sender_id ensures correct recipient)
            send_product_results(sender_id, product_urls, product_info)
        else:
            # Pipeline failed
            errors = result.get('errors', [])
            logger.error(f"❌ Pipeline failed for {sender_id}: {errors}")

            # Send error message to user
            send_message_to_user(
                sender_id,
                "😔 Sorry, I encountered an issue while processing your product. Please try again!"
            )

    except Exception as e:
        logger.error(f"❌ Pipeline exception for {sender_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())

        # Send error message to user
        send_message_to_user(
            sender_id,
            "😔 Sorry, something went wrong. Please try again later!"
        )

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
                            message_id = message.get('mid', 'unknown')

                            # Skip echo and read receipts
                            if message.get('is_echo') or 'read' in event:
                                continue

                            # Check for duplicate message
                            if is_message_processed(message_id):
                                logger.info(f"⏭️ Skipping duplicate message: {message_id}")
                                continue

                            # Mark message as processed
                            mark_message_processed(message_id)

                            logger.info(f"🔍 Processing NEW message from: {sender_id}")
                            logger.info(f"   Message ID: {message_id}")

                            # Process message and download media
                            product_data = process_instagram_message(event)

                            # Log results
                            logger.info("=" * 50)
                            logger.info("🔍 PROCESSING RESULTS:")
                            logger.info(f"   👤 Sender: {product_data.sender_id}")
                            logger.info(f"   📧 Message ID: {message_id}")
                            logger.info(f"   🎥 Type: {product_data.post_type}")
                            logger.info(f"   🔗 CDN URL: {product_data.cdn_url[:80] if product_data.cdn_url else 'None'}...")
                            logger.info("=" * 50)

                            # Send acknowledgment
                            send_acknowledgment(sender_id, product_data)

                            # If CDN URL found, run pipeline in background
                            if product_data.cdn_url:
                                logger.info(f"🚀 Starting product pipeline for sender: {sender_id}")
                                logger.info(f"   📧 Session ID: {message_id}")
                                logger.info(f"   👤 Results will be sent to: {sender_id}")

                                # Start pipeline in background thread
                                # sender_id is passed as parameter to ensure correct user receives results
                                pipeline_thread = Thread(
                                    target=process_pipeline_in_background,
                                    args=(
                                        product_data.cdn_url,  # CDN URL (pipeline will download)
                                        message_id,            # Unique session ID
                                        sender_id              # ← SENDER ID for routing results
                                    ),
                                    daemon=True,
                                    name=f"Pipeline-{sender_id[:8]}"  # Named thread for debugging
                                )
                                pipeline_thread.start()

                                logger.info(f"✅ Pipeline thread started: {pipeline_thread.name}")
                                logger.info(f"   Thread will send results to sender: {sender_id}")
                            else:
                                logger.warning(f"⚠️ No CDN URL found for {sender_id}")

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
