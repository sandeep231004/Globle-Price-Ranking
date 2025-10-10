"""
Claude Web Search Product Finder
Uses Claude API's web_search tool to find product URLs from Instagram ad extractions
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

# Directories
EXTRACTION_RESULTS_DIR = Path("extraction_results")
SEARCH_RESULTS_DIR = Path("search_results")
PIPELINE_RESULTS_DIR = Path("pipeline_results")
SEARCH_RESULTS_DIR.mkdir(exist_ok=True)
PIPELINE_RESULTS_DIR.mkdir(exist_ok=True)


class ClaudeProductSearcher:
    """Claude Web Search wrapper for product URL discovery"""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in .env file")

        self.model = "claude-3-7-sonnet-latest"  # Model with web search capability
        self.request_count = 0
        self.search_count = 0  # Track actual web searches (billed at $10/1000)

    def search_products(self, search_queries: List[str], urls_per_query: int = 10) -> List[str]:
        """
        Search for product URLs using Claude web search tool

        Args:
            search_queries: List of search queries from extraction
            urls_per_query: Number of URLs to return per query (default: 10)

        Returns:
            List of product URLs (urls_per_query × len(search_queries))
        """
        try:
            import anthropic
        except ImportError:
            print("❌ anthropic not installed. Run: pip install anthropic")
            return []

        try:
            client = anthropic.Anthropic(api_key=self.api_key)

            # Combine all queries into single prompt
            queries_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(search_queries))

            prompt = f"""You are a product search assistant. I have the following search queries for products:

{queries_text}

Please perform a web search for EACH query and find exactly {urls_per_query} most relevant product purchase URLs for EACH query.

IMPORTANT INSTRUCTIONS:
1. Search the web for EACH query separately (one web search per query)
2. Return ONLY direct product purchase links (e.g., Amazon, Flipkart, brand websites, online retailers)
3. Prioritize URLs from India-based stores or .in domains
4. Avoid generic category pages, blog posts, or review sites
5. Return exactly {urls_per_query} product URLs per query
6. Total URLs should be approximately {urls_per_query * len(search_queries)} (all queries combined)
7. Format your response as a JSON array of URLs (combine all URLs from all queries)

Response format:
{{
  "product_urls": ["url1", "url2", "url3", ...]
}}"""

            print(f"🔍 Searching for products across {len(search_queries)} queries...")
            print(f"🤖 Model: {self.model}")
            print(f"🌐 Using Claude Web Search (Messages API)")

            # Make API call with web_search tool
            # Perform one web search per query to get urls_per_query URLs per query
            # Cost: $0.01 per search, so len(search_queries) * $0.01 total
            max_searches = len(search_queries)  # One search per query

            response = client.messages.create(
                model=self.model,
                max_tokens=4096,  # Enough tokens for multiple searches and results
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": max_searches,  # One search per query
                    "user_location": {
                        "type": "approximate",
                        "country": "IN",  # India for local product searches
                        "timezone": "Asia/Kolkata"
                    }
                }]
            )

            self.request_count += 1

            # Count actual web searches performed (for cost tracking)
            # Web searches are billed separately at $10/1000 searches
            # Check for tool_use blocks (which indicate web searches were performed)
            tool_uses = 0
            for block in response.content:
                if hasattr(block, 'type'):
                    if block.type == 'tool_use' and hasattr(block, 'name') and block.name == 'web_search':
                        tool_uses += 1

            # If no tool_use blocks found, estimate from max_searches
            if tool_uses == 0:
                # Fallback: Assume all searches were performed if we got results
                tool_uses = max_searches

            self.search_count += tool_uses
            print(f"🔍 Web searches detected: {tool_uses}")

            # Extract response text
            result_text = ""
            for block in response.content:
                if hasattr(block, 'text') and block.text is not None:
                    result_text += block.text

            if not result_text:
                print("❌ Empty response from API")
                return []

            print("\n" + "="*70)
            print("📋 RAW SEARCH RESULT:")
            print("="*70)
            print(result_text[:500] if len(result_text) > 500 else result_text)
            print()

            # Parse JSON response
            try:
                # Try to extract JSON from the response
                result_data = json.loads(result_text)
                urls = result_data.get("product_urls", [])
            except json.JSONDecodeError:
                # If direct JSON parsing fails, try to find JSON in text
                import re
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if json_match:
                    try:
                        result_data = json.loads(json_match.group())
                        urls = result_data.get("product_urls", [])
                    except:
                        # Fallback: extract URLs manually
                        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
                        urls = re.findall(url_pattern, result_text)
                else:
                    # Fallback: extract URLs manually
                    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
                    urls = re.findall(url_pattern, result_text)

            print(f"✅ Found {len(urls)} product URLs ({urls_per_query} per query × {len(search_queries)} queries)")
            print(f"💰 Web searches performed: {self.search_count} (${self.search_count * 0.01:.4f})")
            return urls

        except Exception as e:
            import anthropic

            # Re-raise rate limit errors so pipeline can handle them properly
            if isinstance(e, anthropic.RateLimitError):
                print(f"❌ Search failed: Rate limit exceeded")
                print(f"   Please wait and try again, or upgrade your API plan")
                raise  # Re-raise to let pipeline handle it

            print(f"❌ Search failed: {e}")
            import traceback
            traceback.print_exc()
            return []


def get_unprocessed_extractions() -> List[Path]:
    """Find extraction files that haven't been searched yet with Claude"""
    if not EXTRACTION_RESULTS_DIR.exists():
        return []

    extraction_files = list(EXTRACTION_RESULTS_DIR.glob("extraction_*.json"))
    search_files = set(SEARCH_RESULTS_DIR.glob("search_claude_*.json"))

    # Check which extractions don't have corresponding Claude search results
    unprocessed = []
    for ext_file in extraction_files:
        # Expected search result filename for Claude
        search_file = SEARCH_RESULTS_DIR / f"search_claude_{ext_file.stem.replace('extraction_', '')}.json"
        if search_file not in search_files:
            unprocessed.append(ext_file)

    return sorted(unprocessed, key=lambda f: f.stat().st_mtime)


