"""
Streamlined Product Discovery Pipeline
Chains together: CDN Download → VLM Extraction → Claude Search
No intermediate file storage - data passed directly between stages
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Load environment variables
load_dotenv()

# Import pipeline components
sys.path.append(str(Path(__file__).parent))
from cdn_download import download_from_cdn
from vlm_google import extract_from_file_path
from claude_product_search import search_from_extraction_data


def run_pipeline(
    cdn_url: str,
    session_id: str = None,
    sender_id: str = None,
    custom_instruction: str = None,
    urls_per_query: int = 5,
    save_results: bool = True
) -> dict:
    """
    Execute complete pipeline: Download → Extract → Search

    Args:
        cdn_url: Facebook CDN URL to download media from
        session_id: Unique session identifier (for tracking/webhooks)
        sender_id: Instagram sender ID (optional, for webhook context)
        custom_instruction: Optional instruction for focused extraction
        urls_per_query: Number of product URLs to find per search query
        save_results: Save results to JSON file (default: True)

    Returns:
        Dictionary with complete pipeline results, or None if any stage fails
    """
    # Generate session ID if not provided
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    print("\n" + "="*80)
    print("🚀 STARTING PRODUCT DISCOVERY PIPELINE")
    print("="*80)
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🆔 Session ID: {session_id}")
    if sender_id:
        print(f"👤 Sender ID: {sender_id}")
    print()

    pipeline_start = datetime.now()

    # ========================================================================
    # STAGE 1: DOWNLOAD FROM CDN
    # ========================================================================
    print("\n" + "─"*80)
    print("📥 STAGE 1/3: DOWNLOADING MEDIA FROM CDN")
    print("─"*80)

    download_result = download_from_cdn(cdn_url)

    if not download_result or not download_result.get('success'):
        print("\n❌ PIPELINE FAILED: Download stage failed")
        return None

    file_path = download_result['file_path']
    print(f"\n✅ Stage 1 complete: Downloaded to {file_path}")

    # ========================================================================
    # STAGE 2: EXTRACT PRODUCT INFO WITH VLM
    # ========================================================================
    print("\n" + "─"*80)
    print("🤖 STAGE 2/3: EXTRACTING PRODUCT INFORMATION")
    print("─"*80)

    extraction_result = extract_from_file_path(
        file_path=file_path,
        custom_instruction=custom_instruction,
        num_frames=10
    )

    if not extraction_result:
        print("\n❌ PIPELINE FAILED: Extraction stage failed")
        return None

    print(f"\n✅ Stage 2 complete: Extracted {len(extraction_result.get('search_queries', []))} search queries")

    # ========================================================================
    # STAGE 3: SEARCH FOR PRODUCT URLS WITH CLAUDE
    # ========================================================================
    print("\n" + "─"*80)
    print("🔍 STAGE 3/3: SEARCHING FOR PRODUCT URLs")
    print("─"*80)

    search_result = search_from_extraction_data(
        extraction_data=extraction_result,
        urls_per_query=urls_per_query,
        save_to_pipeline=True
    )

    if not search_result:
        print("\n❌ PIPELINE FAILED: Search stage failed")
        return None

    # ========================================================================
    # PIPELINE COMPLETE
    # ========================================================================
    pipeline_end = datetime.now()
    duration = (pipeline_end - pipeline_start).total_seconds()

    print("\n" + "="*80)
    print("✅ PIPELINE COMPLETED SUCCESSFULLY!")
    print("="*80)
    print(f"⏱️  Total duration: {duration:.2f} seconds")
    print(f"📊 Results:")
    print(f"   • Media type: {download_result.get('media_type', 'unknown')}")
    print(f"   • Search queries: {len(extraction_result.get('search_queries', []))}")
    print(f"   • Product URLs found: {search_result.get('total_urls_found', 0)}")
    print(f"   • Saved to: pipeline_results/")
    print("="*80)

    return search_result


def run_pipeline_from_file(file_path: str, custom_instruction: str = None, urls_per_query: int = 5) -> dict:
    """
    Execute pipeline from already-downloaded file (skip download stage)

    Args:
        file_path: Path to local video/image file
        custom_instruction: Optional instruction for focused extraction
        urls_per_query: Number of product URLs to find per search query

    Returns:
        Dictionary with complete pipeline results, or None if any stage fails
    """
    print("\n" + "="*80)
    print("🚀 STARTING PRODUCT DISCOVERY PIPELINE (FROM LOCAL FILE)")
    print("="*80)
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📁 File: {file_path}")
    print()

    pipeline_start = datetime.now()

    # Verify file exists
    if not Path(file_path).exists():
        print(f"\n❌ PIPELINE FAILED: File not found: {file_path}")
        return None

    # ========================================================================
    # STAGE 1: EXTRACT PRODUCT INFO WITH VLM
    # ========================================================================
    print("\n" + "─"*80)
    print("🤖 STAGE 1/2: EXTRACTING PRODUCT INFORMATION")
    print("─"*80)

    extraction_result = extract_from_file_path(
        file_path=file_path,
        custom_instruction=custom_instruction,
        num_frames=10
    )

    if not extraction_result:
        print("\n❌ PIPELINE FAILED: Extraction stage failed")
        return None

    print(f"\n✅ Stage 1 complete: Extracted {len(extraction_result.get('search_queries', []))} search queries")

    # ========================================================================
    # STAGE 2: SEARCH FOR PRODUCT URLS WITH CLAUDE
    # ========================================================================
    print("\n" + "─"*80)
    print("🔍 STAGE 2/2: SEARCHING FOR PRODUCT URLs")
    print("─"*80)

    search_result = search_from_extraction_data(
        extraction_data=extraction_result,
        urls_per_query=urls_per_query,
        save_to_pipeline=True
    )

    if not search_result:
        print("\n❌ PIPELINE FAILED: Search stage failed")
        return None

    # ========================================================================
    # PIPELINE COMPLETE
    # ========================================================================
    pipeline_end = datetime.now()
    duration = (pipeline_end - pipeline_start).total_seconds()

    print("\n" + "="*80)
    print("✅ PIPELINE COMPLETED SUCCESSFULLY!")
    print("="*80)
    print(f"⏱️  Total duration: {duration:.2f} seconds")
    print(f"📊 Results:")
    print(f"   • Search queries: {len(extraction_result.get('search_queries', []))}")
    print(f"   • Product URLs found: {search_result.get('total_urls_found', 0)}")
    print(f"   • Saved to: pipeline_results/")
    print("="*80)

    return search_result


def main():
    """Interactive pipeline runner"""
    print("\n" + "🎯"*40)
    print("   PRODUCT DISCOVERY PIPELINE")
    print("🎯"*40)
    print()
    print("This pipeline:")
    print("  1. Downloads media from CDN")
    print("  2. Extracts product info using Google Gemini")
    print("  3. Searches for product URLs using Claude")
    print("  4. Saves final results to pipeline_results/")
    print()

    # Check API keys
    google_key = os.getenv('GOOGLE_API_KEY')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')

    if not google_key:
        print("❌ GOOGLE_API_KEY not found in .env file")
        print("   Get it from: https://makersuite.google.com/app/apikey")
        return

    if not anthropic_key:
        print("❌ ANTHROPIC_API_KEY not found in .env file")
        print("   Get it from: https://console.anthropic.com/settings/keys")
        return

    print("✅ API keys loaded")
    print()

    # Get mode
    print("─"*80)
    print("📋 SELECT MODE:")
    print("─"*80)
    print("1. Download from CDN URL (full pipeline)")
    print("2. Process existing file (skip download)")
    print("3. Exit")
    print("─"*80)

    choice = input("\nEnter choice (1-3): ").strip()

    if choice == '1':
        # CDN download mode
        print()
        print("📌 Enter CDN URL:")
        cdn_url = input("URL: ").strip()

        if not cdn_url:
            print("❌ No URL provided")
            return

        print()
        print("💬 Optional: Add custom instruction (press Enter to skip)")
        print("   Examples: 'Focus on the shoes', 'Extract watch details'")
        custom_instruction = input("Instruction: ").strip() or None

        print()
        run_pipeline(cdn_url, custom_instruction=custom_instruction, urls_per_query=5)

    elif choice == '2':
        # Local file mode
        print()
        print("📁 Enter file path:")
        file_path = input("Path: ").strip()

        if not file_path:
            print("❌ No file path provided")
            return

        print()
        print("💬 Optional: Add custom instruction (press Enter to skip)")
        print("   Examples: 'Focus on the shoes', 'Extract watch details'")
        custom_instruction = input("Instruction: ").strip() or None

        print()
        run_pipeline_from_file(file_path, custom_instruction=custom_instruction, urls_per_query=5)

    elif choice == '3':
        print("\n👋 Goodbye!")
        return

    else:
        print("\n❌ Invalid choice")


if __name__ == "__main__":
    main()
