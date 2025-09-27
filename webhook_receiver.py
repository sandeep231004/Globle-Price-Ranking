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

class InstagramShoppingExtractor:
    """Enhanced extractor for Instagram shopping URLs from shared content"""

    def __init__(self, access_token: str, ig_business_id: str):
        self.access_token = access_token
        self.ig_business_id = ig_business_id
        self.graph_api_url = "https://graph.facebook.com/v23.0"

    def extract_media_id_from_permalink(self, permalink: str) -> Optional[str]:
        """
        Extract media ID from Instagram permalink
        Example: https://www.instagram.com/p/C4xxxxx/ or /reel/C4xxxxx/
        """
        patterns = [
            r'/p/([A-Za-z0-9_-]+)',
            r'/reel/([A-Za-z0-9_-]+)',
            r'/tv/([A-Za-z0-9_-]+)'
        ]

        for pattern in patterns:
            match = re.search(pattern, permalink)
            if match:
                shortcode = match.group(1)
                logger.info(f"Extracted shortcode: {shortcode}")
                return self.convert_shortcode_to_media_id(shortcode)
        return None

    def convert_shortcode_to_media_id(self, shortcode: str) -> Optional[str]:
        """
        Convert Instagram shortcode to media ID using oembed endpoint
        """
        try:
            # Use Instagram's oembed endpoint to get media information
            oembed_url = f"https://graph.facebook.com/v23.0/instagram_oembed"
            params = {
                'url': f"https://www.instagram.com/p/{shortcode}/",
                'access_token': self.access_token,
                'fields': 'author_id,media_id'
            }

            response = requests.get(oembed_url, params=params)
            if response.status_code == 200:
                data = response.json()
                media_id = data.get('media_id')
                if media_id:
                    logger.info(f"Converted shortcode to media_id: {media_id}")
                    return media_id
        except Exception as e:
            logger.error(f"Error converting shortcode: {e}")
        return None

    def get_media_with_product_tags(self, media_id: str) -> Dict:
        """
        Fetch media details including product tags and shopping information
        """
        logger.info(f"🎯 API CALL: Getting media details for {media_id}")
        logger.info(f"   🔑 Using access token: {self.access_token[:20]}...{self.access_token[-10:] if len(self.access_token) > 30 else ''}")
        logger.info(f"   🆔 Instagram Business ID: {self.ig_business_id}")

        try:
            # Try different ID formats
            media_ids_to_try = [
                media_id,
                f"{self.ig_business_id}_{media_id}",
                f"{media_id}_{self.ig_business_id}"
            ]

            logger.info(f"   📋 Will try {len(media_ids_to_try)} ID formats: {media_ids_to_try}")

            for i, test_id in enumerate(media_ids_to_try, 1):
                url = f"{self.graph_api_url}/{test_id}"

                # Request comprehensive fields including shopping data
                params = {
                    'fields': ','.join([
                        'id',
                        'ig_id',
                        'caption',
                        'media_type',
                        'media_url',
                        'permalink',
                        'username',
                        'timestamp',
                        # Shopping and product fields
                        'product_tags',
                        'shopping_outbound_link',
                        'product_type',
                        'product_appeal_status',
                        # Try to get child media for carousels
                        'children{id,media_url,product_tags}'
                    ]),
                    'access_token': self.access_token
                }

                logger.info(f"   🌐 API Call {i}/{len(media_ids_to_try)}:")
                logger.info(f"      📍 URL: {url}")
                logger.info(f"      📋 Fields: {params['fields']}")
                logger.info(f"      🔑 Token (partial): {self.access_token[:15]}...")

                response = requests.get(url, params=params, timeout=15)

                logger.info(f"   📊 Response {i}: Status {response.status_code}")
                logger.info(f"   📊 Response headers: {dict(response.headers)}")
                logger.info(f"   📊 Response length: {len(response.text)} chars")

                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"   ✅ SUCCESS: Media data retrieved with {len(data)} fields")
                    logger.info(f"   📋 Available fields: {list(data.keys())}")

                    # Log specific important fields
                    if 'product_tags' in data:
                        logger.info(f"   🛍️ Product tags found: {len(data['product_tags'])} items")
                    if 'shopping_outbound_link' in data:
                        logger.info(f"   🔗 Shopping outbound link: {data['shopping_outbound_link']}")
                    if 'caption' in data:
                        caption_preview = data['caption'][:100] if data['caption'] else 'None'
                        logger.info(f"   📝 Caption preview: {caption_preview}")

                    return data

                elif response.status_code == 400:
                    logger.warning(f"   ⚠️ ID format {i} invalid (400): {response.text[:200]}")
                    continue

                else:
                    logger.error(f"   ❌ API Error {response.status_code}: {response.text[:300]}")

                    # Log specific error types
                    if response.status_code == 401:
                        logger.error(f"   🔐 AUTHENTICATION ERROR: Token may be invalid or expired")
                    elif response.status_code == 403:
                        logger.error(f"   🚫 PERMISSION ERROR: Missing required permissions")
                    elif response.status_code == 404:
                        logger.error(f"   🔍 NOT FOUND: Media ID {test_id} doesn't exist")

            logger.warning(f"   ⚠️ All {len(media_ids_to_try)} ID formats failed")

        except requests.Timeout:
            logger.error(f"   ⏰ TIMEOUT: API request timed out after 15 seconds")
        except requests.ConnectionError:
            logger.error(f"   🌐 CONNECTION ERROR: Unable to reach Instagram API")
        except Exception as e:
            logger.error(f"   💥 UNEXPECTED ERROR: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"   📊 Full traceback: {traceback.format_exc()}")

        return {}

    def extract_shopping_urls_from_media(self, media_data: Dict) -> List[str]:
        """
        Extract all shopping URLs from media data
        """
        shopping_urls = []

        # Check for direct shopping outbound link
        if 'shopping_outbound_link' in media_data:
            shopping_urls.append(media_data['shopping_outbound_link'])
            logger.info(f"Found shopping_outbound_link: {media_data['shopping_outbound_link']}")

        # Check for product tags with website URLs
        if 'product_tags' in media_data:
            for tag in media_data.get('product_tags', []):
                if isinstance(tag, dict):
                    # Check for product object with website_url
                    product = tag.get('product', {})
                    if 'website_url' in product:
                        shopping_urls.append(product['website_url'])
                        product_name = product.get('name', 'Unknown')
                        logger.info(f"Found product URL: {product['website_url']} (Product: {product_name})")

                    # Check for direct product_url (legacy format)
                    if 'product_url' in tag:
                        shopping_urls.append(tag['product_url'])
                        logger.info(f"Found product_url: {tag['product_url']}")

        # Check children media (for carousels)
        if 'children' in media_data:
            children = media_data['children']
            if isinstance(children, dict) and 'data' in children:
                for child in children['data']:
                    child_urls = self.extract_shopping_urls_from_media(child)
                    shopping_urls.extend(child_urls)

        # Extract URLs from caption
        if 'caption' in media_data:
            caption_urls = self.extract_urls_from_text(media_data['caption'])
            # Filter to only include likely shopping URLs
            for url in caption_urls:
                if self.is_likely_shopping_url(url):
                    shopping_urls.append(url)
                    logger.info(f"Found shopping URL in caption: {url}")

        return list(set(shopping_urls))  # Remove duplicates

    def extract_urls_from_text(self, text: str) -> List[str]:
        """Extract URLs from text"""
        if not text:
            return []

        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text, re.IGNORECASE)
        return urls

    def is_likely_shopping_url(self, url: str) -> bool:
        """Check if URL is likely a shopping/product URL"""
        # Filter out social media and CDN URLs
        excluded_domains = [
            'instagram.com', 'facebook.com', 'fb.me',
            'lookaside.fbsbx.com', 'scontent', 'twitter.com',
            'youtube.com', 'tiktok.com', 'linkedin.com'
        ]

        parsed = urlparse(url.lower())
        domain = parsed.netloc

        for excluded in excluded_domains:
            if excluded in domain:
                return False

        # Check for common e-commerce indicators (generic patterns)
        shopping_indicators = [
            '/product', '/shop', '/buy', '/store', '/item', '/cart',
            '/checkout', '/order', '/purchase', '/collection',
            'shopify.', '.store', '.shop', '.buy'  # Generic e-commerce platforms
        ]

        url_lower = url.lower()
        for indicator in shopping_indicators:
            if indicator in url_lower:
                return True

        # Check for shortened URLs that might be shopping links
        shorteners = ['bit.ly', 'linktr.ee', 'link.bio', 'linkin.bio']
        for shortener in shorteners:
            if shortener in domain:
                return True

        return False

    def get_business_discovery_data(self, username: str) -> Dict:
        """
        Use Business Discovery API to get public media data
        """
        try:
            url = f"{self.graph_api_url}/{self.ig_business_id}"
            params = {
                'fields': f'business_discovery.username({username}){{media{{caption,media_url,permalink,shopping_outbound_link,product_tags}}}}',
                'access_token': self.access_token
            }

            response = requests.get(url, params=params)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Business Discovery error: {e}")

        return {}

    def process_shared_content(self, webhook_data: Dict) -> List[str]:
        """
        Main method to process shared content and extract shopping URLs
        """
        all_shopping_urls = []

        # Extract from attachments
        message = webhook_data.get('message', {})
        attachments = message.get('attachments', [])

        for attachment in attachments:
            if attachment.get('type') == 'ig_reel':
                payload = attachment.get('payload', {})

                # Try to get permalink from title or other fields
                title = payload.get('title', '')

                # Method 1: Try using reel_video_id with proper format
                reel_id = payload.get('reel_video_id')
                if reel_id:
                    media_data = self.get_media_with_product_tags(reel_id)
                    if media_data:
                        urls = self.extract_shopping_urls_from_media(media_data)
                        all_shopping_urls.extend(urls)

                # Method 2: Extract from title/description
                if title:
                    urls = self.extract_urls_from_text(title)
                    for url in urls:
                        if self.is_likely_shopping_url(url):
                            all_shopping_urls.append(url)

                # Method 3: Try to extract username and use Business Discovery
                # This would require parsing the username from the content

        # Remove duplicates and return
        return list(set(all_shopping_urls))

