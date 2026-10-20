"""Small contracts shared by retrieval backends; scores are not probabilities."""
from dataclasses import dataclass, field, asdict
from hashlib import sha256


@dataclass
class Hit:
    id: str
    source: str
    uri: str
    text: str
    title: str = ""
    start_line: int | None = None
    end_line: int | None = None
    score: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def identifier(*parts):
    return sha256("\0".join(str(p) for p in parts).encode()).hexdigest()[:24]


@dataclass
class SearchResult:
    hits: list[Hit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    diagnostics: dict = field(default_factory=dict)
