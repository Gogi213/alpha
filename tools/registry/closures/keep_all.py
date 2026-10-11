"""Тождественный плагин KIND='keep': оставляет все сигналы B1. Гейт М5 «выкл = прежний» внутри раннера: после/до обязаны совпасть."""
NAME = "keep_all"
KIND, SOURCES = "keep", ("signals", "rounds")
PARAMS = {"on": (1,)}


def run(data, ctx, on):
    return [(d["symbol"], d["day_utc"], d["form"], d["signal_index"]) for d in data]
