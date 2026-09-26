import asyncio
import os
import re
import logging
from typing import List, Optional, Sequence

from Bio import Entrez

from app.models.paper import Paper
from app.utils.cache import cache
from app.utils.rate_limiter import AsyncRateLimiter

logger = logging.getLogger(__name__)

# Terms that signal a paper is actually about a physical/regulatory
# relationship rather than merely mentioning the protein.
#
# Note on field tags: the original code used "[Mesh]" (lowercase "esh") and
# PubMed silently discarded those clauses -- the executed query collapsed to
# just the [Title/Abstract] branch. "protein-protein interaction" is also not
# a current MeSH heading (verified: 0 hits on its own), so it is searched as
# text instead. The two MeSH headings used below were verified to return hits.
INTERACTION_TERMS = (
    '("protein binding"[MeSH Terms] OR "binding sites"[MeSH Terms] '
    'OR "protein-protein interaction"[tiab] OR "protein binding"[tiab] '
    'OR "binds"[tiab] OR "interacts with"[tiab] OR "complex formation"[tiab])'
)

# MEDLINE tags whose values are repeatable and should be collected into a list.
REPEATABLE_TAGS = {"AU", "FAU", "PT", "IS", "LID", "AUID", "IR", "MH"}


def _clean(value: Optional[str]) -> Optional[str]:
    """Normalise an env var to None when it is blank.

    This matters: a .env line like `NCBI_API_KEY=` yields an empty string, and
    assigning that to Entrez.api_key makes Biopython send `api_key=` on every
    request. NCBI then rejects the whole query with
    `{"error":"API key invalid","api-key":""}` and HTTP 400 -- so a blank
    .env entry silently breaks all PubMed access.
    """
    if value is None:
        return None
    value = value.strip()
    return value or None


