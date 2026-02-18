"""
Fetch Polymarket YES/NO (Up/Down) clobTokenIds from an event link or slug.

Usage:
  python fetch_tokens.py https://polymarket.com/event/btc-updown-5m-1771442700
  python fetch_tokens.py btc-updown-5m-1771442700
"""

# Source GPT-5.1-Codex-Max 

import sys
import json
import requests
from urllib.parse import urlparse

API_BASE = "https://gamma-api.polymarket.com/events/slug/"


def extract_slug(arg: str) -> str:
    if arg.startswith("http"):
        path = urlparse(arg).path.strip("/")
        parts = path.split("/")
        # prefer trailing segment; Polymarket event URLs end with the slug
        return parts[-1] if parts else ""
    return arg


def fetch_event(slug: str) -> dict:
    url = API_BASE + slug
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()

def as_list(x):
    """Gamma sometimes wraps lists as JSON strings."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, list) else []
        except Exception:
            return []
    return []


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    slug = extract_slug(sys.argv[1].strip())
    if not slug:
        print("Could not parse slug from input.")
        sys.exit(1)

    try:
        data = fetch_event(slug)
    except Exception as e:
        print(f"Failed to fetch event '{slug}': {e}")
        sys.exit(1)

    markets = data.get("markets") or []
    if not markets:
        print("No markets found in response.")
        sys.exit(1)

    m = markets[0]
    outcomes = as_list(m.get("outcomes"))
    tokens = as_list(m.get("clobTokenIds"))
    question = m.get("question") or data.get("title") or slug

    if len(outcomes) == 2 and len(tokens) == 2:
        print(f"Question: {question}")
        print(f"{outcomes[0]} token: {tokens[0]}")
        print(f"{outcomes[1]} token: {tokens[1]}")
    else:
        print("Unexpected outcomes/token structure:")
        print(json.dumps({"outcomes": outcomes, "clobTokenIds": tokens}, indent=2))


if __name__ == "__main__":
    main()
