"""Тождество KIND='rounds': ничего не заменяет; подставное дерево s-v должно дать закрытия = цепочка без плагина (гейт М5 «выкл = прежний»)."""
NAME = "rounds_none"
KIND, SOURCES = "rounds", ()
PARAMS = {"off": (0,)}


def run(data, ctx, off):
    return {}
