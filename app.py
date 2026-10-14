import streamlit as st
import json
import os
from datetime import datetime, date, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from almacenamiento import cargar_datos, guardar_datos, usando_github, exportar_json
import pandas as pd
import finanzas

DATA_FILE = "data.json"

MESES_ESPANOL = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

def producto_disponible(fecha_llegada_str):
    if not fecha_llegada_str or fecha_llegada_str == "Por definir" or fecha_llegada_str.strip() == "":
        return True
    try:
        f_llegada = datetime.strptime(fecha_llegada_str, "%Y-%m-%d").date()
        return f_llegada <= date.today()
    except:
        return True

def evaluar_regla_liberacion_miercoles(datos):
    '''
    Regla:
    Sólo se evalúa cuando HOY sea Miércoles a partir de las 9:00 AM (o posterior en el ciclo del miércoles).
    - Si no tiene fecha de llegada, se asume que llegó esta misma semana.
    - Si un pedido llega en Domingo (6), Lunes (0) o Martes (1), NO se libera este miércoles, pasa al próximo miércoles.
    - Si un pedido llega en Miércoles (2), Jueves (3), Viernes (4) o Sábado (5), se libera en el miércoles correspondiente.
    '''
    ahora = datetime.now()
    
    # ÚNICAMENTE liberar si el día actual es Miércoles a partir de las 9:00 AM, o si ya pasó esa hora del miércoles actual.
    # weekday: 0=Lun, 1=Mar, 2=Mié, 3=Jue, 4=Vie, 5=Sáb, 6=Dom
    
    # Calcular el último miércoles a las 9am
    dias_desde_miercoles = (ahora.weekday() - 2) % 7
    ultimo_miercoles = ahora - timedelta(days=dias_desde_miercoles)
    ultimo_miercoles_9am = ultimo_miercoles.replace(hour=9, minute=0, second=0, microsecond=0)
    
    if ahora < ultimo_miercoles_9am:
        # Aún no son las 9:00 AM del miércoles actual (o estamos en Lun/Mar antes del Mié de esta semana)
        return False

    ultima_liberacion_str = datos.get("ultima_liberacion_timestamp", "")
    
    # Evitar re-ejecutar si ya se aplicó la liberación de este miércoles
    if ultima_liberacion_str == ultimo_miercoles_9am.strftime("%Y-%m-%d %H:%M"):
        return False

    productos = datos.get("productos", {})
    hubo_cambios = False
    
    for p_nombre, p_info in list(productos.items()):
        fecha_lleg_str = p_info.get("fecha_llegada", "")
        
        if not fecha_lleg_str or fecha_lleg_str == "Por definir" or fecha_lleg_str.strip() == "":
            # Asumimos que llegó durante la semana de trabajo actual
            f_ref = ultimo_miercoles_9am.date() - timedelta(days=3) # Ref: Domingo/Lunes previo
        else:
            try:
                f_ref = datetime.strptime(fecha_lleg_str, "%Y-%m-%d").date()
            except:
                f_ref = ultimo_miercoles_9am.date()
                
        w_day = f_ref.weekday() # 0:Lun, 1:Mar, 2:Mié, 3:Jue, 4:Vie, 5:Sáb, 6:Dom
        
        # Domingo, Lunes, Martes -> Se salta este miércoles (pasa al siguiente)
        if w_day in [6, 0, 1]:
            # Su fecha de evaluación es el miércoles de la SIGUIENTE semana
            dias_a_sig_miercoles = (2 - w_day) % 7
            if dias_a_sig_miercoles == 0:
                dias_a_sig_miercoles = 7
            miercoles_evaluacion = f_ref + timedelta(days=dias_a_sig_miercoles + 7)
        else:
            dias_a_sig_miercoles = (2 - w_day) % 7
            if dias_a_sig_miercoles == 0:
                dias_a_sig_miercoles = 7
            miercoles_evaluacion = f_ref + timedelta(days=dias_a_sig_miercoles)
            
        fecha_eval_dt = datetime.combine(miercoles_evaluacion, datetime.min.time()).replace(hour=9)
        
        # Sólo si el último miércoles a las 9am ya alcanzó o superó la fecha de evaluación de este producto
        if ultimo_miercoles_9am >= fecha_eval_dt:
            tipo = p_info.get("tipo", "normal")
            clientes_data = p_info.get("clientes", {})
            
            if tipo == "normal":
                a_eliminar_cli = []
                for cli, info in clientes_data.items():
                    pagado = info.get("pagado", False) if isinstance(info, dict) else False
                    entregado = info.get("entregado", False) if isinstance(info, dict) else False
                    if not pagado and not entregado:  # lo ya entregado NO se libera (el cliente ya lo tiene)
                        cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                        datos["liberados_no_recogidos"].append({
                            "cliente": cli,
                            "producto": p_nombre,
                            "detalle": "",
                            "cantidad": cant,
                            "precio_u": p_info.get("precio", 0.0),
                            "fecha_liberacion": ahora.strftime("%d/%m/%Y %H:%M"),
                            "motivo": "Liberado por falta de pago (Miércoles 9:00 AM)"
                        })
                        a_eliminar_cli.append(cli)
                for cli in a_eliminar_cli:
                    del p_info["clientes"][cli]
                    hubo_cambios = True
            else:
                if isinstance(clientes_data, list):
                    nuevos_items = []
                    for item in clientes_data:
                        pagado = item.get("pagado", False)
                        if not pagado and not item.get("entregado", False):  # lo ya entregado NO se libera
                            datos["liberados_no_recogidos"].append({
                                "cliente": item.get("cliente", ""),
                                "producto": p_nombre,
                                "detalle": item.get("detalle", ""),
                                "cantidad": item.get("cantidad", 1),
                                "precio_u": item.get("precio", 0.0),
                                "fecha_liberacion": ahora.strftime("%d/%m/%Y %H:%M"),
                                "motivo": "Liberado por falta de pago (Miércoles 9:00 AM)"
                            })
                            hubo_cambios = True
                        else:
                            nuevos_items.append(item)
                    p_info["clientes"] = nuevos_items

    datos["ultima_liberacion_timestamp"] = ultimo_miercoles_9am.strftime("%Y-%m-%d %H:%M")
    if hubo_cambios:
        guardar_datos(datos)
    return hubo_cambios

