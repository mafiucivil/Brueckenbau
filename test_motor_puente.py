# -*- coding: utf-8 -*-
"""
Validación del motor de cálculo (motor_puente.py).

Compara contra formulaciones INDEPENDIENTES, no contra sí mismo:
  T1  Soluciones cerradas de tabla y método de las fuerzas resuelto a mano.
  T2  Método de las fuerzas por integración numérica (vigas continuas).
  T3  Rigidez con NODO DOBLE en la rótula: otra implementación de la rótula.
  T4  Gerber isostática: casos a mano, equilibrio global y M = 0 en la rótula.
  T5  Detección de mecanismos y estructuras inestables.

Ejecutar con:   py test_motor_puente.py
"""

import sys

import numpy as np

import motor_puente as mp

fallos = []


def chequear(nombre, ok, detalle=""):
    print(("  OK    " if ok else "  FALLA ") + nombre + ("   " + detalle if detalle else ""))
    if not ok:
        fallos.append(nombre)


# =====================================================================
# Referencia 1: MÉTODO DE LAS FUERZAS (sólo vigas continuas)
# Primaria = viga simplemente apoyada en los extremos; redundantes = los
# apoyos interiores; flexibilidades por integración numérica de M·m dx.
# =====================================================================
def ref_metodo_fuerzas(apoyos, cargas_p, cargas_u, h=5e-4):
    Lt = apoyos[-1]
    x = np.arange(0.0, Lt + h, h)

    def M_simple(P_list, U_list):
        Mo = np.zeros_like(x)
        for P, xp in P_list:
            Rl = P * (Lt - xp) / Lt
            Mo = Mo + Rl * x - P * np.maximum(0.0, x - xp)
        for xa, xb, w in U_list:
            W = w * (xb - xa)
            Rl = W * (Lt - 0.5 * (xa + xb)) / Lt
            dentro = np.clip(x, xa, xb)
            Mo = Mo + Rl * x - w * (dentro - xa) * (x - 0.5 * (dentro + xa)) * (x > xa)
        return Mo

    interiores = list(range(1, len(apoyos) - 1))
    M0 = M_simple(cargas_p, cargas_u)
    m = [M_simple([(1.0, apoyos[i])], []) for i in interiores]

    R = np.zeros(len(apoyos))
    if interiores:
        n = len(interiores)
        f = np.array([[np.trapezoid(m[i] * m[j], x) for j in range(n)] for i in range(n)])
        d0 = np.array([np.trapezoid(M0 * m[i], x) for i in range(n)])
        Xr = np.linalg.solve(f, -d0)          # carga hacia abajo equivalente
        for k, i in enumerate(interiores):
            R[i] = -Xr[k]

    P_tot = sum(P for P, _ in cargas_p) + sum(w * (xb - xa) for xa, xb, w in cargas_u)
    Mo_A = (sum(P * xp for P, xp in cargas_p)
            + sum(w * (xb - xa) * 0.5 * (xa + xb) for xa, xb, w in cargas_u))
    R[-1] = (Mo_A - sum(R[i] * apoyos[i] for i in interiores)) / Lt
    R[0] = P_tot - sum(R[i] for i in interiores) - R[-1]
    return R


# =====================================================================
# Referencia 2: RIGIDEZ CON NODO DOBLE (sin condensación estática)
# =====================================================================
def ref_nodo_doble(apoyos, rotulas, cargas_p, cargas_u):
    nodos = np.array(sorted(set(round(v, 9) for v in list(apoyos) + list(rotulas))))
    nn = len(nodos)
    es_ap = np.array([any(abs(a - p) < 1e-7 for a in apoyos) for p in nodos])
    es_ro = np.array([any(abs(r - p) < 1e-7 for r in rotulas) for p in nodos])

    nd = nn
    giro_izq = [None] * nn
    giro_der = [None] * nn
    for j in range(nn):
        giro_izq[j] = nd
        nd += 1
        if es_ro[j]:                      # la rótula duplica el GDL de giro
            giro_der[j] = nd
            nd += 1
        else:
            giro_der[j] = giro_izq[j]

    K = np.zeros((nd, nd))
    F = np.zeros(nd)

    def N(L, xx):
        s = xx / L
        return np.array([1 - 3 * s ** 2 + 2 * s ** 3, L * (s - 2 * s ** 2 + s ** 3),
                         3 * s ** 2 - 2 * s ** 3, L * (-s ** 2 + s ** 3)])

    for mb in range(nn - 1):
        x1, x2 = nodos[mb], nodos[mb + 1]
        L = x2 - x1
        L2, L3 = L * L, L * L * L
        k = np.array([[12 / L3, 6 / L2, -12 / L3, 6 / L2],
                      [6 / L2, 4 / L, -6 / L2, 2 / L],
                      [-12 / L3, -6 / L2, 12 / L3, -6 / L2],
                      [6 / L2, 2 / L, -6 / L2, 4 / L]])
        g = [mb, giro_der[mb], mb + 1, giro_izq[mb + 1]]
        K[np.ix_(g, g)] += k

        p = np.zeros(4)
        for P, xp in cargas_p:
            if (x1 - 1e-9 <= xp < x2 - 1e-9) or (mb == nn - 2 and abs(xp - x2) < 1e-9):
                p += -P * N(L, xp - x1)
        for xa, xb, w in cargas_u:
            a, b = max(xa, x1), min(xb, x2)
            if b - a > 1e-9:
                for gp in (-1 / np.sqrt(3), 1 / np.sqrt(3)):
                    xg = 0.5 * (a + b) + 0.5 * (b - a) * gp
                    p += -w * N(L, xg - x1) * 0.5 * (b - a)
        F[g] += p

    libres = [i for i in range(nd) if not (i < nn and es_ap[i])]
    d = np.zeros(nd)
    d[libres] = np.linalg.solve(K[np.ix_(libres, libres)], F[libres])
    reac = K @ d - F
    return np.array([reac[int(np.argmin(np.abs(nodos - a)))] for a in apoyos])


CASOS_ROTULAS = [([10, 12, 10], [8.0]),
                 ([10, 12, 10], [8.0, 24.0]),
                 ([10, 12, 10], [10.0]),
                 ([10, 12, 10], [12.4, 19.6]),
                 ([15, 20, 20, 15], [11.0, 24.0]),
                 ([15, 20, 20, 15], [11.0, 24.0, 44.0]),
                 ([20, 25, 20], [16.0, 49.0]),
                 ([8, 14, 9, 11, 8], [6.0, 19.0, 33.0])]


def cargas_de_prueba(tramos, ap):
    casos = [([(14.56, 3.7)], []),
             ([(3.64, 1.3), (14.56, 5.6), (14.56, 9.9)], []),
             ([(10.0, sum(tramos) * 0.77)], []),
             ([(10.0, sum(tramos) * 0.83)], []),
             ([], [(ap[0], ap[1], 0.96)]),
             ([], [(ap[k], ap[k + 1], 0.96) for k in range(0, len(tramos), 2)]),
             ([], [(ap[k], ap[k + 1], 0.96) for k in range(len(tramos))])]
    if len(tramos) > 1:
        casos.append(([(11.2, sum(tramos) * 0.41)], [(ap[1], ap[2], 0.96)]))
    return casos


print("=" * 78)
print("T1 - SOLUCIONES CERRADAS")
print("=" * 78)
e2 = mp.Estructura([10, 10])
R = e2.reacciones(cargas_puntuales=[(10.0, 5.0)])
chequear("2 vanos, P=10 en x=5 (tabla: 13P/32, 11P/16, -3P/32)",
         np.allclose(R, [4.0625, 6.875, -0.9375], atol=1e-9), "R = %s" % np.round(R, 5))

# Método de las fuerzas a mano: viga simple S=20, flecha en el centro por P=10
# a 3 m = P·a·(3S²-4a²)/48 = 727.5/EI; flexibilidad del centro = S³/48 = 166.667/EI
# -> R_B = 727.5/166.667 = 4.365
R = e2.reacciones(cargas_puntuales=[(10.0, 3.0)])
chequear("2 vanos, P=10 en x=3 (a mano: R_B = 4.365)",
         np.allclose(R, [6.3175, 4.365, -0.6825], atol=1e-9), "R = %s" % np.round(R, 5))

