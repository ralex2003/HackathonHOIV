from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Protein:
    """A protein entry.

    `uniprot_id` is the canonical accession, but `gene_name` is what we use as
    the graph node id and what we search PubMed with, because accessions such
    as "P04637" essentially never appear in paper text.
    """

    uniprot_id: str
    name: str
    gene_name: Optional[str] = None
    protein_name: Optional[str] = None
    function: Optional[str] = None
    pathways: Optional[str] = None
    localization: Optional[str] = None
    description: Optional[str] = None
    organism: Optional[str] = None
    synonyms: List[str] = field(default_factory=list)
    sequence_length: Optional[int] = None

    def __post_init__(self):
        # `name` used to hold the SwissProt entry name ("P53_HUMAN"); prefer the
        # gene symbol for display, falling back to whatever we were given.
        if not self.gene_name:
            self.gene_name = self.name
        if not self.name:
            self.name = self.gene_name

    @property
    def node_id(self) -> str:
        """Identifier used for graph nodes and interaction endpoints."""
        return self.gene_name or self.uniprot_id

    def search_terms(self, min_length: int = 4, limit: int = 4) -> List[str]:
        """Phrases worth searching PubMed for, most specific first.

        Short gene symbols are excluded by default: PubMed's [tiab] search
        treats "INS", "CAT" and "AR" as ordinary words, which returns mostly
        unrelated papers (measured: "CAT"[tiab] gave 4,972 hits versus 1,335
        for "Catalase"[tiab]). The full protein name is used as the fallback.
        """
        candidates: List[str] = []
        if self.protein_name: 
            candidates.append(self.protein_name)
        if self.gene_name:
            candidates.append(self.gene_name)
        candidates.extend(self.synonyms)

        terms: List[str] = []
        seen = set()
        for candidate in candidates:
            if not candidate or len(candidate) < min_length:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(candidate)
            if len(terms) >= limit:
                break
        return terms

    def to_dict(self) -> dict:
        return {
            "uniprot_id": self.uniprot_id,
            "name": self.name,
            "gene_name": self.gene_name,
            "protein_name": self.protein_name,
            "description": self.description,
            "function": self.function,
            "pathways": self.pathways,
            "localization": self.localization,
            "organism": self.organism,
            "synonyms": self.synonyms,
            "sequence_length": self.sequence_length,
        }
