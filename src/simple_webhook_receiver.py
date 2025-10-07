"""
Simple Webhook Receiver for Instagram Ad Sharing
Captures raw webhook data to extract CDN URLs
"""

import os
import json
import logging
from datetime import datetime
from flask import Flask, request, Response, jsonify
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('webhook_capture.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', 'your_verify_token_here')

# Create directory for captured webhooks
WEBHOOK_DIR = Path('webhook_captures')
WEBHOOK_DIR.mkdir(exist_ok=True)


@app.route('/', methods=['GET'])
def home():
    """Home endpoint"""
    return jsonify({
        'status': '✅ Simple Webhook Receiver Running',
        'purpose': 'Captures raw Instagram webhook data',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Webhook verification endpoint"""
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    logger.info(f"📝 Webhook verification request")
    logger.info(f"   Mode: {mode}")
    logger.info(f"   Token match: {token == VERIFY_TOKEN}")

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully")
        return Response(challenge, status=200, mimetype='text/plain')

    logger.error("❌ Webhook verification failed")
    return Response('Forbidden', status=403)


@app.route('/webhook', methods=['POST'])
def capture_webhook():
    """Capture raw webhook data"""
    try:
        # Get raw data
        raw_data = request.get_data()

        # Parse JSON
        data = request.get_json()

        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f'webhook_{timestamp}.json'
        filepath = WEBHOOK_DIR / filename

        # Save complete webhook data
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'headers': dict(request.headers),
                'data': data
            }, f, indent=2, ensure_ascii=False)

        logger.info("=" * 70)
        logger.info("📥 WEBHOOK RECEIVED")
        logger.info("=" * 70)
        logger.info(f"💾 Saved to: {filepath}")
        logger.info("")
        logger.info("📋 FULL WEBHOOK DATA:")
        logger.info("=" * 70)
        logger.info(json.dumps(data, indent=2, ensure_ascii=False))
        logger.info("=" * 70)

        # Extract CDN URLs if present
        cdn_urls = extract_cdn_urls(data)

        if cdn_urls:
            logger.info("")
            logger.info("🔗 EXTRACTED CDN URLs:")
            logger.info("=" * 70)
            for i, url in enumerate(cdn_urls, 1):
                logger.info(f"{i}. {url}")
            logger.info("=" * 70)

            # Save CDN URLs separately for easy access
            cdn_file = WEBHOOK_DIR / f'cdn_urls_{timestamp}.txt'
            with open(cdn_file, 'w', encoding='utf-8') as f:
                for url in cdn_urls:
                    f.write(f"{url}\n")
            logger.info(f"💾 CDN URLs saved to: {cdn_file}")
        else:
            logger.warning("⚠️ No CDN URLs found in webhook data")

        logger.info("")

        return Response('EVENT_RECEIVED', status=200)

    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return Response('EVENT_RECEIVED', status=200)


def extract_cdn_urls(data: dict) -> list:
    """
    Extract CDN URLs from webhook data

    Args:
        data: Webhook JSON data

    Returns:
        List of CDN URLs found
    """
    cdn_urls = []

    try:
        # Navigate through webhook structure
        entries = data.get('entry', [])

        for entry in entries:
            # Check for messaging events
            messaging = entry.get('messaging', [])

            for event in messaging:
                message = event.get('message', {})
                attachments = message.get('attachments', [])

                # Extract URLs from attachments
                for attachment in attachments:
                    payload = attachment.get('payload', {})
                    url = payload.get('url', '')

                    # Check if it's a CDN URL
                    if url and 'lookaside.fbsbx.com' in url:
                        cdn_urls.append(url)

                        # Log attachment details
                        att_type = attachment.get('type', 'unknown')
                        logger.info(f"   📎 Attachment type: {att_type}")
                        logger.info(f"   🔗 CDN URL found: {url[:80]}...")

    except Exception as e:
        logger.error(f"Error extracting CDN URLs: {e}")

    return cdn_urls


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'webhook_captures_dir': str(WEBHOOK_DIR),
        'total_captures': len(list(WEBHOOK_DIR.glob('webhook_*.json')))
    })


@app.route('/recent', methods=['GET'])
def recent_captures():
    """View recent webhook captures"""
    captures = sorted(WEBHOOK_DIR.glob('webhook_*.json'), key=lambda f: f.stat().st_mtime, reverse=True)

    recent = []
    for capture in captures[:5]:  # Show last 5
        with open(capture, 'r', encoding='utf-8') as f:
            data = json.load(f)
            recent.append({
                'filename': capture.name,
                'timestamp': data.get('timestamp'),
                'preview': str(data.get('data', {}))[:200] + '...'
            })

    return jsonify({
        'total_captures': len(list(WEBHOOK_DIR.glob('webhook_*.json'))),
        'recent_captures': recent
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))  # Different port from main webhook

    logger.info("")
    logger.info("=" * 70)
    logger.info("🚀 SIMPLE WEBHOOK RECEIVER STARTING")
    logger.info("=" * 70)
    logger.info(f"📍 Port: {port}")
    logger.info(f"📁 Captures saved to: {WEBHOOK_DIR.absolute()}")
    logger.info(f"🔑 Verify token: {VERIFY_TOKEN}")
    logger.info("=" * 70)
    logger.info("")

    app.run(host='0.0.0.0', port=port, debug=True)