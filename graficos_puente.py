# -*- coding: utf-8 -*-
"""
Gráficos de envolventes de carga viva.

No usa pyplot: trabaja sobre objetos Figure, de modo que sirve igual dentro de
la interfaz gráfica (Tk) que en el script por lotes (Agg).
"""

from typing import List, Optional

import numpy as np
from matplotlib.figure import Figure

COLOR_ROTULA = "darkorange"

# =============================================================================
# UNIDADES DE SALIDA
# =============================================================================
# El motor (motor_puente.py) siempre calcula en Tnf y m: esto es sólo una
# conversión de FUERZA para mostrar los gráficos; la distancia queda siempre
# en metros. 1 Tnf = 1000 kgf = 9.80665 kN (kgf y Tnf son unidades de fuerza
# "técnicas", definidas con la gravedad normal; el kN es una unidad del SI).
UNIDAD_DEFECTO = "Tonf (Tnf) y m"
UNIDADES = {
    UNIDAD_DEFECTO: (1.0, "Tnf"),
    "kgf y m": (1000.0, "kgf"),
    "kN y m": (9.80665, "kN"),
}


# =============================================================================
# ESQUEMA DE LA VIGA
# =============================================================================
# Niveles verticales del esquema (unidades de eje Y, que no escalan con la luz
# total: el eje Y siempre va de Y_MIN a Y_MAX sea cual sea el puente).
Y_LABEL = -0.65      # nombre del apoyo (A, B, C...)
Y_EXT_INI = -0.80    # arranque de la línea de extensión de la cota
Y_COTA = -1.05        # línea de cota (acotado de luces)
Y_MIN, Y_MAX = -1.45, 1.55
Y_FLECHA_PUNTA = 0.03   # la punta de la flecha casi toca la viga, sin tapar la línea
Y_FLECHA_COLA = 1.08    # arranque (cola) de la flecha de carga
Y_FLECHA_TXT = 1.14     # etiqueta del valor, encima de la cola
COLOR_CARGA = "red"


def _dibujar_apoyo(ax, x: float, fijo: bool, esc: float) -> None:
    """Símbolo de apoyo tipo plano de ingeniería: triángulo + rayado de suelo.

    fijo=True  -> apoyo articulado/fijo: el rayado va pegado a la base del
                  triángulo (no permite desplazamiento horizontal).
    fijo=False -> apoyo deslizante: dos rodillos entre el triángulo y el
                  rayado, el símbolo clásico de apoyo que sí puede correrse
                  (por dilatación térmica, por ejemplo). Por convención se usa
                  un único apoyo fijo (el primero) y el resto deslizantes,
                  como en un plano general de un puente.

    El ancho del triángulo escala con `esc` (L_total) para que se vea igual
    de proporcionado en un puente de 6 m que en uno de 200 m; el alto y el
    resto de las medidas quedan fijas en unidades de eje Y, que no escalan
    con la luz. Los rodillos se dibujan como marcador "o" (tamaño en puntos)
    y no como Circle-patch: un patch se aplasta en una elipse en cuanto el
    eje X escala con la luz total y el eje Y queda fijo, porque los ejes no
    son 1:1 (aspect="auto"); el marcador no tiene ese problema, igual que ya
    se hacía con la rótula.
    """
    hw = max(0.016 * esc, esc * 0.008)   # semi-ancho del triángulo
    h = 0.24                              # alto del triángulo
    ax.plot([x, x - hw, x + hw, x], [0.0, -h, -h, 0.0], "k-", lw=1.2,
            solid_joinstyle="miter", zorder=3)

    y = -h - 0.02
    if not fijo:
        y_rod = y - 0.045
        ax.plot([x - hw * 0.5, x + hw * 0.5], [y_rod, y_rod], "o",
                mfc="white", mec="black", mew=1.0, ms=6.5, zorder=3)
        y -= 0.09

    ancho = hw * 2.3
    ax.plot([x - ancho / 2, x + ancho / 2], [y, y], "k-", lw=1.0, zorder=3)
    for i in range(7):
        xi = x - ancho / 2 + i * (ancho / 6)
        ax.plot([xi, xi - hw * 0.28], [y, y - 0.13], "k-", lw=0.7, zorder=3)


def _rango_x_cargas(est, ejes):
    """x del eje trasero y del eje delantero al dibujar el vehículo entero
    recién entrando al puente (ver `_dibujar_cargas_eje`). Lo usa también
    `dibujar_esquema` para asegurarse de que el eje X alcance a mostrar el
    camión completo: con tramos cortos, el camión (que mide lo que mida,
    fijo en metros según la norma) puede ser más largo que el propio puente,
    y si el eje delantero quedara fuera del rango visible, su flecha no se
    dibujaría nunca.
    """
    largo = -min(e["offset"] for e in ejes)
    x_trasero = 0.04 * est.L_total
    return x_trasero, x_trasero + largo


