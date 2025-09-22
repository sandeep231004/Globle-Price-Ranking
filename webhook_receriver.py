import os
import json
import hmac
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import requests
from flask import Flask, request, Response, jsonify
import re
from dataclasses import dataclass, asdict
from urllib.parse import urlparse, parse_qs

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============== CONFIGURATION ==============
class Config:
    """Configuration from environment variables"""
    # Facebook App Credentials
    APP_SECRET = os.environ.get('FACEBOOK_APP_SECRET', '')
    VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', 'Globleisbig21')
    PAGE_ACCESS_TOKEN = os.environ.get('PAGE_ACCESS_TOKEN', '')
    
    # Instagram Business Account
    INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get('INSTAGRAM_BUSINESS_ACCOUNT_ID', '')
    
    # API Configuration
    GRAPH_API_VERSION = os.environ.get('GRAPH_API_VERSION', 'v18.0')
    GRAPH_API_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    
    # Webhook Security
    ENABLE_SIGNATURE_VERIFICATION = os.environ.get('ENABLE_SIGNATURE_VERIFICATION', 'false').lower() == 'true'
    
    # Debug Mode
    DEBUG_MODE = os.environ.get('DEBUG_MODE', 'true').lower() == 'true'

config = Config()

# ============== DATA MODELS ==============
@dataclass
class SharedPost:
    """Data structure for shared Instagram posts"""
    sender_id: str
    timestamp: int
    post_id: Optional[str] = None
    post_url: Optional[str] = None
    caption: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    product_urls: List[str] = None
    raw_data: Dict = None
    
    def __post_init__(self):
        if self.product_urls is None:
            self.product_urls = []

@dataclass
class ExtractedData:
    """Extracted data from shared posts"""
    urls: List[str]
    instagram_post_url: Optional[str]
    caption_text: Optional[str]
    hashtags: List[str]
    mentions: List[str]
    product_tags: List[Dict]
    shop_url: Optional[str]

# ============== UTILITY FUNCTIONS ==============

