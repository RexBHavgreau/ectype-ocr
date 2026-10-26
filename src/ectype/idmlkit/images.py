"""Size and resolution of JPEG and PNG images, read from their headers (no image library)."""

import struct


def image_info(path):
    """(width px, height px, horizontal ppi, vertical ppi, format name). Resolution defaults to
    72 ppi when the file doesn't record one."""
    with open(path, "rb") as f:
        data = f.read(65536)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(path, data)
    if data[:2] == b"\xff\xd8":
        return _jpeg(path)
    raise ValueError("%s is not a JPEG or PNG image" % path)


def _png(path, data):
    w, h = struct.unpack(">II", data[16:24])
    ppi = (72.0, 72.0)
    i = data.find(b"pHYs")
    if i > 0:
        x, y, unit = struct.unpack(">IIB", data[i + 4:i + 13])
        if unit == 1:                  # pixels per metre
            ppi = (x * 0.0254, y * 0.0254)
    return w, h, ppi[0], ppi[1], "PNG"


def _jpeg(path):
    w = h = None
    ppi = (72.0, 72.0)
    with open(path, "rb") as f:
        f.read(2)
        while True:
            marker = f.read(2)
            if len(marker) < 2 or marker[0] != 0xFF:
                break
            kind = marker[1]
            if kind in (0xD8, 0x01) or 0xD0 <= kind <= 0xD7:
                continue
            length = struct.unpack(">H", f.read(2))[0]
            seg = f.read(length - 2)
            if kind == 0xE0 and seg[:5] == b"JFIF\x00":
                units, x, y = struct.unpack(">BHH", seg[7:12])
                if units == 1 and x and y:
                    ppi = (float(x), float(y))
                elif units == 2 and x and y:
                    ppi = (x * 2.54, y * 2.54)
            elif kind in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD,
                          0xCE, 0xCF):
                h, w = struct.unpack(">HH", seg[1:5])
                break
            elif kind == 0xDA:
                break
    if w is None:
        raise ValueError("%s: can't read the JPEG's size" % path)
    return w, h, ppi[0], ppi[1], "JPEG"
