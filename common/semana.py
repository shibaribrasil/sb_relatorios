"""Janelas semanais compartilhadas por Google Ads (semanal) e Giro semanal.

Semana = segunda a domingo. A semana em andamento vai só até o último dia FECHADO de todas as fontes (o custo do Google Ads e o GA4 chegam
com 1 dia de atraso, então hoje nunca entra) e é comparada com os MESMOS dias da(s) semana(s) anterior(es). Sem regra de negócio: só datas.
"""
import pandas as pd

DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def segunda(ts):
    ts = pd.Timestamp(ts).normalize()
    return ts - pd.Timedelta(days=ts.weekday())


def rotulo(seg, fim=None):
    """`S40/2026 · 28/09–29/09`; com `fim` a semana em andamento mostra só os dias já fechados."""
    seg = pd.Timestamp(seg)
    dom = seg + pd.Timedelta(days=6)
    if fim is not None:
        dom = min(dom, pd.Timestamp(fim))
    iso = seg.isocalendar()
    return f"S{int(iso.week):02d}/{int(iso.year)} · {seg.strftime('%d/%m')}–{dom.strftime('%d/%m')}"


def janela(seg, fechado):
    """Janela da semana `seg` cortada no último dia fechado. Devolve dict com ini, fim, n (dias), parcial, ant_ini, ant_fim."""
    seg, fechado = pd.Timestamp(seg), pd.Timestamp(fechado)
    fim = min(seg + pd.Timedelta(days=6), fechado)
    n = (fim - seg).days + 1
    return {"ini": seg, "fim": fim, "n": n, "parcial": n < 7, "ant_ini": seg - pd.Timedelta(days=7), "ant_fim": seg - pd.Timedelta(days=7) + pd.Timedelta(days=n - 1)}


def janela_k(seg, n, k):
    """Mesmos `n` primeiros dias da semana, `k` semanas antes de `seg`."""
    ini = pd.Timestamp(seg) - pd.Timedelta(days=7 * k)
    return ini, ini + pd.Timedelta(days=n - 1)


def semanas(primeiro_dia, fechado):
    """Segundas-feiras (mais recente primeiro) das semanas com ao menos 1 dia fechado, desde a semana de `primeiro_dia`."""
    ult, prim = segunda(fechado), segunda(primeiro_dia)
    n = int((ult - prim).days // 7) + 1
    return [ult - pd.Timedelta(days=7 * i) for i in range(n)]


def entre(df, col, ini, fim):
    return df[(df[col] >= pd.Timestamp(ini)) & (df[col] <= pd.Timestamp(fim))]