def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify webhook signature from Instagram/Facebook"""
    if not config.ENABLE_SIGNATURE_VERIFICATION:
        return True
    
    if not signature or not config.APP_SECRET:
        logger.warning("Signature verification skipped - missing signature or app secret")
        return True
    
    try:
        expected_sig = hmac.new(
            config.APP_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        if '=' in signature:
            signature = signature.split('=')[1]
        
        return hmac.compare_digest(expected_sig, signature)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False

def extract_urls_from_text(text: str) -> List[str]:
    """Extract all URLs from text"""
    if not text:
        return []
    
    url_patterns = [
        r'https?://[^\s<>"{}|\\^`\[\]]+',
        r'www\.[^\s<>"{}|\\^`\[\]]+',
        r'[a-zA-Z0-9]+\.com/[^\s]*',
        r'bit\.ly/[^\s]+',
        r'link\.tree/[^\s]+',
        r'linktr\.ee/[^\s]+',
    ]
    
    urls = []
    for pattern in url_patterns:
        found_urls = re.findall(pattern, text, re.IGNORECASE)
        urls.extend(found_urls)
    
    cleaned_urls = []
    for url in urls:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        cleaned_urls.append(url.rstrip('.,;:!?)'))
    
    return list(set(cleaned_urls))

def extract_hashtags_mentions(text: str) -> tuple:
    """Extract hashtags and mentions from text"""
    if not text:
        return [], []
    
    hashtags = re.findall(r'#(\w+)', text)
    mentions = re.findall(r'@(\w+)', text)
    
    return hashtags, mentions

def expand_shortened_url(url: str) -> str:
    """Expand shortened URLs"""
    try:
        response = requests.head(url, allow_redirects=True, timeout=5)
        return response.url
    except:
        return url
        
def get_instagram_media_details(media_id: str) -> Dict:
    """
    Fetch additional media details from Instagram Graph API
    """
    if not config.PAGE_ACCESS_TOKEN:
        return {}
    
    try:
        url = f"{config.GRAPH_API_URL}/{media_id}"
        params = {
            'fields': 'id,media_type,media_url,caption,permalink,thumbnail_url,owner,timestamp',
            'access_token': config.PAGE_ACCESS_TOKEN
        }
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.error(f"Error fetching media details: {e}")
    
    return {}
    
def process_shared_post(message_data: Dict) -> SharedPost:
    """Process and extract all data from a shared Instagram post - FIXED"""
    shared_post = SharedPost(
        sender_id=message_data.get('sender', {}).get('id', ''),
        timestamp=message_data.get('timestamp', 0),
        raw_data=message_data
    )
    
    message = message_data.get('message', {})
    attachments = message.get('attachments', [])
    
    all_urls = []
    payload = None  # Initialize payload variable
    
    # Process attachments
    for attachment in attachments:
        attachment_type = attachment.get('type', '')
        attachment_payload = attachment.get('payload', {})  # Use attachment_payload to avoid confusion
        
        if attachment_type == 'share':
            # Shared post/ad
            shared_post.post_url = attachment_payload.get('url', '')
            if shared_post.post_url:
                all_urls.append(shared_post.post_url)
            
            # Extract description for URLs
            description = attachment_payload.get('description', '')
            if description:
                desc_urls = extract_urls_from_text(description)
                all_urls.extend(desc_urls)
                
            # Save the last payload for later use
            payload = attachment_payload
            
        elif attachment_type == 'image':
            shared_post.media_url = attachment_payload.get('url', '')
            shared_post.media_type = 'image'
            
        elif attachment_type == 'video':
            shared_post.media_url = attachment_payload.get('url', '')
            shared_post.media_type = 'video'
            
        elif attachment_type == 'story_mention':
            shared_post.post_url = attachment_payload.get('url', '')
            if shared_post.post_url:
                all_urls.append(shared_post.post_url)
    
    # Extract text content
    text_content = message.get('text', '')
    if text_content:
        shared_post.caption = text_content
        caption_urls = extract_urls_from_text(text_content)
        all_urls.extend(caption_urls)
    
    # Extract Instagram post ID if available (only if payload was set)
    if payload and 'id' in payload:
        shared_post.post_id = payload.get('id')
    
    # Expand shortened URLs
    expanded_urls = []
    for url in all_urls:
        if any(domain in url for domain in ['bit.ly', 'linktr.ee', 'link.tree']):
            expanded_url = expand_shortened_url(url)
            expanded_urls.append(expanded_url)
        else:
            expanded_urls.append(url)
    
    shared_post.product_urls = list(set(expanded_urls))
    
    return shared_post

def extract_all_data(shared_post: SharedPost) -> ExtractedData:
    """Extract all useful data from the shared post"""
    urls = shared_post.product_urls or []
    caption = shared_post.caption or ""
    
    hashtags, mentions = extract_hashtags_mentions(caption)
    
    product_tags = []
    
    shop_url = None
    for url in urls:
        if 'shop' in url.lower() or 'store' in url.lower() or 'buy' in url.lower():
            shop_url = url
            break
    
    return ExtractedData(
        urls=urls,
        instagram_post_url=shared_post.post_url,
        caption_text=caption,
        hashtags=hashtags,
        mentions=mentions,
        product_tags=product_tags,
        shop_url=shop_url
    )

def send_acknowledgment(recipient_id: str, extracted_data: ExtractedData):
    """Send acknowledgment message back to user"""
    if not config.PAGE_ACCESS_TOKEN:
        logger.warning("Cannot send message - no page access token")
        return
    
    # Don't try to send to test IDs
    if recipient_id.startswith('test_') or recipient_id in ['USER_123456789', 'test_user']:
        logger.info("Skipping message send for test user")
        return
    
    if extracted_data.urls:
        message_text = (
            f"🔍 Great! I found {len(extracted_data.urls)} product link(s) in the shared post.\n"
            f"Analyzing prices across multiple stores...\n"
            f"This will take about 15-30 seconds ⏱️"
        )
    else:
        message_text = (
            "🤔 I couldn't find any product links in this post.\n"
            "Please share a post that contains a product link or shopping tag."
        )
    
    url = f"{config.GRAPH_API_URL}/me/messages"
    payload = {
        'recipient': {'id': recipient_id},
        'message': {'text': message_text}
    }
    headers = {
        'Content-Type': 'application/json'
    }
    params = {
        'access_token': config.PAGE_ACCESS_TOKEN
    }
    
    try:
        response = requests.post(url, json=payload, params=params, headers=headers)
        if response.status_code == 200:
            logger.info(f"Acknowledgment sent to {recipient_id}")
        else:
            logger.error(f"Failed to send message: {response.text}")
    except Exception as e:
        logger.error(f"Error sending acknowledgment: {e}")

# ============== WEBHOOK ENDPOINTS ==============

@app.route('/', methods=['GET'])
def home():
    """Home endpoint for health check"""
    return jsonify({
        'status': 'running',
        'service': 'Instagram Webhook Receiver (Fixed)',
        'timestamp': datetime.utcnow().isoformat(),
        'debug_mode': config.DEBUG_MODE
    })

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Webhook verification endpoint for Facebook"""
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    
    logger.info(f"Webhook verification: mode={mode}, token_match={token==config.VERIFY_TOKEN}")
    
    if mode == 'subscribe' and token == config.VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully")
        return Response(challenge, status=200, mimetype='text/plain')
    
    logger.error("❌ Webhook verification failed")
    return Response('Forbidden', status=403)

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Main webhook handler for Instagram messages - ENHANCED DEBUG VERSION"""

    # Log ALL incoming requests
    logger.info("=== WEBHOOK REQUEST RECEIVED ===")
    logger.info(f"Method: {request.method}")
    logger.info(f"URL: {request.url}")
    logger.info(f"Headers: {dict(request.headers)}")
    logger.info(f"Remote Address: {request.remote_addr}")
    logger.info(f"Content Length: {request.content_length}")

    # Get raw data first
    raw_data = request.get_data()
    logger.info(f"Raw Data Length: {len(raw_data)}")
    logger.info(f"Raw Data Preview: {raw_data[:500]}...")

    # Verify signature if enabled
    signature = request.headers.get('X-Hub-Signature-256', '')
    logger.info(f"Signature present: {bool(signature)}")
    logger.info(f"Signature verification enabled: {config.ENABLE_SIGNATURE_VERIFICATION}")

    if config.ENABLE_SIGNATURE_VERIFICATION and signature:
        if not verify_signature(raw_data, signature):
            logger.error("❌ SIGNATURE VERIFICATION FAILED")
            logger.error(f"Expected signature calculation failed for: {signature}")
            return Response('Unauthorized', status=401)
        else:
            logger.info("✅ Signature verification passed")

    try:
        data = request.get_json()

        # ALWAYS log full webhook data for debugging
        logger.info("=== FULL WEBHOOK DATA ===")
        logger.info(json.dumps(data, indent=2))

        # Check webhook object type
        webhook_object = data.get('object', 'UNKNOWN')
        logger.info(f"Webhook object type: {webhook_object}")

        if webhook_object == 'page':
            logger.info("📱 Received Facebook Page webhook")
            entries = data.get('entry', [])

            for entry in entries:
                logger.info(f"Processing entry: {entry.get('id', 'NO_ID')}")

                # Check for messaging events
                messaging_events = entry.get('messaging', [])
                if messaging_events:
                    logger.info(f"Found {len(messaging_events)} messaging events")

                    for event in messaging_events:
                        sender_id = event.get('sender', {}).get('id', 'UNKNOWN')
                        logger.info(f"Event from sender: {sender_id}")

                        # Skip if it's from our own account
                        if sender_id == config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
                            logger.info("⏭️ Skipping message from own account")
                            continue

                        # Process any message
                        logger.info(f"🔄 Processing message from {sender_id}")
                        logger.info(f"Full event data: {json.dumps(event, indent=2)}")

                        try:
                            shared_post = process_shared_post(event)
                            extracted_data = extract_all_data(shared_post)

                            # Log extracted data
                            logger.info(f"✅ Extracted URLs: {extracted_data.urls}")
                            logger.info(f"📸 Instagram Post: {extracted_data.instagram_post_url}")
                            logger.info(f"🏷️ Hashtags: {extracted_data.hashtags}")
                            logger.info(f"📝 Caption: {extracted_data.caption_text}")

                            # Send acknowledgment
                            send_acknowledgment(sender_id, extracted_data)

                            # Log for next phase
                            if extracted_data.urls:
                                logger.info("🎯 Ready for price comparison processing:")
                                for url in extracted_data.urls:
                                    logger.info(f"  🔗 {url}")
                            else:
                                logger.warning("⚠️ No URLs extracted from message")

                        except Exception as e:
                            logger.error(f"❌ Error processing message: {e}")
                            import traceback
                            logger.error(traceback.format_exc())

                # Check for changes (Instagram-specific)
                changes = entry.get('changes', [])
                if changes:
                    logger.info(f"Found {len(changes)} changes")
                    for change in changes:
                        logger.info(f"Change: {json.dumps(change, indent=2)}")

        elif webhook_object == 'instagram':
            logger.info("📷 Received Instagram webhook")
            entries = data.get('entry', [])

            for entry in entries:
                logger.info(f"Processing Instagram entry: {entry.get('id', 'NO_ID')}")

                # Check for messaging events
                messaging_events = entry.get('messaging', [])
                if messaging_events:
                    logger.info(f"Found {len(messaging_events)} Instagram messaging events")

                    for event in messaging_events:
                        sender_id = event.get('sender', {}).get('id', 'UNKNOWN')
                        logger.info(f"Instagram event from sender: {sender_id}")

                        # Skip if it's from our own account
                        if sender_id == config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
                            logger.info("⏭️ Skipping Instagram message from own account")
                            continue

                        # Process Instagram message
                        logger.info(f"🔄 Processing Instagram message from {sender_id}")
                        logger.info(f"Full Instagram event: {json.dumps(event, indent=2)}")

                        try:
                            shared_post = process_shared_post(event)
                            extracted_data = extract_all_data(shared_post)

                            # Log extracted data
                            logger.info(f"✅ Extracted URLs: {extracted_data.urls}")
                            logger.info(f"📸 Instagram Post: {extracted_data.instagram_post_url}")
                            logger.info(f"🏷️ Hashtags: {extracted_data.hashtags}")

                            # Send acknowledgment
                            send_acknowledgment(sender_id, extracted_data)

                            # Log for next phase
                            if extracted_data.urls:
                                logger.info("🎯 Ready for price comparison processing:")
                                for url in extracted_data.urls:
                                    logger.info(f"  🔗 {url}")

                        except Exception as e:
                            logger.error(f"❌ Error processing Instagram message: {e}")
                            import traceback
                            logger.error(traceback.format_exc())

        else:
            logger.warning(f"🤷 Unknown webhook object type: {webhook_object}")
            logger.info("Full unknown webhook data:")
            logger.info(json.dumps(data, indent=2))

        logger.info("=== WEBHOOK PROCESSING COMPLETE ===")
        return Response('EVENT_RECEIVED', status=200)

    except Exception as e:
        logger.error(f"💥 CRITICAL ERROR processing webhook: {e}")
        import traceback
        logger.error(traceback.format_exc())

        # Still return 200 to avoid webhook deregistration
        return Response('EVENT_RECEIVED', status=200)

@app.route('/test-webhook', methods=['POST'])
def test_webhook():
    """Test endpoint to verify webhook connectivity"""
    logger.info("🧪 TEST WEBHOOK CALLED")
    logger.info(f"Headers: {dict(request.headers)}")
    logger.info(f"Data: {request.get_data()}")
    logger.info(f"JSON: {request.get_json()}")

    return jsonify({
        'status': 'success',
        'message': 'Test webhook received successfully',
        'timestamp': datetime.utcnow().isoformat(),
        'received_data': request.get_json() if request.is_json else str(request.get_data())
    })

@app.route('/webhook-info', methods=['GET'])
def webhook_info():
    """Display webhook configuration info"""
    return jsonify({
        'webhook_url': request.base_url.replace('/webhook-info', '/webhook'),
        'test_webhook_url': request.base_url.replace('/webhook-info', '/test-webhook'),
        'verify_token': config.VERIFY_TOKEN,
        'app_secret_configured': bool(config.APP_SECRET),
        'page_access_token_configured': bool(config.PAGE_ACCESS_TOKEN),
        'instagram_business_id': config.INSTAGRAM_BUSINESS_ACCOUNT_ID,
        'signature_verification_enabled': config.ENABLE_SIGNATURE_VERIFICATION,
        'debug_mode': config.DEBUG_MODE,
        'graph_api_version': config.GRAPH_API_VERSION
    })

@app.route('/health', methods=['GET'])
def health_check():
    """Detailed health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'config': {
            'verify_token_set': bool(config.VERIFY_TOKEN),
            'access_token_set': bool(config.PAGE_ACCESS_TOKEN),
            'app_secret_set': bool(config.APP_SECRET),
            'instagram_id_set': bool(config.INSTAGRAM_BUSINESS_ACCOUNT_ID),
            'signature_verification': config.ENABLE_SIGNATURE_VERIFICATION,
            'debug_mode': config.DEBUG_MODE
        }
    })

