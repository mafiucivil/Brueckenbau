# -*- coding: utf-8 -*-
"""
Motor de cálculo de envolventes de carga viva AASHTO HL-93 para vigas
continuas y vigas Gerber (con rótulas internas).

Método directo de rigidez con EI = 1: en una viga prismática la rigidez se
cancela y las fuerzas internas por carga viva no dependen de la sección.
Las rótulas se introducen liberando el giro de un extremo de barra mediante
condensación estática.

Este módulo no dibuja ni escribe archivos: sólo calcula. Lo usan tanto
gui_puente.py (interfaz gráfica) como carga_viva_vias_puentes.py (script).
"""

from dataclasses import dataclass, field, replace
from typing import List, Dict, Optional, Callable, Sequence, Tuple

import numpy as np

TOL_NODO = 1e-6
_GAUSS = (-1.0 / np.sqrt(3.0), 1.0 / np.sqrt(3.0))   # exacta para el integrando cúbico

# La AASHTO define el HL-93 en unidades inglesas. Estas dos constantes son
# conversiones exactas, para no arrastrar el redondeo de la edición métrica.
PIE = 0.3048            # 1 ft en metros
KIP = 0.45359237        # 1 kip en Tnf (1 kip = 1000 lb = 453.59237 kgf)


# =============================================================================
# CARGAS DE DISEÑO NORMATIVAS
# =============================================================================
HS20_44 = "AASHTO Standard (HS20-44)"
HL_93 = "AASHTO LRFD (HL-93)"
PERSONALIZADO = "Personalizado"

# Cada norma fija vehículos, carga de faja e impacto. "Personalizado" no toca
# nada: deja lo que el usuario haya escrito.
NORMAS: Dict[str, dict] = {
    HS20_44: {
        "metodologia": (
            "Camión HS20-44 (8-32-32 kip) O carga de faja: son ALTERNATIVOS,\n"
            "gobierna el que dé más.\n"
            "Faja: 0.64 kip/ft + concentrada de 18 kip (momento) / 26 kip (corte).\n"
            "Dos concentradas para momento negativo en vigas continuas.\n"
            "Impacto I = 50/(L+125) <= 0.30, sobre la envolvente completa.\n"
            "Es el camión de diseño del Manual de Carreteras de Chile."),
        "nombre_camion": "CAMIÓN HS20-44",
        "ejes_camion": [(KIP * 8.0, 0.0), (KIP * 32.0, -PIE * 14.0), (KIP * 32.0, -PIE * 28.0)],
        "sep_variable": True,
        "sep_min": PIE * 14.0,
        "sep_max": PIE * 30.0,
        "usar_tandem": True,
        "nombre_tandem": "CARGA MILITAR ALTERNATIVA",
        "ejes_tandem": [(KIP * 24.0, 0.0), (KIP * 24.0, -PIE * 4.0)],
        "w_carril": KIP * 0.64 / PIE,
        "p_conc_momento": KIP * 18.0,
        "p_conc_corte": KIP * 26.0,
        "dos_conc_momento_negativo": True,
        "impacto_modo": "aashto_standard",
        "factor_dinamico": 1.30,
        "modo_combinacion": "alternativa",
        "factor_mayoracion": 1.20,   # Chile mayora 1.20 la carga viva en AASHTO Standard
    },
    HL_93: {
        "metodologia": (
            "(Camión HL-93 o Tándem) MÁS carga de carril: se SUMAN.\n"
            "Carril: 0.64 kip/ft, sin carga concentrada.\n"
            "Impacto 1 + IM = 1.33 fijo, sólo sobre el vehículo,\n"
            "nunca sobre la carga de carril."),
        "nombre_camion": "CAMIÓN HL-93",
        "ejes_camion": [(KIP * 8.0, 0.0), (KIP * 32.0, -PIE * 14.0), (KIP * 32.0, -PIE * 28.0)],
        "sep_variable": True,
        "sep_min": PIE * 14.0,
        "sep_max": PIE * 30.0,
        "usar_tandem": True,
        "nombre_tandem": "TÁNDEM",
        "ejes_tandem": [(KIP * 25.0, 0.0), (KIP * 25.0, -PIE * 4.0)],
        "w_carril": KIP * 0.64 / PIE,
        "p_conc_momento": 0.0,
        "p_conc_corte": 0.0,
        "dos_conc_momento_negativo": False,
        "impacto_modo": "fijo",
        "factor_dinamico": 1.33,
        "modo_combinacion": "suma",
        "factor_mayoracion": 1.0,    # Chile no mayora la carga viva en AASHTO LRFD
    },
}


def aplicar_norma(par: "Parametros", norma: str) -> "Parametros":
    """Reescribe en `par` las cargas de diseño de la norma elegida.

    También fija la mayoración global de carga viva que Chile aplica a cada
    norma: 1.20 en AASHTO Standard, 1.0 (sin mayorar) en AASHTO LRFD. El campo
    queda editable igual que el resto: si un proyecto puntual no lleva 1.20 en
    Standard, se ajusta a mano después de elegir la norma.

    No toca la geometría (tramos, rótulas), el camión especial ni dx, que son
    del proyecto y no de la norma.
    """
    par.norma = norma
    d = NORMAS.get(norma)
    if d is None:                       # "Personalizado": se respeta todo
        return par
    par.nombre_camion = d["nombre_camion"]
    par.ejes_camion = [{"P": P, "offset": o} for P, o in d["ejes_camion"]]
    par.sep_variable = d["sep_variable"]
    par.sep_min = d["sep_min"]
    par.sep_max = d["sep_max"]
    par.usar_tandem = d["usar_tandem"]
    par.nombre_tandem = d["nombre_tandem"]
    par.ejes_tandem = [{"P": P, "offset": o} for P, o in d["ejes_tandem"]]
    par.w_carril = d["w_carril"]
    par.p_conc_momento = d["p_conc_momento"]
    par.p_conc_corte = d["p_conc_corte"]
    par.dos_conc_momento_negativo = d["dos_conc_momento_negativo"]
    par.impacto_modo = d["impacto_modo"]
    par.factor_dinamico = d["factor_dinamico"]
    par.modo_combinacion = d["modo_combinacion"]
    par.factor_mayoracion = d["factor_mayoracion"]
    return par


def impacto_aashto_standard(L_m: float) -> float:
    """I = 50/(L+125) <= 0.30, con L en pies (AASHTO Standard 3.8.2)."""
    return min(0.30, 50.0 / (L_m / PIE + 125.0))


# Tabla 3.23.1 de AASHTO Standard (método "S/D"): factor de distribución de
# momento para vigas interiores, g = S/D con S = separación entre vigas en
# pies. D depende del material del tablero/viga y de si el puente se diseña
# para una vía o para dos o más. S_max es el límite de validez de la fórmula;
# más allá de eso la norma pide un análisis más refinado.
#   clave: (D_una_via, S_max_una_via, D_dos_o_mas, S_max_dos_o_mas)
_TABLA_DISTRIBUCION = {
    "hormigon": (6.5, 6.0, 6.0, 10.0),   # "Concrete T-Beams" (hormigón armado)
    "acero": (7.0, 10.0, 5.5, 14.0),     # "Concrete on Steel I-Beam Stringers"
}


def factor_distribucion_momento(tipo_viga: str, separacion_m: float,
                                n_vias: int) -> Tuple[float, float, float, Optional[str]]:
    """g, D, S (en ft), aviso — coeficiente de distribución de momento AASHTO
    Standard Tabla 3.23.1, para viga interior."""
    D_1, Smax_1, D_2, Smax_2 = _TABLA_DISTRIBUCION[tipo_viga]
    D, S_max = (D_1, Smax_1) if n_vias <= 1 else (D_2, Smax_2)
    S_ft = separacion_m / PIE
    g = S_ft / D
    aviso = None
    if S_ft > S_max:
        aviso = ("S = %.2f ft supera el límite de validez de la fórmula S/D (%.1f ft "
                 "para este caso); AASHTO recomienda un análisis más refinado." % (S_ft, S_max))
    return g, D, S_ft, aviso


