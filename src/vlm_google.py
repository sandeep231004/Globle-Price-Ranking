"""
Google Gemini Vision API for Product Information Extraction
Single-purpose module for Google Gemini VLM inference
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, List, Optional
from PIL import Image
from vlm_utils import (
    get_latest_media_file,
    prepare_media_for_extraction,
    parse_json_response,
    generate_search_queries,
    save_extraction_results,
    get_extraction_prompt,
    cleanup_processed_files
)

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Load environment variables
load_dotenv()

# Configuration
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
MODEL_NAME = "Google Gemini Vision"


def extract_with_google_gemini(image_paths: List[Path]) -> Optional[Dict]:
    """
    Extract product information using Google Gemini Vision API

    Args:
        image_paths: List of image paths to analyze

    Returns:
        Dictionary with extracted product information or None if failed
    """
    print("=" * 70)
    print("🤖 USING GOOGLE GEMINI VISION FOR EXTRACTION")
    print("=" * 70)

    if not GOOGLE_API_KEY:
        print("❌ GOOGLE_API_KEY not found in environment")
        print("💡 Add to .env file: GOOGLE_API_KEY=your_api_key")
        return None

    # Try modern google-genai first, fallback to google-generativeai
    try:
        from google import genai as genai_modern
        use_modern = True
    except ImportError:
        use_modern = False
        try:
            import google.generativeai as genai_legacy
        except ImportError:
            print("❌ Google AI library not installed")
            print("Install with: pip install google-generativeai")
            return None

    try:
        # Get extraction prompt
        prompt = get_extraction_prompt()

        if use_modern:
            # Use modern google.genai API
            print("🔧 Using modern google.genai API...")
            client = genai_modern.Client(api_key=GOOGLE_API_KEY)

            # Load images as bytes
            image_parts = []
            for img_path in image_paths[:10]:
                try:
                    with open(img_path, 'rb') as f:
                        img_bytes = f.read()
                    image_parts.append({
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": img_bytes
                        }
                    })
                    print(f"   ✓ Loaded: {img_path.name}")
                except Exception as e:
                    print(f"⚠️ Could not load {img_path}: {e}")
                    continue

            if not image_parts:
                print("❌ No images could be loaded")
                return None

            print(f"\n📤 Sending {len(image_parts)} image(s) to Google Gemini...")
            print(f"🤖 Model: gemini-2.0-flash")
            print("⏳ Waiting for API response...")

            # Build contents
            contents = [{"text": prompt}] + image_parts

            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=contents
            )

            extracted_text = response.text

        else:
            # Use legacy google-generativeai API
            print("🔧 Using legacy google-generativeai API...")
            genai_legacy.configure(api_key=GOOGLE_API_KEY)
            model = genai_legacy.GenerativeModel('gemini-2.0-flash')

            # Load images
            images = []
            for img_path in image_paths[:10]:
                try:
                    img = Image.open(img_path)
                    print(f"   ✓ Loaded: {img_path.name}")
                    images.append(img)
                except Exception as e:
                    print(f"⚠️ Could not load {img_path}: {e}")
                    continue

            if not images:
                print("❌ No images could be loaded")
                return None

            print(f"\n📤 Sending {len(images)} image(s) to Google Gemini...")
            print(f"🤖 Model: {model.model_name}")
            print("⏳ Waiting for API response...")

            response = model.generate_content([prompt] + images)
            extracted_text = response.text

        if not extracted_text:
            print("❌ Empty response from API")
            return None

        print("=" * 70)
        print("📋 RAW EXTRACTION RESULT:")
        print("=" * 70)
        print(extracted_text[:500] if len(extracted_text) > 500 else extracted_text)
        print()

        # Parse JSON response
        parsed_result = parse_json_response(extracted_text)

        if parsed_result:
            print("=" * 70)
            print("✅ STRUCTURED EXTRACTION RESULT:")
            print("=" * 70)
            print(json.dumps(parsed_result, indent=2))
            print()
            return parsed_result
        else:
            # Return raw extraction if JSON parsing failed
            print("⚠️ Could not parse as JSON, returning raw extraction")
            from vlm_utils import extract_search_terms_from_text
            return {
                "raw_extraction": extracted_text,
                "search_queries": extract_search_terms_from_text(extracted_text),
                "error": "JSON parsing failed"
            }

    except Exception as e:
        print(f"❌ Error during extraction: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main test function for Google Gemini VLM"""
    print("\n" + "=" * 70)
    print("   GOOGLE GEMINI VISION PRODUCT EXTRACTION")
    print("=" * 70)
    print()

    # Check API key
    if not GOOGLE_API_KEY:
        print("❌ Google API key not found!")
        print()
        print("📝 To set up:")
        print("1. Get your API key from https://makersuite.google.com/app/apikey")
        print("2. Add to .env file:")
        print("   GOOGLE_API_KEY=your_api_key_here")
        print()
        return

    print("✅ Google API key loaded")
    print()

    # Get latest media file
    print("🔍 Looking for latest downloaded media...")
    media_file = get_latest_media_file()

    if not media_file:
        print("💡 Download some media first by sending an Ad to your Instagram Business Account")
        return

    print(f"✅ Found: {media_file.name}")
    print(f"📏 Size: {media_file.stat().st_size / 1024:.2f} KB")
    print()

    # Prepare media (extract frames if video, return as list if image)
    image_paths = prepare_media_for_extraction(media_file, num_frames=10)

    if not image_paths:
        print("❌ Failed to prepare media for extraction")
        return

    print()

    # Extract product information
    product_info = extract_with_google_gemini(image_paths)

    if not product_info:
        print("❌ Extraction failed")
        return

    # Generate search queries
    print("=" * 70)
    print("🔎 OPTIMIZED SEARCH QUERIES:")
    print("=" * 70)

    search_queries = generate_search_queries(product_info)
    for i, query in enumerate(search_queries, 1):
        print(f"{i}. {query}")
    print()

    # Save results
    output_file = save_extraction_results(
        media_file=media_file,
        product_info=product_info,
        search_queries=search_queries,
        model_name=MODEL_NAME,
        num_frames=len(image_paths)
    )

    print(f"💾 Results saved to: {output_file}")
    print()

    # Cleanup processed files
    print("=" * 70)
    print("🧹 CLEANING UP PROCESSED FILES")
    print("=" * 70)
    cleanup_processed_files(media_file, image_paths)
    print()

    print("=" * 70)
    print("✅ EXTRACTION TEST COMPLETE!")
    print("=" * 70)


if __name__ == "__main__":
    main()