def search_from_extraction_data(extraction_data: Dict, urls_per_query: int = 5, save_to_pipeline: bool = True) -> Optional[Dict]:
    """
    Pipeline-friendly search: Takes extraction data directly, returns search results (saves to pipeline_results/)

    Args:
        extraction_data: Dictionary containing extraction results with 'search_queries' field
        urls_per_query: Number of URLs to return per query (default: 5)
        save_to_pipeline: Save results to pipeline_results/ folder (default: True)

    Returns:
        Dictionary with search results including product_urls, or None if failed
    """
    print("\n" + "="*70)
    print("🔍 CLAUDE WEB SEARCH - PIPELINE MODE")
    print("="*70)

    search_queries = extraction_data.get('search_queries', [])

    if not search_queries:
        print("⚠️ No search queries found in extraction data")
        return None

    print(f"📊 Using {len(search_queries)} search queries")
    print("🔎 Search queries:")
    for i, query in enumerate(search_queries, 1):
        print(f"   {i}. {query}")
    print()

    # Initialize searcher
    searcher = ClaudeProductSearcher()

    # Search for products
    product_urls = searcher.search_products(search_queries, urls_per_query=urls_per_query)

    if not product_urls:
        print("❌ No product URLs found")
        return None

    # Calculate costs
    search_cost = searcher.search_count * 0.01

    # Create result structure matching pipeline_results format
    result = {
        "source_extraction": extraction_data.get("source_file", ""),
        "extraction_timestamp": extraction_data.get("extraction_timestamp", ""),
        "search_timestamp": datetime.now().isoformat(),
        "model_used": searcher.model,
        "search_method": "claude_web_search",
        "search_queries_used": search_queries,
        "total_urls_found": len(product_urls),
        "api_requests_used": searcher.request_count,
        "web_searches_performed": searcher.search_count,
        "estimated_search_cost_usd": round(search_cost, 4),
        "product_urls": product_urls,
        "extraction_data": extraction_data  # Include full extraction data at the end
    }

    # Save to pipeline_results if requested
    if save_to_pipeline:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = PIPELINE_RESULTS_DIR / f"pipeline_result_{timestamp}.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print("="*70)
        print(f"✅ Search complete!")
        print(f"   Product URLs found: {len(product_urls)}")
        print(f"   API requests: {searcher.request_count}")
        print(f"   Web searches: {searcher.search_count}")
        print(f"   Search cost: ${search_cost:.4f}")
        print(f"💾 Saved to: {output_file.name}")
        print("="*70)

    return result