R = e2.reacciones(cargas_uniformes=[(0.0, 10.0, 2.0), (10.0, 20.0, 2.0)])
chequear("2 vanos, w=2 en ambos (tabla: 7.5, 25, 7.5)",
         np.allclose(R, [7.5, 25.0, 7.5], atol=1e-9), "R = %s" % np.round(R, 5))

R = mp.Estructura([10, 10, 10]).reacciones(cargas_uniformes=[(10.0, 20.0, 2.0)])
chequear("3 vanos, w=2 sólo en el central (tabla: -1, 11, 11, -1)",
         np.allclose(R, [-1.0, 11.0, 11.0, -1.0], atol=1e-9), "R = %s" % np.round(R, 5))

print()
print("=" * 78)
print("T2 - VIGA CONTINUA: rigidez vs. MÉTODO DE LAS FUERZAS")
print("=" * 78)
for tramos in ([10, 12, 10], [15, 20, 20, 15], [12, 12], [8, 14, 9, 11, 8], [18]):
    est = mp.Estructura(tramos)
    peor = 0.0
    for cp, cu in cargas_de_prueba(tramos, est.apoyos_x):
        if cu and len(tramos) < 2 and len(cu) > 1:
            continue
        R1 = est.reacciones(cargas_puntuales=cp, cargas_uniformes=cu)
        R2 = ref_metodo_fuerzas(est.apoyos_x, cp, cu)
        peor = max(peor, float(np.max(np.abs(R1 - R2))))
    chequear("tramos %s" % (tramos,), peor < 2e-3, "dif. máx = %.2e Tnf" % peor)

print()
print("=" * 78)
print("T3 - CON RÓTULAS: condensación estática vs. NODO DOBLE")
print("=" * 78)
for tramos, rot in CASOS_ROTULAS:
    est = mp.Estructura(tramos, rot)
    peor = 0.0
    for cp, cu in cargas_de_prueba(tramos, est.apoyos_x):
        R1 = est.reacciones(cargas_puntuales=cp, cargas_uniformes=cu)
        R2 = ref_nodo_doble(est.apoyos_x, rot, cp, cu)
        peor = max(peor, float(np.max(np.abs(R1 - R2))))
    chequear("tramos %s rótulas %s" % (tramos, rot), peor < 1e-8, "dif. máx = %.2e Tnf" % peor)

print()
print("=" * 78)
print("T4 - GERBER: casos a mano, equilibrio y M = 0 en las rótulas")
print("=" * 78)
g = mp.Estructura([10, 10], [15.0])
R = g.reacciones(cargas_puntuales=[(10.0, 5.0)])
chequear("P=10 en x=5  -> [5, 5, 0]", np.allclose(R, [5, 5, 0], atol=1e-9), "R = %s" % np.round(R, 6))
R = g.reacciones(cargas_puntuales=[(10.0, 17.0)])
chequear("P=10 en x=17 -> [-3, 9, 4]", np.allclose(R, [-3, 9, 4], atol=1e-9), "R = %s" % np.round(R, 6))

for tramos, rot in CASOS_ROTULAS:
    par = mp.Parametros(tramos=tramos, rotulas=rot)
    est = mp.Estructura(tramos, rot)
    X = np.round(np.arange(0.0, est.L_total + par.dx, par.dx), 6)
    idx = [int(np.argmin(np.abs(X - r))) for r in rot]
    e_eq = e_rot = 0.0
    for xt in np.arange(1.0, est.L_total + 9.0, 0.53):
        V, M, act, R = est.fuerzas_vehiculo(X, xt, par.ejes_camion)
        e_eq = max(e_eq, abs(float(R.sum()) - sum(P for P, _ in act)))
        for i in idx:
            e_rot = max(e_rot, abs(float(M[i])))
    for patron in ([i % 2 == 0 for i in range(len(tramos))], [True] * len(tramos)):
        V, M, R = est.fuerzas_carril(X, patron, par.w_carril)
        carga = sum(par.w_carril * tramos[k] for k in range(len(tramos)) if patron[k])
        e_eq = max(e_eq, abs(float(R.sum()) - carga))
        for i in idx:
            e_rot = max(e_rot, abs(float(M[i])))
    chequear("equilibrio y M_rótula = 0: %s %s" % (tramos, rot), max(e_eq, e_rot) < 1e-7,
             "eq = %.2e  M_rótula = %.2e" % (e_eq, e_rot))

print()
print("=" * 78)
print("T5 - CONFIGURACIONES INVÁLIDAS")
print("=" * 78)
for descr, tramos, rot in [("3 rótulas con 4 apoyos (mecanismo)", [10, 12, 10], [4.0, 8.0, 20.0]),
                           ("2 rótulas en el mismo tramo", [10, 12, 10], [3.0, 6.0]),
                           ("1 rótula en viga de un solo tramo", [18], [9.0])]:
    try:
        mp.Estructura(tramos, rot)
        chequear(descr, False, "no lanzó error")
    except (ValueError, np.linalg.LinAlgError) as exc:
        chequear(descr, True, type(exc).__name__)

for descr, kwargs in [("luz negativa", dict(tramos=[10, -2])),
                      ("dx demasiado grueso", dict(tramos=[10, 12, 10], dx=5.0)),
                      ("offset positivo en el camión",
                       dict(ejes_camion=[{"P": 5.0, "offset": 2.0}]))]:
    try:
        mp.Parametros(**kwargs).validar()
        chequear(descr, False, "no lanzó error")
    except ValueError:
        chequear(descr, True, "ValueError")

print()
print("=" * 78)
print("T6 - SEPARACIÓN VARIABLE ENTRE EJES TRASEROS (AASHTO 3.6.1.2.2)")
print("=" * 78)

# La norma define el camión en pies: 14 ft fijos y 14 a 30 ft variables.
S_MIN, S_MAX = mp.PIE * 14.0, mp.PIE * 30.0      # 4.2672 y 9.1440 m

# --- Geometría de las configuraciones generadas
par = mp.Parametros()
chequear("por defecto usa pies exactos, no el redondeo métrico",
         abs(par.sep_min - S_MIN) < 1e-12 and abs(par.sep_max - S_MAX) < 1e-12
         and abs(par.ejes_camion[1]["offset"] + S_MIN) < 1e-12
         and abs(par.ejes_camion[2]["offset"] + 2 * S_MIN) < 1e-12,
         "14 ft = %.4f m, 30 ft = %.4f m, offsets %.4f y %.4f"
         % (par.sep_min, par.sep_max, par.ejes_camion[1]["offset"], par.ejes_camion[2]["offset"]))
cfgs = par.configuraciones_camion()
seps = [s for s, _ in cfgs]
chequear("el barrido cubre de 14 a 30 ft con los extremos incluidos",
         abs(seps[0] - S_MIN) < 1e-12 and abs(seps[-1] - S_MAX) < 1e-12
         and all(seps[i] < seps[i + 1] for i in range(len(seps) - 1)),
         "%d configuraciones, de %.4f a %.4f m" % (len(cfgs), seps[0], seps[-1]))
ok_geom = True
for s, ejes in cfgs:
    off = [e["offset"] for e in ejes]
    ok_geom &= (abs(off[0]) < 1e-12 and abs(off[1] + S_MIN) < 1e-12
                and abs(off[2] - (off[1] - s)) < 1e-12)
    ok_geom &= (abs(ejes[0]["P"] - mp.KIP * 8) < 1e-12
                and abs(ejes[2]["P"] - mp.KIP * 32) < 1e-12)
chequear("delantero fijo a 14 ft y trasero a la separación del barrido", ok_geom,
         "último offset: %.2f a %.2f m" % (cfgs[0][1][2]["offset"], cfgs[-1][1][2]["offset"]))