def verificar_y_archivar_productos(datos):
    productos = datos.get("productos", {})
    productos_a_archivar = []
    
    for p_nombre, p_info in productos.items():
        if not producto_disponible(p_info.get("fecha_llegada", "")):
            continue

        tipo = p_info.get("tipo", "normal")
        clientes_data = p_info.get("clientes", {})
        
        todos_items = []
        if tipo == "normal":
            for cli, info in clientes_data.items():
                if isinstance(info, dict):
                    todos_items.append(info)
                else:
                    todos_items.append({"cantidad": info, "entregado": False, "pagado": False})
        else:
            if isinstance(clientes_data, list):
                todos_items = clientes_data
            elif isinstance(clientes_data, dict):
                for cli, info in clientes_data.items():
                    if isinstance(info, dict):
                        todos_items.append(info)
        
        if todos_items and all(item.get("entregado", False) and item.get("pagado", False) for item in todos_items):
            costo_inv = p_info.get("costo_inversion", 0.0)
            venta_total = 0.0
            cant_total = 0
            
            if tipo == "normal":
                precio = p_info.get("precio", 0.0)
                for cli, info in clientes_data.items():
                    cant = info.get("cantidad", 0) if isinstance(info, dict) else info
                    cant_total += cant
                    venta_total += cant * precio
            else:
                for item in clientes_data:
                    cant = item.get("cantidad", 1)
                    prec = item.get("precio", 0.0)
                    cant_total += cant
                    venta_total += cant * prec
            
            ganancia_neta = venta_total - costo_inv
            
            ahora = datetime.now()
            mes_actual = f"{MESES_ESPANOL[ahora.month]} {ahora.year}"
            
            registro_mensual = {
                "mes": mes_actual,
                "producto": p_nombre,
                "cantidad": cant_total,
                "costo": costo_inv,
                "venta": venta_total,
                "ganancia": ganancia_neta,
                "fecha_archivo": ahora.strftime("%d/%m/%Y")
            }
            
            datos["historial_mensual"].append(registro_mensual)
            productos_a_archivar.append(p_nombre)
            
    for p_nombre in productos_a_archivar:
        del datos["productos"][p_nombre]
        
    if productos_a_archivar:
        guardar_datos(datos)

def generar_pdf_resumen(datos):
    pdf_filename = "temp_resumen.pdf"
    doc = SimpleDocTemplate(pdf_filename, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    story = []
    
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle('Titulo', parent=styles['Heading1'], fontSize=14, alignment=1, spaceAfter=6)
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=9, alignment=1, spaceAfter=15, textColor=colors.gray)
    cliente_style = ParagraphStyle('ClienteTitle', parent=styles['Heading2'], fontSize=11, spaceBefore=10, spaceAfter=4, textColor=colors.black)
    
    story.append(Paragraph("SISTEMA DE VENTAS CINEPEACHY - RESUMEN GENERAL", titulo_style))
    story.append(Paragraph(f"Fecha de emisión: {datetime.now().strftime('%d/%m/%Y %H:%M')}", sub_style))
    
    prods_en_sem = datos.get("productos", {})
    
    cuentas_clientes = {}
    for p_nombre, p_info in prods_en_sem.items():
        if not producto_disponible(p_info.get("fecha_llegada", "")):
            continue

        tipo = p_info.get("tipo", "normal")
        clientes_data = p_info.get("clientes", {})
        
        if tipo == "normal":
            precio = p_info.get("precio", 0.0)
            for cli, info in clientes_data.items():
                if isinstance(info, dict) and info.get("entregado", False) and info.get("pagado", False):
                    continue
                if cli not in cuentas_clientes:
                    cuentas_clientes[cli] = []
                cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                subtotal = cant * precio
                pagado = info.get("pagado", False) if isinstance(info, dict) else False
                cuentas_clientes[cli].append((p_nombre, cant, precio, subtotal, pagado))
        else:
            if isinstance(clientes_data, list):
                for item in clientes_data:
                    if item.get("entregado", False) and item.get("pagado", False):
                        continue
                    cli = item.get("cliente", "")
                    detalle = item.get("detalle", "")
                    cant = item.get("cantidad", 1)
                    precio = item.get("precio", 0.0)
                    pagado = item.get("pagado", False)
                    if cli:
                        if cli not in cuentas_clientes:
                            cuentas_clientes[cli] = []
                        subtotal = cant * precio
                        nombre_prod_completo = f"{p_nombre} ({detalle})" if detalle else p_nombre
                        cuentas_clientes[cli].append((nombre_prod_completo, cant, precio, subtotal, pagado))
            elif isinstance(clientes_data, dict):
                for cli, info_cli in clientes_data.items():
                    if isinstance(info_cli, dict) and info_cli.get("entregado", False) and info_cli.get("pagado", False):
                        continue
                    if cli not in cuentas_clientes:
                        cuentas_clientes[cli] = []
                    if isinstance(info_cli, dict):
                        detalle = info_cli.get("detalle", "")
                        cant = info_cli.get("cantidad", 1)
                        precio = info_cli.get("precio", 0.0)
                        pagado = info_cli.get("pagado", False)
                        subtotal = cant * precio
                        nombre_prod_completo = f"{p_nombre} ({detalle})" if detalle else p_nombre
                        cuentas_clientes[cli].append((nombre_prod_completo, cant, precio, subtotal, pagado))
            
    if not cuentas_clientes:
        story.append(Paragraph("No hay pedidos pendientes de entrega o cobro en este momento.", styles['Italic']))
    else:
        gran_total = 0
        for cli in sorted(cuentas_clientes.keys()):
            items = cuentas_clientes[cli]
            total_cli = sum(item[3] for item in items if not item[4])
            gran_total += total_cli
            
            story.append(Paragraph(f"CLIENTE: {cli.upper()}", cliente_style))
            
            table_data = [["Producto / Detalle", "Cantidad", "Precio U.", "Subtotal", "Estatus"]]
            for prod, cant, precio, subtotal, pag in items:
                estatus_txt = "(Pagado)" if pag else f"${subtotal:,.2f}"
                table_data.append([prod, str(cant), f"${precio:,.2f}", f"${subtotal:,.2f}", estatus_txt])
            
            table_data.append(["", "", "", "Pendiente por Cobrar:", f"${total_cli:,.2f}"])
            
            t = Table(table_data, colWidths=[200, 60, 70, 80, 90])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                ('ALIGN', (1,0), (-1,-1), 'CENTER'),
                ('ALIGN', (3,1), (3,-1), 'RIGHT'),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('GRID', (0,0), (-1,-2), 0.5, colors.grey),
                ('LINEBELOW', (0,-1), (-1,-1), 1, colors.black),
                ('FONTNAME', (3,-1), (-1,-1), 'Helvetica-Bold'),
            ]))
            story.append(t)
            story.append(Spacer(1, 10))
            
        story.append(Spacer(1, 10))
        t_total = Table([[f"TOTAL PENDIENTE DE COBRO GENERAL: ${gran_total:,.2f}"]], colWidths=[500])
        t_total.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 11),
            ('BOX', (0,0), (-1,-1), 1, colors.black),
        ]))
        story.append(t_total)
        
    doc.build(story)
    with open(pdf_filename, "rb") as f:
        pdf_bytes = f.read()
    if os.path.exists(pdf_filename):
        os.remove(pdf_filename)
    return pdf_bytes

st.set_page_config(page_title="Sistema de Ventas Cinepeachy", page_icon="🍑", layout="wide")

if "data" not in st.session_state:
    st.session_state.data = cargar_datos()

datos = st.session_state.data

if finanzas.migrar_cobros(datos):
    guardar_datos(datos)

evaluar_regla_liberacion_miercoles(datos)
verificar_y_archivar_productos(datos)

st.title("🍑 Sistema de Ventas Cinepeachy")

st.sidebar.header("⚙️ Panel de Control")