# =============================================================================
# PARÁMETROS DE ENTRADA
# =============================================================================
@dataclass
class Parametros:
    """Datos de entrada del análisis.

    El motor es agnóstico a las unidades: basta con que sean coherentes. Con
    cargas en Tnf y luces en m, los resultados salen en Tnf y Tnf·m; si se
    ingresan las cargas en kN, salen en kN y kN·m.

    La carga de diseño se elige con `norma` (ver NORMAS). Todo se escribe con
    las conversiones exactas de las unidades inglesas, no con el redondeo de la
    edición métrica del AASHTO.

    HS20-44 (AASHTO Standard; es el camión de diseño del Manual de Carreteras
    de Chile):
        camión: 8 - 32 - 32 kip, 14 ft fijos y 14 a 30 ft variables
        carga de faja: 0.64 kip/ft MÁS una carga concentrada de 18 kip para
                       momento y 26 kip para corte, ubicada donde más daña
        impacto: I = 50/(L+125) <= 0.30, con L en pies
    HL-93 (AASHTO LRFD):
        camión igual al HS20-44, más tándem de 2 x 25 kip a 4 ft
        carga de carril: 0.64 kip/ft, sin carga concentrada
        impacto: 1 + IM = 1.33 fijo
    """
    norma: str = HS20_44
    tramos: List[float] = field(default_factory=lambda: [10.0, 12.0, 10.0])
    rotulas: List[float] = field(default_factory=list)
    ejes_camion: List[Dict[str, float]] = field(default_factory=lambda: [
        {"P": KIP * 8.0, "offset": 0.0},
        {"P": KIP * 32.0, "offset": -PIE * 14.0},    # -4.2672 m
        {"P": KIP * 32.0, "offset": -PIE * 28.0},    # -8.5344 m (14 + 14 ft)
    ])
    nombre_camion: str = "CAMIÓN HS20-44"

    # Segundo vehículo normativo: tándem en HL-93, carga militar alternativa
    # (2 x 24 kip a 4 ft, AASHTO Standard 3.7.4) en HS20-44.
    usar_tandem: bool = True
    nombre_tandem: str = "CARGA MILITAR ALTERNATIVA"
    ejes_tandem: List[Dict[str, float]] = field(default_factory=lambda: [
        {"P": KIP * 24.0, "offset": 0.0},
        {"P": KIP * 24.0, "offset": -PIE * 4.0},     # -1.2192 m
    ])

    # Carga de faja: uniforme + carga concentrada móvil (0 = sin concentrada).
    w_carril: float = KIP * 0.64 / PIE   # 0.64 kip/ft = 0.9524 Tnf/m
    p_conc_momento: float = KIP * 18.0   # 18 kip
    p_conc_corte: float = KIP * 26.0     # 26 kip
    dos_conc_momento_negativo: bool = True

    dx: float = 0.05                # paso de discretización (m)

    # Impacto: "fijo" usa factor_dinamico tal cual; "aashto_standard" calcula
    # I = 50/(L+125) <= 0.30 por tramo, con L en pies.
    impacto_modo: str = "aashto_standard"
    factor_dinamico: float = 1.33

    # Cómo se combinan vehículo y faja:
    #   "alternativa" (AASHTO Standard / HS20-44): son estados ALTERNATIVOS,
    #       gobierna el mayor, y el impacto afecta a ambos.
    #   "suma" (AASHTO LRFD / HL-93): se suman, y el impacto sólo afecta al
    #       vehículo, no a la carga de carril.
    modo_combinacion: str = "alternativa"

    # Mayoración global de la carga viva. `aplicar_norma` la fija en 1.20 para
    # AASHTO Standard y 1.0 (sin mayorar) para AASHTO LRFD, que es lo que Chile
    # usa en cada norma; este valor de fábrica de la dataclass sólo importa si
    # se arma un Parametros() sin pasar por aplicar_norma.
    factor_mayoracion: float = 1.0

    # Peso propio estimado (carga permanente), aplicado como una carga
    # uniformemente distribuida sobre TODA la estructura (no se alterna por
    # tramo como el carril: el peso propio siempre está, en toda la luz).
    # No se toca al cambiar de norma (AASHTO Standard/LRFD): es del proyecto,
    # no de la carga viva.
    incluir_peso_propio: bool = False
    ancho_tablero: float = 8.0          # m, ancho transversal del puente
    espesor_losa: float = 0.20          # m, 20 cm por defecto
    densidad_hormigon: float = 2.4      # Tnf/m3
    espesor_carpeta: float = 0.05       # m, 5 cm por defecto
    densidad_asfalto: float = 2.2       # Tnf/m3
    peso_barandas: float = 0.0          # Tnf/m, ya combinado (ambos lados)
    peso_viga: float = 0.0              # Tnf/m, peso de UNA viga
    n_vigas: int = 0

    # Coeficiente de distribución transversal de momento (AASHTO Standard,
    # Tabla 3.23.1, método "S/D"): pasa la envolvente de una vía completa a
    # la envolvente por viga interior. Depende del material de la viga y de
    # la separación entre ejes de vigas. No se toca al cambiar de norma.
    incluir_distribucion: bool = False
    tipo_viga: str = "acero"        # "hormigon" | "acero"
    separacion_vigas: float = 2.0   # m, separación S entre ejes de vigas
    n_vias_diseno: int = 2          # 1, o 2 (representa "2 o más")

    # Peso propio "por viga": desglose individual de cada partida (viga, losa,
    # carpeta, baranda) para graficarlas por separado y prender/apagar cada
    # una. A diferencia de `desglose_peso_propio` (toda la vía), acá losa y
    # carpeta se reparten por `ancho_tributario` en vez del ancho completo del
    # tablero, y `peso_viga` se toma tal cual (ya es de UNA viga). No se toca
    # al cambiar de norma, igual que el resto del peso propio.
    incluir_pp_por_viga: bool = False
    ancho_tributario: float = 2.0        # m, ancho tributario de losa/carpeta
    # Reparto de la baranda (`peso_barandas`, ya combinado ambos lados) en el
    # peso propio por viga:
    #   "ninguna"   -> 0 (viga interior: la Tabla 3.23.1 en la que se basa el
    #                  coeficiente de distribución de carga viva es para viga
    #                  interior, que en rigor no recibe baranda).
    #   "completa"  -> el 100% de `peso_barandas` va a esta viga (viga de
    #                  borde, que recibe toda la carga de un lado o se
    #                  analiza como si la recibiera entera).
    #   "repartida" -> `peso_barandas` / `n_vigas` (promedio simplificado
    #                  entre todas las vigas, sin distinguir borde/interior).
    modo_baranda_pp: str = "ninguna"

    # Separación variable entre los dos ejes traseros del camión (AASHTO
    # 3.6.1.2.2): se barre el rango completo y se toma la envolvente. Cuando
    # está activa, el offset del último eje lo fija el barrido.
    sep_variable: bool = True
    sep_min: float = PIE * 14.0     # 14 ft = 4.2672 m
    sep_max: float = PIE * 30.0     # 30 ft = 9.1440 m
    sep_paso: float = 0.10

    # Vehículo especial (transporte sobredimensionado, carga de permiso, etc.):
    # cualquier número de ejes con cargas y separaciones arbitrarias. Lista
    # vacía = no se analiza. Si incluir_especial es True entra además en la
    # envolvente gobernante de vehículos y por lo tanto en la combinación.
    ejes_especial: List[Dict[str, float]] = field(default_factory=list)
    incluir_especial: bool = False
    nombre_especial: str = "CAMIÓN ESPECIAL"

    def validar(self) -> None:
        """Lanza ValueError con un mensaje legible si los datos no sirven."""
        if not self.tramos:
            raise ValueError("Debe definir al menos un tramo.")
        for L in self.tramos:
            if L <= 0:
                raise ValueError("Todas las luces de tramo deben ser mayores que cero.")
        if self.dx <= 0:
            raise ValueError("El paso dx debe ser mayor que cero.")
        if self.dx > min(self.tramos) / 4.0:
            raise ValueError("El paso dx es demasiado grueso frente a la luz menor "
                             "(use como máximo L_min/4).")
        if not self.ejes_camion:
            raise ValueError("Debe definir al menos un eje para el camión.")
        if self.usar_tandem and not self.ejes_tandem:
            raise ValueError("Debe definir al menos un eje para el segundo vehículo, "
                             "o desactivarlo.")
        vehiculos = [("camión", self.ejes_camion)]
        if self.usar_tandem:
            vehiculos.append((self.nombre_tandem.lower(), self.ejes_tandem))
        for nombre, ejes in vehiculos:
            for e in ejes:
                if e["P"] <= 0:
                    raise ValueError("Las cargas de eje del %s deben ser positivas." % nombre)
            if max(e["offset"] for e in ejes) > TOL_NODO:
                raise ValueError("Los offsets del %s deben ser <= 0 (el eje delantero "
                                 "es el origen)." % nombre)
        if self.w_carril < 0:
            raise ValueError("La carga de faja no puede ser negativa.")
        if self.p_conc_momento < 0 or self.p_conc_corte < 0:
            raise ValueError("Las cargas concentradas de la faja no pueden ser negativas.")
        if self.factor_dinamico <= 0:
            raise ValueError("El factor dinámico debe ser positivo.")
        if self.impacto_modo not in ("fijo", "aashto_standard"):
            raise ValueError("El modo de impacto debe ser «fijo» o «aashto_standard».")
        if self.modo_combinacion not in ("suma", "alternativa"):
            raise ValueError("La combinación debe ser «suma» o «alternativa».")
        if self.factor_mayoracion <= 0:
            raise ValueError("El factor de mayoración debe ser positivo.")
        if self.incluir_peso_propio:
            if self.ancho_tablero <= 0:
                raise ValueError("El ancho del tablero debe ser positivo.")
            if self.espesor_losa < 0 or self.espesor_carpeta < 0:
                raise ValueError("Los espesores de losa y carpeta no pueden ser negativos.")
            if self.densidad_hormigon <= 0 or self.densidad_asfalto <= 0:
                raise ValueError("Las densidades de hormigón y asfalto deben ser positivas.")
            if self.peso_barandas < 0 or self.peso_viga < 0:
                raise ValueError("El peso de barandas y el peso por viga no pueden ser negativos.")
            if self.n_vigas < 0:
                raise ValueError("El número de vigas no puede ser negativo.")
            if self.peso_propio_total() <= 0:
                raise ValueError("El peso propio total dio cero: revise losa, carpeta, "
                                 "barandas y vigas (al menos uno debe ser mayor que cero).")
        if self.incluir_distribucion:
            if self.tipo_viga not in ("hormigon", "acero"):
                raise ValueError("El tipo de viga debe ser «hormigon» o «acero».")
            if self.separacion_vigas <= 0:
                raise ValueError("La separación entre vigas debe ser positiva.")
            if self.n_vias_diseno not in (1, 2):
                raise ValueError("El número de vías de diseño debe ser 1 o 2 (2 = dos o más).")
        if self.incluir_pp_por_viga:
            if not self.incluir_peso_propio:
                raise ValueError("Para desglosar el peso propio por viga primero debe "
                                 "activar el peso propio.")
            if self.ancho_tributario <= 0:
                raise ValueError("El ancho tributario debe ser positivo.")
            if self.modo_baranda_pp not in ("ninguna", "completa", "repartida"):
                raise ValueError("El reparto de baranda debe ser «ninguna», «completa» "
                                 "o «repartida».")
            if self.modo_baranda_pp == "repartida" and self.n_vigas <= 0:
                raise ValueError("Para repartir la baranda entre las vigas, el número de "
                                 "vigas debe ser mayor que cero.")
            if self.desglose_peso_propio_por_viga()["total"] <= 0:
                raise ValueError("El peso propio por viga dio cero: revise ancho tributario, "
                                 "peso de la viga y el reparto de baranda.")
        if self.ejes_especial:
            for e in self.ejes_especial:
                if e["P"] <= 0:
                    raise ValueError("Las cargas de eje del camión especial deben ser positivas.")
            offs = [e["offset"] for e in self.ejes_especial]
            if max(offs) - min(offs) <= 0 and len(offs) > 1:
                raise ValueError("Los ejes del camión especial no pueden estar todos en la "
                                 "misma posición: revise las separaciones.")
        if self.sep_variable and len(self.ejes_camion) >= 2:
            if self.sep_min <= 0:
                raise ValueError("La separación mínima entre ejes traseros debe ser positiva.")
            if self.sep_max < self.sep_min:
                raise ValueError("La separación máxima entre ejes traseros no puede ser "
                                 "menor que la mínima.")
            if self.sep_paso <= 0:
                raise ValueError("El paso de la separación variable debe ser positivo.")

    def ejes_especial_normalizados(self) -> List[Dict[str, float]]:
        """Ejes del camión especial ordenados y referidos al eje delantero (offset 0)."""
        return normalizar_ejes(self.ejes_especial)

    def desglose_peso_propio(self) -> Dict[str, float]:
        """Peso propio estimado (Tnf/m), descompuesto por partida.

        losa y carpeta se dan como espesor x densidad x ancho del tablero
        (conversión de una carga por m² a una carga por metro lineal de
        puente); barandas se ingresa directo en Tnf/m; vigas es el peso de
        una viga multiplicado por el número de vigas.
        """
        losa = self.espesor_losa * self.densidad_hormigon * self.ancho_tablero
        carpeta = self.espesor_carpeta * self.densidad_asfalto * self.ancho_tablero
        vigas = self.peso_viga * self.n_vigas
        return {"losa": losa, "carpeta": carpeta, "barandas": self.peso_barandas,
               "vigas": vigas, "total": losa + carpeta + self.peso_barandas + vigas}

    def peso_propio_total(self) -> float:
        """Peso propio estimado, en Tnf/m."""
        return self.desglose_peso_propio()["total"]

    def desglose_peso_propio_por_viga(self) -> Dict[str, float]:
        """Peso propio de UNA viga (Tnf/m), descompuesto por partida.

        losa y carpeta se reparten por `ancho_tributario` (no por el ancho
        completo del tablero, a diferencia de `desglose_peso_propio`); la
        viga se toma tal cual (`peso_viga` ya es de una sola viga); la
        baranda se reparte según `modo_baranda_pp` (ver su docstring).
        """
        losa = self.espesor_losa * self.densidad_hormigon * self.ancho_tributario
        carpeta = self.espesor_carpeta * self.densidad_asfalto * self.ancho_tributario
        viga = self.peso_viga
        if self.modo_baranda_pp == "completa":
            baranda = self.peso_barandas
        elif self.modo_baranda_pp == "repartida":
            baranda = self.peso_barandas / self.n_vigas if self.n_vigas > 0 else 0.0
        else:
            baranda = 0.0
        return {"viga": viga, "losa": losa, "carpeta": carpeta, "barandas": baranda,
               "total": viga + losa + carpeta + baranda}

    def configuraciones_camion(self) -> List[Tuple[float, List[Dict[str, float]]]]:
        """[(separación, ejes)] recorriendo la separación entre los dos ejes traseros.

        Los ejes se ordenan del delantero (offset 0) al trasero (offset más
        negativo); la separación variable es la que media entre los dos últimos.
        """
        ejes = sorted((dict(e) for e in self.ejes_camion), key=lambda e: -e["offset"])
        if not self.sep_variable or len(ejes) < 2:
            sep = ejes[-2]["offset"] - ejes[-1]["offset"] if len(ejes) >= 2 else 0.0
            return [(float(sep), ejes)]

        n = int(np.floor((self.sep_max - self.sep_min) / self.sep_paso + 1e-9)) + 1
        seps = [self.sep_min + k * self.sep_paso for k in range(n)]
        if seps[-1] < self.sep_max - 1e-9:
            seps.append(self.sep_max)       # asegura que el extremo superior se evalúe

        salida = []
        for s in seps:
            cfg = [dict(e) for e in ejes]
            cfg[-1]["offset"] = cfg[-2]["offset"] - float(s)
            salida.append((float(s), cfg))
        return salida


