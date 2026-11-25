"""
wmdc.py  -  what is known about the game's WMDC data container (aura.bin, meters.bin, mt6meter.bin, *.txt ...).

A WMDC file is encrypted. What the files themselves show (checked on 6 files):

    offset  size
    0       4     "WMDC"
    4       4     version 0x00000101
    8       8     two 32-bit values that differ per file (nonce / key seed: not understood)
    16      4     chunk size 0x00100000 (1 MiB)
    20      4     length of the plain data
    24      8     zeros
    32      ...   the data, in chunks of up to 1 MiB, each followed by a 16-byte tag
                  (file size = 32 + plain length + 16 x number of chunks, exact for all 6 files)

The bytes look random (entropy 8.0) at every position; XOR against known plain data (meters.bin / meters-v2.bin)
gives a keystream with no pattern, so it is a real cipher (stream or AES-CTR / GCM like) with a key this tool does not have.
Without the key (or the game's code that reads the container) the tool cannot open, edit or write these files.
It reads the header only, to tell you what you have.
"""
import os
import struct

MAGIC = b"WMDC"


class WmdcError(Exception):
    pass


def inspect(path_or_bytes):
    """header facts of a WMDC file -> dict, or None when it is not a WMDC file"""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        head, size = bytes(path_or_bytes[:32]), len(path_or_bytes)
    else:
        size = os.path.getsize(path_or_bytes)
        with open(path_or_bytes, "rb") as fh:
            head = fh.read(32)
    if len(head) < 32 or head[:4] != MAGIC:
        return None
    ver, a, b, chunk, plen, z1, z2 = struct.unpack("<IIIIIII", head[4:32])
    nchunks = -(-plen // chunk) if chunk else 0
    return dict(version=ver, a=a, b=b, chunk=chunk, plain_length=plen, chunks=nchunks, size=size,
                consistent=(size == 32 + plen + 16 * nchunks and not z1 and not z2))


def describe(info):
    return ("WMDC container v%x: %d bytes of plain data in %d chunk(s) of up to %d bytes, each with a 16-byte tag%s." % (
        info["version"], info["plain_length"], info["chunks"], info["chunk"],
        "" if info["consistent"] else " (the sizes do not add up: damaged or a different version)"))