def _dibujar_cargas_eje(ax, est, ejes, factor: float = 1.0, uf: str = "Tnf") -> None:
    """Flechas rojas sobre la viga: la carga de cada eje del vehículo,
    apuntando hacia abajo (la rueda empuja la viga), con su valor en Tnf.

    `ejes` son los ejes del vehículo tal cual vienen en `Parametros`
    (offset <= 0, el eje delantero en offset=0): es sólo un esquema
    ilustrativo de la carga de diseño, no la posición crítica de ningún
    cálculo, así que el camión se dibuja entero y recién empezando a cruzar
    el puente (el eje más trasero apenas adentro del primer apoyo) -- la
    posición que de verdad gobierna cada sección ya se resuelve puntual a
    punto en el barrido, no hace falta mostrarla acá. El vehículo (camión o
    tándem) ya queda identificado en el título de la figura, así que acá no
    se repite el nombre -junto a las flechas no hay dónde ponerlo sin que
    choque con algún valor, sea cual sea la escala del puente.
    """
    if not ejes:
        return
    x_trasero, x_delantero = _rango_x_cargas(est, ejes)
    # Si dos ejes quedan más cerca entre sí que lo que ocupa la etiqueta (p.ej.
    # el tándem, a 1.22 m), el valor de uno se superpone con el del vecino:
    # se alterna la altura de a dos para separarlos, igual que el abanico de
    # las anotaciones de picos.
    gap_min = 0.06 * est.L_total
    alto = False
    x_prev = None
    for e in sorted(ejes, key=lambda e: x_delantero + e["offset"]):
        x = x_delantero + e["offset"]
        alto = (x_prev is not None and x - x_prev < gap_min and not alto)
        x_prev = x
        y_txt = Y_FLECHA_TXT + (0.16 if alto else 0.0)
        ax.annotate("", xy=(x, Y_FLECHA_PUNTA), xytext=(x, Y_FLECHA_COLA),
                    arrowprops=dict(arrowstyle="-|>", color=COLOR_CARGA, lw=1.8,
                                    mutation_scale=14), zorder=7)
        ax.text(x, y_txt, "%.2f %s" % (e["P"] * factor, uf), ha="center", va="bottom",
                fontsize=8, color=COLOR_CARGA, fontweight="bold", zorder=7)


def _dibujar_carga_uniforme(ax, est, w: Optional[float],
                            factor: float = 1.0, uf: str = "Tnf") -> None:
    """Carga distribuida roja sobre toda la viga: una fila de flechas cortas
    hacia abajo unidas por una línea arriba (el símbolo clásico de "peine"
    para una UDL), con el valor (Tnf/m) centrado encima.

    Igual que `_dibujar_cargas_eje`, es ilustrativa de la MAGNITUD, no de un
    patrón de carga concreto: la faja, por ejemplo, en el cálculo real
    alterna de tramo en tramo según qué caso gobierne cada sección, pero acá
    se dibuja entera para mostrar de un vistazo cuánto vale.
    """
    if not w or w <= 0:
        return
    n = max(6, min(18, int(est.L_total / 2.5)))
    xs = np.linspace(0.0, est.L_total, n)
    ax.plot([xs[0], xs[-1]], [Y_FLECHA_COLA, Y_FLECHA_COLA], color=COLOR_CARGA,
            lw=1.3, zorder=7)
    for x in xs:
        ax.annotate("", xy=(x, Y_FLECHA_PUNTA), xytext=(x, Y_FLECHA_COLA),
                    arrowprops=dict(arrowstyle="-|>", color=COLOR_CARGA, lw=1.3,
                                    mutation_scale=10), zorder=7)
    ax.text(0.5 * est.L_total, Y_FLECHA_TXT, "w = %.3f %s/m" % (w * factor, uf),
            ha="center", va="bottom", fontsize=8.5, color=COLOR_CARGA,
            fontweight="bold", zorder=7)


