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
    VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', '')
    PAGE_ACCESS_TOKEN = os.environ.get('PAGE_ACCESS_TOKEN', '')
    
    # Instagram Business Account
    INSTAGRAM_BUSINESS_ACCOUNT_ID = os.environ.get('INSTAGRAM_BUSINESS_ACCOUNT_ID', '')
    
    # API Configuration
    GRAPH_API_VERSION = os.environ.get('GRAPH_API_VERSION', 'v18.0')
    GRAPH_API_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    
    # Webhook Security
    ENABLE_SIGNATURE_VERIFICATION = os.environ.get('ENABLE_SIGNATURE_VERIFICATION', 'true').lower() == 'true'
    
    # Data Storage (optional - for later phases)
    DATABASE_URL = os.environ.get('DATABASE_URL', '')
    REDIS_URL = os.environ.get('REDIS_URL', '')
    
    # Debug Mode
    DEBUG_MODE = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'

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
    """
    Verify webhook signature from Instagram/Facebook
    """
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
        
        # Facebook sends signature as "sha256=xxxxx"
        if '=' in signature:
            signature = signature.split('=')[1]
        
        return hmac.compare_digest(expected_sig, signature)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False

def extract_urls_from_text(text: str) -> List[str]:
    """
    Extract all URLs from text (caption, bio, etc.)
    """
    if not text:
        return []
    
    # Comprehensive URL regex pattern
    url_patterns = [
        r'https?://[^\s<>"{}|\\^`\[\]]+',  # Standard URLs
        r'www\.[^\s<>"{}|\\^`\[\]]+',       # URLs starting with www
        r'[a-zA-Z0-9]+\.com/[^\s]*',        # Short domain URLs
        r'bit\.ly/[^\s]+',                  # Bit.ly links
        r'link\.tree/[^\s]+',               # Linktree links
        r'linktr\.ee/[^\s]+',               # Linktree short
    ]
    
    urls = []
    for pattern in url_patterns:
        found_urls = re.findall(pattern, text, re.IGNORECASE)
        urls.extend(found_urls)
    
    # Clean and validate URLs
    cleaned_urls = []
    for url in urls:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        cleaned_urls.append(url.rstrip('.,;:!?)'))
    
    return list(set(cleaned_urls))  # Remove duplicates

def extract_hashtags_mentions(text: str) -> tuple:
    """
    Extract hashtags and mentions from text
    """
    if not text:
        return [], []
    
    hashtags = re.findall(r'#(\w+)', text)
    mentions = re.findall(r'@(\w+)', text)
    
    return hashtags, mentions

def expand_shortened_url(url: str) -> str:
    """
    Expand shortened URLs (bit.ly, etc.) to get final destination
    """
    try:
        response = requests.head(url, allow_redirects=True, timeout=5)
        return response.url
    except:
        return url  # Return original if expansion fails

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
    """
    Process and extract all data from a shared Instagram post
    """
    shared_post = SharedPost(
        sender_id=message_data.get('sender', {}).get('id', ''),
        timestamp=message_data.get('timestamp', 0),
        raw_data=message_data
    )
    
    # Check for attachments (shared posts come as attachments)
    message = message_data.get('message', {})
    attachments = message.get('attachments', [])
    
    all_urls = []
    
    for attachment in attachments:
        attachment_type = attachment.get('type', '')
        payload = attachment.get('payload', {})
        
        # Handle different attachment types
        if attachment_type == 'share':
            # Shared post/ad
            shared_post.post_url = payload.get('url', '')
            if shared_post.post_url:
                all_urls.append(shared_post.post_url)
            
        elif attachment_type == 'image':
            # Shared image
            shared_post.media_url = payload.get('url', '')
            shared_post.media_type = 'image'
            
        elif attachment_type == 'video':
            # Shared video
            shared_post.media_url = payload.get('url', '')
            shared_post.media_type = 'video'
        
        elif attachment_type == 'story_mention':
            # Story mention
            shared_post.post_url = payload.get('url', '')
    
    # Extract text content
    text_content = message.get('text', '')
    if text_content:
        shared_post.caption = text_content
        # Extract URLs from caption
        caption_urls = extract_urls_from_text(text_content)
        all_urls.extend(caption_urls)
    
    # Extract Instagram post ID if available
    if 'id' in payload:
        shared_post.post_id = payload.get('id')
        # Try to get more details via API
        additional_details = get_instagram_media_details(shared_post.post_id)
        if additional_details:
            shared_post.caption = additional_details.get('caption', shared_post.caption)
            shared_post.post_url = additional_details.get('permalink', shared_post.post_url)
    
    # Expand shortened URLs
    expanded_urls = []
    for url in all_urls:
        if any(domain in url for domain in ['bit.ly', 'linktr.ee', 'link.tree']):
            expanded_url = expand_shortened_url(url)
            expanded_urls.append(expanded_url)
        else:
            expanded_urls.append(url)
    
    shared_post.product_urls = list(set(expanded_urls))  # Remove duplicates
    
    return shared_post

