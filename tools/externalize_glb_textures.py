#!/usr/bin/env python3
"Remove the EMBEDDED textures from the .glb files to a shared folder.\n\nTHE PROBLEM\n-----------\ngltf_writer embeds each texture inside each .glb (Image.bufferView), and the\nGodot scene importer comes with `gltf/embedded_image_handling=1`\n(Extract Textures): for each embedded image write a `<asset>_<n>.png` to the\nside of the .glb and then import it as a loose texture.\n\nWith a texture shared by 107 buildings, that's 107 embedded copies, 107\nExtracted PNGs and 107 different resources in VRAM. Measured on the dataset:\n\n  - 77% de the bytes of the .glb are embedded texture (1205 MB of 1564)\n  - 11,760 of the 14,545 PNGs in the project were extracted by Godot from our .glb files\n  - 7,811 of those PNGs are a byte-by-byte copy of another (818 MB)\n  - importing a texture costs 60 ms, so it's ~8 min for each reimport\n\nWHAT THIS SCRIPT DOES\n--------------------\nRewrites the already generated .glb: output each image to data/<SHARED>/ with a\nname derived from its CONTENT --las equals fall in the same file-- and the\nreference by `uri` instead of by bufferView. Godot no longer has anything to do\nextract and load the texture that N assets share once.\n\nThere is no need for the original dataset or to convert again: it operates on what already exists\nIt is in data/. gltf_writer was also fixed so that the new thing comes out like this\ninput.\n\nSECURITY\n---------\nEach file is written to a temporary, parsed again to verify that\nIt was well formed, and only then replaced the original. A .glb that fails is left\nintact and reported.\n\nUsage:\n    python tools/externalize_glb_textures.py --dry-run only reports\n    python tools/externalize_glb_textures.py transform\n    python tools/externalize_glb_textures.py --dir data/entity/building"

import hashlib
import json
import struct
import sys
from pathlib import Path
from urllib.parse import quote

PROJECT = Path(__file__).resolve().parent.parent
DATA = PROJECT / "data"
## Single folder for the entire project, and not one per section: duplicates
## larger ones cross folders (559 between building and shape, 411 between building
## and world/dungeon), so separating them by section would lose just the bulk of
## deduplication.
SHARED = DATA / "textures"

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def leer_glb(path: Path):
    '(json, bytes of the BIN chunk). None if it is not a GLB that we know how to read.'
    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] != b"glTF":
        return None
    total = struct.unpack_from("<I", raw, 8)[0]
    off = 12
    doc = None
    blob = b""
    while off + 8 <= min(total, len(raw)):
        length, kind = struct.unpack_from("<II", raw, off)
        datos = raw[off + 8:off + 8 + length]
        if kind == JSON_CHUNK:
            doc = json.loads(datos.decode("utf-8"))
        elif kind == BIN_CHUNK:
            blob = datos
        off += 8 + length
    return None if doc is None else (doc, blob)


def escribir_glb(path: Path, doc: dict, blob: bytes) -> None:
    texto = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    # The two chunks are aligned to 4: the JSON is filled with SPACES and the
    # binary with zeros. This is what the spec asks for, and Godot rejects the file if
    # is not fulfilled.
    texto += b" " * (-len(texto) % 4)
    blob += b"\x00" * (-len(blob) % 4)

    partes = [struct.pack("<II", len(texto), JSON_CHUNK), texto]
    if blob:
        partes += [struct.pack("<II", len(blob), BIN_CHUNK), blob]
    cuerpo = b"".join(partes)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(cuerpo)) + cuerpo)


def _vista(doc, blob, indice):
    v = doc["bufferViews"][indice]
    inicio = v.get("byteOffset", 0)
    return blob[inicio:inicio + v["byteLength"]]


