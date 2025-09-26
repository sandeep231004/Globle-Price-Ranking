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
    APP_SECRET = os.environ.get('FACEBOOK_APP_SECRET')
    VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')
    PAGE_ACCESS_TOKEN = os.environ.get('PAGE_ACCESS_TOKEN')

    # Instagram Business Account
    INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get('INSTAGRAM_BUSINESS_ACCOUNT_ID')

    # Page ID
    PAGE_ID = os.environ.get('PAGE_ID')

    # API Configuration
    GRAPH_API_VERSION = os.environ.get('GRAPH_API_VERSION', 'v23.0')
    GRAPH_API_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

    # Instagram Access Token (if different from page token)
    INSTAGRAM_ACCESS_TOKEN = os.environ.get('INSTAGRAM_ACCESS_TOKEN')

    # Webhook Security
    ENABLE_SIGNATURE_VERIFICATION = os.environ.get('ENABLE_SIGNATURE_VERIFICATION', 'true').lower() == 'true'

    # Debug Mode
    DEBUG_MODE = os.environ.get('DEBUG_MODE', 'true').lower() == 'true'

config = Config()

# ============== DATA MODELS ==============
@dataclass
class ProductData:
    """Extracted shop URLs from shared posts"""
    timestamp: str
    sender_id: str
    message_id: str
    post_type: str  # 'share', 'unsupported_share', 'direct_link'
    shop_urls: List[str]  # Only URLs from Shop Now/Buy Now buttons
    raw_webhook_data: Dict

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
        # Remove 'sha256=' prefix if present
        if signature.startswith('sha256='):
            signature = signature[7:]

        # Calculate expected signature
        expected_sig = hmac.new(
            config.APP_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()

        # Compare signatures
        is_valid = hmac.compare_digest(expected_sig, signature)
        logger.info(f"Signature verification result: {is_valid}")
        return is_valid

    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False

def extract_urls_from_text(text: str) -> List[str]:
    """Extract all URLs from text including shortened URLs"""
    if not text:
        return []

    # Comprehensive URL patterns
    url_patterns = [
        r'https?://[^\\s<>"{}|\\\\^`\\[\\]]+',
        r'www\\.[^\\s<>"{}|\\\\^`\\[\\]]+',
        r'bit\\.ly/[^\\s]+',
        r'linktr\\.ee/[^\\s]+',
        r'link\\.bio/[^\\s]+',
        r'linkin\\.bio/[^\\s]+',
        r'shop\\.link/[^\\s]+',
    ]

    urls = []
    for pattern in url_patterns:
        found_urls = re.findall(pattern, text, re.IGNORECASE)
        for url in found_urls:
            # Add https:// if missing
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            # Clean up URL
            url = url.rstrip('.,;:!?)')
            urls.append(url)

    return list(set(urls))

def expand_shortened_url(url: str, max_redirects: int = 5) -> str:
    """Expand shortened URLs to get the final destination"""
    try:
        session = requests.Session()
        session.max_redirects = max_redirects
        response = session.head(url, allow_redirects=True, timeout=5)
        expanded_url = response.url
        logger.info(f"Expanded URL: {url} -> {expanded_url}")
        return expanded_url
    except Exception as e:
        logger.warning(f"Could not expand URL {url}: {e}")
        return url

def extract_shop_urls(text: str, attachments: List[Dict]) -> List[str]:
    """Extract only shop/buy URLs from text and attachments"""
    shop_urls = []

    # Extract URLs from text
    if text:
        urls = extract_urls_from_text(text)
        # Include all URLs found in text - let user decide what's relevant
        shop_urls.extend(urls)

    # Extract shop URLs from attachments
    for attachment in attachments:
        payload = attachment.get('payload', {})
        attachment_type = attachment.get('type', '')

        # Handle different attachment types
        if attachment_type == 'ig_reel':
            # Instagram Reel shared - extract URLs from title/description
            title = payload.get('title', '')
            if title:
                title_urls = extract_urls_from_text(title)
                shop_urls.extend(title_urls)
                logger.info(f"Found reel title with potential URLs: {title[:100]}...")

            # Check for reel video ID and try to get more details
            reel_id = payload.get('reel_video_id')
            if reel_id:
                logger.info(f"Found Instagram Reel ID: {reel_id}")
                # Try to get detailed media info including product tags and links
                media_details = get_instagram_media_details(reel_id)
                if media_details:
                    # Look for shopping product tags or links
                    if 'shopping_product_tags' in media_details:
                        for tag in media_details['shopping_product_tags']:
                            if 'product_url' in tag:
                                shop_urls.append(tag['product_url'])
                                logger.info(f"Found shop URL from product tag: {tag['product_url']}")

                    # Look for product tags (alternative field)
                    if 'product_tags' in media_details:
                        for tag in media_details['product_tags']:
                            if isinstance(tag, dict) and 'product_url' in tag:
                                shop_urls.append(tag['product_url'])
                                logger.info(f"Found shop URL from product tag: {tag['product_url']}")

                    # Check for URLs in the caption
                    if 'caption_urls' in media_details:
                        caption_urls = media_details['caption_urls']
                        # Filter out social media and internal URLs
                        for url in caption_urls:
                            if not any(domain in url.lower() for domain in [
                                'instagram.com', 'facebook.com', 'fb.me', 'lookaside.fbsbx.com',
                                'ig_messaging_cdn', 'scontent', 'twitter.com', 'youtube.com'
                            ]):
                                shop_urls.append(url)
                                logger.info(f"Found shop URL from caption: {url}")

                    # Check for child media (carousel posts)
                    if 'children' in media_details and 'data' in media_details['children']:
                        for child in media_details['children']['data']:
                            if 'product_tags' in child:
                                for tag in child['product_tags']:
                                    if isinstance(tag, dict) and 'product_url' in tag:
                                        shop_urls.append(tag['product_url'])
                                        logger.info(f"Found shop URL from child media: {tag['product_url']}")

                    # Log comprehensive API response for debugging
                    logger.info(f"Media details API response keys: {list(media_details.keys())}")
                    if config.DEBUG_MODE:
                        logger.info(f"Full media details: {json.dumps(media_details, indent=2)}")
                else:
                    logger.warning(f"Could not fetch additional details for reel {reel_id}")

            # If this is a reel but no shop URLs found, provide helpful guidance
            if attachment_type == 'ig_reel' and not shop_urls:
                logger.warning("⚠️ No shop URLs found in reel - this is a known limitation")
                logger.info("💡 EXPLANATION: Instagram 'Shop Now' buttons are only visible in the mobile app")
                logger.info("📱 They don't appear in webhook data or API responses")
                logger.info("🔗 WORKAROUND: Ask users to:")
                logger.info("   1. Tap 'Shop Now' button in the Instagram app")
                logger.info("   2. Copy the product URL from the opened page")
                logger.info("   3. Send that URL directly to the bot")

                # Set a flag to send a helpful message to the user
                shop_urls.append("SHOP_NOW_LIMITATION_DETECTED")

        elif attachment_type == 'share':
            # Regular shared post
            if 'url' in payload:
                shop_urls.append(payload['url'])
            if 'title' in payload:
                title_urls = extract_urls_from_text(payload['title'])
                shop_urls.extend(title_urls)
            if 'description' in payload:
                desc_urls = extract_urls_from_text(payload['description'])
                shop_urls.extend(desc_urls)

        # Check for direct shop URLs in payload
        if 'shop_url' in payload:
            shop_urls.append(payload['shop_url'])
        elif 'product_url' in payload:
            shop_urls.append(payload['product_url'])
        elif 'url' in payload:
            url = payload['url']
            # Filter out Facebook/Instagram internal URLs (video CDN, etc.)
            if not any(domain in url for domain in ['lookaside.fbsbx.com', 'ig_messaging_cdn', 'scontent']):
                shop_urls.append(url)
            else:
                logger.info(f"Skipping internal media URL: {url[:100]}...")

        # Also check for description field which may contain URLs
        if 'description' in payload:
            desc_urls = extract_urls_from_text(payload['description'])
            shop_urls.extend(desc_urls)

    # Expand shortened URLs and remove duplicates
    expanded_urls = []
    for url in shop_urls:
        if any(domain in url for domain in ['bit.ly', 'linktr.ee', 'link.bio', 'linkin.bio', 'tinyurl.com']):
            expanded_url = expand_shortened_url(url)
            expanded_urls.append(expanded_url)
        else:
            expanded_urls.append(url)

    return list(set(expanded_urls))

def get_instagram_media_details(media_id: str) -> Dict:
    """Fetch additional media details using Instagram Graph API"""
    if not config.INSTAGRAM_ACCESS_TOKEN and not config.PAGE_ACCESS_TOKEN:
        logger.warning("No access token available for fetching media details")
        return {}

    access_token = config.INSTAGRAM_ACCESS_TOKEN or config.PAGE_ACCESS_TOKEN

    try:
        url = f"{config.GRAPH_API_URL}/{media_id}"
        # Request comprehensive fields including shopping and product information
        params = {
            'fields': 'id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,username,product_tags,shopping_product_tags,owner,children{media_url,media_type,product_tags},insights.metric(reach,impressions),comments_count,like_count',
            'access_token': access_token
        }

        logger.info(f"Fetching comprehensive media details for {media_id}")
        response = requests.get(url, params=params, timeout=15)

        logger.info(f"API Response Status: {response.status_code}")
        logger.info(f"API Response: {response.text}")

        if response.status_code == 200:
            data = response.json()
            logger.info(f"Successfully fetched media details for {media_id}")

            # Try to extract shop URLs from caption if available
            caption = data.get('caption', '')
            if caption:
                logger.info(f"Media caption: {caption}")
                caption_urls = extract_urls_from_text(caption)
                if caption_urls:
                    logger.info(f"Found URLs in caption: {caption_urls}")
                    data['caption_urls'] = caption_urls

            return data
        else:
            logger.error(f"Failed to fetch media details: {response.status_code} - {response.text}")

            # If direct media access fails, try alternative approaches
            if response.status_code == 400:
                logger.info("Trying alternative media access methods...")
                return try_alternative_media_access(media_id, access_token)

    except Exception as e:
        logger.error(f"Error fetching media details: {e}")

    return {}

def try_alternative_media_access(media_id: str, access_token: str) -> Dict:
    """Try alternative methods to access media information"""
    try:
        # Try using Instagram Business Discovery API
        if config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
            url = f"{config.GRAPH_API_URL}/{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}"
            params = {
                'fields': f'business_discovery.username({media_id}){{media{{caption,permalink,media_url,product_tags}}}}',
                'access_token': access_token
            }

            logger.info("Trying Instagram Business Discovery API...")
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                logger.info(f"Business Discovery API response: {response.text}")
                return data
            else:
                logger.warning(f"Business Discovery API failed: {response.status_code}")

        # Try using the media ID as a hashtag search (if it contains recognizable product info)
        logger.info("Media access methods exhausted - trying hashtag analysis...")
        return {}

    except Exception as e:
        logger.error(f"Alternative media access error: {e}")
        return {}

# Global variable to track processed messages and prevent duplicates
processed_messages = set()

def process_instagram_message(event: Dict) -> ProductData:
    """Process Instagram message and extract shop URLs only"""

    # Initialize with basic data
    product_data = ProductData(
        timestamp=datetime.utcnow().isoformat(),
        sender_id=event.get('sender', {}).get('id', 'unknown'),
        message_id=event.get('message', {}).get('mid', 'unknown'),
        post_type='unknown',
        shop_urls=[],
        raw_webhook_data=event
    )

    message = event.get('message', {})

    # Check if this is an unsupported message (often shared posts)
    if message.get('is_unsupported'):
        logger.info("🔍 Processing unsupported message (likely shared post)")
        product_data.post_type = 'unsupported_share'

    # Extract shop URLs from text and attachments
    text = message.get('text', '')
    attachments = message.get('attachments', [])

    shop_urls = extract_shop_urls(text, attachments)
    product_data.shop_urls = shop_urls

    # Determine post type if not already set
    if product_data.post_type == 'unknown':
        if attachments:
            for attachment in attachments:
                attachment_type = attachment.get('type')
                if attachment_type == 'share':
                    product_data.post_type = 'share'
                    break
                elif attachment_type == 'ig_reel':
                    product_data.post_type = 'ig_reel'
                    break
                elif attachment_type == 'link':
                    product_data.post_type = 'direct_link'
                    break
        else:
            product_data.post_type = 'text_message'

    return product_data

def send_acknowledgment(recipient_id: str, product_data: ProductData):
    """Send acknowledgment message back to user"""
    if not config.PAGE_ACCESS_TOKEN:
        logger.warning("Cannot send message - no page access token")
        return

    # Filter out special flags from URLs for user response
    actual_shop_urls = [url for url in product_data.shop_urls if url != "SHOP_NOW_LIMITATION_DETECTED"]
    has_limitation_flag = "SHOP_NOW_LIMITATION_DETECTED" in product_data.shop_urls

    # Prepare message based on what was found
    if actual_shop_urls:
        message_text = (
            f"✅ Found {len(actual_shop_urls)} shop URL(s)!\\n"
            f"🔍 Searching for best prices...\\n"
            f"⏱️ This will take 15-30 seconds."
        )
    elif has_limitation_flag:
        message_text = (
            "📱 I see you shared an Instagram Reel/Ad!\\n\\n"
            "⚠️ Unfortunately, Instagram 'Shop Now' buttons aren't accessible through our system.\\n\\n"
            "🔗 Here's how to get price comparisons:\\n"
            "1️⃣ Tap the 'Shop Now' button in Instagram\\n"
            "2️⃣ Copy the product URL from the page that opens\\n"
            "3️⃣ Send me that URL directly\\n\\n"
            "💡 Or share posts that have direct product links in the caption!"
        )
    elif product_data.post_type == 'unsupported_share':
        message_text = "📱 I see you shared a post! Looking for shop URLs..."
    else:
        message_text = "No URLs in the Ad. Please share a post that contains a product link or a direct product URL."

    url = f"{config.GRAPH_API_URL}/{config.PAGE_ID}/messages"
    payload = {
        'recipient': {'id': recipient_id},
        'message': {'text': message_text},
        'messaging_type': 'RESPONSE'
    }

    headers = {'Content-Type': 'application/json'}
    params = {'access_token': config.PAGE_ACCESS_TOKEN}

    try:
        response = requests.post(url, json=payload, params=params, headers=headers)
        if response.status_code == 200:
            logger.info(f"✅ Message sent to user {recipient_id}")
        else:
            logger.error(f"❌ Failed to send message: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error sending acknowledgment: {e}")

# ============== WEBHOOK ENDPOINTS ==============

@app.route('/', methods=['GET'])
def home():
    """Home endpoint for health check"""
    return jsonify({
        'status': 'running',
        'service': 'Instagram Shop URL Extraction Bot',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '3.0 - Simple',
        'debug_mode': config.DEBUG_MODE
    })

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Webhook verification endpoint for Facebook/Instagram"""
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    logger.info(f"Webhook verification request: mode={mode}, token_provided={bool(token)}")

    if mode == 'subscribe' and token == config.VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully")
        return Response(challenge, status=200, mimetype='text/plain')

    logger.error("❌ Webhook verification failed - token mismatch")
    return Response('Forbidden', status=403)

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Main webhook handler for Instagram messages"""

    # Get raw data for signature verification
    raw_data = request.get_data()

    # Verify signature
    signature = request.headers.get('X-Hub-Signature-256', '')
    if config.ENABLE_SIGNATURE_VERIFICATION:
        if not verify_webhook_signature(raw_data, signature):
            logger.error("❌ Invalid webhook signature")
            return Response('Unauthorized', status=401)

    try:
        data = request.get_json()

        # Log the complete webhook data for debugging
        if config.DEBUG_MODE:
            logger.info("📥 Full Webhook Data:")
            logger.info(json.dumps(data, indent=2))

        # Check webhook object type
        webhook_object = data.get('object')

        if webhook_object in ['instagram', 'page']:
            entries = data.get('entry', [])

            for entry in entries:
                # Process Instagram messaging events
                if 'messaging' in entry:
                    for event in entry['messaging']:
                        sender_id = event.get('sender', {}).get('id')

                        # Skip if it's from our own account
                        if sender_id == config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
                            continue

                        # Check if this is a message with content
                        if 'message' in event:
                            message = event.get('message', {})
                            message_id = message.get('mid', '')

                            # Skip echo messages (our own bot responses)
                            if message.get('is_echo'):
                                logger.info("⏭️ Skipping echo message (bot's own response)")
                                continue

                            # Skip duplicate messages
                            if message_id in processed_messages:
                                logger.info(f"⏭️ Skipping duplicate message: {message_id}")
                                continue

                            # Add to processed messages
                            processed_messages.add(message_id)

                            # Skip read receipts
                            if 'read' in event:
                                logger.info("⏭️ Skipping read receipt")
                                continue

                            logger.info(f"📨 Processing message from user: {sender_id}")

                            # Extract shop URLs only
                            product_data = process_instagram_message(event)

                            # Filter actual URLs for logging (exclude special flags)
                            actual_shop_urls = [url for url in product_data.shop_urls if url != "SHOP_NOW_LIMITATION_DETECTED"]
                            has_limitation_flag = "SHOP_NOW_LIMITATION_DETECTED" in product_data.shop_urls

                            # Log extracted data
                            logger.info("=" * 50)
                            logger.info("🛍️ SHOP URL EXTRACTION RESULTS:")
                            logger.info("=" * 50)
                            logger.info(f"📅 Timestamp: {product_data.timestamp}")
                            logger.info(f"👤 Sender ID: {product_data.sender_id}")
                            logger.info(f"📝 Post Type: {product_data.post_type}")

                            if has_limitation_flag:
                                logger.info(f"⚠️  Shop Now Button Limitation Detected (Instagram Reel/Ad)")
                                logger.info(f"🛒 Actual Shop URLs Found: {len(actual_shop_urls)}")
                            else:
                                logger.info(f"🛒 Shop URLs Found: {len(actual_shop_urls)}")

                            for i, url in enumerate(actual_shop_urls, 1):
                                logger.info(f"   {i}. {url}")
                            logger.info("=" * 50)

                            # Send acknowledgment to user (only once)
                            send_acknowledgment(sender_id, product_data)

                            # Log completion
                            if actual_shop_urls:
                                logger.info(f"🚀 Ready for price comparison: {len(actual_shop_urls)} URLs to process")
                            elif has_limitation_flag:
                                logger.info("📱 User guidance sent for Shop Now button limitation")
                            else:
                                logger.info("ℹ️ No shop URLs found in this message")

        return Response('EVENT_RECEIVED', status=200)

    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # Return 200 to prevent webhook deregistration
        return Response('EVENT_RECEIVED', status=200)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'configs_set': {
            'page_access_token': bool(config.PAGE_ACCESS_TOKEN),
            'instagram_access_token': bool(config.INSTAGRAM_ACCESS_TOKEN),
            'app_secret': bool(config.APP_SECRET),
            'instagram_business_id': bool(config.INSTAGRAM_BUSINESS_ACCOUNT_ID),
            'page_id': bool(config.PAGE_ID)
        }
    })

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

