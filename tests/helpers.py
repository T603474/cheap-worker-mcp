"""Dobles de test compartidos entre los módulos de prueba."""


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
