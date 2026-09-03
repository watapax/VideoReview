"""Cálculo de la nota final ponderada y formato de números a la chilena (coma decimal)."""

from typing import Iterable, Optional

SCALE_MIN = 1.0
SCALE_MAX = 7.0


def fmt(value: Optional[float], decimals: int = 1) -> str:
    """Formatea un número con coma decimal (ej: 6.0 -> '6,0'). Vacío si es None."""
    if value is None:
        return "—"
    return f"{value:.{decimals}f}".replace(".", ",")


def weighted_average(scores_and_weights: Iterable[tuple[Optional[float], float]]) -> Optional[float]:
    """Recibe pares (nota, ponderación%) y retorna el promedio ponderado.

    Si falta alguna nota, retorna None (no se puede calcular la nota final todavía).
    Las ponderaciones se normalizan por si no suman exactamente 100.
    """
    # Un aspecto con ponderación 0% no debería impedir calcular la nota final
    # aunque no tenga puntaje (por ejemplo, un aspecto que se dejó de usar).
    pairs = [(score, weight) for score, weight in scores_and_weights if weight > 0]
    if not pairs or any(score is None for score, _weight in pairs):
        return None
    total_weight = sum(weight for _score, weight in pairs)
    if total_weight <= 0:
        return None
    weighted_sum = sum(score * weight for score, weight in pairs)
    return weighted_sum / total_weight


def weights_sum(weights: Iterable[float]) -> float:
    return round(sum(weights), 2)


def band(score: Optional[float]) -> str:
    """Banda de color para una nota: ok (>=6.0) / warn (>=5.0) / bad (<5.0) / pending (sin nota)."""
    if score is None:
        return "pending"
    if score >= 6.0:
        return "ok"
    if score >= 5.0:
        return "warn"
    return "bad"


def weight_display(weight: float) -> str:
    """25.0 -> '25', 12.5 -> '12,5'"""
    if weight == int(weight):
        return str(int(weight))
    return f"{weight:.1f}".replace(".", ",")