@app.route('/callback', methods=['GET'])
def oauth_callback():
    """OAuth callback endpoint for Facebook/Instagram App Authorization"""
    logger.info("=== OAUTH CALLBACK RECEIVED ===")

    # Get all query parameters
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')
    error_reason = request.args.get('error_reason')
    error_description = request.args.get('error_description')

    logger.info(f"Authorization code: {code}")
    logger.info(f"State parameter: {state}")

    if error:
        logger.error(f"OAuth error: {error} - {error_reason} - {error_description}")
        return jsonify({
            'status': 'error',
            'error': error,
            'error_reason': error_reason,
            'error_description': error_description,
            'message': 'Authorization failed. Please try again.'
        }), 400

    if not code:
        logger.error("No authorization code received")
        return jsonify({
            'status': 'error',
            'message': 'No authorization code received'
        }), 400

    try:
        # Exchange authorization code for access token
        token_url = f"{config.GRAPH_API_URL}/oauth/access_token"
        token_params = {
            'client_id': os.environ.get('FACEBOOK_APP_ID', ''),
            'client_secret': config.APP_SECRET,
            'redirect_uri': request.base_url,
            'code': code
        }

        logger.info(f"Exchanging code for token with params: {token_params}")

        response = requests.get(token_url, params=token_params)
        logger.info(f"Token exchange response: {response.text}")

        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data.get('access_token')

            if access_token:
                logger.info("✅ OAuth authorization successful")
                return jsonify({
                    'status': 'success',
                    'message': 'Authorization successful! You can now close this window.',
                    'access_token': access_token[:20] + '...',  # Show partial token for security
                    'token_type': token_data.get('token_type', 'bearer')
                })
            else:
                logger.error("No access token in response")
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to obtain access token'
                }), 400
        else:
            logger.error(f"Token exchange failed: {response.text}")
            return jsonify({
                'status': 'error',
                'message': 'Failed to exchange authorization code for token',
                'details': response.text
            }), 400

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'status': 'error',
            'message': 'Internal server error during authorization',
            'error': str(e)
        }), 500

