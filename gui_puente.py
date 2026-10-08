# -*- coding: utf-8 -*-
"""
Interfaz gráfica para el análisis de envolventes de carga viva AASHTO HL-93
en vigas continuas y vigas Gerber.

Ejecutar con:   py gui_puente.py
"""

import json
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import motor_puente as mp
import graficos_puente as gp

APP = "Envolventes de Carga Viva - AASHTO Standard / LRFD"
MAX_FILAS_TABLA = 6000          # tope de filas mostradas en la tabla punto a punto

SIN_RESULTADOS = "(ejecute el cálculo)"

# Etiquetas legibles para las dos metodologías, y su valor en el motor
COMBINACIONES = {"alternativa": "Alternativos (gobierna el mayor)",
                 "suma": "Se suman"}
IMPACTOS = {"aashto_standard": "I = 50/(L+125) ≤ 0.30",
            "fijo": "Fijo (1 + IM)"}

TIPOS_VIGA = {"hormigon": "Hormigón armado", "acero": "Acero"}
VIGAS_DIST = {"interior": "Viga interior", "exterior": "Viga exterior"}
VIAS_DISENO = {1: "1 vía de diseño", 2: "2 o más vías de diseño"}
MODOS_BARANDA = {
    "ninguna": "No incluir (viga interior)",
    "completa": "100% a esta viga (viga de borde)",
    "repartida": "Repartida entre todas las vigas",
}


def _clave_de(diccionario, etiqueta, por_defecto):
    for clave, texto in diccionario.items():
        if texto == etiqueta:
            return clave
    return por_defecto

def parsear_lista(texto, nombre, permitir_vacio=False):
    """'10, 12, 10' -> [10.0, 12.0, 10.0]. También acepta coma decimal chilena:
    '10,5, 12,3, 10,0' -> [10.5, 12.3, 10.0]. Separadores: espacio, «;» o «,».

    Una coma pegada a un dígito («10,5») es coma decimal y se convierte a punto
    ANTES de partir la lista; el resto de las comas (seguidas de espacio, «;»,
    otra coma o fin de texto) son separadores. Así no hay ambigüedad entre
    "coma que separa valores" y "coma que separa los decimales de un valor".
    """
    texto = (texto or "").strip()
    if not texto:
        if permitir_vacio:
            return []
        raise ValueError("El campo «%s» está vacío." % nombre)
    marcado = re.sub(r",(?=\d)", ".", texto)
    partes = [p for p in re.split(r"[,;\s]+", marcado) if p]
    valores = []
    for p in partes:
        try:
            valores.append(float(p))
        except ValueError:
            if p.count(".") > 1:
                raise ValueError(
                    "En «%s» no se entiende «%s»: separe los valores con espacio o «;» "
                    "(la coma pegada a un valor se interpreta como coma decimal)."
                    % (nombre, p))
            raise ValueError("En «%s» no se entiende el valor: %s" % (nombre, p))
    return valores


def parsear_num(texto, nombre):
    try:
        return float(str(texto).strip().replace(",", "."))
    except ValueError:
        raise ValueError("En «%s» no se entiende el valor: %s" % (nombre, texto))


# =============================================================================
# TABLA DE EJES REUTILIZABLE
# =============================================================================
class TablaEjes(ttk.LabelFrame):
    """Tabla editable de ejes: carga P y separación (offset) respecto al eje guía."""

    def __init__(self, master, titulo, ejes, al_cambiar=None):
        super().__init__(master, text=titulo, padding=6)
        self.al_cambiar = al_cambiar
        # Cuando el barrido de separación variable está activo, la celda de
        # offset del eje trasero muestra el RANGO recorrido ("-8.53 a -13.41")
        # en vez de un número editable. self._offset_real guarda, por iid, el
        # valor numérico verdadero que usa el cálculo (el punto de partida:
        # la posición con la separación mínima); la celda es sólo texto.
        self._offset_real = {}
        self.tree = ttk.Treeview(self, columns=("P", "off"), show="headings", height=4)
        self.tree.heading("P", text="P (Tnf)")
        self.tree.heading("off", text="Offset (m)")
        # stretch=False en ambas: si no, la columna "P" (stretch=True por
        # defecto) absorbe el espacio libre y empuja "off" fuera del panel.
        self.tree.column("P", width=85, anchor="e", stretch=False)
        self.tree.column("off", width=130, anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._al_seleccionar)

        self.var_p = tk.StringVar()
        self.var_o = tk.StringVar()
        ttk.Label(self, text="P").grid(row=1, column=0, sticky="e", pady=(6, 0))
        ttk.Entry(self, textvariable=self.var_p, width=8).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(self, text="Offset").grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(self, textvariable=self.var_o, width=8).grid(row=1, column=3, sticky="w", pady=(6, 0))

        barra = ttk.Frame(self)
        barra.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Button(barra, text="Agregar", width=9, command=self.agregar).pack(side="left")
        ttk.Button(barra, text="Modificar", width=10, command=self.modificar).pack(side="left", padx=3)
        ttk.Button(barra, text="Eliminar", width=9, command=self.eliminar).pack(side="left")
        self.columnconfigure(1, weight=1)

        self.cargar(ejes)

    def _al_seleccionar(self, _=None):
        sel = self.tree.selection()
        if sel:
            p, o = self.tree.item(sel[0], "values")
            self.var_p.set(p)
            # Si la celda muestra un rango, se edita el valor real de partida,
            # no el texto del rango (que no es un número).
            o_real = self._offset_real.get(sel[0])
            self.var_o.set("%g" % o_real if o_real is not None else o)

    def _notificar(self):
        if self.al_cambiar is not None:
            self.al_cambiar()

    def cargar(self, ejes):
        self.tree.delete(*self.tree.get_children())
        self._offset_real.clear()
        for e in ejes:
            self.tree.insert("", "end", values=("%g" % e["P"], "%g" % e["offset"]))
        self._notificar()

    def mostrar_rango(self, iid, texto, valor_real):
        """Muestra `texto` (p. ej. "-8.53 a -13.41") en la celda de offset de
        `iid`, conservando `valor_real` como el número que usa el cálculo."""
        p, _ = self.tree.item(iid, "values")
        self.tree.item(iid, values=(p, texto))
        self._offset_real[iid] = valor_real

    def limpiar_rangos(self):
        """Restituye el número real en toda celda que estuviera mostrando un rango."""
        for iid, valor in list(self._offset_real.items()):
            if self.tree.exists(iid):
                p, _ = self.tree.item(iid, "values")
                self.tree.item(iid, values=(p, "%g" % valor))
        self._offset_real.clear()

    def _leer_campos(self):
        return parsear_num(self.var_p.get(), "P"), parsear_num(self.var_o.get(), "Offset")

    def agregar(self):
        try:
            p, o = self._leer_campos()
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        self.tree.insert("", "end", values=("%g" % p, "%g" % o))
        self._notificar()

    def modificar(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP, "Seleccione primero una fila de la tabla.", parent=self)
            return
        try:
            p, o = self._leer_campos()
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        self.tree.item(sel[0], values=("%g" % p, "%g" % o))
        self._offset_real.pop(sel[0], None)   # edición explícita: ya no es un rango
        self._notificar()

    def eliminar(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP, "Seleccione primero una fila de la tabla.", parent=self)
            return
        if len(self.tree.get_children()) <= 1:
            messagebox.showwarning(APP, "Debe quedar al menos un eje.", parent=self)
            return
        self.tree.delete(sel[0])
        self._offset_real.pop(sel[0], None)
        self._notificar()

    def ejes(self):
        salida = []
        for iid in self.tree.get_children():
            p, o = self.tree.item(iid, "values")
            o_real = self._offset_real.get(iid)
            salida.append({"P": float(p), "offset": o_real if o_real is not None else float(o)})
        return salida


