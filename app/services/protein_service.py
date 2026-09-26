import re
import logging
from typing import List, Optional

import requests

from app.models.protein import Protein
from app.utils.cache import cache

logger = logging.getLogger(__name__)

UNIPROT_BASE = "https://rest.uniprot.org/uniprotkb"
HUMAN_TAXON = "9606"

# UniProt accession patterns, e.g. P04637, Q9BVI0, A0A024RBG1
ACCESSION_RE = re.compile(
    r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})$",
    re.IGNORECASE,
)

FIELDS = ",".join(
    [
        "accession",
        "id",
        "gene_names",
        "protein_name",
        "organism_name",
        "length",
        "cc_function",
        "cc_pathway",
        "cc_subcellular_location",
    ]
)


class ProteinService:
    """Resolves protein names / gene symbols / accessions via the UniProt REST API.

    The previous implementation used Biopython's ExPASy SwissProt fetcher plus a
    hardcoded 7-entry name->accession dict, so only p53/tp53/insulin/hemoglobin/
    myoglobin/actin/tubulin could be looked up at all. It also reported
    `P53_HUMAN` as the protein name and gave no gene symbol, which is what
    PubMed actually needs.
    """

    def __init__(self, timeout: int = 30, organism: str = HUMAN_TAXON):
        self.timeout = timeout
        self.organism = organism
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "protein-friend-finder/0.1"})

    # ------------------------------------------------------------------ public

    async def get_protein_by_name(self, name: str) -> Optional[Protein]:
        """Resolve a free-text name, gene symbol or accession to a Protein."""
        if not name or not name.strip():
            logger.warning("get_protein_by_name called with empty name")
            return None
        logger.info("Resolving protein by name: '%s'", name)
        return await self._resolve(name.strip())

    async def get_protein_by_id(self, uniprot_id: str) -> Optional[Protein]:
        """Fetch a Protein by UniProt accession (falls back to symbol lookup)."""
        if not uniprot_id or not uniprot_id.strip():
            logger.warning("get_protein_by_id called with empty uniprot_id")
            return None
        identifier = uniprot_id.strip()
        logger.info("Fetching protein by UniProt ID: '%s'", identifier)
        if not ACCESSION_RE.match(identifier):
            # Callers (e.g. graph node clicks) may hand us a gene symbol.
            logger.info(
                "'%s' does not match accession pattern, treating as gene symbol",
                identifier,
            )
            return await self._resolve(identifier)
        return await self._fetch_accession(identifier.upper())

    # ---------------------------------------------------------------- internal

    async def _resolve(self, query: str) -> Optional[Protein]:
        cache_key = f"protein_resolve:{self.organism}:{query.lower()}"
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache HIT for protein resolve: query='%s'", query)
            return cached

        logger.info("Resolving protein: query='%s', strategies=%s", query, list(str(f.__name__) for f in self._resolution_order(query)))
        protein = None
        for attempt in self._resolution_order(query):
            strategy = attempt.__name__
            logger.debug("Trying resolution strategy: %s", strategy)
            protein = await attempt()
            if protein is not None:
                logger.info("Protein resolved via %s: %s (UniProt=%s)", strategy, protein.name, protein.uniprot_id)
                break

        if protein is not None:
            cache.set(cache_key, protein)
            # Also cache under the accession so lookups by symbol are cheap.
            cache.set(f"protein:{protein.uniprot_id}", protein)
            logger.debug("Protein cached under resolve key and accession key")
        else:
            logger.warning("Could not resolve protein: query='%s'", query)
        return protein

    def _resolution_order(self, query: str):
        """Yield lookup strategies best-first.

        Free-text search alone is unreliable: querying UniProt for "p53"
        intermittently returns PHF20 rather than TP53, because it matches any
        field including protein descriptions. Scoping to the gene field first
        makes resolution deterministic.
        """
        if ACCESSION_RE.match(query):
            upper = query.upper()

            async def by_accession():
                return await self._fetch_accession(upper)

            yield by_accession

        if re.fullmatch(r"[A-Za-z0-9\-]+", query):
            symbol = query.upper()

            async def by_exact_gene():
                return await self._search(f"gene_exact:{symbol}", exact_gene=symbol)

            async def by_gene():
                return await self._search(f"gene:{symbol}", exact_gene=symbol)

            yield by_exact_gene
            yield by_gene

        async def by_free_text():
            return await self._search(f'"{query}"', preferred=query)

        yield by_free_text

    async def _fetch_accession(self, accession: str) -> Optional[Protein]:
        cache_key = f"protein:{accession}"
        cached = cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache HIT for accession: %s", accession)
            return cached

        logger.info("Fetching protein from UniProt accession: %s", accession)
        url = f"{UNIPROT_BASE}/{accession}.json"
        protein = await self._get_json(url, {"fields": FIELDS})
        if protein is None:
            logger.warning("No protein data returned for accession: %s", accession)
            return None

        result = self._to_protein(protein)
        if result is not None:
            cache.set(cache_key, result)
            logger.info("Protein fetched and cached: %s (%s)", result.name, accession)
        else:
            logger.warning("Could not parse protein data for accession: %s", accession)
        return result

    async def _search(self, query: str, exact_gene: str = None, preferred: str = None):
        term = f"({query}) AND organism_id:{self.organism} AND reviewed:true"
        url = f"{UNIPROT_BASE}/search"
        params = {"query": term, "fields": FIELDS, "format": "json", "size": 25}

        payload = await self._get_json(url, params)
        if not payload:
            return None

        results = payload.get("results") or []
        if not results:
            return None

        # Rank ourselves rather than trusting the API's ordering.
        query_tokens = self._tokenize(preferred or query.strip('"'))
        candidates = []
        for entry in results:
            score = self._score(entry, exact_gene, preferred, query_tokens)
            candidates.append((score, entry))

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        best = candidates[0]
        if best[0] <= 0:
            # Nothing matched well; a free-text hit on an unrelated protein is
            # worse than telling the caller we could not resolve it.
            return None

        return self._to_protein(best[1])

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]

    def _score(self, entry: dict, exact_gene: str, preferred: str, query_tokens: List[str]) -> int:
        """Rank a UniProt hit against what the user actually typed.

        Plain substring matching is not enough: "hemoglobin beta" should match
        "Hemoglobin subunit beta" even though neither contains the other.
        """
        gene = self._gene_name(entry)
        protein_name = self._protein_name(entry)
        haystack = " ".join(filter(None, [gene, protein_name]))
        haystack_tokens = set(self._tokenize(haystack))

        score = 0
        if exact_gene and gene and gene.upper() == exact_gene.upper():
            score += 100
        if preferred:
            low = preferred.lower()
            if gene and gene.lower() == low:
                score += 80
            if protein_name and protein_name.lower() == low:
                score += 60
            if protein_name and low in protein_name.lower():
                score += 20
        if query_tokens:
            matched = [t for t in query_tokens if t in haystack_tokens]
            if len(matched) == len(query_tokens):
                score += 40
            elif matched:
                score += 10 * len(matched) // len(query_tokens)
        return score

    async def _get_json(self, url: str, params: dict):
        try:
            logger.debug("Sending HTTP request to: %s", url)
            response = await self._async_get(url, params)
        except Exception as exc:  # network/HTTP problems must not 500 the API
            logger.error(
                "HTTP request failed for %s: %s", url, exc,
                exc_info=True,
            )
            return None
        if response is None:
            logger.warning("HTTP request returned None for URL: %s", url)
            return None
        try:
            return response.json()
        except ValueError:
            logger.error("Non-JSON response received from %s", url)
            return None

    async def _async_get(self, url: str, params: dict):
        import asyncio

        def do_request():
            return self._session.get(url, params=params, timeout=self.timeout)

        response = await asyncio.to_thread(do_request)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response

    # ------------------------------------------------------------- extraction

    def _to_protein(self, entry: dict) -> Optional[Protein]:
        accession = entry.get("primaryAccession")
        if not accession:
            return None

        gene = self._gene_name(entry)
        protein_name = self._protein_name(entry)
        display = gene or protein_name or entry.get("uniProtkbId") or accession

        function = self._comment_text(entry, "FUNCTION")
        localization = self._comment_text(entry, "SUBCELLULAR LOCATION")
        pathways = self._comment_text(entry, "PATHWAY")

        description = protein_name or display
        if entry.get("uniProtkbId"):
            description = f"{description} ({entry['uniProtkbId']})"

        return Protein(
            uniprot_id=accession,
            name=display,
            gene_name=gene,
            protein_name=protein_name,
            description=description,
            function=function or "Function information not available",
            pathways=pathways or "Pathway information not available",
            localization=localization or "Localization information not available",
            organism=(entry.get("organism") or {}).get("scientificName"),
            synonyms=self._synonyms(entry),
            sequence_length=(entry.get("sequence") or {}).get("length"),
        )

    @staticmethod
    def _gene_name(entry: dict) -> Optional[str]:
        for gene in entry.get("genes") or []:
            name = (gene.get("geneName") or {}).get("value")
            if name:
                return name
        return None

    @staticmethod
    def _synonyms(entry: dict) -> List[str]:
        out: List[str] = []
        for gene in entry.get("genes") or []:
            for syn in gene.get("synonyms") or []:
                value = syn.get("value")
                if value and value not in out:
                    out.append(value)
            for syn in gene.get("orderedLocusNames") or []:
                value = syn.get("value")
                if value and value not in out:
                    out.append(value)
        return out

    @staticmethod
    def _protein_name(entry: dict) -> Optional[str]:
        desc = entry.get("proteinDescription") or {}
        recommended = (desc.get("recommendedName") or {}).get("fullName") or {}
        if recommended.get("value"):
            return recommended["value"]
        for alt in desc.get("alternativeNames") or []:
            value = (alt.get("fullName") or {}).get("value")
            if value:
                return value
        submitted = desc.get("submissionNames") or []
        for alt in submitted:
            value = (alt.get("fullName") or {}).get("value")
            if value:
                return value
        return None

    @staticmethod
    def _comment_text(entry: dict, comment_type: str) -> Optional[str]:
        """Join the text of every comment of a given type.

        UniProt uses two different shapes: FUNCTION/PATHWAY carry a "texts"
        list, while SUBCELLULAR LOCATION carries "subcellularLocations". It
        also emits one SUBCELLULAR LOCATION comment per isoform, so the
        isoform-specific ones (which carry a "molecule" label) are skipped.
        """
        collected: List[str] = []
        for comment in entry.get("comments") or []:
            if comment.get("commentType") != comment_type:
                continue
            if comment_type == "SUBCELLULAR LOCATION" and comment.get("molecule"):
                continue  # isoform-specific duplicate

            parts: List[str] = []
            for item in comment.get("texts") or []:
                value = item.get("value")
                if value:
                    parts.append(value)

            if comment_type == "SUBCELLULAR LOCATION":
                locations = []
                for entry_loc in comment.get("subcellularLocations") or []:
                    location = (entry_loc.get("location") or {}).get("value")
                    if not location:
                        continue
                    topology = (entry_loc.get("topology") or {}).get("value")
                    orientation = (entry_loc.get("orientation") or {}).get("value")
                    text = location
                    if topology:
                        text += f", {topology}"
                    if orientation:
                        text += f", {orientation}"
                    locations.append(text)
                if locations:
                    parts.insert(0, "; ".join(locations))

            value = " ".join(parts).strip()
            if value:
                collected.append(value)

        if not collected:
            return None
        seen = set()
        unique = []
        for text in collected:
            if text not in seen:
                seen.add(text)
                unique.append(text)
        return " ".join(unique)