# Add these endpoints to your webhook_receiver.py file

@app.route('/privacy', methods=['GET'])
def privacy_policy():
    """Privacy Policy page"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Privacy Policy - Globle Price Comparison Bot</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #2c3e50;
                border-bottom: 3px solid #3498db;
                padding-bottom: 10px;
            }
            h2 {
                color: #34495e;
                margin-top: 30px;
            }
            .last-updated {
                color: #7f8c8d;
                font-style: italic;
            }
            ul {
                line-height: 1.8;
            }
            .contact {
                background: #ecf0f1;
                padding: 15px;
                border-radius: 5px;
                margin-top: 30px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Privacy Policy</h1>
            <p class="last-updated">Last updated: September 2025</p>
            
            <h2>1. Introduction</h2>
            <p>Welcome to Globle Price Comparison Bot ("we," "our," or "us"). This Privacy Policy explains how we collect, use, and protect your information when you use our Instagram-based price comparison service (the "Service").</p>
            
            <h2>2. Information We Collect</h2>
            <p>When you interact with our bot, we may collect:</p>
            <ul>
                <li><strong>Instagram User ID:</strong> Your unique Instagram identifier to send responses</li>
                <li><strong>Shared Content:</strong> Instagram posts/ads you share with our bot</li>
                <li><strong>Product URLs:</strong> Links extracted from shared posts for price comparison</li>
                <li><strong>Timestamps:</strong> When you interact with our service</li>
                <li><strong>Message Content:</strong> Text messages you send to our bot</li>
            </ul>
            
            <h2>3. How We Use Your Information</h2>
            <p>We use the collected information to:</p>
            <ul>
                <li>Process your price comparison requests</li>
                <li>Extract product information from shared posts</li>
                <li>Send you price comparison results</li>
                <li>Improve our service quality and accuracy</li>
                <li>Debug and maintain our service</li>
            </ul>
            
            <h2>4. Data Storage and Retention</h2>
            <ul>
                <li>We process data in real-time and do not permanently store personal messages</li>
                <li>Temporary data is retained only for the duration needed to provide the service</li>
                <li>Logs for debugging are retained for a maximum of 30 days</li>
                <li>We do not store or share your Instagram credentials</li>
            </ul>
            
            <h2>5. Data Sharing</h2>
            <p>We do not sell, trade, or otherwise transfer your personal information to third parties. We may share data only:</p>
            <ul>
                <li>With e-commerce platforms to fetch current product prices</li>
                <li>When required by law or to protect our rights</li>
                <li>With your explicit consent</li>
            </ul>
            
            <h2>6. Data Security</h2>
            <p>We implement appropriate security measures to protect your information:</p>
            <ul>
                <li>Secure HTTPS connections for all data transmission</li>
                <li>Encryption of sensitive data</li>
                <li>Regular security audits and updates</li>
                <li>Limited access to user data on a need-to-know basis</li>
            </ul>
            
            <h2>7. Third-Party Services</h2>
            <p>Our service interacts with:</p>
            <ul>
                <li><strong>Meta/Instagram:</strong> To receive and send messages (governed by Meta's Privacy Policy)</li>
                <li><strong>E-commerce Platforms:</strong> To fetch product prices (Amazon, Flipkart, Myntra, etc.)</li>
                <li><strong>Hosting Provider:</strong> Render.com for server infrastructure</li>
            </ul>
            
            <h2>8. Your Rights</h2>
            <p>You have the right to:</p>
            <ul>
                <li>Stop using our service at any time</li>
                <li>Request information about data we've processed</li>
                <li>Request deletion of your data from our logs</li>
                <li>Opt-out of any communications</li>
            </ul>
            
            <h2>9. Children's Privacy</h2>
            <p>Our service is not directed to individuals under 13 years of age. We do not knowingly collect personal information from children under 13.</p>
            
            <h2>10. Changes to This Policy</h2>
            <p>We may update this Privacy Policy from time to time. We will notify users of any material changes by updating the "Last updated" date.</p>
            
            <h2>11. Contact Us</h2>
            <div class="contact">
                <p><strong>If you have questions about this Privacy Policy, please contact us:</strong></p>
                <p>Email: support@globle.club<br>
                Instagram: @globle.club<br>
                Service: Globle Price Comparison Bot</p>
            </div>
            
            <h2>12. Compliance</h2>
            <p>This privacy policy complies with:</p>
            <ul>
                <li>General Data Protection Regulation (GDPR)</li>
                <li>California Consumer Privacy Act (CCPA)</li>
                <li>Meta Platform Terms and Policies</li>
            </ul>
        </div>
    </body>
    </html>
    """

