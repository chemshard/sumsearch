import os
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Callable

import requests
import tldextract
from dotenv import load_dotenv
from flask import Flask, render_template, request

load_dotenv()

app = Flask(__name__)

# Prevent tldextract from trying to download the public suffix list at runtime.
# This makes deployment less annoying.
extractor = tldextract.TLDExtract(suffix_list_urls=())

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "").strip()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

SERPAPI_URL = "https://serpapi.com/search.json"
BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

DEFAULT_BLOCKED_DOMAINS = {
    "reddit.com",
    "quora.com",
    "pinterest.com",
    "medium.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "x.com",
    "twitter.com",
    "wattpad.com",
    "fandom.com",
    "answers.com",
    "wikihow.com",
    "stackexchange.com",
    "stackoverflow.com",
}

# Edit this list based on what you personally trust.
# This does not mean every result from these sites is perfect.
# It just gives them a scoring boost and allows them in strict mode.
TRUSTED_DOMAINS = {
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "nih.gov",
    "nature.com",
    "science.org",
    "cell.com",
    "pnas.org",
    "frontiersin.org",
    "springer.com",
    "sciencedirect.com",
    "wiley.com",
    "britannica.com",
    "khanacademy.org",
    "bio.libretexts.org",
    "chem.libretexts.org",
    "openstax.org",
    "merckmanuals.com",
    "who.int",
    "cdc.gov",
    "noaa.gov",
    "nasa.gov",
    "royalsocietypublishing.org",
    "asm.org",
    "jstor.org",
    "stanford.edu",
    "harvard.edu",
    "mit.edu",
    "cambridge.org",
    "oxfordreference.com",
    "wikipedia.org",
}

