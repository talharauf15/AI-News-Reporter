#!/usr/bin/env python3
"""
AI / tech news digest.

Pulls from free sources (Hacker News, arXiv, RSS), removes anything already
seen, asks an LLM to rank and summarise, posts the result to Discord.

Runs once a day via GitHub Actions. Costs nothing except the LLM call.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from itertools import zip_longest

import feedparser
import requests

# ---------------------------------------------------------------- config

LOOKBACK_HOURS = 26          # slight overlap with the 24h schedule, dedupe handles it
MAX_ITEMS_TO_LLM = 180       # cap so one noisy day can't blow up the token bill
MIN_ITEMS_TO_SEND = 3        # below this, stay quiet rather than send a junk digest
HN_MIN_POINTS = 40
SEEN_FILE = "seen.json"
SEEN_KEEP = 3000

# Edit this list freely. If a feed 404s the script just skips it.
RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://arstechnica.com/ai/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://simonwillison.net/atom/everything/",
    "https://huggingface.co/blog/feed.xml",
    "https://bair.berkeley.edu/blog/feed.xml",
    "https://export.arxiv.org/rss/cs.AI",
    "https://export.arxiv.org/rss/cs.LG",
]

HN_QUERIES = ["AI", "LLM", "OpenAI", "Anthropic", "machine learning"]

# ---- X / Twitter (optional) --------------------------------------------
# Leave APIFY_TOKEN unset and the script simply skips X. Nothing breaks.
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
APIFY_ACTOR = os.environ.get(
    "APIFY_ACTOR",
    "kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest",
)
X_ACCOUNTS = [
    # Labs and official
    "OpenAI", "AnthropicAI", "GoogleDeepMind", "MistralAI", "huggingface",
    # Researchers and builders
    "sama", "karpathy", "ylecun", "JeffDean", "goodfellow_ian",
    "jackclarkSF", "drjimfan", "hardmaru",
    # Paper and release trackers
    "_akhaliq", "arankomatsuzaki", "rohanpaul_ai",
    # Commentary worth reading
    "emollick", "simonw", "swyx", "amasad",
]
X_SEARCH_TERMS = []          # extra raw queries, added on top of the handles
X_HANDLES_PER_QUERY = 5      # batched with OR — fewer queries, lower minimum fees
X_MIN_LIKES = 100            # engagement floor; smaller accounts need a lower bar
X_MAX_ITEMS = 400            # hard cap on results = hard cap on spend (~$3/mo)

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

UA = {"User-Agent": "ai-digest/1.0 (personal news digest)"}


# ---------------------------------------------------------------- helpers

def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def item_id(url):
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:16]


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen[-SEEN_KEEP:], f, indent=0)


# ---------------------------------------------------------------- sources

def fetch_hackernews(cutoff_ts):
    """Front-page-quality HN stories matching our topics."""
    out = []
    for q in HN_QUERIES:
        url = "https://hn.algolia.com/api/v1/search"
        params = {
            "query": q,
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff_ts},points>{HN_MIN_POINTS}",
            "hitsPerPage": 30,
        }
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            r.raise_for_status()
            for hit in r.json().get("hits", []):
                link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                out.append({
                    "title": hit.get("title", "").strip(),
                    "url": link,
                    "source": f"HN ({hit.get('points', 0)} pts)",
                    "summary": "",
                })
        except Exception as e:
            log(f"  HN query '{q}' failed: {e}")
        time.sleep(0.4)  # be polite to the free API
    log(f"Hacker News: {len(out)} items")
    return out


def _dig(obj, path):
    """Walk a dotted path through nested dicts, returning None if absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if cur not in ("", None) else None


def _first(obj, *paths):
    for p in paths:
        val = _dig(obj, p)
        if val is not None:
            return val
    return None


