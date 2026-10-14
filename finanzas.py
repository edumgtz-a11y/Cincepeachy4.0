"""
Balance Cinepeachy: cobros con fecha, inversión total y resumen por semana.

Ideas clave:
- Inversión = suma del costo_inversion de TODOS los productos (activos, en tránsito
  y ya archivados). No importa cuándo se compró cada uno.
- Cobrado = dinero de productos marcados como Pagado, en la semana en que se marcó.
- Balance = cobrado acumulado - inversión total. Cuando cruza el cero, ya se
  recuperó la inversión; desde ahí lo que sube es ganancia.
"""
import calendar
from collections import OrderedDict
from datetime import date, datetime, timedelta

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}
MESES_CORTOS = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}
_MES_POR_NOMBRE = {v: k for k, v in MESES.items()}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def etiqueta_mes(anio, mes):
    return f"{MESES[mes]} {anio}"


def parsear_mes(etiqueta):
    """'Octubre 2026' -> (2026, 10). Devuelve None si no se entiende."""
    try:
        nombre, anio = etiqueta.split()
        return int(anio), _MES_POR_NOMBRE[nombre]
    except Exception:
        return None


def _a_fecha(texto):
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except Exception:
        return None


def _nombre_item(producto, detalle):
    return f"{producto} ({detalle})" if detalle else producto


def _items_pagados_activos(datos):
    """Itera (producto, detalle, cantidad, monto) de lo marcado como pagado en productos activos."""
    for p_nombre, p_info in datos.get("productos", {}).items():
        tipo = p_info.get("tipo", "normal")
        clientes = p_info.get("clientes", {})
        if tipo == "normal" and isinstance(clientes, dict):
            precio = p_info.get("precio", 0.0)
            for _cli, info in clientes.items():
                if isinstance(info, dict) and info.get("pagado", False):
                    cant = info.get("cantidad", 1)
                    yield p_nombre, "", cant, cant * precio
        else:
            lista = clientes if isinstance(clientes, list) else (
                [v for v in clientes.values() if isinstance(v, dict)] if isinstance(clientes, dict) else []
            )
            for item in lista:
                if item.get("pagado", False):
                    cant = item.get("cantidad", 1)
                    yield p_nombre, item.get("detalle", ""), cant, cant * item.get("precio", 0.0)


# --------------------------------------------------------------------------
# Migración (una sola vez) y registro de cobros
# --------------------------------------------------------------------------
def migrar_cobros(datos):
    """
    Prepara los datos para llevar cobros con fecha. Lo que ya estaba pagado
    ANTES de esta actualización no tiene fecha, así que se junta en 'cobro_base'.
    Devuelve True si hubo cambios (hay que guardar).
    """
    if "cobros" in datos and "cobro_base" in datos:
        return False
    monto = 0.0
    detalle = {}
    for prod, det, cant, m in _items_pagados_activos(datos):
        monto += m
        nombre = _nombre_item(prod, det)
        detalle[nombre] = detalle.get(nombre, 0) + cant
    for h in datos.get("historial_mensual", []):
        monto += h.get("venta", 0.0)
        nombre = h.get("producto", "")
        detalle[nombre] = detalle.get(nombre, 0) + h.get("cantidad", 0)
    datos["cobros"] = datos.get("cobros", [])
    datos["cobro_base"] = {"monto": monto, "detalle": detalle}
    return True


def registrar_cobro(datos, cliente, producto, detalle, cantidad, monto, fecha=None):
    datos.setdefault("cobros", []).append({
        "fecha": (fecha or date.today()).strftime("%Y-%m-%d"),
        "cliente": cliente,
        "producto": producto,
        "detalle": detalle or "",
        "cantidad": cantidad,
        "monto": monto,
    })


