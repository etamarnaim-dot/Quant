from __future__ import annotations

from pathlib import Path

from src.news import fetch_news_events, ingest_news_articles
from src.news_sources import PublicFeedNewsSource, build_default_news_sources, parse_sec_atom, parse_yahoo_rss


YAHOO_RSS = b"""<?xml version='1.0'?>
<rss><channel><item>
  <title>ACME reports strong earnings</title>
  <link>https://example.com/acme-earnings</link>
  <guid>acme-earnings-1</guid>
  <description>Quarterly revenue beats expectations.</description>
  <pubDate>Fri, 13 Nov 2026 12:00:00 GMT</pubDate>
</item></channel></rss>"""

SEC_ATOM = b"""<?xml version='1.0'?>
<feed xmlns='http://www.w3.org/2005/Atom' xmlns:edgar='https://www.sec.gov/Archives/edgar'>
  <entry>
    <title>8-K</title>
    <updated>2026-11-13T12:05:00Z</updated>
    <link href='https://www.sec.gov/Archives/edgar/data/example/filing.htm'/>
    <edgar:company-name>ACME Corporation</edgar:company-name>
    <edgar:filing-type>8-K</edgar:filing-type>
    <edgar:accession-number>0000000000-26-000001</edgar:accession-number>
    <edgar:items-desc>Item 2.02: Results of Operations and Financial Condition</edgar:items-desc>
  </entry>
</feed>"""
SEC_TICKERS = b'{"0":{"ticker":"ACME","cik_str":123}}'


def test_yahoo_rss_parser_normalizes_headline():
    articles = parse_yahoo_rss(YAHOO_RSS, "ACME")

    assert len(articles) == 1
    assert articles[0]["source"] == "Yahoo Finance"
    assert articles[0]["symbol"] == "ACME"
    assert articles[0]["title"] == "ACME reports strong earnings"
    assert articles[0]["published_at"] == "2026-11-13T12:00:00+00:00"


def test_sec_atom_parser_marks_important_8k_items():
    articles = parse_sec_atom(SEC_ATOM, "ACME")

    assert len(articles) == 1
    assert articles[0]["source"] == "SEC EDGAR"
    assert articles[0]["important"] is True
    assert articles[0]["symbols"] == ["ACME"]
    assert "earnings results" in articles[0]["title"].lower()


def test_public_feed_source_fetches_rss_and_sec_with_headers(monkeypatch):
    requests = []

    class Response:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.content

    def fake_urlopen(req, timeout):
        requests.append(req)
        if "company_tickers.json" in req.full_url:
            return Response(SEC_TICKERS)
        return Response(SEC_ATOM if "sec.gov" in req.full_url else YAHOO_RSS)

    monkeypatch.setattr("src.news_sources.request.urlopen", fake_urlopen)
    source = PublicFeedNewsSource(["ACME"], sec_user_agent="Research App user@example.com")

    articles = source.fetch_articles()

    assert len(articles) == 2
    assert {article["source"] for article in articles} == {"Yahoo Finance", "SEC EDGAR"}
    assert all(req.get_header("User-agent") for req in requests)
    filing_request = next(req for req in requests if "browse-edgar" in req.full_url)
    assert "CIK=0000000123" in filing_request.full_url


def test_public_feed_articles_ingest_into_existing_news_events(tmp_path, monkeypatch):
    class Response:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.content

    def fake_urlopen(req, timeout):
        if "company_tickers.json" in req.full_url:
            return Response(SEC_TICKERS)
        return Response(SEC_ATOM if "sec.gov" in req.full_url else YAHOO_RSS)

    monkeypatch.setattr("src.news_sources.request.urlopen", fake_urlopen)
    source = PublicFeedNewsSource(["ACME"], sec_user_agent="Research App user@example.com")
    inserted = ingest_news_articles(tmp_path / "research.db", source.fetch_articles())

    assert len(inserted) == 2
    assert {event["source"] for event in fetch_news_events(tmp_path / "research.db")} == {
        "Yahoo Finance",
        "SEC EDGAR",
    }


def test_public_feed_article_without_published_time_ingests(tmp_path):
    article = parse_yahoo_rss(
        b"<rss><channel><item><title>ACME headline</title><link>https://example.com/no-date</link></item></channel></rss>",
        "ACME",
    )[0]

    inserted = ingest_news_articles(tmp_path / "research.db", [article])

    assert len(inserted) == 1
    assert inserted[0]["symbol"] == "ACME"


def test_default_registry_registers_public_feed_for_symbols(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    registry = build_default_news_sources(symbols=["ACME"])

    assert any(isinstance(source, PublicFeedNewsSource) for source in registry._sources)


def test_sec_user_agent_rejects_env_example_placeholder():
    example_path = Path(__file__).resolve().parents[1] / ".env.example"
    example_line = next(line for line in example_path.read_text(encoding="utf-8").splitlines() if line.startswith("SEC_USER_AGENT="))
    placeholder = example_line.partition("=")[2]

    source = PublicFeedNewsSource(["ACME"], sec_user_agent=placeholder)

    assert source.sec_user_agent is None


def test_sec_user_agent_rejects_example_domains_and_placeholder_name():
    invalid_values = (
        "Research App user@example.com",
        "Research App user@example.org",
        "Research App user@example.net",
        "Quant your.name@research.org",
    )

    assert all(
        PublicFeedNewsSource(["ACME"], sec_user_agent=value).sec_user_agent is None
        for value in invalid_values
    )