def build_x_queries(cutoff_dt):
    """Turn X_ACCOUNTS into X advanced-search queries.

    This actor has no "handles" field — you drive it through Search Terms using
    X's own syntax. Handles are batched with OR because the actor charges a
    minimum per query, so 4 queries cost far less than 20.
    """
    since = cutoff_dt.strftime("%Y-%m-%d_%H:%M:%S_UTC")
    terms = []
    for i in range(0, len(X_ACCOUNTS), X_HANDLES_PER_QUERY):
        batch = X_ACCOUNTS[i:i + X_HANDLES_PER_QUERY]
        ors = " OR ".join(f"from:{h}" for h in batch)
        terms.append(f"({ors}) since:{since}")
    return terms + list(X_SEARCH_TERMS)


def fetch_x(cutoff_dt):
    """Pull recent posts from selected X accounts via an Apify actor.

    Input schemas differ between actors. If you swap actors, open its Input tab
    in Apify Console, switch Form -> JSON, and copy the real key names here.
    """
    if not APIFY_TOKEN:
        log("X: no APIFY_TOKEN set, skipping")
        return []

    queries = build_x_queries(cutoff_dt)
    log(f"X: {len(queries)} queries, e.g. {queries[0][:90]}")

    payload = {
        "searchTerms": queries,
        "maxItems": X_MAX_ITEMS,
        "queryType": "Latest",
        "tweetLanguage": "en",
    }

    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
    try:
        r = requests.post(
            url, params={"token": APIFY_TOKEN}, json=payload, timeout=300
        )
        if r.status_code >= 400:
            log(f"X: Apify returned {r.status_code}: {r.text[:300]}")
            return []
        rows = r.json()
    except Exception as e:
        log(f"X: fetch failed: {e}")
        return []

    if rows and isinstance(rows[0], dict):
        log(f"X: field names in first row -> {sorted(rows[0].keys())}")

    # Some actors bill a minimum per call and return filler rows when a query
    # matches nothing. Treat that as an input-schema failure, not as data.
    real = [t for t in rows if isinstance(t, dict)
            and "mock" not in str(t.get("type", "")).lower()]
    if rows and not real:
        log("X: actor returned only mock/filler rows — the query matched nothing.")
        log("X: your `payload` field names are wrong for this actor. Run it once")
        log("X: in Apify Console, click API, and copy the generated input JSON.")
        return []
    rows = real

    out = []
    for t in rows:
        text = _first(t, "text", "full_text", "content")
        link = _first(t, "url", "twitterUrl", "tweetUrl")
        if not text or not link:
            continue

        likes = _first(t, "likeCount", "favorite_count", "likes") or 0
        try:
            likes = int(likes)
        except (TypeError, ValueError):
            likes = 0
        if likes < X_MIN_LIKES:
            continue

        handle = _first(
            t, "author.userName", "author.screen_name", "username", "screenName"
        ) or "x"

        flat = " ".join(text.split())
        out.append({
            "title": flat[:180],
            "url": link,
            "source": f"X @{handle} ({likes} likes)",
            "summary": flat[:400],
        })

    if rows and not out:
        log("X: everything was filtered out. First row was:")
        log(json.dumps(rows[0], default=str)[:1200])

    log(f"X: {len(out)} items from {len(rows)} fetched")
    return out


def fetch_rss(cutoff_dt):
    out = []
    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url, request_headers=UA)
            name = parsed.feed.get("title", feed_url.split("/")[2])
            count = 0
            for entry in parsed.entries[:40]:
                stamp = entry.get("published_parsed") or entry.get("updated_parsed")
                if stamp:
                    published = datetime(*stamp[:6], tzinfo=timezone.utc)
                    if published < cutoff_dt:
                        continue
                link = entry.get("link")
                if not link:
                    continue
                blurb = (entry.get("summary", "") or "")[:400]
                out.append({
                    "title": entry.get("title", "").strip(),
                    "url": link,
                    "source": name,
                    "summary": blurb,
                })
                count += 1
            log(f"  {name}: {count} items")
        except Exception as e:
            log(f"  feed failed {feed_url}: {e}")
    return out


# ---------------------------------------------------------------- the LLM