# =============================================================================
# CAMIÓN ESPECIAL: CUALQUIER NÚMERO DE EJES
# =============================================================================
class DialogoGrupo(tk.Toplevel):
    """Pide un grupo de ejes iguales: cantidad, carga y separaciones."""

    def __init__(self, master, hay_ejes):
        super().__init__(master)
        self.title("Agregar grupo de ejes")
        self.transient(master)
        self.resizable(False, False)
        self.resultado = None

        cuerpo = ttk.Frame(self, padding=12)
        cuerpo.pack(fill="both", expand=True)
        ttk.Label(cuerpo, text="Repite un mismo eje varias veces. Para un vano largo entre\n"
                               "grupos (por ejemplo 18 m), póngalo en «separación al eje\n"
                               "anterior».", foreground="gray").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.var_n = tk.StringVar(value="16")
        self.var_p = tk.StringVar(value="11.6")
        self.var_s = tk.StringVar(value="1.5")
        self.var_prev = tk.StringVar(value="18" if hay_ejes else "0")

        campos = [("Cantidad de ejes", self.var_n),
                  ("Carga por eje (Tnf)", self.var_p),
                  ("Separación entre ellos (m)", self.var_s)]
        if hay_ejes:
            campos.append(("Separación al eje anterior (m)", self.var_prev))
        for i, (etq, var) in enumerate(campos, start=1):
            ttk.Label(cuerpo, text=etq).grid(row=i, column=0, sticky="e", padx=(0, 8), pady=3)
            ttk.Entry(cuerpo, textvariable=var, width=12).grid(row=i, column=1, sticky="w", pady=3)

        barra = ttk.Frame(self, padding=(12, 0, 12, 12))
        barra.pack(fill="x")
        ttk.Button(barra, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(barra, text="Agregar", command=self._aceptar).pack(side="right", padx=6)

        self.hay_ejes = hay_ejes
        self.grab_set()
        self.bind("<Return>", lambda _: self._aceptar())
        master.wait_window(self)

    def _aceptar(self):
        try:
            n = int(round(parsear_num(self.var_n.get(), "Cantidad de ejes")))
            p = parsear_num(self.var_p.get(), "Carga por eje")
            s = parsear_num(self.var_s.get(), "Separación entre ellos")
            prev = parsear_num(self.var_prev.get(), "Separación al eje anterior") if self.hay_ejes else 0.0
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        if n < 1:
            messagebox.showerror(APP, "La cantidad de ejes debe ser al menos 1.", parent=self)
            return
        if p <= 0:
            messagebox.showerror(APP, "La carga por eje debe ser positiva.", parent=self)
            return
        if n > 1 and s <= 0:
            messagebox.showerror(APP, "La separación entre ejes del grupo debe ser mayor que cero.",
                                 parent=self)
            return
        if self.hay_ejes and prev <= 0:
            messagebox.showerror(APP, "La separación al eje anterior debe ser mayor que cero.",
                                 parent=self)
            return
        self.resultado = {"n": n, "P": p, "sep": s, "prev": prev}
        self.destroy()


class TablaEspecial(ttk.LabelFrame):
    """Convoy de cualquier número de ejes, descrito por cargas y separaciones.

    La primera fila no tiene separación anterior (es el eje delantero); de ahí
    en adelante cada fila guarda su distancia al eje que la precede.
    """

    def __init__(self, master, al_cambiar=None):
        super().__init__(master, text="Camión especial (convoy de N ejes)", padding=6)
        self.al_cambiar = al_cambiar

        cont = ttk.Frame(self)
        cont.grid(row=0, column=0, columnspan=5, sticky="ew")
        self.tree = ttk.Treeview(cont, columns=("n", "P", "sep"), show="headings", height=6)
        for c, t, an in (("n", "#", 40), ("P", "P (Tnf)", 80), ("sep", "Sep. anterior (m)", 120)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=an, anchor="e")
        sb = ttk.Scrollbar(cont, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._al_seleccionar)

        self.var_p = tk.StringVar()
        self.var_s = tk.StringVar()
        ttk.Label(self, text="P").grid(row=1, column=0, sticky="e", pady=(6, 0))
        ttk.Entry(self, textvariable=self.var_p, width=8).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(self, text="Sep.").grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(self, textvariable=self.var_s, width=8).grid(row=1, column=3, sticky="w", pady=(6, 0))

        b1 = ttk.Frame(self)
        b1.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        ttk.Button(b1, text="Agregar grupo...", command=self.agregar_grupo).pack(side="left")
        ttk.Button(b1, text="Agregar eje", width=11, command=self.agregar).pack(side="left", padx=3)
        ttk.Button(b1, text="Importar Excel...", command=self.importar_excel).pack(side="left")
        b2 = ttk.Frame(self)
        b2.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(3, 0))
        ttk.Button(b2, text="Modificar", width=10, command=self.modificar).pack(side="left")
        ttk.Button(b2, text="Eliminar", width=9, command=self.eliminar).pack(side="left", padx=3)
        ttk.Button(b2, text="Limpiar todo", width=12, command=self.limpiar).pack(side="left")

        self.lbl = ttk.Label(self, text="", foreground="#123a8a")
        self.lbl.grid(row=4, column=0, columnspan=5, sticky="w", pady=(5, 0))
        self.columnconfigure(1, weight=1)
        self._renumerar()

    # ------------------------------------------------------------------ datos
    def _al_seleccionar(self, _=None):
        sel = self.tree.selection()
        if sel:
            _, p, s = self.tree.item(sel[0], "values")
            self.var_p.set(p)
            self.var_s.set(s)

    def _renumerar(self):
        for i, iid in enumerate(self.tree.get_children()):
            vals = list(self.tree.item(iid, "values"))
            vals[0] = str(i + 1)
            if i == 0:
                vals[2] = "-"                      # el eje delantero no tiene anterior
            elif vals[2] in ("-", ""):
                vals[2] = "1.5"
            self.tree.item(iid, values=vals)
        self._resumen()
        if self.al_cambiar is not None:
            self.al_cambiar()

    def _resumen(self):
        filas = self.tree.get_children()
        if not filas:
            self.lbl.configure(text="Sin ejes: el camión especial no se analiza.")
            return
        cargas, seps = self._crudos()
        self.lbl.configure(text="%d ejes   |   largo %.2f m   |   carga total %.2f Tnf"
                                % (len(cargas), sum(seps), sum(cargas)))

    def _crudos(self):
        cargas, seps = [], []
        for i, iid in enumerate(self.tree.get_children()):
            _, p, s = self.tree.item(iid, "values")
            cargas.append(float(p))
            if i > 0:
                seps.append(float(s))
        return cargas, seps

    def ejes(self):
        """Lista de ejes {P, offset} lista para el motor. [] si la tabla está vacía."""
        cargas, seps = self._crudos()
        if not cargas:
            return []
        return mp.ejes_desde_separaciones(cargas, seps)

    def cargar(self, ejes):
        self.tree.delete(*self.tree.get_children())
        ejes = mp.normalizar_ejes(ejes)
        for i, e in enumerate(ejes):
            sep = "-" if i == 0 else "%g" % (ejes[i - 1]["offset"] - e["offset"])
            self.tree.insert("", "end", values=(str(i + 1), "%g" % e["P"], sep))
        self._renumerar()

    # ---------------------------------------------------------------- acciones
    def agregar(self):
        try:
            p = parsear_num(self.var_p.get(), "P")
            s = parsear_num(self.var_s.get(), "Sep.") if self.tree.get_children() else 0.0
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        if p <= 0:
            messagebox.showerror(APP, "La carga del eje debe ser positiva.", parent=self)
            return
        if self.tree.get_children() and s <= 0:
            messagebox.showerror(APP, "La separación al eje anterior debe ser mayor que cero.",
                                 parent=self)
            return
        self.tree.insert("", "end", values=("", "%g" % p, "%g" % s))
        self._renumerar()

    def agregar_grupo(self):
        dlg = DialogoGrupo(self.winfo_toplevel(), bool(self.tree.get_children()))
        if dlg.resultado is None:
            return
        g = dlg.resultado
        for k in range(g["n"]):
            primero = not self.tree.get_children()
            if primero:
                sep = "-"
            elif k == 0:
                sep = "%g" % g["prev"]
            else:
                sep = "%g" % g["sep"]
            self.tree.insert("", "end", values=("", "%g" % g["P"], sep))
        self._renumerar()
        self.tree.see(self.tree.get_children()[-1])

    def modificar(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP, "Seleccione primero una fila de la tabla.", parent=self)
            return
        try:
            p = parsear_num(self.var_p.get(), "P")
            s = parsear_num(self.var_s.get(), "Sep.")
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        es_primero = sel[0] == self.tree.get_children()[0]
        if p <= 0:
            messagebox.showerror(APP, "La carga del eje debe ser positiva.", parent=self)
            return
        if not es_primero and s <= 0:
            messagebox.showerror(APP, "La separación al eje anterior debe ser mayor que cero.",
                                 parent=self)
            return
        self.tree.item(sel[0], values=("", "%g" % p, "-" if es_primero else "%g" % s))
        self._renumerar()

    def eliminar(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP, "Seleccione primero una fila de la tabla.", parent=self)
            return
        self.tree.delete(sel[0])
        self._renumerar()

    def limpiar(self):
        if self.tree.get_children() and not messagebox.askyesno(
                APP, "¿Borrar todos los ejes del camión especial?", parent=self):
            return
        self.tree.delete(*self.tree.get_children())
        self._renumerar()

    def importar_excel(self):
        """Carga el convoy desde un Excel de dos columnas: «Separación» y
        «Carga en eje» (cualquier nombre de encabezado sirve, se detecta por
        posición). La separación de la primera fila se ignora -es el eje
        delantero, sin eje anterior-, igual que en la tabla manual."""
        ruta = filedialog.askopenfilename(
            parent=self, filetypes=[("Excel", "*.xlsx *.xlsm"), ("Todos los archivos", "*.*")])
        if not ruta:
            return
        try:
            import openpyxl
            wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
            ws = wb.active
            filas = [r for r in ws.iter_rows(values_only=True) if any(v is not None for v in r)]
            wb.close()
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror(APP, "No se pudo abrir el archivo:\n%s" % e, parent=self)
            return
        if not filas:
            messagebox.showerror(APP, "El archivo está vacío.", parent=self)
            return
        # La primera fila es encabezado si su segunda columna no es un número
        # (p.ej. "Separación" / "Carga en eje").
        if len(filas[0]) < 2 or not isinstance(filas[0][1], (int, float)):
            filas = filas[1:]
        if not filas:
            messagebox.showerror(
                APP, "No se encontraron filas de datos: se esperan dos columnas, "
                    "«Separación» y «Carga en eje».", parent=self)
            return
        try:
            cargas = [float(r[1]) for r in filas]
            seps = [float(r[0]) for r in filas[1:]]
        except (TypeError, ValueError, IndexError):
            messagebox.showerror(
                APP, "No se entienden los datos: se esperan dos columnas numéricas, "
                    "«Separación» y «Carga en eje» (la separación de la primera fila "
                    "se ignora, es el eje delantero, sin eje anterior).", parent=self)
            return
        try:
            ejes = mp.ejes_desde_separaciones(cargas, seps)
            for e in ejes:
                if e["P"] <= 0:
                    raise ValueError("Las cargas de eje deben ser positivas.")
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        self.cargar(ejes)
        messagebox.showinfo(
            APP, "Se importaron %d ejes desde «%s».\n\n"
                "Recuerde marcar «Incluirlo en la envolvente gobernante» si quiere "
                "que entre en el cálculo." % (len(ejes), os.path.basename(ruta)),
            parent=self)


