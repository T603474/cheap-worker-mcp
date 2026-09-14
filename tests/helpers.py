"""Dobles de test compartidos entre los módulos de prueba."""


def pdf_minimo(paginas):
    """Bytes de un PDF válido con una lista de líneas por página.

    Escrito a mano para no depender de una librería que genere PDF: los tests
    solo necesitan algo que pypdf sepa leer. Solo texto ASCII.
    """
    import io

    cuerpo = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    hijos = []
    for i, lineas in enumerate(paginas):
        pagina, contenido = 4 + 2 * i, 5 + 2 * i
        hijos.append(f"{pagina} 0 R")
        operaciones = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({l}) '" for l in lineas) + " ET"
        flujo = operaciones.encode("ascii")
        cuerpo.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {contenido} 0 R >>".encode("ascii")
        )
        cuerpo.append(b"<< /Length %d >>\nstream\n" % len(flujo) + flujo + b"\nendstream")
    cuerpo[1] = f"<< /Type /Pages /Kids [{' '.join(hijos)}] /Count {len(paginas)} >>".encode("ascii")

    salida = io.BytesIO()
    salida.write(b"%PDF-1.4\n")
    desplazamientos = []
    for numero, objeto in enumerate(cuerpo, start=1):
        desplazamientos.append(salida.tell())
        salida.write(b"%d 0 obj\n" % numero + objeto + b"\nendobj\n")
    xref = salida.tell()
    salida.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(cuerpo) + 1))
    for d in desplazamientos:
        salida.write(b"%010d 00000 n \n" % d)
    salida.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(cuerpo) + 1, xref))
    return salida.getvalue()


class BackendFalso:
    """Backend de mentira: sirve respuestas predefinidas y registra las llamadas.

    `respuestas` es siempre una lista, incluso para una sola respuesta, para que
    todas las llamadas tengan la misma forma.
    """

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def chat(self, perfil, system, user):
        self.llamadas.append({
            "perfil": perfil, "system": system, "user": user,
            "modelo": perfil.modelo, "temperature": perfil.temperatura,
        })
        return self.respuestas.pop(0)