def normalizar_ejes(ejes: Sequence[Dict[str, float]]) -> List[Dict[str, float]]:
    """Ordena los ejes de adelante hacia atrás y los refiere al delantero (offset 0)."""
    if not ejes:
        return []
    limpios = sorted(({"P": float(e["P"]), "offset": float(e["offset"])} for e in ejes),
                     key=lambda e: -e["offset"])
    frente = limpios[0]["offset"]
    for e in limpios:
        e["offset"] -= frente
    return limpios


def espejar_ejes(ejes: Sequence[Dict[str, float]]) -> List[Dict[str, float]]:
    """El mismo vehículo circulando en el sentido contrario.

    Invierte el orden de los ejes (el que era trasero queda de delantero) y
    vuelve a referir los offsets a 0, conservando las separaciones reales
    entre ejes. Como en el camión de diseño las separaciones NO son
    simétricas (p.ej. HS20/HL-93: 14 ft fija entre el 1er y 2do eje, 14-30 ft
    variable entre el 2do y 3ro), la envolvente puede depender de con qué eje
    entra el vehículo al puente primero; por eso el barrido se hace en ambos
    sentidos (ver `calcular`).

    El resultado queda ordenado de adelante hacia atrás (offset 0 primero),
    igual que `normalizar_ejes`.
    """
    if not ejes:
        return []
    o_min = min(e["offset"] for e in ejes)
    espejo = [{"P": float(e["P"]), "offset": o_min - e["offset"]} for e in ejes]
    espejo.sort(key=lambda e: -e["offset"])
    return espejo


def _mismos_ejes(a: Sequence[Dict[str, float]], b: Sequence[Dict[str, float]],
                 tol: float = 1e-9) -> bool:
    """True si `a` y `b` son el mismo conjunto de (P, offset), en cualquier orden.

    Sirve para no barrer dos veces un vehículo simétrico (p.ej. el tándem, con
    dos ejes iguales): su espejo es geométricamente idéntico al original.
    """
    if len(a) != len(b):
        return False
    ka = sorted((round(e["P"], 6), round(e["offset"], 6)) for e in a)
    kb = sorted((round(e["P"], 6), round(e["offset"], 6)) for e in b)
    return all(abs(pa - pb) <= tol and abs(oa - ob) <= tol
              for (pa, oa), (pb, ob) in zip(ka, kb))


def _con_ambos_sentidos(configs: Sequence[Tuple[float, List[Dict[str, float]]]]
                        ) -> List[Tuple[float, List[Dict[str, float]]]]:
    """Duplica cada (separación, ejes) agregando el vehículo en sentido contrario.

    Cada entrada de salida es (separación, ejes, sentido), con sentido
    "normal" o "espejo": el motor conserva por separado la envolvente de
    cada sentido (ver `barrer`), porque son dos configuraciones físicas
    distintas -el vehículo entrando por cada extremo del puente- y ambas
    posiciones críticas son de interés aunque el valor extremo empate.

    Se omite el espejo cuando es geométricamente idéntico al original (p.ej.
    un tándem de dos ejes iguales a la misma separación), para no barrer dos
    veces la misma carga.
    """
    salida = []
    for sep, cfg in configs:
        salida.append((sep, cfg, "normal"))
        esp = espejar_ejes(cfg)
        if not _mismos_ejes(cfg, esp):
            salida.append((sep, esp, "espejo"))
    return salida


def ejes_desde_separaciones(cargas: Sequence[float],
                            separaciones: Sequence[float]) -> List[Dict[str, float]]:
    """Construye la lista de ejes a partir de cargas y separaciones consecutivas.

    'separaciones[i]' es la distancia entre el eje i y el i+1, de modo que
    len(separaciones) == len(cargas) - 1. Es la forma natural de describir un
    convoy: 8-12-12 con separaciones 3.5-1.3, un vano de 18 m, y así.
    """
    if len(separaciones) != max(0, len(cargas) - 1):
        raise ValueError("Se necesitan %d separaciones para %d ejes (una entre cada par)."
                         % (max(0, len(cargas) - 1), len(cargas)))
    ejes, x = [], 0.0
    for i, P in enumerate(cargas):
        ejes.append({"P": float(P), "offset": x})
        if i < len(separaciones):
            if separaciones[i] <= 0:
                raise ValueError("La separación entre el eje %d y el %d debe ser mayor que cero."
                                 % (i + 1, i + 2))
            x -= float(separaciones[i])
    return ejes


