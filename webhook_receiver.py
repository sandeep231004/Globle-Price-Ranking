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
from urllib.parse import urlparse

# Configure detailed logging with enhanced debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
        logging.FileHandler('instagram_webhook.log', encoding='utf-8')  # File output
    ]
)
logger = logging.getLogger(__name__)

# Enhanced debug logging functions
def log_debug_info(title: str, data: any, max_length: int = 1000):
    """Enhanced logging function for debug information"""
    logger.info(f"🔍 DEBUG - {title}")
    if isinstance(data, dict):
        logger.info(f"   📊 Type: Dictionary with {len(data)} keys")
        logger.info(f"   🔑 Keys: {list(data.keys())}")
        data_str = json.dumps(data, indent=2)
        if len(data_str) > max_length:
            logger.info(f"   📝 Content (truncated): {data_str[:max_length]}...")
        else:
            logger.info(f"   📝 Content: {data_str}")
    elif isinstance(data, list):
        logger.info(f"   📊 Type: List with {len(data)} items")
        if data:
            logger.info(f"   📝 First item: {data[0]}")
            if len(data) > 1:
                logger.info(f"   📝 Last item: {data[-1]}")
    else:
        logger.info(f"   📝 Value: {str(data)[:max_length]}")

def log_api_call(method: str, url: str, params: dict = None, headers: dict = None):
    """Log API call details for debugging"""
    logger.info(f"🌐 API CALL - {method}")
    logger.info(f"   📍 URL: {url}")
    if params:
        # Mask sensitive data
        safe_params = params.copy()
        if 'access_token' in safe_params:
            token = safe_params['access_token']
            safe_params['access_token'] = f"{token[:15]}...{token[-10:]}" if len(token) > 25 else "***"
        logger.info(f"   📋 Params: {safe_params}")
    if headers:
        logger.info(f"   📤 Headers: {headers}")

def log_api_response(response: requests.Response):
    """Log API response details for debugging"""
    logger.info(f"📥 API RESPONSE")
    logger.info(f"   📊 Status: {response.status_code}")
    logger.info(f"   📊 Headers: {dict(response.headers)}")
    logger.info(f"   📊 Content Length: {len(response.text)} chars")

    # Log response content (truncated for readability)
    if response.status_code == 200:
        try:
            data = response.json()
            log_debug_info("Response Data", data, 1000)
        except:
            logger.info(f"   📝 Raw Response: {response.text[:500]}...")
    else:
        logger.error(f"   ❌ Error Response: {response.text[:500]}")

def log_webhook_structure(webhook_data: dict):
    """Comprehensive webhook structure analysis for debugging"""
    logger.info("🔍 WEBHOOK STRUCTURE ANALYSIS")
    logger.info("=" * 60)

    # Top level structure
    logger.info(f"📦 Top-level keys: {list(webhook_data.keys())}")
    logger.info(f"📦 Object type: {webhook_data.get('object', 'unknown')}")

    # Entry analysis
    entries = webhook_data.get('entry', [])
    logger.info(f"📦 Entry count: {len(entries)}")

    for i, entry in enumerate(entries):
        logger.info(f"📋 Entry {i+1}:")
        logger.info(f"   🆔 ID: {entry.get('id', 'unknown')}")
        logger.info(f"   ⏰ Time: {entry.get('time', 'unknown')}")
        logger.info(f"   🔑 Keys: {list(entry.keys())}")

        # Messaging events
        messaging = entry.get('messaging', [])
        logger.info(f"   💬 Messaging events: {len(messaging)}")

        for j, message_event in enumerate(messaging):
            logger.info(f"   💬 Message Event {j+1}:")
            logger.info(f"      🔑 Keys: {list(message_event.keys())}")

            # Sender info
            sender = message_event.get('sender', {})
            logger.info(f"      👤 Sender ID: {sender.get('id', 'unknown')}")

            # Message details
            message = message_event.get('message', {})
            if message:
                logger.info(f"      📝 Message keys: {list(message.keys())}")
                logger.info(f"      📝 Message ID: {message.get('mid', 'unknown')}")
                logger.info(f"      📝 Is echo: {message.get('is_echo', False)}")
                logger.info(f"      📝 Is unsupported: {message.get('is_unsupported', False)}")
                logger.info(f"      📝 Text: {message.get('text', 'None')}")

                # Attachments analysis
                attachments = message.get('attachments', [])
                logger.info(f"      📎 Attachments: {len(attachments)}")

                for k, attachment in enumerate(attachments):
                    logger.info(f"      📎 Attachment {k+1}:")
                    logger.info(f"         🔖 Type: {attachment.get('type', 'unknown')}")

                    payload = attachment.get('payload', {})
                    logger.info(f"         📦 Payload keys: {list(payload.keys())}")

                    # Log important payload fields
                    important_fields = ['url', 'title', 'description', 'reel_video_id']
                    for field in important_fields:
                        if field in payload:
                            value = payload[field]
                            if field == 'url' and len(str(value)) > 100:
                                logger.info(f"         🔗 {field}: {str(value)[:50]}...{str(value)[-20:]}")
                            else:
                                logger.info(f"         📝 {field}: {value}")

                    # Look for shopping-related fields
                    shopping_fields = ['shop_url', 'product_url', 'shopping_url', 'merchant_url', 'buy_url']
                    for field in shopping_fields:
                        if field in payload:
                            logger.info(f"         🛍️ SHOPPING FIELD - {field}: {payload[field]}")

    logger.info("=" * 60)

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
    post_type: str
    shop_urls: List[str]
    raw_webhook_data: Dict

