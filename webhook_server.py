# webhook_server.py
import os
import logging
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse, Response
import json

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Your verify token - make sure this EXACTLY matches what you put in Facebook
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Globleisbig21")

app = FastAPI()

@app.get("/")
async def root():
    """Root endpoint to verify server is running"""
    return {"status": "Server is running", "webhook_path": "/webhook"}

@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Facebook webhook verification endpoint.
    Facebook sends GET request with these parameters:
    - hub.mode=subscribe
    - hub.verify_token=your_verify_token
    - hub.challenge=random_string_to_echo_back
    """
    # Get query parameters from the request
    params = request.query_params
    
    # Log what we received for debugging
    logger.info(f"Verification request received")
    logger.info(f"Query params: {dict(params)}")
    
    # Extract the parameters (Facebook uses dots in parameter names)
    hub_mode = params.get("hub.mode")
    hub_verify_token = params.get("hub.verify_token")
    hub_challenge = params.get("hub.challenge")
    
    logger.info(f"hub.mode: {hub_mode}")
    logger.info(f"hub.verify_token: {hub_verify_token}")
    logger.info(f"hub.challenge: {hub_challenge}")
    logger.info(f"Expected token: {VERIFY_TOKEN}")
    
    # Verify the token
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully!")
        # Return ONLY the challenge string, nothing else
        return Response(content=hub_challenge, media_type="text/plain")
    else:
        logger.error(f"Verification failed. Mode: {hub_mode}, Token match: {hub_verify_token == VERIFY_TOKEN}")
        raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Handle incoming webhook events from Instagram
    """
    # Get the raw body for signature verification (if needed later)
    body = await request.body()
    
    # Parse JSON data
    try:
        data = await request.json()
        logger.info(f"Webhook received: {json.dumps(data, indent=2)}")
        
        # Process Instagram webhook data
        if data.get("object") == "instagram":
            entries = data.get("entry", [])
            for entry in entries:
                # Handle messaging events
                messaging = entry.get("messaging", [])
                for message_event in messaging:
                    sender_id = message_event.get("sender", {}).get("id")
                    message = message_event.get("message", {})
                    
                    # Check for shared posts/attachments
                    if "attachments" in message:
                        for attachment in message["attachments"]:
                            if attachment.get("type") == "share":
                                logger.info(f"Shared post from {sender_id}")
                                logger.info(f"Attachment data: {attachment}")
                                # TODO: Extract URL from shared post
                    
                    # Check for text messages
                    if "text" in message:
                        logger.info(f"Text message from {sender_id}: {message['text']}")
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
    
    # Always return 200 OK to acknowledge receipt
    return Response(content="EVENT_RECEIVED", media_type="text/plain", status_code=200)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "verify_token_set": bool(VERIFY_TOKEN)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting server on port {port}")
    logger.info(f"Verify token: {VERIFY_TOKEN}")
    uvicorn.run(app, host="0.0.0.0", port=port)