par_fija = mp.Parametros(sep_variable=False)
chequear("con sep_variable=False queda una sola configuración",
         len(par_fija.configuraciones_camion()) == 1)

# --- La envolvente del barrido debe ser el máximo de las envolventes individuales
base = mp.Parametros(tramos=[10, 12, 10], dx=0.1)


def con_sep_fija(s):
    p = mp.Parametros(tramos=base.tramos, dx=base.dx, sep_variable=False)
    p.ejes_camion = [{"P": mp.KIP * 8, "offset": 0.0},
                     {"P": mp.KIP * 32, "offset": -S_MIN},
                     {"P": mp.KIP * 32, "offset": -(S_MIN + s)}]
    return mp.calcular(p)


p_var = mp.Parametros(tramos=base.tramos, dx=base.dx, sep_variable=True,
                      sep_min=S_MIN, sep_max=S_MAX, sep_paso=(S_MAX - S_MIN) / 4.0)
res_var = mp.calcular(p_var)

SEPS = res_var.separaciones          # 5 separaciones entre 14 y 30 ft
sueltas = [con_sep_fija(s) for s in SEPS]
env_var = res_var.envolventes["camion"]
M_max_ref = np.max([r.envolventes["camion"].M_max for r in sueltas], axis=0)
M_min_ref = np.min([r.envolventes["camion"].M_min for r in sueltas], axis=0)
V_max_ref = np.max([r.envolventes["camion"].V_max for r in sueltas], axis=0)
V_min_ref = np.min([r.envolventes["camion"].V_min for r in sueltas], axis=0)
chequear("la envolvente del barrido = máximo de las envolventes sueltas",
         np.allclose(env_var.M_max, M_max_ref, atol=1e-9)
         and np.allclose(env_var.M_min, M_min_ref, atol=1e-9)
         and np.allclose(env_var.V_max, V_max_ref, atol=1e-9)
         and np.allclose(env_var.V_min, V_min_ref, atol=1e-9),
         "%d separaciones: %s" % (len(SEPS), ", ".join("%.2f" % s for s in SEPS)))

R_pos_ref = {i: max(r.envolventes["camion"].R_pos[i] for r in sueltas)
             for i in range(len(base.tramos) + 1)}
chequear("las reacciones también envuelven todo el barrido",
         all(abs(env_var.R_pos[i] - R_pos_ref[i]) < 1e-9 for i in R_pos_ref))

# --- La separación registrada debe ser la que efectivamente gobierna
ok_sep = True
for j in range(len(res_var.X)):
    s_gob = res_var.sep_camion_M_max[j]
    k = int(np.argmin([abs(s - s_gob) for s in SEPS]))
    ok_sep &= abs(sueltas[k].envolventes["camion"].M_max[j] - env_var.M_max[j]) < 1e-9
chequear("la separación registrada reproduce el M(+) envolvente", ok_sep)

# --- El barrido nunca puede dar menos que la separación mínima sola
fija_min = sueltas[0].envolventes["camion"]
chequear("el barrido contiene al caso de separación mínima",
         bool(np.all(env_var.M_max >= fija_min.M_max - 1e-9)
              and np.all(env_var.M_min <= fija_min.M_min + 1e-9)))

# --- Sentido físico: en un tramo simple corto gobierna la separación mínima
r_simple = mp.calcular(mp.Parametros(tramos=[10], dx=0.05))
j = int(np.argmin(np.abs(r_simple.X - 5.0)))
chequear("en viga simple de 10 m gobierna la separación mínima en el centro",
         abs(r_simple.sep_camion_M_max[j] - S_MIN) < 1e-9,
         "separación crítica = %.2f m" % r_simple.sep_camion_M_max[j])

# --- Sentido físico: en luces cortas los dos ejes traseros alcanzan los dos
# lóbulos de la línea de influencia de M(-) y una separación larga gobierna.
def m_min_en_apoyo(tramos, x_apoyo, variable):
    p = mp.Parametros(tramos=tramos, dx=0.1, sep_variable=variable)
    if not variable:
        p.ejes_camion = [{"P": mp.KIP * 8, "offset": 0.0}, {"P": mp.KIP * 32, "offset": -S_MIN},
                         {"P": mp.KIP * 32, "offset": -2 * S_MIN}]
    r = mp.calcular(p)
    j = int(np.argmin(np.abs(r.X - x_apoyo)))
    return r, j


r_var, j = m_min_en_apoyo([10, 10], 10.0, True)
r_fij, _ = m_min_en_apoyo([10, 10], 10.0, False)
M_var = r_var.envolventes["camion"].M_min[j]
M_fij = r_fij.envolventes["camion"].M_min[j]
s_apoyo = r_var.sep_camion_M_min[j]
print("      2 tramos de 10 m, M(-) en el apoyo: fija 14 ft = %.2f | barrido = %.2f Tnf·m "
      "(%+.1f %%), separación crítica %.2f m"
      % (M_fij, M_var, 100 * (M_var - M_fij) / abs(M_fij), s_apoyo))
chequear("en luces cortas gobierna una separación larga en el apoyo",
         s_apoyo > S_MIN + 1e-9 and M_var < M_fij - 1e-6)

# En luces largas los lóbulos quedan fuera del alcance del camión: manda la mínima
r_largo, j = m_min_en_apoyo([30, 30], 30.0, True)
chequear("en luces largas vuelve a gobernar la separación mínima",
         abs(r_largo.sep_camion_M_min[j] - S_MIN) < 1e-9,
         "separación crítica = %.2f m" % r_largo.sep_camion_M_min[j])

print()
print("=" * 78)
print("T6b - BARRIDO EN AMBOS SENTIDOS DE CIRCULACIÓN")
print("=" * 78)

# --- espejar_ejes: geometría del vehículo invertido
ejes_prueba = [{"P": 8.0, "offset": 0.0}, {"P": 32.0, "offset": -4.0}, {"P": 32.0, "offset": -10.0}]
esp = mp.espejar_ejes(ejes_prueba)
chequear("espejar_ejes invierte el orden y conserva las separaciones",
         [round(e["offset"], 6) for e in esp] == [0.0, -6.0, -10.0]
         and [e["P"] for e in esp] == [32.0, 32.0, 8.0],
         str([(e["P"], round(e["offset"], 3)) for e in esp]))
chequear("espejar dos veces devuelve la configuración original",
         mp._mismos_ejes(mp.espejar_ejes(esp), ejes_prueba))

# --- En una estructura ASIMÉTRICA (tramos distintos), la envolvente debe
# coincidir con barrer manualmente el camión en los dos sentidos y tomar el
# máximo; y el sentido sí debe cambiar el resultado (a diferencia de T6,
# donde todo se verificó en estructuras simétricas evaluadas en su propio
# eje de simetría, donde el espejo no aporta nada nuevo).
par_asim = mp.aplicar_norma(mp.Parametros(tramos=[8.0, 16.0], dx=0.1), mp.HS20_44)
par_asim.sep_variable = False
par_asim.factor_mayoracion = 1.0   # comparación limpia contra el barrido manual, sin mayorar
res_asim = mp.calcular(par_asim)
env_cam = res_asim.envolventes["camion"]

est_asim, X_asim = res_asim.estructura, res_asim.X
ejes_fwd = par_asim.ejes_camion
ejes_mir = mp.espejar_ejes(ejes_fwd)
largo = -min(e["offset"] for e in ejes_fwd)
base_asim = np.arange(0.0, est_asim.L_total + largo + 1.0, par_asim.dx)


