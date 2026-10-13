"""
Almacenamiento seguro para Sistema de Ventas Cinepeachy.

- En la NUBE (Streamlit Community Cloud): guarda data.json en un repo PRIVADO
  de GitHub (cada guardado = un commit = historial de versiones gratis).
  Se activa solo si existe la sección [github] en los Secrets.
- En tu PC: guarda en data.json con escritura atómica y respaldos cada hora
  en la carpeta backups/.

Regla de oro: si algo falla al LEER, la app se detiene con un error en lugar
de arrancar vacía (así nunca se sobreescriben tus datos reales con datos vacíos).
"""
import base64
import glob
import json
import os
import shutil
from datetime import datetime

import requests
import streamlit as st

DATA_FILE = "data.json"
BACKUP_DIR = "backups"
MAX_BACKUPS = 200  # respaldos por hora que se conservan (~8 días)


def _datos_iniciales():
    return {
        "clientes": ["Chusita", "Esme", "Juanita", "Ana"],
        "historial_mensual": [],
        "liberados_no_recogidos": [],
        "productos": {},
    }


def _completar(datos):
    datos.setdefault("historial_mensual", [])
    datos.setdefault("liberados_no_recogidos", [])
    datos.setdefault("productos", {})
    datos.setdefault("clientes", ["Chusita", "Esme", "Juanita", "Ana"])
    return datos


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------
def _config_github():
    """Devuelve (token, repo, rama, ruta) o None si no está configurado."""
    try:
        cfg = st.secrets["github"]
        return (
            cfg["token"],
            cfg["repo"],  # "usuario/nombre-repo-privado"
            cfg.get("branch", "main"),
            cfg.get("path", "data.json"),
        )
    except Exception:
        return None


def usando_github():
    return _config_github() is not None


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _url(repo, path):
    return f"https://api.github.com/repos/{repo}/contents/{path}"


def _gh_leer():
    token, repo, branch, path = _config_github()
    r = requests.get(
        _url(repo, path), headers=_headers(token), params={"ref": branch}, timeout=20
    )
    if r.status_code == 404:
        return None, None  # primera vez: el archivo aún no existe
    r.raise_for_status()
    info = r.json()
    contenido = base64.b64decode(info["content"]).decode("utf-8")
    return json.loads(contenido), info["sha"]


def _gh_guardar(datos):
    token, repo, branch, path = _config_github()
    cuerpo_json = json.dumps(datos, ensure_ascii=False, indent=4)
    contenido_b64 = base64.b64encode(cuerpo_json.encode("utf-8")).decode("ascii")
    mensaje = f"Guardado {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    for intento in range(2):
        sha = st.session_state.get("_gh_sha")
        payload = {"message": mensaje, "content": contenido_b64, "branch": branch}
        if sha:
            payload["sha"] = sha
        r = requests.put(
            _url(repo, path), headers=_headers(token), json=payload, timeout=20
        )
        if r.status_code in (200, 201):
            st.session_state["_gh_sha"] = r.json()["content"]["sha"]
            return
        if r.status_code in (409, 422) and intento == 0:
            # sha desactualizado: lo volvemos a pedir y reintentamos una vez
            try:
                _, st.session_state["_gh_sha"] = _gh_leer()
            except Exception:
                pass
            continue
        r.raise_for_status()


# --------------------------------------------------------------------------
# Local (tu PC)
# --------------------------------------------------------------------------
def _respaldo_diario():
    """Copia data.json a backups/ una vez por hora (estado ANTES del primer cambio de esa hora)."""
    if not os.path.exists(DATA_FILE):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    destino = os.path.join(BACKUP_DIR, f"data_{datetime.now():%Y%m%d_%H}h.json")
    if not os.path.exists(destino):
        shutil.copy2(DATA_FILE, destino)
    viejos = sorted(glob.glob(os.path.join(BACKUP_DIR, "data_*.json")))[:-MAX_BACKUPS]
    for v in viejos:
        try:
            os.remove(v)
        except OSError:
            pass


def _local_guardar(datos):
    _respaldo_diario()
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)  # reemplazo atómico: nunca queda a medias


def _local_leer():
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Archivo dañado: lo apartamos (no lo borramos) e intentamos el último respaldo
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(DATA_FILE, f"data.dañado_{marca}.json")
        respaldos = sorted(glob.glob(os.path.join(BACKUP_DIR, "data_*.json")))
        for ruta in reversed(respaldos):
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    datos = json.load(f)
                st.warning(
                    f"⚠️ data.json estaba dañado; se recuperó el respaldo {os.path.basename(ruta)}."
                )
                return datos
            except Exception:
                continue
        raise


# --------------------------------------------------------------------------
# API pública (misma firma que antes)
# --------------------------------------------------------------------------
def cargar_datos():
    try:
        if usando_github():
            datos, sha = _gh_leer()
            st.session_state["_gh_sha"] = sha
            if datos is None:
                # Primera vez en la nube: si hay un data.json local, úsalo como semilla
                datos = _local_leer() or _datos_iniciales()
                _gh_guardar(_completar(datos))
        else:
            datos = _local_leer() or _datos_iniciales()
        return _completar(datos)
    except Exception as e:
        st.error(
            "🛑 No pude leer tus datos, así que detuve la app para NO sobrescribirlos "
            f"con datos vacíos.\n\nDetalle: {e}"
        )
        st.stop()


def guardar_datos(datos):
    if not isinstance(datos, dict):
        raise ValueError("Los datos a guardar no son válidos")
    # Siempre guardamos copia local (si el disco es temporal en la nube, no pasa nada)
    try:
        _local_guardar(datos)
    except Exception:
        pass
    if usando_github():
        try:
            _gh_guardar(datos)
        except Exception as e:
            st.error(
                f"⚠️ No se pudo guardar en GitHub: {e}\n\n"
                "Tus cambios siguen en pantalla; descarga el .json desde el panel lateral como respaldo."
            )


def exportar_json(datos):
    return json.dumps(datos, ensure_ascii=False, indent=4)