class PubMedService:
    def __init__(self, email: str = None, api_key: str = None, rate_limit: int = 3):
        # NCBI asks for a real contact address. Falls back to the environment
        # so the app does not ship with the literal "your_email@example.com".
        self.email = (
            _clean(email)
            or _clean(os.environ.get("NCBI_EMAIL"))
            or "protein-friend-finder@example.com"
        )
        self.api_key = _clean(api_key) or _clean(os.environ.get("NCBI_API_KEY"))
        Entrez.email = self.email
        Entrez.tool = "protein-friend-finder"
        # Only ever set a non-empty key; never let a blank .env value reach
        # the wire.
        Entrez.api_key = self.api_key
        self.rate_limiter = AsyncRateLimiter(rate_limit=rate_limit)
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------ query build

    @staticmethod
    def build_query(terms: Sequence[str], interaction_terms: str = None) -> Optional[str]:
        """Compose a PubMed query from protein search terms.

        Every user-supplied term is parenthesised. The previous version relied
        on PubMed's operator precedence, where AND binds tighter than OR, so
        "a[Mesh] OR b[tiab] AND (interaction)" parsed as
        "a[Mesh] OR (b[tiab] AND interaction)" -- the MeSH branch was
        unconstrained by any interaction requirement.
        """
        cleaned = [t.strip() for t in terms if t and t.strip()]
        if not cleaned:
            return None
        subject = "(" + " OR ".join(f'"{t}"[tiab]' for t in cleaned) + ")"
        return f"{subject} AND {interaction_terms or INTERACTION_TERMS}"

    # ----------------------------------------------------------------- search

    async def search_papers(self, query: str, max_results: int = 50) -> List[str]:
        """Search PubMed and return a list of PMIDs.

        `query` may be a ready-made PubMed query string or a plain protein
        term, in which case an interaction-focused query is built for it.
        """
        if not query:
            logger.warning("search_papers called with empty query")
            return []

        is_structured = any(
            token in query for token in ("[tiab]", "[MeSH", "[Mesh]", "[Title/Abstract]")
        )
        term = query if is_structured else self.build_query([query])
        logger.info("Searching PubMed: query='%s', max_results=%d", term, max_results)

        cache_key = f"search:{term}:{max_results}"
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info("Cache HIT for PubMed search: '%s'", cache_key)
            return cached

        try:
            logger.debug("Entering rate limiter for PubMed search")
            async with self.rate_limiter:
                logger.debug("Executing Entrez.esearch for term='%s'", term)
                handle = Entrez.esearch(
                    db="pubmed", term=term, retmax=max_results, sort="relevance"
                )
                record = Entrez.read(handle)
                handle.close()
                pmids = list(record.get("IdList", []))
                cache.set(cache_key, pmids)
                self.last_error = None
                logger.info(
                    "PubMed search complete: found %d PMIDs (term='%s')",
                    len(pmids), term,
                )
                return pmids
        except Exception as exc:
            # Record the reason. Returning [] here used to make a total PubMed
            # failure look like "this protein has no papers", producing an
            # empty graph that reported itself as healthy.
            self.last_error = f"{type(exc).__name__}: {exc}"[:500]
            logger.error(
                "PubMed esearch FAILED for term='%s': %s", term, exc,
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------ fetch

    async def fetch_paper_details(self, pmid: str) -> Optional[Paper]:
        """Fetch and parse a single paper by PMID."""
        if not pmid:
            logger.warning("fetch_paper_details called with empty PMID")
            return None

        logger.info("Fetching paper details: PMID=%s", pmid)
        cache_key = f"paper:{pmid}"
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache HIT for paper: PMID=%s", pmid)
            return cached

        try:
            logger.debug("Entering rate limiter for efetch PMID=%s", pmid)
            async with self.rate_limiter:
                logger.debug("Executing Entrez.efetch for PMID=%s", pmid)
                handle = Entrez.efetch(
                    db="pubmed", id=pmid, rettype="medline", retmode="text"
                )
                raw = handle.read()
                handle.close()
                paper = self._parse_medline(raw, str(pmid))
                if paper is not None:
                    cache.set(cache_key, paper)
                    logger.info("Paper fetched and cached: PMID=%s, title='%s'", pmid, paper.title[:60])
                else:
                    logger.warning("Paper parsing returned None: PMID=%s", pmid)
                return paper
        except Exception as exc:
            logger.error(
                "Failed to fetch paper PMID=%s: %s", pmid, exc,
                exc_info=True,
            )
            return None

    async def fetch_multiple_papers(self, pmids: List[str]) -> List[Paper]:
        """Fetch many papers, batching into as few HTTP requests as possible.

        The previous implementation issued one efetch per PMID (50 sequential
        requests, each paying the 0.35s rate-limit sleep). PubMed's history
        server lets a single efetch retrieve the whole result set, which is
        both far faster and far gentler on the rate limit.
        """
        pmids = [str(p) for p in pmids if p]
        if not pmids:
            logger.warning("fetch_multiple_papers called with empty PMID list")
            return []

        logger.info(
            "Fetching %d papers (batch mode)", len(pmids),
        )

        # Preserve order, drop duplicates and anything already cached.
        unique: List[str] = []
        seen = set()
        for pmid in pmids:
            if pmid not in seen:
                seen.add(pmid)
                unique.append(pmid)

        papers: List[Paper] = []
        pending: List[str] = []
        for pmid in unique:
            cached = cache.get(f"paper:{pmid}")
            if cached is not None:
                papers.append(cached)
            else:
                pending.append(pmid)

        logger.info(
            "Papers: %d cached, %d pending fetch",
            len(papers), len(pending),
        )

        if not pending:
            logger.info("All papers already cached, returning %d results", len(papers))
            return _in_request_order(papers, unique)

        BATCH = 100
        for start in range(0, len(pending), BATCH):
            chunk = pending[start : start + BATCH]
            logger.debug("Fetching batch of %d papers (PMIDs %d-%d)", len(chunk), start + 1, start + len(chunk))
            papers.extend(await self._fetch_chunk(chunk))

        result = _in_request_order(papers, unique)
        logger.info("fetch_multiple_papers complete: %d papers retrieved", len(result))
        return result

    async def _fetch_chunk(self, pmids: List[str]) -> List[Paper]:
        logger.debug("Fetching batch of %d PMIDs", len(pmids))
        try:
            async with self.rate_limiter:
                handle = Entrez.efetch(
                    db="pubmed",
                    id=",".join(pmids),
                    rettype="medline",
                    retmode="text",
                )
                raw = handle.read()
                handle.close()
        except Exception as exc:
            logger.error(
                "Batch efetch failed for %d PMIDs, falling back to individual: %s",
                len(pmids), exc,
                exc_info=True,
            )
            return [p for p in await self._fetch_individually(pmids) if p is not None]

        papers: List[Paper] = []
        for record_text in self._split_records(raw):
            pmid = self._record_pmid(record_text)
            paper = self._parse_medline(record_text, pmid)
            if paper is not None:
                cache.set(f"paper:{paper.pmid}", paper)
                papers.append(paper)
        return papers

    async def _fetch_individually(self, pmids: List[str]) -> List[Optional[Paper]]:
        logger.debug("Falling back to individual fetch for %d PMIDs", len(pmids))
        tasks = [self.fetch_paper_details(pmid) for pmid in pmids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[Optional[Paper]] = []
        for result in results:
            out.append(result if isinstance(result, Paper) else None)
        logger.debug("Individual fetch complete: %d papers retrieved", len(out))
        return out

    # ------------------------------------------------------------ MEDLINE I/O

    @staticmethod
    def _split_records(raw: str) -> List[str]:
        """Split a multi-record MEDLINE payload into individual records."""
        records: List[str] = []
        current: List[str] = []

        def flush():
            text = "\n".join(current).strip("\n")
            if "PMID-" in text:
                records.append(text)

        for line in raw.split("\n"):
            if line.strip():
                current.append(line)
            else:
                flush()
                current = []
        flush()
        return records

    @staticmethod
    def _record_pmid(record_text: str) -> str:
        for line in record_text.split("\n"):
            if line[:4].strip() == "PMID":
                return line[6:].strip().rstrip("-").strip()
        return ""

    def _parse_medline(self, medline_text: str, pmid: str = None) -> Optional[Paper]:
        """Parse MEDLINE text into a Paper.

        MEDLINE wraps long fields across several lines: the tag occupies
        columns 0-3 and continuation lines are indented six spaces. The
        previous parser only ever looked at `line[:4]`, so continuation lines
        were silently dropped -- a 9-author paper with a 1,700-character
        abstract was reduced to one author and 78 characters of abstract.
        Repeated tags (AU, PT, ...) were also overwritten rather than
        accumulated, keeping only the last author.
        """
        if not medline_text:
            return None

        values: dict = {}
        multi: dict = {}
        last_tag: Optional[str] = None

        for line in medline_text.split("\n"):
            if not line.strip():
                continue

            tag = line[:4].strip()
            if not tag:
                # Continuation of the previous field.
                if last_tag is None:
                    continue
                addition = line[6:].strip() if len(line) > 6 else line.strip()
                if not addition:
                    continue
                if last_tag in REPEATABLE_TAGS:
                    if multi.get(last_tag):
                        multi[last_tag][-1] = f"{multi[last_tag][-1]} {addition}".strip()
                else:
                    values[last_tag] = f"{values.get(last_tag, '')} {addition}".strip()
                continue

            content = line[6:].strip() if len(line) > 6 else line[4:].strip()
            last_tag = tag

            if tag in REPEATABLE_TAGS:
                multi.setdefault(tag, []).append(content)
            else:
                values[tag] = f"{values.get(tag, '')} {content}".strip() if tag in values else content

        record_pmid = pmid or self._record_pmid(medline_text)
        title = re.sub(r"\s+", " ", values.get("TI", "")).strip()
        if not title:
            return None

        authors = [a for a in (multi.get("AU") or multi.get("FAU") or []) if a]
        abstract = re.sub(r"\s+", " ", values.get("AB", "")).strip() or None
        year = values.get("DP", "").split()[0] if values.get("DP") else ""
        journal = re.sub(r"\s+", " ", values.get("TA", "")).strip() or None

        return Paper(
            pmid=record_pmid,
            title=title,
            authors=", ".join(authors),
            year=year,
            abstract=abstract,
            journal=journal,
            author_list=authors,
        )


def _in_request_order(papers: List[Paper], order: List[str]) -> List[Paper]:
    """Return papers sorted to match the requested PMID order."""
    by_pmid = {p.pmid: p for p in papers if p is not None}
    return [by_pmid[pmid] for pmid in order if pmid in by_pmid]
