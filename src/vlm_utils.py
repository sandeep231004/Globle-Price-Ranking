"""
Shared utility functions for VLM (Vision Language Model) inference
Provides common functionality for image/video processing, file handling, and result parsing
"""

import os
import json
import cv2
import base64
import requests
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


# Common directories
DOWNLOADS_DIR = Path("downloads")
FRAMES_DIR = Path("extracted_frames")
EXTRACTION_RESULTS_DIR = Path("extraction_results")

# Ensure directories exist
FRAMES_DIR.mkdir(exist_ok=True)
EXTRACTION_RESULTS_DIR.mkdir(exist_ok=True)


def get_latest_media_file() -> Optional[Path]:
    """Get the most recently downloaded media file"""
    if not DOWNLOADS_DIR.exists():
        print("❌ Downloads directory doesn't exist")
        return None

    # Find all video and image files
    media_extensions = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
    media_files = []

    for ext in media_extensions:
        media_files.extend(DOWNLOADS_DIR.glob(f"*{ext}"))

    if not media_files:
        print("❌ No media files found in downloads directory")
        return None

    # Sort by modification time, get latest
    latest_file = max(media_files, key=lambda f: f.stat().st_mtime)
    return latest_file


def extract_frames_from_video(video_path: Path, num_frames: int = 10) -> List[Path]:
    """Extract frames from video for analysis"""
    print(f"🎬 Extracting {num_frames} frames from video...")

    cap = cv2.VideoCapture(str(video_path))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if total_frames == 0:
        print("❌ Could not read video")
        return []

    # Calculate frame intervals
    interval = max(1, total_frames // num_frames)

    frames_paths = []
    frame_count = 0
    extracted = 0

    while cap.isOpened() and extracted < num_frames:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_count % interval == 0:
            # Save frame
            frame_path = FRAMES_DIR / f"{video_path.stem}_frame_{extracted:03d}.jpg"
            cv2.imwrite(str(frame_path), frame)
            frames_paths.append(frame_path)
            extracted += 1
            print(f"   ✓ Frame {extracted}/{num_frames}")

        frame_count += 1

    cap.release()
    print(f"✅ Extracted {len(frames_paths)} frames")
    return frames_paths


def encode_image_to_base64(image_path: Path) -> str:
    """Encode image to base64 for API requests"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def upload_to_tmpfiles(file_path: Path) -> Optional[str]:
    """Upload file to tmpfiles.org for temporary hosting"""
    try:
        with open(file_path, 'rb') as f:
            response = requests.post(
                'https://tmpfiles.org/api/v1/upload',
                files={'file': f},
                timeout=30
            )
        if response.status_code == 200:
            data = response.json()
            # tmpfiles returns URL in format: https://tmpfiles.org/XXXXX
            # Need to change to direct link: https://tmpfiles.org/dl/XXXXX
            url = data.get('data', {}).get('url', '')
            if url:
                url = url.replace('tmpfiles.org/', 'tmpfiles.org/dl/')
                return url
    except Exception as e:
        print(f"⚠️ Upload failed: {e}")
    return None


def clean_json_response(text: str) -> str:
    """Clean JSON response by removing markdown code blocks"""
    cleaned = text.strip()

    # Remove markdown code blocks
    if cleaned.startswith('```json'):
        cleaned = cleaned.split('```json')[1]
    if cleaned.startswith('```'):
        cleaned = cleaned.split('```')[1]
    if cleaned.endswith('```'):
        cleaned = cleaned.rsplit('```', 1)[0]

    return cleaned.strip()


def parse_json_response(text: str) -> Optional[Dict]:
    """Parse JSON response with error handling"""
    try:
        cleaned = clean_json_response(text)
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"⚠️ JSON Parse Error: {e}")
        return None


def extract_search_terms_from_text(text: str) -> List[str]:
    """Extract potential search queries from descriptive text"""
    queries = []

    # Split into sentences
    lines = [line.strip() for line in text.split('.') if line.strip()]

    # Take first 5 meaningful sentences
    for line in lines[:5]:
        if 15 < len(line) < 100:
            queries.append(line)

    return queries if queries else [text[:100]]


def generate_search_queries(product_info: Dict) -> List[str]:
    """Generate optimized search queries from extracted product info"""
    if not product_info:
        return []

    queries = []

    # Use pre-generated search queries if available
    if 'search_queries' in product_info:
        return product_info['search_queries']

    # Generate from brand-product pairs
    if 'brand_product_pairs' in product_info:
        for item in product_info['brand_product_pairs']:
            queries.append(item.get('full_name', f"{item.get('brand', '')} {item.get('product', '')}").strip())

    # Add price-based queries
    if 'prices' in product_info and product_info['prices']:
        first_price = product_info['prices'][0]
        if queries:
            queries.append(f"{queries[0]} price {first_price.get('display', '')}")

    # Add variant-specific queries
    if 'variants' in product_info:
        variants = product_info['variants']
        if queries and any(variants.values()):
            variant_str = ""
            if variants.get('colors'):
                variant_str += f" {variants['colors'][0]}"
            if variants.get('models'):
                variant_str += f" {variants['models'][0]}"
            if variant_str:
                queries.append(f"{queries[0]}{variant_str}")

    # Fallback to product names
    if not queries and 'text_content' in product_info:
        product_names = product_info['text_content'].get('product_names', [])
        queries.extend(product_names[:3])

    # Fallback to raw extraction
    if not queries and 'raw_extraction' in product_info:
        queries = extract_search_terms_from_text(product_info['raw_extraction'])

    return queries if queries else ["product search query"]


def cleanup_processed_files(media_file: Path, extracted_frames: List[Path]) -> None:
    """
    Delete processed media file and its extracted frames after successful extraction

    Args:
        media_file: Original media file from downloads/
        extracted_frames: List of frame paths from extracted_frames/
    """
    try:
        # Delete source media file
        if media_file.exists():
            media_file.unlink()
            print(f"🗑️ Deleted source file: {media_file.name}")

        # Delete extracted frames
        deleted_count = 0
        for frame_path in extracted_frames:
            if frame_path.exists():
                frame_path.unlink()
                deleted_count += 1

        if deleted_count > 0:
            print(f"🗑️ Deleted {deleted_count} extracted frame(s)")

    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")


def save_extraction_results(
    media_file: Path,
    product_info: Dict,
    search_queries: List[str],
    model_name: str,
    num_frames: int = 1
) -> Path:
    """Save extraction results to JSON file"""
    output_file = EXTRACTION_RESULTS_DIR / f"extraction_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'source_file': str(media_file),
            'extraction_timestamp': datetime.now().isoformat(),
            'model': model_name,
            'num_frames': num_frames,
            'product_info': product_info,
            'search_queries': search_queries
        }, f, indent=2, ensure_ascii=False)

    return output_file


def get_extraction_prompt() -> str:
    """Get standard product extraction prompt"""
    return """
PRODUCT EXTRACTION FOR SEARCH API

Analyze this image/video advertisement and extract product information optimized for web search.

CRITICAL: Your output will be sent DIRECTLY to a search engine API. Generate ONLY search-ready queries.

Extract and structure:

1. PRIMARY PRODUCTS: Main products advertised with brand names
2. IDENTIFYING DETAILS: Model names, versions, colors, sizes that help narrow search
3. PRICE CONTEXT: Only if prominently displayed (helps filter results)
4. SEARCH QUERIES: Ready-to-use search strings optimized for e-commerce sites

OUTPUT FORMAT (JSON only, no markdown):
{
  "products": [
    {
      "brand": "Nike",
      "product": "Air Max 270",
      "variant": "Triple Black",
      "category": "sneakers"
    }
  ],
  "search_queries": [
    "Nike Air Max 270 Triple Black buy online",
    "Nike Air Max 270 Black sneakers price"
  ],
  "prices": ["$150", "₹12000"],
  "keywords": ["running shoes", "black sneakers", "air cushion"]
}

SEARCH QUERY OPTIMIZATION RULES:
- Include brand + product + variant in each query
- Add commercial intent keywords: "buy", "price", "online", "shop"
- Keep queries 3-8 words (optimal for search APIs)
- If multiple products, create separate query for each
- Prioritize exact product names over generic descriptions
- Include category/type for better filtering

EXTRACTION PRIORITIES:
1. Brand name (CRITICAL - always extract if visible)
2. Product name/model (CRITICAL)
3. Variant/color/size (HIGH - helps differentiate)
4. Price (MEDIUM - useful for filtering)
5. Generic descriptions (LOW - only if no specific product name)

OMIT:
- Marketing slogans
- Generic CTAs ("Shop Now", "Buy Today")
- Decorative text
- Company addresses/legal text
- Promotional copy without product details
- Bounding boxes

If multiple products are shown:
- Create separate entry for each distinct product
- Generate 1-2 search queries per product
- Maximum 5 total search queries in output

If product details are unclear:
- Use visible category + brand (e.g., "Nike sneakers black")
- Avoid generic queries like "shoes" or "clothing"
- Include any visible distinguishing features

RESPOND WITH ONLY THE JSON - no explanations, no markdown code blocks.
"""


def get_enhanced_extraction_prompt(custom_instruction: str = None) -> str:
    """
    Get enhanced product extraction prompt with additional metadata fields

    Args:
        custom_instruction: Optional custom instruction from user to focus on specific details
                          (e.g., "Focus on the shoes the person is wearing" or
                           "Extract details about the watch in the video")

    Returns:
        Formatted prompt string
    """
    # Build prompt based on whether custom instruction exists
    if custom_instruction:
        base_prompt = f"""
USER-FOCUSED PRODUCT EXTRACTION

🎯 **USER'S SPECIFIC REQUEST**: {custom_instruction}

CRITICAL INSTRUCTION: The user has a SPECIFIC question or request. Focus ONLY on extracting information that answers their request.

Examples:
- If user asks "Where can I get those shoes?" → Extract ONLY shoe information
- If user asks "What watch is he wearing?" → Extract ONLY watch information
- If user asks "Find me that dress" → Extract ONLY dress information

Your task: Answer the user's question by extracting the specific product they're asking about.

EXTRACT THE FOLLOWING INFORMATION (for the specific item the user is asking about):
"""
    else:
        base_prompt = """
COMPREHENSIVE INSTAGRAM POST ANALYSIS & PRODUCT EXTRACTION

Analyze this Instagram post content and extract detailed information for product search and marketing analysis.

EXTRACT THE FOLLOWING INFORMATION:

1. **PRODUCT INFORMATION** (for search optimization):
   - Brand names and product names with model/variant details
   - Colors, sizes, versions that help identify the product
   - Visible prices (if displayed prominently)
   - Product category - BE SPECIFIC and accurate based on actual product function and use

2. **MEDIA TYPE**:
   - Determine if this is: "Image" or "Video"

3. **CREATOR/PERSON IN VIDEO**:
   - Attempt to identify the actual person/people appearing in the image/video
   - Use visual recognition to identify anyone you may recognize - this could be celebrities, public figures, influencers, content creators, or any person with an online presence
   - Do not limit recognition to only famous people - try to identify anyone if their appearance, context, or visible information provides clues to their identity
   - If you can identify them: Provide their name (e.g., "Virat Kohli", "Kylie Jenner", "Sarah Johnson - Fitness Coach")
   - If not identifiable: Describe them objectively (e.g., "Female fitness influencer", "Male model", "Young woman in her 20s")
   - If no person visible: "No person visible" or "Product only"

4. **POST TYPE** (Ad vs Organic):
   - Determine if this post is an "Ad" or "Organic" content
   - Ad indicators: "Sponsored" label, professional production quality, clear product focus with pricing/CTAs, commercial intent language ("Shop Now", "Buy", "Limited Time"), highly polished visuals, explicit promotional messaging
   - Organic indicators: Casual/behind-the-scenes content, lifestyle context without hard selling, user testimonials, brand storytelling, educational content, community engagement focus

5. **CONTENT CREATION TYPE**:
   - Determine who created this content:
     - "Brand Generated": Professional content from brand's marketing team or agency, polished production, official brand messaging
     - "Influencer Generated": Content from social media influencers/creators with substantial following, partnership with brands, authentic personal style mixed with promotion
     - "User Generated": Regular customers/users showcasing products voluntarily, authentic reviews/unboxing, casual production quality, personal testimonials

6. **AD TYPE & MESSAGING** (if applicable):
   - Identify the promotional message: "Sale", "New Launch", or other campaign type
   - Multiple selections possible if post covers multiple themes

7. **MEDIA USE CASE** (select all that apply):
   - "How to Use?": Tutorial or demonstration of product usage
   - "Teaser": Preview or sneak peek of upcoming product
   - "Product Information": Detailed specs, features, benefits
   - "Brand Information": Brand story, values, positioning
   - "Reviews": Customer testimonials or product reviews
   - "Look & Feel": Lifestyle imagery, aesthetic showcase, styling inspiration

8. **WHAT ARE THEY SELLING** (focus/scope):
   - "Brand": Overall brand awareness and identity
   - "Collection": Product line or seasonal collection
   - "Product": Specific individual product(s)
   - Multiple selections possible

9. **SEARCH QUERIES**:
   - Ready-to-use search strings optimized for e-commerce (3-8 words each)
   - Include brand + product + variant, commercial intent keywords

OUTPUT FORMAT (JSON only, no markdown):
{
  "category": "specific product category",
  "brand_information": {
    "content_creation_type": "Brand Generated/Influencer Generated/User Generated",
    "brand_identity": "specific brand name",
    "brand_positioning": "luxury/premium/mid-range/budget/eco-friendly/sustainable/affordable-luxury/mass-market/etc"
  },
  "post_type": "Ad/Organic",
  "creator": "Name of person in video OR description if unknown OR 'No person visible'",
  "media_type": "Image/Video",
  "ad_type_messaging": ["Sale", "New Launch"],
  "media_usecase": ["How to Use?", "Teaser", "Product Information", "Brand Information", "Reviews", "Look & Feel"],
  "what_selling": ["Brand", "Collection", "Product"],
  "products": [
    {
      "brand": "Nike",
      "product": "Air Max 270",
      "variant": "Triple Black",
      "category": "sneakers"
    }
  ],
  "search_queries": [
    "Nike Air Max 270 Triple Black buy online",
    "Nike Air Max 270 Black sneakers price"
  ],
  "prices": ["$150", "₹12000"],
  "keywords": ["running shoes", "black sneakers", "air cushion"]
}

IMPORTANT CLASSIFICATION GUIDELINES:

**For Product Category**:
Categorize products accurately based on their PRIMARY function and use:
- **Healthcare/Medical**: Pain relievers, vitamins, supplements, medicines, first aid, medical devices, health monitors
- **Personal Care**: Hygiene products, oral care, menstrual products, contraceptives
- **Beauty/Cosmetics**: Makeup, skincare, hair styling, fragrances, beauty tools
- **Fashion/Apparel**: Clothing, footwear, accessories, jewelry, watches
- **Electronics**: Phones, computers, gadgets, appliances, audio equipment
- **Food & Beverage**: Groceries, snacks, drinks, supplements
- **Home & Living**: Furniture, decor, kitchenware, bedding, storage
- **Sports & Fitness**: Exercise equipment, activewear, sports gear, nutrition
- **Baby & Kids**: Baby care, toys, children's products
- **Automotive**: Car accessories, parts, maintenance products
- **Pet Care**: Pet food, toys, accessories, grooming

CRITICAL: Choose the category that matches the product's ACTUAL FUNCTION:
- Pain relief gel → "Healthcare/Medical" NOT "Beauty"
- Vitamin supplements → "Healthcare/Medical" NOT "Food & Beverage"
- Hair removal cream → "Personal Care" NOT "Beauty"
- Sunscreen → "Personal Care" OR "Beauty" (both acceptable)
- Protein powder for bodybuilding → "Sports & Fitness" NOT "Food & Beverage"

**For Post Type (Ad vs Organic)**:
- Look for "Sponsored", "Paid partnership", or promotional disclosures
- Assess production quality and commercial intent
- Consider presence of pricing, CTAs, and product-focused messaging

**For Brand Positioning**:
Identify the brand's market positioning based on visual cues, messaging, and product presentation:
- **Luxury/Premium**: High-end brands, sophisticated imagery, premium packaging, exclusive messaging, celebrity endorsements, high price points
- **Affordable Luxury**: Aspirational brands, quality emphasis, accessible premium feel
- **Mid-Range**: Balanced quality and price, mainstream appeal, reliable brand reputation
- **Budget/Mass-Market**: Value-focused, competitive pricing, wide availability, practical messaging
- **Eco-Friendly/Sustainable**: Environmental messaging, natural imagery, sustainability claims, ethical production
- **Performance/Technical**: Innovation-focused, technical specifications, professional/athletic endorsements
- **Artisanal/Craft**: Handmade emphasis, small-batch, traditional methods, unique/limited

Look for indicators:
- Visual aesthetics (minimalist luxury vs vibrant mass-market)
- Language used (exclusive, premium, affordable, value)
- Packaging quality and design
- Price point if visible
- Celebrity/influencer partnerships
- Production quality of content

**For Creator (Person in Video/Image)**:
- Make your best effort to identify ANY person appearing in the content, regardless of fame level
- Use all available visual information: facial features, clothing, context, visible text/logos, setting, and any other identifying details
- Attempt recognition for anyone - from global celebrities to micro-influencers to everyday people with online presence
- If you can identify them (with any degree of confidence), provide the name and context (e.g., "Emma Chen - Beauty YouTuber", "John Smith - Tech Reviewer")
- If you cannot identify them despite attempting, provide a detailed physical description (e.g., "Woman in mid-20s with dark hair, wearing athletic clothing")
- Consider all context clues: watermarks, social media handles visible in frame, brand partnerships, unique styling
- If no person is visible, state "No person visible" or "Product-only shot"

**For Content Creation Type**:
- Brand Generated: Official brand content, professional agency-quality production
- Influencer Generated: Personal brand of creator visible, authentic voice with promotional elements
- User Generated: Casual customer content, authentic personal experience, non-professional production

**For Media Use Case**:
- Select ALL applicable categories (multiple selections expected)
- Consider both primary and secondary purposes of the content

**For What Are They Selling**:
- Brand: Focus on brand identity/values/awareness
- Collection: Multiple products from same line/season
- Product: Specific individual item(s)

RESPOND WITH ONLY THE JSON - no explanations, no markdown code blocks.
"""

    return base_prompt


def is_video_file(file_path: Path) -> bool:
    """Check if file is a video"""
    return file_path.suffix.lower() in ['.mp4', '.mov', '.avi', '.mkv', '.webm']


def is_image_file(file_path: Path) -> bool:
    """Check if file is an image"""
    return file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']


def prepare_media_for_extraction(media_file: Path, num_frames: int = 10) -> List[Path]:
    """Prepare media file for extraction (extract frames if video, return as list if image)"""
    if is_video_file(media_file):
        print("📹 Detected video file")
        return extract_frames_from_video(media_file, num_frames)
    elif is_image_file(media_file):
        print("🖼️ Detected image file")
        return [media_file]
    else:
        print(f"⚠️ Unknown file type: {media_file.suffix}")
        return []