# =============================================================================
# ESTRUCTURA: TOPOLOGÍA + MOTOR MATRICIAL DE RIGIDEZ
# =============================================================================
class Estructura:
    """Viga continua con rótulas internas opcionales. GDL por nodo: [w, giro]."""

    def __init__(self, tramos: Sequence[float], rotulas: Sequence[float] = ()):
        self.tramos = [float(L) for L in tramos]
        self.n_tramos = len(self.tramos)
        self.n_sup = self.n_tramos + 1
        self.L_total = float(sum(self.tramos))
        self.apoyos_x = np.cumsum([0.0] + self.tramos)
        self.nombres_apoyos = [_nombre_apoyo(i) for i in range(self.n_sup)]
        self.avisos: List[str] = []

        self.rotulas = self._depurar_rotulas(rotulas)
        self._construir_topologia()

        self.gh = (self.n_sup - 2) - len(self.rotulas)
        if self.gh < 0:
            raise ValueError(
                "Hay %d rótulas para %d apoyos: la estructura es un mecanismo (GH = %d). "
                "El máximo admisible es %d rótulas."
                % (len(self.rotulas), self.n_sup, self.gh, self.n_sup - 2))

        self._ensamblar_rigidez()

    # ---------------------------------------------------------------- topología
    def _depurar_rotulas(self, rotulas) -> List[float]:
        limpias: List[float] = []
        for r in rotulas:
            r = float(r)
            if r <= TOL_NODO or r >= self.L_total - TOL_NODO:
                self.avisos.append("Rótula en x = %.3f m ignorada: está en un extremo, "
                                   "donde el momento ya es nulo." % r)
                continue
            if any(abs(r - q) <= TOL_NODO for q in limpias):
                self.avisos.append("Rótula duplicada en x = %.3f m ignorada." % r)
                continue
            limpias.append(r)
        return sorted(limpias)

    def _construir_topologia(self) -> None:
        pos = sorted(list(self.apoyos_x) + self.rotulas)
        nodos: List[float] = []
        for p in pos:
            if not nodos or abs(p - nodos[-1]) > TOL_NODO:
                nodos.append(p)
        self.nodos_x = np.array(nodos)
        self.n_nodos = len(self.nodos_x)
        self.n_barras = self.n_nodos - 1
        self.long_barra = np.diff(self.nodos_x)

        self.es_apoyo = np.array([bool(np.any(np.abs(self.apoyos_x - p) <= TOL_NODO))
                                  for p in self.nodos_x])
        self.es_rotula = np.array([any(abs(r - p) <= TOL_NODO for r in self.rotulas)
                                   for p in self.nodos_x])
        self.idx_apoyo = [int(np.argmin(np.abs(self.nodos_x - a))) for a in self.apoyos_x]

        # En cada nodo con rótula se libera el giro del extremo DERECHO de la barra
        # anterior; por equilibrio del nodo el momento de la barra siguiente también
        # resulta nulo, de modo que basta una liberación por rótula.
        self.lib_der = np.array([bool(self.es_rotula[m + 1]) for m in range(self.n_barras)])

    # ---------------------------------------------------------------- rigidez
    @staticmethod
    def _k_barra(L: float) -> np.ndarray:
        L2, L3 = L * L, L * L * L
        return np.array([
            [12.0 / L3,  6.0 / L2, -12.0 / L3,  6.0 / L2],
            [6.0 / L2,   4.0 / L,   -6.0 / L2,  2.0 / L],
            [-12.0 / L3, -6.0 / L2, 12.0 / L3, -6.0 / L2],
            [6.0 / L2,   2.0 / L,   -6.0 / L2,  4.0 / L],
        ])

    @staticmethod
    def _n_hermite(L: float, x: float) -> np.ndarray:
        s = x / L
        return np.array([1 - 3 * s ** 2 + 2 * s ** 3,
                         L * (s - 2 * s ** 2 + s ** 3),
                         3 * s ** 2 - 2 * s ** 3,
                         L * (-s ** 2 + s ** 3)])

    def _ensamblar_rigidez(self) -> None:
        nd = 2 * self.n_nodos
        K = np.zeros((nd, nd))
        self.cond_barra: List[Optional[np.ndarray]] = []
        for m in range(self.n_barras):
            k = self._k_barra(self.long_barra[m])
            if self.lib_der[m]:
                r, s = 3, [0, 1, 2]
                coef = k[s, r] / k[r, r]           # condensación estática del giro liberado
                k_c = np.zeros((4, 4))
                k_c[np.ix_(s, s)] = k[np.ix_(s, s)] - np.outer(coef, k[r, s])
                self.cond_barra.append(coef)
                k = k_c
            else:
                self.cond_barra.append(None)
            g = [2 * m, 2 * m + 1, 2 * m + 2, 2 * m + 3]
            K[np.ix_(g, g)] += k

        self.K = K
        self.gdl_libres = [i for i in range(nd)
                           if not (i % 2 == 0 and self.es_apoyo[i // 2])]
        self.K_ff = K[np.ix_(self.gdl_libres, self.gdl_libres)]
        if np.linalg.cond(self.K_ff) > 1e12:
            raise ValueError(
                "Estructura inestable: revise la ubicación de las rótulas "
                "(por ejemplo, dos rótulas dentro de un mismo tramo sin apoyo intermedio).")

    # ---------------------------------------------------------------- solución
    def _barra_de(self, xp: float) -> int:
        m = int(np.searchsorted(self.nodos_x, xp - TOL_NODO, side="right")) - 1
        return min(max(m, 0), self.n_barras - 1)

    def _cargas_nodales(self, cargas_puntuales, cargas_uniformes) -> np.ndarray:
        F = np.zeros(2 * self.n_nodos)
        # Se agrupan las cargas por barra una sola vez: con decenas de ejes,
        # recorrer todas las cargas dentro de cada barra se vuelve caro.
        por_barra: Dict[int, list] = {}
        for P, xp in cargas_puntuales:
            por_barra.setdefault(self._barra_de(xp), []).append((P, xp))

        # Sin cargas uniformes, sólo las barras con carga puntual aportan algo.
        barras = range(self.n_barras) if cargas_uniformes else sorted(por_barra)
        for m in barras:
            L, x1, x2 = self.long_barra[m], self.nodos_x[m], self.nodos_x[m + 1]
            p = np.zeros(4)
            for P, xp in por_barra.get(m, ()):
                p += -P * self._n_hermite(L, xp - x1)
            for xa, xb, w in cargas_uniformes:
                a, b = max(xa, x1), min(xb, x2)
                if b - a > TOL_NODO:
                    for g in _GAUSS:
                        xg = 0.5 * (a + b) + 0.5 * (b - a) * g
                        p += -w * self._n_hermite(L, xg - x1) * 0.5 * (b - a)
            if self.cond_barra[m] is not None:
                p[[0, 1, 2]] -= self.cond_barra[m] * p[3]
                p[3] = 0.0
            g = [2 * m, 2 * m + 1, 2 * m + 2, 2 * m + 3]
            F[g] += p
        return F

    def reacciones(self, cargas_puntuales=(), cargas_uniformes=()) -> np.ndarray:
        """Reacciones verticales en los apoyos (+ hacia arriba), en Tnf."""
        F = self._cargas_nodales(cargas_puntuales, cargas_uniformes)
        d = np.zeros(2 * self.n_nodos)
        d[self.gdl_libres] = np.linalg.solve(self.K_ff, F[self.gdl_libres])
        reac = self.K @ d - F
        return np.array([reac[2 * self.idx_apoyo[i]] for i in range(self.n_sup)])

    # ------------------------------------------------- diagramas V(x) y M(x)
    # Con las reacciones conocidas, V y M salen de la estática del cuerpo libre
    # izquierdo: válido con o sin rótulas.
    def fuerzas_vehiculo(self, X: np.ndarray, xt: float, ejes) -> Tuple[np.ndarray, np.ndarray, list, np.ndarray]:
        cargas = [(e["P"], xt + e["offset"]) for e in ejes
                  if 0 <= xt + e["offset"] <= self.L_total]
        R = self.reacciones(cargas_puntuales=cargas)

        V, M = self._diagramas_de_reacciones(X, R)
        if cargas:
            # Vectorizado sobre los ejes: un convoy puede traer decenas de ellos
            Ps = np.array([c[0] for c in cargas])[:, None]
            xs = np.array([c[1] for c in cargas])[:, None]
            V -= (Ps * (X[None, :] >= xs - 1e-7)).sum(axis=0)
            M -= (Ps * np.maximum(0.0, X[None, :] - xs)).sum(axis=0)
        M[0] = M[-1] = 0
        return V, M, cargas, R

    def _diagramas_de_reacciones(self, X: np.ndarray, R: np.ndarray):
        """Aporte de las reacciones a V(x) y M(x), por el cuerpo libre izquierdo.

        En cada apoyo se toma el valor inmediatamente a la derecha. El apoyo
        extremo derecho se excluye a propósito: ningún corte lo deja a su
        izquierda, y así en x = L el cortante es el de diseño (-R_última) y no
        el cero de "fuera de la viga".
        """
        escalones, rampas = self._bases_reacciones(X)
        V = np.zeros_like(X)
        M = np.zeros_like(X)
        for k in range(self.n_sup - 1):
            V += R[k] * escalones[k]
            M += R[k] * rampas[k]
        M += R[-1] * rampas[-1]
        return V, M

    def _bases_reacciones(self, X: np.ndarray):
        """Escalones y rampas por apoyo, cacheados por identidad de la grilla X.

        X y la geometría no cambian durante un cálculo; recalcularlos en cada
        posición del vehículo era el costo dominante.
        """
        cache = getattr(self, "_cache_bases", None)
        if cache is not None and cache[0] is X:
            return cache[1], cache[2]
        escalones = [X >= self.apoyos_x[k] - 1e-7 for k in range(self.n_sup - 1)]
        rampas = [np.maximum(0, X - self.apoyos_x[k]) for k in range(self.n_sup)]
        self._cache_bases = (X, escalones, rampas)
        return escalones, rampas

    def fuerzas_carril(self, X: np.ndarray, tramos_cargados, w: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        cargas_u = [(self.apoyos_x[k], self.apoyos_x[k + 1], w)
                    for k in range(self.n_tramos) if tramos_cargados[k]]
        R = self.reacciones(cargas_uniformes=cargas_u)

        V, M = self._diagramas_de_reacciones(X, R)

        for k in range(self.n_tramos):
            if tramos_cargados[k]:
                st, ed = self.apoyos_x[k], self.apoyos_x[k + 1]
                dentro = (X > st) & (X <= ed)
                despues = (X > ed)
                V[dentro] -= w * (X[dentro] - st)
                V[despues] -= w * (ed - st)
                M[dentro] -= w * (X[dentro] - st) ** 2 / 2.0
                M[despues] -= w * (ed - st) * (X[despues] - (st + (ed - st) / 2.0))
        M[0] = M[-1] = 0
        return V, M, R

    # ---------------------------------------------------------------- info
    def descripcion(self) -> str:
        lin = []
        lin.append("Tramos          : %d  (%s)   L total = %g m"
                   % (self.n_tramos, ", ".join("%g m" % L for L in self.tramos), self.L_total))
        lin.append("Apoyos          : %d  en x = %s m"
                   % (self.n_sup, ", ".join("%g" % a for a in self.apoyos_x)))
        if self.rotulas:
            lin.append("Rótulas         : %d  en x = %s m"
                       % (len(self.rotulas), ", ".join("%g" % r for r in self.rotulas)))
        else:
            lin.append("Rótulas         : ninguna (viga continua)")
        tipo = ("isostática - tipo Gerber" if self.gh == 0 else
                ("hiperestática" if self.gh > 0 else "MECANISMO"))
        lin.append("Grado hiperest. : GH = %d  (%s)" % (self.gh, tipo))
        return "\n".join(lin)


def _nombre_apoyo(i: int) -> str:
    """A, B, ... Z, AA, AB, ... para cualquier número de apoyos."""
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# =============================================================================
# ENVOLVENTES
# =============================================================================
@dataclass
class Envolvente:
    """Envolvente punto a punto y resumen de un estado de carga."""
    titulo: str
    V_max: np.ndarray
    V_min: np.ndarray
    M_max: np.ndarray
    M_min: np.ndarray
    R_pos: Dict[int, float]
    R_neg: Dict[int, float]
    resumen: Dict[str, Dict[int, float]] = field(default_factory=dict)


@dataclass
class Resultados:
    parametros: Parametros
    estructura: Estructura
    X: np.ndarray
    envolventes: Dict[str, Envolvente]
    casos_carril: List[dict]
    frames_camion: np.ndarray
    frames_tandem: np.ndarray
    configs_camion: List[Tuple[float, List[Dict[str, float]]]] = field(default_factory=list)
    sep_camion_M_max: Optional[np.ndarray] = None
    sep_camion_M_min: Optional[np.ndarray] = None
    frames_especial: Optional[np.ndarray] = None
    ejes_especial: List[Dict[str, float]] = field(default_factory=list)
    impacto_tramos: List[float] = field(default_factory=list)
    impacto_apoyos: Dict[int, float] = field(default_factory=dict)
    dist_g: Optional[float] = None
    dist_D: Optional[float] = None
    dist_S_ft: Optional[float] = None
    dist_aviso: Optional[str] = None
    # Envolvente de M por sentido de circulación ("normal"/"espejo"), por
    # envolvente que involucra vehículos (camion/tandem/especial/cvt/
    # combinada): permite reportar en el gráfico la posición crítica propia
    # de cada sentido, aunque su valor empate con el del otro (ver `barrer`
    # en `calcular`). Envolventes sin vehículo (carril, peso_propio,
    # viga_interior) no tienen entrada aquí.
    direccion_M: Dict[str, Dict[str, np.ndarray]] = field(default_factory=dict)
    # Datos CRUDOS de la carga viva (sin impacto, sin mayoración, sin g), para
    # que la vista pueda prender/apagar cada factor sin recalcular el barrido:
    # clave -> (título, V_max, V_min, M_max, M_min, R_pos, R_neg). `base_cvt`
    # es el vehículo gobernante aunque haya uno solo (en ese caso "cvt" no
    # aparece en `envolventes`, pero la combinada igual lo necesita).
    base_viva: Dict[str, tuple] = field(default_factory=dict)
    base_cvt: Optional[tuple] = None
    fd_x: Optional[np.ndarray] = None
    titulo_cvt: str = ""
    imp_txt: str = ""
    # Qué factores tiene aplicados esta vista (None = los de `calcular`).
    factores: Optional[Dict[str, bool]] = None

    def texto_carga_diseno(self) -> str:
        """Descripción de la carga de diseño usada, para el reporte."""
        par, est = self.parametros, self.estructura
        out = ["=" * 56, "      CARGA DE DISEÑO: %s" % par.norma, "=" * 56]
        out.append("Camión (%s): %s" % (par.nombre_camion, ", ".join(
            "%.3f Tnf" % e["P"] for e in par.ejes_camion)))
        if par.sep_variable:
            out.append("   separaciones: %.4f m fija; %.4f a %.4f m variable"
                       % (-par.ejes_camion[1]["offset"], par.sep_min, par.sep_max))
        if par.usar_tandem:
            out.append("%s: %s a %.4f m" % (
                par.nombre_tandem, ", ".join("%.3f Tnf" % e["P"] for e in par.ejes_tandem),
                -par.ejes_tandem[-1]["offset"]))
        if self.factores is not None:
            _on = lambda b: "SÍ" if b else "NO"
            out.append("Factores aplicados a la carga viva en estos resultados:  "
                       "impacto = %s   |   coef. distribución g = %s   |   mayoración = %s"
                       % (_on(self.factores["impacto"]), _on(self.factores["distribucion"]),
                          _on(self.factores["mayoracion"])))
        _vehs = ["camión"]
        if par.usar_tandem:
            _vehs.append(par.nombre_tandem.lower())
        if par.incluir_especial:
            _vehs.append("camión especial")
        out.append("   cada vehículo (%s) se barre en los DOS sentidos de circulación "
                   "(ida y espejo); gobierna el más desfavorable de cada sección."
                   % ", ".join(_vehs))
        out.append("Faja: %.4f Tnf/m" % par.w_carril)
        if max(par.p_conc_momento, par.p_conc_corte) > 0:
            out.append("   carga concentrada: %.3f Tnf para momento, %.3f Tnf para corte"
                       % (par.p_conc_momento, par.p_conc_corte))
            out.append("   %s concentrada(s) en los patrones de momento negativo"
                       % ("dos" if par.dos_conc_momento_negativo else "una"))
        out.append("Combinación: %s" % (
            "vehículo y faja son ALTERNATIVOS, gobierna el mayor (AASHTO Standard)"
            if par.modo_combinacion == "alternativa"
            else "vehículo + carril, sumados (AASHTO LRFD)"))
        if par.impacto_modo == "fijo":
            out.append("Impacto: 1 + IM = %g (fijo)" % par.factor_dinamico)
        else:
            out.append("Impacto: I = 50/(L+125) <= 0.30, con L en pies")
            for k, I in enumerate(self.impacto_tramos):
                out.append("   tramo %s-%s (L = %g m = %.1f ft):  I = %.4f   ->  1+I = %.4f"
                           % (est.nombres_apoyos[k], est.nombres_apoyos[k + 1],
                              est.tramos[k], est.tramos[k] / PIE, I - 1.0, I))
            for i in range(1, est.n_tramos):
                out.append("   apoyo %s (L promedio = %g m):        1+I = %.4f"
                           % (est.nombres_apoyos[i],
                              0.5 * (est.tramos[i - 1] + est.tramos[i]),
                              self.impacto_apoyos.get(i, float("nan"))))
        if par.factor_mayoracion != 1.0:
            out.append("Mayoración global de la carga viva: x %g" % par.factor_mayoracion)
        if par.incluir_peso_propio:
            d = par.desglose_peso_propio()
            out.append("")
            out.append("Peso propio (carga permanente, uniforme en toda la estructura):")
            out.append("   losa      : %5.3f m x %.2f Tnf/m3 x %.2f m ancho = %.3f Tnf/m"
                       % (par.espesor_losa, par.densidad_hormigon, par.ancho_tablero, d["losa"]))
            out.append("   carpeta   : %5.3f m x %.2f Tnf/m3 x %.2f m ancho = %.3f Tnf/m"
                       % (par.espesor_carpeta, par.densidad_asfalto, par.ancho_tablero,
                          d["carpeta"]))
            out.append("   barandas  : %.3f Tnf/m" % d["barandas"])
            out.append("   vigas     : %g vigas x %.3f Tnf/m = %.3f Tnf/m"
                       % (par.n_vigas, par.peso_viga, d["vigas"]))
            out.append("   TOTAL     : %.3f Tnf/m  (no lleva mayoración de carga viva ni impacto)"
                       % d["total"])
        if par.incluir_pp_por_viga:
            dpv = par.desglose_peso_propio_por_viga()
            out.append("")
            out.append("Peso propio POR VIGA (ancho tributario = %.3f m):" % par.ancho_tributario)
            out.append("   viga      : %.3f Tnf/m  (peso_viga, tal cual)" % dpv["viga"])
            out.append("   losa      : %5.3f m x %.2f Tnf/m3 x %.2f m trib. = %.3f Tnf/m"
                       % (par.espesor_losa, par.densidad_hormigon, par.ancho_tributario,
                          dpv["losa"]))
            out.append("   carpeta   : %5.3f m x %.2f Tnf/m3 x %.2f m trib. = %.3f Tnf/m"
                       % (par.espesor_carpeta, par.densidad_asfalto, par.ancho_tributario,
                          dpv["carpeta"]))
            _modo_txt = {"ninguna": "no incluida (viga interior)",
                        "completa": "100% a esta viga (viga de borde)",
                        "repartida": "repartida entre %g vigas" % par.n_vigas}[par.modo_baranda_pp]
            out.append("   barandas  : %.3f Tnf/m  (%s)" % (dpv["barandas"], _modo_txt))
            out.append("   TOTAL     : %.3f Tnf/m  (no lleva mayoración de carga viva ni impacto)"
                       % dpv["total"])
        if par.incluir_distribucion:
            out.append("")
            out.append("Distribución transversal de momento (AASHTO Standard, Tabla 3.23.1, "
                       "método S/D):")
            out.append("   viga de %s, S = %.3f m (%.2f ft), %s"
                       % ("hormigón armado" if par.tipo_viga == "hormigon" else "acero",
                          par.separacion_vigas, self.dist_S_ft or 0.0,
                          "1 vía de diseño" if par.n_vias_diseno == 1 else "2 o más vías de diseño"))
            out.append("   g = S/D = %.2f/%.1f = %.4f  ->  M_viga = g x M_vía completa"
                       % (self.dist_S_ft or 0.0, self.dist_D or 0.0, self.dist_g or 0.0))
            if self.dist_aviso:
                out.append("   AVISO: " + self.dist_aviso)
            out.append("   Corte y reacciones se mantienen por vía completa (esta tabla no da "
                       "un factor para ellos).")
            out.append("   El peso propio no lleva este coeficiente: su reparto por viga es "
                       "por ancho tributario, un método distinto.")
        out.append("")
        return "\n".join(out)

    @property
    def orden(self) -> List[str]:
        """Claves de las envolventes presentes, en orden de presentación."""
        posibles = ["camion", "tandem", "especial", "cvt", "carril", "peso_propio",
                   "combinada", "viga_interior"]
        return [k for k in posibles if k in self.envolventes]

    def con_factores(self, impacto: bool = True, distribucion: bool = True,
                     mayoracion: bool = True) -> "Resultados":
        """Vista de las envolventes de carga viva con cada factor prendido o
        apagado, SIN recalcular el barrido: usa los datos crudos que dejó
        `calcular`.

        - `impacto`: 1+I (o 1.33) sólo en la combinada, como siempre.
        - `mayoracion`: `factor_mayoracion` sobre toda la carga viva.
        - `distribucion`: g = S/D sobre el MOMENTO de toda la carga viva (el
          corte y las reacciones quedan por vía completa). Sólo existe si el
          cálculo se hizo con `incluir_distribucion`; si no, se ignora.
        En esta vista no aparece la envolvente aparte "viga_interior": su
        lugar lo toma el factor g. El peso propio no lleva ninguno.
        Con los tres prendidos, las envolventes de carga viva coinciden bit a
        bit con las de `calcular` (y "combinada" con g, con "viga_interior").
        """
        if not self.base_viva or self.base_cvt is None:
            return self
        par, est, X = self.parametros, self.estructura, self.X
        fm = par.factor_mayoracion if mayoracion else 1.0
        fdx = self.fd_x if impacto else np.ones_like(X)
        fdR = (self.impacto_apoyos if impacto
               else {i: 1.0 for i in range(est.n_sup)})
        g = self.dist_g if (distribucion and self.dist_g) else None
        n = est.n_sup

        _, Vcx, Vcn, Mcx, Mcn, Rpc, Rnc = self.base_cvt
        _, Vkx, Vkn, Mkx, Mkn, Rpk, Rnk = self.base_viva["carril"]
        if par.modo_combinacion == "alternativa":
            Vx = np.round(fdx * np.maximum(Vcx, Vkx), 3)
            Vn = np.round(fdx * np.minimum(Vcn, Vkn), 3)
            Mx = np.round(fdx * np.maximum(Mcx, Mkx), 3)
            Mn = np.round(fdx * np.minimum(Mcn, Mkn), 3)
            Rp = {i: fdR[i] * max(Rpc[i], Rpk[i]) for i in range(n)}
            Rn = {i: fdR[i] * min(Rnc[i], Rnk[i]) for i in range(n)}
            tit = ("%s x máx(%s ; Faja)" % (self.imp_txt, self.titulo_cvt) if impacto
                   else "máx(%s ; Faja)" % self.titulo_cvt)
        else:
            Vx = np.round(fdx * Vcx + Vkx, 3)
            Vn = np.round(fdx * Vcn + Vkn, 3)
            Mx = np.round(fdx * Mcx + Mkx, 3)
            Mn = np.round(fdx * Mcn + Mkn, 3)
            Rp = {i: fdR[i] * Rpc[i] + Rpk[i] for i in range(n)}
            Rn = {i: fdR[i] * Rnc[i] + Rnk[i] for i in range(n)}
            tit = ("%s(%s) + Carril" % (self.imp_txt, self.titulo_cvt) if impacto
                   else "(%s) + Carril" % self.titulo_cvt)

        vivas = dict(self.base_viva)
        vivas["combinada"] = (tit, Vx, Vn, Mx, Mn, Rp, Rn)
        envs = {}
        for clave, (titulo, Vx, Vn, Mx, Mn, Rp, Rn) in vivas.items():
            Vx, Vn, Mx, Mn = (np.round(fm * a, 3) for a in (Vx, Vn, Mx, Mn))
            Rp = {i: fm * v for i, v in dict(Rp).items()}
            Rn = {i: fm * v for i, v in dict(Rn).items()}
            if g is not None:
                Mx, Mn = np.round(Mx * g, 3), np.round(Mn * g, 3)
            envs[clave] = Envolvente(titulo, Vx, Vn, Mx, Mn, Rp, Rn,
                                     _resumen(est, X, Vx, Vn, Mx, Mn))
        for k, v in self.envolventes.items():     # peso propio y sus partidas
            if k not in vivas and k != "viga_interior":
                envs[k] = v
        return replace(self, envolventes=envs,
                       factores={"impacto": impacto, "distribucion": g is not None,
                                 "mayoracion": mayoracion})

    @property
    def componentes_pp_viga(self) -> List[str]:
        """Claves del desglose de peso propio por viga presentes: las cuatro
        partidas, dos combinaciones útiles y el total, en orden de
        presentación."""
        posibles = ["pp_viga", "pp_losa", "pp_carpeta", "pp_barandas",
                   "pp_losa_viga", "pp_losa_viga_barandas", "pp_total"]
        return [k for k in posibles if k in self.envolventes]

    @property
    def separaciones(self) -> List[float]:
        return [s for s, _ in self.configs_camion]

    def separaciones_criticas(self) -> List[Tuple[str, float, float, float]]:
        """[(ubicación, x, sep. que gobierna M+, sep. que gobierna M-)] en las
        secciones de control: centros de tramo y apoyos interiores."""
        if self.sep_camion_M_max is None or len(self.configs_camion) < 2:
            return []
        est = self.estructura
        puntos = []
        for i in range(est.n_tramos):
            xc = est.apoyos_x[i] + est.tramos[i] / 2.0
            puntos.append(("Centro tramo %s-%s" % (est.nombres_apoyos[i],
                                                   est.nombres_apoyos[i + 1]), xc))
        for i in range(1, est.n_tramos):
            puntos.append(("Apoyo %s" % est.nombres_apoyos[i], float(est.apoyos_x[i])))

        filas = []
        for nombre, x in puntos:
            j = int(np.argmin(np.abs(self.X - x)))
            filas.append((nombre, float(self.X[j]),
                          float(self.sep_camion_M_max[j]), float(self.sep_camion_M_min[j])))
        return filas

    def tabla_puntos(self):
        """DataFrame de 30 columnas con la discretización punto a punto."""
        import pandas as pd
        u_v, u_m = "Tnf", "Tnf·m"
        cols, datos = [], []
        for diag, unidad in (("DFC", u_v), ("DMF", u_m)):
            for clave in self.orden + self.componentes_pp_viga:
                env = self.envolventes[clave]
                cols += ["%s Via (%s) - Posición (m)" % (diag, env.titulo),
                         "%s Via (%s) - Máx (+) (%s)" % (diag, env.titulo, unidad),
                         "%s Via (%s) - Mín (-) (%s)" % (diag, env.titulo, unidad)]
                if diag == "DFC":
                    datos += [self.X, env.V_max, env.V_min]
                else:
                    datos += [self.X, env.M_max, env.M_min]
        return pd.DataFrame(np.column_stack(datos), columns=cols)

    def exportar_excel(self, ruta: str) -> str:
        self.tabla_puntos().to_excel(ruta, index=False)
        return ruta

    def texto_resumen(self) -> str:
        """Reporte tabular de todas las envolventes, listo para consola o portapapeles."""
        e = self.estructura
        out = [e.descripcion(), "", self.texto_carga_diseno()]
        for clave in self.orden + self.componentes_pp_viga:
            env = self.envolventes[clave]
            r = env.resumen
            out.append("=" * 56)
            out.append("      RESUMEN DE ENVOLVENTES (%s)" % env.titulo)
            out.append("=" * 56)
            for i in range(e.n_sup):
                out.append("R_%s_max : (+) %6.2f Tnf  |  (-) %6.2f Tnf"
                           % (e.nombres_apoyos[i], env.R_pos[i], env.R_neg[i]))
            out.append("-" * 56)
            for i in range(e.n_tramos):
                out.append("M_%s%s_max : (+) %6.2f Tnf·m  en x=%6.2f m  |  (-) %6.2f Tnf·m  en x=%6.2f m"
                           % (e.nombres_apoyos[i], e.nombres_apoyos[i + 1],
                              r["M_tram_pos"][i], r["M_tram_pos_x"][i],
                              r["M_tram_neg"][i], r["M_tram_neg_x"][i]))
            for i in range(1, e.n_tramos):
                out.append("M_%s_max  : (+) %6.2f Tnf·m  en x=%6.2f m  |  (-) %6.2f Tnf·m  en x=%6.2f m"
                           % (e.nombres_apoyos[i],
                              r["M_apoy_pos"][i], r["M_apoy_pos_x"][i],
                              r["M_apoy_neg"][i], r["M_apoy_neg_x"][i]))
            out.append("-" * 56)
            for i in range(e.n_tramos):
                out.append("V_%s%s_max :     %6.2f Tnf (Absoluto)  en x=%6.2f m"
                           % (e.nombres_apoyos[i], e.nombres_apoyos[i + 1],
                              r["V_max"][i], r["V_max_x"][i]))
            out.append("")

        criticas = self.separaciones_criticas()
        if criticas:
            seps = self.separaciones
            out.append("=" * 56)
            out.append("      SEPARACIÓN CRÍTICA ENTRE EJES TRASEROS")
            out.append("=" * 56)
            out.append("Barrido de %.2f a %.2f m en %d posiciones (AASHTO 3.6.1.2.2)."
                       % (seps[0], seps[-1], len(seps)))
            out.append("%-24s %8s %12s %12s" % ("Sección", "x (m)", "para M(+)", "para M(-)"))
            for nombre, x, s_pos, s_neg in criticas:
                out.append("%-24s %8.2f %10.2f m %10.2f m" % (nombre, x, s_pos, s_neg))
            out.append("")
        return "\n".join(out)

    def valores_en(self, x: float, clave: str) -> Dict[str, float]:
        """Interpola V y M (máx y mín) de una envolvente en la abscisa x."""
        env = self.envolventes[clave]
        x = float(np.clip(x, self.X[0], self.X[-1]))
        return {"x": x,
                "V_max": float(np.interp(x, self.X, env.V_max)),
                "V_min": float(np.interp(x, self.X, env.V_min)),
                "M_max": float(np.interp(x, self.X, env.M_max)),
                "M_min": float(np.interp(x, self.X, env.M_min))}


def _resumen(est: Estructura, X, V_max, V_min, M_max, M_min) -> Dict[str, Dict[int, float]]:
    """Valores extremos por tramo/apoyo, cada uno junto a la abscisa x (m) donde ocurre."""
    r = {"M_tram_pos": {}, "M_tram_pos_x": {}, "M_tram_neg": {}, "M_tram_neg_x": {},
        "M_apoy_pos": {}, "M_apoy_pos_x": {}, "M_apoy_neg": {}, "M_apoy_neg_x": {},
        "V_max": {}, "V_max_x": {}}
    for i in range(est.n_tramos):
        idx_mask = np.nonzero((X >= est.apoyos_x[i]) & (X <= est.apoyos_x[i + 1]))[0]
        if len(idx_mask):
            j = idx_mask[int(np.argmax(M_max[idx_mask]))]
            r["M_tram_pos"][i] = float(M_max[j]); r["M_tram_pos_x"][i] = float(X[j])

            jx = idx_mask[int(np.argmax(np.abs(V_max[idx_mask])))]
            jn = idx_mask[int(np.argmax(np.abs(V_min[idx_mask])))]
            if abs(V_max[jx]) >= abs(V_min[jn]):
                r["V_max"][i], r["V_max_x"][i] = float(abs(V_max[jx])), float(X[jx])
            else:
                r["V_max"][i], r["V_max_x"][i] = float(abs(V_min[jn])), float(X[jn])
        else:
            r["M_tram_pos"][i], r["M_tram_pos_x"][i] = 0.0, float(est.apoyos_x[i])
            r["V_max"][i], r["V_max_x"][i] = 0.0, float(est.apoyos_x[i])
        idx_c = int(np.argmin(np.abs(X - (est.apoyos_x[i] + est.tramos[i] / 2.0))))
        r["M_tram_neg"][i] = float(M_min[idx_c]); r["M_tram_neg_x"][i] = float(X[idx_c])
    for i in range(1, est.n_tramos):
        idx = int(np.argmin(np.abs(X - est.apoyos_x[i])))
        r["M_apoy_pos"][i] = float(M_max[idx]); r["M_apoy_pos_x"][i] = float(X[idx])
        r["M_apoy_neg"][i] = float(M_min[idx]); r["M_apoy_neg_x"][i] = float(X[idx])
    return r


def casos_de_carril(est: Estructura) -> List[dict]:
    """Patrones de carga de faja: vanos alternos y pares de vanos adyacentes."""
    n = est.n_tramos
    casos = [{"tramos": [(i % 2 == 0) for i in range(n)],
              "titulo": "Momento (+) Máximo - Tramos Impares", "negativo": False}]
    if n > 1:
        casos.append({"tramos": [(i % 2 != 0) for i in range(n)],
                      "titulo": "Momento (+) Máximo - Tramos Pares", "negativo": False})
    for i in range(1, n):
        t = [False] * n
        t[i - 1] = True
        t[i] = True
        casos.append({"tramos": t, "negativo": True,
                      "titulo": "Momento (-) Máximo - Apoyo %s" % est.nombres_apoyos[i]})
    return casos


def _envolventes_unitarias(est: Estructura, X: np.ndarray, dx: float) -> List[dict]:
    """Envolvente punto a punto de una carga unitaria recorriendo cada tramo.

    Sirve para la carga concentrada de la faja HS20-44: como el problema es
    lineal, la contribución de una concentrada de P Tnf ubicada donde más daña
    es P veces esta envolvente.
    """
    unitario = [{"P": 1.0, "offset": 0.0}]
    salida = []
    for k in range(est.n_tramos):
        x0, x1 = est.apoyos_x[k], est.apoyos_x[k + 1]
        pos = np.arange(x0, x1 + dx * 0.5, dx)
        # La línea de influencia del cortante salta en los apoyos: hay que
        # evaluar la carga justo al lado de cada uno para tomar el pico.
        pos = np.unique(np.clip(np.concatenate([pos, [x1, x0 + TOL_NODO, x1 - TOL_NODO]]),
                                x0, x1))
        Vx = np.full_like(X, -np.inf); Vn = np.full_like(X, np.inf)
        Mx = np.full_like(X, -np.inf); Mn = np.full_like(X, np.inf)
        Rp = np.full(est.n_sup, -np.inf); Rr = np.full(est.n_sup, np.inf)
        for xp in pos:
            V, M, _, R = est.fuerzas_vehiculo(X, float(xp), unitario)
            np.maximum(Vx, V, out=Vx); np.minimum(Vn, V, out=Vn)
            np.maximum(Mx, M, out=Mx); np.minimum(Mn, M, out=Mn)
            np.maximum(Rp, R, out=Rp); np.minimum(Rr, R, out=Rr)
        salida.append({"V_max": Vx, "V_min": Vn, "M_max": Mx, "M_min": Mn,
                       "R_pos": Rp, "R_neg": Rr})
    return salida


def _aporte_concentrada(unit, cargados, dobles, P, n_sup):
    """Contribución de la carga concentrada de la faja, en el peor lugar.

    Con `dobles` se usan dos concentradas, una en cada tramo cargado (lo que
    AASHTO Standard pide para momento negativo en vigas continuas). Como las
    posiciones son independientes, el máximo de la suma es la suma de los
    máximos sección por sección, así que no hace falta un barrido doble.
    """
    ceros = np.zeros_like(unit[0]["V_max"])
    if P <= 0 or not cargados:
        return (ceros, ceros, ceros, ceros, np.zeros(n_sup), np.zeros(n_sup))

    if dobles and len(cargados) >= 2:
        sel = cargados[:2]
        tomar_max = lambda clave: sum(unit[k][clave] for k in sel)
        tomar_min = tomar_max
    else:
        sel = cargados
        tomar_max = lambda clave: np.max([unit[k][clave] for k in sel], axis=0)
        tomar_min = lambda clave: np.min([unit[k][clave] for k in sel], axis=0)

    return (P * tomar_max("V_max"), P * tomar_min("V_min"),
            P * tomar_max("M_max"), P * tomar_min("M_min"),
            P * tomar_max("R_pos"), P * tomar_min("R_neg"))


def factores_impacto(est: Estructura, X: np.ndarray, par: "Parametros"):
    """(factor por sección, factor por apoyo) para 1 + I.

    En modo AASHTO Standard, I = 50/(L+125) <= 0.30 con L la luz del tramo que
    contiene la sección. En los apoyos interiores, donde manda el momento
    negativo, L es el promedio de los dos tramos adyacentes, como pide la norma.
    """
    if par.impacto_modo == "fijo":
        return (np.full_like(X, par.factor_dinamico),
                {i: par.factor_dinamico for i in range(est.n_sup)})

    fd_x = np.empty_like(X)
    for k in range(est.n_tramos):
        dentro = (X >= est.apoyos_x[k] - TOL_NODO) & (X <= est.apoyos_x[k + 1] + TOL_NODO)
        fd_x[dentro] = 1.0 + impacto_aashto_standard(est.tramos[k])
    for i in range(1, est.n_tramos):
        justo = np.abs(X - est.apoyos_x[i]) <= TOL_NODO
        fd_x[justo] = 1.0 + impacto_aashto_standard(
            0.5 * (est.tramos[i - 1] + est.tramos[i]))

    fd_R = {}
    for i in range(est.n_sup):
        if i == 0:
            L = est.tramos[0]
        elif i == est.n_sup - 1:
            L = est.tramos[-1]
        else:
            L = 0.5 * (est.tramos[i - 1] + est.tramos[i])
        fd_R[i] = 1.0 + impacto_aashto_standard(L)
    return fd_x, fd_R


def calcular(par: Parametros, progreso: Optional[Callable[[float, str], bool]] = None) -> Resultados:
    """Ejecuta el análisis completo.

    progreso(fraccion, texto) -> devuelva False para cancelar el cálculo.
    """
    par.validar()
    est = Estructura(par.tramos, par.rotulas)

    # Grilla uniforme + las abscisas de apoyos y rótulas: ahí están los extremos
    # de los diagramas y el momento nulo de cada rótula, y no siempre caen en un
    # múltiplo exacto de dx.
    X = np.round(np.arange(0.0, est.L_total + par.dx, par.dx), 6)
    X = np.unique(np.round(np.concatenate([X, est.nodos_x]), 6))
    X = X[(X >= -TOL_NODO) & (X <= est.L_total + TOL_NODO)]

    def avisar(frac, txt):
        if progreso is not None and progreso(frac, txt) is False:
            raise CalculoCancelado()

    # ------------------------------------------------------------- vehículos
    def barrer(configs, etiqueta, f0, f1):
        """Envolvente sobre todas las posiciones y todas las configuraciones de ejes.

        'configs' es [(separación, ejes)]: para el camión recorre la separación
        variable entre ejes traseros; para el tándem trae una sola entrada.

        Cada configuración se recorre en los dos sentidos de circulación (el
        vehículo tal cual y su espejo, ver `espejar_ejes`): con separaciones
        de eje asimétricas, la línea de influencia no es la misma si el
        vehículo entra al puente por un eje o por el otro, y la norma pide la
        envolvente más desfavorable, no una sola dirección.
        """
        configs = _con_ambos_sentidos(configs)
        largo_max = max(abs(min(e["offset"] for e in cfg)) for _, cfg, _ in configs)
        frames = np.arange(0.0, est.L_total + largo_max + 1.0, par.dx)
        R_pos = {i: 0.0 for i in range(est.n_sup)}
        R_neg = {i: 0.0 for i in range(est.n_sup)}
        V_max = np.full_like(X, -np.inf); V_min = np.full_like(X, np.inf)
        M_max = np.full_like(X, -np.inf); M_min = np.full_like(X, np.inf)
        # Separación que gobierna cada ordenada del diagrama de momentos
        sep_M_max = np.zeros_like(X); sep_M_min = np.zeros_like(X)
        # Envolvente de M por sentido de circulación, ADEMÁS de la combinada:
        # el barrido en ambos sentidos puede dar picos "gemelos" con un valor
        # casi idéntico pero en posiciones distintas (el vehículo entrando
        # por cada extremo del puente); guardar cada sentido por separado
        # permite reportar la posición crítica propia de cada uno, en vez de
        # depender de si los valores empatan o no dentro de una tolerancia.
        M_max_dir = {"normal": np.full_like(X, -np.inf), "espejo": np.full_like(X, -np.inf)}
        M_min_dir = {"normal": np.full_like(X, np.inf), "espejo": np.full_like(X, np.inf)}

        for ci, (sep, cfg, sentido) in enumerate(configs):
            # Al barrido uniforme se le suman las posiciones que dejan un eje
            # pegado a cada apoyo: ahí está el pico del cortante, y con paso dx
            # se perdería por un margen del orden de dx/L.
            criticas = [a - e["offset"] + s
                        for e in cfg for a in est.apoyos_x for s in (-TOL_NODO, TOL_NODO)]
            pos = np.unique(np.concatenate([frames, np.array(criticas)]))
            pos = pos[(pos >= 0.0) & (pos <= frames[-1])]

            paso_aviso = max(1, len(pos) // 20)
            Vx = np.full_like(X, -np.inf); Vn = np.full_like(X, np.inf)
            Mx = np.full_like(X, -np.inf); Mn = np.full_like(X, np.inf)
            for j, xt in enumerate(pos):
                V, M, _, R = est.fuerzas_vehiculo(X, xt, cfg)
                for i in range(est.n_sup):
                    R_pos[i] = max(R_pos[i], R[i])
                    R_neg[i] = min(R_neg[i], R[i])
                np.maximum(Vx, V, out=Vx); np.minimum(Vn, V, out=Vn)
                np.maximum(Mx, M, out=Mx); np.minimum(Mn, M, out=Mn)
                if j % paso_aviso == 0:
                    frac = (ci + j / len(pos)) / len(configs)
                    texto = "Barriendo %s..." % etiqueta
                    if len(configs) > 1:
                        texto = ("Barriendo %s: separación %.2f m (%d de %d)..."
                                 % (etiqueta, sep, ci + 1, len(configs)))
                    avisar(f0 + (f1 - f0) * frac, texto)

            mejora = Mx > M_max
            M_max[mejora] = Mx[mejora]; sep_M_max[mejora] = sep
            mejora = Mn < M_min
            M_min[mejora] = Mn[mejora]; sep_M_min[mejora] = sep
            np.maximum(V_max, Vx, out=V_max); np.minimum(V_min, Vn, out=V_min)
            np.maximum(M_max_dir[sentido], Mx, out=M_max_dir[sentido])
            np.minimum(M_min_dir[sentido], Mn, out=M_min_dir[sentido])

        return (frames, R_pos, R_neg,
                np.round(V_max, 3), np.round(V_min, 3),
                np.round(M_max, 3), np.round(M_min, 3),
                sep_M_max, sep_M_min,
                np.round(M_max_dir["normal"], 3), np.round(M_max_dir["espejo"], 3),
                np.round(M_min_dir["normal"], 3), np.round(M_min_dir["espejo"], 3))

    avisar(0.0, "Preparando la estructura...")
    ejes_esp = par.ejes_especial_normalizados()
    f_tan_fin = 0.72 if ejes_esp else 0.92

    configs_cam = par.configuraciones_camion()
    (fr_cam, Rp_cam, Rn_cam, Vcam_max, Vcam_min, Mcam_max, Mcam_min,
     sep_M_max, sep_M_min,
     Mcam_max_n, Mcam_max_e, Mcam_min_n, Mcam_min_e) = barrer(
        configs_cam, "camión HS-20", 0.02, 0.50)
    if par.usar_tandem:
        sep_tandem = (par.ejes_tandem[0]["offset"] - par.ejes_tandem[-1]["offset"]
                      if len(par.ejes_tandem) >= 2 else 0.0)
        (fr_tan, Rp_tan, Rn_tan, Vtan_max, Vtan_min, Mtan_max, Mtan_min,
         _, _, Mtan_max_n, Mtan_max_e, Mtan_min_n, Mtan_min_e) = barrer(
            [(sep_tandem, list(par.ejes_tandem))], par.nombre_tandem.lower(), 0.50, f_tan_fin)
    else:
        fr_tan = np.array([0.0])
        Rp_tan = {i: -np.inf for i in range(est.n_sup)}
        Rn_tan = {i: np.inf for i in range(est.n_sup)}
        Vtan_max = np.full_like(X, -np.inf); Vtan_min = np.full_like(X, np.inf)
        Mtan_max = np.full_like(X, -np.inf); Mtan_min = np.full_like(X, np.inf)
        Mtan_max_n = Mtan_max_e = np.full_like(X, -np.inf)
        Mtan_min_n = Mtan_min_e = np.full_like(X, np.inf)

    esp = None
    Mesp_max_n = Mesp_max_e = np.full_like(X, -np.inf)
    Mesp_min_n = Mesp_min_e = np.full_like(X, np.inf)
    if ejes_esp:
        (fr_esp, Rp_esp, Rn_esp, Vesp_max, Vesp_min, Mesp_max, Mesp_min,
         _, _, Mesp_max_n, Mesp_max_e, Mesp_min_n, Mesp_min_e) = barrer(
            [(0.0, ejes_esp)], "camión especial", f_tan_fin, 0.92)
        esp = (Rp_esp, Rn_esp, Vesp_max, Vesp_min, Mesp_max, Mesp_min)
    else:
        fr_esp = None

    # ------------------------------------------------------- carga de faja
    avisar(0.93, "Evaluando la carga de faja...")
    casos = casos_de_carril(est)

    # Carga concentrada móvil de la faja (HS20-44): se recorre una carga
    # unitaria por cada tramo y se guarda la envolvente punto a punto. Como la
    # respuesta es lineal, después basta escalarla por 18 kip (momento) o
    # 26 kip (corte), y sumar dos tramos cuando la norma pide dos concentradas.
    hay_conc = max(par.p_conc_momento, par.p_conc_corte) > 0
    unit = _envolventes_unitarias(est, X, par.dx) if hay_conc else None

    Rp_car = {i: 0.0 for i in range(est.n_sup)}
    Rn_car = {i: 0.0 for i in range(est.n_sup)}
    Vcar_max = np.full_like(X, -np.inf); Vcar_min = np.full_like(X, np.inf)
    Mcar_max = np.full_like(X, -np.inf); Mcar_min = np.full_like(X, np.inf)
    for caso in casos:
        V, M, R = est.fuerzas_carril(X, caso["tramos"], par.w_carril)
        Vx, Vn, Mx, Mn = V.copy(), V.copy(), M.copy(), M.copy()
        Rx = {i: R[i] for i in range(est.n_sup)}
        Rn = {i: R[i] for i in range(est.n_sup)}

        if hay_conc:
            cargados = [k for k in range(est.n_tramos) if caso["tramos"][k]]
            dobles = par.dos_conc_momento_negativo and caso.get("negativo", False)
            ap_V, an_V, mp_V, mn_V, rp_V, rn_V = _aporte_concentrada(
                unit, cargados, dobles, par.p_conc_corte, est.n_sup)
            ap_M, an_M, mp_M, mn_M, rp_M, rn_M = _aporte_concentrada(
                unit, cargados, dobles, par.p_conc_momento, est.n_sup)
            Vx += ap_V; Vn += an_V
            Mx += mp_M; Mn += mn_M
            for i in range(est.n_sup):
                Rx[i] += rp_V[i]
                Rn[i] += rn_V[i]

        for i in range(est.n_sup):
            Rp_car[i] = max(Rp_car[i], Rx[i])
            Rn_car[i] = min(Rn_car[i], Rn[i])
        np.maximum(Vcar_max, Vx, out=Vcar_max); np.minimum(Vcar_min, Vn, out=Vcar_min)
        np.maximum(Mcar_max, Mx, out=Mcar_max); np.minimum(Mcar_min, Mn, out=Mcar_min)

    # Limpieza de signos: si la envolvente no cambia de signo, el otro lado es 0
    Vcar_min[(Vcar_max > 0) & (Vcar_min > 0)] = 0.0
    Mcar_min[(Mcar_max > 0) & (Mcar_min > 0)] = 0.0
    Vcar_max[(Vcar_max < 0) & (Vcar_min < 0)] = 0.0
    Mcar_max[(Mcar_max < 0) & (Mcar_min < 0)] = 0.0
    Vcar_max = np.round(Vcar_max, 3); Vcar_min = np.round(Vcar_min, 3)
    Mcar_max = np.round(Mcar_max, 3); Mcar_min = np.round(Mcar_min, 3)

    # --------------------------------------------------------- peso propio
    # Carga permanente, uniforme sobre TODA la estructura (no se alterna por
    # tramo: a diferencia del carril, el peso propio siempre está entero).
    # Es un único estado de carga, no un barrido: V y M salen determinados,
    # así que M_max = M_min (y lo mismo para V y las reacciones).
    pp = None
    if par.incluir_peso_propio:
        avisar(0.975, "Evaluando el peso propio...")
        w_pp = par.peso_propio_total()
        Vpp, Mpp, Rpp = est.fuerzas_carril(X, [True] * est.n_tramos, w_pp)
        Vpp = np.round(Vpp, 3); Mpp = np.round(Mpp, 3)
        Rpp_dict = {i: float(Rpp[i]) for i in range(est.n_sup)}
        pp = (w_pp, Vpp, Mpp, Rpp_dict)

    # ------------------------------------------------- combinación AASHTO
    avisar(0.97, "Combinando estados...")
    # Envolvente gobernante: el máximo de los vehículos que correspondan
    Vcvt_max = Vcam_max.copy(); Vcvt_min = Vcam_min.copy()
    Mcvt_max = Mcam_max.copy(); Mcvt_min = Mcam_min.copy()
    Rp_cvt = dict(Rp_cam); Rn_cvt = dict(Rn_cam)
    nombres_veh = [par.nombre_camion]

    a_fusionar = []
    if par.usar_tandem:
        a_fusionar.append((par.nombre_tandem, Vtan_max, Vtan_min, Mtan_max, Mtan_min,
                           Rp_tan, Rn_tan))
    if esp is not None and par.incluir_especial:
        Rp_esp, Rn_esp, Vesp_max, Vesp_min, Mesp_max, Mesp_min = esp
        a_fusionar.append((par.nombre_especial, Vesp_max, Vesp_min, Mesp_max, Mesp_min,
                           Rp_esp, Rn_esp))
    for nombre, Vx, Vn, Mx, Mn, Rp, Rn in a_fusionar:
        np.maximum(Vcvt_max, Vx, out=Vcvt_max); np.minimum(Vcvt_min, Vn, out=Vcvt_min)
        np.maximum(Mcvt_max, Mx, out=Mcvt_max); np.minimum(Mcvt_min, Mn, out=Mcvt_min)
        Rp_cvt = {i: max(Rp_cvt[i], Rp[i]) for i in range(est.n_sup)}
        Rn_cvt = {i: min(Rn_cvt[i], Rn[i]) for i in range(est.n_sup)}
        nombres_veh.append(nombre)

    titulo_cvt = " o ".join(nombres_veh)
    fd_x, fd_R = factores_impacto(est, X, par)
    imp = ("%g" % par.factor_dinamico if par.impacto_modo == "fijo" else "(1+I)")

    # Mismo armado de "cvt", pero por separado para cada sentido de
    # circulación (ver `barrer`): sirve para reportar, en el gráfico, la
    # posición crítica propia de cada sentido en "cvt" y "combinada", no
    # sólo en "camion" a secas.
    Mcvt_max_n = Mcam_max_n.copy(); Mcvt_max_e = Mcam_max_e.copy()
    Mcvt_min_n = Mcam_min_n.copy(); Mcvt_min_e = Mcam_min_e.copy()
    if par.usar_tandem:
        np.maximum(Mcvt_max_n, Mtan_max_n, out=Mcvt_max_n)
        np.maximum(Mcvt_max_e, Mtan_max_e, out=Mcvt_max_e)
        np.minimum(Mcvt_min_n, Mtan_min_n, out=Mcvt_min_n)
        np.minimum(Mcvt_min_e, Mtan_min_e, out=Mcvt_min_e)
    if esp is not None and par.incluir_especial:
        np.maximum(Mcvt_max_n, Mesp_max_n, out=Mcvt_max_n)
        np.maximum(Mcvt_max_e, Mesp_max_e, out=Mcvt_max_e)
        np.minimum(Mcvt_min_n, Mesp_min_n, out=Mcvt_min_n)
        np.minimum(Mcvt_min_e, Mesp_min_e, out=Mcvt_min_e)

    if par.modo_combinacion == "alternativa":
        # AASHTO Standard: vehículo y faja son alternativos; gobierna el mayor
        # y el impacto se aplica a la envolvente resultante.
        Vcomb_max = np.round(fd_x * np.maximum(Vcvt_max, Vcar_max), 3)
        Vcomb_min = np.round(fd_x * np.minimum(Vcvt_min, Vcar_min), 3)
        Mcomb_max = np.round(fd_x * np.maximum(Mcvt_max, Mcar_max), 3)
        Mcomb_min = np.round(fd_x * np.minimum(Mcvt_min, Mcar_min), 3)
        Rp_comb = {i: fd_R[i] * max(Rp_cvt[i], Rp_car[i]) for i in range(est.n_sup)}
        Rn_comb = {i: fd_R[i] * min(Rn_cvt[i], Rn_car[i]) for i in range(est.n_sup)}
        titulo_comb = "%s x máx(%s ; Faja)" % (imp, titulo_cvt)
        Mcomb_max_n = np.round(fd_x * np.maximum(Mcvt_max_n, Mcar_max), 3)
        Mcomb_max_e = np.round(fd_x * np.maximum(Mcvt_max_e, Mcar_max), 3)
        Mcomb_min_n = np.round(fd_x * np.minimum(Mcvt_min_n, Mcar_min), 3)
        Mcomb_min_e = np.round(fd_x * np.minimum(Mcvt_min_e, Mcar_min), 3)
    else:
        # AASHTO LRFD: se suman, y el impacto no afecta a la carga de carril.
        Vcomb_max = np.round(fd_x * Vcvt_max + Vcar_max, 3)
        Vcomb_min = np.round(fd_x * Vcvt_min + Vcar_min, 3)
        Mcomb_max = np.round(fd_x * Mcvt_max + Mcar_max, 3)
        Mcomb_min = np.round(fd_x * Mcvt_min + Mcar_min, 3)
        Rp_comb = {i: fd_R[i] * Rp_cvt[i] + Rp_car[i] for i in range(est.n_sup)}
        Mcomb_max_n = np.round(fd_x * Mcvt_max_n + Mcar_max, 3)
        Mcomb_max_e = np.round(fd_x * Mcvt_max_e + Mcar_max, 3)
        Mcomb_min_n = np.round(fd_x * Mcvt_min_n + Mcar_min, 3)
        Mcomb_min_e = np.round(fd_x * Mcvt_min_e + Mcar_min, 3)
        Rn_comb = {i: fd_R[i] * Rn_cvt[i] + Rn_car[i] for i in range(est.n_sup)}
        titulo_comb = "%s(%s) + Carril" % (imp, titulo_cvt)

    crudas = [
        ("camion", par.nombre_camion, Vcam_max, Vcam_min, Mcam_max, Mcam_min, Rp_cam, Rn_cam),
    ]
    if par.usar_tandem:
        crudas.append(("tandem", par.nombre_tandem,
                       Vtan_max, Vtan_min, Mtan_max, Mtan_min, Rp_tan, Rn_tan))
    if esp is not None:
        Rp_esp, Rn_esp, Vesp_max, Vesp_min, Mesp_max, Mesp_min = esp
        crudas.append(("especial", par.nombre_especial,
                       Vesp_max, Vesp_min, Mesp_max, Mesp_min, Rp_esp, Rn_esp))
    if len(nombres_veh) > 1:            # con un solo vehículo, cvt repetiría el camión
        crudas.append(("cvt", titulo_cvt.upper(),
                       Vcvt_max, Vcvt_min, Mcvt_max, Mcvt_min, Rp_cvt, Rn_cvt))
    crudas += [
        ("carril", "CARGA DE FAJA" if hay_conc else "CARRIL",
         Vcar_max, Vcar_min, Mcar_max, Mcar_min, Rp_car, Rn_car),
        ("combinada", titulo_comb,
         Vcomb_max, Vcomb_min, Mcomb_max, Mcomb_min, Rp_comb, Rn_comb),
    ]

    # Mayoración global de la carga viva. El máximo es homogéneo, así que
    # escalar al final equivale a escalar las cargas de entrada. El peso
    # propio NO lleva esta mayoración: es carga permanente, no carga viva.
    fm = par.factor_mayoracion
    envs = {}
    for clave, titulo, Vx, Vn, Mx, Mn, Rp, Rn in crudas:
        Vx, Vn, Mx, Mn = (np.round(fm * a, 3) for a in (Vx, Vn, Mx, Mn))
        Rp = {i: fm * v for i, v in dict(Rp).items()}
        Rn = {i: fm * v for i, v in dict(Rn).items()}
        envs[clave] = Envolvente(titulo, Vx, Vn, Mx, Mn, Rp, Rn,
                                 _resumen(est, X, Vx, Vn, Mx, Mn))

    if pp is not None:
        w_pp, Vpp, Mpp, Rpp_dict = pp
        envs["peso_propio"] = Envolvente(
            "PESO PROPIO (%.3f Tnf/m)" % w_pp, Vpp, Vpp, Mpp, Mpp, Rpp_dict, Rpp_dict,
            _resumen(est, X, Vpp, Vpp, Mpp, Mpp))

    # ----------------------------------------- peso propio por viga (desglose)
    # Todas las partidas (y sus combinaciones) son cargas uniformes sobre TODA
    # la estructura, igual que el peso propio de toda la vía: basta resolver
    # una vez con w=1 Tnf/m y escalar por superposición (el problema es
    # lineal), en vez de resolver cada una por separado.
    if par.incluir_pp_por_viga:
        avisar(0.977, "Evaluando el peso propio por viga...")
        dpv = par.desglose_peso_propio_por_viga()
        V1, M1, R1 = est.fuerzas_carril(X, [True] * est.n_tramos, 1.0)

        def _env_pp(clave, titulo, w):
            Vc = np.round(w * V1, 3)
            Mc = np.round(w * M1, 3)
            Rc = {i: float(w * R1[i]) for i in range(est.n_sup)}
            envs["pp_" + clave] = Envolvente(
                "PESO PROPIO POR VIGA - %s (%.4f Tnf/m)" % (titulo, w),
                Vc, Vc, Mc, Mc, Rc, Rc, _resumen(est, X, Vc, Vc, Mc, Mc))

        for clave, titulo in (("viga", "VIGA"), ("losa", "LOSA"),
                              ("carpeta", "CARPETA ASFÁLTICA"), ("barandas", "BARANDA")):
            _env_pp(clave, titulo, dpv[clave])

        # Combinaciones útiles para comparar contra la carpeta y el total,
        # sin tener que sumarlas a mano prendiendo varios checkbox a la vez.
        _env_pp("losa_viga", "LOSA + VIGA", dpv["losa"] + dpv["viga"])
        _env_pp("losa_viga_barandas", "LOSA + VIGA + BARANDA",
               dpv["losa"] + dpv["viga"] + dpv["barandas"])
        _env_pp("total", "TOTAL (VIGA + LOSA + CARPETA + BARANDA)", dpv["total"])

    # ------------------------------------- distribución transversal (viga interior)
    # Sólo el MOMENTO de la envolvente de carga viva combinada se ve afectado
    # (es lo que pide el método S/D de la Tabla 3.23.1). El corte y las
    # reacciones se conservan por vía completa: esta tabla no da un factor
    # para ellos. El peso propio queda aparte: su reparto por viga es por
    # ancho tributario, un método distinto del de rueda móvil que usa g.
    dist_g = dist_D = dist_S_ft = None
    dist_aviso = None
    if par.incluir_distribucion and "combinada" in envs:
        dist_g, dist_D, dist_S_ft, dist_aviso = factor_distribucion_momento(
            par.tipo_viga, par.separacion_vigas, par.n_vias_diseno)
        comb = envs["combinada"]
        Mx_vg = np.round(comb.M_max * dist_g, 3)
        Mn_vg = np.round(comb.M_min * dist_g, 3)
        envs["viga_interior"] = Envolvente(
            "MOMENTO EN VIGA INTERIOR  (g = %.4f = S/%.1f, %s)"
            % (dist_g, dist_D, "hormigón armado" if par.tipo_viga == "hormigon" else "acero"),
            comb.V_max, comb.V_min, Mx_vg, Mn_vg, dict(comb.R_pos), dict(comb.R_neg),
            _resumen(est, X, comb.V_max, comb.V_min, Mx_vg, Mn_vg))

    def _dir(mx_n, mx_e, mn_n, mn_e):
        return {"max_normal": mx_n, "max_espejo": mx_e, "min_normal": mn_n, "min_espejo": mn_e}

    direccion_M = {"camion": _dir(Mcam_max_n, Mcam_max_e, Mcam_min_n, Mcam_min_e)}
    if par.usar_tandem:
        direccion_M["tandem"] = _dir(Mtan_max_n, Mtan_max_e, Mtan_min_n, Mtan_min_e)
    if esp is not None:
        direccion_M["especial"] = _dir(Mesp_max_n, Mesp_max_e, Mesp_min_n, Mesp_min_e)
    if len(nombres_veh) > 1:
        direccion_M["cvt"] = _dir(Mcvt_max_n, Mcvt_max_e, Mcvt_min_n, Mcvt_min_e)
    direccion_M["combinada"] = _dir(Mcomb_max_n, Mcomb_max_e, Mcomb_min_n, Mcomb_min_e)
    if "viga_interior" in envs:
        # Mismo M que "combinada", sólo escalado por g (constante > 0): la
        # posición de cada pico no cambia.
        direccion_M["viga_interior"] = direccion_M["combinada"]

    avisar(1.0, "Listo.")
    return Resultados(par, est, X, envs, casos, fr_cam, fr_tan,
                      configs_camion=configs_cam,
                      sep_camion_M_max=sep_M_max, sep_camion_M_min=sep_M_min,
                      frames_especial=fr_esp, ejes_especial=ejes_esp,
                      impacto_tramos=[1.0 + impacto_aashto_standard(L) for L in est.tramos]
                      if par.impacto_modo != "fijo" else [par.factor_dinamico] * est.n_tramos,
                      impacto_apoyos=dict(fd_R),
                      dist_g=dist_g, dist_D=dist_D, dist_S_ft=dist_S_ft, dist_aviso=dist_aviso,
                      direccion_M=direccion_M,
                      base_viva={c[0]: (c[1],) + tuple(c[2:]) for c in crudas
                                 if c[0] != "combinada"},
                      base_cvt=(titulo_cvt, Vcvt_max, Vcvt_min, Mcvt_max, Mcvt_min,
                                Rp_cvt, Rn_cvt),
                      fd_x=fd_x, titulo_cvt=titulo_cvt, imp_txt=imp)


class CalculoCancelado(Exception):
    """El usuario canceló el cálculo desde la interfaz."""
