from enum import StrEnum


class CommissionsRequestPreset(StrEnum):
    COMMISSION_QUICK = "commission_quick"
    COMMISSION_REVERSE_LEAN = "commission_reverse_lean"

    def __str__(self) -> str:
        return str(self.value)
