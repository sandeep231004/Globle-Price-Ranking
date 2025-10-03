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