def extract_all_data(shared_post: SharedPost) -> ExtractedData:
    """
    Extract all useful data from the shared post
    """
    urls = shared_post.product_urls or []
    caption = shared_post.caption or ""
    
    # Extract hashtags and mentions
    hashtags, mentions = extract_hashtags_mentions(caption)
    
    # Try to identify product tags (Instagram Shopping)
    product_tags = []
    if shared_post.raw_data:
        # Look for product tags in the raw data
        # Instagram sometimes includes these in the payload
        pass  # Implement based on actual data structure
    
    # Identify shop URL if present
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
    """
    Send acknowledgment message back to user
    """
    if not config.PAGE_ACCESS_TOKEN:
        logger.warning("Cannot send message - no page access token")
        return
    
    # Prepare response message
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
    
    # Send via Instagram Messaging API
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
        'service': 'Instagram Webhook Receiver',
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
    """Main webhook handler for Instagram messages"""
    
    # Verify signature (if enabled)
    signature = request.headers.get('X-Hub-Signature-256', '')
    if config.ENABLE_SIGNATURE_VERIFICATION:
        if not verify_signature(request.data, signature):
            logger.error("Invalid signature")
            return Response('Unauthorized', status=401)
    
    # Parse webhook data
    try:
        data = request.get_json()
        
        # Log in debug mode
        if config.DEBUG_MODE:
            logger.info(f"Webhook data: {json.dumps(data, indent=2)}")
        
        # Process Instagram webhooks
        if data.get('object') == 'instagram':
            entries = data.get('entry', [])
            
            for entry in entries:
                # Get messaging events
                messaging_events = entry.get('messaging', [])
                
                for event in messaging_events:
                    sender_id = event.get('sender', {}).get('id')
                    
                    # Skip if it's from our own account
                    if sender_id == config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
                        continue
                    
                    # Process shared post
                    logger.info(f"Processing message from {sender_id}")
                    shared_post = process_shared_post(event)
                    
                    # Extract all data
                    extracted_data = extract_all_data(shared_post)
                    
                    # Log extracted data
                    logger.info(f"Extracted URLs: {extracted_data.urls}")
                    logger.info(f"Instagram Post: {extracted_data.instagram_post_url}")
                    logger.info(f"Hashtags: {extracted_data.hashtags}")
                    
                    # Send acknowledgment
                    send_acknowledgment(sender_id, extracted_data)
                    
                    # TODO: Queue for price comparison processing (Phase 3)
                    # For now, just log the data
                    if extracted_data.urls:
                        logger.info("Ready for price comparison processing:")
                        for url in extracted_data.urls:
                            logger.info(f"  - {url}")
        
        return Response('EVENT_RECEIVED', status=200)
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        if config.DEBUG_MODE:
            import traceback
            logger.error(traceback.format_exc())
        
        # Still return 200 to acknowledge receipt
        return Response('EVENT_RECEIVED', status=200)

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Instagram Webhook Receiver on port {port}")
    logger.info(f"Debug mode: {config.DEBUG_MODE}")
    logger.info(f"Signature verification: {config.ENABLE_SIGNATURE_VERIFICATION}")
    app.run(host='0.0.0.0', port=port, debug=config.DEBUG_MODE)