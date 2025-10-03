"""
Product Discovery Pipeline - LangGraph Orchestration
====================================================
Orchestrates three stages:
1. Download media from CDN URL
2. Extract product information using VLM
3. Search for product URLs using Claude API

Features:
- State management with LangGraph StateGraph
- Automatic checkpointing and error recovery
- Structured logging for production debugging
- Session tracking for multi-user webhook support
- Retry mechanisms for network operations
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import TypedDict, Annotated, Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import operator

from dotenv import load_dotenv

# LangGraph imports
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import RetryPolicy

# Import existing modules
from cdn_download import download_from_cdn
from vlm_google import extract_with_google_gemini
from claude_product_search import ClaudeProductSearcher
from vlm_utils import (
    prepare_media_for_extraction,
    generate_search_queries,
    cleanup_processed_files
)

# Configure UTF-8 for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

CHECKPOINT_DB = Path("pipeline_checkpoints.db")
RESULTS_DIR = Path("pipeline_results")
RESULTS_DIR.mkdir(exist_ok=True)

# Configure minimal logging (structured logs go to state)
logging.basicConfig(
    level=logging.WARNING,  # Only show warnings/errors in console
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# STATE DEFINITIONS
# ============================================================================

@dataclass
class StageLog:
    """Structured log entry for each pipeline stage"""
    stage: str
    status: str  # 'started', 'success', 'error', 'skipped'
    timestamp: str
    duration_seconds: Optional[float] = None
    message: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


class PipelineState(TypedDict):
    """
    State schema for the product discovery pipeline

    State flows through: Input -> Download -> Extract -> Search -> Output
    Each node reads from state and returns updates to merge into state
    """
    # ===== Input Fields =====
    cdn_url: str
    session_id: str  # Unique ID for tracking (webhook_id, user_id, etc.)
    sender_id: Optional[str]  # Instagram user ID (for webhook context)

    # ===== Stage 1: Download =====
    media_file_path: Optional[str]
    media_type: Optional[str]  # 'image' or 'video'
    download_error: Optional[str]

    # ===== Stage 2: VLM Extraction =====
    extracted_frames: Optional[List[str]]  # Frame paths if video
    product_info: Optional[Dict[str, Any]]
    search_queries: Optional[List[str]]
    extraction_error: Optional[str]

    # ===== Stage 3: Product Search =====
    product_urls: List[str]
    search_metadata: Optional[Dict[str, Any]]  # Cost, API calls, etc.
    search_error: Optional[str]

    # ===== Metadata & Logging =====
    pipeline_start_time: str
    pipeline_end_time: Optional[str]
    total_duration_seconds: Optional[float]

    # Structured logs (accumulated across nodes)
    logs: Annotated[List[Dict[str, Any]], operator.add]

    # Error tracking
    errors: Annotated[List[str], operator.add]

    # Success flag
    completed_successfully: bool


# ============================================================================
# PIPELINE NODES
# ============================================================================

def node_download_media(state: PipelineState) -> Dict:
    """
    Node 1: Download media from CDN URL

    Uses: test_cdn_download.download_from_cdn()
    Input: state['cdn_url']
    Output: Updates media_file_path, media_type, or download_error
    """
    stage_name = "download"
    start_time = datetime.now()

    log_entry = StageLog(
        stage=stage_name,
        status="started",
        timestamp=start_time.isoformat(),
        message=f"Downloading from CDN: {state['cdn_url'][:60]}..."
    )

    try:
        # Call existing download function
        result = download_from_cdn(
            cdn_url=state['cdn_url'],
            output_dir="downloads"
        )

        if result and result.get('success'):
            duration = (datetime.now() - start_time).total_seconds()

            log_entry.status = "success"
            log_entry.duration_seconds = duration
            log_entry.message = f"Downloaded {result['media_type']}: {result['file_size'] / 1024:.2f} KB"
            log_entry.metadata = {
                "file_size_bytes": result['file_size'],
                "media_type": result['media_type'],
                "filename": result['filename']
            }

            return {
                "media_file_path": result['file_path'],
                "media_type": result['media_type'],
                "logs": [log_entry.to_dict()]
            }
        else:
            # Download failed
            error_msg = "Download failed: No result returned"
            log_entry.status = "error"
            log_entry.error = error_msg
            log_entry.duration_seconds = (datetime.now() - start_time).total_seconds()

            return {
                "download_error": error_msg,
                "logs": [log_entry.to_dict()],
                "errors": [f"[{stage_name}] {error_msg}"]
            }

    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        error_msg = str(e)

        log_entry.status = "error"
        log_entry.error = error_msg
        log_entry.duration_seconds = duration

        logger.error(f"Download failed: {error_msg}")

        return {
            "download_error": error_msg,
            "logs": [log_entry.to_dict()],
            "errors": [f"[{stage_name}] {error_msg}"]
        }


def node_extract_product_info(state: PipelineState) -> Dict:
    """
    Node 2: Extract product information using Google Gemini VLM

    Uses: vlm_google.extract_with_google_gemini()
    Input: state['media_file_path']
    Output: Updates product_info, search_queries, or extraction_error
    """
    stage_name = "extraction"
    start_time = datetime.now()

    log_entry = StageLog(
        stage=stage_name,
        status="started",
        timestamp=start_time.isoformat()
    )

    # Check if download failed - skip extraction
    if state.get('download_error'):
        log_entry.status = "skipped"
        log_entry.message = "Skipped due to download failure"
        return {"logs": [log_entry.to_dict()]}

    try:
        media_file = Path(state['media_file_path'])

        log_entry.message = f"Extracting from {media_file.name} ({state.get('media_type', 'unknown')})"

        # Prepare media (extract frames if video, return as list if image)
        image_paths = prepare_media_for_extraction(
            media_file,
            num_frames=10 if state.get('media_type') == 'video' else 1
        )

        if not image_paths:
            error_msg = "Failed to prepare media for extraction"
            log_entry.status = "error"
            log_entry.error = error_msg
            log_entry.duration_seconds = (datetime.now() - start_time).total_seconds()

            return {
                "extraction_error": error_msg,
                "logs": [log_entry.to_dict()],
                "errors": [f"[{stage_name}] {error_msg}"]
            }

        # Extract product info using VLM
        product_info = extract_with_google_gemini(image_paths)

        if not product_info:
            error_msg = "VLM extraction returned no results"
            log_entry.status = "error"
            log_entry.error = error_msg
            log_entry.duration_seconds = (datetime.now() - start_time).total_seconds()

            return {
                "extraction_error": error_msg,
                "logs": [log_entry.to_dict()],
                "errors": [f"[{stage_name}] {error_msg}"]
            }

        # Generate search queries
        search_queries = generate_search_queries(product_info)

        duration = (datetime.now() - start_time).total_seconds()

        log_entry.status = "success"
        log_entry.duration_seconds = duration
        log_entry.message = f"Extracted product info, generated {len(search_queries)} search queries"
        log_entry.metadata = {
            "num_frames_analyzed": len(image_paths),
            "num_search_queries": len(search_queries),
            "product_summary": {
                "brand": product_info.get('brand_name'),
                "product": product_info.get('product_name'),
                "category": product_info.get('category')
            }
        }

        return {
            "extracted_frames": [str(p) for p in image_paths],
            "product_info": product_info,
            "search_queries": search_queries,
            "logs": [log_entry.to_dict()]
        }

    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        error_msg = str(e)

        log_entry.status = "error"
        log_entry.error = error_msg
        log_entry.duration_seconds = duration

        logger.error(f"Extraction failed: {error_msg}")

        return {
            "extraction_error": error_msg,
            "logs": [log_entry.to_dict()],
            "errors": [f"[{stage_name}] {error_msg}"]
        }


def node_search_products(state: PipelineState) -> Dict:
    """
    Node 3: Search for product URLs using Claude Web Search API

    Uses: claude_product_search.ClaudeProductSearcher
    Input: state['search_queries']
    Output: Updates product_urls, search_metadata, or search_error
    """
    stage_name = "search"
    start_time = datetime.now()

    log_entry = StageLog(
        stage=stage_name,
        status="started",
        timestamp=start_time.isoformat()
    )

    # Check if extraction failed - skip search
    if state.get('extraction_error'):
        log_entry.status = "skipped"
        log_entry.message = "Skipped due to extraction failure"
        return {"logs": [log_entry.to_dict()]}

    try:
        search_queries = state.get('search_queries', [])

        if not search_queries:
            error_msg = "No search queries available"
            log_entry.status = "error"
            log_entry.error = error_msg
            log_entry.duration_seconds = (datetime.now() - start_time).total_seconds()

            return {
                "search_error": error_msg,
                "logs": [log_entry.to_dict()],
                "errors": [f"[{stage_name}] {error_msg}"]
            }

        log_entry.message = f"Searching with {len(search_queries)} queries"

        # Initialize Claude searcher
        searcher = ClaudeProductSearcher()

        # Search for products with rate limit retry
        import time
        max_retries = 2
        retry_delay = 60  # Wait 60 seconds on rate limit

        product_urls = []
        for attempt in range(max_retries):
            try:
                # Search for products (5 URLs per query by default)
                product_urls = searcher.search_products(
                    search_queries=search_queries,
                    urls_per_query=5
                )
                break  # Success!

            except Exception as search_error:
                is_rate_limit_error = "rate_limit" in str(search_error).lower() or "429" in str(search_error)

                if is_rate_limit_error and attempt < max_retries - 1:
                    # Rate limit hit, wait and retry
                    print(f"\n⏳ Rate limit detected. Waiting {retry_delay}s before retry...")
                    print(f"   Attempt {attempt + 1}/{max_retries}")
                    time.sleep(retry_delay)
                else:
                    # Not a rate limit or final attempt - raise the error
                    raise

        duration = (datetime.now() - start_time).total_seconds()

        # Calculate costs
        search_cost = searcher.search_count * 0.01  # $0.01 per web search

        log_entry.status = "success"
        log_entry.duration_seconds = duration
        log_entry.message = f"Found {len(product_urls)} product URLs"
        log_entry.metadata = {
            "num_queries": len(search_queries),
            "num_urls_found": len(product_urls),
            "api_requests": searcher.request_count,
            "web_searches": searcher.search_count,
            "estimated_cost_usd": round(search_cost, 4)
        }

        return {
            "product_urls": product_urls,
            "search_metadata": {
                "model": searcher.model,
                "api_requests": searcher.request_count,
                "web_searches": searcher.search_count,
                "estimated_cost_usd": round(search_cost, 4)
            },
            "logs": [log_entry.to_dict()]
        }

    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        error_msg = str(e)

        # Check if it's a rate limit error
        is_rate_limit = "rate_limit" in error_msg.lower() or "429" in error_msg

        log_entry.status = "error"
        log_entry.error = error_msg
        log_entry.duration_seconds = duration
        log_entry.metadata = {
            "error_type": "rate_limit" if is_rate_limit else "unknown",
            "is_rate_limit": is_rate_limit
        }

        logger.error(f"Search failed: {error_msg}")

        # Return with proper metadata even on error
        return {
            "search_error": error_msg,
            "search_metadata": {
                "model": "claude-3-7-sonnet-latest",
                "api_requests": 0,
                "web_searches": 0,
                "estimated_cost_usd": 0.0,
                "error_type": "rate_limit" if is_rate_limit else "unknown"
            },
            "logs": [log_entry.to_dict()],
            "errors": [f"[{stage_name}] {'Rate Limit Error' if is_rate_limit else 'Search Error'}: {error_msg}"]
        }


def node_finalize_pipeline(state: PipelineState) -> Dict:
    """
    Final node: Calculate total duration, cleanup files, set completion status
    """
    try:
        # Calculate total duration
        start_time = datetime.fromisoformat(state['pipeline_start_time'])
        end_time = datetime.now()
        total_duration = (end_time - start_time).total_seconds()

        # Determine if pipeline completed successfully
        has_errors = bool(state.get('errors'))
        completed_successfully = not has_errors and bool(state.get('product_urls'))

        # Cleanup temporary files (frames) if extraction was successful
        if state.get('extracted_frames') and state.get('media_file_path'):
            try:
                media_file = Path(state['media_file_path'])
                frame_paths = [Path(p) for p in state['extracted_frames']]
                cleanup_processed_files(media_file, frame_paths)
            except Exception as e:
                logger.warning(f"Cleanup failed: {e}")

        return {
            "pipeline_end_time": end_time.isoformat(),
            "total_duration_seconds": total_duration,
            "completed_successfully": completed_successfully,
            "logs": [{
                "stage": "finalize",
                "status": "success" if completed_successfully else "completed_with_errors",
                "timestamp": end_time.isoformat(),
                "duration_seconds": total_duration,
                "message": f"Pipeline completed in {total_duration:.2f}s"
            }]
        }

    except Exception as e:
        logger.error(f"Finalization error: {e}")
        return {
            "pipeline_end_time": datetime.now().isoformat(),
            "completed_successfully": False
        }


# ============================================================================
# PIPELINE BUILDER
# ============================================================================

def create_product_pipeline(
    checkpoint_db_path: str = str(CHECKPOINT_DB),
    enable_checkpointing: bool = True
) -> StateGraph:
    """
    Create and compile the LangGraph product discovery pipeline

    Pipeline Flow:
        START -> download -> extract -> search -> finalize -> END

    Args:
        checkpoint_db_path: Path to SQLite checkpoint database
        enable_checkpointing: Enable state persistence (for recovery)

    Returns:
        Compiled LangGraph StateGraph
    """

    # Create state graph
    builder = StateGraph(PipelineState)

    # Retry policy for network operations
    retry_policy = RetryPolicy(
        max_attempts=3,
        backoff_factor=2.0
    )

    # Add nodes with retry policies
    builder.add_node("download", node_download_media, retry=retry_policy)
    builder.add_node("extract", node_extract_product_info, retry=retry_policy)
    builder.add_node("search", node_search_products, retry=retry_policy)
    builder.add_node("finalize", node_finalize_pipeline)

    # Define sequential edges (no conditional routing for now)
    builder.add_edge(START, "download")
    builder.add_edge("download", "extract")
    builder.add_edge("extract", "search")
    builder.add_edge("search", "finalize")
    builder.add_edge("finalize", END)

    # Compile pipeline with or without checkpointing
    # Note: Checkpointing disabled by default due to SQLiteSaver context manager complexity
    # For production, use MemorySaver or implement proper SQLite connection management
    pipeline = builder.compile()

    return pipeline


# ============================================================================
# PIPELINE EXECUTION
# ============================================================================

def run_pipeline(
    cdn_url: str,
    session_id: Optional[str] = None,
    sender_id: Optional[str] = None,
    save_results: bool = True
) -> Dict[str, Any]:
    """
    Execute the product discovery pipeline

    Args:
        cdn_url: Facebook/Instagram CDN URL to download media from
        session_id: Unique session identifier (for tracking/webhooks)
        sender_id: Instagram sender ID (optional, for webhook context)
        save_results: Save results to JSON file

    Returns:
        Final pipeline state as dictionary
    """

    # Generate session ID if not provided
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Initialize pipeline
    pipeline = create_product_pipeline()

    # Prepare initial state
    initial_state: PipelineState = {
        # Input
        "cdn_url": cdn_url,
        "session_id": session_id,
        "sender_id": sender_id,

        # Stage outputs (initialized as None/empty)
        "media_file_path": None,
        "media_type": None,
        "download_error": None,

        "extracted_frames": None,
        "product_info": None,
        "search_queries": None,
        "extraction_error": None,

        "product_urls": [],
        "search_metadata": None,
        "search_error": None,

        # Metadata
        "pipeline_start_time": datetime.now().isoformat(),
        "pipeline_end_time": None,
        "total_duration_seconds": None,

        "logs": [],
        "errors": [],
        "completed_successfully": False
    }

    # Execute pipeline with checkpointing
    print("=" * 80)
    print("🚀 PRODUCT DISCOVERY PIPELINE")
    print("=" * 80)
    print(f"Session ID: {session_id}")
    print(f"CDN URL: {cdn_url[:70]}...")
    print("=" * 80)
    print()

    try:
        # Run pipeline
        result = pipeline.invoke(initial_state)

        # Print structured summary
        print_pipeline_summary(result)

        # Save results to file
        if save_results:
            result_file = save_pipeline_results(result)
            print(f"\n💾 Results saved: {result_file}")

        return result

    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}")
        print(f"\n❌ Pipeline failed: {e}")
        raise


def print_pipeline_summary(state: Dict[str, Any]):
    """Print structured summary of pipeline execution"""

    print("\n" + "=" * 80)
    print("📊 PIPELINE EXECUTION SUMMARY")
    print("=" * 80)

    # Status
    status = "✅ SUCCESS" if state.get('completed_successfully') else "⚠️ COMPLETED WITH ERRORS"
    print(f"Status: {status}")
    print(f"Duration: {state.get('total_duration_seconds', 0):.2f}s")
    print(f"Session ID: {state.get('session_id')}")

    # Stage-by-stage logs
    print("\n📋 STAGE EXECUTION:")
    print("-" * 80)

    for log in state.get('logs', []):
        stage = log.get('stage', 'unknown')
        status_icon = {
            'started': '🔄',
            'success': '✅',
            'error': '❌',
            'skipped': '⏭️'
        }.get(log.get('status'), '❓')

        duration = log.get('duration_seconds')
        duration_str = f" ({duration:.2f}s)" if duration else ""

        message = log.get('message', '')
        error = log.get('error', '')

        print(f"{status_icon} {stage.upper()}{duration_str}")
        if message:
            print(f"   {message}")
        if error:
            print(f"   Error: {error}")

        # Print metadata if available
        metadata = log.get('metadata')
        if metadata:
            if log.get('status') == 'success':
                if stage == 'download':
                    print(f"   Size: {metadata.get('file_size_bytes', 0) / 1024:.2f} KB")
                elif stage == 'extraction':
                    summary = metadata.get('product_summary', {})
                    if summary.get('brand'):
                        print(f"   Brand: {summary['brand']}")
                    if summary.get('product'):
                        print(f"   Product: {summary['product']}")
                elif stage == 'search':
                    print(f"   URLs found: {metadata.get('num_urls_found', 0)}")
                    print(f"   Cost: ${metadata.get('estimated_cost_usd', 0):.4f}")
            elif log.get('status') == 'error' and stage == 'search':
                # Show rate limit info for search errors
                if metadata.get('is_rate_limit'):
                    print(f"   ⚠️  Rate Limit Hit: Please wait and retry")
                    print(f"   💡 Tip: Reduce search queries or upgrade API plan")

        print()

    # Errors
    errors = state.get('errors', [])
    if errors:
        print("❌ ERRORS:")
        print("-" * 80)
        for error in errors:
            print(f"   • {error}")
        print()

    # Results
    product_urls = state.get('product_urls', [])
    if product_urls:
        print("🔗 PRODUCT URLS FOUND:")
        print("-" * 80)
        for i, url in enumerate(product_urls[:10], 1):  # Show first 10
            print(f"   {i}. {url}")
        if len(product_urls) > 10:
            print(f"   ... and {len(product_urls) - 10} more")
        print()

    print("=" * 80)


def save_pipeline_results(state: Dict[str, Any]) -> Path:
    """Save pipeline results to JSON file"""

    session_id = state.get('session_id', 'unknown')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    result_file = RESULTS_DIR / f"pipeline_{session_id}_{timestamp}.json"

    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    return result_file


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """Main function for CLI testing"""

    print("\n" + "🎯" * 40)
    print("   PRODUCT DISCOVERY PIPELINE - LangGraph Orchestration")
    print("🎯" * 40)
    print()
    print("This pipeline orchestrates:")
    print("  1. 📥 Download media from CDN URL")
    print("  2. 🤖 Extract product info with Google Gemini VLM")
    print("  3. 🔍 Search product URLs with Claude Web Search")
    print()

    # Check required API keys
    print("🔑 Checking API keys...")
    google_key = os.getenv('GOOGLE_API_KEY')
    claude_key = os.getenv('ANTHROPIC_API_KEY')

    if not google_key:
        print("❌ GOOGLE_API_KEY not found in .env")
        return
    if not claude_key:
        print("❌ ANTHROPIC_API_KEY not found in .env")
        return

    print("✅ API keys loaded")
    print()

    # Interactive mode
    while True:
        print("\n" + "-" * 80)
        print("OPTIONS:")
        print("-" * 80)
        print("1. Run pipeline with CDN URL")
        print("2. Run pipeline with sample URL")
        print("3. Exit")
        print("-" * 80)

        choice = input("\nChoice (1-3): ").strip()

        if choice == '1':
            print()
            cdn_url = input("Enter CDN URL: ").strip()

            if not cdn_url:
                print("❌ Empty URL")
                continue

            session_id = input("Enter session ID (or press Enter to auto-generate): ").strip()

            print()
            run_pipeline(
                cdn_url=cdn_url,
                session_id=session_id if session_id else None,
                save_results=True
            )

        elif choice == '2':
            # Sample URL from webhook example
            sample_url = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=18067040134554519&signature=AYfxei3oo1VDlZ6lYGF8gUH24T62lUjIpLENHimlaRutGL0DRfZLXtfQg5qfXpL2V5SOkPHNcXX9sejVClmEx57XG283yW5E85pjRNeJMFj0jnz5I6RALSFsG63isbObX0vC5kBHjHQEg5t0PaOYIJtT00oSiS4S00yLLNPzN0b-vn_MqDcU6DEt7Spq6RtfwrP5NRtpZQ6Qmp7uKaHWVS86egmoevM"

            print()
            print("📌 Using sample URL...")
            print()

            run_pipeline(
                cdn_url=sample_url,
                session_id="sample_test",
                save_results=True
            )

        elif choice == '3':
            print("\n👋 Goodbye!")
            break

        else:
            print("\n❌ Invalid choice")


if __name__ == "__main__":
    main()