def search_extraction_file(extraction_file: Path, urls_per_query: int = 5) -> Optional[Path]:
    """
    Search products for a single extraction file using Claude web search

    Args:
        extraction_file: Path to extraction JSON
        urls_per_query: Number of URLs to return per query (default: 10)

    Returns:
        Path to saved search results file
    """
    print("\n" + "="*70)
    print(f"📄 Processing: {extraction_file.name}")
    print("="*70)

    # Load extraction data
    with open(extraction_file, 'r', encoding='utf-8') as f:
        extraction_data = json.load(f)

    search_queries = extraction_data.get('search_queries', [])

    if not search_queries:
        print("⚠️ No search queries found in extraction")
        return None

    print(f"📊 Using {len(search_queries)} search queries")
    print("🔎 Search queries:")
    for i, query in enumerate(search_queries, 1):
        print(f"   {i}. {query}")
    print()

    # Initialize searcher
    searcher = ClaudeProductSearcher()

    # Search for products
    product_urls = searcher.search_products(search_queries, urls_per_query=urls_per_query)

    if not product_urls:
        print("❌ No product URLs found")
        return None

    # Calculate costs
    # Web search: $10/1000 searches
    # Token costs vary by model (input/output)
    search_cost = searcher.search_count * 0.01

    # Create minimal result structure (URLs only to save costs)
    result = {
        "source_extraction": str(extraction_file),
        "extraction_timestamp": extraction_data.get("extraction_timestamp"),
        "search_timestamp": datetime.now().isoformat(),
        "model_used": searcher.model,
        "search_method": "claude_web_search",
        "search_queries_used": search_queries,
        "total_urls_found": len(product_urls),
        "api_requests_used": searcher.request_count,
        "web_searches_performed": searcher.search_count,
        "estimated_search_cost_usd": round(search_cost, 4),
        "product_urls": product_urls
    }

    # Save results with Claude-specific filename
    timestamp = extraction_file.stem.replace('extraction_', '')
    output_file = SEARCH_RESULTS_DIR / f"search_claude_{timestamp}.json"

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("="*70)
    print(f"✅ Search complete!")
    print(f"   Product URLs found: {len(product_urls)}")
    print(f"   API requests: {searcher.request_count}")
    print(f"   Web searches: {searcher.search_count}")
    print(f"   Search cost: ${search_cost:.4f}")
    print(f"💾 Saved to: {output_file.name}")
    print("="*70)

    return output_file


def main():
    """Main function - searches all unprocessed extractions"""
    print("\n" + "="*70)
    print("   CLAUDE WEB SEARCH PRODUCT FINDER")
    print("="*70)

    # Check API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n❌ ANTHROPIC_API_KEY not found in .env file!")
        print("\n📝 To set up:")
        print("1. Get API key from: https://console.anthropic.com/settings/keys")
        print("2. Add to .env file:")
        print("   ANTHROPIC_API_KEY=your_api_key_here")
        print()
        return

    print(f"\n✅ Claude API key loaded")

    # Find unprocessed extractions
    unprocessed = get_unprocessed_extractions()

    if not unprocessed:
        print("\n✅ All extractions have been searched!")
        print("💡 No new extraction files to process")
        return

    print(f"\n📋 Found {len(unprocessed)} unprocessed extraction(s)")

    # Process each
    total_search_cost = 0.0
    for extraction_file in unprocessed:
        result_file = search_extraction_file(extraction_file, urls_per_query=5)
        if result_file:
            # Track cumulative costs
            with open(result_file, 'r', encoding='utf-8') as f:
                result_data = json.load(f)
                total_search_cost += result_data.get('estimated_search_cost_usd', 0)
        print()

    print("="*70)
    print("✅ ALL SEARCHES COMPLETE!")
    print(f"💰 Total estimated search cost: ${total_search_cost:.4f}")
    print("="*70)


if __name__ == "__main__":
    main()
