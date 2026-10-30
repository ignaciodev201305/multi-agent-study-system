"""Gestión de los directorios de archivos de cada sesión de chat."""

import shutil


class SessionManager:
    """Borra el directorio de PDFs y Chroma de una sesión."""

    session_path: str

    def remove_session(self, id):
        shutil.rmtree(self.session_path, ignore_errors=True)