chk_seguridad_liberacion = st.sidebar.checkbox("☑️ Confirmar forzar liberación", value=False, help="Marca esta casilla para habilitar el botón de forzar liberación.")
if st.sidebar.button("⚡ Forzar Liberación de No Pagados Ahora", help="Aplica manualmente la regla de liberación de los miércoles a las 9:00 AM"):
    if chk_seguridad_liberacion:
        datos["ultima_liberacion_timestamp"] = ""
        res = evaluar_regla_liberacion_miercoles(datos)
        guardar_datos(datos)
        st.sidebar.success("¡Regla de liberación ejecutada con éxito!")
        st.rerun()
    else:
        st.sidebar.error("Debes marcar la casilla de confirmación para forzar la liberación.")

if st.sidebar.button("💾 Guardar Cambios Ahora", type="primary"):
    guardar_datos(datos)
    st.sidebar.success("¡Cambios guardados correctamente!")

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔄 Migración y Respaldo")

# 1. Download JSON button
st.sidebar.caption("☁️ Guardando en GitHub (persistente)" if usando_github() else "💻 Guardando en este equipo")
st.sidebar.download_button(
    label="📥 Descargar Base de Datos (.json)",
    data=exportar_json(datos),
    file_name=f"data_{datetime.now():%Y%m%d}.json",
    mime="application/json",
    help="Descarga tu archivo de datos como respaldo extra."
)

# 2. Upload JSON file uploader for migration / restoring
uploaded_json_file = st.sidebar.file_uploader("📤 Restaurar / Migrar (.json)", type=["json"], help="Sube un archivo data.json previo para actualizar o migrar tu base de datos.")
if uploaded_json_file is not None:
    try:
        nuevo_contenido_json = json.load(uploaded_json_file)
        # Validate minimal keys
        if isinstance(nuevo_contenido_json, dict):
            guardar_datos(nuevo_contenido_json)
            st.sidebar.success("¡Base de datos migrada/restaurada con éxito! Recargando...")
            st.rerun()
        else:
            st.sidebar.error("El archivo JSON no tiene el formato correcto.")
    except Exception as e:
        st.sidebar.error(f"Error al leer el archivo JSON: {e}")

pdf_bytes = generar_pdf_resumen(datos)
st.sidebar.download_button(
    label="📄 Descargar Resumen General (PDF)",
    data=pdf_bytes,
    file_name="Resumen_Ventas_Cinepeachy.pdf",
    mime="application/pdf"
)

tab_gestion, tab_buscador, tab_proximas, tab_disponibles, tab_historial, tab_clientes = st.tabs([
    "📋 Gestión",
    "🔍 Cuentas",
    "🚚 Entregas",
    "📦 Producto",
    "📊 Historial",
    "👥 Registro"
])

