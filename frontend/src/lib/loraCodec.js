export function decodeDownlinkHex(hexData) {
  if (typeof hexData !== 'string' || hexData.length < 4 || hexData.length % 2 !== 0) {
    return null
  }

  const bytes = new Uint8Array(hexData.length / 2)
  for (let index = 0; index < hexData.length; index += 2) {
    const byte = Number.parseInt(hexData.slice(index, index + 2), 16)
    if (!Number.isInteger(byte)) return null
    bytes[index / 2] = byte
  }

  if (bytes[0] !== 0x01) return null
  return {
    packEpoch: bytes[1],
    codes: Array.from(bytes.slice(2)),
  }
}
