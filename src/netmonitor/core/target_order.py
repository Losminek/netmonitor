"""Operace nad seznamem cílů a sekcí (čistá logika bez Qt – testovatelné).

Seznam je plochý: nadpis sekce patří ke všem cílům, které za ním následují až
do dalšího nadpisu. Cíle před první sekcí jsou "bez sekce".
"""
from __future__ import annotations

from .config import PingTarget


def index_of(items: list[PingTarget], tid: str) -> int:
    for i, t in enumerate(items):
        if t.id == tid:
            return i
    raise KeyError(tid)


def block_range(items: list[PingTarget], i: int) -> tuple[int, int]:
    """Rozsah [start, end) bloku: sekce + její cíle, nebo jediný cíl."""
    if not items[i].is_section:
        return i, i + 1
    j = i + 1
    while j < len(items) and not items[j].is_section:
        j += 1
    return i, j


def insert_target(items: list[PingTarget], new: PingTarget, anchor_id: str | None) -> list[PingTarget]:
    """Vloží cíl za vybranou položku (za nadpis sekce = na začátek sekce); bez výběru na konec."""
    out = list(items)
    if anchor_id is None:
        out.append(new)
        return out
    try:
        i = index_of(out, anchor_id)
    except KeyError:
        out.append(new)
        return out
    out.insert(i + 1, new)
    return out


def move(items: list[PingTarget], tid: str, delta: int) -> list[PingTarget]:
    """Posun o jednu pozici nahoru (-1) / dolů (+1).

    Cíl se prohodí se sousedem (může tím přejít do vedlejší sekce).
    Sekce se posouvá celá i se svými cíli – přeskočí celý sousední blok.
    """
    out = list(items)
    i = index_of(out, tid)
    if not out[i].is_section:
        j = i + delta
        if 0 <= j < len(out):
            out[i], out[j] = out[j], out[i]
        return out
    s, e = block_range(out, i)
    block = out[s:e]
    rest = out[:s] + out[e:]
    if delta < 0:
        if s == 0:
            return out
        # najdi začátek předchozího bloku (sekce, nebo cíle bez sekce na začátku)
        k = s - 1
        while k > 0 and not rest[k].is_section:
            k -= 1
        if not rest[k].is_section:
            # před sekcí jsou už jen cíle bez sekce – výš nelze (pohltila by je)
            return out
        insert_at = k
    else:
        if e >= len(out):
            return out
        nxt_start = s                 # v `rest` začíná následující blok na indexu s
        _, nxt_end = block_range(rest, nxt_start)
        insert_at = nxt_end
    return rest[:insert_at] + block + rest[insert_at:]


def remove(items: list[PingTarget], tid: str) -> list[PingTarget]:
    """Smaže položku. U sekce se maže jen nadpis – její cíle zůstanou (připojí se k předchozí sekci)."""
    return [t for t in items if t.id != tid]


def reorder(items: list[PingTarget], ids: list[str]) -> list[PingTarget]:
    """Nové pořadí podle seznamu ID (výsledek přetažení myší). Neznámé ID se ignorují,
    chybějící položky se připojí na konec – nic se neztratí."""
    by_id = {t.id: t for t in items}
    out = [by_id[i] for i in ids if i in by_id]
    seen = {t.id for t in out}
    out += [t for t in items if t.id not in seen]
    return out


def section_of(items: list[PingTarget], tid: str) -> str | None:
    """Název sekce, do které cíl patří (None = bez sekce)."""
    cur = None
    for t in items:
        if t.is_section:
            cur = t.name
        elif t.id == tid:
            return cur
    return None
