# Alertas de noticias (prototipo)

Revisa Google Noticias cada 15 minutos y avisa al celular (app **ntfy**) cuando un
hecho nuevo aparece en varios medios distintos.

**Canales** (región + categoría): `arequipa`, `peru`, `internacional` ×
`politica`, `economia`, `deporte`, `policial`.
En ntfy te suscribes a `TU_PREFIJO-region-categoria`, por ejemplo `nt-k3x9q-peru-economia`.

**Ajustes** en `config.json`: búsquedas, y `umbral` = cuántos medios distintos deben
publicar el mismo hecho para avisar (Arequipa 2, Perú 3, Internacional 3).

**Prueba sin internet:** `python pruebas/prueba_local.py`
