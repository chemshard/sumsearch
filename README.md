# Clean Search Flask App — Multi-provider version

A simple Flask app that lets users search the web, fetches top result snippets, and filters out low-quality domains such as Reddit, Quora, Pinterest, Medium, TikTok, etc.

No AI summaries. No page scraping. Just cleaner search result snippets.

## Features

- Search box
- Provider dropdown:
  - SerpApi
  - Brave Search API
  - Tavily Search API
  - Demo mode
- Domain blacklist
- Manual post-filtering after API results return
- Optional strict academic mode
- Quality scoring
- Clean UI
- Render-ready deployment files

## Folder structure

```text
clean_search_flask_multiprovider/
├─ app.py
├─ requirements.txt
├─ Procfile
├─ render.yaml
├─ README.md
├─ .env.example
├─ .gitignore
├─ templates/
│  └─ index.html
└─ static/
   ├─ styles.css
   └─ favicon.svg
```

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Add your API key

Create a `.env` file in the project folder.

You can copy `.env.example` and rename it to `.env`.

Example for SerpApi:

```bash
SEARCH_PROVIDER=serpapi
SERPAPI_KEY=your_key_here
```

Example for Brave:

```bash
SEARCH_PROVIDER=brave
BRAVE_API_KEY=your_key_here
```

Example for Tavily:

```bash
SEARCH_PROVIDER=tavily
TAVILY_API_KEY=your_key_here
```

Or use demo mode without any API key:

```bash
SEARCH_PROVIDER=demo
```

## 3. Run locally

```bash
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

## 4. Customise blocked domains

In `app.py`, edit:

```python
DEFAULT_BLOCKED_DOMAINS = {
    "reddit.com",
    "quora.com",
    ...
}
```

Or in your `.env` file:

```bash
BLOCKED_DOMAINS=reddit.com,quora.com,pinterest.com,medium.com
```

## 5. Customise trusted domains

In `app.py`, edit:

```python
TRUSTED_DOMAINS = {
    "ncbi.nlm.nih.gov",
    "khanacademy.org",
    ...
}
```

Strict academic mode uses this list plus educational/governmental suffixes.

## 6. Deploy to Render

Use these settings:

- Build command:

```bash
pip install -r requirements.txt
```

- Start command:

```bash
gunicorn app:app
```

Then add environment variables in Render.

For example:

```bash
SEARCH_PROVIDER=serpapi
SERPAPI_KEY=your_key_here
```

## Notes

This app does not summarise articles.

It displays snippets returned by search APIs, then filters and ranks them.