with tab_gestion:
    st.subheader("📋 Catálogo General de Productos")
    
    with st.expander("➕ Agregar Nuevo Producto o Lote", expanded=False):
        with st.form("form_nuevo_producto", clear_on_submit=True):
            st.markdown("### Nuevo Producto / Categoría")
            nuevo_prod = st.text_input("Nombre del Producto o Catálogo (ej. Pistaches o Zapatos Andrea)")
            
            col_inf1, col_inf2, col_inf3 = st.columns(3)
            with col_inf1:
                unidades_comp = st.number_input("Unidades Compradas / Lote", min_value=1, value=10, step=1)
            with col_inf2:
                costo_inv = st.number_input("Costo Total Inversión ($)", min_value=0.0, step=10.0, value=0.0)
            with col_inf3:
                tiene_fecha = st.checkbox("¿Programar fecha de llegada?", value=False)
                fecha_llegada_val = st.date_input("Fecha Estimada de Llegada", value=date.today())
            
            es_catalogo = st.checkbox("¿Es producto de catálogo con precios individuales (ej. Zapatos Andrea)?")
            
            precio_base = 0.0
            if not es_catalogo:
                precio_base = st.number_input("Precio Estándar de Venta ($)", min_value=0.0, step=1.0, value=50.0)
            
            submit_prod = st.form_submit_button("Guardar Producto")
            
            if submit_prod:
                if nuevo_prod:
                    if nuevo_prod not in datos["productos"]:
                        f_str = fecha_llegada_val.strftime("%Y-%m-%d") if tiene_fecha else ""
                        datos["productos"][nuevo_prod] = {
                            "tipo": "catalogo" if es_catalogo else "normal",
                            "precio": precio_base if not es_catalogo else 0.0,
                            "costo_inversion": costo_inv,
                            "unidades_compradas": unidades_comp,
                            "fecha_llegada": f_str,
                            "clientes": [] if es_catalogo else {}
                        }
                        guardar_datos(datos)
                        st.session_state.select_prod_gestion = nuevo_prod
                        st.success(f"Producto '{nuevo_prod}' agregado y seleccionado.")
                        st.rerun()
                    else:
                        st.warning("El producto ya existe.")
                else:
                    st.error("Escribe un nombre de producto válido.")

    productos_activos = datos.get("productos", {})

    if not productos_activos:
        st.info("No hay productos en el catálogo todavía.")
    else:
        nombres_productos = list(productos_activos.keys())
        
        if "select_prod_gestion" not in st.session_state or st.session_state.select_prod_gestion not in nombres_productos:
            st.session_state.select_prod_gestion = nombres_productos[0]
            
        st.markdown("---")
        prod_nombre = st.selectbox(
            "🛍️ Seleccionar Producto para ver Stock y Anotar Pedido", 
            options=nombres_productos, 
            key="select_prod_gestion"
        )
        
        if prod_nombre:
            prod_info = productos_activos[prod_nombre]
            tipo_prod = prod_info.get("tipo", "normal")
            costo_inversion = prod_info.get("costo_inversion", 0.0)
            unidades_compradas = prod_info.get("unidades_compradas", 0)
            fecha_llegada_str = prod_info.get("fecha_llegada", "")
            
            if fecha_llegada_str and fecha_llegada_str != "Por definir" and fecha_llegada_str.strip() != "":
                try:
                    f_llegada_default = datetime.strptime(fecha_llegada_str, "%Y-%m-%d").date()
                    tiene_fecha_def = True
                except:
                    f_llegada_default = date.today()
                    tiene_fecha_def = False
            else:
                f_llegada_default = date.today()
                tiene_fecha_def = False

            disponible = producto_disponible(fecha_llegada_str)
            
            clientes_data = prod_info.get("clientes", {} if tipo_prod == "normal" else [])
            unidades_pedidas = 0
            venta_estimada = 0.0
            
            if tipo_prod == "normal":
                precio_unit = prod_info.get("precio", 0.0)
                for cli, info in clientes_data.items():
                    cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                    unidades_pedidas += cant
                venta_estimada = unidades_pedidas * precio_unit
            else:
                if isinstance(clientes_data, list):
                    for item in clientes_data:
                        cant = item.get("cantidad", 1)
                        prec = item.get("precio", 0.0)
                        unidades_pedidas += cant
                        venta_estimada += cant * prec
                elif isinstance(clientes_data, dict):
                    for cli, info in clientes_data.items():
                        cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                        prec = info.get("precio", 0.0) if isinstance(info, dict) else 0.0
                        unidades_pedidas += cant
                        venta_estimada += cant * prec

            unidades_disponibles = unidades_compradas - unidades_pedidas
            ganancia_estimada = venta_estimada - costo_inversion

            col_p_title, col_p_del = st.columns([3, 1])
            with col_p_title:
                st.markdown(f"### Gestión de: **{prod_nombre}**")
            with col_p_del:
                with st.form(f"form_eliminar_prod_{prod_nombre}"):
                    chk_conf_del = st.checkbox("Confirmar eliminación de producto", key=f"chk_conf_del_{prod_nombre}")
                    submit_del_prod = st.form_submit_button("🗑️ Eliminar Producto")
                    if submit_del_prod:
                        if chk_conf_del:
                            del datos["productos"][prod_nombre]
                            guardar_datos(datos)
                            prods_rest = list(datos.get("productos", {}).keys())
                            if prods_rest:
                                st.session_state.select_prod_gestion = prods_rest[0]
                            st.success(f"Producto '{prod_nombre}' eliminado.")
                            st.rerun()
                        else:
                            st.error("Debes marcar la casilla de confirmación para eliminar el producto.")

            if disponible:
                st.success("✅ **Estatus del Producto:** Disponible / Ya disponible (Inventario y costos activos en finanzas)")
            else:
                st.warning(f"⏳ **Estatus del Producto:** En tránsito / Aún no disponible (Llega el {fecha_llegada_str}). Sus costos e inversiones están congelados y no suman al balance financiero todavía.")

            c_st1, c_st2, c_st3, c_st4 = st.columns(4)
            c_st1.metric("📦 Compradas", unidades_compradas)
            c_st2.metric("🛒 Pedidas / Vendidas", unidades_pedidas)
            if unidades_disponibles >= 0:
                c_st3.metric("✨ Disponibles", unidades_disponibles)
            else:
                c_st3.metric("⚠️ Sobrepedido", unidades_disponibles, delta_color="inverse")
            c_st4.metric("📈 Ganancia Estimada", f"${ganancia_estimada:,.2f}")

            info_fecha_txt = fecha_llegada_str if fecha_llegada_str else "Inmediata / Sin fecha"
            st.info(f"📅 **Llegada:** {info_fecha_txt}  |  💰 **Inversión:** ${costo_inversion:,.2f}  |  💵 **Venta Total Esperada:** ${venta_estimada:,.2f}")

            with st.form(f"form_info_prod_{prod_nombre}"):
                ip1, ip2, ip3, ip4 = st.columns(4)
                with ip1:
                    new_uc = st.number_input("U. Compradas", value=int(unidades_compradas), min_value=1, key=f"uc_{prod_nombre}")
                with ip2:
                    new_ci = st.number_input("Costo Inv. ($)", value=float(costo_inversion), min_value=0.0, key=f"ci_{prod_nombre}")
                with ip3:
                    new_tiene_f = st.checkbox("Programar fecha", value=tiene_fecha_def, key=f"chk_f_{prod_nombre}")
                    new_fl = st.date_input("Fecha Llegada", value=f_llegada_default, key=f"fl_date_{prod_nombre}")
                with ip4:
                    if tipo_prod == "normal":
                        new_pr = st.number_input("Precio U. ($)", value=float(prod_info.get("precio", 0.0)), min_value=0.0, key=f"pr_{prod_nombre}")
                    else:
                        st.write("Catálogo (Varios)")
                        new_pr = 0.0
                
                submit_upd_prod = st.form_submit_button("💾 Actualizar Lote e Inversión")
                if submit_upd_prod:
                    prod_info["unidades_compradas"] = new_uc
                    prod_info["costo_inversion"] = new_ci
                    prod_info["fecha_llegada"] = new_fl.strftime("%Y-%m-%d") if new_tiene_f else ""
                    if tipo_prod == "normal":
                        prod_info["precio"] = new_pr
                    guardar_datos(datos)
                    st.success("¡Datos del lote actualizados!")
                    st.rerun()

            st.divider()
            st.markdown("#### Anotar Pedido de Cliente")
            
            if tipo_prod == "normal":
                with st.form(f"form_cliente_{prod_nombre}", clear_on_submit=True):
                    c1, c2, c3 = st.columns([2, 1, 1])
                    lista_clientes_global = datos.get("clientes", [])
                    
                    with c1:
                        cliente_elegido = st.selectbox("Cliente", options=["-- Seleccionar --"] + lista_clientes_global, key=f"sel_c_{prod_nombre}")
                        cliente_nuevo_input = st.text_input("O nuevo cliente:", key=f"new_c_{prod_nombre}")
                    with c2:
                        cantidad = st.number_input("Cantidad", min_value=1, value=1, step=1, key=f"cant_{prod_nombre}")
                    with c3:
                        st.write("")
                        st.write("")
                        submit_pedido = st.form_submit_button("➕ Anotar Pedido")
                    
                    if submit_pedido:
                        cliente_final = cliente_nuevo_input.strip() if cliente_nuevo_input.strip() else cliente_elegido
                        if cliente_final == "-- Seleccionar --" or not cliente_final:
                            st.error("Selecciona o escribe un cliente válido.")
                        else:
                            if cliente_final not in datos["clientes"]:
                                datos["clientes"].append(cliente_final)
                                datos["clientes"].sort()
                            
                            if "clientes" not in prod_info or not isinstance(prod_info["clientes"], dict):
                                prod_info["clientes"] = {}
                            
                            if cliente_final in prod_info["clientes"]:
                                ant = prod_info["clientes"][cliente_final]
                                cant_ant = ant.get("cantidad", 0) if isinstance(ant, dict) else ant
                                prod_info["clientes"][cliente_final] = {
                                    "cantidad": cant_ant + cantidad,
                                    "entregado": ant.get("entregado", False) if isinstance(ant, dict) else False,
                                    "pagado": ant.get("pagado", False) if isinstance(ant, dict) else False
                                }
                            else:
                                prod_info["clientes"][cliente_final] = {
                                    "cantidad": cantidad,
                                    "entregado": False,
                                    "pagado": False
                                }
                            guardar_datos(datos)
                            st.success(f"¡Registrado {cantidad}x para {cliente_final}!")
                            st.rerun()
            else:
                with st.form(f"form_cliente_cat_{prod_nombre}", clear_on_submit=True):
                    c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
                    lista_clientes_global = datos.get("clientes", [])
                    
                    with c1:
                        cliente_elegido = st.selectbox("Cliente", options=["-- Seleccionar --"] + lista_clientes_global, key=f"sel_cc_{prod_nombre}")
                        cliente_nuevo_input = st.text_input("O nuevo cliente:", key=f"new_cc_{prod_nombre}")
                    with c2:
                        detalle_articulo = st.text_input("Detalle / Modelo (ej. Adidas 200)", key=f"det_{prod_nombre}")
                    with c3:
                        precio_articulo = st.number_input("Precio ($)", min_value=0.0, step=10.0, value=200.0, key=f"p_art_{prod_nombre}")
                    with c4:
                        cant_articulo = st.number_input("Cant.", min_value=1, value=1, step=1, key=f"cant_art_{prod_nombre}")
                    
                    submit_pedido_cat = st.form_submit_button("➕ Anotar Artículo de Catálogo")
                    
                    if submit_pedido_cat:
                        cliente_final = cliente_nuevo_input.strip() if cliente_nuevo_input.strip() else cliente_elegido
                        if cliente_final == "-- Seleccionar --" or not cliente_final:
                            st.error("Selecciona o escribe un cliente válido.")
                        else:
                            if cliente_final not in datos["clientes"]:
                                datos["clientes"].append(cliente_final)
                                datos["clientes"].sort()
                            
                            if "clientes" not in prod_info:
                                prod_info["clientes"] = []
                            elif isinstance(prod_info["clientes"], dict):
                                old_dict = prod_info["clientes"]
                                prod_info["clientes"] = []
                                for dc, dval in old_dict.items():
                                    if isinstance(dval, dict):
                                        prod_info["clientes"].append({
                                            "cliente": dc, "detalle": dval.get("detalle",""), "precio": dval.get("precio",0.0), "cantidad": dval.get("cantidad",1),
                                            "entregado": dval.get("entregado", False), "pagado": dval.get("pagado", False)
                                        })
                                    else:
                                        prod_info["clientes"].append({
                                            "cliente": dc, "detalle": "", "precio": 0.0, "cantidad": dval,
                                            "entregado": False, "pagado": False
                                        })
                            
                            prod_info["clientes"].append({
                                "cliente": cliente_final,
                                "detalle": detalle_articulo,
                                "precio": precio_articulo,
                                "cantidad": cant_articulo,
                                "entregado": False,
                                "pagado": False
                            })
                            guardar_datos(datos)
                            st.success(f"¡Registrado {detalle_articulo} para {cliente_final}!")
                            st.rerun()

            st.markdown("#### Detalle actual de este producto:")
            if tipo_prod == "normal":
                if not clientes_data:
                    st.info("Aún no hay clientes anotados.")
                else:
                    precio_u = prod_info.get("precio", 0.0)
                    for cli, info in list(clientes_data.items()):
                        cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                        ent = "✅ Sí" if (isinstance(info, dict) and info.get("entregado")) else "❌ No"
                        pag = "✅ Sí" if (isinstance(info, dict) and info.get("pagado")) else "❌ No"
                        sub = cant * precio_u
                        
                        col_d1, col_d2, col_d3 = st.columns([2.5, 1.5, 1])
                        with col_d1:
                            st.markdown(f"**👤 {cli}** (Cant: {cant} | Subtotal: ${sub:,.2f})")
                            st.caption(f"Entregado: {ent} | Pagado: {pag}")
                        with col_d2:
                            chk_del_cli = st.checkbox("☑️ Confirmar borrado", key=f"chk_del_cli_norm_{prod_nombre}_{cli}")
                        with col_d3:
                            if st.button("🗑️ Borrar", key=f"del_cli_norm_{prod_nombre}_{cli}"):
                                if chk_del_cli:
                                    del prod_info["clientes"][cli]
                                    guardar_datos(datos)
                                    st.success(f"Pedido de {cli} eliminado y stock liberado.")
                                    st.rerun()
                                else:
                                    st.error("Marca la casilla para confirmar.")
                        st.markdown("---")
            else:
                if not clientes_data:
                    st.info("Aún no hay artículos anotados.")
                else:
                    for i, item in enumerate(list(clientes_data)):
                        cli = item.get("cliente", "")
                        det = item.get("detalle", "")
                        cant = item.get("cantidad", 1)
                        precio = item.get("precio", 0.0)
                        sub = cant * precio
                        ent = "✅ Sí" if item.get("entregado") else "❌ No"
                        pag = "✅ Sí" if item.get("pagado") else "❌ No"
                        
                        col_d1, col_d2, col_d3 = st.columns([2.5, 1.5, 1])
                        with col_d1:
                            st.markdown(f"**#{i+1} - 👤 {cli}** — {det} (Cant: {cant} | ${sub:,.2f})")
                            st.caption(f"Entregado: {ent} | Pagado: {pag}")
                        with col_d2:
                            chk_del_item = st.checkbox("☑️ Confirmar borrado", key=f"chk_del_item_cat_{prod_nombre}_{i}")
                        with col_d3:
                            if st.button("🗑️ Borrar", key=f"del_item_cat_{prod_nombre}_{i}"):
                                if chk_del_item:
                                    prod_info["clientes"].pop(i)
                                    guardar_datos(datos)
                                    st.success("Artículo eliminado y stock liberado.")
                                    st.rerun()
                                else:
                                    st.error("Marca la casilla para confirmar.")
                        st.markdown("---")