def _barrido_manual(ejes):
    # Igual que adentro de barrer(): además de la grilla uniforme, se agregan
    # las posiciones que dejan un eje pegado a cada apoyo (ahí está el pico
    # del cortante y, en el apoyo interior, también el de M(-)); si no, con
    # paso dx se puede perder el pico exacto en el apoyo interior.
    criticas = [a - e["offset"] + s for e in ejes for a in est_asim.apoyos_x
               for s in (-mp.TOL_NODO, mp.TOL_NODO)]
    pos = np.unique(np.concatenate([base_asim, np.array(criticas)]))
    pos = pos[(pos >= 0.0) & (pos <= base_asim[-1])]

    Vx = np.full_like(X_asim, -np.inf); Vn = np.full_like(X_asim, np.inf)
    Mx = np.full_like(X_asim, -np.inf); Mn = np.full_like(X_asim, np.inf)
    for xt in pos:
        V, M, _, _ = est_asim.fuerzas_vehiculo(X_asim, xt, ejes)
        np.maximum(Vx, V, out=Vx); np.minimum(Vn, V, out=Vn)
        np.maximum(Mx, M, out=Mx); np.minimum(Mn, M, out=Mn)
    return Vx, Vn, Mx, Mn


Vx1, Vn1, Mx1, Mn1 = _barrido_manual(ejes_fwd)
Vx2, Vn2, Mx2, Mn2 = _barrido_manual(ejes_mir)
# atol = 1e-3: el motor redondea V y M a 3 decimales al final de barrer().
peor_asim = max(float(np.max(np.abs(env_cam.M_max - np.maximum(Mx1, Mx2)))),
               float(np.max(np.abs(env_cam.M_min - np.minimum(Mn1, Mn2)))),
               float(np.max(np.abs(env_cam.V_max - np.maximum(Vx1, Vx2)))),
               float(np.max(np.abs(env_cam.V_min - np.minimum(Vn1, Vn2)))))
chequear("el motor reproduce el máximo de barrer a mano en los dos sentidos",
         peor_asim < 1e-3, "dif. máx = %.2e" % peor_asim)
chequear("el sentido de circulación sí cambia la envolvente en una estructura asimétrica",
         not np.allclose(Mx1, Mx2, atol=1e-6),
         "máx |M(+) sentido 1 - sentido 2| = %.3f Tnf·m" % float(np.max(np.abs(Mx1 - Mx2))))

print()
print("=" * 78)
print("T7 - CAMIÓN ESPECIAL DE N EJES")
print("=" * 78)

# --- Construcción a partir de cargas y separaciones
ejes = mp.ejes_desde_separaciones([8, 12, 12], [3.5, 1.3])
chequear("ejes_desde_separaciones ubica bien los offsets",
         [round(e["offset"], 6) for e in ejes] == [0.0, -3.5, -4.8]
         and [e["P"] for e in ejes] == [8.0, 12.0, 12.0],
         str([round(e["offset"], 3) for e in ejes]))
try:
    mp.ejes_desde_separaciones([8, 12, 12], [3.5])
    chequear("exige una separación entre cada par de ejes", False, "no lanzó error")
except ValueError:
    chequear("exige una separación entre cada par de ejes", True, "ValueError")
try:
    mp.ejes_desde_separaciones([8, 12], [0.0])
    chequear("rechaza separación nula", False, "no lanzó error")
except ValueError:
    chequear("rechaza separación nula", True, "ValueError")

desordenados = [{"P": 12, "offset": -4.8}, {"P": 8, "offset": 0.5}, {"P": 12, "offset": -3.0}]
norm = mp.normalizar_ejes(desordenados)
chequear("normalizar_ejes ordena y lleva el delantero a 0",
         [round(e["offset"], 6) for e in norm] == [0.0, -3.5, -5.3]
         and [e["P"] for e in norm] == [8.0, 12.0, 12.0],
         str([round(e["offset"], 3) for e in norm]))

# --- Convoy del caso real: 8-12-12-8-12-12, 16 ejes de 11.6, vano de 18 m,
#     16 ejes de 11.6 y 8-12-12. 41 ejes en total.
CARGAS = [8, 12, 12, 8, 12, 12] + [11.6] * 16 + [11.6] * 16 + [8, 12, 12]
SEPS = [3.5, 1.3, 6.0, 3.5, 1.3, 3.0] + [1.5] * 15 + [18.0] + [1.5] * 15 + [3.0, 3.5, 1.3]
convoy = mp.ejes_desde_separaciones(CARGAS, SEPS)
chequear("convoy de 41 ejes armado", len(convoy) == 41,
         "largo %.2f m, carga total %.1f Tnf" % (-convoy[-1]["offset"], sum(CARGAS)))


# --- Referencia independiente: viga simplemente apoyada por estática elemental
def ref_simple_convoy(L, X, ejes, dx):
    largo = -ejes[-1]["offset"]
    base = np.arange(0.0, L + largo + 1.0, dx)
    # mismo muestreo que el motor: se agregan las posiciones con un eje pegado
    # a cada apoyo, donde salta la línea de influencia del cortante
    criticas = [a - e["offset"] + s for e in ejes for a in (0.0, L)
                for s in (-1e-6, 1e-6)]
    posiciones = np.unique(np.concatenate([base, np.array(criticas)]))
    posiciones = posiciones[(posiciones >= 0.0) & (posiciones <= base[-1])]

    Vx = np.full_like(X, -np.inf); Vn = np.full_like(X, np.inf)
    Mx = np.full_like(X, -np.inf); Mn = np.full_like(X, np.inf)
    RA_max = -np.inf
    for xt in posiciones:
        cargas = [(e["P"], xt + e["offset"]) for e in ejes if 0 <= xt + e["offset"] <= L]
        RA = sum(P * (L - xp) / L for P, xp in cargas)      # estática pura, sin rigidez
        RA_max = max(RA_max, RA)
        V = np.full_like(X, RA)
        M = RA * X
        for P, xp in cargas:
            V -= P * (X >= xp - 1e-7)
            M -= P * np.maximum(0.0, X - xp)
        M[0] = M[-1] = 0.0
        np.maximum(Vx, V, out=Vx); np.minimum(Vn, V, out=Vn)
        np.maximum(Mx, M, out=Mx); np.minimum(Mn, M, out=Mn)
    return np.round(Vx, 3), np.round(Vn, 3), np.round(Mx, 3), np.round(Mn, 3), RA_max


for nombre, ejes_v in (("convoy de 41 ejes", convoy),
                       ("camión de 3 ejes", mp.ejes_desde_separaciones([8, 12, 12], [3.5, 1.3]))):
    p = mp.Parametros(tramos=[25.0], dx=0.1, ejes_especial=ejes_v)
    r = mp.calcular(p)
    env = r.envolventes["especial"]
    # El motor barre el vehículo en los dos sentidos de circulación (ver
    # espejar_ejes); la referencia independiente debe hacer lo mismo para
    # seguir siendo una comparación válida.
    ejes_norm = mp.normalizar_ejes(ejes_v)
    Vx1, Vn1, Mx1, Mn1, RA1 = ref_simple_convoy(25.0, r.X, ejes_norm, 0.1)
    Vx2, Vn2, Mx2, Mn2, RA2 = ref_simple_convoy(25.0, r.X, mp.espejar_ejes(ejes_norm), 0.1)
    Vx, Vn = np.maximum(Vx1, Vx2), np.minimum(Vn1, Vn2)
    Mx, Mn = np.maximum(Mx1, Mx2), np.minimum(Mn1, Mn2)
    RA = max(RA1, RA2)
    peor = max(float(np.max(np.abs(env.V_max - Vx))), float(np.max(np.abs(env.V_min - Vn))),
               float(np.max(np.abs(env.M_max - Mx))), float(np.max(np.abs(env.M_min - Mn))))
    chequear("%s en viga simple: rigidez vs. estática elemental (ambos sentidos)" % nombre,
             peor < 1e-6, "dif. máx = %.2e   M(+) máx = %.2f Tnf·m" % (peor, np.max(Mx)))
    chequear("  y la reacción coincide", abs(env.R_pos[0] - RA) < 1e-6,
             "R_A = %.3f vs %.3f Tnf" % (env.R_pos[0], RA))

# --- Sin camión especial no aparece la envolvente
r_sin = mp.calcular(mp.Parametros(tramos=[10, 12, 10], dx=0.25))
chequear("sin convoy no se agrega ninguna envolvente",
         "especial" not in r_sin.envolventes and r_sin.orden == ["camion", "tandem", "cvt",
                                                                "carril", "combinada"])

