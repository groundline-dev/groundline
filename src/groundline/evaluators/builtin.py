"""The registry of every built-in evaluator."""

from groundline.core import Registry
from groundline.evaluators.commissions import register_commissions
from groundline.evaluators.omissions import register_omissions


def default_registry() -> Registry:
    """A registry with every built-in step and implementation."""
    registry = Registry()
    register_omissions(registry)
    register_commissions(registry)
    return registry