with tab_buscador:
    st.subheader("🔍 Cuentas y Cobros por Cliente (Estatus Entregado / Pagado)")
    
    if "cliente_activo_busqueda" not in st.session_state:
        st.session_state.cliente_activo_busqueda = ""
        
    with st.form("form_buscador_cliente_explicit"):
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            clientes_disponibles = datos.get("clientes", [])
            cliente_busq_sel = st.selectbox("Seleccionar Cliente", options=["-- Selecciona un cliente --"] + clientes_disponibles)
        with col_b2:
            cliente_texto_libre = st.text_input("O escribe nombre exacto:")
            
        submit_buscar_cli = st.form_submit_button("🔍 Buscar Cuenta del Cliente", type="primary")
        
        if submit_buscar_cli:
            cli_escogido = cliente_texto_libre.strip() if cliente_texto_libre.strip() else cliente_busq_sel
            if cli_escogido == "-- Selecciona un cliente --" or not cli_escogido:
                st.warning("Selecciona o escribe un cliente válido.")
                st.session_state.cliente_activo_busqueda = ""
            else:
                st.session_state.cliente_activo_busqueda = cli_escogido

    cliente_final_busq = st.session_state.cliente_activo_busqueda

    if cliente_final_busq:
        st.markdown(f"### 📋 Cuenta de: **{cliente_final_busq}**")
        
        prods_activos = datos.get("productos", {})
        items_cliente = []
        
        for p_nombre, p_info in prods_activos.items():
            tipo = p_info.get("tipo", "normal")
            clientes_data = p_info.get("clientes", {})
            fecha_lleg = p_info.get("fecha_llegada", "")
            disponible = producto_disponible(fecha_lleg)
            
            if tipo == "normal":
                if isinstance(clientes_data, dict) and cliente_final_busq in clientes_data:
                    info = clientes_data[cliente_final_busq]
                    cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                    ent = info.get("entregado", False) if isinstance(info, dict) else False
                    pag = info.get("pagado", False) if isinstance(info, dict) else False
                    precio = p_info.get("precio", 0.0)
                    subtotal = cant * precio
                    items_cliente.append({
                        "tipo": "normal",
                        "prod_nombre": p_nombre,
                        "detalle": "",
                        "cant": cant,
                        "precio": precio,
                        "subtotal": subtotal,
                        "entregado": ent,
                        "pagado": pag,
                        "disponible": disponible,
                        "fecha_llegada": fecha_lleg
                    })
            else:
                if isinstance(clientes_data, list):
                    for idx_item, item in enumerate(clientes_data):
                        if item.get("cliente") == cliente_final_busq:
                            det = item.get("detalle", "")
                            cant = item.get("cantidad", 1)
                            prec = item.get("precio", 0.0)
                            sub = cant * prec
                            ent = item.get("entregado", False)
                            pag = item.get("pagado", False)
                            items_cliente.append({
                                "tipo": "catalogo",
                                "prod_nombre": p_nombre,
                                "idx_item": idx_item,
                                "detalle": det,
                                "cant": cant,
                                "precio": prec,
                                "subtotal": sub,
                                "entregado": ent,
                                "pagado": pag,
                                "disponible": disponible,
                                "fecha_llegada": fecha_lleg
                            })

        if not items_cliente:
            st.info(f"El cliente '{cliente_final_busq}' no tiene pedidos activos registrados.")
        else:
            with st.form(f"form_unificado_cliente_{cliente_final_busq}"):
                st.markdown("#### 📝 Gestión Unificada de Estatus")
                
                nuevos_estados = []
                
                for idx, item in enumerate(items_cliente):
                    p_nom = item["prod_nombre"]
                    det_txt = f" - {item['detalle']}" if item['detalle'] else ""
                    disp = item["disponible"]
                    f_lleg = item["fecha_llegada"]
                    subt = item["subtotal"]
                    
                    st.markdown("---")
                    if disp:
                        tag_disp = "✅ **Disponible**"
                    else:
                        tag_disp = f"⏳ **En tránsito** (Llega el {f_lleg}) ⚠️ *No disponible aún*"
                        
                    st.markdown(f"**{idx+1}. {p_nom}{det_txt}** | {tag_disp}")
                    
                    key_ent = f"u_ent_{cliente_final_busq}_{p_nom}_{idx}"
                    key_pag = f"u_pag_{cliente_final_busq}_{p_nom}_{idx}"
                    key_del = f"u_del_{cliente_final_busq}_{p_nom}_{idx}"
                    
                    col1, col2, col3, col4 = st.columns([1, 1, 2, 1])
                    with col1:
                        ch_ent = st.checkbox("Entregado", value=item["entregado"], key=key_ent)
                    with col2:
                        ch_pag = st.checkbox("Pagado", value=item["pagado"], key=key_pag)
                    with col3:
                        st.write(f"**Cant:** {item['cant']} | **Precio U:** ${item['precio']:,.2f} | **Subtotal:** ${subt:,.2f}")
                    with col4:
                        ch_del = st.checkbox("🗑️ Cancelar", value=False, key=key_del)
                    
                    nuevos_estados.append({
                        "item_ref": item,
                        "entregado": ch_ent,
                        "pagado": ch_pag,
                        "cancelar": ch_del
                    })
                
                st.markdown("---")
                col_bt1, col_bt2, col_bt3 = st.columns(3)
                with col_bt1:
                    submit_guardar_cliente = st.form_submit_button("💾 Guardar Estatus", type="primary")
                with col_bt2:
                    submit_todo_pagado = st.form_submit_button("💵 Pagar lo disponible")
                with col_bt3:
                    submit_todo_pagado_entregado = st.form_submit_button("⚡ Pagado y Entregado (disponible)")
                
                if submit_guardar_cliente or submit_todo_pagado or submit_todo_pagado_entregado:
                    for estado in nuevos_estados:
                        it = estado["item_ref"]
                        p_nom = it["prod_nombre"]
                        p_info = datos["productos"][p_nom]
                        
                        if (submit_todo_pagado_entregado or submit_todo_pagado) and not it["disponible"]:
                            # Los botones masivos NO tocan lo que aún no ha llegado (en tránsito)
                            entregado_val = it["entregado"]
                            pagado_val = it["pagado"]
                            cancelar_val = False
                        elif submit_todo_pagado_entregado:
                            entregado_val = True
                            pagado_val = True
                            cancelar_val = False
                        elif submit_todo_pagado:
                            entregado_val = it["entregado"]
                            pagado_val = True
                            cancelar_val = False
                        else:
                            entregado_val = estado["entregado"]
                            pagado_val = estado["pagado"]
                            cancelar_val = estado["cancelar"]

                        # --- Registro de cobros con fecha (para el balance semanal) ---
                        if it["pagado"] and (cancelar_val or not pagado_val):
                            finanzas.quitar_cobro(datos, cliente_final_busq, p_nom, it["detalle"], it["cant"], it["subtotal"])
                        elif (not it["pagado"]) and pagado_val and not cancelar_val:
                            finanzas.registrar_cobro(datos, cliente_final_busq, p_nom, it["detalle"], it["cant"], it["subtotal"])

                        if cancelar_val:
                            if it["tipo"] == "normal":
                                if cliente_final_busq in p_info.get("clientes", {}):
                                    del p_info["clientes"][cliente_final_busq]
                            else:
                                if "idx_item" in it and isinstance(p_info.get("clientes"), list):
                                    idx_remove = it["idx_item"]
                                    if idx_remove < len(p_info["clientes"]):
                                        p_info["clientes"].pop(idx_remove)
                        else:
                            if it["tipo"] == "normal":
                                p_info["clientes"][cliente_final_busq] = {
                                    "cantidad": it["cant"],
                                    "entregado": entregado_val,
                                    "pagado": pagado_val
                                }
                            else:
                                if "idx_item" in it and isinstance(p_info.get("clientes"), list):
                                    p_info["clientes"][it["idx_item"]]["entregado"] = entregado_val
                                    p_info["clientes"][it["idx_item"]]["pagado"] = pagado_val
                    
                    guardar_datos(datos)
                    verificar_y_archivar_productos(datos)
                    if submit_todo_pagado_entregado:
                        st.success(f"¡Lo disponible de '{cliente_final_busq}' ha sido marcado como Pagado y Entregado! (Lo que aún está en tránsito no se modificó)")
                    elif submit_todo_pagado:
                        st.success(f"¡Lo disponible de '{cliente_final_busq}' ha sido marcado como Pagado! (Lo que aún está en tránsito no se modificó)")
                    else:
                        st.success("¡Todos los estatus de la cuenta han sido actualizados exitosamente!")
                    st.rerun()

            st.markdown("---")
            st.markdown(f"### 📑 Resumen de la Cuenta de **{cliente_final_busq}**")
            
            lineas_resumen = []
            total_pendiente = 0.0
            
            for item in items_cliente:
                p_nom = item["prod_nombre"]
                det = f" ({item['detalle']})" if item["detalle"] else ""
                cant = item["cant"]
                subt = item["subtotal"]
                pag = item["pagado"]
                ent = item["entregado"]
                disp = item["disponible"]
                
                if ent and pag:
                    continue  # entregado y pagado: cuenta cerrada
                
                if ent:
                    lineas_resumen.append(f"• **{p_nom}{det}** x{cant} — 📦 *Entregado, falta pago* — **${subt:,.2f}**")
                    total_pendiente += subt
                elif not disp:
                    lineas_resumen.append(f"• **{p_nom}{det}** x{cant} — ⏳ *En tránsito (Llega {item['fecha_llegada']}) - No cobrable aún*")
                elif pag:
                    lineas_resumen.append(f"• **{p_nom}{det}** x{cant} — *(pagado)*")
                else:
                    lineas_resumen.append(f"• **{p_nom}{det}** x{cant} — **${subt:,.2f}**")
                    total_pendiente += subt

            if not lineas_resumen:
                st.success("✨ ¡Todo lo que tenía este cliente ya fue entregado y pagado!")
            else:
                for linea in lineas_resumen:
                    st.markdown(linea)
                
                st.markdown(f"#### 💵 **Total a Cobrar: ${total_pendiente:,.2f}**")
                st.caption("Los productos entregados y pagados ya no aparecen. Los entregados sin pago sí aparecen y suman al total a cobrar. Los marcados como (pagado) o en tránsito no suman.")

