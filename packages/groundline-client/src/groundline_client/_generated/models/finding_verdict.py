from enum import StrEnum


class FindingVerdict(StrEnum):
    ABSENT = "absent"
    CONTRADICTED = "contradicted"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"

    def __str__(self) -> str:
        return str(self.value)