def analyze_caption_for_products(caption: str) -> Dict:
    """
    Analyze caption for product mentions and shopping hints
    """
    product_hints = {
        'brand_mentions': [],
        'product_keywords': [],
        'price_mentions': [],
        'call_to_actions': []
    }

    # Extract brand mentions (detect any branded mentions)
    brand_patterns = [
        r'@[a-zA-Z0-9_.]+',  # Instagram handles
        r'#[a-zA-Z0-9_]+brand',  # Brand hashtags
        r'#[a-zA-Z0-9_]+official',  # Official hashtags
        r'\b[A-Z][a-zA-Z0-9&\.]+(?:\s+[A-Z][a-zA-Z0-9&\.]*)*\b'  # Capitalized brand names
    ]

    for pattern in brand_patterns:
        brands = re.findall(pattern, caption)
        product_hints['brand_mentions'].extend(brands)

    # Extract price mentions
    price_patterns = [
        r'₹\s*[\d,]+',
        r'Rs\.?\s*[\d,]+',
        r'INR\s*[\d,]+',
        r'\$\s*[\d,]+',
        r'[\d,]+\s*/-'
    ]

    for pattern in price_patterns:
        prices = re.findall(pattern, caption, re.IGNORECASE)
        product_hints['price_mentions'].extend(prices)

    # Extract CTAs
    cta_phrases = [
        'shop now', 'buy now', 'order now', 'get it now',
        'link in bio', 'swipe up', 'check out', 'available at'
    ]

    for cta in cta_phrases:
        if cta in caption.lower():
            product_hints['call_to_actions'].append(cta)

    # Extract product keywords
    product_keywords = [
        'dress', 'shirt', 'shoes', 'bag', 'watch', 'jewelry',
        'makeup', 'skincare', 'perfume', 'electronics'
    ]

    for keyword in product_keywords:
        if keyword in caption.lower():
            product_hints['product_keywords'].append(keyword)

    return product_hints