with tab_proximas:
    st.subheader("🚚 Próximas Entregas y Logística Programada")
    st.write("Consulta exclusiva de los productos y lotes que tienen una **fecha de llegada programada**, ordenados por proximidad.")
    
    prods_all = datos.get("productos", {})
    entregas_programadas = []
    
    hoy = date.today()
    
    for p_nom, p_inf in prods_all.items():
        fecha_lleg_str = p_inf.get("fecha_llegada", "")
        if not fecha_lleg_str or fecha_lleg_str == "Por definir" or fecha_lleg_str.strip() == "":
            continue
            
        try:
            f_lleg = datetime.strptime(fecha_lleg_str, "%Y-%m-%d").date()
        except:
            continue
            
        dias_restantes = (f_lleg - hoy).days
        
        tipo = p_inf.get("tipo", "normal")
        comp = p_inf.get("unidades_compradas", 0)
        clientes_d = p_inf.get("clientes", {})
        
        pedidas = 0
        if tipo == "normal":
            for cli, info in clientes_d.items():
                cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                pedidas += cant
        else:
            if isinstance(clientes_d, list):
                for item in clientes_d:
                    pedidas += item.get("cantidad", 1)
            elif isinstance(clientes_d, dict):
                for cli, info in clientes_d.items():
                    cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                    pedidas += cant

        entregas_programadas.append({
            "producto": p_nom,
            "tipo": "Estándar" if tipo == "normal" else "Catálogo",
            "fecha_str": fecha_lleg_str,
            "f_lleg": f_lleg,
            "dias_restantes": dias_restantes,
            "compradas": comp,
            "pedidas": pedidas
        })

    # Ordenar por fecha de llegada más cercana
    entregas_programadas.sort(key=lambda x: x["f_lleg"])

    if not entregas_programadas:
        st.info("✨ No hay productos con fecha de llegada programada en este momento. Todos los productos activos son de entrega inmediata / sin fecha.")
    else:
        tabla_prog = []
        for entrega in entregas_programadas:
            dias = entrega["dias_restantes"]
            if dias < 0:
                estatus_tiempo = f"⚠️ Fecha pasada ({entrega['fecha_str']})"
            elif dias == 0:
                estatus_tiempo = f"🔥 ¡Llega HOY! ({entrega['fecha_str']})"
            elif dias == 1:
                estatus_tiempo = f"⏰ Llega mañana ({entrega['fecha_str']})"
            else:
                estatus_tiempo = f"📅 En {dias} días ({entrega['fecha_str']})"
                
            tabla_prog.append({
                "Estatus Tiempo": estatus_tiempo,
                "Producto": entrega["producto"],
                "Tipo": entrega["tipo"],
                "Fecha Llegada": entrega["fecha_str"],
                "U. Compradas": entrega["compradas"],
                "U. Pedidas": entrega["pedidas"]
            })
            
        st.dataframe(tabla_prog, use_container_width=True)

