"""esxkit — toolkit for Xactimate .esx estimate files.

Read, create, convert, validate, and batch-process Xactimate ESX files.

Format knowledge is based on public reverse-engineering documentation
(ESXPress AI-Knowledge corpus, CC BY 4.0), verified against real exports.
Full fidelity for native (encrypted) files is limited by design: the
estimate XML inside carrier-issued ESX files is AES-encrypted and is not
decrypted by this toolkit.
"""

__version__ = "0.2.0"
