from enum import StrEnum


class FindingSeverityType0(StrEnum):
    CRITICAL = "critical"
    PERIPHERAL = "peripheral"
    SUPPORTING = "supporting"

    def __str__(self) -> str:
        return str(self.value)
