import re
from pathlib import Path
from docx import Document
from pypdf import PdfReader

EXTENSIONES_PERMITIDAS = {".txt", ".docx", ".pdf"}
MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def normalizar_espacios(texto: str) -> str:
    """Colapsa saltos de línea y espacios múltiples sin alterar palabras ni puntuación."""
    return re.sub(r"\s+", " ", texto).strip()


def verificar_cita_en_texto(cita_textual: str, texto_documento: str) -> bool:
    """
    Comprueba determinísticamente que la cita textual exista literalmente
    dentro del documento fuente (tolerando únicamente saltos de línea de PDF/DOCX).
    """
    cita_norm = normalizar_espacios(cita_textual)
    doc_norm = normalizar_espacios(texto_documento)
    if not cita_norm or not doc_norm:
        return False
    return cita_norm in doc_norm


def extraer_documento(ruta_archivo: str) -> dict:
    """
    Extrae texto estructurado por bloques/párrafos desde un archivo .txt, .docx o .pdf.
    Opera en modo Fail-Closed ante PDFs escaneados sin texto o rutas inválidas.
    """
    ruta = Path(ruta_archivo).expanduser()

    if not ruta.exists() or not ruta.is_file():
        return {
            "estado": "ERROR_ARCHIVO",
            "mensaje": f"No se encontró el archivo especificado: {ruta}",
        }

    extension = ruta.suffix.lower()
    if extension not in EXTENSIONES_PERMITIDAS:
        return {
            "estado": "FUERA_DE_ALCANCE",
            "mensaje": f"Extensión '{extension}' no soportada. Formatos permitidos: .txt, .docx, .pdf.",
        }

    tamano = ruta.stat().st_size
    if tamano == 0:
        return {
            "estado": "ERROR_ARCHIVO",
            "mensaje": "El archivo está vacío (0 bytes).",
        }
    if tamano > MAX_BYTES:
        return {
            "estado": "FUERA_DE_ALCANCE",
            "mensaje": f"El archivo supera el límite operativo de 10 MB ({tamano} bytes).",
        }

    bloques = []
    try:
        if extension == ".txt":
            contenido = ruta.read_text(encoding="utf-8")
            bloques = [p.strip() for p in contenido.splitlines() if p.strip()]
        elif extension == ".docx":
            doc = Document(ruta)
            bloques = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        elif extension == ".pdf":
            lector = PdfReader(ruta)
            for idx, pagina in enumerate(lector.pages, start=1):
                texto_pag = (pagina.extract_text() or "").strip()
                if texto_pag:
                    bloques.append(f"[Página {idx}] {texto_pag}")
    except Exception as exc:
        return {
            "estado": "ERROR_LECTURA",
            "mensaje": f"No fue posible leer el documento ({extension}): {exc}",
        }

    texto_completo = "\n\n".join(bloques).strip()
    if not texto_completo:
        return {
            "estado": "PDF_SIN_TEXTO_EXTRAIBLE" if extension == ".pdf" else "ERROR_LECTURA",
            "mensaje": "El documento no contiene texto extraíble (posible PDF escaneado como imagen que requiere OCR previo).",
        }

    return {
        "estado": "OK",
        "archivo": ruta.name,
        "extension": extension,
        "total_bloques": len(bloques),
        "bloques_numerados": [
            {"indice": i, "texto": b} for i, b in enumerate(bloques, start=1)
        ],
        "texto_completo": texto_completo,
    }