# ============== INSTAGRAM PRODUCT LINK EXTRACTOR ==============
class InstagramAdProductExtractor:
    """
    Following the exact 5-step process for extracting Instagram Ad product links:
    Step 1: Get Instagram Business Account ID from Page
    Step 2: Resolve IG media ID from shared post/reel
    Step 3: Fetch product tags using /product_tags endpoint
    Step 4: Handle missing tags with fallback methods
    Step 5: Handle paid ads vs organic posts differentiation
    """

    def __init__(self, access_token: str, page_id: str = None, ig_business_id: str = None):
        self.access_token = access_token
        self.page_id = page_id
        self.ig_business_id = ig_business_id
        self.graph_api_url = "https://graph.facebook.com/v23.0"

        # Step 1: If no IG business ID provided, get it from Page ID
        if not self.ig_business_id and self.page_id:
            self.ig_business_id = self.step1_get_instagram_business_account_from_page()

    def step1_get_instagram_business_account_from_page(self) -> str:
        """
        Step 1: Get Instagram Business Account ID from connected Facebook Page
        GET /{page-id}?fields=instagram_business_account{id}
        """
        if not self.page_id:
            logger.error("❌ Step 1 FAILED: No Page ID provided")
            return None

        logger.info(f"🔍 STEP 1: Getting Instagram Business Account ID from Page {self.page_id}")

        try:
            url = f"{self.graph_api_url}/{self.page_id}"
            params = {
                'fields': 'instagram_business_account{id}',
                'access_token': self.access_token
            }

            log_api_call("GET", url, params)
            response = requests.get(url, params=params, timeout=10)
            log_api_response(response)

            if response.status_code == 200:
                data = response.json()
                ig_business_account = data.get('instagram_business_account', {})
                ig_business_id = ig_business_account.get('id')

                if ig_business_id:
                    logger.info(f"   ✅ STEP 1 SUCCESS: Instagram Business Account ID: {ig_business_id}")
                    return ig_business_id
                else:
                    logger.error(f"   ❌ STEP 1 FAILED: No Instagram Business Account linked to Page {self.page_id}")
                    return None
            else:
                logger.error(f"   ❌ STEP 1 FAILED: API Error {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"   💥 STEP 1 ERROR: {e}")
            return None

    def step2_resolve_ig_media_id(self, webhook_data: Dict) -> Optional[str]:
        """
        Step 2: Resolve IG media ID from shared post/reel
        - If webhook provides reel_video_id, use it directly (it's usually the IG media ID)
        - If post permalink, parse shortcode and find media ID via /{ig-user-id}/media
        """
        logger.info(f"🔍 STEP 2: Resolving IG media ID from webhook data")

        message = webhook_data.get('message', {})
        attachments = message.get('attachments', [])

        for attachment in attachments:
            attachment_type = attachment.get('type')
            payload = attachment.get('payload', {})

            logger.info(f"   📎 Processing attachment type: {attachment_type}")

            if attachment_type == 'ig_reel':
                # Method 2A: Direct reel_video_id (usually already the IG media ID)
                reel_id = payload.get('reel_video_id')
                if reel_id:
                    logger.info(f"   ✅ STEP 2 SUCCESS: Found reel_video_id: {reel_id}")
                    return reel_id

            elif attachment_type == 'share':
                # Method 2B: Post permalink - extract shortcode and resolve
                post_url = payload.get('url', '')
                if post_url and 'instagram.com' in post_url:
                    logger.info(f"   🔗 Found Instagram permalink: {post_url}")

                    # Extract shortcode from URL
                    shortcode = self._extract_shortcode_from_permalink(post_url)
                    if shortcode:
                        # Find media ID using shortcode
                        media_id = self._find_media_id_by_shortcode(shortcode)
                        if media_id:
                            logger.info(f"   ✅ STEP 2 SUCCESS: Resolved media ID: {media_id}")
                            return media_id

        logger.error(f"   ❌ STEP 2 FAILED: Could not resolve IG media ID from webhook data")
        return None

    def _extract_shortcode_from_permalink(self, permalink: str) -> Optional[str]:
        """Extract shortcode from Instagram permalink"""
        patterns = [
            r'/p/([A-Za-z0-9_-]+)',
            r'/reel/([A-Za-z0-9_-]+)',
            r'/tv/([A-Za-z0-9_-]+)'
        ]

        for pattern in patterns:
            match = re.search(pattern, permalink)
            if match:
                shortcode = match.group(1)
                logger.info(f"   📋 Extracted shortcode: {shortcode}")
                return shortcode
        return None

    def _find_media_id_by_shortcode(self, shortcode: str) -> Optional[str]:
        """Find media ID by matching shortcode via /{ig-user-id}/media"""
        if not self.ig_business_id:
            logger.error(f"   ❌ No IG Business ID available for media lookup")
            return None

        try:
            url = f"{self.graph_api_url}/{self.ig_business_id}/media"
            params = {
                'fields': 'id,shortcode,permalink',
                'access_token': self.access_token
            }

            logger.info(f"   🔍 Searching media with shortcode: {shortcode}")
            log_api_call("GET", url, params)
            response = requests.get(url, params=params, timeout=15)
            log_api_response(response)

            if response.status_code == 200:
                data = response.json()
                media_list = data.get('data', [])

                for media in media_list:
                    if media.get('shortcode') == shortcode:
                        media_id = media.get('id')
                        logger.info(f"   ✅ Found matching media ID: {media_id}")
                        return media_id

                logger.warning(f"   ⚠️ No media found with shortcode: {shortcode}")
            else:
                logger.error(f"   ❌ Media search failed: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"   💥 Error searching media: {e}")

        return None

    def step3_fetch_product_tags(self, ig_media_id: str) -> List[str]:
        """
        Step 3: Fetch product tags using /product_tags endpoint
        GET /{ig-media-id}/product_tags?fields=product{id,retailer_id,name,price,shop_link}
        """
        logger.info(f"🔍 STEP 3: Fetching product tags for media ID: {ig_media_id}")

        try:
            url = f"{self.graph_api_url}/{ig_media_id}/product_tags"
            params = {
                'fields': 'product{id,retailer_id,name,price,shop_link}',
                'access_token': self.access_token
            }

            log_api_call("GET", url, params)
            response = requests.get(url, params=params, timeout=15)
            log_api_response(response)

            if response.status_code == 200:
                data = response.json()
                tags = data.get('data', [])

                if tags:
                    shop_links = []
                    logger.info(f"   ✅ STEP 3 SUCCESS: Found {len(tags)} product tags")

                    for tag in tags:
                        product = tag.get('product', {})
                        shop_link = product.get('shop_link')

                        if shop_link:
                            shop_links.append(shop_link)
                            product_name = product.get('name', 'Unknown')
                            logger.info(f"   🛍️ Product: {product_name} -> {shop_link}")

                    return shop_links
                else:
                    logger.warning(f"   ⚠️ STEP 3: No product tags found (empty data array)")
                    return []
            else:
                logger.error(f"   ❌ STEP 3 FAILED: API Error {response.status_code} - {response.text}")
                return []

        except Exception as e:
            logger.error(f"   💥 STEP 3 ERROR: {e}")
            return []

    def step4_fallback_methods(self, ig_media_id: str, webhook_data: Dict) -> List[str]:
        """
        Step 4: Handle missing tags with fallback methods
        - Try oEmbed endpoint for embed HTML
        - Try Business Discovery API
        - Extract URLs from caption
        """
        logger.info(f"🔍 STEP 4: Using fallback methods for media ID: {ig_media_id}")
        fallback_urls = []

        # Fallback 4A: Try oEmbed endpoint
        try:
            logger.info(f"   🔄 Fallback 4A: Trying oEmbed endpoint")
            oembed_url = f"{self.graph_api_url}/instagram_oembed"

            # Try to construct permalink for oEmbed
            permalink = f"https://www.instagram.com/p/{ig_media_id}/"  # This might not work for all IDs

            params = {
                'url': permalink,
                'access_token': self.access_token
            }

            log_api_call("GET", oembed_url, params)
            response = requests.get(oembed_url, params=params, timeout=10)

            if response.status_code == 200:
                oembed_data = response.json()
                html = oembed_data.get('html', '')

                # Extract URLs from HTML
                urls = self._extract_urls_from_text(html)
                fallback_urls.extend(urls)
                logger.info(f"   ✅ Fallback 4A: Found {len(urls)} URLs from oEmbed")
            else:
                logger.warning(f"   ⚠️ Fallback 4A failed: {response.status_code}")

        except Exception as e:
            logger.warning(f"   ⚠️ Fallback 4A error: {e}")

        # Fallback 4B: Try Business Discovery (if we can extract username)
        try:
            logger.info(f"   🔄 Fallback 4B: Trying Business Discovery")
            # This would require extracting username from webhook data or other means
            # For now, we'll skip this as it requires more complex username resolution

        except Exception as e:
            logger.warning(f"   ⚠️ Fallback 4B error: {e}")

        # Fallback 4C: Extract URLs from webhook attachment text
        try:
            logger.info(f"   🔄 Fallback 4C: Extracting URLs from webhook text")
            message = webhook_data.get('message', {})
            text = message.get('text', '')

            if text:
                text_urls = self._extract_urls_from_text(text)
                fallback_urls.extend(text_urls)
                logger.info(f"   ✅ Fallback 4C: Found {len(text_urls)} URLs from text")

            # Check attachment payloads for text content
            attachments = message.get('attachments', [])
            for attachment in attachments:
                payload = attachment.get('payload', {})

                # Check title and description
                for field in ['title', 'description']:
                    field_text = payload.get(field, '')
                    if field_text:
                        field_urls = self._extract_urls_from_text(field_text)
                        fallback_urls.extend(field_urls)
                        logger.info(f"   ✅ Fallback 4C: Found {len(field_urls)} URLs from {field}")

        except Exception as e:
            logger.warning(f"   ⚠️ Fallback 4C error: {e}")

        if fallback_urls:
            logger.info(f"   ✅ STEP 4 SUCCESS: Total fallback URLs found: {len(fallback_urls)}")
        else:
            logger.warning(f"   ⚠️ STEP 4: No URLs found via fallback methods")

        return list(set(fallback_urls))  # Remove duplicates

    def step5_handle_paid_vs_organic(self, ig_media_id: str, shop_urls: List[str]) -> List[str]:
        """
        Step 5: Handle paid ads vs organic posts differentiation
        - If no product tags found, this might be a paid ad with CTA-only
        - Log the case for Marketing API follow-up if needed
        """
        logger.info(f"🔍 STEP 5: Analyzing paid vs organic post for media ID: {ig_media_id}")

        if shop_urls:
            logger.info(f"   ✅ STEP 5: Organic post with {len(shop_urls)} product tags found")
            return shop_urls
        else:
            logger.warning(f"   ⚠️ STEP 5: No product tags found - might be paid ad with CTA")
            logger.warning(f"   💡 Note: Paid ad CTA links require Marketing API (Ad/Creative ID needed)")
            logger.warning(f"   💡 Consider this an 'unsupported' case or log for manual review")

            # Return empty list but log the case
            return []

    def _extract_urls_from_text(self, text: str) -> List[str]:
        """Extract URLs from text content"""
        if not text:
            return []

        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text, re.IGNORECASE)

        # Clean up URLs
        cleaned_urls = []
        for url in urls:
            url = url.rstrip('.,;:!?)')
            cleaned_urls.append(url)

        return cleaned_urls

    def extract_product_links(self, webhook_data: Dict) -> List[str]:
        """
        Main method that orchestrates all 5 steps to extract Instagram Ad product links
        """
        logger.info("🚀 STARTING 5-STEP INSTAGRAM AD PRODUCT LINK EXTRACTION")
        logger.info("=" * 60)

        # Add comprehensive webhook analysis
        log_webhook_structure(webhook_data)

        # Validation
        if not self.ig_business_id:
            logger.error("❌ EXTRACTION FAILED: No Instagram Business Account ID available")
            return []

        # Step 2: Resolve IG media ID
        ig_media_id = self.step2_resolve_ig_media_id(webhook_data)
        if not ig_media_id:
            logger.error("❌ EXTRACTION FAILED: Could not resolve IG media ID")
            return []

        # Step 3: Fetch product tags
        shop_urls = self.step3_fetch_product_tags(ig_media_id)

        # Step 4: Use fallback methods if no product tags found
        if not shop_urls:
            logger.info("🔄 No product tags found, trying fallback methods...")
            shop_urls = self.step4_fallback_methods(ig_media_id, webhook_data)

        # Step 5: Handle paid vs organic differentiation
        final_urls = self.step5_handle_paid_vs_organic(ig_media_id, shop_urls)

        logger.info("=" * 60)
        logger.info(f"🏁 EXTRACTION COMPLETE: Found {len(final_urls)} product links")
        for i, url in enumerate(final_urls, 1):
            logger.info(f"   {i}. {url}")

        return final_urls

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
    """Process Instagram message and extract shop URLs using 5-step method"""

    logger.info("📨 PROCESSING INSTAGRAM MESSAGE")
    logger.info(f"   🔑 PAGE_ACCESS_TOKEN: {'✅ Set' if config.PAGE_ACCESS_TOKEN else '❌ Missing'}")
    logger.info(f"   🆔 PAGE_ID: {'✅ Set' if config.PAGE_ID else '❌ Missing'} ({config.PAGE_ID})")
    logger.info(f"   🆔 IG_BUSINESS_ID: {'✅ Set' if config.INSTAGRAM_BUSINESS_ACCOUNT_ID else '❌ Missing'} ({config.INSTAGRAM_BUSINESS_ACCOUNT_ID})")

    # Initialize extractor
    extractor = InstagramAdProductExtractor(
        access_token=config.PAGE_ACCESS_TOKEN,
        page_id=config.PAGE_ID,
        ig_business_id=config.INSTAGRAM_BUSINESS_ACCOUNT_ID
    )

    # Initialize product data
    product_data = ProductData(
        timestamp=datetime.utcnow().isoformat(),
        sender_id=event.get('sender', {}).get('id', 'unknown'),
        message_id=event.get('message', {}).get('mid', 'unknown'),
        post_type='unknown',
        shop_urls=[],
        raw_webhook_data=event
    )

    # Determine post type
    message = event.get('message', {})
    if message.get('is_unsupported'):
        product_data.post_type = 'unsupported_share'

    attachments = message.get('attachments', [])
    for attachment in attachments:
        attachment_type = attachment.get('type')
        if attachment_type == 'ig_reel':
            product_data.post_type = 'ig_reel'
            break
        elif attachment_type == 'share':
            product_data.post_type = 'share'
            break

    # Extract product links using 5-step method
    shop_urls = extractor.extract_product_links(event)
    product_data.shop_urls = shop_urls

    return product_data

def send_acknowledgment(recipient_id: str, product_data: ProductData):
    """Send acknowledgment message back to user"""
    logger.info(f"💬 SENDING ACKNOWLEDGMENT TO: {recipient_id}")

    if not config.PAGE_ACCESS_TOKEN:
        logger.error("❌ Cannot send message - PAGE_ACCESS_TOKEN missing")
        return

    # Prepare message based on results
    if product_data.shop_urls:
        message_text = (
            f"✅ Found {len(product_data.shop_urls)} product link(s)!\n"
            f"🔍 Searching for best prices...\n"
            f"⏱️ This will take 15-30 seconds.\n\n"
            f"Links found:\n"
        )
        for i, url in enumerate(product_data.shop_urls[:3], 1):
            domain = urlparse(url).netloc
            message_text += f"{i}. {domain}\n"

        if len(product_data.shop_urls) > 3:
            message_text += f"... and {len(product_data.shop_urls) - 3} more"

    elif product_data.post_type == 'ig_reel':
        message_text = (
            "📱 I received the reel but couldn't find product links.\n\n"
            "This might be because:\n"
            "1. The reel doesn't have product tags\n"
            "2. It's a paid ad with CTA (requires Marketing API)\n"
            "3. The product links aren't accessible via Graph API\n\n"
            "Try sharing posts with visible product tags."
        )

    else:
        message_text = "No product links found. Please share posts with product tags or Shop Now buttons."

    # Send message
    url = f"{config.GRAPH_API_URL}/me/messages"
    payload = {
        'recipient': {'id': recipient_id},
        'message': {'text': message_text}
    }

    headers = {'Content-Type': 'application/json'}
    params = {'access_token': config.PAGE_ACCESS_TOKEN}

    try:
        logger.info(f"💬 Sending message to {recipient_id}")
        log_api_call("POST", url, params, headers)

        response = requests.post(url, json=payload, params=params, headers=headers, timeout=10)
        log_api_response(response)

        if response.status_code == 200:
            logger.info(f"✅ Message sent successfully")
        else:
            logger.error(f"❌ Message send failed: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"💥 Message send error: {e}")

# ============== WEBHOOK ENDPOINTS ==============

@app.route('/', methods=['GET'])
def home():
    """Home endpoint for health check"""
    return jsonify({
        'status': 'running',
        'service': 'Instagram Ad Product Link Extractor',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '5-Step Method Implementation'
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
            logger.info("📥 Full Webhook Data:")
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
                            message_id = message.get('mid', '')

                            # Skip echo and read receipts
                            if message.get('is_echo') or 'read' in event:
                                continue

                            logger.info(f"📨 Processing message from: {sender_id}")

                            # Extract product links using 5-step method
                            product_data = process_instagram_message(event)

                            # Log results
                            logger.info("🛍️ EXTRACTION RESULTS:")
                            logger.info(f"   📅 Timestamp: {product_data.timestamp}")
                            logger.info(f"   👤 Sender: {product_data.sender_id}")
                            logger.info(f"   📝 Type: {product_data.post_type}")
                            logger.info(f"   🛒 Links: {len(product_data.shop_urls)}")

                            for i, url in enumerate(product_data.shop_urls, 1):
                                logger.info(f"      {i}. {url}")

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
        'timestamp': datetime.utcnow().isoformat(),
        'configs_set': {
            'page_access_token': bool(config.PAGE_ACCESS_TOKEN),
            'app_secret': bool(config.APP_SECRET),
            'page_id': bool(config.PAGE_ID),
            'instagram_business_id': bool(config.INSTAGRAM_BUSINESS_ACCOUNT_ID)
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting Instagram Ad Product Link Extractor on port {port}")
    logger.info(f"📝 Using 5-Step Method Implementation")
    logger.info(f"🐛 Debug Mode: {config.DEBUG_MODE}")
    logger.info(f"🔐 Signature Verification: {config.ENABLE_SIGNATURE_VERIFICATION}")
    app.run(host='0.0.0.0', port=port, debug=config.DEBUG_MODE)