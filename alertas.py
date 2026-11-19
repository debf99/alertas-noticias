"""
Alertas de noticias — prototipo (etapa 1, 100 % gratis).

Cada vez que se ejecuta:
  1. Lee los titulares recientes de Google Noticias para cada canal
     (región + categoría) definido en config.json.
  2. Agrupa los titulares que hablan del mismo hecho ("evento").
  3. Cuando un evento nuevo aparece en suficientes medios distintos,
     envía UN aviso al celular por ntfy, al canal correspondiente.
  4. Guarda lo que ya vio en estado.json para no repetir avisos.

Variables de entorno:
  NTFY_PREFIJO  (obligatoria para enviar) prefijo secreto de los canales,
                p. ej. "nt-k3x9q" -> canal "nt-k3x9q-peru-politica".
  NTFY_SERVIDOR (opcional) por defecto https://ntfy.sh
  MODO_PRUEBA=1 (opcional) no envía nada, solo muestra en pantalla.
"""

import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests

CARPETA = os.path.dirname(os.path.abspath(__file__))
RUTA_CONFIG = os.path.join(CARPETA, "config.json")
RUTA_ESTADO = os.path.join(CARPETA, "estado.json")

HORAS_GUARDAR_EVENTO = 48          # se olvidan los eventos más viejos
MAX_AVISOS_POR_EJECUCION = 15      # freno de seguridad contra avisos en masa

PALABRAS_VACIAS = set("""
a al algo ante antes asi aun bajo cada como con contra cual cuando de del desde donde
dos e el ella ellos en entre era es esa ese eso esta este esto fue han hasta hay la las
le les lo los mas me mi muy ni no nos o otra otro para pero por porque que quien se sea
segun ser si sin sobre son su sus tambien tras tu un una uno unos unas y ya hoy ayer
tras dice dijo sera seria ha he tiene tienen hace video fotos vivo minuto ultimo ultima
""".split())


# ----------------------------------------------------------------- utilidades

def ahora():
    return datetime.now(timezone.utc)


def normalizar(texto):
    """minúsculas, sin tildes, sin signos."""
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9ñ ]+", " ", texto)


def palabras_clave(titulo):
    return {p for p in normalizar(titulo).split()
            if len(p) > 2 and p not in PALABRAS_VACIAS}


def similitud(a, b):
    """Qué tanto se parecen dos titulares (0 a 1)."""
    if not a or not b:
        return 0.0
    comunes = len(a & b)
    # mezcla de Jaccard y de "contención" para titulares de distinto largo
    jaccard = comunes / len(a | b)
    contencion = comunes / min(len(a), len(b))
    return max(jaccard, contencion * 0.75)


def separar_fuente(entrada):
    """Google Noticias pone 'Titular - Medio'. Devuelve (titular, medio)."""
    titulo = (entrada.get("title") or "").strip()
    fuente = ""
    src = entrada.get("source")
    if isinstance(src, dict):
        fuente = (src.get("title") or "").strip()
    if " - " in titulo:
        base, posible = titulo.rsplit(" - ", 1)
        if not fuente or posible.strip().lower() == fuente.lower():
            fuente = fuente or posible.strip()
            titulo = base.strip()
    return titulo, fuente or "desconocido"


def fecha_entrada(entrada):
    for campo in ("published", "updated"):
        valor = entrada.get(campo)
        if valor:
            try:
                f = parsedate_to_datetime(valor)
                return f if f.tzinfo else f.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return None


def url_google_noticias(busqueda, horas):
    q = f"{busqueda} when:{max(1, round(horas))}h"
    return ("https://news.google.com/rss/search?"
            + urllib.parse.urlencode({"q": q, "hl": "es-419",
                                      "gl": "PE", "ceid": "PE:es-419"}))


# ----------------------------------------------------------------- estado