with tab_disponibles:
    st.subheader("📦 Productos Disponibles y Stock Libre para Venta")
    st.write("Consulta rápida de todos los productos disponibles con **unidades libres** sin asignar a clientes.")
    
    st.info("💡 **Regla Automática de los Miércoles 9:00 AM:** Todos los productos que llegaron de Miércoles a Sábado que no hayan sido pagados se liberan automáticamente y vuelven a sumarse a este stock disponible. Los productos llegados en Domingo, Lunes o Martes pasan de gracia a la siguiente semana. Los productos sin fecha registrada se toman en cuenta esa misma semana.")
    
    prods_all = datos.get("productos", {})
    tabla_disponibles = []
    
    for p_nom, p_inf in prods_all.items():
        fecha_lleg = p_inf.get("fecha_llegada", "")
        disp_fecha = producto_disponible(fecha_lleg)
        
        tipo = p_inf.get("tipo", "normal")
        comp = p_inf.get("unidades_compradas", 0)
        clientes_d = p_inf.get("clientes", {})
        
        pedidas = 0
        if tipo == "normal":
            precio_u = p_inf.get("precio", 0.0)
            for cli, info in clientes_d.items():
                cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                pedidas += cant
        else:
            precio_u = "Catálogo"
            if isinstance(clientes_d, list):
                for item in clientes_d:
                    pedidas += item.get("cantidad", 1)
            elif isinstance(clientes_d, dict):
                for cli, info in clientes_d.items():
                    cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                    pedidas += cant
                    
        disponibles_qty = comp - pedidas
        estatus_llegada = "✅ Disponible" if disp_fecha else f"⏳ En tránsito (Llega {fecha_lleg})"
        
        if disponibles_qty > 0:
            precio_str = f"${precio_u:,.2f}" if isinstance(precio_u, (int, float)) else str(precio_u)
            tabla_disponibles.append({
                "Producto": p_nom,
                "Tipo": "Estándar" if tipo == "normal" else "Catálogo",
                "Compradas": comp,
                "Anotadas / Pedidas": pedidas,
                "✨ Stock Libre / Disponible": disponibles_qty,
                "Precio Venta": precio_str,
                "Estatus": estatus_llegada
            })

    if not tabla_disponibles:
        st.info("No hay productos con stock libre en este momento.")
    else:
        st.markdown(f"#### 🛍️ Hay **{len(tabla_disponibles)}** producto(s) con unidades disponibles:")
        st.dataframe(tabla_disponibles, use_container_width=True)
        
        total_unidades_libres = sum(item["✨ Stock Libre / Disponible"] for item in tabla_disponibles)
        st.metric("✨ Total de Unidades Libres para Vender", total_unidades_libres)

    st.markdown("---")
    st.markdown("### ⚠️ Resumen de Clientes que No Recogieron / Productos Liberados")
    st.write("Registro histórico de los productos que fueron liberados por falta de pago y devueltos al inventario disponible:")
    
    liberados_list = datos.get("liberados_no_recogidos", [])
    
    if not liberados_list:
        st.success("✨ ¡Aún no hay registro de productos liberados por no recoger o falta de pago!")
    else:
        tabla_liberados = []
        for idx, lib in enumerate(reversed(liberados_list)):
            p_completo = f"{lib['producto']} ({lib['detalle']})" if lib.get('detalle') else lib['producto']
            tabla_liberados.append({
                "#": len(liberados_list) - idx,
                "Cliente": lib.get("cliente"),
                "Producto / Detalle": p_completo,
                "Cant. Liberada": lib.get("cantidad"),
                "Precio U.": f"${lib.get('precio_u', 0.0):,.2f}",
                "Fecha Liberación": lib.get("fecha_liberacion"),
                "Motivo / Estatus": lib.get("motivo")
            })
        st.dataframe(tabla_liberados, use_container_width=True)
        
        if st.button("🗑️ Limpiar Registro de Liberados"):
            datos["liberados_no_recogidos"] = []
            guardar_datos(datos)
            st.success("Registro limpiado correctamente.")
            st.rerun()