@app.route('/auth/instagram', methods=['GET'])
def instagram_auth():
    """Generate Instagram OAuth authorization URL"""
    app_id = os.environ.get('FACEBOOK_APP_ID', '')
    if not app_id:
        return jsonify({
            'status': 'error',
            'message': 'Facebook App ID not configured'
        }), 500

    redirect_uri = request.base_url.replace('/auth/instagram', '/callback')
    state = os.urandom(16).hex()  # Generate random state for security

    auth_url = (
        f"https://www.facebook.com/v{config.GRAPH_API_VERSION.replace('v', '')}/dialog/oauth?"
        f"client_id={app_id}&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        f"scope=instagram_basic,instagram_content_publish,pages_messaging,pages_read_engagement"
    )

    logger.info(f"Generated auth URL: {auth_url}")

    return jsonify({
        'status': 'success',
        'auth_url': auth_url,
        'redirect_uri': redirect_uri,
        'state': state
    })

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
    logger.info(f"🚀 Starting Instagram Shop URL Extraction Bot on port {port}")
    logger.info(f"📝 Verify Token: {config.VERIFY_TOKEN}")
    logger.info(f"🔐 Signature Verification: {config.ENABLE_SIGNATURE_VERIFICATION}")
    logger.info(f"🐛 Debug Mode: {config.DEBUG_MODE}")
    app.run(host='0.0.0.0', port=port, debug=config.DEBUG_MODE)