def externalizar(path: Path, compartidas: dict, dry_run: bool) -> tuple:
    """(imagenes sacadas, bytes liberados). (0, 0) si no habia nada embebido."""
    leido = leer_glb(path)
    if leido is None:
        return 0, 0
    doc, blob = leido
    imagenes = doc.get("images") or []
    if not any("bufferView" in img for img in imagenes):
        return 0, 0

    # The views that occupy the images are gone; the rest is repackaged and there
    # to reindex whoever names them.
    a_sacar = set()
    sacadas = 0
    liberados = 0
    for i, img in enumerate(imagenes):
        vi = img.get("bufferView")
        if vi is None:
            continue
        datos = _vista(doc, blob, vi)
        firma = hashlib.md5(datos).hexdigest()[:16]
        if firma not in compartidas:
            # The name comes ONLY from the content, not from the asset that brought it.
            #

            # Naming them after who saw them first read better, but
            # reintroduces the bug that this script comes to fix: gltf_writer
            # I would name it by the source .dds and this by the .glb, so
            # converting a loose map would write the same image again with
            # another name. With the content as the name, the same texture is the
            # The same file is written by whoever writes it.
            compartidas[firma] = firma + ".png"
            if not dry_run:
                SHARED.mkdir(parents=True, exist_ok=True)
                (SHARED / compartidas[firma]).write_bytes(datos)
        destino = SHARED / compartidas[firma]
        # uri RELATIVE to the .glb and with the rare escaped characters: there are assets
        # with spaces in the name ("coal mine_01.glb").
        img.pop("bufferView", None)
        img.pop("mimeType", None)
        img["uri"] = quote(str(Path(destino).relative_to(path.parent, walk_up=True)).replace("\\", "/"))
        a_sacar.add(vi)
        sacadas += 1
        liberados += len(datos)

    if dry_run:
        return sacadas, liberados

    nuevas = []
    remap = {}
    nuevo_blob = bytearray()
    for i, v in enumerate(doc.get("bufferViews", [])):
        if i in a_sacar:
            continue
        datos = _vista(doc, blob, i)
        remap[i] = len(nuevas)
        v = dict(v)
        v["byteOffset"] = len(nuevo_blob)
        v["byteLength"] = len(datos)
        nuevas.append(v)
        nuevo_blob += datos
        # Each view aligned to 4: accessors read 2 and 4 byte types and
        # with odd offsets the file is invalid.
        nuevo_blob += b"\x00" * (-len(nuevo_blob) % 4)

    for acc in doc.get("accessors", []):
        if "bufferView" in acc:
            acc["bufferView"] = remap[acc["bufferView"]]
        esparso = acc.get("sparse")
        if esparso:
            esparso["indices"]["bufferView"] = remap[esparso["indices"]["bufferView"]]
            esparso["values"]["bufferView"] = remap[esparso["values"]["bufferView"]]

    doc["bufferViews"] = nuevas
    if nuevas:
        doc["buffers"] = [{"byteLength": len(nuevo_blob)}]
    else:
        doc.pop("buffers", None)

    # Temporarily, it is revalidated by parsing what was written, and only then replaces it. A
    # Corrupt .glb here is not recovered without converting back from the original.
    tmp = path.with_suffix(".glb.tmp")
    escribir_glb(tmp, doc, bytes(nuevo_blob))
    control = leer_glb(tmp)
    if control is None or len(control[0].get("bufferViews", [])) != len(nuevas):
        tmp.unlink(missing_ok=True)
        raise ValueError('the rewritten .glb did not parse again')
    tmp.replace(path)
    return sacadas, liberados


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    raiz = DATA
    if "--dir" in sys.argv:
        raiz = PROJECT / sys.argv[sys.argv.index("--dir") + 1]

    compartidas = {}
    archivos = fallidos = total_img = 0
    liberados = 0
    # Alphabetical order: makes the names of the shared ones deterministic.
    for glb in sorted(raiz.rglob("*.glb")):
        try:
            n, bytes_ = externalizar(glb, compartidas, dry_run)
        except (ValueError, KeyError, struct.error) as exc:
            print("  FAILED %s: %s" % (glb, exc), file=sys.stderr)
            fallidos += 1
            continue
        if n:
            archivos += 1
            total_img += n
            liberados += bytes_

    print('%d .glb headdresses, %d images taken, %d unique textures in %s'
          % (archivos, total_img, len(compartidas), SHARED.relative_to(PROJECT)))
    print("%.0f MB de imagen embebida -> %.0f MB compartidos%s"
          % (liberados / 1e6,
             sum((SHARED / n).stat().st_size for n in compartidas.values()) / 1e6
             if not dry_run and compartidas else 0.0,
             "  (simulacion)" if dry_run else ""))
    if fallidos:
        print('%d files were left UNTOUCHED by mistake' % fallidos, file=sys.stderr)
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