PROMPT = """You are curating a daily briefing for a working AI/ML/Software engineer who \
wants to stay current with the industry.

Below are {n} items collected in the last 24 hours. Your job:

1. Drop anything that is marketing fluff, listicles, opinion with no new facts, \
or a rehash of something older.
2. Merge items covering the same story into one entry, keeping the best link.
3. Rank what remains by how much it actually matters to a practitioner: model \
releases, research with real results, funding or acquisitions that shift the \
landscape, tooling that changes workflows, regulation with teeth.
4. Keep at most 10 entries. Fewer is fine. A quiet day should produce a short list.

Return ONLY a JSON object, no markdown fences, no preamble, in this exact shape:

{{"items": [
  {{"headline": "short, factual, no hype",
    "why": "one or two sentences on why a practitioner should care",
    "url": "the source link",
    "importance": 1}}
]}}

importance is 1-5 where 5 is "drop what you're doing" and 1 is "mildly interesting".

ITEMS:
{items}
"""


def summarise(items):
    listing = "\n".join(
        f"- [{it['source']}] {it['title']} | {it['url']} | {it['summary'][:200]}"
        for it in items
    )
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "user", "content": PROMPT.format(n=len(items), items=listing)}
        ],
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120,
    )
    if r.status_code != 200:
        log(f"OpenAI error {r.status_code}: {r.text[:500]}")
        r.raise_for_status()

    text = r.json()["choices"][0]["message"]["content"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    return json.loads(text).get("items", [])


# ---------------------------------------------------------------- delivery

def send_discord(entries):
    stars = {5: "🔴", 4: "🟠", 3: "🟡", 2: "⚪", 1: "⚪"}
    header = f"**AI & Tech Digest** — {datetime.now(timezone.utc):%d %b %Y}\n"

    blocks, current = [], header
    for e in entries:
        mark = stars.get(int(e.get("importance", 2)), "⚪")
        chunk = f"\n{mark} **{e['headline']}**\n{e['why']}\n<{e['url']}>\n"
        if len(current) + len(chunk) > 1900:      # Discord hard limit is 2000
            blocks.append(current)
            current = chunk
        else:
            current += chunk
    blocks.append(current)

    for i, block in enumerate(blocks):
        r = requests.post(DISCORD_WEBHOOK, json={"content": block}, timeout=30)
        if r.status_code not in (200, 204):
            log(f"Discord error {r.status_code}: {r.text[:300]}")
        if i < len(blocks) - 1:
            time.sleep(1)
    log(f"Sent {len(entries)} entries in {len(blocks)} message(s)")


# ---------------------------------------------------------------- main

def interleave(groups):
    """Round-robin the sources together.

    Matters because `fresh` gets truncated to MAX_ITEMS_TO_LLM. Plain
    concatenation would put whichever source ran last at the tail, and a busy
    news day would silently drop all of it. Interleaving makes the cut
    proportional instead.
    """
    out = []
    for row in zip_longest(*groups):
        out.extend(item for item in row if item is not None)
    return out


def main():
    missing = [k for k, v in
               [("OPENAI_API_KEY", OPENAI_KEY), ("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK)]
               if not v]
    if missing:
        log(f"Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    cutoff_ts = int(cutoff_dt.timestamp())

    log("Fetching...")
    raw = interleave([
        fetch_hackernews(cutoff_ts),
        fetch_rss(cutoff_dt),
        fetch_x(cutoff_dt),
    ])
    log(f"Collected {len(raw)} raw items")

    seen = load_seen()
    seen_set = set(seen)
    fresh, new_ids = [], []
    for it in raw:
        if not it["title"] or not it["url"]:
            continue
        iid = item_id(it["url"])
        if iid in seen_set:
            continue
        seen_set.add(iid)
        new_ids.append(iid)
        fresh.append(it)

    log(f"{len(fresh)} new after dedupe")

    if len(fresh) < MIN_ITEMS_TO_SEND:
        log("Not enough new material. Staying quiet.")
        save_seen(seen + new_ids)
        return

    fresh = fresh[:MAX_ITEMS_TO_LLM]

    log(f"Summarising with {OPENAI_MODEL}...")
    entries = summarise(fresh)
    log(f"LLM kept {len(entries)} entries")

    if entries:
        send_discord(entries)
    else:
        log("LLM returned nothing worth sending.")

    save_seen(seen + new_ids)
    log("Done.")


if __name__ == "__main__":
    main()