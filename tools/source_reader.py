'Little-endian binary reader shared by converters at tools/.\n\nReplicates the `Cursor`/`Reader` pattern used by each *_loader.cpp of the engine\noriginal (src/world/): bounds-checked, does not launch until prompted\ninvalid data (self.ok goes False and reads return 0).'

import struct


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.ok = True

    def _ensure(self, n: int) -> bool:
        if not self.ok or self.pos + n > len(self.data):
            self.ok = False
            return False
        return True

    def u8(self) -> int:
        if not self._ensure(1):
            return 0
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        if not self._ensure(2):
            return 0
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i16(self) -> int:
        if not self._ensure(2):
            return 0
        v = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        if not self._ensure(4):
            return 0
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self) -> int:
        if not self._ensure(4):
            return 0
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        if not self._ensure(4):
            return 0.0
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def vec2(self) -> list:
        return [self.f32(), self.f32()]

    def vec3(self) -> list:
        return [self.f32(), self.f32(), self.f32()]

    def bytes(self, n: int) -> bytes:
        if not self._ensure(n):
            return b""
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def skip(self, n: int) -> None:
        self._ensure(n)
        if self.ok:
            self.pos += n

    def string256(self) -> str:
        raw = self.bytes(256)
        nul = raw.find(b"\0")
        if nul != -1:
            raw = raw[:nul]
        return raw.decode("latin-1")

    def string_length_prefixed(self, max_length: int = 4096) -> str:
        """u32 byte count + bytes; strips one trailing NUL. Matches
        EftReader::string in eft_binary_reader.h."""
        length = self.u32()
        if not self.ok or length > max_length or self.pos + length > len(self.data):
            self.ok = False
            return ""
        raw = self.bytes(length)
        if raw.endswith(b"\0"):
            raw = raw[:-1]
        return raw.decode("latin-1")

    def count(self, maximum: int) -> int:
        value = self.u32()
        if not self.ok or value > maximum:
            self.ok = False
            return 0
        return value