def debug_webhook_data(event: Dict):
    """Debug function to analyze webhook structure"""

    logger.info("=== WEBHOOK DATA ANALYSIS ===")

    # Check for different attachment types
    message = event.get('message', {})
    attachments = message.get('attachments', [])

    for i, attachment in enumerate(attachments):
        logger.info(f"Attachment {i+1}:")
        logger.info(f"  Type: {attachment.get('type')}")

        payload = attachment.get('payload', {})
        logger.info(f"  Payload keys: {list(payload.keys())}")

        # For reels, check specific fields
        if attachment.get('type') == 'ig_reel':
            logger.info(f"  Reel video ID: {payload.get('reel_video_id')}")
            logger.info(f"  Title present: {'title' in payload}")
            logger.info(f"  URL: {payload.get('url', 'Not found')[:50]}...")

            # Check if there are any shopping-related fields
            shopping_fields = ['product_id', 'product_url', 'shop_url',
                             'shopping_url', 'merchant_url', 'product_tags']
            for field in shopping_fields:
                if field in payload:
                    logger.info(f"  🛍️ Found shopping field: {field} = {payload[field]}")

    logger.info("=== END ANALYSIS ===")

def enhanced_extract_shop_urls(text: str, attachments: List[Dict], event_data: Dict,
                               access_token: str, ig_business_id: str) -> List[str]:
    """
    Enhanced shop URL extraction using the new extractor class
    """
    extractor = InstagramShoppingExtractor(access_token, ig_business_id)

    # Process the webhook data
    shop_urls = extractor.process_shared_content(event_data)

    # Also extract from text if provided
    if text:
        text_urls = extractor.extract_urls_from_text(text)
        for url in text_urls:
            if extractor.is_likely_shopping_url(url):
                shop_urls.append(url)

    return list(set(shop_urls))

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
            'fields': 'id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,username,product_tags{product{name,website_url}},shopping_product_tags,owner,children{media_url,media_type,product_tags{product{name,website_url}}},comments_count,like_count',
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

