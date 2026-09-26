"""Canonicalises protein symbols so one protein can only be one node.

Why this exists
---------------
`GraphService` keys nodes by the raw string the LLM emitted. That guarantees
unique *strings* but not unique *proteins*. Verified against UniProt:

    TP53, p53, P53        -> P04637   (one protein, three spellings)
    EGFR, ERBB1, HER1     -> P00533   (three names, one protein)
    SUMO1, UBL1, SMT3C    -> P63165

Left alone, one paper saying "p53" and another saying "TP53" produces two
nodes for the same protein, joined by nothing. UniProt accessions are the
authority here: they are stable and unambiguous, so identity is decided by
accession rather than by spelling.
"""  

import asyncio
import logging
import re
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from app.utils.cache import cache

logger = logging.getLogger(__name__)

UNIPROT_BASE = "https://rest.uniprot.org/uniprotkb"
HUMAN_TAXON = "9606"

# Gene symbols are upper-case tokens. Anything else (long prose, a sentence)
# is not a symbol and must never be treated as a protein name.
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,14}$")


class ProteinResolver:
    """Maps gene symbols to a canonical symbol + UniProt accession."""

    def __init__(self, organism: str = HUMAN_TAXON, timeout: int = 30):
        self.organism = organism
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "protein-friend-finder/0.1"})
        # (symbol, chosen_canonical, rejected_canonical) triples seen this
        # process. Surfaced by ambiguity_report() so a name that is genuinely
        # two proteins cannot be silently mis-resolved.
        self.ambiguous: set = set()
        # symbol -> {rejected_canonical} for the batch currently being fetched.
        # Read by resolve_many so the record can be cached next to the
        # mapping. Reset at the top of every _fetch_batch call.
        self._last_ambiguity: Dict[str, set] = {}

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def normalize(symbol: str) -> str:
        """Upper-case and strip. Does not resolve anything."""
        if not symbol:
            return ""
        return symbol.strip().upper()

    @classmethod
    def looks_like_symbol(cls, symbol: str) -> bool:
        return bool(_SYMBOL_RE.match(cls.normalize(symbol)))

    # --------------------------------------------------------------- lookups

    async def resolve_many(self, symbols: Iterable[str]) -> Dict[str, str]:
        """Canonicalise a batch of symbols.

        Returns {normalised_symbol: canonical_symbol}. Keys are upper-cased and
        stripped, so look up `mapping[symbol.strip().upper()]`; use `resolve()`
        for the single-symbol case. Symbols UniProt does not recognise are
        returned unchanged - we never drop a partner protein just because we
        could not identify it.
        """
        unique: List[str] = []
        seen = set()
        for symbol in symbols:
            key = self.normalize(symbol)
            if key and key not in seen:
                seen.add(key)
                unique.append(key)

        result: Dict[str, str] = {}
        pending: List[str] = []
        for symbol in unique:
            cached = cache.get(self._cache_key(symbol))
            if cached is not None:
                result[symbol] = cached
                # Re-hydrate the ambiguity record too. Without this a symbol
                # served from cache resolves correctly but silently stops
                # being reported as ambiguous.
                for rejected in cache.get(self._ambig_cache_key(symbol)) or ():
                    self.ambiguous.add((symbol, cached, rejected))
            else:
                pending.append(symbol)

        if pending:
            fetched = await asyncio.to_thread(self._fetch_batch, pending)
            rejected = self._last_ambiguity
            for symbol in pending:
                canonical = fetched.get(symbol, symbol)
                result[symbol] = canonical
                cache.set(self._cache_key(symbol), canonical)
                losers = sorted(rejected.get(symbol, ()))
                if losers:
                    cache.set(self._ambig_cache_key(symbol), losers)

        return result

    async def resolve(self, symbol: str) -> str:
        """Canonicalise a single symbol. Unknown symbols come back normalised."""
        key = self.normalize(symbol)
        if not key:
            return ""
        mapping = await self.resolve_many([key])
        return mapping.get(key, key)

    async def resolve_accessions(
        self, symbols: Iterable[str]
    ) -> Dict[str, Optional[str]]:
        """Return {normalised_symbol: uniprot_accession_or_None}.

        Keyed by normalised symbol, like `resolve_many`. Callers must normalise
        before lookup, or use `accession_for`.
        """
        canonical = await self.resolve_many(symbols)
        unique_canon = sorted(set(canonical.values()))
        found: Dict[str, Optional[str]] = {}
        for canon in unique_canon:
            found[canon] = await asyncio.to_thread(self._accession_for, canon)
        return {
            symbol: found.get(canon)
            for symbol, canon in canonical.items()
        }

    async def accession_for(self, symbol: str) -> Optional[str]:
        """UniProt accession for a single symbol, or None if unrecognised."""
        canon = await self.resolve(symbol)
        return await asyncio.to_thread(self._accession_for, canon) if canon else None

    def _cache_key(self, symbol: str) -> str:
        return f"symresolve:{self.organism}:{symbol}"

    def _ambig_cache_key(self, symbol: str) -> str:
        """Cache slot for the losing alternatives of an ambiguous symbol."""
        return f"symambig:{self.organism}:{symbol}"

    def _accession_for(self, symbol: str) -> Optional[str]:
        payload = self._search([symbol])
        if not payload:
            return None
        genes = payload[0].get("genes") or []
        return payload[0].get("primaryAccession") if genes else None

    def _search(self, symbols: List[str]) -> List[dict]:
        clause = " OR ".join(f'gene_exact:"{s}"' for s in symbols)
        term = f"({clause}) AND organism_id:{self.organism} AND reviewed:true"
        try:
            response = self._session.get(
                f"{UNIPROT_BASE}/search",
                params={
                    "query": term,
                    "fields": "accession,gene_names",
                    "format": "json",
                    "size": 500,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json().get("results", []) or []
        except Exception as exc:
            logger.warning("UniProt symbol lookup failed for %s: %s", symbols, exc)
            return []

    def _fetch_batch(self, symbols: List[str]) -> Dict[str, str]:
        """One request for many symbols; returns {symbol: canonical}.

        A queried symbol can be claimed by more than one UniProt entry: either
        as its canonical gene name, or as somebody's synonym. These collisions
        are real, not hypothetical -- UniProt lists PAF1 as a SYNONYM of PEX2
        (P28328) while PAF1 is also the canonical name of Q8N7H5.

        The old code registered every name in a plain dict, so the entry
        returned LAST silently overwrote the other. That made the mapping
        depend on UniProt's result ordering, which is not stable: the same
        symbol could canonicalise to different proteins on different runs or in
        different batches. Verified: 'PAF1' resolved to PEX2 when queried
        alone and to PAF1 when batched with PEX2.

        Rule now applied: a canonical-name match always beats a synonym-only
        match. A symbol that is genuinely two proteins' canonical name is
        still reported as ambiguous rather than silently picked.
        """
        # rank 0 = the symbol is this entry's canonical gene name
        # rank 1 = the symbol is only a synonym / ordered locus name
        best: Dict[str, Tuple[int, str]] = {}
        self._last_ambiguity = {}

        def offer(key: str, canonical: str, rank: int) -> None:
            current = best.get(key)
            if current is None or rank < current[0]:
                best[key] = (rank, canonical)
            elif rank == current[0] and canonical != current[1]:
                # Two proteins claim this symbol equally well and neither owns
                # it canonically, so there is no principled winner. Break the
                # tie on the canonical name instead of on arrival order, so
                # the answer cannot change between runs, and record it.
                self.ambiguous.add((key, current[1], canonical))
                self._last_ambiguity.setdefault(key, set()).update(
                    (current[1], canonical)
                )
                if canonical < current[1]:
                    best[key] = (rank, canonical)

        for start in range(0, len(symbols), 40):
            chunk = symbols[start : start + 40]
            for entry in self._search(chunk):
                genes = entry.get("genes") or []
                if not genes:
                    continue
                gene = genes[0]
                canonical = (gene.get("geneName") or {}).get("value")
                if not canonical:
                    continue
                accession = entry.get("primaryAccession")
                canonical_key = self.normalize(canonical)

                # Register the canonical name and every synonym against it.
                names = {canonical_key}
                for group in (gene.get("synonyms") or [],
                              gene.get("orderedLocusNames") or []):
                    for item in group:
                        value = (item or {}).get("value")
                        if value:
                            names.add(self.normalize(value))

                for key in names:
                    offer(key, canonical, 0 if key == canonical_key else 1)
                logger.debug(
                    "Resolved %s -> %s (%s)", chunk, canonical, accession
                )

        resolved: Dict[str, str] = {}
        for symbol in symbols:
            hit = best.get(symbol)
            # Unknown symbols come back unchanged: we never drop a partner
            # protein just because we could not identify it.
            resolved[symbol] = hit[1] if hit else symbol
        return resolved

    # ------------------------------------------------------------ validation

    def ambiguity_report(self) -> List[dict]:
        """Symbols UniProt claims for more than one protein.

        A canonical-name match always wins over a synonym match, so these
        resolve deterministically -- but the losing protein is a real
        alternative reading and callers may want to surface it.
        """
        out: Dict[str, dict] = {}
        for symbol, chosen, rejected in sorted(self.ambiguous):
            # A symbol is not an alternative reading of itself. This can arise
            # because the cache stores every rejected candidate while
            # self.ambiguous stores both orderings of the pair.
            if rejected == chosen:
                continue
            row = out.setdefault(
                symbol, {"symbol": symbol, "chosen": chosen, "alternatives": []}
            )
            if rejected not in row["alternatives"]:
                row["alternatives"].append(rejected)
        for row in out.values():
            row["alternatives"].sort()
        return list(out.values())

    async def identity_groups(self, symbols: Iterable[str]) -> Dict[str, List[str]]:
        """Group input symbols that denote the same protein.

        Returns {canonical_symbol: [aliases_seen]}. Only symbols UniProt
        recognises are grouped; unknown symbols stand alone.
        """
        canonical = await self.resolve_many(symbols)
        groups: Dict[str, List[str]] = {}
        for symbol, canon in canonical.items():
            groups.setdefault(canon, []).append(symbol)
        return groups
