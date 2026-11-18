from enum import StrEnum


class FindingType(StrEnum):
    COMMISSION = "commission"
    OMISSION = "omission"

    def __str__(self) -> str:
        return str(self.value)