def dibujar_esquema(ax, est, mostrar_luces: bool = True,
                    ejes_carga: Optional[list] = None,
                    carga_uniforme: Optional[float] = None,
                    factor: float = 1.0, uf: str = "Tnf") -> None:
    """Viga, apoyos (articulado + deslizantes), rótulas y acotado de luces.

    `ejes_carga` (opcional): ejes de un vehículo (ver `Parametros.ejes_camion`)
    a dibujar como flechas rojas de carga puntual -- un esquema ilustrativo
    del vehículo de diseño, no una posición crítica de cálculo (ver
    `_dibujar_cargas_eje`). `carga_uniforme` (opcional, Tnf/m): una carga
    distribuida a dibujar en su lugar (ver `_dibujar_carga_uniforme`); se
    ignora si ya se pasó `ejes_carga`.
    """
    ax.clear()
    x_der = est.L_total * 1.05
    if ejes_carga:
        # Si el vehículo es más largo que el puente (típico en tramos
        # cortos: el camión mide lo que mida en metros, fijo por norma), el
        # eje delantero puede caer más allá de L_total -- hay que ensanchar
        # el rango visible o esa flecha no se vería nunca.
        _, x_delantero_veh = _rango_x_cargas(est, ejes_carga)
        x_der = max(x_der, x_delantero_veh * 1.05)
    ax.set_xlim(-0.05 * est.L_total, x_der)
    ax.set_ylim(Y_MIN, Y_MAX)
    ax.axis("off")

    ax.plot([0, est.L_total], [0, 0], "k-", lw=2.2, solid_capstyle="butt", zorder=4)

    for i, a in enumerate(est.apoyos_x):
        _dibujar_apoyo(ax, a, fijo=(i == 0), esc=est.L_total)
        ax.text(a, Y_LABEL, est.nombres_apoyos[i], ha="center", va="center",
                fontweight="bold", fontsize=10)

    if ejes_carga:
        _dibujar_cargas_eje(ax, est, ejes_carga, factor=factor, uf=uf)
    elif carga_uniforme:
        _dibujar_carga_uniforme(ax, est, carga_uniforme, factor=factor, uf=uf)

    if est.rotulas:
        ax.plot(est.rotulas, np.zeros(len(est.rotulas)), "o",
                mfc="white", mec="black", mew=1.5, ms=9, zorder=5)
        for r in est.rotulas:
            ax.text(r, 0.42, "rótula\n%g m" % r, ha="center", va="bottom",
                    fontsize=8, color=COLOR_ROTULA, fontweight="bold")

    if mostrar_luces:
        for k in range(est.n_tramos):
            x0, x1 = est.apoyos_x[k], est.apoyos_x[k + 1]
            for xe in (x0, x1):
                ax.plot([xe, xe], [Y_EXT_INI, Y_COTA + 0.08], color="0.4", lw=0.6, zorder=1)
            ax.plot([x0, x1], [Y_COTA, Y_COTA], color="0.15", lw=0.8, zorder=2)
            dx = 0.006 * est.L_total
            for xe in (x0, x1):
                ax.plot([xe - dx, xe + dx], [Y_COTA - 0.045, Y_COTA + 0.045],
                        color="0.15", lw=1.1, zorder=3)
            ax.text(0.5 * (x0 + x1), Y_COTA - 0.08, "%g m" % est.tramos[k],
                    ha="center", va="top", fontsize=8.5, color="0.1", fontweight="bold")


def _config_ax(ax, est, y_lims, ylab) -> None:
    ax.set_xlim(-0.04 * est.L_total, est.L_total * 1.04)
    ax.set_ylim(y_lims)
    ax.set_ylabel(ylab, fontweight="bold")
    ax.grid(True, axis="y", linestyle="--", alpha=0.6)
    ax.axhline(0, color="black", lw=1.5)
    for p in est.apoyos_x:
        ax.axvline(x=p, color="gray", linestyle="--", alpha=0.7)
    for r in est.rotulas:
        ax.axvline(x=r, color=COLOR_ROTULA, linestyle=":", lw=1.8, alpha=0.9)


# =============================================================================
# ANOTACIÓN DE PICOS
# =============================================================================
def _fmt_valor(v: float) -> str:
    """2 decimales en Tnf/kN; sin decimales en kgf, donde los valores son miles."""
    return "%.0f" % v if abs(v) >= 1000 else "%.2f" % v


# Muchos picos caen justo en un apoyo o una rótula (línea vertical punteada
# de fondo) o cerca de la curva vecina: sin un fondo propio, esas líneas
# atraviesan el texto de la anotación y lo hacen ilegible. El bbox blanco
# semitransparente tapa lo que hay detrás sin ocultar del todo la grilla.
_BBOX_ANOT = dict(boxstyle="round,pad=0.18", facecolor="white", edgecolor="none", alpha=0.82)