def get_ad_creative_link(ad_id: str) -> str:
    """Get the Shop Now/Buy Now destination URL from Ad Creative object"""
    if not config.PAGE_ACCESS_TOKEN:
        logger.warning("No access token available for fetching ad creative")
        return ""

    access_token = config.PAGE_ACCESS_TOKEN

    try:
        url = f"{config.GRAPH_API_URL}/{ad_id}"
        params = {
            "fields": "creative{object_story_spec,object_story_id,object_story_link_data}",
            "access_token": access_token
        }

        logger.info(f"Fetching Ad Creative details for ad_id: {ad_id}")
        response = requests.get(url, params=params, timeout=10)

        logger.info(f"Ad Creative API Response Status: {response.status_code}")
        logger.info(f"Ad Creative API Response: {response.text}")

        if response.status_code == 200:
            data = response.json()
            creative = data.get("creative", {})
            link_data = creative.get("object_story_link_data", {})
            product_link = link_data.get("link", "")

            if product_link:
                cta_type = link_data.get("call_to_action", {}).get("type", "UNKNOWN")
                logger.info(f"Found Ad Creative product URL: {product_link} (CTA: {cta_type})")
                return product_link
            else:
                logger.warning("No link found in Ad Creative object_story_link_data")

        else:
            logger.error(f"Ad creative fetch failed: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error fetching ad creative: {e}")

    return ""

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
    """Process Instagram message and extract shop URLs with enhanced extraction"""

    # 🔐 TOKEN AND CREDENTIALS VALIDATION
    logger.info("🔐 CREDENTIALS CHECK:")
    logger.info(f"   📟 PAGE_ACCESS_TOKEN: {'✅ Set' if config.PAGE_ACCESS_TOKEN else '❌ Missing'} (Length: {len(config.PAGE_ACCESS_TOKEN) if config.PAGE_ACCESS_TOKEN else 0})")
    logger.info(f"   🆔 INSTAGRAM_BUSINESS_ID: {'✅ Set' if config.INSTAGRAM_BUSINESS_ACCOUNT_ID else '❌ Missing'} ({config.INSTAGRAM_BUSINESS_ACCOUNT_ID})")
    logger.info(f"   🔑 APP_SECRET: {'✅ Set' if config.APP_SECRET else '❌ Missing'} (Length: {len(config.APP_SECRET) if config.APP_SECRET else 0})")
    logger.info(f"   📋 VERIFY_TOKEN: {'✅ Set' if config.VERIFY_TOKEN else '❌ Missing'} ({config.VERIFY_TOKEN})")
    logger.info(f"   🌐 GRAPH_API_VERSION: {config.GRAPH_API_VERSION}")

    # Token validation
    if config.PAGE_ACCESS_TOKEN:
        # Extract token parts for debugging (safely)
        token_parts = config.PAGE_ACCESS_TOKEN.split('|')
        if len(token_parts) >= 2:
            logger.info(f"   🔍 Token structure: {len(token_parts)} parts")
            logger.info(f"   🔍 Token prefix: {config.PAGE_ACCESS_TOKEN[:20]}...")
            logger.info(f"   🔍 Token suffix: ...{config.PAGE_ACCESS_TOKEN[-10:]}")
        else:
            logger.warning(f"   ⚠️ Token format unusual: {config.PAGE_ACCESS_TOKEN[:30]}...")

    # Initialize extractor with your credentials
    extractor = InstagramShoppingExtractor(
        access_token=config.PAGE_ACCESS_TOKEN,
        ig_business_id=config.INSTAGRAM_BUSINESS_ACCOUNT_ID
    )

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
        logger.info("📝 Processing unsupported message (likely shared post)")
        product_data.post_type = 'unsupported_share'

    # Extract text and attachments
    text = message.get('text', '')
    attachments = message.get('attachments', [])

    # Add comprehensive debugging
    debug_webhook_data(event)

    # Use enhanced extraction for shopping URLs
    shop_urls = []

    # 🚀 HYBRID APPROACH: Combine working patterns with Instagram API leverage
    for attachment in attachments:
        attachment_type = attachment.get('type')
        payload = attachment.get('payload', {})

        logger.info(f"🔍 Processing attachment type: {attachment_type}")
        logger.info(f"📋 Payload keys: {list(payload.keys())}")

        if attachment_type == 'share':
            # ⭐ PRIMARY: Direct URL extraction (PROVEN WORKING)
            product_data.post_type = 'share'

            # Layer 1: Direct URL extraction (webhook_receiverX.py approach)
            post_url = payload.get('url', '')
            if post_url:
                shop_urls.append(post_url)
                logger.info(f"✅ Layer 1 - Direct shared post URL: {post_url}")

            # Layer 2: Extract URLs from description & title (PROVEN WORKING)
            description = payload.get('description', '')
            if description:
                desc_urls = extractor.extract_urls_from_text(description)
                shop_urls.extend(desc_urls)
                logger.info(f"✅ Layer 2 - Found {len(desc_urls)} URLs in description: {desc_urls}")

            title = payload.get('title', '')
            if title:
                title_urls = extractor.extract_urls_from_text(title)
                shop_urls.extend(title_urls)
                logger.info(f"✅ Layer 2 - Found {len(title_urls)} URLs in title: {title_urls}")

            # Layer 3: 🎯 INSTAGRAM API LEVERAGE - Extract media ID and get detailed data
            if post_url and 'instagram.com' in post_url:
                logger.info(f"🚀 Layer 3 - Instagram API leverage for: {post_url}")
                media_id = extractor.extract_media_id_from_permalink(post_url)
                if media_id:
                    media_data = extractor.get_media_with_product_tags(media_id)
                    if media_data:
                        api_urls = extractor.extract_shopping_urls_from_media(media_data)
                        shop_urls.extend(api_urls)
                        logger.info(f"🎯 API extracted {len(api_urls)} additional URLs: {api_urls}")

                        # BONUS: Extract from API caption
                        if 'caption' in media_data:
                            caption_urls = extractor.extract_urls_from_text(media_data['caption'])
                            filtered_caption_urls = [url for url in caption_urls if extractor.is_likely_shopping_url(url)]
                            shop_urls.extend(filtered_caption_urls)
                            logger.info(f"🎯 API caption extracted {len(filtered_caption_urls)} URLs: {filtered_caption_urls}")

        elif attachment_type == 'ig_reel':
            # 🎬 INSTAGRAM REEL: Maximum API leverage
            product_data.post_type = 'ig_reel'

            # Layer 1: Direct title/description extraction (WORKING)
            title = payload.get('title', '')
            if title:
                title_urls = extractor.extract_urls_from_text(title)
                for url in title_urls:
                    if extractor.is_likely_shopping_url(url):
                        shop_urls.append(url)
                        logger.info(f"✅ Layer 1 - Reel title URL: {url}")

            # Layer 2: 🚀 INSTAGRAM API LEVERAGE for reels
            reel_id = payload.get('reel_video_id')
            if reel_id:
                logger.info(f"🚀 Layer 2 - Instagram API leverage for reel: {reel_id}")

                # Try multiple API approaches for reels
                media_data = extractor.get_media_with_product_tags(reel_id)
                if media_data:
                    # Extract shopping URLs from API
                    api_urls = extractor.extract_shopping_urls_from_media(media_data)
                    shop_urls.extend(api_urls)
                    logger.info(f"🎯 Reel API extracted {len(api_urls)} URLs: {api_urls}")

                    # Extract from API caption with advanced filtering
                    if 'caption' in media_data:
                        caption_text = media_data['caption']
                        caption_urls = extractor.extract_urls_from_text(caption_text)

                        # Advanced filtering for shopping URLs
                        shopping_caption_urls = []
                        for url in caption_urls:
                            if extractor.is_likely_shopping_url(url):
                                shopping_caption_urls.append(url)

                        shop_urls.extend(shopping_caption_urls)
                        logger.info(f"🎯 Reel caption extracted {len(shopping_caption_urls)} shopping URLs: {shopping_caption_urls}")

                        # Analyze caption for shopping hints
                        hints = analyze_caption_for_products(caption_text)
                        if hints['brand_mentions'] or hints['call_to_actions']:
                            logger.info(f"💡 Shopping hints found: {hints}")

                # Alternative API approach: Try Business Discovery if main approach fails
                if not shop_urls and 'permalink' in media_data:
                    permalink = media_data['permalink']
                    logger.info(f"🔄 Trying Business Discovery for: {permalink}")
                    # Extract username from permalink and try Business Discovery
                    username_match = re.search(r'instagram\.com/([^/]+)/', permalink)
                    if username_match:
                        username = username_match.group(1)
                        discovery_data = extractor.get_business_discovery_data(username)
                        if discovery_data:
                            logger.info(f"🎯 Business Discovery data retrieved for @{username}")

        elif attachment_type == 'image' or attachment_type == 'video':
            # 📸🎥 MEDIA ATTACHMENTS: API leverage for media analysis
            product_data.post_type = attachment_type
            media_url = payload.get('url', '')

            if media_url:
                logger.info(f"📎 {attachment_type.title()} attachment: {media_url}")

                # Check if media URL contains shopping indicators
                if extractor.is_likely_shopping_url(media_url):
                    shop_urls.append(media_url)
                    logger.info(f"✅ Shopping URL in {attachment_type}: {media_url}")

        elif attachment_type == 'story_mention':
            # 📱 STORY MENTIONS: Extract with API backup
            product_data.post_type = 'story_mention'
            story_url = payload.get('url', '')

            if story_url:
                shop_urls.append(story_url)
                logger.info(f"✅ Story mention URL: {story_url}")

        # 🔍 COMPREHENSIVE PAYLOAD SCANNING: Extract from any URL field
        for key, value in payload.items():
            if 'url' in key.lower() and isinstance(value, str) and value.startswith('http'):
                if extractor.is_likely_shopping_url(value):
                    shop_urls.append(value)
                    logger.info(f"✅ Found shopping URL in payload.{key}: {value}")

        # 🎯 PAYLOAD INTELLIGENCE: Look for shopping-specific fields
        shopping_fields = ['product_url', 'shop_url', 'shopping_url', 'merchant_url', 'buy_url', 'order_url']
        for field in shopping_fields:
            if field in payload and payload[field]:
                shop_urls.append(payload[field])
                logger.info(f"🛍️ Found shopping field payload.{field}: {payload[field]}")

    # 💬 TEXT MESSAGE PROCESSING: Extract URLs and analyze with API leverage
    if text:
        logger.info(f"💬 Processing text message: {text[:100]}...")
        text_urls = extractor.extract_urls_from_text(text)

        for url in text_urls:
            if extractor.is_likely_shopping_url(url):
                shop_urls.append(url)
                logger.info(f"✅ Shopping URL from text: {url}")

            # 🎯 API LEVERAGE: If text contains Instagram URLs, extract via API
            elif 'instagram.com' in url:
                logger.info(f"🚀 Instagram URL in text - API leverage: {url}")
                media_id = extractor.extract_media_id_from_permalink(url)
                if media_id:
                    media_data = extractor.get_media_with_product_tags(media_id)
                    if media_data:
                        api_urls = extractor.extract_shopping_urls_from_media(media_data)
                        shop_urls.extend(api_urls)
                        logger.info(f"🎯 Text Instagram URL extracted {len(api_urls)} URLs: {api_urls}")

        # Analyze text for shopping hints
        if text:
            hints = analyze_caption_for_products(text)
            if hints['brand_mentions'] or hints['call_to_actions']:
                logger.info(f"💡 Text shopping hints: {hints}")

    # 🔗 INTELLIGENT URL EXPANSION: Enhanced with API fallbacks
    expanded_urls = []
    for url in shop_urls:
        original_url = url

        # Expand shortened URLs
        if any(domain in url for domain in ['bit.ly', 'linktr.ee', 'link.tree', 'tinyurl.com', 'goo.gl', 't.co']):
            try:
                expanded_url = expand_shortened_url(url)
                expanded_urls.append(expanded_url)
                logger.info(f"🔗 Expanded: {original_url} -> {expanded_url}")

                # 🎯 API LEVERAGE: If expanded URL is Instagram, extract via API
                if 'instagram.com' in expanded_url:
                    media_id = extractor.extract_media_id_from_permalink(expanded_url)
                    if media_id:
                        media_data = extractor.get_media_with_product_tags(media_id)
                        if media_data:
                            api_urls = extractor.extract_shopping_urls_from_media(media_data)
                            expanded_urls.extend(api_urls)
                            logger.info(f"🎯 Expanded Instagram URL extracted {len(api_urls)} additional URLs: {api_urls}")

            except Exception as e:
                logger.warning(f"⚠️ URL expansion failed for {url}: {e}")
                expanded_urls.append(url)
        else:
            expanded_urls.append(url)

    # 🧹 INTELLIGENT DEDUPLICATION: Remove duplicates while preserving quality
    unique_urls = []
    seen_domains = set()

    for url in expanded_urls:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            path = parsed.path

            # Create a signature for the URL (domain + important path parts)
            url_signature = f"{domain}{path}"

            if url_signature not in seen_domains:
                unique_urls.append(url)
                seen_domains.add(url_signature)
                logger.info(f"✅ Unique URL added: {url}")
            else:
                logger.info(f"🔄 Duplicate URL filtered: {url}")

        except Exception as e:
            # If URL parsing fails, still include it but check for exact duplicates
            if url not in unique_urls:
                unique_urls.append(url)
                logger.info(f"✅ URL added (parsing failed): {url}")

    # Final assignment
    product_data.shop_urls = unique_urls

    # 📊 EXTRACTION SUMMARY
    logger.info(f"🎯 EXTRACTION COMPLETE:")
    logger.info(f"   📦 Total URLs found: {len(expanded_urls)}")
    logger.info(f"   ✨ Unique URLs: {len(unique_urls)}")
    logger.info(f"   🛍️ Shopping URLs: {[url for url in unique_urls if extractor.is_likely_shopping_url(url)]}")
    logger.info(f"   📱 Instagram URLs processed via API: {len([url for url in unique_urls if 'instagram.com' in url])}")

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
    """Send acknowledgment message back to user with detailed status"""
    logger.info(f"💬 SENDING MESSAGE:")
    logger.info(f"   👤 Recipient ID: {recipient_id}")
    logger.info(f"   🔑 Token available: {'✅ Yes' if config.PAGE_ACCESS_TOKEN else '❌ No'}")

    if not config.PAGE_ACCESS_TOKEN:
        logger.error("❌ Cannot send message - PAGE_ACCESS_TOKEN missing")
        return

    # Log token details
    logger.info(f"   🔑 Token prefix: {config.PAGE_ACCESS_TOKEN[:20]}...")
    logger.info(f"   🔑 Token length: {len(config.PAGE_ACCESS_TOKEN)}")

    # Prepare message based on what was found
    if product_data.shop_urls:
        message_text = (
            f"✅ Found {len(product_data.shop_urls)} shop URL(s)!\\n"
            f"🔍 Searching for best prices...\\n"
            f"⏱️ This will take 15-30 seconds.\\n\\n"
            f"URLs found:\\n"
        )
        for i, url in enumerate(product_data.shop_urls[:3], 1):
            domain = urlparse(url).netloc
            message_text += f"{i}. {domain}\\n"

        if len(product_data.shop_urls) > 3:
            message_text += f"... and {len(product_data.shop_urls) - 3} more"

    elif product_data.post_type == 'ig_reel':
        message_text = (
            "📱 I received the reel but couldn't find shopping URLs.\\n\\n"
            "This might be because:\\n"
            "1. The reel doesn't have product tags\\n"
            "2. It's not a shoppable post\\n"
            "3. The Shop Now button data isn't accessible\\n\\n"
            "Try sharing posts with visible product tags or Shop Now buttons."
        )

    elif product_data.post_type == 'unsupported_share':
        message_text = "📱 I see you shared a post! However, I couldn't access its shopping data. Try sharing posts with product tags."

    else:
        message_text = "No shopping URLs found. Please share posts with:\\n• Product tags\\n• Shop Now buttons\\n• Direct product links"

    logger.info(f"   📝 Message text ({len(message_text)} chars): {message_text[:100]}...")

    # Send the message using the WORKING endpoint from webhook_receiverX.py
    url = f"{config.GRAPH_API_URL}/me/messages"
    payload = {
        'recipient': {'id': recipient_id},
        'message': {'text': message_text}
    }

    headers = {'Content-Type': 'application/json'}
    params = {'access_token': config.PAGE_ACCESS_TOKEN}

    logger.info(f"   📍 Sending to: {url}")
    logger.info(f"   📦 Payload: {json.dumps(payload, indent=2)}")
    logger.info(f"   🔑 Using token: {config.PAGE_ACCESS_TOKEN[:15]}...")

    try:
        logger.info(f"   🚀 Making POST request...")
        response = requests.post(url, json=payload, params=params, headers=headers, timeout=10)

        logger.info(f"   📊 Response status: {response.status_code}")
        logger.info(f"   📊 Response headers: {dict(response.headers)}")
        logger.info(f"   📊 Response text: {response.text}")

        if response.status_code == 200:
            logger.info(f"   ✅ SUCCESS: Message sent to user {recipient_id}")
            response_data = response.json()
            if 'message_id' in response_data:
                logger.info(f"   📨 Message ID: {response_data['message_id']}")
        else:
            logger.error(f"   ❌ FAILED: Status {response.status_code}")
            logger.error(f"   📝 Error response: {response.text}")

            # Specific error handling
            if response.status_code == 400:
                logger.error(f"   🔍 BAD REQUEST: Check payload format or recipient ID")
            elif response.status_code == 401:
                logger.error(f"   🔐 UNAUTHORIZED: Invalid or expired access token")
            elif response.status_code == 403:
                logger.error(f"   🚫 FORBIDDEN: Missing permissions or recipient blocked bot")
            elif response.status_code == 429:
                logger.error(f"   ⏱️ RATE LIMITED: Too many requests")

    except requests.Timeout:
        logger.error(f"   ⏰ TIMEOUT: Request timed out after 10 seconds")
    except requests.ConnectionError:
        logger.error(f"   🌐 CONNECTION ERROR: Unable to reach Facebook API")
    except Exception as e:
        logger.error(f"   💥 UNEXPECTED ERROR: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"   📊 Full traceback: {traceback.format_exc()}")

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

        # Add comprehensive webhook structure analysis
        logger.info("=== RAW WEBHOOK STRUCTURE ===")
        logger.info(f"Object type: {data.get('object')}")
        logger.info(f"Entry count: {len(data.get('entry', []))}")

        for entry in data.get('entry', []):
            logger.info(f"Entry ID: {entry.get('id')}")
            logger.info(f"Entry time: {entry.get('time')}")

            for event in entry.get('messaging', []):
                message = event.get('message', {})
                logger.info(f"Message keys: {list(message.keys())}")

                if 'attachments' in message:
                    for att in message['attachments']:
                        logger.info(f"Attachment type: {att.get('type')}")
                        payload = att.get('payload', {})
                        logger.info(f"Payload keys: {list(payload.keys())}")

                        # Check for any field containing 'shop', 'product', 'buy'
                        for key, value in payload.items():
                            if any(term in key.lower() for term in ['shop', 'product', 'buy', 'url']):
                                logger.info(f"  Found shopping field: {key} = {value}")

        logger.info("=== END STRUCTURE ANALYSIS ===")

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

                            # Log extracted data
                            logger.info("=" * 50)
                            logger.info("🛍️ SHOP URL EXTRACTION RESULTS:")
                            logger.info("=" * 50)
                            logger.info(f"📅 Timestamp: {product_data.timestamp}")
                            logger.info(f"👤 Sender ID: {product_data.sender_id}")
                            logger.info(f"📝 Post Type: {product_data.post_type}")
                            logger.info(f"🛒 Shop URLs Found: {len(product_data.shop_urls)}")

                            for i, url in enumerate(product_data.shop_urls, 1):
                                logger.info(f"   {i}. {url}")
                            logger.info("=" * 50)

                            # Send acknowledgment to user (only once)
                            send_acknowledgment(sender_id, product_data)

                            # Log completion
                            if product_data.shop_urls:
                                logger.info(f"🚀 Ready for price comparison: {len(product_data.shop_urls)} URLs to process")
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