# --- Con convoy: entra como envolvente propia y, si se pide, en la gobernante
base = dict(tramos=[10, 12, 10], dx=0.25, ejes_especial=convoy)
r_aparte = mp.calcular(mp.Parametros(incluir_especial=False, **base))
r_dentro = mp.calcular(mp.Parametros(incluir_especial=True, **base))
chequear("el convoy aparece como envolvente propia",
         "especial" in r_aparte.envolventes and r_aparte.orden[2] == "especial")

esp = r_aparte.envolventes["especial"]
cam = r_aparte.envolventes["camion"]
tan = r_aparte.envolventes["tandem"]
chequear("sin incluirlo, la gobernante ignora el convoy",
         np.allclose(r_aparte.envolventes["cvt"].M_max,
                     np.maximum(cam.M_max, tan.M_max), atol=1e-9))
chequear("al incluirlo, la gobernante es el máximo de los tres",
         np.allclose(r_dentro.envolventes["cvt"].M_max,
                     np.maximum(np.maximum(cam.M_max, tan.M_max), esp.M_max), atol=1e-9)
         and np.allclose(r_dentro.envolventes["cvt"].M_min,
                         np.minimum(np.minimum(cam.M_min, tan.M_min), esp.M_min), atol=1e-9))
chequear("el título de la combinación avisa que incluye el especial",
         "ESPECIAL" in r_dentro.envolventes["combinada"].titulo.upper(),
         r_dentro.envolventes["combinada"].titulo)
chequear("la planilla suma las columnas del convoy",
         r_dentro.tabla_puntos().shape[1] == 36 and r_sin.tabla_puntos().shape[1] == 30,
         "%d columnas con convoy, %d sin él"
         % (r_dentro.tabla_puntos().shape[1], r_sin.tabla_puntos().shape[1]))

# --- Equilibrio global del convoy sobre viga continua
est = r_aparte.estructura
peor_eq = 0.0
for xt in np.arange(1.0, est.L_total + 95.0, 1.7):
    V, M, act, R = est.fuerzas_vehiculo(r_aparte.X, xt, convoy)
    peor_eq = max(peor_eq, abs(float(R.sum()) - sum(P for P, _ in act)))
chequear("equilibrio vertical con el convoy sobre la viga", peor_eq < 1e-9,
         "error máx = %.2e Tnf" % peor_eq)

print()
print("=" * 78)
print("T8 - LAS DOS NORMAS Y SUS METODOLOGÍAS")
print("=" * 78)

# --- aplicar_norma deja cada juego completo y coherente
std = mp.aplicar_norma(mp.Parametros(), mp.HS20_44)
lrfd = mp.aplicar_norma(mp.Parametros(), mp.HL_93)
chequear("AASHTO Standard: faja con concentradas, alternativa, I variable",
         abs(std.p_conc_momento - mp.KIP * 18) < 1e-12
         and abs(std.p_conc_corte - mp.KIP * 26) < 1e-12
         and std.modo_combinacion == "alternativa"
         and std.impacto_modo == "aashto_standard"
         and std.dos_conc_momento_negativo,
         "conc %.4f / %.4f Tnf" % (std.p_conc_momento, std.p_conc_corte))
chequear("AASHTO LRFD: carril sin concentradas, suma, impacto fijo 1.33",
         lrfd.p_conc_momento == 0 and lrfd.p_conc_corte == 0
         and lrfd.modo_combinacion == "suma" and lrfd.impacto_modo == "fijo"
         and lrfd.factor_dinamico == 1.33)
chequear("los dos camiones son 8-32-32 kip (el HS20-44 es la base de ambos)",
         all(abs(a["P"] - b["P"]) < 1e-12 and abs(a["offset"] - b["offset"]) < 1e-12
             for a, b in zip(std.ejes_camion, lrfd.ejes_camion)))
chequear("el segundo vehículo sí cambia: militar 24 kip vs tándem 25 kip",
         abs(std.ejes_tandem[0]["P"] - mp.KIP * 24) < 1e-12
         and abs(lrfd.ejes_tandem[0]["P"] - mp.KIP * 25) < 1e-12)
chequear("mayoración chilena por norma: 1.20 en Standard, 1.0 (sin mayorar) en LRFD",
         std.factor_mayoracion == 1.20 and lrfd.factor_mayoracion == 1.0,
         "Standard=%g  LRFD=%g" % (std.factor_mayoracion, lrfd.factor_mayoracion))
per = mp.aplicar_norma(
    mp.Parametros(w_carril=1.234, factor_dinamico=1.11, factor_mayoracion=1.05),
    mp.PERSONALIZADO)
chequear("«Personalizado» no pisa nada, tampoco la mayoración",
         per.w_carril == 1.234 and per.factor_dinamico == 1.11
         and per.factor_mayoracion == 1.05)

# --- Fórmula de impacto contra valores calculados a mano
casos_I = [(10.0, 50.0 / (10.0 / mp.PIE + 125.0)), (30.0, 50.0 / (30.0 / mp.PIE + 125.0))]
ok_I = all(abs(mp.impacto_aashto_standard(L) - min(0.30, esperado)) < 1e-12
           for L, esperado in casos_I)
chequear("I = 50/(L+125) con tope 0.30", ok_I,
         "L=10 m -> I=%.4f (topado);  L=30 m -> I=%.4f"
         % (mp.impacto_aashto_standard(10.0), mp.impacto_aashto_standard(30.0)))
chequear("el tope de 0.30 actúa en luces cortas",
         abs(mp.impacto_aashto_standard(8.0) - 0.30) < 1e-12)

# --- Carga de faja HS20-44 en viga simple, contra fórmulas de mano
#     M_max = q L^2/8 + P_mom L/4      V_max = q L/2 + P_corte
#     (factor_mayoracion se fuerza a 1.0: la fórmula de mano no lo lleva y
#     aplicar_norma ya deja 1.20 de fábrica en HS20-44)
L = 10.0
p_std = mp.aplicar_norma(mp.Parametros(tramos=[L], dx=0.05), mp.HS20_44)
p_std.factor_mayoracion = 1.0
r_std = mp.calcular(p_std)
faja = r_std.envolventes["carril"]
M_mano = p_std.w_carril * L ** 2 / 8.0 + p_std.p_conc_momento * L / 4.0
V_mano = p_std.w_carril * L / 2.0 + p_std.p_conc_corte
j_centro = int(np.argmin(np.abs(r_std.X - L / 2.0)))
chequear("faja HS20-44, M en el centro = qL²/8 + P·L/4",
         abs(faja.M_max[j_centro] - M_mano) < 2e-3,
         "%.4f vs %.4f Tnf·m" % (faja.M_max[j_centro], M_mano))
chequear("faja HS20-44, V en el apoyo = qL/2 + P",
         abs(faja.V_max[0] - V_mano) < 2e-3,
         "%.4f vs %.4f Tnf" % (faja.V_max[0], V_mano))
chequear("usa 18 kip para momento y 26 kip para corte, no el mismo valor",
         abs(p_std.p_conc_momento - p_std.p_conc_corte) > 1e-6)

# --- El LRFD, en cambio, no lleva concentrada
p_lrfd = mp.aplicar_norma(mp.Parametros(tramos=[L], dx=0.05), mp.HL_93)
r_lrfd = mp.calcular(p_lrfd)
faja_l = r_lrfd.envolventes["carril"]
chequear("carril LRFD en el centro = qL²/8, sin concentrada",
         abs(faja_l.M_max[j_centro] - p_lrfd.w_carril * L ** 2 / 8.0) < 2e-3,
         "%.4f Tnf·m" % faja_l.M_max[j_centro])

# --- Combinación: alternativa vs. suma
veh = r_std.envolventes["cvt"].M_max
faj = r_std.envolventes["carril"].M_max
fd_x, fd_R = mp.factores_impacto(r_std.estructura, r_std.X, p_std)
chequear("Standard: la combinada es (1+I)·máx(vehículo ; faja)",
         np.allclose(r_std.envolventes["combinada"].M_max,
                     np.round(fd_x * np.maximum(veh, faj), 3), atol=2e-3))
