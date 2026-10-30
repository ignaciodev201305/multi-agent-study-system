"""Reducer que acota el historial de mensajes del grafo de sesión."""

from langchain.messages import SystemMessage

def add_optimized(before_messages, after_messages,):
    """Concatena mensajes, conserva el último SystemMessage y recorta el historial antiguo."""
    messages = before_messages + after_messages
    limit_message = 11 
    drop = -6
    # Reemplazar SystemMessages previos con el nuevo
    system_messages = [msg for msg in messages if isinstance(msg, SystemMessage)]
    system_messages = system_messages[-1:]

    chat_messages = [msg for msg in messages if not isinstance(msg, SystemMessage)]

    # Recortar conversacion, sin incluir SystemMessages
    if len(chat_messages)>=limit_message:
        chat_messages = chat_messages[drop:]

    return system_messages + chat_messages