def quitar_cobro(datos, cliente, producto, detalle, cantidad, monto):
    """
    Revierte un cobro (se desmarcó 'Pagado' o se canceló el pedido).
    Quita el más reciente que coincida; si no hay (era un pago anterior a la
    actualización), lo descuenta del cobro_base para que el balance siga cuadrando.
    """
    cobros = datos.setdefault("cobros", [])
    for i in range(len(cobros) - 1, -1, -1):
        c = cobros[i]
        if (c.get("cliente") == cliente and c.get("producto") == producto
                and (c.get("detalle") or "") == (detalle or "")
                and c.get("cantidad") == cantidad):
            del cobros[i]
            return True
    base = datos.setdefault("cobro_base", {"monto": 0.0, "detalle": {}})
    base["monto"] = max(0.0, base.get("monto", 0.0) - monto)
    nombre = _nombre_item(producto, detalle)
    det = base.setdefault("detalle", {})
    if nombre in det:
        det[nombre] = max(0, det[nombre] - cantidad)
        if det[nombre] == 0:
            del det[nombre]
    return False


# --------------------------------------------------------------------------
# Totales
# --------------------------------------------------------------------------
def inversion_total(datos):
    total = sum(p.get("costo_inversion", 0.0) for p in datos.get("productos", {}).values())
    total += sum(h.get("costo", 0.0) for h in datos.get("historial_mensual", []))
    return total


def cobrado_total(datos):
    base = datos.get("cobro_base", {}).get("monto", 0.0)
    return base + sum(c.get("monto", 0.0) for c in datos.get("cobros", []))


# --------------------------------------------------------------------------
# Meses y semanas
# --------------------------------------------------------------------------
def meses_disponibles(datos):
    """Etiquetas de meses con archivados o cobros, del más reciente al más antiguo."""
    pares = set()
    for h in datos.get("historial_mensual", []):
        p = parsear_mes(h.get("mes", ""))
        if p:
            pares.add(p)
    for c in datos.get("cobros", []):
        f = _a_fecha(c.get("fecha", ""))
        if f:
            pares.add((f.year, f.month))
    return [etiqueta_mes(a, m) for a, m in sorted(pares, reverse=True)]


def semanas_del_mes(anio, mes):
    """Semanas (lunes a domingo) que tocan el mes, recortadas a los días del mes."""
    primero = date(anio, mes, 1)
    ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1])
    lunes = primero - timedelta(days=primero.weekday())
    semanas = []
    while lunes <= ultimo:
        ini = max(lunes, primero)
        fin = min(lunes + timedelta(days=6), ultimo)
        semanas.append((ini, fin))
        lunes += timedelta(days=7)
    return semanas


def _rotulo(ini, fin):
    if ini.month == fin.month:
        return f"{ini.day}–{fin.day} {MESES_CORTOS[fin.month]}"
    return f"{ini.day} {MESES_CORTOS[ini.month]}–{fin.day} {MESES_CORTOS[fin.month]}"


def resumen_semanal(datos, anio, mes, hoy=None):
    """
    Lista de semanas del mes (hasta hoy) con: rótulo, cobrado, vendido y balance acumulado.
    El balance acumulado = cobros anteriores + cobros hasta el fin de esa semana - inversión total.
    """
    hoy = hoy or date.today()
    inv = inversion_total(datos)
    base = datos.get("cobro_base", {}).get("monto", 0.0)
    cobros = []
    for c in datos.get("cobros", []):
        f = _a_fecha(c.get("fecha", ""))
        if f:
            cobros.append((f, c))

    resultado = []
    for i, (ini, fin) in enumerate(semanas_del_mes(anio, mes), start=1):
        if ini > hoy:
            continue
        de_la_semana = [c for f, c in cobros if ini <= f <= fin]
        vendido = OrderedDict()
        for c in de_la_semana:
            nombre = _nombre_item(c.get("producto", ""), c.get("detalle", ""))
            vendido[nombre] = vendido.get(nombre, 0) + c.get("cantidad", 0)
        acumulado = base + sum(c.get("monto", 0.0) for f, c in cobros if f <= fin)
        resultado.append({
            "numero": i,
            "rotulo": _rotulo(ini, fin),
            "cobrado": sum(c.get("monto", 0.0) for c in de_la_semana),
            "vendido": list(vendido.items()),
            "balance": acumulado - inv,
        })
    return resultado
