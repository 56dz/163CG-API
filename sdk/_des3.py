"""纯标准库 3DES-CBC + PKCS7，用于复刻网易云游戏密码登录。

项目其它部分都只用标准库，因此这里不引入 pycryptodome / cryptography。
实现按 FIPS 46-3 的经典表；只做加密（登录只需要加密方向）。
"""

__all__ = ["des3_cbc_encrypt_pkcs7", "des_cbc_encrypt"]

# 初始置换 IP
_IP = [58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
       62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
       57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
       61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7]

# 最终置换 FP = IP^-1
_FP = [40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
       38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
       36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
       34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25]

# 扩展置换 E
_E = [32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9,
      8, 9, 10, 11, 12, 13, 12, 13, 14, 15, 16, 17,
      16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
      24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1]

# 置换 P
_P = [16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
      2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25]

# 置换选择 PC1 / PC2
_PC1 = [57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18,
        10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
        63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22,
        14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4]

_PC2 = [14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
        23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
        41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
        44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32]

_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

# 8 个 S 盒，每个 4x16
_SBOX = [
    [[14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7],
     [0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8],
     [4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0],
     [15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13]],
    [[15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10],
     [3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5],
     [0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15],
     [13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9]],
    [[10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8],
     [13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1],
     [13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7],
     [1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12]],
    [[7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15],
     [13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9],
     [10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4],
     [3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14]],
    [[2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9],
     [14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6],
     [4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14],
     [11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3]],
    [[12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11],
     [10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8],
     [9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6],
     [4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13]],
    [[4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1],
     [13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6],
     [1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2],
     [6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12]],
    [[13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7],
     [1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2],
     [7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8],
     [2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11]],
]


def _permute(block: int, table: list[int], in_bits: int) -> int:
    """按 1-based 表从 in_bits 位的 block 里取位拼成新整数。"""
    out = 0
    for pos in table:
        out = (out << 1) | ((block >> (in_bits - pos)) & 1)
    return out


def _to_int(data: bytes) -> int:
    return int.from_bytes(data, "big")


def _to_bytes(value: int, length: int) -> bytes:
    return value.to_bytes(length, "big")


def _key_schedule(key: bytes) -> list[int]:
    k = _permute(_to_int(key), _PC1, 64)          # 56 bit
    c = (k >> 28) & 0xFFFFFFF
    d = k & 0xFFFFFFF
    subkeys = []
    for shift in _SHIFTS:
        c = ((c << shift) | (c >> (28 - shift))) & 0xFFFFFFF
        d = ((d << shift) | (d >> (28 - shift))) & 0xFFFFFFF
        subkeys.append(_permute((c << 28) | d, _PC2, 56))
    return subkeys


def _feistel(right: int, subkey: int) -> int:
    x = _permute(right, _E, 32) ^ subkey       # 48 bit
    out = 0
    for i in range(8):
        chunk = (x >> (42 - 6 * i)) & 0x3F
        row = ((chunk & 0x20) >> 4) | (chunk & 1)
        col = (chunk >> 1) & 0xF
        out = (out << 4) | _SBOX[i][row][col]
    return _permute(out, _P, 32)


def _des_block(block: bytes, subkeys: list[int]) -> bytes:
    v = _permute(_to_int(block), _IP, 64)
    left = (v >> 32) & 0xFFFFFFFF
    right = v & 0xFFFFFFFF
    for subkey in subkeys:
        left, right = right, left ^ _feistel(right, subkey)
    pre = (right << 32) | left
    return _to_bytes(_permute(pre, _FP, 64), 8)


def _pkcs7_pad(data: bytes, block: int = 8) -> bytes:
    n = block - len(data) % block
    return data + bytes([n]) * n


def des_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """单 DES-CBC（无填充，data 长度需为 8 的倍数）。"""
    if len(key) != 8 or len(iv) != 8:
        raise ValueError("DES 的 key / iv 必须是 8 字节")
    subkeys = _key_schedule(key)
    prev = iv
    out = bytearray()
    for i in range(0, len(data), 8):
        block = bytes(a ^ b for a, b in zip(data[i:i + 8], prev))
        prev = _des_block(block, subkeys)
        out += prev
    return bytes(out)


def des3_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """3DES-CBC（EDE，无填充）。key 支持 24 字节或 16 字节（2-key）。"""
    if len(key) == 16:
        key = key + key[:8]
    if len(key) != 24:
        raise ValueError("3DES 的 key 必须是 16 或 24 字节")
    k1, k2, k3 = key[:8], key[8:16], key[16:24]
    # 解密方向只需要 DES 逆；这里用「加密=反向子密钥」实现逆运算
    sk1, sk2, sk3 = _key_schedule(k1), _key_schedule(k2), _key_schedule(k3)
    prev = iv
    out = bytearray()
    for i in range(0, len(data), 8):
        block = bytes(a ^ b for a, b in zip(data[i:i + 8], prev))
        # E(k1) -> D(k2) -> E(k3)
        t = _des_block(block, sk1)
        t = _des_block(t, list(reversed(sk2)))
        t = _des_block(t, sk3)
        prev = t
        out += t
    return bytes(out)


def des3_cbc_encrypt_pkcs7(data: bytes, key: bytes, iv: bytes) -> bytes:
    """3DES-CBC + PKCS7 填充，前端 CryptoJS 的等价实现。"""
    return des3_cbc_encrypt(_pkcs7_pad(data, 8), key, iv)
