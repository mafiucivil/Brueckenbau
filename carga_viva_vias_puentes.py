# -*- coding: utf-8 -*-
"""
Envolventes de carga viva en vigas continuas y vigas Gerber.

Script por lotes: calcula, imprime el reporte y genera la planilla Excel y el
gráfico PNG. Para trabajar con interfaz gráfica ejecute gui_puente.py en su
lugar.

El cálculo vive en motor_puente.py y los gráficos en graficos_puente.py, de
modo que el script y la interfaz comparten exactamente la misma física.
"""

import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

import motor_puente as mp
import graficos_puente as gp

# =============================================================================
# 1. NORMA: define toda la metodología de la carga viva
# =============================================================================
#   mp.HS20_44  -> AASHTO Standard. Camión HS20-44 O carga de faja (con
#                  concentrada de 18/26 kip), alternativos; impacto
#                  I = 50/(L+125) <= 0.30 sobre la envolvente completa.
#                  Es el camión de diseño del Manual de Carreteras de Chile.
#   mp.HL_93    -> AASHTO LRFD. (Camión o Tándem) MÁS carril, sumados;
#                  impacto 1.33 fijo, sólo sobre el vehículo.
NORMA = mp.HS20_44

par = mp.aplicar_norma(mp.Parametros(), NORMA)

# =============================================================================
# 2. DATOS DEL PROYECTO: modifique aquí
# =============================================================================
# Luces de los tramos, en metros (un valor por tramo)
par.tramos = [10, 12, 10]

# Rótulas internas: posiciones en m desde el extremo izquierdo.
#   []              -> viga continua
#   [12.4, 19.6]    -> viga Gerber
# Grado de hiperestaticidad GH = (n_apoyos - 2) - n_rótulas
par.rotulas = []

# Paso de cálculo (0.05 m = 5 cm)
par.dx = 0.05

# Mayoración global de la carga viva. aplicar_norma() ya la dejó en el valor
# que Chile usa por norma (1.20 en AASHTO Standard, 1.0 en AASHTO LRFD);
# descomente para forzar otro valor en un proyecto puntual.
# par.factor_mayoracion = 1.0

# ---- Camión especial / convoy (opcional) ------------------------------------
# Se describe con las cargas por eje y las separaciones entre ejes consecutivos.
# Deje la lista vacía para no analizarlo. Ejemplo de un convoy de 41 ejes:
#
# par.ejes_especial = mp.ejes_desde_separaciones(
#     [8, 12, 12, 8, 12, 12] + [11.6] * 32 + [8, 12, 12],
#     [3.5, 1.3, 6.0, 3.5, 1.3, 3.0] + [1.5] * 15 + [18.0] + [1.5] * 15 + [3.0, 3.5, 1.3])
# par.incluir_especial = True    # que entre en la envolvente gobernante
par.ejes_especial = []
par.incluir_especial = False

ARCHIVO_EXCEL = "Fuerzas Internas por Via.xlsx"
ARCHIVO_PNG = "Envolvente de Carga Viva por Via.png"


# =============================================================================
# 3. CÁLCULO
# =============================================================================
if __name__ == "__main__":
    estado = {"txt": ""}

    def progreso(frac, texto):
        if texto != estado["txt"]:
            estado["txt"] = texto
            print("  [%3.0f%%] %s" % (frac * 100, texto))
        return True

    print("=" * 56)
    print("      TOPOLOGÍA DE LA ESTRUCTURA")
    print("=" * 56)
    est_previa = mp.Estructura(par.tramos, par.rotulas)
    print(est_previa.descripcion())
    for aviso in est_previa.avisos:
        print("AVISO: " + aviso)
    print("=" * 56 + "\n")

    res = mp.calcular(par, progreso=progreso)
    print()
    print(res.texto_resumen())

    # =========================================================================
    # 4. EXPORTACIÓN
    # =========================================================================
    print("Generando la planilla con las coordenadas punto a punto...")
    res.exportar_excel(ARCHIVO_EXCEL)
    print("¡Exportación exitosa! Revise el archivo '%s'.\n" % ARCHIVO_EXCEL)

    print("Generando la imagen estática de la envolvente combinada (PNG)...")
    fig = gp.figura_envolvente(res, "combinada", fig=Figure(figsize=(12, 9)))
    fig.savefig(ARCHIVO_PNG, dpi=300)
    print("¡Imagen guardada como '%s'!\n" % ARCHIVO_PNG)