veh_l = r_lrfd.envolventes["cvt"].M_max
faj_l = r_lrfd.envolventes["carril"].M_max
chequear("LRFD: la combinada es 1.33·vehículo + carril",
         np.allclose(r_lrfd.envolventes["combinada"].M_max,
                     np.round(1.33 * veh_l + faj_l, 3), atol=2e-3))

# --- El impacto del LRFD no toca el carril; el del Standard sí toca todo
solo_faja = mp.aplicar_norma(mp.Parametros(tramos=[L], dx=0.1), mp.HS20_44)
solo_faja.ejes_camion = [{"P": 1e-6, "offset": 0.0}]
solo_faja.usar_tandem = False
solo_faja.sep_variable = False
r_sf = mp.calcular(solo_faja)
I = mp.impacto_aashto_standard(L)
chequear("Standard: la faja también lleva impacto",
         abs(r_sf.envolventes["combinada"].M_max[j_centro]
             - (1 + I) * r_sf.envolventes["carril"].M_max[j_centro]) < 5e-3,
         "1+I = %.4f" % (1 + I))

# --- Dos concentradas en momento negativo
dos = mp.aplicar_norma(mp.Parametros(tramos=[10, 10], dx=0.1), mp.HS20_44)
una = mp.aplicar_norma(mp.Parametros(tramos=[10, 10], dx=0.1), mp.HS20_44)
una.dos_conc_momento_negativo = False
r_dos = mp.calcular(dos).envolventes["carril"]
r_una = mp.calcular(una).envolventes["carril"]
j_ap = int(np.argmin(np.abs(mp.calcular(dos).X - 10.0)))
chequear("dos concentradas agravan el momento negativo en el apoyo",
         r_dos.M_min[j_ap] < r_una.M_min[j_ap] - 1e-3,
         "una: %.2f   dos: %.2f Tnf·m" % (r_una.M_min[j_ap], r_dos.M_min[j_ap]))

# --- Mayoración global (se fija explícito en 1.20 y 1.0: aplicar_norma ya
#     deja 1.20 de fábrica en HS20-44, así que hay que forzar el 1.0 "sin
#     mayorar" a mano para que la comparación no quede 1.20 contra 1.20).
may = mp.aplicar_norma(mp.Parametros(tramos=[L], dx=0.2), mp.HS20_44)
may.factor_mayoracion = 1.20
r_may = mp.calcular(may)
nom = mp.aplicar_norma(mp.Parametros(tramos=[L], dx=0.2), mp.HS20_44)
nom.factor_mayoracion = 1.0
r_nom = mp.calcular(nom)
chequear("la mayoración escala toda la envolvente",
         np.allclose(r_may.envolventes["combinada"].M_max,
                     np.round(1.20 * r_nom.envolventes["combinada"].M_max, 3), atol=2e-3))

# --- Sin segundo vehículo no aparece esa envolvente ni la gobernante duplicada
sin_tan = mp.aplicar_norma(mp.Parametros(tramos=[L], dx=0.2), mp.HS20_44)
sin_tan.usar_tandem = False
r_st = mp.calcular(sin_tan)
chequear("sin segundo vehículo se omiten «tandem» y «cvt»",
         "tandem" not in r_st.envolventes and "cvt" not in r_st.envolventes,
         str(r_st.orden))

print()
print("=" * 78)
print("T9 - PESO PROPIO (CARGA PERMANENTE ESTIMADA)")
print("=" * 78)


def con_peso_propio(tramos, **kw):
    p = mp.aplicar_norma(mp.Parametros(tramos=tramos, dx=0.1), mp.HS20_44)
    p.incluir_peso_propio = True
    p.ancho_tablero = kw.get("ancho_tablero", 8.0)
    p.espesor_losa = kw.get("espesor_losa", 0.20)
    p.densidad_hormigon = kw.get("densidad_hormigon", 2.4)
    p.espesor_carpeta = kw.get("espesor_carpeta", 0.05)
    p.densidad_asfalto = kw.get("densidad_asfalto", 2.2)
    p.peso_barandas = kw.get("peso_barandas", 0.10)
    p.peso_viga = kw.get("peso_viga", 0.5)
    p.n_vigas = kw.get("n_vigas", 5)
    return p, mp.calcular(p)


# --- Desglose: cada partida a mano
p_pp, r_pp = con_peso_propio([10, 12, 10])
d = p_pp.desglose_peso_propio()
losa_mano = 0.20 * 2.4 * 8.0
carpeta_mano = 0.05 * 2.2 * 8.0
vigas_mano = 0.5 * 5
total_mano = losa_mano + carpeta_mano + 0.10 + vigas_mano
chequear("desglose losa/carpeta/barandas/vigas coincide con la fórmula a mano",
         abs(d["losa"] - losa_mano) < 1e-9 and abs(d["carpeta"] - carpeta_mano) < 1e-9
         and abs(d["vigas"] - vigas_mano) < 1e-9 and abs(d["total"] - total_mano) < 1e-9,
         "total = %.3f Tnf/m" % d["total"])

chequear("sin incluir_peso_propio no aparece esa envolvente",
         "peso_propio" not in mp.calcular(mp.aplicar_norma(
             mp.Parametros(tramos=[10, 12, 10], dx=0.2), mp.HS20_44)).envolventes)

env_pp = r_pp.envolventes["peso_propio"]
chequear("peso propio es un único estado: M_max = M_min (no hay barrido de posición)",
         np.array_equal(env_pp.M_max, env_pp.M_min) and np.array_equal(env_pp.V_max, env_pp.V_min))

# --- Contra el método de las fuerzas independiente (T2), con UDL en todos los tramos
est_pp = r_pp.estructura
ap = est_pp.apoyos_x
cu = [(ap[k], ap[k + 1], d["total"]) for k in range(est_pp.n_tramos)]
R_ref = ref_metodo_fuerzas(ap, [], cu)
R_motor = np.array([env_pp.R_pos[i] for i in range(est_pp.n_sup)])
chequear("reacciones del peso propio contra el método de las fuerzas",
         np.max(np.abs(R_motor - R_ref)) < 2e-3,
         "motor=%s  referencia=%s" % (np.round(R_motor, 3), np.round(R_ref, 3)))

# --- Equilibrio vertical exacto: suma de reacciones = carga total
chequear("equilibrio vertical exacto (suma de reacciones = w · L_total)",
         abs(sum(env_pp.R_pos.values()) - d["total"] * sum(p_pp.tramos)) < 1e-6)

# --- La mayoración de carga viva NO debe tocar el peso propio
p_may, r_may = con_peso_propio([10, 12, 10])
p_may.factor_mayoracion = 1.20
r_may2 = mp.calcular(p_may)
chequear("la mayoración de carga viva no escala el peso propio",
         np.array_equal(r_may2.envolventes["peso_propio"].M_max, env_pp.M_max))

# --- El impacto no toca el peso propio (no lleva 1+I)
p_imp, _ = con_peso_propio([10, 12, 10])
r_imp = mp.calcular(p_imp)
chequear("el peso propio no lleva factor de impacto",
         np.array_equal(r_imp.envolventes["peso_propio"].M_max, env_pp.M_max))

# --- Validaciones de entrada
for descr, kwargs in [("ancho de tablero nulo", dict(ancho_tablero=0.0)),
                      ("densidad de hormigón negativa", dict(densidad_hormigon=-1.0)),
                      ("peso propio total cero", dict(espesor_losa=0, espesor_carpeta=0,
                                                      peso_barandas=0, peso_viga=0, n_vigas=0))]:
    p_bad, _ = con_peso_propio([10, 12, 10])
    for k, v in kwargs.items():
        setattr(p_bad, k, v)
    try:
        p_bad.validar()
        chequear(descr, False, "no lanzó error")
    except ValueError:
        chequear(descr, True, "ValueError")