@app.route('/terms', methods=['GET'])
def terms_of_service():
    """Terms of Service page"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Terms of Service - Globle Price Comparison Bot</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #2c3e50;
                border-bottom: 3px solid #e74c3c;
                padding-bottom: 10px;
            }
            h2 {
                color: #34495e;
                margin-top: 30px;
            }
            .last-updated {
                color: #7f8c8d;
                font-style: italic;
            }
            ul {
                line-height: 1.8;
            }
            .important {
                background: #fff3cd;
                border-left: 4px solid #ffc107;
                padding: 15px;
                margin: 20px 0;
            }
            .contact {
                background: #ecf0f1;
                padding: 15px;
                border-radius: 5px;
                margin-top: 30px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Terms of Service</h1>
            <p class="last-updated">Effective Date: September 2025</p>
            
            <h2>1. Acceptance of Terms</h2>
            <p>By using the Globle Price Comparison Bot ("Service"), you agree to be bound by these Terms of Service ("Terms"). If you do not agree to these Terms, please do not use our Service.</p>
            
            <h2>2. Description of Service</h2>
            <p>Globle Price Comparison Bot is an Instagram-based service that:</p>
            <ul>
                <li>Receives Instagram posts/ads shared by users</li>
                <li>Extracts product information from shared content</li>
                <li>Compares prices across multiple e-commerce platforms</li>
                <li>Returns price comparison results to users</li>
            </ul>
            
            <h2>3. User Requirements</h2>
            <p>To use our Service, you must:</p>
            <ul>
                <li>Have a valid Instagram account</li>
                <li>Be at least 13 years of age</li>
                <li>Comply with Instagram's Terms of Service</li>
                <li>Use the Service for lawful purposes only</li>
            </ul>
            
            <h2>4. Acceptable Use</h2>
            <p>You agree NOT to:</p>
            <ul>
                <li>Use the Service for any illegal or unauthorized purpose</li>
                <li>Spam or send excessive requests to the bot</li>
                <li>Attempt to hack, disrupt, or overload our servers</li>
                <li>Share inappropriate, offensive, or harmful content</li>
                <li>Impersonate others or provide false information</li>
                <li>Resell or commercialize the Service without permission</li>
                <li>Use automated systems to interact with the Service</li>
            </ul>
            
            <h2>5. Service Availability</h2>
            <ul>
                <li>The Service is provided "as is" and "as available"</li>
                <li>We do not guarantee uninterrupted or error-free service</li>
                <li>We reserve the right to modify or discontinue the Service at any time</li>
                <li>Scheduled maintenance may temporarily affect availability</li>
            </ul>
            
            <h2>6. Price Information Disclaimer</h2>
            <div class="important">
                <strong>Important:</strong> Price information provided by our Service is for reference only. We strive for accuracy but:
                <ul>
                    <li>Prices may change after our comparison</li>
                    <li>Availability may vary by location</li>
                    <li>Shipping costs may not be included</li>
                    <li>We are not responsible for pricing errors</li>
                    <li>Always verify prices on the merchant's website before purchasing</li>
                </ul>
            </div>
            
            <h2>7. Intellectual Property</h2>
            <ul>
                <li>The Service and its content are owned by Globle</li>
                <li>Product information belongs to respective merchants</li>
                <li>Instagram content belongs to respective users</li>
                <li>You retain ownership of content you share with the Service</li>
            </ul>
            
            <h2>8. Privacy and Data</h2>
            <p>Your use of the Service is subject to our Privacy Policy. By using the Service, you consent to:</p>
            <ul>
                <li>Processing of shared Instagram posts</li>
                <li>Extraction of product URLs</li>
                <li>Temporary storage of interaction data</li>
                <li>Receiving automated responses from our bot</li>
            </ul>
            
            <h2>9. Third-Party Services</h2>
            <p>Our Service interacts with third-party platforms including:</p>
            <ul>
                <li>Instagram/Meta platforms</li>
                <li>Various e-commerce websites</li>
                <li>Cloud hosting services</li>
            </ul>
            <p>We are not responsible for the content, policies, or practices of third-party services.</p>
            
            <h2>10. Limitation of Liability</h2>
            <p>To the maximum extent permitted by law:</p>
            <ul>
                <li>We are not liable for any indirect, incidental, or consequential damages</li>
                <li>We are not responsible for purchase decisions made based on our comparisons</li>
                <li>We are not liable for losses due to service interruptions</li>
                <li>Our total liability shall not exceed $100 USD</li>
            </ul>
            
            <h2>11. Indemnification</h2>
            <p>You agree to indemnify and hold harmless Globle, its affiliates, and their respective officers, directors, employees, and agents from any claims, damages, losses, or expenses arising from your use of the Service or violation of these Terms.</p>
            
            <h2>12. Termination</h2>
            <p>We reserve the right to:</p>
            <ul>
                <li>Terminate or suspend your access to the Service</li>
                <li>Block users who violate these Terms</li>
                <li>Report violations to Instagram/Meta</li>
            </ul>
            
            <h2>13. Modifications to Terms</h2>
            <p>We may modify these Terms at any time. Continued use of the Service after changes constitutes acceptance of the modified Terms.</p>
            
            <h2>14. Governing Law</h2>
            <p>These Terms shall be governed by the laws of the jurisdiction in which Globle operates, without regard to conflict of law principles.</p>
            
            <h2>15. Severability</h2>
            <p>If any provision of these Terms is found to be unenforceable, the remaining provisions shall continue in full force and effect.</p>
            
            <h2>16. Contact Information</h2>
            <div class="contact">
                <p><strong>For questions about these Terms, please contact us:</strong></p>
                <p>Email: support@globle.club<br>
                Instagram: @globle.club<br>
                Service Name: Globle Price Comparison Bot</p>
            </div>
            
            <h2>17. Entire Agreement</h2>
            <p>These Terms constitute the entire agreement between you and Globle regarding the use of the Service and supersede all prior agreements and understandings.</p>
        </div>
    </body>
    </html>
    """

