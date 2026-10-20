"""Formatação em português. Números do cockpit aparecem em todo log e relatório."""


def brl(valor) -> str:
    """1234567.89 -> 'R$ 1.234.567,89'"""
    if valor is None:
        return "—"
    texto = f"{float(valor):,.2f}"
    return "R$ " + texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pct(valor, casas: int = 1) -> str:
    if valor is None:
        return "—"
    return f"{float(valor):.{casas}f}".replace(".", ",") + "%"
