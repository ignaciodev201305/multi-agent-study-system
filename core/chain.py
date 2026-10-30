"""Construcción de cadenas LCEL con salida opcional estructurada."""

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

class ChainBuilder():    
    """Arma cadenas prompt-modelo y añade el parser cuando se define un schema."""
    def build_chain(self, model, prompt, schema=None):
        """Construye la cadena; con schema usa salida Pydantic estructurada."""
        if schema:
            parser_schema = PydanticOutputParser(pydantic_object=schema)
            prompt = ChatPromptTemplate.from_messages([
                ("system", prompt)]).partial(format_instructions=parser_schema.get_format_instructions())
            return prompt | model | parser_schema
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt)])
        return prompt | model