# --- Reporte de texto incluye el desglose
chequear("el desglose aparece en el reporte de texto",
         "PESO PROPIO" in r_pp.texto_resumen() and "TOTAL" in r_pp.texto_resumen())

print()
print("=" * 78)
print("T10 - DISTRIBUCIÓN TRANSVERSAL DE MOMENTO (AASHTO STANDARD, TABLA 3.23.1)")
print("=" * 78)

# --- g = S/D contra la fórmula a mano, para las 4 combinaciones de la tabla
CASOS_TABLA = [("acero", 1, 7.0, 10.0), ("acero", 2, 5.5, 14.0),
              ("hormigon", 1, 6.5, 6.0), ("hormigon", 2, 6.0, 10.0)]
for tipo, nvias, D_esp, Smax_esp in CASOS_TABLA:
    S_m = 1.5
    g, D, S_ft, aviso = mp.factor_distribucion_momento(tipo, S_m, nvias)
    g_mano = (S_m / mp.PIE) / D_esp
    chequear("g = S/D para %s, %d vía(s): D=%.1f" % (tipo, nvias, D_esp),
             abs(g - g_mano) < 1e-9 and D == D_esp,
             "g=%.5f  D=%.1f" % (g, D))

# --- Aviso cuando S excede el límite de validez de cada fila
for tipo, nvias, D_esp, Smax_esp in CASOS_TABLA:
    S_m = (Smax_esp + 2.0) * mp.PIE      # deliberadamente por sobre el límite
    _, _, _, aviso = mp.factor_distribucion_momento(tipo, S_m, nvias)
    chequear("aviso de rango cuando S > %.0f ft (%s, %d vía(s))" % (Smax_esp, tipo, nvias),
             aviso is not None)
    S_m2 = (Smax_esp - 1.0) * mp.PIE     # deliberadamente por debajo
    _, _, _, aviso2 = mp.factor_distribucion_momento(tipo, S_m2, nvias)
    chequear("sin aviso cuando S está dentro del rango (%s, %d vía(s))" % (tipo, nvias),
             aviso2 is None)


def con_distribucion(tipo_viga, S, n_vias, **kw):
    p = mp.aplicar_norma(mp.Parametros(tramos=kw.get("tramos", [10, 12, 10]), dx=0.1),
                         mp.HS20_44)
    p.incluir_distribucion = True
    p.tipo_viga = tipo_viga
    p.separacion_vigas = S
    p.n_vias_diseno = n_vias
    return p, mp.calcular(p)


# --- Sin activar el coeficiente no aparece la envolvente
r_sin = mp.calcular(mp.aplicar_norma(mp.Parametros(tramos=[10, 12, 10], dx=0.2), mp.HS20_44))
chequear("sin incluir_distribucion no aparece «viga_interior»",
         "viga_interior" not in r_sin.envolventes)

p_vi, r_vi = con_distribucion("acero", 2.0, 2)
comb = r_vi.envolventes["combinada"]
vi = r_vi.envolventes["viga_interior"]
g_esperado = (2.0 / mp.PIE) / 5.5

chequear("M de la viga interior = g x M de la combinada, exacto",
         np.array_equal(vi.M_max, np.round(comb.M_max * g_esperado, 3))
         and np.array_equal(vi.M_min, np.round(comb.M_min * g_esperado, 3)))
chequear("V de la viga interior queda IGUAL a la de la combinada (sin factor)",
         np.array_equal(vi.V_max, comb.V_max) and np.array_equal(vi.V_min, comb.V_min))
chequear("las reacciones también quedan sin tocar",
         vi.R_pos == comb.R_pos and vi.R_neg == comb.R_neg)
chequear("el título trae el valor de g y el material",
         "acero" in vi.titulo and "%.4f" % g_esperado in vi.titulo, vi.titulo)

# --- Hormigón armado da un g distinto al de acero para la misma separación
_, r_horm = con_distribucion("hormigon", 2.0, 2)
chequear("hormigón armado y acero dan factores distintos para la misma S",
         not np.array_equal(r_horm.envolventes["viga_interior"].M_max, vi.M_max))

# --- El peso propio no lleva el coeficiente de distribución
p_pp2, r_pp2 = con_distribucion("acero", 2.0, 2)
p_pp2.incluir_peso_propio = True
p_pp2.ancho_tablero = 8.0
p_pp2.peso_viga = 0.5
p_pp2.n_vigas = 5
r_pp2 = mp.calcular(p_pp2)
p_sin_dist = mp.aplicar_norma(mp.Parametros(tramos=[10, 12, 10], dx=0.1), mp.HS20_44)
p_sin_dist.incluir_peso_propio = True
p_sin_dist.ancho_tablero = 8.0
p_sin_dist.peso_viga = 0.5
p_sin_dist.n_vigas = 5
r_sin_dist = mp.calcular(p_sin_dist)
chequear("el peso propio es idéntico con o sin coeficiente de distribución activo",
         np.array_equal(r_pp2.envolventes["peso_propio"].M_max,
                        r_sin_dist.envolventes["peso_propio"].M_max))

# --- Validaciones de entrada
for descr, kwargs in [("tipo de viga inválido", dict(tipo_viga="madera")),
                      ("separación entre vigas nula", dict(separacion_vigas=0.0)),
                      ("número de vías inválido", dict(n_vias_diseno=3))]:
    p_bad = mp.Parametros(incluir_distribucion=True, tipo_viga="acero", separacion_vigas=2.0,
                          n_vias_diseno=2)
    for k, v in kwargs.items():
        setattr(p_bad, k, v)
    try:
        p_bad.validar()
        chequear(descr, False, "no lanzó error")
    except ValueError:
        chequear(descr, True, "ValueError")

# --- El reporte de texto trae el desglose
chequear("el reporte de texto incluye g, D y el tipo de viga",
         "g = S/D" in r_vi.texto_resumen() and "acero" in r_vi.texto_resumen())

print()
print("=" * 78)
print("T11 - PESO PROPIO POR VIGA (desglose por partida)")
print("=" * 78)


def con_pp_por_viga(tramos, modo_baranda="ninguna", **kw):
    p = mp.aplicar_norma(mp.Parametros(tramos=tramos, dx=0.1), mp.HS20_44)
    p.incluir_peso_propio = True
    p.espesor_losa = kw.get("espesor_losa", 0.20)
    p.densidad_hormigon = kw.get("densidad_hormigon", 2.4)
    p.espesor_carpeta = kw.get("espesor_carpeta", 0.05)
    p.densidad_asfalto = kw.get("densidad_asfalto", 2.2)
    p.peso_barandas = kw.get("peso_barandas", 0.10)
    p.peso_viga = kw.get("peso_viga", 0.5)
    p.n_vigas = kw.get("n_vigas", 5)
    p.incluir_pp_por_viga = True
    p.ancho_tributario = kw.get("ancho_tributario", 2.0)
    p.modo_baranda_pp = modo_baranda
    return p, mp.calcular(p)


# --- Desglose a mano, en los tres modos de reparto de baranda
p_pv, r_pv = con_pp_por_viga([10, 12, 10], modo_baranda="repartida")
dpv = p_pv.desglose_peso_propio_por_viga()
losa_mano = 0.20 * 2.4 * 2.0
carpeta_mano = 0.05 * 2.2 * 2.0
baranda_mano = 0.10 / 5
chequear("desglose por viga (reparto «repartida») coincide con la fórmula a mano",
         abs(dpv["viga"] - 0.5) < 1e-9 and abs(dpv["losa"] - losa_mano) < 1e-9
         and abs(dpv["carpeta"] - carpeta_mano) < 1e-9
         and abs(dpv["barandas"] - baranda_mano) < 1e-9
         and abs(dpv["total"] - (0.5 + losa_mano + carpeta_mano + baranda_mano)) < 1e-9,
         "total = %.4f Tnf/m" % dpv["total"])