with tab_historial:
    st.subheader("📊 Historial General y Ganancias Mensuales")
    st.write("Aquí se archivan automáticamente los productos y lotes cuyos pedidos ya fueron **100% entregados y pagados** por todos los clientes.")

    historial = datos.get("historial_mensual", [])

    # ---------- Balance general (no depende del mes) ----------
    inv_total = finanzas.inversion_total(datos)
    cobrado_tot = finanzas.cobrado_total(datos)
    balance_tot = cobrado_tot - inv_total

    st.markdown("### 🧮 Balance General: ¿ya recuperé mi inversión?")
    bg1, bg2, bg3 = st.columns(3)
    bg1.metric("💸 Inversión total", f"${inv_total:,.2f}")
    bg2.metric("💵 Cobrado total", f"${cobrado_tot:,.2f}")
    if balance_tot < 0:
        bg3.metric("⏳ Falta por recuperar", f"${-balance_tot:,.2f}")
    else:
        bg3.metric("✅ Ganancia (inversión recuperada)", f"${balance_tot:,.2f}")
    st.caption("La inversión suma todos los productos, incluso los que aún no llegan. Lo cobrado cuenta solo lo marcado como Pagado.")

    base_info = datos.get("cobro_base", {})
    if base_info.get("monto", 0) > 0:
        with st.expander(f"Cobros anteriores a la actualización (sin fecha): ${base_info['monto']:,.2f}"):
            st.caption("Lo que ya estaba pagado antes de activar el balance semanal no tiene fecha, por eso va junto aquí.")
            for nombre_b, cant_b in sorted(base_info.get("detalle", {}).items()):
                st.write(f"• {cant_b} {nombre_b}")

    st.markdown("---")

    # ---------- Selector de mes (meses con productos archivados o con cobros) ----------
    meses_disponibles = finanzas.meses_disponibles(datos)

    if not meses_disponibles:
        st.info("Aún no hay productos archivados ni cobros registrados. Cuando marques pagos, aquí aparecerá el balance por semana.")
    else:
        mes_sel = st.selectbox("Seleccionar Mes a Consultar", options=meses_disponibles)

        items_mes = [h for h in historial if h.get("mes") == mes_sel]

        if items_mes:
            t_hist = []
            tot_costo = 0.0
            tot_venta = 0.0
            tot_ganancia = 0.0
            tot_cant = 0

            for h in items_mes:
                tot_cant += h.get("cantidad", 0)
                tot_costo += h.get("costo", 0.0)
                tot_venta += h.get("venta", 0.0)
                tot_ganancia += h.get("ganancia", 0.0)

                t_hist.append({
                    "Producto": h.get("producto"),
                    "Cant. Total": h.get("cantidad"),
                    "Costo Inversión": f"${h.get('costo', 0.0):,.2f}",
                    "Venta Total": f"${h.get('venta', 0.0):,.2f}",
                    "Ganancia Neta": f"${h.get('ganancia', 0.0):,.2f}",
                    "Fecha Archivo": h.get("fecha_archivo")
                })

            st.dataframe(t_hist, use_container_width=True)

            st.markdown("### 💰 Resumen Financiero del Mes: " + mes_sel)
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("📦 Unidades Vendidas", tot_cant)
            mc2.metric("💸 Inversión Total", f"${tot_costo:,.2f}")
            mc3.metric("💵 Venta Total", f"${tot_venta:,.2f}")
            mc4.metric("📈 Ganancia Neta Limpia", f"${tot_ganancia:,.2f}")
        else:
            st.caption("En este mes no se archivó ningún producto completo; abajo ves lo que se cobró.")

        # ---------- Balance semanal del mes ----------
        parsed = finanzas.parsear_mes(mes_sel)
        if parsed:
            semanas = finanzas.resumen_semanal(datos, parsed[0], parsed[1])
            st.markdown(f"### 📅 Cobros por semana: {mes_sel}")
            if not semanas:
                st.info("Este mes aún no empieza.")
            else:
                etiquetas = [f"S{w['numero']} · {w['rotulo']}" for w in semanas]
                st.markdown("**💵 Cobrado cada semana**")
                st.bar_chart(pd.DataFrame({"Cobrado": [w["cobrado"] for w in semanas]}, index=etiquetas))
                st.markdown("**📉 Balance acumulado** (0 = inversión recuperada)")
                st.line_chart(pd.DataFrame({"Balance": [w["balance"] for w in semanas]}, index=etiquetas))

                st.markdown("**🛒 Lo que se cobró cada semana**")
                for w in semanas:
                    if w["vendido"]:
                        st.markdown(f"**Semana {w['rotulo']} · cobrado ${w['cobrado']:,.2f}**")
                        for nombre_v, cant_v in w["vendido"]:
                            st.write(f"• {cant_v} {nombre_v}")
                    else:
                        st.markdown(f"**Semana {w['rotulo']}** · sin cobros")
with tab_clientes:
    st.subheader("👥 Administración General de Clientes")
    st.write("Registra nuevos clientes aquí o consulta la base de datos completa de clientes cargada.")
    
    col_c1, col_c2 = st.columns([1, 1])
    
    with col_c1:
        with st.form("form_alta_cliente_global", clear_on_submit=True):
            st.markdown("#### ➕ Registrar Nuevo Cliente")
            nuevo_cli_nombre = st.text_input("Nombre completo del cliente:")
            submit_alta_cli = st.form_submit_button("💾 Guardar Cliente")
            
            if submit_alta_cli:
                nombre_limpio = nuevo_cli_nombre.strip()
                if nombre_limpio:
                    if nombre_limpio not in datos["clientes"]:
                        datos["clientes"].append(nombre_limpio)
                        datos["clientes"].sort()
                        guardar_datos(datos)
                        st.success(f"¡Cliente '{nombre_limpio}' registrado!")
                        st.rerun()
                    else:
                        st.warning("Este cliente ya existe.")
                else:
                    st.error("Escribe un nombre válido.")

    with col_c2:
        st.markdown(f"#### 📜 Lista de Clientes Registrados ({len(datos.get('clientes', []))})")
        lista_cli_actual = datos.get("clientes", [])
        if not lista_cli_actual:
            st.info("No hay clientes registrados.")
        else:
            tabla_clientes = [{"#": i+1, "Nombre del Cliente": c} for i, c in enumerate(sorted(lista_cli_actual))]
            st.dataframe(tabla_clientes, use_container_width=True, height=350)
            
            with st.form("form_baja_cliente_global"):
                cli_borrar_global = st.selectbox("Eliminar cliente", options=["-- Seleccionar --"] + sorted(lista_cli_actual))
                submit_baja_cli = st.form_submit_button("🗑️ Eliminar de la Lista")
                if submit_baja_cli:
                    if cli_borrar_global != "-- Seleccionar --":
                        datos["clientes"].remove(cli_borrar_global)
                        guardar_datos(datos)
                        st.success(f"Se eliminó a '{cli_borrar_global}'.")
                        st.rerun()
                    else:
                        st.warning("Selecciona un cliente.")