def _picos_empatados(idx_mask, Y, X, modo, tol_rel=0.01, tol_abs=1e-3, sep_min=0.5,
                     max_picos=4):
    """Índices donde Y alcanza un extremo LOCAL genuino (máx o mín) dentro de
    idx_mask, a `tol_rel`/`tol_abs` del extremo global.

    Un simple argmax sólo devuelve el PRIMER punto que toca el extremo: si el
    mismo valor -o uno casi igual- se repite en otro punto del mismo tramo
    (dos picos gemelos, típico de un tándem/dos concentradas, o de un tramo
    simétrico), hay que reportar los dos, no sólo uno.

    El candidato tiene que ser un giro real de la curva (Y[j] >= vecinos para
    "max", <= para "min"), no cualquier punto que ande cerca del valor
    extremo: en una meseta larga y casi plana (típico de un tándem en un
    tramo largo, donde los dos picos gemelos están separados por un valle de
    apenas centésimas) una tolerancia relativa generosa deja "cerca" a
    decenas de puntos intermedios que no son picos, y agruparlos todos por
    `sep_min` fundiría los dos picos reales en uno solo aunque el valle entre
    ellos sea genuino. Exigir un giro local evita eso sin tener que apretar
    la tolerancia (lo que sí perdería picos gemelos con más diferencia entre
    sí). `sep_min` (m) sigue sirviendo para lo suyo: no contar el mismo pico
    físico dos veces sólo porque la discretización lo ensancha en varios
    puntos consecutivos que también giran.
    """
    idx_mask = np.asarray(idx_mask)
    vals = Y[idx_mask]
    extremo = float(np.max(vals)) if modo == "max" else float(np.min(vals))
    tol = max(tol_abs, abs(extremo) * tol_rel)

    candidatos = []
    for j in idx_mask.tolist():
        if 0 < j < len(Y) - 1:
            cerca = Y[j] >= extremo - tol if modo == "max" else Y[j] <= extremo + tol
            giro = Y[j] >= Y[j - 1] and Y[j] >= Y[j + 1] if modo == "max" \
                else Y[j] <= Y[j - 1] and Y[j] <= Y[j + 1]
            if cerca and giro:
                candidatos.append(j)
    # El extremo global siempre entra, aunque caiga en un borde de idx_mask
    # donde no se pudo evaluar el giro (p.ej. el primer/último punto del
    # tramo, ver el llamador).
    j_glob = int(idx_mask[int(np.argmax(vals))]) if modo == "max" else int(idx_mask[int(np.argmin(vals))])
    if j_glob not in candidatos:
        candidatos.append(j_glob)
    if not candidatos:
        return []

    orden = sorted(set(candidatos), key=lambda i: X[i])
    grupos = [[orden[0]]]
    for i in orden[1:]:
        if X[i] - X[grupos[-1][-1]] <= sep_min:
            grupos[-1].append(i)
        else:
            grupos.append([i])

    picos = []
    for g in grupos:
        sub = Y[g]
        j = g[int(np.argmax(sub))] if modo == "max" else g[int(np.argmin(sub))]
        picos.append(j)
    # si hay más grupos que max_picos, quedarse con los más extremos
    picos.sort(key=lambda i: Y[i], reverse=(modo == "max"))
    return picos[:max_picos]


def _anotar_picos_dfc(ax, X, est, Y_max, Y_min, color_txt) -> None:
    """Cortantes en el entorno inmediato de cada apoyo."""
    hechos_max, hechos_min = set(), set()
    for x_sup in est.apoyos_x:
        i = int(np.argmin(np.abs(X - x_sup)))
        ini, fin = max(0, i - 2), min(len(X) - 1, i + 2)

        v = float(np.max(Y_max[ini:fin + 1]))
        xv = X[ini + int(np.argmax(Y_max[ini:fin + 1]))]
        if v > 0.1 and xv not in hechos_max:
            ax.plot(xv, v, "ko", markersize=4, zorder=5)
            ax.annotate("%s\n(x=%.2f m)" % (_fmt_valor(v), xv), (xv, v), textcoords="offset points",
                        xytext=(0, 11), ha="center", va="bottom", fontsize=7.5,
                        fontweight="bold", color=color_txt, bbox=_BBOX_ANOT, zorder=6)
            hechos_max.add(xv)

        v = float(np.min(Y_min[ini:fin + 1]))
        xv = X[ini + int(np.argmin(Y_min[ini:fin + 1]))]
        if v < -0.1 and xv not in hechos_min:
            ax.plot(xv, v, "ko", markersize=4, zorder=5)
            ax.annotate("%s\n(x=%.2f m)" % (_fmt_valor(v), xv), (xv, v), textcoords="offset points",
                        xytext=(0, -15), ha="center", va="top", fontsize=7.5,
                        fontweight="bold", color=color_txt, bbox=_BBOX_ANOT, zorder=6)
            hechos_min.add(xv)


def _offsets_abanico(n: int, paso: float = 40.0) -> List[float]:
    """n corrimientos horizontales (en puntos), centrados en 0 y repartidos en
    abanico -- para picos gemelos muy próximos en x, un simple escalonado
    vertical no alcanza a separar los textos; hace falta abrirlos a los
    lados."""
    if n <= 1:
        return [0.0]
    total = (n - 1) * paso
    return [-total / 2.0 + i * paso for i in range(n)]


