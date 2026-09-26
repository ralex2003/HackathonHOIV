from dataclasses import dataclass, field
from typing import List, Optional

from app.models.paper import Paper


@dataclass
class Interaction:
    source_protein: str
    target_protein: str
    interaction_type: str  # physical_binding, regulatory, complex_formation, genetic
    papers: List[Paper] = field(default_factory=list)
    context: Optional[str] = None

    # Interaction types for categorization
    TYPES = {
        "physical_binding": "Physical Binding",
        "regulatory": "Regulatory",
        "complex_formation": "Complex Formation",
        "genetic": "Genetic",
    }

    @staticmethod
    def normalize_type(value: str) -> str:
        """Map free-text interaction types onto the four known categories."""
        if not value:
            return "physical_binding"
        v = value.strip().lower().replace("-", "_").replace(" ", "_")
        if v in Interaction.TYPES:
            return v
        aliases = {
            "binding": "physical_binding",
            "physical": "physical_binding",
            "binds": "physical_binding",
            "interacts": "physical_binding",
            "interaction": "physical_binding",
            "direct_binding": "physical_binding",
            "complex": "complex_formation",
            "complex_formation": "complex_formation",
            "regulates": "regulatory",
            "regulation": "regulatory",
            "transcriptional_regulation": "regulatory",
            "genetic_interaction": "genetic",
        }
        return aliases.get(v, "physical_binding")

    @property
    def type_label(self) -> str:
        return self.TYPES.get(self.interaction_type, self.interaction_type)

    def key(self) -> tuple:
        """Identity of an edge, independent of which paper reported it.

        Endpoints are sorted so that A->B and B->A collapse into one edge.
        """
        a, b = sorted([self.source_protein, self.target_protein])
        return (a, b, self.interaction_type)

    def merge(self, other: "Interaction") -> None:
        """Fold another observation of the same edge into this one."""
        known = {p.pmid for p in self.papers}
        for paper in other.papers:
            if paper.pmid not in known:
                self.papers.append(paper)
                known.add(paper.pmid)
        if self.context is None and other.context:
            self.context = other.context

    def to_dict(self, include_context: bool = False) -> dict:
        data = {
            "source_protein": self.source_protein,
            "target_protein": self.target_protein,
            "interaction_type": self.interaction_type,
            "interaction_label": self.type_label,
            "paper_count": len(self.papers),
            "papers": [
                {"pmid": p.pmid, "title": p.title, "year": p.year, "url": p.url}
                for p in self.papers
            ],
        }
        if include_context:
            data["context"] = self.context
        return data
