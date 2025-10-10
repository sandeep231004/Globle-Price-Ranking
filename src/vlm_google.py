"""
Google Gemini Vision API for Product Information Extraction
Single-purpose module for Google Gemini VLM inference
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from typing import Dict, List, Optional
from PIL import Image
from vlm_utils import (
    get_latest_media_file,
    prepare_media_for_extraction,
    parse_json_response,
    generate_search_queries,
    save_extraction_results,
    get_enhanced_extraction_prompt,
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


def extract_from_file_path(file_path: str, custom_instruction: str = None, num_frames: int = 10) -> Optional[Dict]:
    """
    Pipeline-friendly extraction: Takes file path, returns extraction data directly (no file saving)

    Args:
        file_path: Path to video or image file
        custom_instruction: Optional custom instruction to focus on specific details
        num_frames: Number of frames to extract from video (default: 10)

    Returns:
        Dictionary with extracted product information including search_queries, or None if failed
    """
    media_file = Path(file_path)

    if not media_file.exists():
        print(f"❌ File not found: {file_path}")
        return None

    print(f"📁 Processing: {media_file.name}")
    print(f"📏 Size: {media_file.stat().st_size / 1024:.2f} KB")
    print()

    # Prepare media (extract frames if video, return as list if image)
    image_paths = prepare_media_for_extraction(media_file, num_frames=num_frames)

    if not image_paths:
        print("❌ Failed to prepare media for extraction")
        return None

    # Extract product information
    product_info = extract_with_google_gemini(image_paths, custom_instruction=custom_instruction)

    if not product_info:
        print("❌ Extraction failed")
        return None

    # Generate search queries if not present
    if 'search_queries' not in product_info or not product_info['search_queries']:
        search_queries = generate_search_queries(product_info)
        product_info['search_queries'] = search_queries

    # Add metadata
    product_info['source_file'] = str(media_file)
    product_info['extraction_timestamp'] = datetime.now().isoformat()
    product_info['model'] = MODEL_NAME
    product_info['num_frames'] = len(image_paths)

    # Cleanup temporary frames (but keep original downloaded file)
    if len(image_paths) > 1:  # Only if frames were extracted (video)
        print("🧹 Cleaning up temporary frames...")
        for frame_path in image_paths:
            if frame_path.exists() and frame_path != media_file:
                frame_path.unlink()

    return product_info


def extract_with_google_gemini(image_paths: List[Path], custom_instruction: str = None) -> Optional[Dict]:
    """
    Extract product information using Google Gemini Vision API

    Args:
        image_paths: List of image paths to analyze
        custom_instruction: Optional custom instruction to focus on specific details
                          (e.g., "Focus on the shoes the person is wearing")

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
        # Get enhanced extraction prompt with additional metadata fields
        prompt = get_extraction_prompt(custom_instruction=custom_instruction)

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
            print(f"🤖 Model: gemini-2.5-flash")
            print("⏳ Waiting for API response...")

            # Build contents
            contents = [{"text": prompt}] + image_parts

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents
            )

            extracted_text = response.text

        else:
            # Use legacy google-generativeai API
            print("🔧 Using legacy google-generativeai API...")
            genai_legacy.configure(api_key=GOOGLE_API_KEY)
            model = genai_legacy.GenerativeModel('gemini-2.5-flash')

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

    # Check for custom instruction from command line arguments
    import sys
    custom_instruction = None
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        # User provided custom instruction as command line argument
        custom_instruction = ' '.join(sys.argv[1:])
        print(f"🎯 Custom Instruction (from command line): {custom_instruction}")
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

    # Ask for custom instruction if not already provided via command line
    if not custom_instruction:
        print("=" * 70)
        print("💬 OPTIONAL: Add a Custom Instruction")
        print("=" * 70)
        print()
        print("Do you want to focus on something specific in the image/video?")
        print()
        print("Examples:")
        print("  • 'Where can I get those shoes?'")
        print("  • 'What watch is he wearing?'")
        print("  • 'Focus on the sunglasses'")
        print("  • 'Extract information about the bag'")
        print()
        print("Press ENTER to skip (analyze everything)")
        print("Or type your specific request:")
        print()

        user_input = input("👉 Your instruction: ").strip()

        if user_input:
            custom_instruction = user_input
            print()
            print(f"✅ Using custom instruction: '{custom_instruction}'")
        else:
            print()
            print("ℹ️ No custom instruction - analyzing all products in the image/video")

        print()

    # Extract product information
    product_info = extract_with_google_gemini(image_paths, custom_instruction=custom_instruction)

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
