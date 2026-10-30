"""Gestión de los directorios de archivos de cada sesión de chat."""

import os
import shutil

from config.settings import DATA_DIRECTORY, PDF_DIRECTORY, CHROMA_DIRECTORY

"""Clase para gestionar las sesiones"""
class SessionManager:
    """Administra los directorios de PDFs y Chroma de una sesión."""
    def _init_(self, session_id):
        self._init_session_chat(id)
    
    def _init_session_chat(self, session_id):
        self.session_path = os.path.join(DATA_DIRECTORY, session_id)
        for directory in (PDF_DIRECTORY, CHROMA_DIRECTORY):
            path = os.path.join(self.session_path, directory)
            os.makedirs(path, exist_ok=True)
   
    
    def remove_session(self, id):
        shutil.rmtree(self.session_path, ignore_errors=True)