def _picos_por_direccion(idx_interior, X, D_normal, D_espejo, sep_min=0.5):
    """Un índice por cada sentido de circulación (normal/espejo) que tenga
    datos, dentro de idx_interior -- pero sólo si es un extremo local
    GENUINO (la curva da vuelta ahí), no el borde de una rampa que sigue
    subiendo/bajando monótonamente hacia el apoyo excluido de idx_interior;
    de lo contrario el punto "más cercano al apoyo permitido" quedaría
    marcado como si fuera un pico de campo, duplicando la anotación del
    apoyo (que ya lo cubre aparte, ver `_anotar_picos_dmf`).

    No importa si los valores empatan o no: son dos configuraciones físicas
    distintas -el vehículo entrando al puente por cada extremo- y ambas
    posiciones son de interés en el diseño, aunque el valor sea casi
    idéntico (típico en un tramo simétrico). `D_normal`/`D_espejo` ya vienen
    con el signo correcto (más grande = mejor: para M(-) hay que pasarles el
    array negado antes de llamar, ver `_anotar_picos_dmf`). Un sentido sin
    datos (p.ej. un tándem simétrico, sin espejo) se ignora; si ambos caen a
    menos de sep_min uno del otro, se los funde en un solo punto.
    """
    if len(idx_interior) == 0:
        return []
    candidatos = []
    for D in (D_normal, D_espejo):
        if D is None:
            continue
        vals = D[idx_interior]
        finito = np.isfinite(vals)
        if not np.any(finito):
            continue
        j = int(idx_interior[int(np.argmax(np.where(finito, vals, -np.inf)))])
        if 0 < j < len(D) - 1 and D[j] >= D[j - 1] and D[j] >= D[j + 1]:
            candidatos.append(j)
    if not candidatos:
        return []
    candidatos = sorted(set(candidatos), key=lambda i: X[i])
    fundidos = [candidatos[0]]
    for i in candidatos[1:]:
        if X[i] - X[fundidos[-1]] > sep_min:
            fundidos.append(i)
    return fundidos


def _anotar_picos_dmf(ax, X, est, Y_max, Y_min, color_txt, dir_data=None) -> None:
    """Momentos extremos dentro de cada tramo.

    Si se pasa `dir_data` (la envolvente de M por sentido de circulación de
    esta curva, ver Resultados.direccion_M), el pico de CADA sentido dentro
    del campo del tramo se reporta por separado -aunque sus valores
    empaten-, porque son dos configuraciones físicas distintas (el vehículo
    entrando por cada extremo del puente). Sin esos datos (envolventes sin
    vehículo, como carril o peso propio) se usa en cambio un criterio de
    empate por valor. Si quedan varios muy cerca en x, sus etiquetas se
    abren en abanico (una línea fina las conecta de vuelta a su punto) para
    no superponerse.

    Los dos EXTREMOS del tramo (sus apoyos) se tratan aparte, sin abanico:
    en un tramo intermedio ambos apoyos suelen tener un M(-) parecido por
    diseño, y eso los hace "empatar" en la búsqueda -- pero son dos apoyos
    vecinos distintos, no dos picos gemelos dentro del campo de este tramo.
    """
    dir_data = dir_data or {}
    hechos = set()
    for k in range(est.n_tramos):
        mask = (X >= est.apoyos_x[k]) & (X <= est.apoyos_x[k + 1])
        idx_mask = np.nonzero(mask)[0]
        if len(idx_mask) == 0:
            continue
        extremos_tramo = (int(idx_mask[0]), int(idx_mask[-1]))
        # Sólo se excluyen los dos nodos exactos de los apoyos: el resto del
        # campo queda disponible para _picos_por_direccion, que ya filtra
        # por su cuenta cualquier punto que no sea un extremo local genuino
        # (ver esa función) -- así una rampa monótona hacia el apoyo no
        # cuela como si fuera un pico de campo aparte.
        interior = idx_mask[1:-1]
        # Separación mínima FIJA (no proporcional a la luz): lo que hay que
        # evitar es partir un único pico ancho en varios por ruido de
        # discretización, no una propiedad que dependa del largo del tramo;
        # una luz larga no debería impedir separar dos picos reales a ~1 m.
        sep_min = 0.5

        for Y, signo, va, y0, D_n, D_e in (
                (Y_max, "max", "top", -15.0, dir_data.get("max_normal"), dir_data.get("max_espejo")),
                (Y_min, "min", "bottom", 11.0, dir_data.get("min_normal"), dir_data.get("min_espejo"))):
            picos = _picos_empatados(idx_mask, Y, X, signo, sep_min=sep_min)
            de_apoyo = [i for i in picos if i in extremos_tramo]
            # El criterio "por sentido" sólo sirve cuando AMBOS sentidos
            # traen datos utilizables: necesita comparar la posición crítica
            # de cada uno por separado. Un vehículo simétrico (el tándem, dos
            # cargas iguales) no barre sentido espejo -esa pasada sería
            # idéntica a la normal, así que se omite por eficiencia- y su
            # array queda todo -inf/inf; si sólo hay un sentido con datos, el
            # pico gemelo (cuando existe) es estructural, no de sentido de
            # circulación, y aparece como dos giros genuinos DENTRO de esa
            # misma curva: lo encuentra `_picos_empatados`, no éste.
            tiene = lambda D: D is not None and np.any(np.isfinite(D))
            if tiene(D_n) and tiene(D_e):
                # Para M(-) el "mejor" es el más NEGATIVO: se niega para
                # reutilizar el mismo argmax de _picos_por_direccion.
                s = -1.0 if signo == "min" else 1.0
                de_campo = _picos_por_direccion(interior, X, s * D_n, s * D_e, sep_min=sep_min)
            else:
                interior_set = set(interior.tolist())
                de_campo = [i for i in picos if i in interior_set]
            de_campo = sorted(de_campo, key=lambda i: X[i])

            for i in de_apoyo:
                v, xv = float(Y[i]), float(X[i])
                clave = (round(xv, 3), signo)
                if abs(v) > 1e-3 and clave not in hechos:
                    ax.plot(xv, v, "ko", markersize=4, zorder=5)
                    ax.annotate("%s\n(x=%.2f m)" % (_fmt_valor(v), xv), (xv, v),
                                textcoords="offset points", xytext=(0, y0),
                                ha="center", va=va, fontsize=7.5, fontweight="bold",
                                color=color_txt, bbox=_BBOX_ANOT, zorder=6)
                    hechos.add(clave)

            for dx_pt, i in zip(_offsets_abanico(len(de_campo)), de_campo):
                v, xv = float(Y[i]), float(X[i])
                clave = (round(xv, 3), signo)
                if abs(v) <= 1e-3 or clave in hechos:
                    continue
                ha = "center" if abs(dx_pt) < 1.0 else ("left" if dx_pt > 0 else "right")
                ax.plot(xv, v, "ko", markersize=4, zorder=5)
                ax.annotate("%s\n(x=%.2f m)" % (_fmt_valor(v), xv), (xv, v),
                            textcoords="offset points", xytext=(dx_pt, y0),
                            ha=ha, va=va, fontsize=7.5, fontweight="bold", color=color_txt,
                            bbox=_BBOX_ANOT, zorder=6,
                            arrowprops=(None if abs(dx_pt) < 1.0 else dict(
                                arrowstyle="-", color=color_txt, lw=0.6,
                                shrinkA=0, shrinkB=3, relpos=(0.5, 1 if va == "top" else 0))))
                hechos.add(clave)


