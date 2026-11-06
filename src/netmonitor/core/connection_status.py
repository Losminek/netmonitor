"""Vyhodnocení celkového stavu připojení.

Rozlišuje vrstvy: lokální rozhraní → výchozí brána → internet → jednotlivé cíle.
Selhání jediného cíle nikdy samo neznamená výpadek internetu.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum


class ConnState(str, Enum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ConnInputs:
    iface_up: bool | None                  # aktivní rozhraní s IP adresou
    has_default_route: bool | None
    gateway_up: bool | None = None         # ping brány (None = neměřeno / vypnuto)
    internet_up: list[bool | None] = field(default_factory=list)   # stav internetových cílů
    internet_rtts: list[float] = field(default_factory=list)       # poslední RTT dostupných cílů
    internet_loss: list[float] = field(default_factory=list)       # loss % internetových cílů
    high_latency_ms: float = 150.0
    high_loss_pct: float = 20.0
    http_ok: bool | None = None            # čerstvý výsledek HTTPS dotazu (WAN IP), None = neznámý


@dataclass(frozen=True)
class ConnEvaluation:
    state: ConnState
    reason: str
    layers: dict


def evaluate(i: ConnInputs) -> ConnEvaluation:
    layers = {
        "interface": i.iface_up,
        "gateway": i.gateway_up,
        "internet_targets": f"{sum(1 for x in i.internet_up if x)}/{sum(1 for x in i.internet_up if x is not None)}",
    }
    if i.iface_up is False:
        return ConnEvaluation(ConnState.OFFLINE, "Žádné aktivní síťové rozhraní", layers)
    if i.has_default_route is False:
        return ConnEvaluation(ConnState.OFFLINE, "Chybí výchozí trasa (brána)", layers)

    known = [x for x in i.internet_up if x is not None]
    if not known:
        if i.iface_up is None:
            return ConnEvaluation(ConnState.UNKNOWN, "Stav zatím nezjištěn", layers)
        if i.gateway_up is False:
            return ConnEvaluation(ConnState.DEGRADED, "Brána neodpovídá, internet zatím neověřen", layers)
        return ConnEvaluation(ConnState.UNKNOWN, "Žádný internetový cíl zatím neměřen", layers)

    up = sum(1 for x in known if x)
    down = len(known) - up
    if up == 0:
        if i.http_ok:
            return ConnEvaluation(ConnState.DEGRADED,
                                  "Žádný cíl neodpovídá na ICMP, ale HTTPS funguje (ICMP může být blokováno)", layers)
        if len(known) >= 2 or i.gateway_up is False:
            return ConnEvaluation(ConnState.OFFLINE, "Žádný internetový cíl neodpovídá", layers)
        return ConnEvaluation(ConnState.DEGRADED, "Jediný internetový cíl neodpovídá – nelze potvrdit výpadek", layers)

    reasons = []
    degraded = False
    if down and down / len(known) >= 0.34:
        degraded = True
        reasons.append(f"{down} z {len(known)} internetových cílů nedostupných")
    elif down:
        reasons.append(f"{down} cíl(e) nedostupné (ignorováno – menšina)")
    if i.internet_rtts:
        med = statistics.median(i.internet_rtts)
        if med > i.high_latency_ms:
            degraded = True
            reasons.append(f"vysoká latence (medián {med:.0f} ms)")
    if i.internet_loss:
        med_loss = statistics.median(i.internet_loss)
        if med_loss >= i.high_loss_pct:
            degraded = True
            reasons.append(f"ztrátovost {med_loss:.0f} %")
    if i.gateway_up is False:
        reasons.append("brána neodpovídá na ping (může blokovat ICMP)")
    state = ConnState.DEGRADED if degraded else ConnState.ONLINE
    return ConnEvaluation(state, "; ".join(reasons) or "Internet dostupný", layers)