STRICT_HOST_SUFFIXES = (
    ".edu",
    ".gov",
    ".edu.sg",
    ".gov.sg",
    ".ac.uk",
    ".edu.au",
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    provider: str
    score: int


@dataclass
class ProviderInfo:
    key: str
    label: str
    env_key: str | None
    needs_key: bool


PROVIDERS = [
    ProviderInfo("serpapi", "SerpApi / Google results", "SERPAPI_KEY", True),
    ProviderInfo("brave", "Brave Search API", "BRAVE_API_KEY", True),
    ProviderInfo("tavily", "Tavily Search API", "TAVILY_API_KEY", True),
    ProviderInfo("demo", "Demo mode, no API key", None, False),
]


def get_env_domain_set(name: str, fallback: set[str]) -> set[str]:
    """
    Lets you override domain lists in .env using comma-separated values.

    Example:
    BLOCKED_DOMAINS=reddit.com,quora.com,pinterest.com
    """
    raw = os.getenv(name, "").strip()

    if not raw:
        return set(fallback)

    return {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }


def hostname(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def registered_domain(url: str) -> str:
    """
    Converts:
      https://old.reddit.com/r/foo -> reddit.com
      https://www.ncbi.nlm.nih.gov/foo -> nih.gov
    """
    try:
        host = hostname(url)
        extracted = extractor(host)

        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"

        return host
    except Exception:
        return ""


def is_blocked(url: str, blocked_domains: set[str]) -> bool:
    host = hostname(url)
    reg_domain = registered_domain(url)

    return (
        host in blocked_domains
        or reg_domain in blocked_domains
        or any(host.endswith("." + blocked) for blocked in blocked_domains)
    )


def is_trusted(url: str) -> bool:
    host = hostname(url)
    reg_domain = registered_domain(url)

    if host in TRUSTED_DOMAINS or reg_domain in TRUSTED_DOMAINS:
        return True

    if any(host.endswith("." + trusted) for trusted in TRUSTED_DOMAINS):
        return True

    if host.endswith(STRICT_HOST_SUFFIXES):
        return True

    return False


def is_wikipedia(url: str) -> bool:
    """
    Returns True for wikipedia.org and its subdomains.
    """
    host = hostname(url)
    reg_domain = registered_domain(url)
    return reg_domain == "wikipedia.org" or host.endswith(".wikipedia.org")


def quality_score(url: str, title: str = "", snippet: str = "") -> int:
    """
    Simple scoring. Not AI. Not a truth detector. Just hygiene.
    """
    host = hostname(url)
    reg_domain = registered_domain(url)

    score = 0

    if is_trusted(url):
        score += 25

    if host.endswith(".edu") or host.endswith(".gov") or host.endswith(".edu.sg") or host.endswith(".gov.sg"):
        score += 15

    if reg_domain.endswith(".org"):
        score += 4

    lowered = f"{url} {title} {snippet}".lower()

    spammy_terms = [
        "top 10",
        "you won't believe",
        "ultimate guide",
        "best tips",
        "affiliate",
        "sponsored",
        "reviewed by",
        "fact checked by",
    ]

    for term in spammy_terms:
        if term in lowered:
            score -= 5

    return score


def google_style_exclusion_query(query: str, blocked_domains: set[str]) -> str:
    exclusions = " ".join(f"-site:{domain}" for domain in sorted(blocked_domains))
    return f"{query} {exclusions}".strip()


def brave_style_exclusion_query(query: str, blocked_domains: set[str]) -> str:
    exclusions = " ".join(f"NOT site:{domain}" for domain in sorted(blocked_domains))
    return f"{query} {exclusions}".strip()


def normalize_and_filter(
    raw_results: list[SearchResult],
    blocked_domains: set[str],
    strict_mode: bool,
    max_results: int = 7,
) -> list[SearchResult]:
    cleaned: list[SearchResult] = []
    seen_urls = set()

    for result in raw_results:
        if not result.url or result.url in seen_urls:
            continue

        if is_blocked(result.url, blocked_domains):
            continue

        if strict_mode and not is_trusted(result.url):
            continue

        result.domain = hostname(result.url)
        result.score = quality_score(result.url, result.title, result.snippet)

        cleaned.append(result)
        seen_urls.add(result.url)

    # Wikipedia should appear first if it is present.
    # Everything else is sorted by the normal quality score.
    cleaned.sort(key=lambda r: (not is_wikipedia(r.url), -r.score, r.domain))
    return cleaned[:max_results]


def search_serpapi(query: str, blocked_domains: set[str], count: int = 15) -> list[SearchResult]:
    if not SERPAPI_KEY:
        raise RuntimeError("Missing SERPAPI_KEY. Add it to your .env file or Render environment variables.")

    q = google_style_exclusion_query(query, blocked_domains)

    params = {
        "engine": "google",
        "q": q,
        "api_key": SERPAPI_KEY,
        "num": min(max(count, 1), 20),
    }

    response = requests.get(SERPAPI_URL, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(f"SerpApi error: {data['error']}")

    results = []
    for item in data.get("organic_results", []):
        results.append(
            SearchResult(
                title=(item.get("title") or "Untitled").strip(),
                url=(item.get("link") or "").strip(),
                snippet=(item.get("snippet") or "").strip(),
                domain="",
                provider="SerpApi",
                score=0,
            )
        )

    return results


def search_brave(query: str, blocked_domains: set[str], count: int = 15) -> list[SearchResult]:
    if not BRAVE_API_KEY:
        raise RuntimeError("Missing BRAVE_API_KEY. Add it to your .env file or Render environment variables.")

    q = brave_style_exclusion_query(query, blocked_domains)

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY,
    }

    params = {
        "q": q,
        "count": min(max(count, 1), 20),
    }

    response = requests.get(BRAVE_WEB_SEARCH_URL, headers=headers, params=params, timeout=15)

    if response.status_code in (401, 403):
        raise RuntimeError("Brave rejected your API key. Check BRAVE_API_KEY.")

    if response.status_code == 429:
        raise RuntimeError("Brave rate limit hit. Try again later.")

    response.raise_for_status()

    data = response.json()

    results = []
    for item in data.get("web", {}).get("results", []):
        results.append(
            SearchResult(
                title=(item.get("title") or "Untitled").strip(),
                url=(item.get("url") or "").strip(),
                snippet=(item.get("description") or "").strip(),
                domain="",
                provider="Brave",
                score=0,
            )
        )

    return results


def search_tavily(query: str, blocked_domains: set[str], count: int = 15) -> list[SearchResult]:
    if not TAVILY_API_KEY:
        raise RuntimeError("Missing TAVILY_API_KEY. Add it to your .env file or Render environment variables.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TAVILY_API_KEY}",
    }

    payload = {
        "query": query,
        "max_results": min(max(count, 1), 20),
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "exclude_domains": sorted(blocked_domains),
    }

    response = requests.post(TAVILY_SEARCH_URL, headers=headers, json=payload, timeout=15)

    if response.status_code in (401, 403):
        raise RuntimeError("Tavily rejected your API key. Check TAVILY_API_KEY.")

    if response.status_code == 429:
        raise RuntimeError("Tavily rate limit hit. Try again later.")

    response.raise_for_status()

    data = response.json()

    results = []
    for item in data.get("results", []):
        results.append(
            SearchResult(
                title=(item.get("title") or "Untitled").strip(),
                url=(item.get("url") or "").strip(),
                snippet=(item.get("content") or "").strip(),
                domain="",
                provider="Tavily",
                score=0,
            )
        )

    return results


def search_demo(query: str, blocked_domains: set[str], count: int = 15) -> list[SearchResult]:
    """
    Fake results so you can test the app before using real API keys.
    Domain filtering and strict mode still work.
    """
    samples = [
        {
            "title": "Photosynthesis - OpenStax Biology 2e",
            "url": "https://openstax.org/books/biology-2e/pages/8-introduction",
            "snippet": "Overview of photosynthesis, light reactions, carbon fixation, and energy conversion in plants.",
        },
        {
            "title": "C4 carbon fixation - Khan Academy",
            "url": "https://www.khanacademy.org/science/biology/photosynthesis-in-plants",
            "snippet": "Explains C3, C4, and CAM photosynthesis, including photorespiration and bundle-sheath cells.",
        },
        {
            "title": "Some Reddit thread about C4 plants",
            "url": "https://www.reddit.com/r/biology/comments/example",
            "snippet": "Random people arguing confidently. This should be filtered out.",
        },
        {
            "title": "Photosynthesis and Carbon Fixation - NCBI Bookshelf",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK26819/",
            "snippet": "Textbook-style explanation of photosynthetic carbon fixation and chloroplast function.",
        },
        {
            "title": "Quora answer about photosynthesis",
            "url": "https://www.quora.com/What-is-C4-photosynthesis",
            "snippet": "This should also be filtered out because no thank you.",
        },
        {
            "title": "Photosynthesis | Encyclopaedia Britannica",
            "url": "https://www.britannica.com/science/photosynthesis",
            "snippet": "General overview of photosynthesis, organisms involved, and chemical significance.",
        },
        {
            "title": "C4 photosynthesis in crop plants - Nature",
            "url": "https://www.nature.com/articles/example",
            "snippet": "Scientific discussion of C4 photosynthesis and plant productivity.",
        },
    ]

    return [
        SearchResult(
            title=item["title"],
            url=item["url"],
            snippet=item["snippet"],
            domain="",
            provider="Demo",
            score=0,
        )
        for item in samples
    ]


SEARCH_FUNCTIONS: dict[str, Callable[[str, set[str], int], list[SearchResult]]] = {
    "serpapi": search_serpapi,
    "brave": search_brave,
    "tavily": search_tavily,
    "demo": search_demo,
}


def available_keys() -> dict[str, bool]:
    return {
        "SERPAPI_KEY": bool(SERPAPI_KEY),
        "BRAVE_API_KEY": bool(BRAVE_API_KEY),
        "TAVILY_API_KEY": bool(TAVILY_API_KEY),
    }


@app.route("/", methods=["GET", "POST"])
def index():
    query = ""
    selected_provider = os.getenv("SEARCH_PROVIDER", "serpapi").strip().lower()
    strict_mode = False
    results: list[SearchResult] = []
    error = None

    blocked_domains = get_env_domain_set("BLOCKED_DOMAINS", DEFAULT_BLOCKED_DOMAINS)

    if selected_provider not in SEARCH_FUNCTIONS:
        selected_provider = "serpapi"

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        selected_provider = request.form.get("provider", selected_provider).strip().lower()
        strict_mode = request.form.get("strict_mode") == "on"

        if selected_provider not in SEARCH_FUNCTIONS:
            selected_provider = "serpapi"

        if not query:
            error = "Type a search query first. The app is clean, not clairvoyant."
        else:
            try:
                raw_results = SEARCH_FUNCTIONS[selected_provider](query, blocked_domains, 15)
                results = normalize_and_filter(raw_results, blocked_domains, strict_mode, max_results=7)

                if not results:
                    error = "No clean results found. Try turning off strict mode or using broader keywords."

            except requests.RequestException as exc:
                error = f"Search request failed: {exc}"
            except RuntimeError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"Something broke: {exc}"

    return render_template(
        "index.html",
        query=query,
        providers=PROVIDERS,
        selected_provider=selected_provider,
        strict_mode=strict_mode,
        results=results,
        error=error,
        blocked_domains=sorted(blocked_domains),
        available_keys=available_keys(),
    )


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(debug=True)