# =============================================================================
# FIGURA DE ENVOLVENTES
# =============================================================================
def figura_envolvente(res, clave: str = "combinada", fig: Optional[Figure] = None,
                      con_esquema: bool = True, anotar: bool = True,
                      unidad: str = UNIDAD_DEFECTO) -> Figure:
    """Dibuja el esquema de la viga + DFC + DMF de una envolvente.

    `unidad` elige la unidad de fuerza de los ejes y las anotaciones (ver
    UNIDADES); la distancia siempre se grafica en metros.
    """
    est, X = res.estructura, res.X
    env = res.envolventes[clave]
    factor, uf = UNIDADES.get(unidad, UNIDADES[UNIDAD_DEFECTO])
    V_max, V_min = env.V_max * factor, env.V_min * factor
    M_max, M_min = env.M_max * factor, env.M_min * factor

    if fig is None:
        fig = Figure(figsize=(12, 9))
    fig.clear()

    if con_esquema:
        ejes_fig = fig.subplots(3, 1, gridspec_kw={"height_ratios": [0.8, 2, 2]})
        ax_esq, ax_v, ax_m = ejes_fig
        par = res.parametros
        # El esquema de cargas ilustra la que corresponda a la envolvente que
        # se está viendo: el segundo vehículo (tándem o carga militar) o el
        # camión especial muestran sus propios ejes; la faja y el peso
        # propio se dibujan como carga distribuida; cualquier otra
        # envolvente (camión, especial vs. camión, combinada...) muestra el
        # camión de diseño por default.
        if clave == "tandem":
            dibujar_esquema(ax_esq, est, ejes_carga=par.ejes_tandem, factor=factor, uf=uf)
        elif clave == "especial":
            dibujar_esquema(ax_esq, est, ejes_carga=res.ejes_especial, factor=factor, uf=uf)
        elif clave == "carril":
            dibujar_esquema(ax_esq, est, carga_uniforme=par.w_carril, factor=factor, uf=uf)
        elif clave == "peso_propio":
            dibujar_esquema(ax_esq, est, carga_uniforme=par.peso_propio_total(), factor=factor, uf=uf)
        else:
            dibujar_esquema(ax_esq, est, ejes_carga=par.ejes_camion, factor=factor, uf=uf)
    else:
        ax_v, ax_m = fig.subplots(2, 1)

    rotulo = "Envolvente de Carga Viva: %s" % env.titulo
    # Los títulos de la combinación pueden ser largos (nombran los vehículos y
    # la forma de combinar): se achica la letra en vez de dejar que se corte.
    tam = 15 if len(rotulo) <= 60 else max(8.5, 15.0 * 60.0 / len(rotulo))
    fig.suptitle(rotulo, fontsize=tam, fontweight="bold")

    v_abs = max(float(np.max(V_max)), abs(float(np.min(V_min))), 1e-6)
    _config_ax(ax_v, est, (-v_abs * 1.25, v_abs * 1.25), "Fuerza Cortante (%s)" % uf)
    ax_v.plot(X, V_max, "b-", lw=1.5, label="Máx (+)")
    ax_v.plot(X, V_min, "b--", lw=1.5, label="Mín (-)")
    ax_v.fill_between(X, V_min, V_max, color="skyblue", alpha=0.4)
    ax_v.legend(loc="upper right", fontsize=9)

    m_sup = float(np.max(M_max))
    m_inf = float(np.min(M_min))
    margen = max(abs(m_sup), abs(m_inf), 1e-6) * 0.2
    # Eje invertido: el momento positivo (tracción abajo) se dibuja hacia abajo
    _config_ax(ax_m, est, (m_sup + margen, m_inf - margen), "Momento Flector (%s·m)" % uf)
    ax_m.set_xlabel("Distancia a lo largo de la estructura (m)", fontweight="bold")
    ax_m.plot(X, M_max, "r-", lw=1.5, label="Máx (+)")
    ax_m.plot(X, M_min, "r--", lw=1.5, label="Mín (-)")
    ax_m.fill_between(X, M_min, M_max, color="salmon", alpha=0.4)
    ax_m.legend(loc="upper right", fontsize=9)

    if anotar:
        _anotar_picos_dfc(ax_v, X, est, V_max, V_min, "darkblue")
        _anotar_picos_dmf(ax_m, X, est, M_max, M_min, "darkred",
                          dir_data=getattr(res, "direccion_M", {}).get(clave))

    fig.tight_layout()
    return fig