@app.route('/support', methods=['GET'])
def support_page():
    """Support/Help page (bonus - good to have)"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Support - Globle Price Comparison Bot</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #2c3e50;
                border-bottom: 3px solid #27ae60;
                padding-bottom: 10px;
            }
            h2 {
                color: #34495e;
                margin-top: 30px;
            }
            .faq {
                background: #f8f9fa;
                padding: 15px;
                margin: 15px 0;
                border-radius: 5px;
                border-left: 4px solid #27ae60;
            }
            .faq h3 {
                margin-top: 0;
                color: #27ae60;
            }
            .steps {
                background: #e8f5e9;
                padding: 20px;
                border-radius: 5px;
                margin: 20px 0;
            }
            .steps ol {
                margin: 10px 0;
                padding-left: 20px;
            }
            .steps li {
                margin: 10px 0;
            }
            .contact {
                background: #ecf0f1;
                padding: 15px;
                border-radius: 5px;
                margin-top: 30px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Support & Help</h1>
            
            <h2>How to Use Globle Price Comparison Bot</h2>
            <div class="steps">
                <ol>
                    <li><strong>Find a Product on Instagram:</strong> Browse Instagram and find a product post or ad you're interested in</li>
                    <li><strong>Share to Our Bot:</strong> Tap the share button (paper airplane icon) and send it to @globle.club</li>
                    <li><strong>Wait for Analysis:</strong> Our bot will extract the product and search for prices (15-30 seconds)</li>
                    <li><strong>Get Results:</strong> Receive a comparison of prices from multiple stores</li>
                    <li><strong>Shop Smart:</strong> Click on the best price to visit the store directly</li>
                </ol>
            </div>
            
            <h2>Frequently Asked Questions</h2>
            
            <div class="faq">
                <h3>Q: What types of posts can I share?</h3>
                <p>A: You can share any Instagram post that contains products - regular posts, shopping posts, ads, reels, or stories with product tags.</p>
            </div>
            
            <div class="faq">
                <h3>Q: Which stores do you compare?</h3>
                <p>A: We currently compare prices from Amazon, Flipkart, Myntra, Nykaa, Ajio, and many more. We're constantly adding new stores!</p>
            </div>
            
            <div class="faq">
                <h3>Q: Is the service free?</h3>
                <p>A: Yes! Our price comparison service is completely free to use.</p>
            </div>
            
            <div class="faq">
                <h3>Q: How accurate are the prices?</h3>
                <p>A: We fetch real-time prices when you send a request. However, prices can change quickly, so always verify on the merchant's site before purchasing.</p>
            </div>
            
            <div class="faq">
                <h3>Q: Why didn't I get a response?</h3>
                <p>A: Make sure you're sharing to the correct account (@globle.club) and that the post contains a product. If issues persist, contact our support.</p>
            </div>
            
            <div class="faq">
                <h3>Q: Do you store my data?</h3>
                <p>A: We only process data temporarily to provide the service. We don't store personal messages or shopping history. See our Privacy Policy for details.</p>
            </div>
            
            <div class="faq">
                <h3>Q: Can I use this for business?</h3>
                <p>A: Our service is intended for personal use. For business inquiries, please contact us directly.</p>
            </div>
            
            <h2>Troubleshooting</h2>
            <ul>
                <li><strong>Bot not responding:</strong> Ensure you're messaging @globle.club (business account)</li>
                <li><strong>No prices found:</strong> The product might not be available online or in our supported stores</li>
                <li><strong>Wrong product:</strong> Try sharing a post with clearer product information</li>
                <li><strong>Error messages:</strong> Try again in a few minutes or contact support</li>
            </ul>
            
            <h2>Contact Support</h2>
            <div class="contact">
                <p><strong>Need more help? Reach out to us:</strong></p>
                <p>📧 Email: support@globle.club<br>
                📱 Instagram: @globle.club<br>
                💬 Send us a DM on Instagram for quick help<br>
                ⏰ Response Time: Usually within 24 hours</p>
            </div>
            
            <h2>Feature Requests</h2>
            <p>Have ideas for improving our service? We'd love to hear from you! Send your suggestions to support@globle.club or DM us on Instagram.</p>
        </div>
    </body>
    </html>
    """
    
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Instagram Webhook Receiver (Fixed) on port {port}")
    logger.info(f"Debug mode: {config.DEBUG_MODE}")
    app.run(host='0.0.0.0', port=port, debug=config.DEBUG_MODE)