def cargar_estado():
    if os.path.exists(RUTA_ESTADO):
        with open(RUTA_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    return {"eventos": [], "creado": ahora().isoformat()}


def guardar_estado(estado):
    limite = ahora().timestamp() - HORAS_GUARDAR_EVENTO * 3600
    estado["eventos"] = [e for e in estado["eventos"] if e["ultimo_visto"] >= limite]
    estado["actualizado"] = ahora().isoformat()
    with open(RUTA_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)


# ----------------------------------------------------------------- lógica

def leer_canal(url):
    """Descarga un feed. Devuelve lista de entradas (vacía si falla)."""
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (alertas-noticias)"})
        r.raise_for_status()
        return feedparser.parse(r.content).entries
    except requests.RequestException as e:
        print(f"  ! no se pudo leer el canal: {e}")
        return []


def agrupar(estado, canal, entradas, horas_ventana, similitud_minima):
    """Mete cada titular en su evento (o crea uno nuevo)."""
    limite = ahora().timestamp() - horas_ventana * 3600
    eventos = [e for e in estado["eventos"] if e["canal"] == canal]
    for entrada in entradas:
        fecha = fecha_entrada(entrada)
        if fecha and fecha.timestamp() < limite:
            continue
        titulo, fuente = separar_fuente(entrada)
        claves = palabras_clave(titulo)
        if len(claves) < 3:
            continue
        mejor, mejor_sim = None, 0.0
        for ev in eventos:
            s = similitud(claves, set(ev["claves"]))
            if s > mejor_sim:
                mejor, mejor_sim = ev, s
        marca = ahora().timestamp()
        if mejor and mejor_sim >= similitud_minima:
            if fuente not in mejor["fuentes"]:
                mejor["fuentes"].append(fuente)
                mejor["enlaces"].append(entrada.get("link", ""))
            mejor["ultimo_visto"] = marca
        else:
            nuevo = {
                "canal": canal,
                "titulo": titulo,
                "claves": sorted(claves),
                "fuentes": [fuente],
                "enlaces": [entrada.get("link", "")],
                "primer_visto": marca,
                "ultimo_visto": marca,
                "avisado": False,
            }
            estado["eventos"].append(nuevo)
            eventos.append(nuevo)


def enviar_aviso(evento, nombre_region, nombre_cat, prefijo):
    topico = f"{prefijo}-{evento['canal']}"
    medios = ", ".join(evento["fuentes"][:4])
    if len(evento["fuentes"]) > 4:
        medios += f" y {len(evento['fuentes']) - 4} más"
    cuerpo = {
        "topic": topico,
        "title": f"{nombre_region} · {nombre_cat}",
        "message": f"{evento['titulo']}\n\nReportado por: {medios}",
        "click": evento["enlaces"][0],
        "tags": ["newspaper"],
    }
    if os.environ.get("MODO_PRUEBA") == "1" or not prefijo:
        print(f"  [PRUEBA] -> {topico}: {evento['titulo']} ({medios})")
        return True
    servidor = os.environ.get("NTFY_SERVIDOR", "https://ntfy.sh").rstrip("/")
    try:
        r = requests.post(servidor, json=cuerpo, timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  ! no se pudo enviar el aviso: {e}")
        return False


def ejecutar(lector=leer_canal):
    with open(RUTA_CONFIG, encoding="utf-8") as f:
        config = json.load(f)
    estado = cargar_estado()
    primera_vez = not estado["eventos"]
    prefijo = os.environ.get("NTFY_PREFIJO", "").strip()
    horas = config.get("horas_ventana", 12)
    sim_min = config.get("similitud_minima", 0.34)
    nombres_cat = config["nombres_categorias"]

    avisos = 0
    for clave_region, region in config["regiones"].items():
        for clave_cat, busqueda in region["categorias"].items():
            canal = f"{clave_region}-{clave_cat}"
            print(f"- {canal}")
            entradas = lector(url_google_noticias(busqueda, horas))
            print(f"  {len(entradas)} titulares")
            agrupar(estado, canal, entradas, horas, sim_min)
            time.sleep(0.5)

            for ev in estado["eventos"]:
                if ev["canal"] != canal or ev["avisado"]:
                    continue
                if len(ev["fuentes"]) < region["umbral"]:
                    continue
                if primera_vez:
                    # en la primera ejecución no se avisa lo que ya existía
                    ev["avisado"] = True
                    continue
                if avisos >= MAX_AVISOS_POR_EJECUCION:
                    continue
                if enviar_aviso(ev, region["nombre"], nombres_cat[clave_cat], prefijo):
                    ev["avisado"] = True
                    avisos += 1

    guardar_estado(estado)
    if primera_vez:
        print("Primera ejecución: se registraron las noticias actuales sin avisar. "
              "Desde la próxima, solo llegarán eventos nuevos.")
    print(f"Listo. Avisos enviados: {avisos}")
    return avisos


if __name__ == "__main__":
    sys.exit(0 if ejecutar() is not None else 1)