# =============================================================================
# PESO PROPIO POR VIGA (desglose por partida)
# =============================================================================
ETIQUETAS_PP = {"pp_viga": "Viga", "pp_losa": "Losa", "pp_carpeta": "Carpeta asfáltica",
                "pp_barandas": "Baranda", "pp_losa_viga": "Losa + Viga",
                "pp_losa_viga_barandas": "Losa + Viga + Baranda", "pp_total": "TOTAL"}
# Colores bien diferenciados entre sí; el total va en negro punteado y
# grueso (ver el estilo de línea en `figura_peso_propio_viga`), así que
# comparte el color con la baranda sin confundirse con ella.
COLORES_PP = {"pp_viga": "blue", "pp_losa": "red", "pp_carpeta": "green",
             "pp_barandas": "black", "pp_losa_viga": "purple",
             "pp_losa_viga_barandas": "darkorange", "pp_total": "black"}
ESTILOS_PP = {"pp_total": "--"}


def _anotar_valores_pp(ax, X, curvas) -> None:
    """Anota el valor máximo GLOBAL y el mínimo GLOBAL de cada curva (partida
    + total) -uno solo de cada, no todos los picos locales por tramo/apoyo,
    que con hasta 5 curvas satura el gráfico y se sale de los ejes-, apilados
    en una columna con el color de cada curva.

    Todas las partidas de peso propio son cargas uniformes sobre la misma
    estructura, así que son proporcionales entre sí (mismo patrón, distinta
    magnitud) y comparten EXACTAMENTE la misma posición de pico: por eso se
    anotan apiladas en un solo punto por extremo, a diferencia de
    `_anotar_picos_dmf`/`_anotar_picos_dfc` (pensadas para una sola curva,
    donde varias etiquetas en el mismo punto se superpondrían). La posición
    se ubica con la primera curva de la lista (cualquiera sirve, son todas
    proporcionales); el valor que se imprime es el de cada curva en ese mismo
    punto. Los valores por tramo/apoyo de cada partida siguen disponibles en
    el reporte de texto y el Excel.
    """
    if not curvas:
        return
    paso = 13.0

    def apilar(idx, signo):
        va = "top" if signo == "max" else "bottom"
        base = -12.0 if signo == "max" else 9.0
        xv = float(X[idx])
        # Todas las etiquetas cuelgan del mismo punto de anclaje -el de la
        # PRIMERA curva (el total, o la más extrema si no hay total)-, no del
        # valor propio de cada una: si se ancla cada etiqueta en su propio
        # punto, una partida chica (p.ej. la baranda, casi 0) con un offset
        # grande puede terminar más cerca de la etiqueta de una partida
        # grande que la etiqueta de esa partida grande consigo misma, y las
        # dos se superponen. El marcador sí va en el valor real de cada una.
        y_ancla = float(curvas[0][1][idx])
        n = 0
        for etiqueta, curva, color, _estilo in curvas:
            v = float(curva[idx])
            if abs(v) <= 1e-3:
                continue
            dy = base - paso * n if signo == "max" else base + paso * n
            ax.plot(xv, v, "o", markersize=4, color=color, zorder=6)
            ax.annotate("%s: %s" % (etiqueta, _fmt_valor(v)), (xv, y_ancla),
                       textcoords="offset points", xytext=(0, dy),
                       ha="center", va=va, fontsize=7.5, fontweight="bold",
                       color=color, bbox=_BBOX_ANOT, zorder=7)
            n += 1

    ref = curvas[0][1]
    j_max, j_min = int(np.argmax(ref)), int(np.argmin(ref))
    apilar(j_max, "max")
    if j_min != j_max:
        apilar(j_min, "min")


