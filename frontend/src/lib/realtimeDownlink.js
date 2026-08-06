export function applyDownlinkToSpotMap(lastBySpot, payload, manifest, receivedAt = Date.now()) {
  const { packEpoch, codes } = payload || {}
  if (!manifest || packEpoch !== manifest.pack_epoch || !Array.isArray(codes)) {
    return { applied: false, stale: true, receivedAt: null }
  }

  for (const [index, spot] of (manifest.spots || []).entries()) {
    const code = codes[index]
    if (code === undefined || !Number.isInteger(code) || code < 0 || code > 0xff) continue
    lastBySpot[spot.spot_id] = {
      w: code >> 4,
      c: code & 0x0f,
      at: receivedAt,
    }
  }

  return { applied: true, stale: false, receivedAt }
}