# =============================================================================
# VENTANA PRINCIPAL
# =============================================================================
class Aplicacion(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP)
        self.geometry("1360x860")
        self.minsize(1080, 700)

        self.resultados = None
        self.hilo = None
        self.cancelar = threading.Event()
        self.cola = queue.Queue()
        self.ruta_proyecto = None

        self._crear_menu()
        # La barra de estado se empaqueta primero contra el borde inferior: así
        # el panel expansible no le quita el espacio cuando la ventana es baja.
        self._crear_barra_estado()
        panel = ttk.PanedWindow(self, orient="horizontal")
        panel.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        panel.add(self._crear_panel_entrada(panel), weight=0)
        panel.add(self._crear_panel_salida(panel), weight=1)

        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        self.after(100, self._revisar_cola)

    # ------------------------------------------------------------------ menú
    def _crear_menu(self):
        barra = tk.Menu(self)
        m = tk.Menu(barra, tearoff=0)
        m.add_command(label="Nuevo (valores por defecto)", command=self.nuevo_proyecto)
        m.add_command(label="Abrir proyecto...", accelerator="Ctrl+O", command=self.abrir_proyecto)
        m.add_command(label="Guardar proyecto...", accelerator="Ctrl+S", command=self.guardar_proyecto)
        m.add_separator()
        m.add_command(label="Salir", command=self._al_cerrar)
        barra.add_cascade(label="Archivo", menu=m)

        e = tk.Menu(barra, tearoff=0)
        e.add_command(label="Planilla Excel (punto a punto)...", command=self.exportar_excel)
        e.add_command(label="Gráfico PNG...", command=self.exportar_png)
        e.add_separator()
        e.add_command(label="Reporte de texto...", command=self.exportar_reporte)
        barra.add_cascade(label="Exportar", menu=e)

        a = tk.Menu(barra, tearoff=0)
        a.add_command(label="Acerca de...", command=self.acerca_de)
        barra.add_cascade(label="Ayuda", menu=a)

        self.config(menu=barra)
        self.bind_all("<Control-o>", lambda _: self.abrir_proyecto())
        self.bind_all("<Control-s>", lambda _: self.guardar_proyecto())
        self.bind_all("<F5>", lambda _: self.calcular())

    # --------------------------------------------------------- panel entrada
    def _crear_panel_entrada(self, master):
        cont = ttk.Frame(master, width=400)
        lienzo = tk.Canvas(cont, width=390, highlightthickness=0)
        scroll = ttk.Scrollbar(cont, orient="vertical", command=lienzo.yview)
        marco = ttk.Frame(lienzo, padding=(2, 2, 8, 2))
        marco.bind("<Configure>", lambda _: lienzo.configure(scrollregion=lienzo.bbox("all")))
        lienzo.create_window((0, 0), window=marco, anchor="nw")
        lienzo.configure(yscrollcommand=scroll.set)
        lienzo.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        par = mp.Parametros()

        nrm = ttk.LabelFrame(marco, text="Carga de diseño", padding=6)
        nrm.pack(fill="x", pady=(0, 6))
        self.var_norma = tk.StringVar(value=par.norma)
        cbn = ttk.Combobox(nrm, textvariable=self.var_norma, state="readonly",
                           values=[mp.HS20_44, mp.HL_93, mp.PERSONALIZADO])
        cbn.pack(fill="x")
        cbn.bind("<<ComboboxSelected>>", lambda _: self._cambiar_norma())
        self.lbl_norma = ttk.Label(nrm, text="", foreground="gray", justify="left")
        self.lbl_norma.pack(anchor="w", pady=(4, 0))

        geo = ttk.LabelFrame(marco, text="Geometría", padding=6)
        geo.pack(fill="x", pady=(0, 6))
        ttk.Label(geo, text="Luces de los tramos (m)").grid(row=0, column=0, sticky="w")
        self.var_tramos = tk.StringVar(value=", ".join("%g" % L for L in par.tramos))
        ttk.Entry(geo, textvariable=self.var_tramos).grid(row=1, column=0, sticky="ew")
        ttk.Label(geo, text="Ejemplo: 10, 12, 10   (un valor por tramo)",
                  foreground="gray").grid(row=2, column=0, sticky="w")

        ttk.Label(geo, text="Rótulas (m desde el extremo izquierdo)").grid(
            row=3, column=0, sticky="w", pady=(8, 0))
        self.var_rotulas = tk.StringVar(value="")
        ttk.Entry(geo, textvariable=self.var_rotulas).grid(row=4, column=0, sticky="ew")
        ttk.Label(geo, text="Vacío = viga continua.  Ej. Gerber: 12.4, 19.6",
                  foreground="gray").grid(row=5, column=0, sticky="w")
        self.lbl_gh = ttk.Label(geo, text="", foreground="#0b5d1e", font=("TkDefaultFont", 9, "bold"))
        self.lbl_gh.grid(row=6, column=0, sticky="w", pady=(6, 0))
        geo.columnconfigure(0, weight=1)
        self.var_tramos.trace_add("write", lambda *_: (self._actualizar_gh(),
                                                        self._actualizar_impacto()))
        self.var_rotulas.trace_add("write", lambda *_: self._actualizar_gh())

        pp = ttk.LabelFrame(marco, text="Peso propio (carga permanente estimada)", padding=6)
        pp.pack(fill="x", pady=(0, 6))
        self.var_pp_incluir = tk.BooleanVar(value=par.incluir_peso_propio)
        ttk.Checkbutton(pp, text="Incluirlo como carga distribuida sobre toda la estructura",
                        variable=self.var_pp_incluir,
                        command=self._alternar_pp).grid(row=0, column=0, columnspan=3, sticky="w")

        self.var_pp_ancho = tk.StringVar(value="%g" % par.ancho_tablero)
        self.var_pp_esplosa = tk.StringVar(value="%g" % (par.espesor_losa * 100))
        self.var_pp_denshorm = tk.StringVar(value="%g" % par.densidad_hormigon)
        self.var_pp_espcarp = tk.StringVar(value="%g" % (par.espesor_carpeta * 100))
        self.var_pp_densasf = tk.StringVar(value="%g" % par.densidad_asfalto)
        self.var_pp_barandas = tk.StringVar(value="%g" % par.peso_barandas)
        self.var_pp_pviga = tk.StringVar(value="%g" % par.peso_viga)
        self.var_pp_nvigas = tk.StringVar(value="%g" % par.n_vigas)

        self.campos_pp = []
        filas_pp = [
            ("Ancho del tablero (m)", self.var_pp_ancho, "para pasar losa/carpeta a Tnf/m"),
            ("Espesor losa de H.A. (cm)", self.var_pp_esplosa, "20 cm por defecto"),
            ("Densidad hormigón (Tnf/m³)", self.var_pp_denshorm, "2.4 Tnf/m³ típico"),
            ("Espesor carpeta asfalto (cm)", self.var_pp_espcarp, "5 cm por defecto"),
            ("Densidad asfalto (Tnf/m³)", self.var_pp_densasf, "2.2 Tnf/m³ típico"),
            ("Peso barandas (Tnf/m)", self.var_pp_barandas, "ambos lados, sumado"),
            ("Peso por viga (Tnf/m)", self.var_pp_pviga, "peso de UNA viga"),
            ("Número de vigas", self.var_pp_nvigas, ""),
        ]
        for fila, (etq, var, ayuda) in enumerate(filas_pp, start=1):
            ttk.Label(pp, text=etq).grid(row=fila, column=0, sticky="w", pady=1)
            campo = ttk.Entry(pp, textvariable=var, width=10)
            campo.grid(row=fila, column=1, sticky="e", pady=1)
            ttk.Label(pp, text=ayuda, foreground="gray").grid(row=fila, column=2, sticky="w",
                                                              padx=(6, 0))
            self.campos_pp.append(campo)
            var.trace_add("write", lambda *_: (self._actualizar_peso_propio(),
                                               self._actualizar_ppv()))
        self.lbl_pp = ttk.Label(pp, text="", foreground="#123a8a", justify="left")
        self.lbl_pp.grid(row=len(filas_pp) + 1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        pp.columnconfigure(0, weight=1)
        self._alternar_pp()

        dist = ttk.LabelFrame(marco, text="Distribución transversal (viga interior)", padding=6)
        dist.pack(fill="x", pady=(0, 6))
        self.var_dist_incluir = tk.BooleanVar(value=par.incluir_distribucion)
        ttk.Checkbutton(dist, text="Aplicar a la envolvente combinada (momento por viga)",
                        variable=self.var_dist_incluir,
                        command=self._alternar_dist).grid(row=0, column=0, columnspan=3,
                                                          sticky="w")

        self.var_dist_tipo = tk.StringVar(value=TIPOS_VIGA[par.tipo_viga])
        self.var_dist_S = tk.StringVar(value="%g" % par.separacion_vigas)
        self.var_dist_vias = tk.StringVar(value=VIAS_DISENO[par.n_vias_diseno])
        for var in (self.var_dist_tipo, self.var_dist_vias):
            var.trace_add("write", lambda *_: self._actualizar_dist())

        ttk.Label(dist, text="Tipo de viga").grid(row=1, column=0, sticky="w", pady=1)
        self.cb_dist_tipo = ttk.Combobox(dist, textvariable=self.var_dist_tipo, state="readonly",
                                         width=16, values=list(TIPOS_VIGA.values()))
        self.cb_dist_tipo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=1)
        self.cb_dist_tipo.bind("<<ComboboxSelected>>", lambda _: self._actualizar_dist())

        ttk.Label(dist, text="Separación entre vigas S (m)").grid(row=2, column=0, sticky="w",
                                                                  pady=1)
        self.campo_dist_S = ttk.Entry(dist, textvariable=self.var_dist_S, width=10)
        self.campo_dist_S.grid(row=2, column=1, sticky="e", pady=1)
        self.var_dist_S.trace_add("write", lambda *_: self._actualizar_dist())

        ttk.Label(dist, text="Vías de diseño").grid(row=3, column=0, sticky="w", pady=1)
        self.cb_dist_vias = ttk.Combobox(dist, textvariable=self.var_dist_vias, state="readonly",
                                         width=16, values=list(VIAS_DISENO.values()))
        self.cb_dist_vias.grid(row=3, column=1, columnspan=2, sticky="ew", pady=1)
        self.cb_dist_vias.bind("<<ComboboxSelected>>", lambda _: self._actualizar_dist())

        self.campos_dist = [self.cb_dist_tipo, self.campo_dist_S, self.cb_dist_vias]
        self.marco_dist_std = dist
        self.lbl_dist = ttk.Label(dist, text="", foreground="#123a8a", justify="left")
        self.lbl_dist.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(dist, text="Coeficiente g = S/D (AASHTO Standard, Tabla 3.23.1). Sólo\n"
                             "afecta al MOMENTO; corte y reacciones quedan por vía\n"
                             "completa. No se aplica al peso propio (usa otro método).",
                  foreground="gray").grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        dist.columnconfigure(0, weight=1)

        # --- AASHTO LRFD (Art. 4.6.2.2): reemplaza al método S/D con la norma HL-93
        dl = ttk.LabelFrame(marco, text="Distribución transversal AASHTO LRFD (Art. 4.6.2.2)",
                            padding=6)
        self.marco_dist_lrfd = dl
        self.var_dist_incluir_l = tk.BooleanVar(value=par.incluir_distribucion)
        ttk.Checkbutton(dl, text="Calcular coeficientes (momento y corte, viga interior y "
                                 "exterior)", variable=self.var_dist_incluir_l,
                        command=lambda: (self.var_dist_incluir.set(self.var_dist_incluir_l.get()),
                                         self._alternar_dist())
                        ).grid(row=0, column=0, columnspan=3, sticky="w")
        self.var_dist_incluir.trace_add("write", lambda *_: self.var_dist_incluir_l.set(
            self.var_dist_incluir.get()))
        self.vl = {}
        self.campos_dist_l = []
        filas_l = [("S", "Separación entre vigas S (m)", self.var_dist_S, "eje a eje"),
                   ("nb_dist", "Número de vigas Nb", None, "mín. 4 para las fórmulas"),
                   ("ts_dist", "Espesor de losa ts (m)", None, "4.5–12 in"),
                   ("L_dist", "Luz L para la fórmula (m)", None, "0 = promedio de las luces"),
                   ("n_mod", "Relación modular n", None, "(f'c viga / f'c losa)^0.33"),
                   ("Ig_dist", "Inercia de la viga Ig (m4)", None, ""),
                   ("Ag_dist", "Área de la viga Ag (m2)", None, ""),
                   ("eg_dist", "Excentricidad eg (m)", None, "centroide viga a centroide losa"),
                   ("de_dist", "de: eje viga ext. a barrera (m)", None, "−0.3 a 1.68 m"),
                   ("w_calzada", "Ancho libre de calzada w (m)", None,
                    "0 = sin cuerpo rígido")]
        for i, (clave, etq, var, ayuda) in enumerate(filas_l, start=1):
            if var is None:
                var = tk.StringVar(value="%g" % getattr(par, clave))
            self.vl[clave] = var
            ttk.Label(dl, text=etq).grid(row=i, column=0, sticky="w", pady=1)
            campo = ttk.Entry(dl, textvariable=var, width=10)
            campo.grid(row=i, column=1, sticky="e", pady=1)
            ttk.Label(dl, text=ayuda, foreground="gray").grid(row=i, column=2, sticky="w",
                                                              padx=(6, 0))
            self.campos_dist_l.append(campo)
            var.trace_add("write", lambda *_: self._actualizar_dist())
        n_fl = len(filas_l) + 1
        self.var_diafragmas = tk.BooleanVar(value=par.diafragmas)
        chk_d = ttk.Checkbutton(dl, text="Vigas con diafragmas (verifica cuerpo rígido en la "
                                         "viga exterior)", variable=self.var_diafragmas,
                                command=self._actualizar_dist)
        chk_d.grid(row=n_fl, column=0, columnspan=3, sticky="w")
        self.campos_dist_l.append(chk_d)
        ttk.Label(dl, text="Viga que se usa por defecto").grid(row=n_fl + 1, column=0,
                                                               sticky="w", pady=1)
        self.var_viga_dist = tk.StringVar(value=VIGAS_DIST[par.viga_dist])
        cb_v = ttk.Combobox(dl, textvariable=self.var_viga_dist, state="readonly", width=16,
                            values=list(VIGAS_DIST.values()))
        cb_v.grid(row=n_fl + 1, column=1, columnspan=2, sticky="ew", pady=1)
        cb_v.bind("<<ComboboxSelected>>", lambda _: self._actualizar_dist())
        self.campos_dist_l.append(cb_v)
        self.lbl_dist_l = ttk.Label(dl, text="", foreground="#123a8a", justify="left")
        self.lbl_dist_l.grid(row=n_fl + 2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(dl, text="Los factores incluyen la presencia múltiple (no se vuelve a aplicar).\n"
                           "Afectan momento (gM) y corte/reacciones (gV). No se aplican al peso\n"
                           "propio. En Gráficos se elige viga interior o exterior.",
                  foreground="gray").grid(row=n_fl + 3, column=0, columnspan=3, sticky="w",
                                          pady=(4, 0))
        dl.columnconfigure(0, weight=1)
        self._alternar_dist()
        self._mostrar_dist_segun_norma(par.norma)

        ppv = ttk.LabelFrame(marco, text="Peso propio por viga (desglose por partida)", padding=6)
        self.marco_ppv = ppv
        ppv.pack(fill="x", pady=(0, 6))
        self.var_ppv_incluir = tk.BooleanVar(value=par.incluir_pp_por_viga)
        ttk.Checkbutton(ppv, text="Calcular el desglose (viga, losa, carpeta y baranda por "
                                  "separado, para el gráfico de peso propio por viga)",
                        variable=self.var_ppv_incluir,
                        command=self._alternar_ppv).grid(row=0, column=0, columnspan=3,
                                                          sticky="w")

        self.var_ppv_ancho_auto = tk.BooleanVar(value=True)
        self.chk_ppv_auto = ttk.Checkbutton(
            ppv, text="Ancho tributario = separación entre vigas S (de la sección anterior)",
            variable=self.var_ppv_ancho_auto, command=self._alternar_ppv_ancho)
        self.chk_ppv_auto.grid(row=1, column=0, columnspan=3, sticky="w")

        ttk.Label(ppv, text="Ancho tributario (m)").grid(row=2, column=0, sticky="w", pady=1)
        self.var_ppv_ancho = tk.StringVar(value="%g" % par.ancho_tributario)
        self.campo_ppv_ancho = ttk.Entry(ppv, textvariable=self.var_ppv_ancho, width=10)
        self.campo_ppv_ancho.grid(row=2, column=1, sticky="e", pady=1)
        ttk.Label(ppv, text="para pasar losa/carpeta a Tnf/m de UNA viga",
                 foreground="gray").grid(row=2, column=2, sticky="w", padx=(6, 0))
        self.var_ppv_ancho.trace_add("write", lambda *_: self._actualizar_ppv())
        # Mientras el ancho tributario esté en modo "auto", sigue en vivo a la
        # separación entre vigas de la sección de distribución transversal.
        self.var_dist_S.trace_add("write", lambda *_: (
            self.var_ppv_ancho.set(self.var_dist_S.get())
            if self.var_ppv_ancho_auto.get() else None))

        ttk.Label(ppv, text="Reparto de baranda").grid(row=3, column=0, sticky="w", pady=1)
        self.var_ppv_baranda = tk.StringVar(value=MODOS_BARANDA[par.modo_baranda_pp])
        self.cb_ppv_baranda = ttk.Combobox(ppv, textvariable=self.var_ppv_baranda, state="readonly",
                                           width=30, values=list(MODOS_BARANDA.values()))
        self.cb_ppv_baranda.grid(row=3, column=1, columnspan=2, sticky="ew", pady=1)
        self.cb_ppv_baranda.bind("<<ComboboxSelected>>", lambda _: self._actualizar_ppv())

        self.campos_ppv = [self.chk_ppv_auto, self.campo_ppv_ancho, self.cb_ppv_baranda]
        self.lbl_ppv = ttk.Label(ppv, text="", foreground="#123a8a", justify="left")
        self.lbl_ppv.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ppv.columnconfigure(0, weight=1)
        self._alternar_ppv()

        self.tabla_camion = TablaEjes(marco, "Camión de diseño (HS-20)", par.ejes_camion,
                                      al_cambiar=self._actualizar_nota_sep)
        self.tabla_camion.pack(fill="x", pady=(0, 6))
        self.tabla_camion.tree.tag_configure("barrido", foreground="#b06000")

        sep = ttk.LabelFrame(marco, text="Separación entre ejes traseros", padding=6)
        sep.pack(fill="x", pady=(0, 6))
        self.var_sepvar = tk.BooleanVar(value=par.sep_variable)
        ttk.Checkbutton(sep, text="Variable (AASHTO LRFD 3.6.1.2.2)",
                        variable=self.var_sepvar,
                        command=self._alternar_sep).grid(row=0, column=0, columnspan=6, sticky="w")
        self.var_smin = tk.StringVar(value="%g" % par.sep_min)
        self.var_smax = tk.StringVar(value="%g" % par.sep_max)
        self.var_spaso = tk.StringVar(value="%g" % par.sep_paso)
        self.campos_sep = []
        for col, (etq, var) in enumerate([("Mín", self.var_smin), ("Máx", self.var_smax),
                                          ("Paso", self.var_spaso)]):
            ttk.Label(sep, text=etq).grid(row=1, column=2 * col, sticky="e", padx=(0, 2), pady=(4, 0))
            campo = ttk.Entry(sep, textvariable=var, width=7)
            campo.grid(row=1, column=2 * col + 1, sticky="w", padx=(0, 8), pady=(4, 0))
            self.campos_sep.append(campo)
        ttk.Label(sep, text="En metros. 4.2672 a 9.1440 m = 14 a 30 ft exactos\n"
                            "(1 ft = 0.3048 m).",
                  foreground="gray").grid(row=2, column=0, columnspan=6, sticky="w", pady=(4, 0))
        self.lbl_sep = ttk.Label(sep, text="", foreground="#b06000", justify="left")
        self.lbl_sep.grid(row=3, column=0, columnspan=6, sticky="w", pady=(2, 0))
        for var in (self.var_smin, self.var_smax):
            var.trace_add("write", lambda *_: self._actualizar_nota_sep())

        self.tabla_tandem = TablaEjes(marco, par.nombre_tandem.capitalize(), par.ejes_tandem)
        self.tabla_tandem.pack(fill="x", pady=(0, 2))
        self.var_usar_tan = tk.BooleanVar(value=par.usar_tandem)
        ttk.Checkbutton(marco, text="Analizar este segundo vehículo",
                        variable=self.var_usar_tan).pack(anchor="w", pady=(0, 6))
        ttk.Label(marco, text="El offset es la distancia al eje guía: 0 el delantero,\n"
                              "negativo hacia atrás (p. ej. -4.2672).",
                  foreground="gray").pack(anchor="w", pady=(0, 6))

        self.tabla_especial = TablaEspecial(marco)
        self.tabla_especial.pack(fill="x", pady=(0, 4))
        self.var_incluir_esp = tk.BooleanVar(value=par.incluir_especial)
        ttk.Checkbutton(marco, text="Incluirlo en la envolvente gobernante",
                        variable=self.var_incluir_esp).pack(anchor="w")
        ttk.Label(marco, text="Sin marcar se calcula igual y se informa como\n"
                              "envolvente aparte, fuera de la combinación.",
                  foreground="gray").pack(anchor="w", pady=(0, 6))

        faja = ttk.LabelFrame(marco, text="Carga de faja / carril", padding=6)
        faja.pack(fill="x", pady=(0, 6))
        self.var_w = tk.StringVar(value="%.4f" % par.w_carril)
        self.var_pm = tk.StringVar(value="%.4f" % par.p_conc_momento)
        self.var_pv = tk.StringVar(value="%.4f" % par.p_conc_corte)
        for fila, (etq, var, ayuda) in enumerate([
                ("Uniforme  q (Tnf/m)", self.var_w, "0.64 kip/ft"),
                ("Concentrada momento", self.var_pm, "18 kip; 0 = sin ella"),
                ("Concentrada corte", self.var_pv, "26 kip; 0 = sin ella")]):
            ttk.Label(faja, text=etq).grid(row=fila, column=0, sticky="w", pady=2)
            ttk.Entry(faja, textvariable=var, width=10).grid(row=fila, column=1, sticky="e", pady=2)
            ttk.Label(faja, text=ayuda, foreground="gray").grid(row=fila, column=2, sticky="w",
                                                                padx=(6, 0))
        self.var_dosconc = tk.BooleanVar(value=par.dos_conc_momento_negativo)
        ttk.Checkbutton(faja, text="Dos concentradas para momento negativo",
                        variable=self.var_dosconc).grid(row=3, column=0, columnspan=3,
                                                        sticky="w", pady=(4, 0))
        faja.columnconfigure(0, weight=1)

        car = ttk.LabelFrame(marco, text="Combinación, impacto y análisis", padding=6)
        car.pack(fill="x", pady=(0, 6))
        self.var_comb = tk.StringVar(value=COMBINACIONES[par.modo_combinacion])
        self.var_imp = tk.StringVar(value=IMPACTOS[par.impacto_modo])
        ttk.Label(car, text="Vehículo y faja").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(car, textvariable=self.var_comb, state="readonly", width=22,
                     values=list(COMBINACIONES.values())).grid(row=0, column=1, columnspan=2,
                                                               sticky="ew", pady=2)
        ttk.Label(car, text="Impacto").grid(row=1, column=0, sticky="w", pady=2)
        cb_imp = ttk.Combobox(car, textvariable=self.var_imp, state="readonly", width=22,
                              values=list(IMPACTOS.values()))
        cb_imp.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)
        cb_imp.bind("<<ComboboxSelected>>", lambda _: self._actualizar_impacto())
        self.lbl_impacto = ttk.Label(car, text="", foreground="#123a8a", justify="left")
        self.lbl_impacto.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self.var_fd = tk.StringVar(value="%g" % par.factor_dinamico)
        self.var_may = tk.StringVar(value="%g" % par.factor_mayoracion)
        self.var_dx = tk.StringVar(value="%g" % par.dx)
        for fila, (etq, var, ayuda) in enumerate([
                ("1 + IM (si es fijo)", self.var_fd, "1.33 en LRFD"),
                ("Mayoración carga viva", self.var_may, "1.0; Chile usa 1.20 en\nciertas rutas"),
                ("Paso de cálculo dx (m)", self.var_dx, "0.05 m = 5 cm")], start=3):
            ttk.Label(car, text=etq).grid(row=fila, column=0, sticky="w", pady=2)
            ttk.Entry(car, textvariable=var, width=10).grid(row=fila, column=1, sticky="e", pady=2)
            ttk.Label(car, text=ayuda, foreground="gray").grid(row=fila, column=2, sticky="w",
                                                               padx=(6, 0))
        self.var_fd.trace_add("write", lambda *_: self._actualizar_impacto())
        car.columnconfigure(0, weight=1)
        self._actualizar_impacto()

        acc = ttk.Frame(marco)
        acc.pack(fill="x", pady=(4, 8))
        self.btn_calcular = ttk.Button(acc, text="CALCULAR   (F5)", command=self.calcular)
        self.btn_calcular.pack(fill="x", ipady=6)
        self.btn_cancelar = ttk.Button(acc, text="Cancelar", command=self.cancelar_calculo,
                                       state="disabled")
        self.btn_cancelar.pack(fill="x", pady=(4, 0))

        self._escribir_carga_diseno(par)

        self._actualizar_gh()
        return cont

    # ---------------------------------------------------------- panel salida
    def _crear_panel_salida(self, master):
        libreta = ttk.Notebook(master)

        # --- Gráficos
        tab_g = ttk.Frame(libreta, padding=4)
        cab = ttk.Frame(tab_g)
        cab.pack(fill="x")
        ttk.Label(cab, text="Envolvente:").pack(side="left")
        self.var_caso_g = tk.StringVar(value=SIN_RESULTADOS)
        self.cb_g = ttk.Combobox(cab, textvariable=self.var_caso_g, state="readonly", width=42,
                                 values=[SIN_RESULTADOS])
        self.cb_g.pack(side="left", padx=6)
        self.cb_g.bind("<<ComboboxSelected>>", lambda _: self.redibujar())
        ttk.Label(cab, text="   Unidades:").pack(side="left")
        self.var_unidad_g = tk.StringVar(value=gp.UNIDAD_DEFECTO)
        self.cb_unidad_g = ttk.Combobox(cab, textvariable=self.var_unidad_g, state="readonly",
                                        width=16, values=list(gp.UNIDADES.keys()))
        self.cb_unidad_g.pack(side="left", padx=6)
        self.cb_unidad_g.bind("<<ComboboxSelected>>", lambda _: self.redibujar())
        ttk.Button(cab, text="Guardar PNG", command=self.exportar_png).pack(side="right")

        # Factores sobre la carga viva: por defecto NO se aplican (resultado crudo).
        cab2 = ttk.Frame(tab_g, padding=(6, 0, 6, 4))
        cab2.pack(fill="x")
        ttk.Label(cab2, text="Incluir en la carga viva:").pack(side="left")
        self.var_f_imp = tk.BooleanVar(value=False)
        self.var_f_dist = tk.BooleanVar(value=False)
        self.var_f_may = tk.BooleanVar(value=False)
        self.chk_f_imp = ttk.Checkbutton(cab2, text="Coef. de impacto", variable=self.var_f_imp,
                                         command=self._refrescar_vista)
        self.chk_f_dist = ttk.Checkbutton(cab2, text="Coef. de distribución (g)",
                                          variable=self.var_f_dist, command=self._refrescar_vista)
        self.chk_f_may = ttk.Checkbutton(cab2, text="Mayoración (Chile, x1.20)",
                                         variable=self.var_f_may, command=self._refrescar_vista)
        for c in (self.chk_f_imp, self.chk_f_dist, self.chk_f_may):
            c.pack(side="left", padx=8)
        ttk.Label(cab2, text="   Viga:").pack(side="left")
        self.var_f_viga = tk.StringVar(value=VIGAS_DIST["interior"])
        self.cb_f_viga = ttk.Combobox(cab2, textvariable=self.var_f_viga, state="disabled",
                                      width=14, values=list(VIGAS_DIST.values()))
        self.cb_f_viga.pack(side="left", padx=6)
        self.cb_f_viga.bind("<<ComboboxSelected>>", lambda _: self._refrescar_vista())
        self.res_base = None

        self.figura = Figure(figsize=(9, 6.5))
        self.lienzo = FigureCanvasTkAgg(self.figura, master=tab_g)
        barra_mpl = NavigationToolbar2Tk(self.lienzo, tab_g, pack_toolbar=False)
        barra_mpl.update()
        barra_mpl.pack(side="bottom", fill="x")
        self.lienzo.get_tk_widget().pack(fill="both", expand=True, pady=(4, 0))
        libreta.add(tab_g, text="  Gráficos  ")

        # --- Peso propio por viga (desglose por partida)
        tab_pv = ttk.Frame(libreta, padding=4)
        fila1 = ttk.Frame(tab_pv)
        fila1.pack(fill="x")
        fila2 = ttk.Frame(tab_pv)
        fila2.pack(fill="x", pady=(2, 0))
        self.var_pv_viga = tk.BooleanVar(value=True)
        self.var_pv_losa = tk.BooleanVar(value=True)
        self.var_pv_carpeta = tk.BooleanVar(value=True)
        self.var_pv_barandas = tk.BooleanVar(value=True)
        self.var_pv_losa_viga = tk.BooleanVar(value=False)
        self.var_pv_losa_viga_barandas = tk.BooleanVar(value=False)
        self.var_pv_total = tk.BooleanVar(value=True)
        self.chks_pv = {}
        ttk.Label(fila1, text="Partidas:").pack(side="left")
        for clave, texto, var in (
                ("pp_viga", "Viga", self.var_pv_viga),
                ("pp_losa", "Losa", self.var_pv_losa),
                ("pp_carpeta", "Carpeta asfáltica", self.var_pv_carpeta),
                ("pp_barandas", "Baranda", self.var_pv_barandas)):
            chk = ttk.Checkbutton(fila1, text=texto, variable=var, command=self.redibujar_pp_viga)
            chk.pack(side="left", padx=(6, 0))
            self.chks_pv[clave] = chk
        ttk.Label(fila2, text="Combinaciones:").pack(side="left")
        for clave, texto, var in (
                ("pp_losa_viga", "Losa + Viga", self.var_pv_losa_viga),
                ("pp_losa_viga_barandas", "Losa + Viga + Baranda", self.var_pv_losa_viga_barandas),
                ("pp_total", "Total (todas las partidas)", self.var_pv_total)):
            chk = ttk.Checkbutton(fila2, text=texto, variable=var, command=self.redibujar_pp_viga)
            chk.pack(side="left", padx=(6, 0))
            self.chks_pv[clave] = chk
        ttk.Label(fila2, text="   Unidades:").pack(side="left")
        self.var_unidad_pv = tk.StringVar(value=gp.UNIDAD_DEFECTO)
        self.cb_unidad_pv = ttk.Combobox(fila2, textvariable=self.var_unidad_pv, state="readonly",
                                         width=16, values=list(gp.UNIDADES.keys()))
        self.cb_unidad_pv.pack(side="left", padx=6)
        self.cb_unidad_pv.bind("<<ComboboxSelected>>", lambda _: self.redibujar_pp_viga())
        ttk.Button(fila2, text="Guardar PNG", command=self.exportar_png_pp_viga).pack(side="right")

        self.lbl_pv_aviso = ttk.Label(tab_pv, text="", foreground="gray")
        self.lbl_pv_aviso.pack(anchor="w", pady=(2, 0))

        self.figura_pv = Figure(figsize=(9, 6.5))
        self.lienzo_pv = FigureCanvasTkAgg(self.figura_pv, master=tab_pv)
        barra_pv = NavigationToolbar2Tk(self.lienzo_pv, tab_pv, pack_toolbar=False)
        barra_pv.update()
        barra_pv.pack(side="bottom", fill="x")
        self.lienzo_pv.get_tk_widget().pack(fill="both", expand=True, pady=(4, 0))
        libreta.add(tab_pv, text="  Peso propio por viga  ")
        self._actualizar_chks_pp_viga()

        # --- Resumen
        tab_r = ttk.Frame(libreta, padding=4)
        cab = ttk.Frame(tab_r)
        cab.pack(fill="x", pady=(0, 4))
        ttk.Label(cab, text="Valores extremos por apoyo y por tramo, para todos los estados de carga.").pack(side="left")
        ttk.Button(cab, text="Copiar al portapapeles",
                   command=self.copiar_resumen).pack(side="right")
        cols = ("magnitud", "ubicacion", "pos", "neg", "unidad")
        self.tv_resumen = ttk.Treeview(tab_r, columns=cols, show="tree headings")
        for c, t, an, al in (("magnitud", "Magnitud", 150, "w"), ("ubicacion", "Ubicación", 110, "w"),
                             ("pos", "Máximo (+)", 110, "e"), ("neg", "Mínimo (-)", 110, "e"),
                             ("unidad", "Unidad", 80, "w")):
            self.tv_resumen.heading(c, text=t)
            self.tv_resumen.column(c, width=an, anchor=al)
        self.tv_resumen.column("#0", width=340, minwidth=200)
        self.tv_resumen.heading("#0", text="Estado de carga")
        sb = ttk.Scrollbar(tab_r, orient="vertical", command=self.tv_resumen.yview)
        self.tv_resumen.configure(yscrollcommand=sb.set)
        self.tv_resumen.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        libreta.add(tab_r, text="  Resumen  ")

        # --- Tabla punto a punto
        tab_t = ttk.Frame(libreta, padding=4)
        cab = ttk.Frame(tab_t)
        cab.pack(fill="x", pady=(0, 4))
        ttk.Label(cab, text="Envolvente:").pack(side="left")
        self.var_caso_t = tk.StringVar(value=SIN_RESULTADOS)
        self.cb_t = ttk.Combobox(cab, textvariable=self.var_caso_t, state="readonly", width=38,
                                 values=[SIN_RESULTADOS])
        self.cb_t.pack(side="left", padx=6)
        self.cb_t.bind("<<ComboboxSelected>>", lambda _: self.llenar_tabla())
        ttk.Label(cab, text="   Consultar en x =").pack(side="left")
        self.var_x = tk.StringVar(value="")
        ttk.Entry(cab, textvariable=self.var_x, width=8).pack(side="left")
        ttk.Label(cab, text="m").pack(side="left")
        ttk.Button(cab, text="Ir", width=4, command=self.consultar_x).pack(side="left", padx=4)
        ttk.Button(cab, text="Exportar Excel", command=self.exportar_excel).pack(side="right")
        self.lbl_consulta = ttk.Label(tab_t, text="", foreground="#123a8a",
                                      font=("TkDefaultFont", 9, "bold"))
        self.lbl_consulta.pack(anchor="w", pady=(0, 3))

        cols = ("x", "vmax", "vmin", "mmax", "mmin")
        self.tv_tabla = ttk.Treeview(tab_t, columns=cols, show="headings")
        for c, t in (("x", "x (m)"), ("vmax", "V máx (+) (Tnf)"), ("vmin", "V mín (-) (Tnf)"),
                     ("mmax", "M máx (+) (Tnf·m)"), ("mmin", "M mín (-) (Tnf·m)")):
            self.tv_tabla.heading(c, text=t)
            self.tv_tabla.column(c, width=140, anchor="e")
        sb2 = ttk.Scrollbar(tab_t, orient="vertical", command=self.tv_tabla.yview)
        self.tv_tabla.configure(yscrollcommand=sb2.set)
        self.tv_tabla.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        libreta.add(tab_t, text="  Tabla punto a punto  ")

        # --- Reporte
        tab_x = ttk.Frame(libreta, padding=4)
        cab = ttk.Frame(tab_x)
        cab.pack(fill="x", pady=(0, 4))
        ttk.Button(cab, text="Copiar todo", command=self.copiar_reporte).pack(side="right")
        ttk.Button(cab, text="Guardar .txt", command=self.exportar_reporte).pack(side="right", padx=4)
        self.txt = tk.Text(tab_x, wrap="none", font=("Consolas", 10))
        sb3 = ttk.Scrollbar(tab_x, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb3.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb3.pack(side="right", fill="y")
        libreta.add(tab_x, text="  Reporte  ")

        return libreta

    def _crear_barra_estado(self):
        barra = ttk.Frame(self, padding=(8, 4))
        barra.pack(side="bottom", fill="x")
        self.var_estado = tk.StringVar(value="Ingrese los datos y pulse CALCULAR.")
        ttk.Label(barra, textvariable=self.var_estado).pack(side="left")
        self.progreso = ttk.Progressbar(barra, length=240, mode="determinate", maximum=100)
        self.progreso.pack(side="right")

    # ------------------------------------------------------------- entrada
    def _actualizar_gh(self):
        """Informa el grado de hiperestaticidad mientras se escribe.

        Arma una Estructura real (misma clase que usa el cálculo) en vez de
        recalcular el GH a mano: así la vista previa nunca puede quedar
        desincronizada de lo que hará «Calcular» -- incluidas rótulas
        duplicadas o pegadas a un extremo, que Estructura ya sabe filtrar.
        """
        try:
            tramos = parsear_lista(self.var_tramos.get(), "tramos")
            rot = parsear_lista(self.var_rotulas.get(), "rótulas", permitir_vacio=True)
            if not tramos or any(L <= 0 for L in tramos):
                self.lbl_gh.configure(text="", foreground="gray")
                return
        except ValueError:
            self.lbl_gh.configure(text="", foreground="gray")
            return
        try:
            est = mp.Estructura(tramos, rot)
        except ValueError as e:
            self.lbl_gh.configure(text=str(e), foreground="#a11")
            return
        texto = ("GH = 0  ->  isostática (tipo Gerber)" if est.gh == 0
                 else "GH = %d  ->  hiperestática" % est.gh)
        if est.avisos:
            texto += "   (%s)" % "; ".join(est.avisos)
        self.lbl_gh.configure(text=texto, foreground="#0b5d1e")

    def _actualizar_impacto(self):
        """Muestra el 1+I resultante por tramo (y por apoyo interior), sin
        necesidad de correr el cálculo completo. No es un único valor para
        todo el puente: cada sección usa la luz que le corresponde."""
        if not hasattr(self, "lbl_impacto"):
            return
        modo = _clave_de(IMPACTOS, self.var_imp.get(), "aashto_standard")
        if modo == "fijo":
            try:
                fd = parsear_num(self.var_fd.get(), "1 + IM")
                self.lbl_impacto.configure(
                    text="1 + IM = %g, fijo e igual en toda la estructura." % fd)
            except ValueError:
                self.lbl_impacto.configure(text="")
            return
        try:
            tramos = parsear_lista(self.var_tramos.get(), "tramos")
        except ValueError:
            self.lbl_impacto.configure(text="")
            return
        if not tramos:
            self.lbl_impacto.configure(text="")
            return

        piezas = []
        for k, L in enumerate(tramos):
            fi = 1.0 + mp.impacto_aashto_standard(L)
            piezas.append("%s-%s: %.3f" % (mp._nombre_apoyo(k), mp._nombre_apoyo(k + 1), fi))
        linea1 = "1+I por tramo (L propia de cada uno):  " + "   ".join(piezas)
        linea2 = ""
        if len(tramos) > 1:
            aps = []
            for i in range(1, len(tramos)):
                Lprom = 0.5 * (tramos[i - 1] + tramos[i])
                fi = 1.0 + mp.impacto_aashto_standard(Lprom)
                aps.append("%s: %.3f" % (mp._nombre_apoyo(i), fi))
            linea2 = "\n1+I en apoyos interiores (L promedio de los 2 tramos vecinos):  " \
                     + "   ".join(aps)
        self.lbl_impacto.configure(text=linea1 + linea2)

    def _mostrar_dist_segun_norma(self, norma):
        """AASHTO LRFD usa su propio panel; el resto de las normas, el método S/D."""
        if not hasattr(self, "marco_dist_lrfd"):
            return
        lrfd = (norma == mp.HL_93)
        mostrar, ocultar = ((self.marco_dist_lrfd, self.marco_dist_std) if lrfd
                            else (self.marco_dist_std, self.marco_dist_lrfd))
        ocultar.pack_forget()
        if hasattr(self, "marco_ppv"):
            mostrar.pack(fill="x", pady=(0, 6), before=self.marco_ppv)
        else:
            mostrar.pack(fill="x", pady=(0, 6))
        self._actualizar_dist()

    def _alternar_dist(self):
        activo = self.var_dist_incluir.get()
        estado = "readonly" if activo else "disabled"
        self.cb_dist_tipo.configure(state=estado)
        self.cb_dist_vias.configure(state=estado)
        self.campo_dist_S.configure(state=("normal" if activo else "disabled"))
        for c in getattr(self, "campos_dist_l", []):
            c.configure(state=("readonly" if isinstance(c, ttk.Combobox)
                               else "normal") if activo else "disabled")
        self._actualizar_dist()

    def _par_lrfd(self):
        """Parámetros mínimos para evaluar los coeficientes LRFD en vivo."""
        g = lambda k, n: parsear_num(self.vl[k].get(), n)
        return mp.Parametros(
            tramos=parsear_lista(self.var_tramos.get(), "Luces de los tramos"),
            separacion_vigas=g("S", "Separación entre vigas"),
            nb_dist=int(round(g("nb_dist", "Número de vigas"))),
            ts_dist=g("ts_dist", "Espesor de losa"), L_dist=g("L_dist", "Luz L"),
            n_mod=g("n_mod", "Relación modular"), Ig_dist=g("Ig_dist", "Inercia"),
            Ag_dist=g("Ag_dist", "Área"), eg_dist=g("eg_dist", "Excentricidad"),
            de_dist=g("de_dist", "de"), w_calzada=g("w_calzada", "Ancho de calzada"),
            diafragmas=bool(self.var_diafragmas.get()),
            viga_dist=_clave_de(VIGAS_DIST, self.var_viga_dist.get(), "interior"))

    def _actualizar_dist(self):
        """Recalcula los coeficientes en vivo, con los mismos avisos que dará el cálculo."""
        if not hasattr(self, "lbl_dist"):
            return
        if self.var_norma.get() == mp.HL_93 and hasattr(self, "lbl_dist_l"):
            self.lbl_dist_l.configure(text="")
            if not self.var_dist_incluir.get():
                return
            try:
                par = self._par_lrfd()
                if par.Ig_dist <= 0 or par.Ag_dist <= 0 or par.eg_dist <= 0 or par.ts_dist <= 0 \
                        or par.n_mod <= 0 or par.separacion_vigas <= 0:
                    self.lbl_dist_l.configure(
                        text="Complete S, ts, n, Ig, Ag y eg (todos > 0).", foreground="#a11")
                    return
                f = mp.factores_distribucion_lrfd(par)
            except (ValueError, ZeroDivisionError):
                return
            texto = "\n".join(f["detalle"][1:3] + f["detalle"][3:3]) + "\n" + \
                "Exterior adoptado: gM = %.4f ; gV = %.4f" % (
                    f["exterior"]["gM"], f["exterior"]["gV"])
            if f["avisos"]:
                texto += "\n" + "\n".join(f["avisos"])
            self.lbl_dist_l.configure(text=texto,
                                      foreground="#a11" if f["avisos"] else "#123a8a")
            return
        if not self.var_dist_incluir.get():
            self.lbl_dist.configure(text="")
            return
        try:
            S = parsear_num(self.var_dist_S.get(), "Separación entre vigas")
        except ValueError:
            self.lbl_dist.configure(text="")
            return
        if S <= 0:
            self.lbl_dist.configure(text="La separación entre vigas debe ser positiva.",
                                    foreground="#a11")
            return
        tipo = _clave_de(TIPOS_VIGA, self.var_dist_tipo.get(), "acero")
        vias = _clave_de(VIAS_DISENO, self.var_dist_vias.get(), 2)
        g, D, S_ft, aviso = mp.factor_distribucion_momento(tipo, S, vias)
        texto = "g = S/D = %.2f ft / %.1f = %.4f   (M_viga = g x M_vía completa)" % (S_ft, D, g)
        if aviso:
            texto += "\n" + aviso
            self.lbl_dist.configure(text=texto, foreground="#a11")
        else:
            self.lbl_dist.configure(text=texto, foreground="#123a8a")

    def _alternar_pp(self):
        estado = "normal" if self.var_pp_incluir.get() else "disabled"
        for campo in self.campos_pp:
            campo.configure(state=estado)
        self._actualizar_peso_propio()

    def _actualizar_peso_propio(self):
        """Recalcula el peso propio total en vivo, sin correr el cálculo completo."""
        if not hasattr(self, "lbl_pp"):
            return
        if not self.var_pp_incluir.get():
            self.lbl_pp.configure(text="")
            return
        try:
            ancho = parsear_num(self.var_pp_ancho.get(), "Ancho del tablero")
            esp_losa = parsear_num(self.var_pp_esplosa.get(), "Espesor de losa") / 100.0
            dens_horm = parsear_num(self.var_pp_denshorm.get(), "Densidad de hormigón")
            esp_carp = parsear_num(self.var_pp_espcarp.get(), "Espesor de carpeta") / 100.0
            dens_asf = parsear_num(self.var_pp_densasf.get(), "Densidad de asfalto")
            barandas = parsear_num(self.var_pp_barandas.get(), "Peso de barandas")
            pviga = parsear_num(self.var_pp_pviga.get(), "Peso por viga")
            nvigas = parsear_num(self.var_pp_nvigas.get(), "Número de vigas")
        except ValueError:
            self.lbl_pp.configure(text="")
            return
        losa = esp_losa * dens_horm * ancho
        carpeta = esp_carp * dens_asf * ancho
        vigas = pviga * nvigas
        total = losa + carpeta + barandas + vigas
        self.lbl_pp.configure(
            text="Total: %.3f Tnf/m   (losa %.3f + carpeta %.3f + barandas %.3f + vigas %.3f)"
                 % (total, losa, carpeta, barandas, vigas))

    def _alternar_ppv(self):
        incluir = self.var_ppv_incluir.get()
        self.chk_ppv_auto.configure(state=("normal" if incluir else "disabled"))
        self.cb_ppv_baranda.configure(state=("readonly" if incluir else "disabled"))
        if incluir:
            self._alternar_ppv_ancho()
        else:
            self.campo_ppv_ancho.configure(state="disabled")
        self._actualizar_ppv()

    def _alternar_ppv_ancho(self):
        auto = self.var_ppv_ancho_auto.get()
        if self.var_ppv_incluir.get():
            self.campo_ppv_ancho.configure(state=("disabled" if auto else "normal"))
        if auto:
            self.var_ppv_ancho.set(self.var_dist_S.get())
        self._actualizar_ppv()

    def _actualizar_ppv(self):
        """Recalcula el peso propio por viga en vivo, sin correr el cálculo completo."""
        if not hasattr(self, "lbl_ppv"):
            return
        if not self.var_ppv_incluir.get():
            self.lbl_ppv.configure(text="")
            return
        try:
            ancho_trib = parsear_num(self.var_ppv_ancho.get(), "Ancho tributario")
            esp_losa = parsear_num(self.var_pp_esplosa.get(), "Espesor de losa") / 100.0
            dens_horm = parsear_num(self.var_pp_denshorm.get(), "Densidad de hormigón")
            esp_carp = parsear_num(self.var_pp_espcarp.get(), "Espesor de carpeta") / 100.0
            dens_asf = parsear_num(self.var_pp_densasf.get(), "Densidad de asfalto")
            barandas = parsear_num(self.var_pp_barandas.get(), "Peso de barandas")
            pviga = parsear_num(self.var_pp_pviga.get(), "Peso por viga")
            nvigas = parsear_num(self.var_pp_nvigas.get(), "Número de vigas")
        except ValueError:
            self.lbl_ppv.configure(text="")
            return
        modo = _clave_de(MODOS_BARANDA, self.var_ppv_baranda.get(), "ninguna")
        losa = esp_losa * dens_horm * ancho_trib
        carpeta = esp_carp * dens_asf * ancho_trib
        if modo == "completa":
            baranda = barandas
        elif modo == "repartida":
            baranda = barandas / nvigas if nvigas > 0 else 0.0
        else:
            baranda = 0.0
        total = pviga + losa + carpeta + baranda
        self.lbl_ppv.configure(
            text="Total por viga: %.3f Tnf/m   (viga %.3f + losa %.3f + carpeta %.3f + "
                "baranda %.3f)" % (total, pviga, losa, carpeta, baranda))

    def _alternar_sep(self):
        estado = "normal" if self.var_sepvar.get() else "disabled"
        for campo in self.campos_sep:
            campo.configure(state=estado)
        self._actualizar_nota_sep()

    def _actualizar_nota_sep(self):
        """Deja en claro que, con el barrido activo, el offset del último eje de
        la tabla es sólo el punto de partida: quien manda es el rango."""
        if not hasattr(self, "lbl_sep"):
            return                              # todavía se está construyendo el panel
        filas = self.tabla_camion.tree.get_children()
        for iid in filas:
            self.tabla_camion.tree.item(iid, tags=())

        if not self.var_sepvar.get():
            self.tabla_camion.limpiar_rangos()
            self.lbl_sep.configure(
                text="Separación fija: se usa el offset del último eje tal como está en la tabla.")
            return
        if len(filas) < 2:
            self.tabla_camion.limpiar_rangos()
            self.lbl_sep.configure(text="Con un solo eje no hay separación que variar.")
            return

        iid_ultimo = filas[-1]
        self.tabla_camion.tree.item(iid_ultimo, tags=("barrido",))
        try:
            # ejes() ya resuelve el valor real si la celda está mostrando un
            # rango de una actualización anterior, así que el "punto de
            # partida" no cambia solo porque se edite Mín/Máx.
            ejes = self.tabla_camion.ejes()
            base = ejes[-2]["offset"]
            valor_real = ejes[-1]["offset"]
            smin = parsear_num(self.var_smin.get(), "Separación mínima")
            smax = parsear_num(self.var_smax.get(), "Separación máxima")
        except (ValueError, IndexError):
            self.lbl_sep.configure(text="")
            return

        extremo1, extremo2 = base - smin, base - smax
        self.tabla_camion.mostrar_rango(iid_ultimo, "%.2f a %.2f" % (extremo1, extremo2),
                                        valor_real)
        self.lbl_sep.configure(
            text="El %g m de la tabla es sólo el punto de partida:\n"
                 "el barrido lleva el último eje de %.2f a %.2f m\n"
                 "y se queda con la envolvente de todo el rango."
                 % (valor_real, extremo1, extremo2))

    def _cambiar_norma(self):
        """Cambia toda la metodología de golpe: vehículos, faja, impacto y combinación."""
        norma = self.var_norma.get()
        if norma == getattr(self, "norma_actual", None):
            return
        d = mp.NORMAS.get(norma)
        if d is None:                       # Personalizado: no se toca nada
            self.norma_actual = norma
            self._describir_norma()
            return
        if not messagebox.askyesno(
                APP,
                "Se reemplazarán los vehículos de diseño, la carga de faja, el impacto\n"
                "y la forma de combinar por los de %s.\n\n"
                "La geometría, el camión especial y el paso dx NO se tocan.\n\n"
                "¿Continuar?" % norma, parent=self):
            self.var_norma.set(getattr(self, "norma_actual", mp.HS20_44))
            return
        self._escribir_carga_diseno(mp.aplicar_norma(mp.Parametros(), norma))
        self._mostrar_dist_segun_norma(norma)

    def _describir_norma(self):
        d = mp.NORMAS.get(self.var_norma.get())
        self.lbl_norma.configure(
            text=d["metodologia"] if d else
            "Personalizado: se usan tal cual los valores que usted escriba abajo.")

    def _escribir_carga_diseno(self, par):
        """Vuelca en la interfaz sólo lo que define la norma, no la geometría."""
        self.var_norma.set(par.norma)
        self.norma_actual = par.norma
        self.tabla_camion.cargar(par.ejes_camion)
        self.nombre_camion = par.nombre_camion
        self.nombre_tandem = par.nombre_tandem
        self.tabla_camion.configure(text="Camión de diseño (%s)"
                                    % par.nombre_camion.replace("CAMIÓN ", ""))
        self.tabla_tandem.cargar(par.ejes_tandem)
        self.tabla_tandem.configure(text=par.nombre_tandem.capitalize())
        self.var_usar_tan.set(bool(par.usar_tandem))
        self.var_sepvar.set(bool(par.sep_variable))
        self.var_smin.set("%g" % par.sep_min)
        self.var_smax.set("%g" % par.sep_max)
        self.var_spaso.set("%g" % par.sep_paso)
        self.var_w.set("%.4f" % par.w_carril)
        self.var_pm.set("%.4f" % par.p_conc_momento)
        self.var_pv.set("%.4f" % par.p_conc_corte)
        self.var_dosconc.set(bool(par.dos_conc_momento_negativo))
        self.var_comb.set(COMBINACIONES[par.modo_combinacion])
        self.var_imp.set(IMPACTOS[par.impacto_modo])
        self.var_fd.set("%g" % par.factor_dinamico)
        self.var_may.set("%g" % par.factor_mayoracion)
        self._describir_norma()
        self._alternar_sep()

    def _campos_lrfd(self):
        """Campos de distribución LRFD (se leen siempre, aunque la norma sea otra)."""
        g = lambda k, n: parsear_num(self.vl[k].get(), n)
        return dict(
            ts_dist=g("ts_dist", "Espesor de losa"), L_dist=g("L_dist", "Luz L"),
            n_mod=g("n_mod", "Relación modular"), Ig_dist=g("Ig_dist", "Inercia"),
            Ag_dist=g("Ag_dist", "Área"), eg_dist=g("eg_dist", "Excentricidad"),
            nb_dist=int(round(g("nb_dist", "Número de vigas"))),
            de_dist=g("de_dist", "de"), w_calzada=g("w_calzada", "Ancho de calzada"),
            diafragmas=bool(self.var_diafragmas.get()),
            viga_dist=_clave_de(VIGAS_DIST, self.var_viga_dist.get(), "interior"))

    def leer_parametros(self):
        par = mp.Parametros(
            tramos=parsear_lista(self.var_tramos.get(), "Luces de los tramos"),
            rotulas=parsear_lista(self.var_rotulas.get(), "Rótulas", permitir_vacio=True),
            ejes_camion=self.tabla_camion.ejes(),
            ejes_tandem=self.tabla_tandem.ejes(),
            nombre_camion=getattr(self, "nombre_camion", "CAMIÓN"),
            usar_tandem=bool(self.var_usar_tan.get()),
            nombre_tandem=getattr(self, "nombre_tandem", "TÁNDEM"),
            w_carril=parsear_num(self.var_w.get(), "Carga uniforme de faja"),
            p_conc_momento=parsear_num(self.var_pm.get(), "Concentrada de momento"),
            p_conc_corte=parsear_num(self.var_pv.get(), "Concentrada de corte"),
            dos_conc_momento_negativo=bool(self.var_dosconc.get()),
            modo_combinacion=_clave_de(COMBINACIONES, self.var_comb.get(), "alternativa"),
            impacto_modo=_clave_de(IMPACTOS, self.var_imp.get(), "aashto_standard"),
            factor_mayoracion=parsear_num(self.var_may.get(), "Mayoración"),
            norma=self.var_norma.get(),
            dx=parsear_num(self.var_dx.get(), "Paso dx"),
            factor_dinamico=parsear_num(self.var_fd.get(), "Factor dinámico"),
            sep_variable=bool(self.var_sepvar.get()),
            sep_min=parsear_num(self.var_smin.get(), "Separación mínima"),
            sep_max=parsear_num(self.var_smax.get(), "Separación máxima"),
            sep_paso=parsear_num(self.var_spaso.get(), "Paso de la separación"),
            ejes_especial=self.tabla_especial.ejes(),
            incluir_especial=bool(self.var_incluir_esp.get()),
            nombre_especial=getattr(self, "nombre_especial", "CAMIÓN ESPECIAL"),
            incluir_peso_propio=bool(self.var_pp_incluir.get()),
            ancho_tablero=parsear_num(self.var_pp_ancho.get(), "Ancho del tablero"),
            espesor_losa=parsear_num(self.var_pp_esplosa.get(), "Espesor de losa") / 100.0,
            densidad_hormigon=parsear_num(self.var_pp_denshorm.get(), "Densidad de hormigón"),
            espesor_carpeta=parsear_num(self.var_pp_espcarp.get(), "Espesor de carpeta") / 100.0,
            densidad_asfalto=parsear_num(self.var_pp_densasf.get(), "Densidad de asfalto"),
            peso_barandas=parsear_num(self.var_pp_barandas.get(), "Peso de barandas"),
            peso_viga=parsear_num(self.var_pp_pviga.get(), "Peso por viga"),
            n_vigas=int(round(parsear_num(self.var_pp_nvigas.get(), "Número de vigas"))),
            incluir_distribucion=bool(self.var_dist_incluir.get()),
            tipo_viga=_clave_de(TIPOS_VIGA, self.var_dist_tipo.get(), "acero"),
            separacion_vigas=parsear_num(self.var_dist_S.get(), "Separación entre vigas"),
            n_vias_diseno=_clave_de(VIAS_DISENO, self.var_dist_vias.get(), 2),
            **self._campos_lrfd(),
            incluir_pp_por_viga=bool(self.var_ppv_incluir.get()),
            ancho_tributario=parsear_num(self.var_ppv_ancho.get(), "Ancho tributario"),
            modo_baranda_pp=_clave_de(MODOS_BARANDA, self.var_ppv_baranda.get(), "ninguna"))
        par.validar()
        return par

    def escribir_parametros(self, par):
        self.var_tramos.set(", ".join("%g" % L for L in par.tramos))
        self.var_rotulas.set(", ".join("%g" % r for r in par.rotulas))
        self.var_dx.set("%g" % par.dx)
        self._escribir_carga_diseno(par)
        self.tabla_especial.cargar(par.ejes_especial)
        self.var_incluir_esp.set(bool(par.incluir_especial))
        self.nombre_especial = par.nombre_especial
        self.var_pp_incluir.set(bool(par.incluir_peso_propio))
        self.var_pp_ancho.set("%g" % par.ancho_tablero)
        self.var_pp_esplosa.set("%g" % (par.espesor_losa * 100))
        self.var_pp_denshorm.set("%g" % par.densidad_hormigon)
        self.var_pp_espcarp.set("%g" % (par.espesor_carpeta * 100))
        self.var_pp_densasf.set("%g" % par.densidad_asfalto)
        self.var_pp_barandas.set("%g" % par.peso_barandas)
        self.var_pp_pviga.set("%g" % par.peso_viga)
        self.var_pp_nvigas.set("%g" % par.n_vigas)
        self._alternar_pp()
        self.var_dist_incluir.set(bool(par.incluir_distribucion))
        self.var_dist_tipo.set(TIPOS_VIGA[par.tipo_viga])
        self.var_dist_S.set("%g" % par.separacion_vigas)
        self.var_dist_vias.set(VIAS_DISENO[par.n_vias_diseno])
        for k in ("nb_dist", "ts_dist", "L_dist", "n_mod", "Ig_dist", "Ag_dist", "eg_dist",
                  "de_dist", "w_calzada"):
            self.vl[k].set("%g" % getattr(par, k))
        self.var_diafragmas.set(bool(par.diafragmas))
        self.var_viga_dist.set(VIGAS_DIST[par.viga_dist])
        self._alternar_dist()
        self._mostrar_dist_segun_norma(par.norma)
        self.var_ppv_incluir.set(bool(par.incluir_pp_por_viga))
        # El ancho tributario guardado puede diferir de la separación entre
        # vigas (el usuario lo desacopló con el checkbox "auto"): al abrir el
        # proyecto se respeta tal cual y se apaga el modo automático si no
        # coincide, para no pisar silenciosamente un valor distinto guardado.
        self.var_ppv_ancho_auto.set(abs(par.ancho_tributario - par.separacion_vigas) < 1e-9)
        self.var_ppv_ancho.set("%g" % par.ancho_tributario)
        self.var_ppv_baranda.set(MODOS_BARANDA[par.modo_baranda_pp])
        self._alternar_ppv()
        self._actualizar_gh()
        self._actualizar_impacto()

    # ------------------------------------------------------------- cálculo
    def calcular(self):
        if self.hilo is not None and self.hilo.is_alive():
            return
        try:
            par = self.leer_parametros()
            mp.Estructura(par.tramos, par.rotulas)      # valida topología antes de arrancar
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return

        self.cancelar.clear()
        self.btn_calcular.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.progreso["value"] = 0

        def avance(frac, texto):
            self.cola.put(("progreso", frac, texto))
            return not self.cancelar.is_set()

        def trabajo():
            try:
                res = mp.calcular(par, progreso=avance)
                self.cola.put(("listo", res, None))
            except mp.CalculoCancelado:
                self.cola.put(("cancelado", None, None))
            except Exception as e:                      # noqa: BLE001 - se informa al usuario
                self.cola.put(("error", e, None))

        self.hilo = threading.Thread(target=trabajo, daemon=True)
        self.hilo.start()

    def cancelar_calculo(self):
        self.cancelar.set()
        self.var_estado.set("Cancelando...")

    def _revisar_cola(self):
        try:
            while True:
                tipo, dato, texto = self.cola.get_nowait()
                if tipo == "progreso":
                    self.progreso["value"] = max(0.0, min(1.0, dato)) * 100
                    if texto:
                        self.var_estado.set(texto)
                elif tipo == "listo":
                    self._al_terminar(dato)
                elif tipo == "cancelado":
                    self._liberar("Cálculo cancelado.")
                elif tipo == "error":
                    self._liberar("Error: el cálculo no se completó.")
                    messagebox.showerror(APP, str(dato), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._revisar_cola)

    def _liberar(self, mensaje):
        self.btn_calcular.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        self.var_estado.set(mensaje)

    def _vista(self, res):
        """Resultados con los factores que marcó el usuario (cheap: sin barrido)."""
        dist_ok = bool(res.dist_g)
        return res.con_factores(impacto=self.var_f_imp.get(),
                                distribucion=self.var_f_dist.get() and dist_ok,
                                mayoracion=self.var_f_may.get(),
                                viga=_clave_de(VIGAS_DIST, self.var_f_viga.get(), "interior"))

    def _estado_checks_factores(self, res):
        self.chk_f_dist.configure(state="normal" if res.dist_g else "disabled")
        self.cb_f_viga.configure(state="readonly" if res.dist_lrfd else "disabled")
        if res.dist_lrfd:
            self.var_f_viga.set(VIGAS_DIST[res.parametros.viga_dist])
        if not res.dist_g:
            self.var_f_dist.set(False)

    def _refrescar_vista(self):
        if self.res_base is None:
            return
        claves = [self._clave(v) for v in (self.var_caso_g, self.var_caso_t)]
        self.resultados = self._vista(self.res_base)
        etiquetas = [c[0] for c in self._casos()]
        self.cb_g.configure(values=etiquetas)
        self.cb_t.configure(values=etiquetas)
        for var, k in zip((self.var_caso_g, self.var_caso_t), claves):
            var.set(self.resultados.envolventes[k].titulo if k in self.resultados.envolventes
                    else etiquetas[0])
        self.redibujar()
        self.redibujar_pp_viga()
        self.llenar_resumen()
        self.llenar_tabla()
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", self.resultados.texto_resumen())

    def _al_terminar(self, res):
        self.res_base = res
        self._estado_checks_factores(res)
        self.resultados = res = self._vista(res)
        self.progreso["value"] = 100
        avisos = res.estructura.avisos
        msg = "Cálculo terminado.  %s" % res.estructura.descripcion().splitlines()[-1]
        if avisos:
            msg += "   |  " + avisos[0]
        self._liberar(msg)
        self._actualizar_casos()
        self.redibujar()
        self._actualizar_chks_pp_viga()
        self.redibujar_pp_viga()
        self.llenar_resumen()
        self.llenar_tabla()
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", res.texto_resumen())

    # ------------------------------------------------------------- salidas
    def _casos(self):
        """[(etiqueta, clave)] de las envolventes disponibles, la combinada primero."""
        if self.resultados is None:
            return []
        orden = self.resultados.orden
        claves = ["combinada"] + [k for k in orden if k != "combinada"]
        return [(self.resultados.envolventes[k].titulo, k) for k in claves]

    def _actualizar_casos(self):
        """Refresca los selectores: el camión especial agrega una envolvente."""
        casos = self._casos()
        etiquetas = [c[0] for c in casos] or [SIN_RESULTADOS]
        for combo, var in ((self.cb_g, self.var_caso_g), (self.cb_t, self.var_caso_t)):
            previa = var.get()
            combo.configure(values=etiquetas)
            if previa not in etiquetas:
                var.set(etiquetas[0])

    def _clave(self, var):
        for nombre, clave in self._casos():
            if nombre == var.get():
                return clave
        return "combinada"

    def redibujar(self):
        if self.resultados is None:
            return
        gp.figura_envolvente(self.resultados, self._clave(self.var_caso_g), fig=self.figura,
                             unidad=self.var_unidad_g.get())
        self.lienzo.draw()

    def _activos_pp_viga(self):
        """Claves de las partidas/combinaciones de peso propio por viga con su
        checkbox marcado."""
        return [clave for clave, var in (
            ("pp_viga", self.var_pv_viga), ("pp_losa", self.var_pv_losa),
            ("pp_carpeta", self.var_pv_carpeta), ("pp_barandas", self.var_pv_barandas),
            ("pp_losa_viga", self.var_pv_losa_viga),
            ("pp_losa_viga_barandas", self.var_pv_losa_viga_barandas),
            ("pp_total", self.var_pv_total))
                if var.get()]

    def _actualizar_chks_pp_viga(self):
        """Habilita sólo los checkboxes de las partidas que el cálculo trajo
        (dependen de «Calcular el desglose» en Peso propio por viga)."""
        if not hasattr(self, "chks_pv"):
            return
        disponibles = set(self.resultados.componentes_pp_viga) if self.resultados else set()
        for clave, chk in self.chks_pv.items():
            chk.configure(state=("normal" if clave in disponibles else "disabled"))
        self.lbl_pv_aviso.configure(
            text="" if disponibles else
            "Active «Calcular el desglose» en Peso propio por viga y vuelva a calcular "
            "para ver este gráfico.")

    def redibujar_pp_viga(self):
        if self.resultados is None:
            return
        gp.figura_peso_propio_viga(self.resultados, self._activos_pp_viga(), fig=self.figura_pv,
                                   unidad=self.var_unidad_pv.get())
        self.lienzo_pv.draw()

    def exportar_png_pp_viga(self):
        if self.resultados is None or not self.resultados.componentes_pp_viga:
            messagebox.showinfo(
                APP, "Primero active «Calcular el desglose» en Peso propio por viga, "
                    "vuelva a calcular, y marque al menos una partida.", parent=self)
            return
        ruta = filedialog.asksaveasfilename(
            parent=self, defaultextension=".png", initialfile="Peso Propio por Viga.png",
            filetypes=[("Imagen PNG", "*.png")])
        if not ruta:
            return
        fig = gp.figura_peso_propio_viga(self.resultados, self._activos_pp_viga(),
                                         fig=Figure(figsize=(12, 9)), unidad=self.var_unidad_pv.get())
        fig.savefig(ruta, dpi=300)
        self.var_estado.set("Gráfico guardado: %s" % os.path.basename(ruta))

    def llenar_resumen(self):
        res = self.resultados
        self.tv_resumen.delete(*self.tv_resumen.get_children())
        est = res.estructura
        for _, clave in self._casos():
            env = res.envolventes[clave]
            padre = self.tv_resumen.insert("", "end", text=env.titulo, open=(clave == "combinada"))
            for i in range(est.n_sup):
                self.tv_resumen.insert(padre, "end", text="", values=(
                    "Reacción", "Apoyo %s" % est.nombres_apoyos[i],
                    "%.2f" % env.R_pos[i], "%.2f" % env.R_neg[i], "Tnf"))
            for i in range(est.n_tramos):
                self.tv_resumen.insert(padre, "end", text="", values=(
                    "Momento en tramo",
                    "%s-%s" % (est.nombres_apoyos[i], est.nombres_apoyos[i + 1]),
                    "%.2f  (x=%.2f m)" % (env.resumen["M_tram_pos"][i], env.resumen["M_tram_pos_x"][i]),
                    "%.2f  (x=%.2f m)" % (env.resumen["M_tram_neg"][i], env.resumen["M_tram_neg_x"][i]),
                    "Tnf·m"))
            for i in range(1, est.n_tramos):
                self.tv_resumen.insert(padre, "end", text="", values=(
                    "Momento en apoyo", "Apoyo %s" % est.nombres_apoyos[i],
                    "%.2f  (x=%.2f m)" % (env.resumen["M_apoy_pos"][i], env.resumen["M_apoy_pos_x"][i]),
                    "%.2f  (x=%.2f m)" % (env.resumen["M_apoy_neg"][i], env.resumen["M_apoy_neg_x"][i]),
                    "Tnf·m"))
            for i in range(est.n_tramos):
                self.tv_resumen.insert(padre, "end", text="", values=(
                    "Cortante absoluto",
                    "%s-%s" % (est.nombres_apoyos[i], est.nombres_apoyos[i + 1]),
                    "%.2f  (x=%.2f m)" % (env.resumen["V_max"][i], env.resumen["V_max_x"][i]),
                    "-", "Tnf"))

        criticas = res.separaciones_criticas()
        if criticas:
            seps = res.separaciones
            padre = self.tv_resumen.insert(
                "", "end", open=True,
                text="SEPARACIÓN CRÍTICA ENTRE EJES TRASEROS (%.2f a %.2f m, %d posiciones)"
                     % (seps[0], seps[-1], len(seps)))
            for nombre, x, s_pos, s_neg in criticas:
                self.tv_resumen.insert(padre, "end", text="", values=(
                    "Separación crítica", "%s  (x = %.2f m)" % (nombre, x),
                    "%.2f  para M(+)" % s_pos, "%.2f  para M(-)" % s_neg, "m"))

    def llenar_tabla(self):
        if self.resultados is None:
            return
        res = self.resultados
        env = res.envolventes[self._clave(self.var_caso_t)]
        self.tv_tabla.delete(*self.tv_tabla.get_children())
        n = len(res.X)
        salto = max(1, -(-n // MAX_FILAS_TABLA))
        for i in range(0, n, salto):
            self.tv_tabla.insert("", "end", iid=str(i), values=(
                "%.3f" % res.X[i], "%.3f" % env.V_max[i], "%.3f" % env.V_min[i],
                "%.3f" % env.M_max[i], "%.3f" % env.M_min[i]))
        if salto > 1:
            self.lbl_consulta.configure(
                text="Mostrando 1 de cada %d filas (%d de %d). La exportación a Excel "
                     "incluye todas." % (salto, len(self.tv_tabla.get_children()), n))
        else:
            self.lbl_consulta.configure(text="")

    def consultar_x(self):
        if self.resultados is None:
            messagebox.showinfo(APP, "Primero ejecute el cálculo.", parent=self)
            return
        try:
            x = parsear_num(self.var_x.get(), "x")
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        v = self.resultados.valores_en(x, self._clave(self.var_caso_t))
        self.lbl_consulta.configure(
            text="En x = %.3f m   ->   V máx = %+.3f Tnf   V mín = %+.3f Tnf   |   "
                 "M máx = %+.3f Tnf·m   M mín = %+.3f Tnf·m"
                 % (v["x"], v["V_max"], v["V_min"], v["M_max"], v["M_min"]))
        idx = min(range(len(self.resultados.X)),
                  key=lambda i: abs(self.resultados.X[i] - v["x"]))
        hijos = self.tv_tabla.get_children()
        if hijos:
            cercano = min(hijos, key=lambda iid: abs(int(iid) - idx))
            self.tv_tabla.selection_set(cercano)
            self.tv_tabla.see(cercano)

    # ----------------------------------------------------------- exportar
    def _hay_resultados(self):
        if self.resultados is None:
            messagebox.showinfo(APP, "Primero ejecute el cálculo.", parent=self)
            return False
        return True

    def exportar_excel(self):
        if not self._hay_resultados():
            return
        ruta = filedialog.asksaveasfilename(
            parent=self, defaultextension=".xlsx", initialfile="Fuerzas Internas por Via.xlsx",
            filetypes=[("Libro de Excel", "*.xlsx")])
        if not ruta:
            return
        try:
            self.resultados.exportar_excel(ruta)
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror(APP, "No se pudo guardar:\n%s" % e, parent=self)
            return
        self.var_estado.set("Planilla guardada: %s" % os.path.basename(ruta))

    def exportar_png(self):
        if not self._hay_resultados():
            return
        ruta = filedialog.asksaveasfilename(
            parent=self, defaultextension=".png", initialfile="Envolvente de Carga Viva.png",
            filetypes=[("Imagen PNG", "*.png")])
        if not ruta:
            return
        fig = gp.figura_envolvente(self.resultados, self._clave(self.var_caso_g),
                                   fig=Figure(figsize=(12, 9)), unidad=self.var_unidad_g.get())
        fig.savefig(ruta, dpi=300)
        self.var_estado.set("Gráfico guardado: %s" % os.path.basename(ruta))

    def exportar_reporte(self):
        if not self._hay_resultados():
            return
        ruta = filedialog.asksaveasfilename(
            parent=self, defaultextension=".txt", initialfile="Reporte envolventes.txt",
            filetypes=[("Texto", "*.txt")])
        if not ruta:
            return
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(self.resultados.texto_resumen())
        self.var_estado.set("Reporte guardado: %s" % os.path.basename(ruta))

    def copiar_resumen(self):
        if not self._hay_resultados():
            return
        est = self.resultados.estructura
        lineas = ["Estado\tMagnitud\tUbicación\tMáx (+)\tMín (-)\tUnidad"]
        for _, clave in self._casos():
            env = self.resultados.envolventes[clave]
            for i in range(est.n_sup):
                lineas.append("%s\tReacción\tApoyo %s\t%.2f\t%.2f\tTnf"
                              % (env.titulo, est.nombres_apoyos[i], env.R_pos[i], env.R_neg[i]))
            for i in range(est.n_tramos):
                lineas.append("%s\tMomento en tramo\t%s-%s\t%.2f\t%.2f\tTnf·m"
                              % (env.titulo, est.nombres_apoyos[i], est.nombres_apoyos[i + 1],
                                 env.resumen["M_tram_pos"][i], env.resumen["M_tram_neg"][i]))
            for i in range(1, est.n_tramos):
                lineas.append("%s\tMomento en apoyo\tApoyo %s\t%.2f\t%.2f\tTnf·m"
                              % (env.titulo, est.nombres_apoyos[i],
                                 env.resumen["M_apoy_pos"][i], env.resumen["M_apoy_neg"][i]))
            for i in range(est.n_tramos):
                lineas.append("%s\tCortante absoluto\t%s-%s\t%.2f\t\tTnf"
                              % (env.titulo, est.nombres_apoyos[i], est.nombres_apoyos[i + 1],
                                 env.resumen["V_max"][i]))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lineas))
        self.var_estado.set("Resumen copiado: puede pegarlo directamente en Excel.")

    def copiar_reporte(self):
        if not self._hay_resultados():
            return
        self.clipboard_clear()
        self.clipboard_append(self.resultados.texto_resumen())
        self.var_estado.set("Reporte copiado al portapapeles.")

    # ------------------------------------------------------------ proyecto
    def nuevo_proyecto(self):
        self.escribir_parametros(mp.Parametros())
        self.ruta_proyecto = None
        self.var_estado.set("Valores por defecto restaurados.")

    def guardar_proyecto(self):
        try:
            par = self.leer_parametros()
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=self)
            return
        ruta = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json", initialfile="proyecto puente.json",
            filetypes=[("Proyecto", "*.json")])
        if not ruta:
            return
        datos = {"tramos": par.tramos, "rotulas": par.rotulas,
                 "ejes_camion": par.ejes_camion, "ejes_tandem": par.ejes_tandem,
                 "w_carril": par.w_carril, "dx": par.dx,
                 "factor_dinamico": par.factor_dinamico,
                 "sep_variable": par.sep_variable, "sep_min": par.sep_min,
                 "sep_max": par.sep_max, "sep_paso": par.sep_paso,
                 "ejes_especial": par.ejes_especial,
                 "nombre_especial": par.nombre_especial,
                 "incluir_especial": par.incluir_especial,
                 "norma": par.norma, "nombre_camion": par.nombre_camion,
                 "usar_tandem": par.usar_tandem, "nombre_tandem": par.nombre_tandem,
                 "p_conc_momento": par.p_conc_momento, "p_conc_corte": par.p_conc_corte,
                 "dos_conc_momento_negativo": par.dos_conc_momento_negativo,
                 "modo_combinacion": par.modo_combinacion,
                 "impacto_modo": par.impacto_modo,
                 "factor_mayoracion": par.factor_mayoracion,
                 "incluir_peso_propio": par.incluir_peso_propio,
                 "ancho_tablero": par.ancho_tablero,
                 "espesor_losa": par.espesor_losa,
                 "densidad_hormigon": par.densidad_hormigon,
                 "espesor_carpeta": par.espesor_carpeta,
                 "densidad_asfalto": par.densidad_asfalto,
                 "peso_barandas": par.peso_barandas,
                 "peso_viga": par.peso_viga,
                 "n_vigas": par.n_vigas,
                 "incluir_distribucion": par.incluir_distribucion,
                 "tipo_viga": par.tipo_viga,
                 "separacion_vigas": par.separacion_vigas,
                 "n_vias_diseno": par.n_vias_diseno,
                 "lrfd_dist": {k: getattr(par, k) for k in (
                     "ts_dist", "L_dist", "n_mod", "Ig_dist", "Ag_dist", "eg_dist", "nb_dist",
                     "de_dist", "w_calzada", "diafragmas", "viga_dist")},
                 "incluir_pp_por_viga": par.incluir_pp_por_viga,
                 "ancho_tributario": par.ancho_tributario,
                 "modo_baranda_pp": par.modo_baranda_pp}
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
        self.ruta_proyecto = ruta
        self.var_estado.set("Proyecto guardado: %s" % os.path.basename(ruta))

    def abrir_proyecto(self):
        ruta = filedialog.askopenfilename(parent=self, filetypes=[("Proyecto", "*.json")])
        if not ruta:
            return
        try:
            with open(ruta, encoding="utf-8") as f:
                d = json.load(f)
            par = mp.Parametros(
                tramos=[float(v) for v in d["tramos"]],
                rotulas=[float(v) for v in d.get("rotulas", [])],
                ejes_camion=[{"P": float(e["P"]), "offset": float(e["offset"])}
                             for e in d["ejes_camion"]],
                ejes_tandem=[{"P": float(e["P"]), "offset": float(e["offset"])}
                             for e in d["ejes_tandem"]],
                w_carril=float(d["w_carril"]), dx=float(d["dx"]),
                factor_dinamico=float(d.get("factor_dinamico", 1.33)),
                sep_variable=bool(d.get("sep_variable", False)),
                sep_min=float(d.get("sep_min", 4.3)),
                sep_max=float(d.get("sep_max", 9.0)),
                sep_paso=float(d.get("sep_paso", 0.10)),
                ejes_especial=[{"P": float(e["P"]), "offset": float(e["offset"])}
                               for e in d.get("ejes_especial", [])],
                nombre_especial=d.get("nombre_especial", "CAMIÓN ESPECIAL"),
                incluir_especial=bool(d.get("incluir_especial", False)),
                norma=d.get("norma", mp.HS20_44),
                nombre_camion=d.get("nombre_camion", "CAMIÓN"),
                usar_tandem=bool(d.get("usar_tandem", True)),
                nombre_tandem=d.get("nombre_tandem", "TÁNDEM"),
                p_conc_momento=float(d.get("p_conc_momento", 0.0)),
                p_conc_corte=float(d.get("p_conc_corte", 0.0)),
                dos_conc_momento_negativo=bool(d.get("dos_conc_momento_negativo", False)),
                modo_combinacion=d.get("modo_combinacion", "suma"),
                impacto_modo=d.get("impacto_modo", "fijo"),
                factor_mayoracion=float(d.get("factor_mayoracion", 1.0)),
                incluir_peso_propio=bool(d.get("incluir_peso_propio", False)),
                ancho_tablero=float(d.get("ancho_tablero", 8.0)),
                espesor_losa=float(d.get("espesor_losa", 0.20)),
                densidad_hormigon=float(d.get("densidad_hormigon", 2.4)),
                espesor_carpeta=float(d.get("espesor_carpeta", 0.05)),
                densidad_asfalto=float(d.get("densidad_asfalto", 2.2)),
                peso_barandas=float(d.get("peso_barandas", 0.0)),
                peso_viga=float(d.get("peso_viga", 0.0)),
                n_vigas=int(d.get("n_vigas", 0)),
                incluir_distribucion=bool(d.get("incluir_distribucion", False)),
                tipo_viga=d.get("tipo_viga", "acero"),
                separacion_vigas=float(d.get("separacion_vigas", 2.0)),
                n_vias_diseno=int(d.get("n_vias_diseno", 2)),
                **{k: type(getattr(mp.Parametros, k))(v)
                   for k, v in d.get("lrfd_dist", {}).items()
                   if k in ("ts_dist", "L_dist", "n_mod", "Ig_dist", "Ag_dist", "eg_dist",
                            "nb_dist", "de_dist", "w_calzada", "diafragmas", "viga_dist")},
                incluir_pp_por_viga=bool(d.get("incluir_pp_por_viga", False)),
                ancho_tributario=float(d.get("ancho_tributario", 2.0)),
                modo_baranda_pp=d.get("modo_baranda_pp", "ninguna"))
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror(APP, "No se pudo leer el proyecto:\n%s" % e, parent=self)
            return
        self.escribir_parametros(par)
        self.ruta_proyecto = ruta
        self.var_estado.set("Proyecto abierto: %s" % os.path.basename(ruta))

    def acerca_de(self):
        messagebox.showinfo(
            APP,
            "Envolventes de carga viva AASHTO HL-93.\n\n"
            "Vigas continuas y vigas Gerber (rótulas internas), resueltas por el método\n"
            "directo de rigidez con EI constante. Las rótulas se introducen liberando el\n"
            "giro de un extremo de barra.\n\n"
            "Combinación: (1+IM)·(Camión o Tándem) + Carril, evaluada punto a punto.\n\n"
            "Motor: motor_puente.py   Gráficos: graficos_puente.py",
            parent=self)

    def _al_cerrar(self):
        if self.hilo is not None and self.hilo.is_alive():
            if not messagebox.askyesno(APP, "Hay un cálculo en curso. ¿Salir de todos modos?",
                                       parent=self):
                return
            self.cancelar.set()
        self.destroy()


if __name__ == "__main__":
    Aplicacion().mainloop()
