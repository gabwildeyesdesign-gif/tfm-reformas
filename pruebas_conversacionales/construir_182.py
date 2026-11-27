# construir_182.py — Crea casos_182_bateria_v5_3.json a partir de casos_176.json (59 casos).
# Solo cambia lo que D56 (categoría pintura) vuelve falso; el resto queda igual.
# Uso (Git Bash, raíz del proyecto): ./venv/Scripts/python.exe pruebas_conversacionales/construir_182.py
import json                                   # leer y escribir los casos
from pathlib import Path                      # rutas

ORIGEN = Path("pruebas_conversacionales/casos_176.json")            # batería de la v5.2
DESTINO = Path("pruebas_conversacionales/casos_182_bateria_v5_3.json")  # batería de la v5.3

casos = json.loads(ORIGEN.read_text(encoding="utf-8"))              # los 59 casos
cambios = []                                                        # para enseñar qué se ha tocado
for c in casos:                                                     # recorre cada caso
    if c["id"] in ("A1-10a", "REG-C3"):                             # "pintar toda la casa": antes parcial_acabados
        for e in c["esperado"]:                                     # sus comprobaciones
            e["contiene"] = [("Reforma: Pintura" if "parcial de acabados" in p else p)
                             for p in e.get("contiene", [])]        # ahora tiene que salir Pintura
        cambios.append(c["id"])
    if c["id"] == "E-A1-38":                                        # pintar solo el salón (exploratorio)
        c["bloque"] = "funcional (D56)"                             # deja de ser exploratorio
        c["esperado"] = [{"turno": 3, "contiene": ["Antes de guardar nada", "Reforma: Pintura"]}]
        cambios.append(c["id"])
assert len(cambios) == 3, cambios                                   # si no encuentra los 3, para
DESTINO.write_text(json.dumps(casos, ensure_ascii=False, indent=1), encoding="utf-8")  # mismo formato que el Proyecto
print(f"{len(casos)} casos -> {DESTINO} | cambiados: {cambios}")    # resumen
