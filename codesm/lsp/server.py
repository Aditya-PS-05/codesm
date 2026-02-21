"""Language Server Protocol implementation"""

import logging
from pygls.server import LanguageServer
from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    CompletionItem,
    CompletionList,
    CompletionParams,
)

class CodesmLanguageServer(LanguageServer):
    def __init__(self):
        super().__init__("codesm-ls", "0.1.0")

server = CodesmLanguageServer()

@server.feature(TEXT_DOCUMENT_COMPLETION)
def completions(params: CompletionParams):
    """Provide completions based on codesm context"""
    items = []
    
    # Example: Suggest codesm commands in comments
    items.append(CompletionItem(label="codesm: help"))
    items.append(CompletionItem(label="codesm: task"))
    
    return CompletionList(is_incomplete=False, items=items)

def start_lsp():
    """Start the language server over stdio"""
    logging.basicConfig(level=logging.INFO, filename="codesm-lsp.log")
    server.start_io()