def figura_peso_propio_viga(res, activos: List[str], fig: Optional[Figure] = None,
                            con_esquema: bool = True, anotar: bool = True,
                            unidad: str = UNIDAD_DEFECTO) -> Figure:
    """DFC y DMF del peso propio por viga: cada partida o combinación
    marcada, con su color propio, y el valor máximo/mínimo global de cada una
    anotado.

    `activos` es la sublista de `res.componentes_pp_viga` que el usuario
    tiene marcada -las cuatro partidas ("pp_viga", "pp_losa", "pp_carpeta",
    "pp_barandas"), dos combinaciones ("pp_losa_viga", que suma losa y viga;
    "pp_losa_viga_barandas", que además suma la baranda) y el total fijo
    ("pp_total", siempre viga+losa+carpeta+baranda, no la suma de lo que esté
    marcado)-, cada una ya calculada en `calcular()` por superposición; acá
    sólo se eligen cuáles mostrar. Con la lista vacía se dibujan los ejes sin
    curvas. Cada una es un único estado de carga (el peso propio no tiene
    envolvente de posición como la carga viva), así que V y M son una sola
    curva, no un par máx/mín.
    """
    est, X = res.estructura, res.X
    factor, uf = UNIDADES.get(unidad, UNIDADES[UNIDAD_DEFECTO])

    if fig is None:
        fig = Figure(figsize=(12, 9))
    fig.clear()

    if con_esquema:
        ejes = fig.subplots(3, 1, gridspec_kw={"height_ratios": [0.8, 2, 2]})
        ax_esq, ax_v, ax_m = ejes
        dibujar_esquema(ax_esq, est, carga_uniforme=res.parametros.peso_propio_total(), factor=factor, uf=uf)
    else:
        ax_v, ax_m = fig.subplots(2, 1)

    fig.suptitle("Peso propio por viga: desglose por partida", fontsize=15, fontweight="bold")

    # El total (si está marcado) va primero: ancla dónde se apilan las
    # etiquetas de `_anotar_valores_pp` (su pico es siempre el más extremo,
    # al incluir todas las partidas); si no está marcado, da igual cuál va
    # primero, son todas proporcionales entre sí.
    activos = [k for k in activos if k in res.componentes_pp_viga]
    activos = sorted(activos, key=lambda k: 0 if k == "pp_total" else 1)
    curvas_V = {k: res.envolventes[k].V_max * factor for k in activos}
    curvas_M = {k: res.envolventes[k].M_max * factor for k in activos}

    # Margen generoso (no el 1.25 habitual): hay que dejar sitio a las
    # etiquetas apiladas de cada curva marcada.
    v_abs = max([float(np.max(np.abs(c))) for c in curvas_V.values()] + [1e-6])
    _config_ax(ax_v, est, (-v_abs * 1.55, v_abs * 1.55), "Fuerza Cortante (%s)" % uf)
    ax_v.grid(True, axis="x", linestyle="--", alpha=0.6)
    for k in activos:
        ax_v.plot(X, curvas_V[k], color=COLORES_PP[k], linestyle=ESTILOS_PP.get(k, "-"),
                 lw=(2.6 if k == "pp_total" else 1.5), label=ETIQUETAS_PP[k])
    if activos:
        ax_v.legend(loc="upper right", fontsize=8.5)

    m_sup = max([float(np.max(c)) for c in curvas_M.values()] + [0.0])
    m_inf = min([float(np.min(c)) for c in curvas_M.values()] + [0.0])
    # Igual que en V: margen generoso para las etiquetas apiladas.
    margen = max(abs(m_sup), abs(m_inf), 1e-6) * 0.55
    # Eje invertido: el momento positivo (tracción abajo) se dibuja hacia abajo
    _config_ax(ax_m, est, (m_sup + margen, m_inf - margen), "Momento Flector (%s·m)" % uf)
    ax_m.grid(True, axis="x", linestyle="--", alpha=0.6)
    ax_m.set_xlabel("Distancia a lo largo de la estructura (m)", fontweight="bold")
    for k in activos:
        ax_m.plot(X, curvas_M[k], color=COLORES_PP[k], linestyle=ESTILOS_PP.get(k, "-"),
                 lw=(2.6 if k == "pp_total" else 1.5), label=ETIQUETAS_PP[k])
    if activos:
        ax_m.legend(loc="upper right", fontsize=8.5)

    if anotar and activos:
        curvas_v_anot = [(ETIQUETAS_PP[k], curvas_V[k], COLORES_PP[k],
                         ESTILOS_PP.get(k, "-")) for k in activos]
        curvas_m_anot = [(ETIQUETAS_PP[k], curvas_M[k], COLORES_PP[k],
                         ESTILOS_PP.get(k, "-")) for k in activos]
        _anotar_valores_pp(ax_v, X, curvas_v_anot)
        _anotar_valores_pp(ax_m, X, curvas_m_anot)

    fig.tight_layout()
    return fig