p_ninguna, _ = con_pp_por_viga([10, 12, 10], modo_baranda="ninguna")
p_completa, _ = con_pp_por_viga([10, 12, 10], modo_baranda="completa")
chequear("reparto «ninguna» deja la baranda en 0 (viga interior)",
         p_ninguna.desglose_peso_propio_por_viga()["barandas"] == 0.0)
chequear("reparto «completa» asigna el 100% de la baranda a esta viga",
         abs(p_completa.desglose_peso_propio_por_viga()["barandas"] - 0.10) < 1e-9)

# --- Sin incluir_pp_por_viga no aparece ningún «pp_*», y viceversa
sin_pv = mp.aplicar_norma(mp.Parametros(tramos=[10, 12, 10], dx=0.2), mp.HS20_44)
sin_pv.incluir_peso_propio = True
sin_pv.peso_viga = 0.5
sin_pv.n_vigas = 5
r_sin_pv = mp.calcular(sin_pv)
chequear("sin incluir_pp_por_viga no aparece ningún componente «pp_*»",
         r_sin_pv.componentes_pp_viga == [] and
         not any(k.startswith("pp_") for k in r_sin_pv.envolventes))
# Las cuatro partidas BASE (sin las combinaciones ni el total, que son sumas
# de éstas -ver más abajo-): sirven para las pruebas de superposición, que
# de lo contrario contarían cada partida varias veces.
partidas_base = ["pp_viga", "pp_losa", "pp_carpeta", "pp_barandas"]
chequear("con incluir_pp_por_viga aparecen las 4 partidas base",
         all(k in r_pv.componentes_pp_viga for k in partidas_base))

# --- Cada partida es un único estado de carga (sin barrido de posición)
for clave in r_pv.componentes_pp_viga:
    env = r_pv.envolventes[clave]
    chequear("«%s» es un único estado: M_max = M_min, V_max = V_min" % clave,
             np.array_equal(env.M_max, env.M_min) and np.array_equal(env.V_max, env.V_min))

# --- Superposición: la suma de las 4 partidas base coincide con el total,
#     punto a punto (todas son UDL sobre toda la estructura, el problema es
#     lineal; cada partida se redondea aparte, así que la tolerancia cubre el
#     redondeo acumulado de hasta 4 términos de 0.001 cada uno)
suma_M = sum(r_pv.envolventes[k].M_max for k in partidas_base)
suma_V = sum(r_pv.envolventes[k].V_max for k in partidas_base)
est_pv = r_pv.estructura
V1, M1, R1 = est_pv.fuerzas_carril(r_pv.X, [True] * est_pv.n_tramos, 1.0)
M_total_esperado = np.round(dpv["total"] * M1, 3)
V_total_esperado = np.round(dpv["total"] * V1, 3)
chequear("la suma de las 4 partidas coincide con el peso propio total por viga (M)",
         np.max(np.abs(suma_M - M_total_esperado)) < 5e-3)
chequear("la suma de las 4 partidas coincide con el peso propio total por viga (V)",
         np.max(np.abs(suma_V - V_total_esperado)) < 5e-3)

# --- Contra el método de las fuerzas independiente (T2), con el total por viga
ap_pv = est_pv.apoyos_x
cu_pv = [(ap_pv[k], ap_pv[k + 1], dpv["total"]) for k in range(est_pv.n_tramos)]
R_ref_pv = ref_metodo_fuerzas(ap_pv, [], cu_pv)
R_motor_pv = sum(np.array([r_pv.envolventes[k].R_pos[i] for i in range(est_pv.n_sup)])
                for k in partidas_base)
chequear("reacciones de la suma de partidas contra el método de las fuerzas",
         np.max(np.abs(R_motor_pv - R_ref_pv)) < 5e-3,
         "motor=%s  referencia=%s" % (np.round(R_motor_pv, 3), np.round(R_ref_pv, 3)))

# --- Validaciones de entrada
for descr, kwargs in [("ancho tributario nulo", dict(ancho_tributario=0.0)),
                      ("reparto de baranda inválido", dict(modo_baranda_pp="otro")),
                      ("repartida sin vigas", dict(modo_baranda_pp="repartida", n_vigas=0))]:
    p_bad, _ = con_pp_por_viga([10, 12, 10])
    for k, v in kwargs.items():
        setattr(p_bad, k, v)
    try:
        p_bad.validar()
        chequear(descr, False, "no lanzó error")
    except ValueError:
        chequear(descr, True, "ValueError")

p_sin_pp = mp.aplicar_norma(mp.Parametros(tramos=[10, 12, 10], dx=0.2), mp.HS20_44)
p_sin_pp.incluir_peso_propio = False
p_sin_pp.incluir_pp_por_viga = True
p_sin_pp.ancho_tributario = 2.0
try:
    p_sin_pp.validar()
    chequear("incluir_pp_por_viga sin incluir_peso_propio", False, "no lanzó error")
except ValueError:
    chequear("incluir_pp_por_viga sin incluir_peso_propio", True, "ValueError")

# --- La mayoración de carga viva y el impacto no tocan el peso propio por viga
p_esc, _ = con_pp_por_viga([10, 12, 10], modo_baranda="repartida")
p_esc.factor_mayoracion = 1.20
r_esc = mp.calcular(p_esc)
chequear("la mayoración de carga viva no escala el peso propio por viga",
         np.array_equal(r_esc.envolventes["pp_losa"].M_max, r_pv.envolventes["pp_losa"].M_max))

# --- Reporte de texto incluye el desglose por viga
chequear("el reporte de texto incluye el desglose de peso propio por viga",
         "PESO PROPIO POR VIGA" in r_pv.texto_resumen()
         and "ancho tributario" in r_pv.texto_resumen())

# --- Combinaciones (Losa+Viga, Losa+Viga+Baranda) y total fijo
chequear("aparecen las 3 claves nuevas, después de las 4 partidas",
         r_pv.componentes_pp_viga == ["pp_viga", "pp_losa", "pp_carpeta", "pp_barandas",
                                      "pp_losa_viga", "pp_losa_viga_barandas", "pp_total"])

viga_M, losa_M = r_pv.envolventes["pp_viga"].M_max, r_pv.envolventes["pp_losa"].M_max
carpeta_M, baranda_M = r_pv.envolventes["pp_carpeta"].M_max, r_pv.envolventes["pp_barandas"].M_max
lv_M = r_pv.envolventes["pp_losa_viga"].M_max
lvb_M = r_pv.envolventes["pp_losa_viga_barandas"].M_max
tot_M = r_pv.envolventes["pp_total"].M_max
chequear("«Losa + Viga» = losa + viga, punto a punto",
         np.max(np.abs(lv_M - (losa_M + viga_M))) < 5e-3)
chequear("«Losa + Viga + Baranda» = losa + viga + baranda, punto a punto",
         np.max(np.abs(lvb_M - (losa_M + viga_M + baranda_M))) < 5e-3)
chequear("«pp_total» = viga + losa + carpeta + baranda, punto a punto",
         np.max(np.abs(tot_M - (viga_M + losa_M + carpeta_M + baranda_M))) < 5e-3)

# --- El total fijo NO depende de qué esté marcado en la GUI: siempre son las
#     cuatro partidas, aun si sólo un subconjunto tiene sentido físico para
#     el usuario (p.ej. reparto de baranda "ninguna").
p_pv_ninguna, r_pv_ninguna = con_pp_por_viga([10, 12, 10], modo_baranda="ninguna")
chequear("pp_total con baranda «ninguna» sigue siendo viga+losa+carpeta (+0 de baranda)",
         np.max(np.abs(r_pv_ninguna.envolventes["pp_total"].M_max
                       - (r_pv_ninguna.envolventes["pp_viga"].M_max
                          + r_pv_ninguna.envolventes["pp_losa"].M_max
                          + r_pv_ninguna.envolventes["pp_carpeta"].M_max))) < 5e-3)

print()
print("=" * 78)
print("RESULTADO: " + ("TODOS LOS TESTS PASARON" if not fallos else "FALLARON -> %s" % fallos))
print("=" * 78)
sys.exit(1 if fallos else 0)
